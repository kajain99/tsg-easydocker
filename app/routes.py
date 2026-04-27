import json
import shutil
from concurrent.futures import ThreadPoolExecutor, as_completed

import requests
from flask import Response, jsonify, redirect, render_template, request

from app_config import BASE_CONFIG, GITHUB_BASE, RECIPES_PATH
from auth_utils import login_view, logout_view, normalize_next_url
from services.catalog_service import build_installed_apps
from services.compose_service import (
    build_compose_summary_from_compose,
    build_form_defaults_from_compose,
    get_compose_data,
)
from services.docker_service import (
    build_container_name,
    build_existing_config_context,
    find_duplicate_containers,
    get_all_containers_info,
    get_all_project_names,
    get_compose_command,
    get_next_container_name,
    get_primary_recipe_image,
    is_safe_container_name,
)
from services.deployment_service import (
    build_deployment_result_payloads,
    get_deployment_run,
    start_deployment_run,
    stream_deployment_events,
)
from services.recipe_service import (
    build_recipe_field_sections,
    get_recipe_from_compose,
    is_safe_recipe_name,
    load_recipe_by_name,
    load_recipes,
    normalize_recipe,
    save_recipe_snapshot,
)
from services.settings_service import detect_and_save_system_hardware, load_settings, save_settings
from services.yaml_service import build_app_links, dump_compose_yaml, generate_compose


REVIEW_ACTIONS = {"review_deploy", "review_pull_deploy"}
DEPLOY_ACTIONS = {"deploy", "pull_deploy"}
ALL_SUBMISSION_ACTIONS = REVIEW_ACTIONS | DEPLOY_ACTIONS


def resolve_submission_action(action):
    if action in ALL_SUBMISSION_ACTIONS:
        return action
    return "review_deploy"


def action_pulls_first(action):
    return action in {"review_pull_deploy", "pull_deploy"}


def build_recipe_form_context(recipe, form_defaults=None, existing_config_name=None):
    project_name = existing_config_name or build_container_name(recipe["name"])
    context = {
        "recipe": recipe,
        "form_defaults": form_defaults or {},
    }
    context.update(
        build_recipe_field_sections(
            recipe,
            form_defaults=form_defaults,
            project_name=project_name,
        )
    )
    if existing_config_name:
        context.update(build_existing_config_context(existing_config_name, recipe))
    return context


def maybe_render_duplicate_warning(recipe, action, confirm_duplicate, container_name_override, form_data):
    if action not in ALL_SUBMISSION_ACTIONS:
        return None, False

    if confirm_duplicate:
        return None, True

    if container_name_override:
        return None, False

    image_name = get_primary_recipe_image(recipe)
    duplicates = find_duplicate_containers(recipe["name"], image_name)
    if not duplicates:
        return None, False

    next_container_name = get_next_container_name(recipe["name"])
    response = render_template(
        "duplicate_warning.html",
        recipe=recipe,
        duplicates=duplicates,
        form_data=form_data.to_dict(flat=True),
        next_container_name=next_container_name
    )
    return response, False


def resolve_container_name(recipe_name, action, container_name_override, has_duplicates):
    base_container_name = container_name_override or build_container_name(recipe_name)
    if action not in ALL_SUBMISSION_ACTIONS:
        return base_container_name

    all_project_names = get_all_project_names()
    if container_name_override or (not has_duplicates and base_container_name not in all_project_names):
        return base_container_name

    return get_next_container_name(recipe_name)


def build_deploy_display_context(compose_summary, folder_path, pull_first):
    compose_cmd = get_compose_command()
    command_parts = []
    if pull_first:
        command_parts.append(" ".join(compose_cmd + ["pull"]))
    command_parts.append(" ".join(compose_cmd + ["up", "-d"]))
    command_display = " then ".join(command_parts)

    exposed_ports = []
    if compose_summary:
        for service in compose_summary.get("services", []):
            exposed_ports.extend(service.get("ports", []))

    return {
        "compose_cmd": compose_cmd,
        "command_display": command_display,
        "folder_display": str(folder_path),
        "services_count": compose_summary.get("service_count", 0) if compose_summary else 0,
        "ports_display": ", ".join(exposed_ports) if exposed_ports else "None",
    }


def build_review_step_context(recipe, yaml_output, compose_summary, form_items, container_name, pull_first):
    display_context = build_deploy_display_context(compose_summary, BASE_CONFIG / container_name, pull_first)
    form_items = list(form_items)
    form_defaults = dict(form_items)
    return {
        "compose_yaml": yaml_output,
        "command_display": display_context["command_display"],
        "folder_display": display_context["folder_display"],
        "services_count": display_context["services_count"],
        "ports_display": display_context["ports_display"],
        "final_action": "pull_deploy" if pull_first else "deploy",
        "form_items": form_items,
        "form_defaults": form_defaults,
        "container_name": container_name,
        **build_recipe_field_sections(
            recipe,
            form_defaults=form_defaults,
            project_name=container_name,
        ),
    }


def build_deploy_step_context(run_id):
    run_record = get_deployment_run(run_id)
    if not run_record:
        return {}
    return {
        "run_id": run_id,
        "deployment_label": run_record["deployment_label"],
        "deploy_command_display": run_record["command_display"],
        "deploy_folder_display": run_record["folder_display"],
        "deploy_services_count": run_record["services_count"],
        "deploy_ports_display": run_record["ports_display"],
    }


def start_recipe_deployment(
    recipe,
    yaml_output,
    compose_summary,
    app_links,
    app_folder,
    container_name,
    pull_first,
    form_items,
):
    display_context = build_deploy_display_context(compose_summary, app_folder, pull_first)
    result_payloads = build_deployment_result_payloads(yaml_output, app_links)
    run_id = start_deployment_run(
        compose_cmd=display_context["compose_cmd"],
        app_folder=app_folder,
        deployment_label=recipe.get("name") or container_name,
        command_display=display_context["command_display"],
        folder_display=display_context["folder_display"],
        services_count=display_context["services_count"],
        ports_display=display_context["ports_display"],
        success_result=result_payloads["success"],
        failure_result=result_payloads["failure"],
        pull_first=pull_first,
    )
    review_context = build_review_step_context(
        recipe,
        yaml_output,
        compose_summary,
        form_items,
        container_name,
        pull_first,
    )
    return render_template(
        "recipe_v2.html",
        **build_recipe_form_context(
            recipe,
            form_defaults=dict(form_items),
            existing_config_name=container_name,
        ),
        workflow_step="deploy",
        review_step=review_context,
        deploy_step=build_deploy_step_context(run_id),
    )


def register_routes(app):
    @app.route("/login", methods=["GET", "POST"])
    def login():
        return login_view()

    @app.route("/logout", methods=["POST"])
    def logout():
        return logout_view()

    @app.route("/")
    def home():
        apps = load_recipes()
        installed_apps, orphan_configs = build_installed_apps()
        return render_template("catalog.html", apps=apps, installed_apps=installed_apps, orphan_configs=orphan_configs)

    @app.route("/installed")
    def installed():
        installed_apps, orphan_configs = build_installed_apps()
        return render_template("installed.html", installed_apps=installed_apps, orphan_configs=orphan_configs)

    @app.route("/settings", methods=["GET", "POST"])
    def settings():
        if request.method == "POST":
            action = request.form.get("action", "save")
            if action == "detect_system":
                settings_values = detect_and_save_system_hardware(request.form)
                return render_template("settings.html", settings=settings_values, saved=True, hardware_detected=True)

            settings_values = save_settings(request.form)
            return render_template("settings.html", settings=settings_values, saved=True, hardware_detected=False)

        return render_template("settings.html", settings=load_settings(), saved=False, hardware_detected=False)

    @app.route("/recipes")
    def get_recipes():
        if not RECIPES_PATH.exists():
            return jsonify({"error": "recipes folder not found"}), 404
        return jsonify(load_recipes())

    @app.route("/recipe/<name>")
    def show_recipe(name):
        if not is_safe_recipe_name(name):
            return "Invalid recipe name", 400

        recipe = load_recipe_by_name(name)
        if not recipe:
            return f"Recipe {name} not found", 404
        return render_template("recipe_v2.html", **build_recipe_form_context(recipe))

    @app.route("/generate/<name>", methods=["POST"])
    def generate_yaml(name):
        if not is_safe_recipe_name(name):
            return "Invalid recipe name", 400

        recipe = load_recipe_by_name(name)
        if not recipe:
            return "Recipe not found", 404

        form_data = request.form
        action = resolve_submission_action(form_data.get("action"))
        pull_first = action_pulls_first(action)
        confirm_duplicate = form_data.get("confirm_duplicate") == "1"
        container_name_override = form_data.get("container_name_override", "").strip()

        if container_name_override and not is_safe_container_name(container_name_override):
            return "Invalid container name", 400

        duplicate_warning_response, has_duplicates = maybe_render_duplicate_warning(
            recipe,
            action,
            confirm_duplicate,
            container_name_override,
            form_data,
        )
        if duplicate_warning_response:
            return duplicate_warning_response

        container_name = resolve_container_name(recipe["name"], action, container_name_override, has_duplicates)

        compose = generate_compose(recipe, form_data, container_name)
        yaml_output = dump_compose_yaml(compose)
        compose_summary = build_compose_summary_from_compose(compose, [])
        form_items = list(form_data.items())
        form_defaults = form_data.to_dict(flat=True)
        host_name = request.host.split(":")[0]
        app_links = build_app_links(recipe, form_data, container_name, host_name)

        if action in REVIEW_ACTIONS:
            return render_template(
                "recipe_v2.html",
                **build_recipe_form_context(
                    recipe,
                    form_defaults=form_defaults,
                    existing_config_name=container_name,
                ),
                workflow_step="review",
                review_step=build_review_step_context(
                    recipe,
                    yaml_output,
                    compose_summary,
                    form_items,
                    container_name,
                    pull_first,
                ),
            )

        app_folder = BASE_CONFIG / container_name
        app_folder.mkdir(parents=True, exist_ok=True)

        yaml_path = app_folder / "docker-compose.yml"
        with open(yaml_path, "w") as handle:
            handle.write(yaml_output)
        save_recipe_snapshot(app_folder, recipe)

        if action in DEPLOY_ACTIONS:
            return start_recipe_deployment(
                recipe,
                yaml_output,
                compose_summary,
                app_links,
                app_folder,
                container_name,
                pull_first,
                form_items,
            )

        return redirect("/")

    @app.route("/deploy/<run_id>")
    def deploy_page(run_id):
        run_record = get_deployment_run(run_id)
        if not run_record:
            return "Deployment run not found", 404

        return render_template(
            "deploy.html",
            run_id=run_id,
            deployment_label=run_record["deployment_label"],
            command_display=run_record["command_display"],
            folder_display=run_record["folder_display"],
            services_count=run_record["services_count"],
            ports_display=run_record["ports_display"],
        )

    @app.route("/deploy-stream/<run_id>")
    def deploy_stream(run_id):
        return Response(
            stream_deployment_events(run_id),
            mimetype="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "X-Accel-Buffering": "no",
            }
        )

    @app.route("/redeploy/<container_name>", methods=["POST"])
    def redeploy_from_config(container_name):
        if not is_safe_container_name(container_name):
            return "Invalid container name", 400

        app_folder = BASE_CONFIG / container_name
        yaml_path = app_folder / "docker-compose.yml"

        if not yaml_path.exists():
            return f"Config for {container_name} not found", 404

        recipe, compose_data = get_recipe_from_compose(yaml_path, get_compose_data)
        if not recipe:
            return f"Recipe for config {container_name} not found", 404

        form_defaults = build_form_defaults_from_compose(recipe, compose_data)
        return render_template(
            "recipe_v2.html",
            **build_recipe_form_context(
                recipe,
                form_defaults=form_defaults,
                existing_config_name=container_name,
            )
        )

    @app.route("/delete-config/<container_name>", methods=["POST"])
    def delete_config(container_name):
        if not is_safe_container_name(container_name):
            return "Invalid container name", 400

        app_folder = (BASE_CONFIG / container_name).resolve()
        base_resolved = BASE_CONFIG.resolve()
        next_url = normalize_next_url(request.form.get("next", "/"))

        try:
            app_folder.relative_to(base_resolved)
        except ValueError:
            return "Invalid config path", 400

        yaml_path = app_folder / "docker-compose.yml"
        if not yaml_path.exists():
            return f"Config for {container_name} not found", 404

        all_containers_info = get_all_containers_info()
        matching_container = next(
            (
                container for container in all_containers_info
                if container.get("project") == container_name or container["name"] == container_name
            ),
            None
        )
        if matching_container:
            return "Cannot delete config while matching project/container still exists", 400

        try:
            shutil.rmtree(app_folder, ignore_errors=False)
        except OSError as exc:
            return f"Could not delete config for {container_name}: {exc}", 400

        return redirect(next_url)

    @app.route("/refresh-recipes", methods=["POST"])
    def refresh_recipes():
        try:
            index_url = f"{GITHUB_BASE}/index.json"
            response = requests.get(index_url, timeout=10)
            if response.status_code != 200:
                return jsonify({"error": "Failed to fetch index.json"}), 502

            index_data = response.json()
            updated = []
            RECIPES_PATH.mkdir(parents=True, exist_ok=True)
            outdated_recipes = []

            for item in index_data.get("recipes", []):
                name = item["name"]
                version = item["version"]
                local_file = RECIPES_PATH / f"{name}.json"
                local_version = 0
                if local_file.exists():
                    with open(local_file) as handle:
                        local_version = json.load(handle).get("version", 0)

                if version > local_version:
                    outdated_recipes.append({
                        "name": name,
                        "local_file": local_file,
                    })

            def fetch_recipe(recipe_name):
                recipe_url = f"{GITHUB_BASE}/{recipe_name}.json"
                recipe_response = requests.get(recipe_url, timeout=10)
                if recipe_response.status_code != 200:
                    raise ValueError(f"Failed to fetch recipe: {recipe_name}")

                recipe_data = recipe_response.json()
                normalize_recipe(recipe_data)
                return recipe_name, recipe_data

            if outdated_recipes:
                max_workers = min(8, len(outdated_recipes))
                fetched_recipes = {}

                with ThreadPoolExecutor(max_workers=max_workers) as executor:
                    future_to_recipe = {
                        executor.submit(fetch_recipe, item["name"]): item
                        for item in outdated_recipes
                    }

                    for future in as_completed(future_to_recipe):
                        item = future_to_recipe[future]
                        try:
                            recipe_name, recipe_data = future.result()
                        except Exception as exc:
                            return jsonify({"error": str(exc)}), 502
                        fetched_recipes[recipe_name] = recipe_data

                for item in outdated_recipes:
                    recipe_name = item["name"]
                    recipe_data = fetched_recipes[recipe_name]
                    with open(item["local_file"], "w") as handle:
                        json.dump(recipe_data, handle, indent=2)
                    updated.append(recipe_name)

            return jsonify({"status": "success", "updated": updated, "updated_count": len(updated)})
        except Exception as exc:
            return jsonify({"error": str(exc)}), 500

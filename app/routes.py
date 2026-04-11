import json
import shutil

import requests
import yaml
from flask import Response, jsonify, redirect, render_template, request, url_for

from app_config import BASE_CONFIG, GITHUB_BASE, RECIPES_PATH
from auth_utils import login_view, logout_view
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
    find_port_conflicts,
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
    get_recipe_from_compose,
    is_safe_recipe_name,
    load_recipe_by_name,
    load_recipes,
    save_recipe_snapshot,
)
from services.yaml_service import build_app_links, generate_compose


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

    @app.route("/recipes")
    def get_recipes():
        if not RECIPES_PATH.exists():
            return jsonify({"error": "recipes folder not found"})
        return jsonify(load_recipes())

    @app.route("/recipe/<name>")
    def show_recipe(name):
        if not is_safe_recipe_name(name):
            return "Invalid recipe name", 400

        recipe = load_recipe_by_name(name)
        if not recipe:
            return f"Recipe {name} not found"
        return render_template("recipe_v2.html", recipe=recipe)

    @app.route("/generate/<name>", methods=["POST"])
    def generate_yaml(name):
        if not is_safe_recipe_name(name):
            return "Invalid recipe name", 400

        recipe = load_recipe_by_name(name)
        if not recipe:
            return "Recipe not found"

        form_data = request.form
        action = form_data.get("action")
        pull_first = action == "pull_deploy"
        image_name = get_primary_recipe_image(recipe)
        confirm_duplicate = form_data.get("confirm_duplicate") == "1"
        container_name_override = form_data.get("container_name_override", "").strip()
        has_duplicates = False

        if container_name_override and not is_safe_container_name(container_name_override):
            return "Invalid container name", 400

        if action not in {"deploy", "pull_deploy"}:
            action = "deploy"
            pull_first = False

        if action in {"deploy", "pull_deploy"} and not confirm_duplicate and not container_name_override:
            duplicates = find_duplicate_containers(recipe["name"], image_name)
            if duplicates:
                next_container_name = get_next_container_name(recipe["name"])
                return render_template(
                    "duplicate_warning.html",
                    recipe=recipe,
                    duplicates=duplicates,
                    form_data=request.form.to_dict(flat=True),
                    next_container_name=next_container_name
                )
        elif action in {"deploy", "pull_deploy"} and confirm_duplicate:
            has_duplicates = True

        base_container_name = container_name_override or build_container_name(recipe["name"])
        all_project_names = get_all_project_names() if action in {"deploy", "pull_deploy"} else set()
        container_name = base_container_name

        if not container_name_override and (has_duplicates or base_container_name in all_project_names):
            container_name = get_next_container_name(recipe["name"])

        compose = generate_compose(recipe, form_data, container_name)
        yaml_output = yaml.dump(compose, sort_keys=False)
        compose_summary = build_compose_summary_from_compose(compose, [])
        host_name = request.host.split(":")[0]
        app_links = build_app_links(recipe, form_data, container_name, host_name)

        if action in {"deploy", "pull_deploy"}:
            port_conflicts = find_port_conflicts(compose, container_name)
            if port_conflicts:
                template_context = {
                    "recipe": recipe,
                    "form_defaults": request.form.to_dict(flat=True),
                    "port_conflicts": port_conflicts
                }
                template_context.update(build_existing_config_context(container_name_override, recipe))
                return render_template("recipe_v2.html", **template_context)

        app_folder = BASE_CONFIG / container_name
        app_folder.mkdir(parents=True, exist_ok=True)

        yaml_path = app_folder / "docker-compose.yml"
        with open(yaml_path, "w") as handle:
            handle.write(yaml_output)
        save_recipe_snapshot(app_folder, recipe)

        if action in {"deploy", "pull_deploy"}:
            compose_cmd = get_compose_command()
            deployment_label = recipe.get("name") or container_name
            command_steps = []
            if pull_first:
                command_steps.append(" ".join(compose_cmd + ["pull"]))
            command_steps.append(" ".join(compose_cmd + ["up", "-d"]))
            command_display = " then ".join(command_steps)
            folder_display = str(app_folder)
            services_count = compose_summary.get("service_count", 0) if compose_summary else 0
            exposed_ports = []
            if compose_summary:
                for service in compose_summary.get("services", []):
                    exposed_ports.extend(service.get("ports", []))
            ports_display = ", ".join(exposed_ports) if exposed_ports else "None"
            result_payloads = build_deployment_result_payloads(compose_summary, app_links)

            run_id = start_deployment_run(
                compose_cmd=compose_cmd,
                app_folder=app_folder,
                deployment_label=deployment_label,
                command_display=command_display,
                folder_display=folder_display,
                services_count=services_count,
                ports_display=ports_display,
                success_result=result_payloads["success"],
                failure_result=result_payloads["failure"],
                pull_first=pull_first,
            )
            return redirect(url_for("deploy_page", run_id=run_id))

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
        template_context = {"recipe": recipe, "form_defaults": form_defaults}
        template_context.update(build_existing_config_context(container_name, recipe))
        return render_template("recipe_v2.html", **template_context)

    @app.route("/delete-config/<container_name>", methods=["POST"])
    def delete_config(container_name):
        if not is_safe_container_name(container_name):
            return "Invalid container name", 400

        app_folder = (BASE_CONFIG / container_name).resolve()
        base_resolved = BASE_CONFIG.resolve()
        next_url = request.form.get("next", "/")

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

        shutil.rmtree(app_folder, ignore_errors=False)
        return redirect(next_url)

    @app.route("/refresh-recipes", methods=["POST"])
    def refresh_recipes():
        try:
            index_url = f"{GITHUB_BASE}/index.json"
            response = requests.get(index_url, timeout=10)
            if response.status_code != 200:
                return jsonify({"error": "Failed to fetch index.json"})

            index_data = response.json()
            updated = []
            RECIPES_PATH.mkdir(parents=True, exist_ok=True)

            for item in index_data.get("recipes", []):
                name = item["name"]
                version = item["version"]
                local_file = RECIPES_PATH / f"{name}.json"
                local_version = 0
                if local_file.exists():
                    with open(local_file) as handle:
                        local_version = json.load(handle).get("version", 0)

                if version > local_version:
                    recipe_url = f"{GITHUB_BASE}/{name}.json"
                    recipe_data = requests.get(recipe_url, timeout=10).json()
                    with open(local_file, "w") as handle:
                        json.dump(recipe_data, handle, indent=2)
                    updated.append(name)

            return jsonify({"status": "success", "updated": updated, "updated_count": len(updated)})
        except Exception as exc:
            return jsonify({"error": str(exc)})

import re

import yaml

from services.host_path_service import build_project_host_path


PLACEHOLDER_RE = re.compile(r"\$\{([A-Z0-9_]+)\}")
RESERVED_RECIPE_KEYS = {"name", "version", "description", "fields", "ui", "app_links"}
EXTRA_ENVIRONMENT_KEY = "x-easydocker-extra-environment"


def coerce_field_value(field, value):
    if value in (None, ""):
        return value

    data_type = field.get("data_type")
    if data_type == "yaml":
        try:
            return yaml.safe_load(value)
        except Exception:
            return value

    return value


def get_docker_network_derivatives(field_name, value):
    derivatives = {
        f"{field_name}_MODE": None,
        f"{field_name}_SERVICE_NETWORKS": None,
        f"{field_name}_DEFINITIONS": None,
    }

    if value in (None, "", "__create_bridge__"):
        return derivatives

    if value == "__host__":
        derivatives[f"{field_name}_MODE"] = "host"
        return derivatives

    external_prefix = "__external__:"
    if isinstance(value, str) and value.startswith(external_prefix):
        network_name = value[len(external_prefix):]
        if network_name:
            derivatives[f"{field_name}_SERVICE_NETWORKS"] = ["selected_network"]
            derivatives[f"{field_name}_DEFINITIONS"] = {
                "selected_network": {
                    "external": True,
                    "name": network_name
                }
            }

    return derivatives


def build_field_values(recipe, form_data, project_name):
    raw_values = {
        "PROJECT_NAME": project_name
    }
    conditional_fields = []
    docker_network_fields = []

    for field in recipe.get("fields", []):
        field_name = field["name"]
        submitted_value = form_data.get(field_name)
        if submitted_value is None or submitted_value == "":
            submitted_value = field.get("default", "")
        coerced_value = coerce_field_value(field, submitted_value)
        raw_values[field_name] = coerced_value

        if field.get("data_type") == "docker_network":
            docker_network_fields.append(field["name"])

        if field.get("omit_when_inactive"):
            conditional_fields.append({
                "name": field_name,
                "visible_when": field.get("visible_when"),
                "hidden_when": field.get("hidden_when"),
            })

    for field in conditional_fields:
        field_name = field["name"]
        visible_when = field["visible_when"]
        hidden_when = field["hidden_when"]

        if visible_when:
            should_show = all(
                raw_values.get(dependency_name) in (allowed_values or [])
                for dependency_name, allowed_values in visible_when.items()
            )
            if not should_show:
                raw_values[field_name] = None
                continue

        if hidden_when:
            should_hide = all(
                raw_values.get(dependency_name) in (allowed_values or [])
                for dependency_name, allowed_values in hidden_when.items()
            )
            if should_hide:
                raw_values[field_name] = None

    for field_name in docker_network_fields:
        raw_values.update(get_docker_network_derivatives(field_name, raw_values.get(field_name)))

    values = {"PROJECT_NAME": project_name}
    for field_name, value in raw_values.items():
        if field_name == "PROJECT_NAME":
            continue
        values[field_name] = substitute_placeholders(value, raw_values)
    return values


def substitute_placeholders(value, field_values):
    if isinstance(value, str):
        exact_match = PLACEHOLDER_RE.fullmatch(value)
        if exact_match:
            resolved_value = field_values.get(exact_match.group(1), "")
            if resolved_value in (None, ""):
                return None
            return resolved_value

        placeholder_only_template = PLACEHOLDER_RE.sub("", value).strip() == ""

        def replace(match):
            resolved_value = field_values.get(match.group(1), "")
            if resolved_value is None:
                return ""
            return str(resolved_value)

        substituted = PLACEHOLDER_RE.sub(replace, value)
        if placeholder_only_template:
            substituted = " ".join(substituted.split())
            if substituted == "":
                return None
        return substituted

    if isinstance(value, list):
        return [substitute_placeholders(item, field_values) for item in value]

    if isinstance(value, dict):
        return {
            key: substitute_placeholders(item, field_values)
            for key, item in value.items()
        }

    return value


def prune_compose_value(value, parent_key=None):
    if isinstance(value, dict):
        pruned = {}
        for key, item in value.items():
            cleaned = prune_compose_value(item, key)
            if cleaned is None:
                continue
            if parent_key == "environment" and cleaned == "":
                continue
            pruned[key] = cleaned
        return pruned

    if isinstance(value, list):
        pruned = []
        for item in value:
            cleaned = prune_compose_value(item, parent_key)
            if cleaned is None:
                continue
            if cleaned == "":
                continue
            pruned.append(cleaned)
        return pruned

    if value is None:
        return None

    return value


def resolve_relative_bind_mount_string(volume_value, project_name):
    if not isinstance(volume_value, str):
        return volume_value

    parts = volume_value.split(":")
    if len(parts) < 2:
        return volume_value

    source = parts[0]
    if not source.startswith("./"):
        return volume_value

    resolved_source = build_project_host_path(project_name, source)
    if not resolved_source:
        return volume_value

    return ":".join([resolved_source, *parts[1:]])


def resolve_relative_bind_mount_item(volume_item, project_name):
    if isinstance(volume_item, str):
        return resolve_relative_bind_mount_string(volume_item, project_name)

    if isinstance(volume_item, dict):
        source = volume_item.get("source")
        if isinstance(source, str) and source.startswith("./"):
            resolved_source = build_project_host_path(project_name, source)
            if resolved_source:
                resolved_item = dict(volume_item)
                resolved_item["source"] = resolved_source
                return resolved_item

    return volume_item


def resolve_relative_bind_mounts(compose, project_name):
    services = compose.get("services", {})
    for service_name, service_data in services.items():
        volumes = service_data.get("volumes")
        if not isinstance(volumes, list):
            continue
        services[service_name]["volumes"] = [
            resolve_relative_bind_mount_item(volume_item, project_name)
            for volume_item in volumes
        ]

    return compose


def apply_service_resource_limits(recipe, compose, field_values):
    services = compose.get("services", {})
    if not isinstance(services, dict):
        return compose

    for field in recipe.get("fields", []):
        resource_kind = field.get("resource_kind")
        service_name = field.get("service_name")
        field_name = field.get("name")

        if resource_kind not in {"cpu_limit", "memory_limit"} or not service_name or not field_name:
            continue
        if service_name not in services:
            continue

        resource_value = field_values.get(field_name)
        if resource_value in (None, ""):
            continue

        if resource_kind == "cpu_limit":
            services[service_name]["cpus"] = str(resource_value)
        elif resource_kind == "memory_limit":
            services[service_name]["mem_limit"] = str(resource_value)

    return compose


def merge_extra_environment(compose):
    services = compose.get("services", {})
    for service_name, service_data in services.items():
        if not isinstance(service_data, dict):
            continue

        extra_environment = service_data.pop(EXTRA_ENVIRONMENT_KEY, None)
        if not isinstance(extra_environment, dict) or not extra_environment:
            continue

        environment = service_data.get("environment")
        if not isinstance(environment, dict):
            environment = {}

        environment.update(extra_environment)
        service_data["environment"] = environment

    return compose


def generate_compose(recipe, form_data, project_name):
    field_values = build_field_values(recipe, form_data, project_name)
    compose = {}

    for top_level_key, template_value in recipe.items():
        if top_level_key in RESERVED_RECIPE_KEYS:
            continue
        cleaned_value = prune_compose_value(
            substitute_placeholders(template_value, field_values)
        )
        if cleaned_value in (None, {}, []):
            continue
        compose[top_level_key] = cleaned_value

    compose = apply_service_resource_limits(recipe, compose, field_values)
    compose = merge_extra_environment(compose)
    return resolve_relative_bind_mounts(compose, project_name)


def build_app_links(recipe, form_data, project_name, host):
    field_values = build_field_values(recipe, form_data, project_name)
    links = []

    for link_template in recipe.get("app_links", []):
        label = substitute_placeholders(link_template.get("label", "Open App"), field_values)
        port = substitute_placeholders(str(link_template.get("port", "")), field_values)
        path = substitute_placeholders(link_template.get("path", ""), field_values)
        scheme = substitute_placeholders(link_template.get("scheme", "http"), field_values)

        if not port:
            continue

        normalized_path = path if not path or path.startswith("/") else f"/{path}"
        links.append({
            "label": label,
            "url": f"{scheme}://{host}:{port}{normalized_path}"
        })

    return links


def dump_compose_yaml(compose):
    return yaml.dump(compose, sort_keys=False)

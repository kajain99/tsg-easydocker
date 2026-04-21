import copy
import re

import yaml

from services.yaml_service import PLACEHOLDER_RE, RESERVED_RECIPE_KEYS, build_app_links


def ensure_compose_list(value):
    if value in (None, ""):
        return []
    if isinstance(value, list):
        return value
    return [value]


def get_compose_data(compose_file):
    try:
        with open(compose_file) as handle:
            return yaml.safe_load(handle) or {}
    except Exception:
        return {}


def tokenize_template_string(template_value):
    tokens = []
    last_end = 0

    for match in PLACEHOLDER_RE.finditer(template_value):
        literal_text = template_value[last_end:match.start()]
        if literal_text:
            tokens.append(("literal", literal_text))
        tokens.append(("placeholder", match.group(1)))
        last_end = match.end()

    trailing_literal = template_value[last_end:]
    if trailing_literal:
        tokens.append(("literal", trailing_literal))

    return tokens


def extract_placeholder_values(template_value, actual_string):
    tokens = tokenize_template_string(template_value)
    if not tokens:
        return None

    def match_from(token_index, string_index):
        if token_index >= len(tokens):
            return {} if string_index == len(actual_string) else None

        token_type, token_value = tokens[token_index]

        if token_type == "literal":
            if token_value.isspace():
                trimmed_index = string_index
                while trimmed_index < len(actual_string) and actual_string[trimmed_index].isspace():
                    trimmed_index += 1
                return match_from(token_index + 1, trimmed_index)

            if not actual_string.startswith(token_value, string_index):
                return None
            return match_from(token_index + 1, string_index + len(token_value))

        next_literal = None
        for candidate_type, candidate_value in tokens[token_index + 1:]:
            if candidate_type == "literal":
                next_literal = candidate_value
                break

        if next_literal is None:
            return {token_value: actual_string[string_index:]}

        if next_literal.isspace():
            for split_index in range(len(actual_string), string_index - 1, -1):
                remainder = actual_string[split_index:]
                if remainder.strip():
                    continue
                matched = match_from(token_index + 1, split_index)
                if matched is not None:
                    matched[token_value] = actual_string[string_index:split_index]
                    return matched
            return None

        search_positions = []
        start = string_index
        while True:
            found_index = actual_string.find(next_literal, start)
            if found_index == -1:
                break
            search_positions.append(found_index)
            start = found_index + 1

        for split_index in reversed(search_positions):
            matched = match_from(token_index + 1, split_index)
            if matched is not None:
                matched[token_value] = actual_string[string_index:split_index]
                return matched

        return None

    return match_from(0, 0)


def extract_values_from_template(template_value, actual_value, extracted_values):
    if isinstance(template_value, str):
        matches = list(PLACEHOLDER_RE.finditer(template_value))
        if not matches:
            return

        actual_string = str(actual_value)
        if len(matches) == 1 and matches[0].span() == (0, len(template_value)):
            placeholder_name = matches[0].group(1)
            if placeholder_name != "PROJECT_NAME":
                if isinstance(actual_value, (list, dict)):
                    extracted_values.setdefault(
                        placeholder_name,
                        yaml.safe_dump(actual_value, sort_keys=False).strip()
                    )
                else:
                    extracted_values.setdefault(placeholder_name, actual_string)
            return

        matched_values = extract_placeholder_values(template_value, actual_string)
        if not matched_values:
            return

        for placeholder_name, extracted_value in matched_values.items():
            if placeholder_name != "PROJECT_NAME":
                extracted_values.setdefault(placeholder_name, extracted_value)
        return

    if isinstance(template_value, dict) and isinstance(actual_value, dict):
        for key, template_child in template_value.items():
            if key in actual_value:
                extract_values_from_template(template_child, actual_value[key], extracted_values)
        return

    if isinstance(template_value, list) and isinstance(actual_value, list):
        for template_child, actual_child in zip(template_value, actual_value):
            extract_values_from_template(template_child, actual_child, extracted_values)


def build_form_defaults_from_compose(recipe, compose_data):
    defaults = {}
    extracted_values = {}

    for top_level_key, template_value in recipe.items():
        if top_level_key in RESERVED_RECIPE_KEYS:
            continue
        if top_level_key in compose_data:
            extract_values_from_template(template_value, compose_data[top_level_key], extracted_values)

    for field in recipe.get("fields", []):
        field_name = field["name"]
        if field.get("resource_kind") and field.get("service_name"):
            service_data = compose_data.get("services", {}).get(field["service_name"], {})
            if field["resource_kind"] == "cpu_limit":
                field_value = service_data.get("cpus", field.get("default", ""))
            else:
                field_value = service_data.get("mem_limit", field.get("default", ""))
            defaults[field_name] = "" if field_value is None else str(field_value)
            continue
        defaults[field_name] = extracted_values.get(field_name, field.get("default", ""))

    return defaults


def build_app_links_from_compose(recipe, compose_data, project_name, host):
    form_defaults = build_form_defaults_from_compose(recipe, compose_data)
    return build_app_links(recipe, form_defaults, project_name, host)


def build_supported_recipe_compose(recipe):
    recipe_compose = {
        key: value
        for key, value in copy.deepcopy(recipe).items()
        if key not in RESERVED_RECIPE_KEYS
    }

    services = recipe_compose.get("services")
    if not isinstance(services, dict):
        return recipe_compose

    for field in recipe.get("fields", []):
        resource_kind = field.get("resource_kind")
        service_name = field.get("service_name")
        field_name = field.get("name")

        if resource_kind not in {"cpu_limit", "memory_limit"} or not service_name or not field_name:
            continue
        if service_name not in services or not isinstance(services[service_name], dict):
            continue

        if resource_kind == "cpu_limit":
            services[service_name]["cpus"] = f"${{{field_name}}}"
        elif resource_kind == "memory_limit":
            services[service_name]["mem_limit"] = f"${{{field_name}}}"

    return recipe_compose


def build_unsupported_compose_items(recipe, compose_data):
    if not compose_data:
        return []

    unsupported_items = []
    recipe_compose = build_supported_recipe_compose(recipe)

    def template_matches(template_value, actual_value):
        if isinstance(template_value, str):
            if PLACEHOLDER_RE.search(template_value):
                return extract_placeholder_values(template_value, str(actual_value)) is not None
            return template_value == actual_value

        if isinstance(template_value, dict) and isinstance(actual_value, dict):
            return all(
                key in actual_value and template_matches(template_child, actual_value[key])
                for key, template_child in template_value.items()
            )

        if isinstance(template_value, list) and isinstance(actual_value, list):
            return all(
                any(template_matches(template_child, actual_child) for template_child in template_value)
                for actual_child in actual_value
            )

        return template_value == actual_value

    def format_yaml_value(value):
        if isinstance(value, dict):
            return ", ".join(f"{key}: {item}" for key, item in value.items())
        if isinstance(value, list):
            return ", ".join(str(item) for item in value)
        return str(value)

    def format_unsupported_item(prefix, key_or_value, actual_value=None):
        section_name = prefix.split(".")[-1]

        if section_name == "environment" and actual_value is not None:
            return f"environment: {key_or_value}: {actual_value}"
        if section_name == "volumes":
            return f"volumes: {key_or_value}"
        if section_name == "cap_add":
            return f"cap_add: {key_or_value}"
        if section_name == "devices":
            return f"devices: {key_or_value}"
        if prefix.startswith("services.") and actual_value is not None:
            return f"{key_or_value}: {format_yaml_value(actual_value)}"
        if actual_value is not None:
            return f"{key_or_value}: {format_yaml_value(actual_value)}"
        return str(key_or_value)

    def collect_unsupported(template_value, actual_value, prefix):
        items = []

        if isinstance(template_value, dict) and isinstance(actual_value, dict):
            for key in sorted(actual_value.keys() - template_value.keys()):
                items.append(format_unsupported_item(prefix, key, actual_value[key]))
            for key in sorted(actual_value.keys() & template_value.keys()):
                items.extend(collect_unsupported(template_value[key], actual_value[key], f"{prefix}.{key}"))
            return items

        if isinstance(template_value, list) and isinstance(actual_value, list):
            for actual_child in actual_value:
                if not any(template_matches(template_child, actual_child) for template_child in template_value):
                    items.append(format_unsupported_item(prefix, actual_child))
            return items

        if not template_matches(template_value, actual_value):
            items.append(format_unsupported_item(prefix, prefix.split(".")[-1], actual_value))
        return items

    for top_level_key in sorted(compose_data.keys() - recipe_compose.keys()):
        unsupported_items.append(
            format_unsupported_item("top_level", top_level_key, compose_data[top_level_key])
        )

    for top_level_key in sorted(compose_data.keys() & recipe_compose.keys()):
        unsupported_items.extend(
            collect_unsupported(recipe_compose[top_level_key], compose_data[top_level_key], top_level_key)
        )

    return unsupported_items


def mask_sensitive_value(key, value):
    sensitive_markers = ["key", "token", "secret", "password", "passwd"]
    if any(marker in key.lower() for marker in sensitive_markers):
        if not value:
            return "(set)"
        if len(value) <= 8:
            return "****"
        return f"{value[:4]}****{value[-2:]}"
    return value


def get_service_environment_summary(service_data):
    environment = []
    raw_environment = service_data.get("environment", [])
    if isinstance(raw_environment, dict):
        for key, value in raw_environment.items():
            environment.append({
                "key": key,
                "value": mask_sensitive_value(key, str(value))
            })
    elif isinstance(raw_environment, list):
        for item in raw_environment:
            if isinstance(item, str) and "=" in item:
                env_key, env_value = item.split("=", 1)
                environment.append({
                    "key": env_key,
                    "value": mask_sensitive_value(env_key, env_value)
                })
    return environment


def build_project_status(containers):
    if not containers:
        return "config only"
    if all(container["running"] for container in containers):
        return "running"
    if any(container["running"] for container in containers):
        return "mixed"

    member_states = [container["state"] for container in containers]
    if len(set(member_states)) == 1:
        return member_states[0]
    return "stopped"


def build_compose_summary_from_compose(compose_data, project_containers=None):
    services = compose_data.get("services", {})
    if not services:
        return {}

    project_containers = project_containers or []
    containers_by_service = {
        (container.get("service") or container["name"]): container
        for container in project_containers
    }

    service_summaries = []
    for service_name, service_data in services.items():
        ports = []
        for port in ensure_compose_list(service_data.get("ports")):
            if isinstance(port, str):
                ports.append(port)
            elif isinstance(port, dict):
                published = port.get("published")
                target = port.get("target")
                if published and target:
                    ports.append(f"{published}:{target}")

        volumes = [
            volume
            for volume in ensure_compose_list(service_data.get("volumes"))
            if isinstance(volume, str)
        ]
        matching_container = containers_by_service.get(service_name)
        service_summaries.append({
            "name": service_name,
            "container_name": service_data.get("container_name"),
            "status": matching_container.get("state") if matching_container else "config only",
            "image": service_data.get("image"),
            "command": service_data.get("command"),
            "entrypoint": service_data.get("entrypoint"),
            "ports": ports,
            "volumes": volumes,
            "restart": service_data.get("restart"),
            "network_mode": service_data.get("network_mode"),
            "cap_add": ensure_compose_list(service_data.get("cap_add")),
            "devices": ensure_compose_list(service_data.get("devices")),
            "environment": get_service_environment_summary(service_data)
        })

    return {
        "status": build_project_status(project_containers),
        "service_count": len(service_summaries),
        "services": service_summaries
    }


def build_compose_summary(compose_file, container_state=None):
    compose_data = get_compose_data(compose_file)
    return build_compose_summary_from_compose(
        compose_data,
        container_state or []
    )


def get_primary_port_from_summary(compose_summary):
    for service in compose_summary.get("services", []):
        ports = service.get("ports", [])
        if not ports:
            continue
        first_port = ports[0]
        if isinstance(first_port, str):
            return first_port.split(":")[0]
    return None

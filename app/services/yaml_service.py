import re


PLACEHOLDER_RE = re.compile(r"\$\{([A-Z0-9_]+)\}")


def build_field_values(recipe, form_data, project_name):
    values = {
        "PROJECT_NAME": project_name
    }

    for field in recipe.get("fields", []):
        field_name = field["name"]
        submitted_value = form_data.get(field_name)
        if submitted_value is None or submitted_value == "":
            submitted_value = field.get("default", "")
        values[field_name] = submitted_value

    return values


def substitute_placeholders(value, field_values):
    if isinstance(value, str):
        def replace(match):
            return str(field_values.get(match.group(1), ""))

        return PLACEHOLDER_RE.sub(replace, value)

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


def generate_compose(recipe, form_data, project_name):
    field_values = build_field_values(recipe, form_data, project_name)

    compose = {
        "services": {}
    }

    for service_name, service_template in recipe.get("services", {}).items():
        service = substitute_placeholders(service_template, field_values)
        compose["services"][service_name] = prune_compose_value(service)

    for top_level_key in ["volumes", "networks"]:
        if recipe.get(top_level_key):
            compose[top_level_key] = prune_compose_value(
                substitute_placeholders(recipe[top_level_key], field_values)
            )

    return compose


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

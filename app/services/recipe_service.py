import json

import bleach
from flask import g, has_request_context

from app_config import RECIPES_PATH, SAFE_RECIPE_NAME_RE
from services.docker_service import get_available_network_options
from services.host_path_service import build_project_host_path
from services.settings_service import load_settings
from services.yaml_service import PLACEHOLDER_RE, RESERVED_RECIPE_KEYS
ALLOWED_HELP_TAGS = ["a", "br", "code", "em", "strong", "ul", "ol", "li", "p"]
ALLOWED_HELP_ATTRIBUTES = {
    "a": ["href", "target", "rel"],
}
ALLOWED_HELP_PROTOCOLS = ["http", "https", "mailto"]
_RECIPE_CACHE = {}


def get_resource_field_name(service_name, resource_kind):
    normalized_service_name = "".join(
        char.upper() if char.isalnum() else "_"
        for char in service_name
    ).strip("_")
    normalized_kind = resource_kind.upper()
    return f"{normalized_service_name}_{normalized_kind}"


def ensure_advanced_section(ui):
    sections = list(ui.get("sections", []))
    if any(section.get("name") == "advanced" for section in sections):
        ui["sections"] = sections
        return ui

    sections.append({
        "name": "advanced",
        "label": "Advanced",
        "collapsed": True,
    })
    ui["sections"] = sections
    return ui


def inject_resource_limit_fields(recipe):
    fields = list(recipe.get("fields", []))
    services = recipe.get("services", {}) or {}
    service_count = len(services)

    for service_name in services.keys():
        for resource_kind, label_suffix, help_text in (
            (
                "cpu_limit",
                "CPU Limit",
                "Optional CPU limit for this service. Leave blank for no CPU limit. Example: 1.5",
            ),
            (
                "memory_limit",
                "Memory Limit",
                "Optional memory limit for this service. Leave blank for no memory limit. Example: 2g or 2048m",
            ),
        ):
            field_name = get_resource_field_name(service_name, resource_kind)
            if any(field.get("name") == field_name for field in fields):
                continue

            label = label_suffix if service_count == 1 else f"{service_name} {label_suffix}"
            fields.append({
                "name": field_name,
                "label": label,
                "section": "advanced",
                "input_type": "text",
                "required": False,
                "default": "",
                "resource_kind": resource_kind,
                "service_name": service_name,
                "help": help_text,
            })

    recipe["fields"] = fields
    return recipe


def is_safe_recipe_name(name):
    return bool(name) and bool(SAFE_RECIPE_NAME_RE.fullmatch(name))


def load_recipes():
    recipes = []
    if not RECIPES_PATH.exists():
        return recipes

    for file_name in RECIPES_PATH.iterdir():
        if file_name.suffix == ".json" and file_name.name != "index.json":
            cached_recipe = _load_cached_recipe(file_name)
            if cached_recipe:
                recipes.append(cached_recipe)

    recipes.sort(key=lambda recipe: (recipe.get("name") or "").lower())
    return recipes


def load_recipe_by_name(name):
    if not is_safe_recipe_name(name):
        return None

    recipe_file = RECIPES_PATH / f"{name}.json"
    if not recipe_file.exists():
        return None

    return _load_cached_recipe(recipe_file)


def _load_cached_recipe(recipe_file):
    try:
        stat = recipe_file.stat()
    except OSError:
        _RECIPE_CACHE.pop(str(recipe_file), None)
        return None

    cache_key = str(recipe_file)
    cached_entry = _RECIPE_CACHE.get(cache_key)
    cache_signature = (stat.st_mtime_ns, stat.st_size)
    if cached_entry and cached_entry["signature"] == cache_signature:
        return cached_entry["recipe"]

    with open(recipe_file) as handle:
        recipe = normalize_recipe(json.load(handle))

    _RECIPE_CACHE[cache_key] = {
        "signature": cache_signature,
        "recipe": recipe,
    }
    return recipe


def normalize_field(field):
    normalized = dict(field)

    section = normalized.get("section")
    if isinstance(section, str):
        normalized["section"] = section.strip().lower()

    editable = normalized.get("editable", True)
    if isinstance(editable, str):
        normalized["editable"] = editable.strip().lower() not in {"false", "0", "no"}
    else:
        normalized["editable"] = editable is not False

    help_text = normalized.get("help")
    if isinstance(help_text, str):
        normalized["help"] = bleach.clean(
            help_text,
            tags=ALLOWED_HELP_TAGS,
            attributes=ALLOWED_HELP_ATTRIBUTES,
            protocols=ALLOWED_HELP_PROTOCOLS,
            strip=True,
        )

    options_source = normalized.get("options_source")
    if isinstance(options_source, str):
        normalized["options_source"] = options_source.strip().lower()

    return normalized


def get_allowed_placeholder_names(fields):
    allowed = {"PROJECT_NAME"}
    for field in fields:
        field_name = field.get("name")
        if not field_name:
            continue
        allowed.add(field_name)
        if field.get("data_type") == "docker_network":
            allowed.update({
                f"{field_name}_MODE",
                f"{field_name}_SERVICE_NETWORKS",
                f"{field_name}_DEFINITIONS",
            })
    return allowed


def normalize_recipe(recipe):
    normalized = dict(recipe)
    normalized["fields"] = [normalize_field(field) for field in recipe.get("fields", [])]
    ui = dict(recipe.get("ui", {}))
    normalized_sections = []
    for section in ui.get("sections", []):
        normalized_section = dict(section)
        section_name = normalized_section.get("name")
        if isinstance(section_name, str):
            normalized_section["name"] = section_name.strip().lower()
        normalized_sections.append(normalized_section)
    ui["sections"] = normalized_sections
    normalized["ui"] = ensure_advanced_section(ui)
    normalized = inject_resource_limit_fields(normalized)
    validate_recipe(normalized)
    return normalized


def validate_recipe(recipe):
    fields = recipe.get("fields", [])
    field_names = set()
    for field in fields:
        field_name = field.get("name")
        if not field_name:
            raise ValueError("Recipe fields must have a name.")
        if field_name in field_names:
            raise ValueError(f"Duplicate field name found: {field_name}")
        field_names.add(field_name)

    ui = recipe.get("ui", {})
    ui_sections = ui.get("sections", [])
    if not ui_sections:
        raise ValueError("Recipes must define ui.sections.")

    section_names = []
    for section in ui_sections:
        section_name = section.get("name")
        if not section_name:
            raise ValueError("Each ui.sections entry must define a name.")
        section_names.append(section_name)

    if len(section_names) != len(set(section_names)):
        raise ValueError("ui.sections contains duplicate section names.")

    declared_sections = set(section_names)
    for field in fields:
        section_name = field.get("section")
        if not section_name:
            raise ValueError(f"Field {field['name']} must declare a section.")
        if section_name not in declared_sections:
            raise ValueError(
                f"Field {field['name']} uses unknown section '{section_name}'. "
                "Add it to ui.sections."
            )

    allowed_placeholders = get_allowed_placeholder_names(fields)
    for top_level_key, value in recipe.items():
        if top_level_key in RESERVED_RECIPE_KEYS:
            continue
        _validate_compose_value(value, allowed_placeholders, path=top_level_key)


def _validate_compose_value(value, allowed_placeholders, path):
    if isinstance(value, dict):
        for key, item in value.items():
            _validate_compose_value(item, allowed_placeholders, f"{path}.{key}")
        return

    if isinstance(value, list):
        for index, item in enumerate(value):
            _validate_compose_value(item, allowed_placeholders, f"{path}[{index}]")
        return

    if not isinstance(value, str):
        raise ValueError(
            f"Compose value at '{path}' must come from a field placeholder and therefore "
            "must be a string template."
        )

    placeholders = PLACEHOLDER_RE.findall(value)
    if not placeholders:
        raise ValueError(
            f"Compose value at '{path}' must include at least one field placeholder."
        )

    unknown = [name for name in placeholders if name not in allowed_placeholders]
    if unknown:
        raise ValueError(
            f"Compose value at '{path}' references unknown field(s): {', '.join(sorted(set(unknown)))}"
        )


def build_field_display_value(field, form_defaults=None, persistent_defaults=None):
    field_value = field.get("default", "")
    if form_defaults and field.get("name") in form_defaults:
        field_value = form_defaults.get(field.get("name"))
    elif persistent_defaults and field.get("name") in persistent_defaults:
        field_value = persistent_defaults.get(field.get("name"), field_value)

    if field.get("input_type") == "select" and field_value == "":
        options = field.get("options") or []
        if options:
            field_value = options[0].get("value", "")

    return field_value


def get_resolved_host_path(field_value, project_name):
    if not project_name or not isinstance(field_value, str):
        return None

    if not field_value.startswith("./"):
        return None

    return build_project_host_path(project_name, field_value)


def get_recipe_runtime_context(project_name=None):
    if has_request_context():
        runtime_context = getattr(g, "recipe_runtime_context", None)
        if runtime_context is None:
            runtime_context = {
                "persistent_defaults": load_settings(),
                "network_options": None,
                "project_host_paths": {},
            }
            g.recipe_runtime_context = runtime_context
    else:
        runtime_context = {
            "persistent_defaults": load_settings(),
            "network_options": None,
            "project_host_paths": {},
        }

    if project_name not in runtime_context["project_host_paths"]:
        runtime_context["project_host_paths"][project_name] = (
            build_project_host_path(project_name, "./") if project_name else None
        )

    return runtime_context


def build_recipe_field_sections(recipe, form_defaults=None, project_name=None):
    fields = recipe.get("fields", [])
    fields_by_section = {}
    runtime_context = get_recipe_runtime_context(project_name)
    project_host_path = runtime_context["project_host_paths"].get(project_name)
    persistent_defaults = runtime_context["persistent_defaults"]
    system_hardware = persistent_defaults
    for field in fields:
        field_copy = dict(field)
        if field_copy.get("options_source") == "docker_networks":
            if runtime_context["network_options"] is None:
                runtime_context["network_options"] = get_available_network_options()
            field_copy["options"] = runtime_context["network_options"]
        field_value = build_field_display_value(field_copy, form_defaults, persistent_defaults)
        availability_path = field_copy.get("availability_path")
        if availability_path:
            device_status = system_hardware.get("devices", {}).get(availability_path)
            if device_status is True:
                field_copy["availability_note"] = f"Detected in system configuration: {availability_path}"
            elif device_status is False:
                if field_value in (None, ""):
                    field_copy["disabled"] = True
                field_copy["disabled_reason"] = f"Not detected in system configuration: {availability_path}"
            else:
                if field_value in (None, ""):
                    field_copy["disabled"] = True
                field_copy["disabled_reason"] = (
                    field_copy.get("detection_required_message")
                    or f"Go to Persistent Variables and run Detect System Hardware for {availability_path}."
                )
        field_copy["current_value"] = field_value
        field_copy["project_host_path"] = project_host_path
        field_copy["show_resolved_host_path"] = (
            field_copy.get("editable", True)
            and field_copy.get("input_type") in {"text", "textarea"}
            and bool(project_host_path)
        )
        field_copy["resolved_host_path"] = get_resolved_host_path(field_value, project_name)
        if field_copy.get("resource_kind") == "cpu_limit" and system_hardware.get("cpu_count"):
            field_copy["system_hint"] = f"Detected host total: {system_hardware['cpu_count']} CPU threads"
        elif field_copy.get("resource_kind") == "memory_limit" and system_hardware.get("memory_human"):
            field_copy["system_hint"] = f"Detected host total: {system_hardware['memory_human']} RAM"
        section_name = field["section"]
        fields_by_section.setdefault(section_name, []).append(field_copy)

    section_groups = []
    for section in recipe.get("ui", {}).get("sections", []):
        section_name = section.get("name")
        if not section_name:
            continue
        section_fields = fields_by_section.get(section_name, [])
        if not section_fields:
            continue
        section_groups.append({
            "name": section_name,
            "label": section.get("label") or section_name.replace("_", " ").title(),
            "collapsed": bool(section.get("collapsed", False)),
            "fields": section_fields,
        })

    return {
        "section_groups": section_groups,
    }


def get_recipe_snapshot_path(app_folder):
    return app_folder / "recipe.snapshot.json"


def get_recipe_metadata_path(app_folder):
    return app_folder / "metadata.json"


def load_saved_recipe_snapshot(app_folder):
    recipe_snapshot_path = get_recipe_snapshot_path(app_folder)
    if not recipe_snapshot_path.exists():
        return None

    try:
        with open(recipe_snapshot_path) as handle:
            return normalize_recipe(json.load(handle))
    except Exception:
        return None


def save_recipe_snapshot(app_folder, recipe):
    recipe_snapshot_path = get_recipe_snapshot_path(app_folder)
    metadata_path = get_recipe_metadata_path(app_folder)

    with open(recipe_snapshot_path, "w") as handle:
        json.dump(recipe, handle, indent=2)

    with open(metadata_path, "w") as handle:
        json.dump(
            {
                "recipe_name": recipe.get("name"),
                "recipe_version": recipe.get("version")
            },
            handle,
            indent=2
        )


def get_recipe_from_compose(compose_file, compose_data_loader):
    app_folder = compose_file.parent
    saved_recipe = load_saved_recipe_snapshot(app_folder)
    if saved_recipe:
        latest_recipe = None
        recipe_name = saved_recipe.get("name")
        if recipe_name:
            latest_recipe = load_recipe_by_name(recipe_name)

        if latest_recipe:
            saved_version = saved_recipe.get("version", 0) or 0
            latest_version = latest_recipe.get("version", 0) or 0
            if latest_version >= saved_version:
                return latest_recipe, compose_data_loader(compose_file)

        return saved_recipe, compose_data_loader(compose_file)

    compose_data = compose_data_loader(compose_file)
    services = compose_data.get("services", {})
    if not services:
        return None, compose_data

    compose_service_names = set(services.keys())
    best_recipe = None
    best_score = None

    for recipe in load_recipes():
        recipe_service_names = set(recipe.get("services", {}).keys())
        if not recipe_service_names:
            continue

        overlap = len(compose_service_names & recipe_service_names)
        if overlap == 0:
            continue

        score = (
            overlap,
            -len(compose_service_names - recipe_service_names),
            -len(recipe_service_names - compose_service_names)
        )
        if best_score is None or score > best_score:
            best_recipe = recipe
            best_score = score

    if best_recipe:
        return best_recipe, compose_data

    project_name = compose_file.parent.name
    project_candidates = [project_name]
    if project_name.startswith("tsg_"):
        project_candidates.append(project_name[4:])

    for candidate in project_candidates:
        recipe = load_recipe_by_name(candidate)
        if recipe:
            return recipe, compose_data

    return None, compose_data

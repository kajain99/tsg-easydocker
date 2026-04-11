import json

from app_config import RECIPES_PATH, SAFE_RECIPE_NAME_RE


def is_safe_recipe_name(name):
    return bool(name) and bool(SAFE_RECIPE_NAME_RE.fullmatch(name))


def load_recipes():
    recipes = []
    if not RECIPES_PATH.exists():
        return recipes

    for file_name in RECIPES_PATH.iterdir():
        if file_name.suffix == ".json":
            with open(file_name) as handle:
                recipes.append(json.load(handle))

    return recipes


def load_recipe_by_name(name):
    if not is_safe_recipe_name(name):
        return None

    recipe_file = RECIPES_PATH / f"{name}.json"
    if not recipe_file.exists():
        return None

    with open(recipe_file) as handle:
        return json.load(handle)


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
            return json.load(handle)
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

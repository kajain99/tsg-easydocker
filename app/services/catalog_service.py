import os

from flask import request

from app_config import BASE_CONFIG
from services.compose_service import (
    build_app_links_from_compose,
    get_compose_data,
    get_port_from_compose,
)
from services.docker_service import get_all_containers_info
from services.recipe_service import get_recipe_from_compose, load_saved_recipe_snapshot


def get_app_links_for_config(project_name, compose_file):
    recipe = load_saved_recipe_snapshot(compose_file.parent)
    if not recipe:
        recipe, compose_data = get_recipe_from_compose(compose_file, get_compose_data)
    else:
        compose_data = get_compose_data(compose_file)

    if not recipe:
        return []

    return build_app_links_from_compose(recipe, compose_data, project_name, request.host.split(":")[0])


def build_installed_apps():
    all_projects = []
    orphan_configs = []
    all_containers_info = get_all_containers_info()
    containers_by_project = {}
    managed_projects = {}

    if BASE_CONFIG.exists():
        for folder in os.listdir(BASE_CONFIG):
            full_path = BASE_CONFIG / folder
            if folder == "recipes":
                continue
            if full_path.is_dir():
                compose_file = full_path / "docker-compose.yml"
                if compose_file.exists():
                    managed_projects[folder] = {
                        "name": folder,
                        "port": get_port_from_compose(compose_file),
                        "app_links": get_app_links_for_config(folder, compose_file),
                        "reviewable": True
                    }

    for container in all_containers_info:
        project_name = container.get("project") or ""
        if project_name:
            containers_by_project.setdefault(project_name, []).append(container)
        else:
            all_projects.append({
                "name": container["name"],
                "source": "external_container",
                "port": None,
                "reviewable": False,
                "members": [{
                    "name": container["service"] or container["name"],
                    "state": container["state"],
                    "running": container["running"]
                }]
            })

    for project_name, project_meta in managed_projects.items():
        members = sorted(
            containers_by_project.get(project_name, []),
            key=lambda item: item["service"] or item["name"]
        )
        if members:
            member_states = [member["state"] for member in members]
            project_state = "running" if all(member["running"] for member in members) else "mixed"
            if not any(member["running"] for member in members):
                project_state = member_states[0] if len(set(member_states)) == 1 else "stopped"
            all_projects.append({
                "name": project_name,
                "source": "easydocker",
                "port": project_meta["port"],
                "app_links": project_meta["app_links"],
                "reviewable": True,
                "state": project_state,
                "members": [
                    {
                        "name": member["service"] or member["name"],
                        "state": member["state"],
                        "running": member["running"]
                    }
                    for member in members
                ]
            })
        else:
            orphan_configs.append({
                "name": project_name,
                "port": project_meta["port"],
                "app_links": project_meta["app_links"],
                "reviewable": True
            })

    for project_name, members in containers_by_project.items():
        if project_name not in managed_projects:
            sorted_members = sorted(members, key=lambda item: item["service"] or item["name"])
            member_states = [member["state"] for member in sorted_members]
            project_state = "running" if all(member["running"] for member in sorted_members) else "mixed"
            if not any(member["running"] for member in sorted_members):
                project_state = member_states[0] if len(set(member_states)) == 1 else "stopped"
            all_projects.append({
                "name": project_name,
                "source": "external_project",
                "port": None,
                "reviewable": False,
                "state": project_state,
                "members": [
                    {
                        "name": member["service"] or member["name"],
                        "state": member["state"],
                        "running": member["running"]
                    }
                    for member in sorted_members
                ]
            })

    all_projects.sort(key=lambda item: item["name"])
    orphan_configs.sort(key=lambda item: item["name"])

    return all_projects, orphan_configs

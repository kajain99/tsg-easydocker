import os

from flask import request

from app_config import BASE_CONFIG
from services.compose_service import (
    build_app_links_from_compose,
    build_compose_summary_from_compose,
    get_primary_port_from_summary,
    build_project_status,
    get_compose_data,
)
from services.docker_service import get_all_containers_info
from services.recipe_service import get_recipe_from_compose


def get_project_config_metadata(project_name, compose_file):
    compose_data = get_compose_data(compose_file)
    compose_summary = build_compose_summary_from_compose(compose_data)
    recipe, _ = get_recipe_from_compose(compose_file, lambda _compose_file: compose_data)

    app_links = []
    if recipe:
        app_links = build_app_links_from_compose(
            recipe,
            compose_data,
            project_name,
            request.host.split(":")[0]
        )

    return {
        "port": get_primary_port_from_summary(compose_summary),
        "app_links": app_links,
        "reviewable": True,
    }


def format_member(container):
    return {
        "name": container["service"] or container["name"],
        "state": container["state"],
        "running": container["running"]
    }


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
                    managed_projects[folder] = get_project_config_metadata(folder, compose_file)

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
                "members": [format_member(container)]
            })

    for project_name, project_meta in managed_projects.items():
        members = sorted(
            containers_by_project.get(project_name, []),
            key=lambda item: item["service"] or item["name"]
        )
        if members:
            all_projects.append({
                "name": project_name,
                "source": "easydocker",
                "port": project_meta["port"],
                "app_links": project_meta["app_links"],
                "reviewable": True,
                "state": build_project_status(members),
                "members": [format_member(member) for member in members]
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
            all_projects.append({
                "name": project_name,
                "source": "external_project",
                "port": None,
                "reviewable": False,
                "state": build_project_status(sorted_members),
                "members": [format_member(member) for member in sorted_members]
            })

    all_projects.sort(key=lambda item: item["name"])
    orphan_configs.sort(key=lambda item: item["name"])

    return all_projects, orphan_configs

import json
import os
import subprocess
from functools import lru_cache

from flask import g, has_request_context

from app_config import BASE_CONFIG, SAFE_CONTAINER_NAME_RE
from services.compose_service import (
    build_compose_summary,
    build_unsupported_compose_items,
    get_compose_data,
)
from services.host_path_service import get_current_container_id


@lru_cache(maxsize=1)
def get_compose_command():
    try:
        result = subprocess.run(
            ["docker", "compose", "version"],
            capture_output=True,
            text=True
        )
        if result.returncode == 0:
            return ["docker", "compose"]
    except Exception:
        pass
    return ["docker-compose"]


def get_available_network_options():
    options = [
        {
            "label": "Create New Bridge",
            "value": "__create_bridge__"
        },
        {
            "label": "Host Mode",
            "value": "__host__"
        },
    ]

    discovered = []
    try:
        result = subprocess.run(
            ["docker", "network", "ls", "--format", "{{.Name}}\t{{.Driver}}"],
            capture_output=True,
            text=True
        )
        if result.returncode == 0:
            for line in result.stdout.splitlines():
                if not line.strip():
                    continue
                parts = line.split("\t", 1)
                name = parts[0].strip()
                driver = parts[1].strip() if len(parts) > 1 else ""
                if not name or driver in {"", "null"}:
                    continue
                if name in {"bridge", "host", "none"}:
                    continue
                discovered.append({
                    "label": f"{name} ({driver})",
                    "value": f"__external__:{name}"
                })
    except Exception:
        pass

    discovered.sort(key=lambda item: item["label"].lower())
    options.extend(discovered)
    return options


def get_docker_network_driver(network_name):
    if not isinstance(network_name, str) or not network_name.strip():
        return None
    network_name = network_name.strip()

    cache = None
    if has_request_context():
        cache = getattr(g, "docker_network_driver_cache", None)
        if cache is None:
            cache = {}
            g.docker_network_driver_cache = cache
        if network_name in cache:
            return cache[network_name]

    driver = None
    try:
        result = subprocess.run(
            [
                "docker", "network", "inspect", network_name,
                "--format", "{{.Driver}}",
            ],
            capture_output=True,
            text=True,
        )
        if result.returncode == 0:
            driver = result.stdout.strip().lower() or None
    except Exception:
        driver = None

    if cache is not None:
        cache[network_name] = driver
    return driver


def recipe_uses_macvlan_network(recipe, form_data):
    external_prefix = "__external__:"
    for field in recipe.get("fields", []):
        if field.get("data_type") != "docker_network":
            continue
        selected_network = form_data.get(field.get("name"), field.get("default", ""))
        if not isinstance(selected_network, str) or not selected_network.startswith(external_prefix):
            continue
        network_name = selected_network[len(external_prefix):].strip()
        if get_docker_network_driver(network_name) == "macvlan":
            return True
    return False


def compose_uses_macvlan_network(compose_data):
    networks = compose_data.get("networks", {}) if isinstance(compose_data, dict) else {}
    if not isinstance(networks, dict):
        return False

    for network_key, network_config in networks.items():
        if not isinstance(network_config, dict) or network_config.get("external") is not True:
            continue
        network_name = network_config.get("name") or network_key
        if get_docker_network_driver(network_name) == "macvlan":
            return True
    return False


def inspect_macvlan_networks():
    try:
        list_result = subprocess.run(
            [
                "docker", "network", "ls",
                "--filter", "driver=macvlan",
                "--format", "{{.Name}}",
            ],
            capture_output=True,
            text=True,
        )
    except Exception:
        return {
            "networks": [],
            "error": "Unable to inspect Docker macvlan networks.",
        }

    if list_result.returncode != 0:
        return {
            "networks": [],
            "error": (list_result.stderr or "").strip()
            or "Unable to inspect Docker macvlan networks.",
        }

    network_names = sorted({
        line.strip()
        for line in list_result.stdout.splitlines()
        if line.strip()
    }, key=str.lower)
    if not network_names:
        return {"networks": [], "error": None}

    try:
        inspect_result = subprocess.run(
            ["docker", "network", "inspect", *network_names],
            capture_output=True,
            text=True,
        )
    except Exception:
        return {
            "networks": [],
            "error": "Unable to inspect Docker macvlan network details.",
        }

    if inspect_result.returncode != 0:
        return {
            "networks": [],
            "error": (inspect_result.stderr or "").strip()
            or "Unable to inspect Docker macvlan network details.",
        }

    try:
        inspected_networks = json.loads(inspect_result.stdout or "[]")
    except (TypeError, ValueError):
        return {
            "networks": [],
            "error": "Docker returned invalid macvlan network details.",
        }

    networks = []
    for network in inspected_networks:
        if not isinstance(network, dict) or network.get("Driver") != "macvlan":
            continue

        ipam_configs = network.get("IPAM", {}).get("Config", [])
        if not isinstance(ipam_configs, list):
            ipam_configs = []
        subnets = sorted({
            config.get("Subnet", "").strip()
            for config in ipam_configs
            if isinstance(config, dict) and config.get("Subnet")
        })
        gateways = sorted({
            config.get("Gateway", "").strip()
            for config in ipam_configs
            if isinstance(config, dict) and config.get("Gateway")
        })

        containers = []
        network_containers = network.get("Containers", {})
        if isinstance(network_containers, dict):
            for container_id, container in network_containers.items():
                if not isinstance(container, dict):
                    continue
                ipv4_address = (container.get("IPv4Address") or "").split("/", 1)[0]
                containers.append({
                    "id": container_id,
                    "name": container.get("Name") or container_id[:12],
                    "ipv4_address": ipv4_address,
                })
        containers.sort(key=lambda item: item["name"].lower())

        options = network.get("Options", {})
        networks.append({
            "name": network.get("Name") or "Unnamed macvlan network",
            "subnets": subnets,
            "gateways": gateways,
            "parent": options.get("parent", "") if isinstance(options, dict) else "",
            "containers": containers,
        })

    networks.sort(key=lambda item: item["name"].lower())
    return {"networks": networks, "error": None}


def _get_current_container_image():
    if has_request_context() and hasattr(g, "current_container_image"):
        return g.current_container_image

    container_id = get_current_container_id()
    if not container_id:
        return None

    image_name = None
    try:
        result = subprocess.run(
            ["docker", "inspect", container_id, "--format", "{{.Config.Image}}"],
            capture_output=True,
            text=True
        )
        if result.returncode == 0:
            image_name = result.stdout.strip() or None
    except Exception:
        image_name = None

    if has_request_context():
        g.current_container_image = image_name

    return image_name


def _docker_host_path_exists(path):
    helper_image = _get_current_container_image()
    if not helper_image:
        return False

    check_command = "test -e /host-check"
    if path == "/dev/dri":
        check_command = (
            "test -d /host-check && "
            "find /host-check -maxdepth 1 "
            "\\( -name 'card*' -o -name 'renderD*' \\) | grep -q ."
        )
    elif path == "/dev/dvb":
        check_command = (
            "test -d /host-check && "
            "find /host-check -mindepth 1 -maxdepth 2 "
            "\\( -name 'adapter*' -o -name 'demux*' -o -name 'dvr*' -o -name 'frontend*' -o -name 'net*' \\) "
            "| grep -q ."
        )

    try:
        result = subprocess.run(
            [
                "docker", "run", "--rm",
                "--entrypoint", "sh",
                "--mount", f"type=bind,src={path},dst=/host-check,readonly",
                helper_image,
                "-c", check_command
            ],
            capture_output=True,
            text=True
        )
        return result.returncode == 0
    except Exception:
        return False


def host_path_exists(path):
    if not path:
        return False

    if has_request_context():
        cache = getattr(g, "host_path_exists_cache", None)
        if cache is None:
            cache = {}
            g.host_path_exists_cache = cache
        if path in cache:
            return cache[path]

    if path.startswith("/dev/"):
        exists = _docker_host_path_exists(path)
    else:
        exists = os.path.exists(path)

    if has_request_context():
        g.host_path_exists_cache[path] = exists

    return exists


def detect_host_paths(paths):
    results = {}
    for path in paths:
        if not isinstance(path, str):
            continue
        normalized_path = path.strip()
        if not normalized_path or not normalized_path.startswith("/dev/"):
            continue
        results[normalized_path] = host_path_exists(normalized_path)
    return results


def is_safe_container_name(name):
    return bool(name) and bool(SAFE_CONTAINER_NAME_RE.fullmatch(name))


def get_all_containers_info():
    if has_request_context() and hasattr(g, "all_containers_info"):
        return g.all_containers_info

    try:
        result = subprocess.run(
            [
                "docker", "ps", "-a", "--format",
                "{{.Names}}\t{{.Image}}\t{{.State}}\t{{.Label \"com.docker.compose.project\"}}\t{{.Label \"com.docker.compose.service\"}}"
            ],
            capture_output=True,
            text=True
        )
        if result.returncode == 0:
            containers = []
            for line in result.stdout.splitlines():
                if not line.strip():
                    continue
                parts = line.split("\t")
                name = parts[0].strip() if len(parts) > 0 else ""
                image = parts[1].strip() if len(parts) > 1 else ""
                state = parts[2].strip() if len(parts) > 2 else ""
                project = parts[3].strip() if len(parts) > 3 else ""
                service = parts[4].strip() if len(parts) > 4 else ""
                if name:
                    containers.append({
                        "name": name,
                        "image": image,
                        "state": state,
                        "running": state == "running",
                        "project": project,
                        "service": service
                    })
            if has_request_context():
                g.all_containers_info = containers
            return containers
    except Exception:
        pass

    if has_request_context():
        g.all_containers_info = []

    return []


def get_all_project_names():
    project_names = {
        container["project"]
        for container in get_all_containers_info()
        if container.get("project")
    }

    if BASE_CONFIG.exists():
        for folder in os.listdir(BASE_CONFIG):
            full_path = BASE_CONFIG / folder
            if folder == "recipes":
                continue
            if full_path.is_dir() and (full_path / "docker-compose.yml").exists():
                project_names.add(folder)

    return project_names


def get_running_containers_info():
    if has_request_context() and hasattr(g, "running_containers_info"):
        return g.running_containers_info

    all_containers_info = get_all_containers_info()
    running_containers = [
        {
            "name": container["name"],
            "image": container["image"]
        }
        for container in all_containers_info
        if container["running"]
    ]
    if has_request_context():
        g.running_containers_info = running_containers
    return running_containers


def build_container_name(app_name):
    safe_name = "".join(
        char.lower() if char.isalnum() else "_"
        for char in app_name
    ).strip("_")
    return f"tsg_{safe_name}"


def get_next_container_name(app_name):
    base_name = build_container_name(app_name)
    existing_names = get_all_project_names()

    if base_name not in existing_names:
        return base_name

    suffix = 2
    while f"{base_name}_{suffix}" in existing_names:
        suffix += 1

    return f"{base_name}_{suffix}"


def find_duplicate_containers(app_name, image_name):
    container_prefix = build_container_name(app_name)
    duplicates = []
    image_prefix = image_name.split("${", 1)[0] if image_name else ""

    for container in get_running_containers_info():
        duplicate_reasons = []
        if container["name"].startswith(container_prefix):
            duplicate_reasons.append("container name matches app prefix")
        if image_name and (
            container["image"] == image_name or
            (image_prefix and container["image"].startswith(image_prefix))
        ):
            duplicate_reasons.append("image matches app image")

        if duplicate_reasons:
            duplicates.append({
                "name": container["name"],
                "image": container["image"],
                "reasons": duplicate_reasons
            })

    return duplicates


def get_primary_recipe_image(recipe):
    for service in recipe.get("services", {}).values():
        image = service.get("image")
        if image:
            return image
    return ""


def build_existing_config_context(container_name, recipe=None):
    if not container_name:
        return {}

    app_folder = BASE_CONFIG / container_name
    yaml_path = app_folder / "docker-compose.yml"
    if not yaml_path.exists():
        return {"existing_config_name": container_name}

    all_containers_info = get_all_containers_info()
    project_containers = [
        container
        for container in all_containers_info
        if container.get("project") == container_name or container["name"] == container_name
    ]
    return {
        "existing_config_name": container_name,
        "existing_config_path": str(yaml_path),
        "redeploy_mode": True,
        "compose_summary": build_compose_summary(yaml_path, project_containers),
        "unsupported_config_items": build_unsupported_compose_items(recipe, get_compose_data(yaml_path)) if recipe else []
    }

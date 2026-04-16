import json
import os
import subprocess
from pathlib import PurePosixPath

from app_config import BASE_CONFIG


_HOST_BASE_CONFIG_PATH = None
_HOST_BASE_CONFIG_PATH_LOADED = False


def _get_current_container_id():
    hostname = os.environ.get("HOSTNAME", "").strip()
    if hostname:
        return hostname

    try:
        with open("/etc/hostname") as handle:
            return handle.read().strip()
    except Exception:
        return None


def _detect_host_base_config_path():
    configured_path = os.environ.get("EASYDOCKER_HOST_BASE_CONFIG", "").strip()
    if configured_path:
        return configured_path

    container_id = _get_current_container_id()
    if not container_id:
        return None

    try:
        result = subprocess.run(
            ["docker", "inspect", container_id, "--format", "{{json .Mounts}}"],
            capture_output=True,
            text=True
        )
        if result.returncode != 0 or not result.stdout.strip():
            return None

        mounts = json.loads(result.stdout)
        for mount in mounts:
            if mount.get("Destination") == str(BASE_CONFIG):
                return mount.get("Source")
    except Exception:
        return None

    return None


def get_host_base_config_path():
    global _HOST_BASE_CONFIG_PATH
    global _HOST_BASE_CONFIG_PATH_LOADED

    if _HOST_BASE_CONFIG_PATH_LOADED:
        return _HOST_BASE_CONFIG_PATH

    _HOST_BASE_CONFIG_PATH = _detect_host_base_config_path()
    _HOST_BASE_CONFIG_PATH_LOADED = True
    return _HOST_BASE_CONFIG_PATH


def build_project_host_path(project_name, relative_path):
    host_base_config = get_host_base_config_path()
    if not host_base_config:
        return None

    relative_suffix = relative_path[2:] if relative_path.startswith("./") else relative_path
    return str(PurePosixPath(host_base_config) / project_name / relative_suffix)

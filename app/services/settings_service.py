import json
import subprocess
import time

from app_config import LEGACY_SETTINGS_FILE, LEGACY_SYSTEM_HW_FILE, SYSTEM_CONFIG_FILE
from services.docker_service import detect_host_paths


PERSISTENT_VARIABLE_FIELDS = ("PUID", "PGID", "TZ")
SYSTEM_DEVICE_PATHS = ("/dev/dri", "/dev/dvb")


def get_default_settings():
    defaults = {field_name: "" for field_name in PERSISTENT_VARIABLE_FIELDS}
    defaults.update({
        "cpu_count": None,
        "memory_bytes": None,
        "memory_human": "",
        "devices": {path: None for path in SYSTEM_DEVICE_PATHS},
        "gpus": [],
        "updated_at": None,
    })
    return defaults


def format_memory_bytes(memory_bytes):
    if not isinstance(memory_bytes, int) or memory_bytes <= 0:
        return ""

    units = ["B", "KiB", "MiB", "GiB", "TiB"]
    size = float(memory_bytes)
    unit_index = 0
    while size >= 1024 and unit_index < len(units) - 1:
        size /= 1024
        unit_index += 1

    if unit_index == 0:
        return f"{int(size)} {units[unit_index]}"
    return f"{size:.1f} {units[unit_index]}"


def normalize_settings(raw_settings):
    normalized = get_default_settings()
    if not isinstance(raw_settings, dict):
        return normalized

    for field_name in PERSISTENT_VARIABLE_FIELDS:
        value = raw_settings.get(field_name, "")
        normalized[field_name] = value.strip() if isinstance(value, str) else ""

    cpu_count = raw_settings.get("cpu_count")
    memory_bytes = raw_settings.get("memory_bytes")
    normalized["cpu_count"] = cpu_count if isinstance(cpu_count, int) and cpu_count > 0 else None
    normalized["memory_bytes"] = memory_bytes if isinstance(memory_bytes, int) and memory_bytes > 0 else None
    normalized["memory_human"] = format_memory_bytes(normalized["memory_bytes"])

    devices = raw_settings.get("devices", {})
    if isinstance(devices, dict):
        for path in SYSTEM_DEVICE_PATHS:
            value = devices.get(path)
            normalized["devices"][path] = value if isinstance(value, bool) else None

    gpus = raw_settings.get("gpus")
    if isinstance(gpus, list):
        normalized["gpus"] = gpus

    updated_at = raw_settings.get("updated_at")
    if isinstance(updated_at, int):
        normalized["updated_at"] = updated_at

    return normalized


def write_settings(settings):
    normalized = normalize_settings(settings)
    with open(SYSTEM_CONFIG_FILE, "w") as handle:
        json.dump(normalized, handle, indent=2)
    return normalized


def load_legacy_settings():
    merged = {}

    if LEGACY_SETTINGS_FILE.exists():
        try:
            with open(LEGACY_SETTINGS_FILE) as handle:
                legacy_settings = json.load(handle)
            if isinstance(legacy_settings, dict):
                merged.update(legacy_settings)
        except Exception:
            pass

    if LEGACY_SYSTEM_HW_FILE.exists():
        try:
            with open(LEGACY_SYSTEM_HW_FILE) as handle:
                legacy_system_hw = json.load(handle)
            if isinstance(legacy_system_hw, dict):
                merged.update(legacy_system_hw)
        except Exception:
            pass

    return normalize_settings(merged)


def load_settings():
    if SYSTEM_CONFIG_FILE.exists():
        try:
            with open(SYSTEM_CONFIG_FILE) as handle:
                return normalize_settings(json.load(handle))
        except Exception:
            return get_default_settings()

    legacy_settings = load_legacy_settings()
    if (
        any(legacy_settings.get(field_name) for field_name in PERSISTENT_VARIABLE_FIELDS)
        or legacy_settings.get("cpu_count")
        or legacy_settings.get("memory_bytes")
        or any(value is not None for value in legacy_settings.get("devices", {}).values())
    ):
        return write_settings(legacy_settings)

    return get_default_settings()


def save_settings(settings):
    normalized = load_settings()
    for field_name in PERSISTENT_VARIABLE_FIELDS:
        value = settings.get(field_name, "")
        normalized[field_name] = value.strip() if isinstance(value, str) else ""
    return write_settings(normalized)


def detect_system_hardware():
    detected = {}

    try:
        result = subprocess.run(
            ["docker", "info", "--format", "{{json .}}"],
            capture_output=True,
            text=True
        )
        if result.returncode == 0:
            info = json.loads(result.stdout or "{}")
            cpu_count = info.get("NCPU")
            memory_bytes = info.get("MemTotal")
            if isinstance(cpu_count, int) and cpu_count > 0:
                detected["cpu_count"] = cpu_count
            if isinstance(memory_bytes, int) and memory_bytes > 0:
                detected["memory_bytes"] = memory_bytes
    except Exception:
        pass

    detected["devices"] = detect_host_paths(list(SYSTEM_DEVICE_PATHS))
    detected["updated_at"] = int(time.time())
    return normalize_settings(detected)


def detect_and_save_system_hardware(settings=None):
    normalized = load_settings()
    if settings is not None:
        normalized = save_settings(settings)

    detected = detect_system_hardware()
    normalized["cpu_count"] = detected.get("cpu_count")
    normalized["memory_bytes"] = detected.get("memory_bytes")
    normalized["memory_human"] = detected.get("memory_human", "")
    normalized["devices"] = detected.get("devices", normalized.get("devices", {}))
    normalized["gpus"] = detected.get("gpus", normalized.get("gpus", []))
    normalized["updated_at"] = detected.get("updated_at")
    return write_settings(normalized)

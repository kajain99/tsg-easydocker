import os
import re
from pathlib import Path


APP_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = APP_DIR.parent
DEFAULT_BASE_CONFIG = Path("/base_config")
LOCAL_BASE_CONFIG = PROJECT_ROOT / "base_config"
BASE_CONFIG = Path(
    os.environ.get(
        "EASYDOCKER_BASE_CONFIG",
        DEFAULT_BASE_CONFIG if DEFAULT_BASE_CONFIG.exists() else LOCAL_BASE_CONFIG
    )
)
RECIPES_PATH = BASE_CONFIG / "recipes"
GITHUB_BASE = "https://raw.githubusercontent.com/kajain99/tsg-EasyDocker-recipes/main/recipes"
SAFE_RECIPE_NAME_RE = re.compile(r"^[A-Za-z0-9_-]+$")
SAFE_CONTAINER_NAME_RE = re.compile(r"^[A-Za-z0-9_]+$")
DEFAULT_USERNAME = "admin"
EASYDOCKER_USERNAME = os.environ.get("EASYDOCKER_USERNAME", DEFAULT_USERNAME)
EASYDOCKER_PASSWORD = os.environ.get("EASYDOCKER_PASSWORD", "").strip()

if not EASYDOCKER_PASSWORD:
    raise RuntimeError(
        "EASYDOCKER_PASSWORD is required.\n"
        "Run EasyDocker with:\n"
        "docker run -d -p 5000:5000 "
        "-e EASYDOCKER_PASSWORD=your-strong-password "
        "-v /var/run/docker.sock:/var/run/docker.sock "
        "-v ./base_config:/base_config "
        "--name easydocker easydocker"
    )

"""XDG paths and the app's names, gi-free so the engine process and tests without GTK can import them."""

from __future__ import annotations

import os
from pathlib import Path

# Tests and headless screenshots override the id so they never talk to a running instance.
APP_ID = os.environ.get("AI_LINUX_APP_ID", "io.github.itsdigvijaysing.AILinux")
APP_NAME = "AI Linux"
ICON_NAME = "io.github.itsdigvijaysing.AILinux"  # fixed even when tests override APP_ID


def _xdg(var: str, default: str) -> Path:
    return Path(os.environ.get(var) or Path.home() / default)


CONFIG_DIR = _xdg("XDG_CONFIG_HOME", ".config") / "ai-linux"
DATA_DIR = _xdg("XDG_DATA_HOME", ".local/share") / "ai-linux"
STATE_DIR = _xdg("XDG_STATE_HOME", ".local/state") / "ai-linux"
CACHE_DIR = _xdg("XDG_CACHE_HOME", ".cache") / "ai-linux"
# v2's launcher owns settings.json; v3 keeps its own file until v2 is removed.
SETTINGS_FILE = CONFIG_DIR / "app.json"
MODELS_DIR = DATA_DIR / "models"
HISTORY_DB = DATA_DIR / "history.db"
LOG_DIR = STATE_DIR / "logs"
PACKAGE_DIR = Path(__file__).resolve().parent
ASSETS_DIR = PACKAGE_DIR / "data"
REPO_ROOT = PACKAGE_DIR.parents[1]


def resource_path(relative_path: str) -> Path:
    """A model file: the user's data dir first, then a source checkout (development)."""
    for base in (DATA_DIR, REPO_ROOT):
        p = base / relative_path
        if p.exists():
            return p
    return DATA_DIR / relative_path

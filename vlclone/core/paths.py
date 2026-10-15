"""Per-user data locations (no Qt dependency)."""

from __future__ import annotations

import os
import sys
from pathlib import Path

from .. import APP_NAME


def config_dir() -> Path:
    override = os.environ.get("VLCLONE_CONFIG_DIR")
    if override:
        base = Path(override)
    elif sys.platform == "win32":
        base = Path(os.environ.get("APPDATA", Path.home() / "AppData" / "Roaming")) / APP_NAME
    elif sys.platform == "darwin":
        base = Path.home() / "Library" / "Application Support" / APP_NAME
    else:
        base = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / APP_NAME.lower()
    base.mkdir(parents=True, exist_ok=True)
    return base


def data_dir() -> Path:
    """Where larger app data lives (media library database, thumbnail cache)."""
    override = os.environ.get("VLCLONE_DATA_DIR")
    if override:
        base = Path(override)
    elif os.environ.get("VLCLONE_CONFIG_DIR"):
        base = Path(os.environ["VLCLONE_CONFIG_DIR"]) / "data"
    elif sys.platform == "win32":
        base = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local")) / APP_NAME
    elif sys.platform == "darwin":
        base = Path.home() / "Library" / "Application Support" / APP_NAME
    else:
        base = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share")) / APP_NAME.lower()
    base.mkdir(parents=True, exist_ok=True)
    return base


def default_snapshot_dir() -> str:
    pictures = Path.home() / "Pictures"
    return str(pictures if pictures.is_dir() else Path.home())

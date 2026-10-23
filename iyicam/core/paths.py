"""Per-user data locations (overridable with the ``IYICAM_HOME`` environment variable)."""
from __future__ import annotations

import os
from pathlib import Path


def user_dir() -> Path:
    base = os.environ.get("IYICAM_HOME")
    p = Path(base) if base else Path.home() / ".iyicam"
    return p


def ensure(p: Path) -> Path:
    p.mkdir(parents=True, exist_ok=True)
    return p


def plugins_dir() -> Path:
    return user_dir() / "plugins"


def posts_dir() -> Path:
    return user_dir() / "posts"


def tools_file() -> Path:
    return user_dir() / "tools.json"


def settings_file() -> Path:
    return user_dir() / "settings.json"

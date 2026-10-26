"""File dialogs that start in the folder last used for that kind of file."""
from __future__ import annotations

import os

from PySide6.QtWidgets import QFileDialog

from .settings import Settings


def _start(settings: Settings | None, kind: str, name: str = "", fallback: str = "") -> str:
    """Where a file dialog starts: the folder last used for this kind of file, else ``fallback``."""
    d = settings.last_dir(kind) if settings is not None else ""
    d = d or (fallback if fallback and os.path.isdir(fallback) else "")
    return os.path.join(d, name) if d and name else (d or name)


def open_file(parent, settings: Settings | None, kind: str, title: str, filt: str = "") -> str:
    """The file picked ('' when cancelled)."""
    path, _ = QFileDialog.getOpenFileName(parent, title, _start(settings, kind), filt)
    if path and settings is not None:
        settings.set_last_dir(kind, path)
    return path or ""


def open_files(parent, settings: Settings | None, kind: str, title: str, filt: str = "") -> list[str]:
    paths, _ = QFileDialog.getOpenFileNames(parent, title, _start(settings, kind), filt)
    if paths and settings is not None:
        settings.set_last_dir(kind, paths[0])
    return list(paths or [])


def save_file(parent, settings: Settings | None, kind: str, title: str, name: str = "",
              filt: str = "", fallback: str = "") -> tuple[str, str]:
    """``(path, chosen filter)``; the path is '' when cancelled. ``name`` is the file name suggested, in the
    folder last used for this kind of file, else in ``fallback``."""
    path, chosen = QFileDialog.getSaveFileName(parent, title, _start(settings, kind, name, fallback), filt)
    if path and settings is not None:
        settings.set_last_dir(kind, path)
    return path or "", chosen or ""


def open_dir(parent, settings: Settings | None, kind: str, title: str) -> str:
    d = QFileDialog.getExistingDirectory(parent, title, _start(settings, kind))
    if d and settings is not None:
        settings.set_last_dir(kind, d)
    return d or ""

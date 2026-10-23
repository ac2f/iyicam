"""Application start-up: settings, plugins, main window."""
from __future__ import annotations

import sys

from ..builtins import ensure_registered, load_user_posts
from ..core import paths
from ..core.registry import REGISTRY
from ..plugins.manager import PluginManager


def create_window(argv: list[str] | None = None, load_plugins: bool = True):
    """Build the application objects; returns ``(app, window)`` without entering the event loop."""
    from PySide6.QtWidgets import QApplication

    from .controller import Controller
    from .main_window import MainWindow
    from .settings import Settings

    app = QApplication.instance() or QApplication(argv if argv is not None else sys.argv)
    app.setApplicationName("iyiCAM")
    app.setOrganizationName("iyiCAM")
    ensure_registered(REGISTRY)
    try:
        load_user_posts(REGISTRY)
    except Exception as exc:  # a broken user post must not stop the application
        print(f"iyiCAM: user posts: {exc}", file=sys.stderr)
    settings = Settings.load()
    pm = PluginManager(REGISTRY, [paths.plugins_dir()], settings.disabled_plugins)
    if load_plugins:
        for info in pm.load_all():
            if info.error:
                print(f"iyiCAM: plugin {info.key} failed:\n{info.error}", file=sys.stderr)
    ctrl = Controller(REGISTRY, settings)
    win = MainWindow(ctrl, pm)
    return app, win


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv if argv is None else argv)
    app, win = create_window(argv)
    files = [a for a in argv[1:] if not a.startswith("-")]
    if files:
        if files[0].lower().endswith(".icam"):
            win.ctrl.open_project(files[0])
        else:
            win.import_vectors(files[0], {})
    win.show()
    win.canvas.zoom_extents()
    return app.exec()

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

    from . import theme
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
    theme.apply(app, settings.theme)
    app.setWindowIcon(theme.app_icon())
    pm = PluginManager(REGISTRY, [paths.plugins_dir()], settings.disabled_plugins)
    if load_plugins:
        for info in pm.load_all():
            if info.error:
                print(f"iyiCAM: plugin {info.key} failed:\n{info.error}", file=sys.stderr)
    ctrl = Controller(REGISTRY, settings)
    win = MainWindow(ctrl, pm)
    return app, win


def crash_log_file():
    return paths.user_dir() / "error.log"


def install_crash_log(show=None) -> None:
    """An error that nothing caught goes to ``error.log`` in the user folder and is shown in a box: a program
    without a console (the packaged one) would otherwise fail without a word."""
    import datetime
    import traceback

    busy = []

    def hook(etype, value, tb):
        if issubclass(etype, KeyboardInterrupt):
            return
        text = "".join(traceback.format_exception(etype, value, tb))
        log = crash_log_file()
        try:
            log.parent.mkdir(parents=True, exist_ok=True)
            with open(log, "a", encoding="utf-8") as fh:
                fh.write(f"--- {datetime.datetime.now():%Y-%m-%d %H:%M:%S}\n{text}\n")
        except OSError:
            pass
        if sys.__stderr__ is not None:
            print(text, file=sys.__stderr__)
        if busy:                       # (an error inside the box that reports an error)
            return
        busy.append(1)
        try:
            (show or _show_crash)(f"{etype.__name__}: {value}", log)
        finally:
            busy.clear()

    sys.excepthook = hook


def _show_crash(message: str, log) -> None:
    from PySide6.QtWidgets import QMessageBox
    QMessageBox.critical(None, "iyiCAM", f"Something went wrong:\n\n{message}\n\nThe details are in\n{log}\n"
                                         "Your work in the other tabs is not affected; save it when you can.")


def _own_taskbar_group() -> None:
    """Windows: the program gets its own taskbar button and icon (not the one of the Python it runs on)."""
    if sys.platform == "win32":
        try:
            import ctypes
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("iyiCAM.iyiCAM")
        except (AttributeError, OSError):
            pass


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv if argv is None else argv)
    _own_taskbar_group()
    app, win = create_window(argv)
    install_crash_log()
    files = [a for a in argv[1:] if not a.startswith("-")]
    win.restore_tabs()                  # the projects of the last session, then the files named now
    for f in files:
        if f.lower().endswith(".icam"):
            win.open_project(f)
        else:
            win.import_vectors(f, {})
    win.show()
    win.canvas.zoom_extents()
    return app.exec()

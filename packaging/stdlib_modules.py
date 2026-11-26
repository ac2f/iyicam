"""The standard library of this Python, for the package: every module, so that a plugin finds what it imports.

PyInstaller only packs what the program imports itself; a plugin (loaded later, from the user folder) that imports
``logging.handlers``, ``http.server``, ``csv`` ... found nothing there ("No module named 'logging.handlers'"). The tests,
the IDE, the turtle, Tk and the installers are left out (megabytes, no plugin needs them).
"""
from __future__ import annotations

import importlib.util
import pkgutil
import sys

SKIP = {"antigravity", "this", "idlelib", "turtle", "turtledemo", "tkinter", "_tkinter", "test", "lib2to3", "ensurepip",
        "venv", "pydoc_data", "distutils", "msilib", "__phello__", "_pyrepl"}
SKIP_PARTS = (".test", ".tests", ".idle_test", "._test")


def modules() -> list[str]:
    out: list[str] = []
    for name in sorted(getattr(sys, "stdlib_module_names", ())):
        if name in SKIP:
            continue
        try:
            spec = importlib.util.find_spec(name)
        except (ImportError, ValueError):
            continue
        if spec is None:                       # (a module of another platform: winreg on Linux, fcntl on Windows)
            continue
        out.append(name)
        if spec.submodule_search_locations:    # a package: its submodules (logging.handlers, http.server, email.mime ...)
            for m in pkgutil.walk_packages(spec.submodule_search_locations, name + "."):
                if not any(p in m.name for p in SKIP_PARTS) and m.name.split(".")[0] not in SKIP:
                    out.append(m.name)
    return out


if __name__ == "__main__":
    mods = modules()
    print(len(mods), "modules;", ", ".join(m for m in mods if m.startswith(("logging", "http", "email.mime")))[:300])

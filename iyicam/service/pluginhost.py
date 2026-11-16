"""Plugins for the page: look at them, install one, switch it on or off, reload it, remove it.

A plugin is Python code (:mod:`iyicam.plugins.manager`): a ``.py`` file or a package in the plugin folder of the computer
the engine runs on.  What it registers (vector commands, toolpath strategies, importers and exporters, posts, G-code
filters) is in the registry of the engine at once, so the page, which builds its lists from ``schema``, offers it as soon as it
asks for the schema again.  The windows and menus a plugin adds to the desktop program do not exist here and are left out.

**A plugin is a program** and runs with the rights of the user the engine runs as.  So installing one from the page is a
decision of the owner of the engine: it can be turned off (``iyicam serve --no-plugin-install``, ``iyicam agent
--no-plugin-install`` or ``IYICAM_PLUGIN_INSTALL=off``), and a hub's own engine never installs one (the agent's computer is the
place).  A file that is sent is looked at before anything is written: the name, the size, a zip's paths (no ``..``, no
absolute path, no link, a short list of file kinds) and, for a ``.py``, that it compiles; nothing of it is run until it is
loaded, and a plugin that raises while loading is rolled back and its error shown.
"""
from __future__ import annotations

import io
import json
import os
import posixpath
import re
import shutil
import stat
import threading
import zipfile
from pathlib import Path

from ..core import paths
from ..core.registry import REGISTRY
from ..plugins.manager import PluginInfo, PluginManager
from .errors import ServiceError

KEY = re.compile(r"^[A-Za-z][A-Za-z0-9_]{0,47}$")
MAX_FILE = 2 * 1024 * 1024               # a plugin file
MAX_ZIP = 8 * 1024 * 1024                # a zip as it is sent
MAX_UNPACKED = 24 * 1024 * 1024          # everything in a zip, unpacked
MAX_ENTRIES = 300
PACKAGE_FILES = {".py", ".toml", ".json", ".txt", ".md", ".svg", ".png", ".csv", ".cfg", ".ini", ".ttf", ".otf"}
KINDS = ("commands", "operations", "importers", "exporters", "posts")


def install_allowed() -> bool:
    """Whether the page may install and remove plugins (``IYICAM_PLUGIN_INSTALL=off`` turns it off)."""
    return os.environ.get("IYICAM_PLUGIN_INSTALL", "on").strip().lower() not in ("off", "0", "no", "false")


class PluginService:
    """The plugins of this engine as the page sees them."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._manager: PluginManager | None = None

    # -- the manager and what is switched off ---------------------------------------------
    @staticmethod
    def state_file() -> Path:
        return paths.user_dir() / "plugins.json"

    def _disabled(self) -> list[str]:
        try:
            data = json.loads(self.state_file().read_text(encoding="utf-8"))
            return [str(k) for k in data.get("disabled", []) if isinstance(k, str)]
        except (OSError, ValueError, AttributeError):
            return []

    def _save_disabled(self, manager: PluginManager) -> None:
        try:
            f = self.state_file()
            f.parent.mkdir(parents=True, exist_ok=True)
            tmp = f.with_name(f.name + ".tmp")
            tmp.write_text(json.dumps({"disabled": sorted(manager.disabled)}), encoding="utf-8")
            os.replace(tmp, f)
        except OSError:
            pass                      # (the choice is lost at the next start, nothing else)

    @property
    def manager(self) -> PluginManager:
        with self._lock:
            where = [paths.plugins_dir()]
            if self._manager is not None and self._manager.dirs != where:      # (another home: the plugins of the old one go)
                for info in list(self._manager.plugins.values()):
                    self._manager.unload(info)
                self._manager = None
            if self._manager is None:
                self._manager = PluginManager(REGISTRY, where, self._disabled())
            return self._manager

    def load_all(self) -> list[PluginInfo]:
        with self._lock:
            return self.manager.load_all()

    # -- what a plugin gave the engine -------------------------------------------------------
    @staticmethod
    def provides(key: str) -> dict:
        source = f"plugin:{key}"
        out: dict = {kind: sorted(k for (kd, k), src in REGISTRY.sources.items() if kd == kind and src == source)
                     for kind in KINDS}
        out["gcode_filters"] = sum(1 for src, _ in REGISTRY.gcode_filters if src == source)
        out["hooks"] = sorted(h for h, fns in REGISTRY.hooks.items() if any(src == source for src, _ in fns))
        out["gui"] = sum(1 for src, _ in REGISTRY.gui_extensions if src == source)       # (windows of the desktop program: not here)
        return out

    def _item(self, info: PluginInfo) -> dict:
        removable = info.path is not None and self._inside(info.path)
        kind = "package" if info.path is not None and info.path.name == "__init__.py" else \
            "file" if info.path is not None else "installed"
        return {"key": info.key, "name": info.name or info.key, "version": info.version, "author": info.author,
                "description": info.description, "enabled": info.enabled, "loaded": info.loaded,
                "error": info.error, "kind": kind, "removable": removable, "provides": self.provides(info.key)}

    @staticmethod
    def _inside(path: Path) -> bool:
        try:
            path.resolve().relative_to(paths.plugins_dir().resolve())
            return True
        except (OSError, ValueError):
            return False

    # -- the operations ---------------------------------------------------------------------------
    def listing(self) -> dict:
        """``plugins`` of the page: every plugin with what it gave, and what the page may do."""
        with self._lock:
            m = self.manager
            m.discover()
            on = os.environ.get("IYICAM_SERVICE_PLUGINS", "1") != "0"
            return {"plugins": [self._item(i) for i in sorted(m.plugins.values(), key=lambda i: i.key.lower())],
                    "install": on and install_allowed(), "plugins_on": on, "folder": str(paths.plugins_dir()),
                    "limits": {"file": MAX_FILE, "zip": MAX_ZIP}}

    def _guard(self) -> None:
        if os.environ.get("IYICAM_SERVICE_PLUGINS", "1") == "0":
            raise ServiceError("plugins_off", "plugins are turned off on this engine (IYICAM_SERVICE_PLUGINS=0)")
        if not install_allowed():
            raise ServiceError("plugin_install_off", "installing plugins from the page is turned off on this engine "
                                                     "(--no-plugin-install, IYICAM_PLUGIN_INSTALL=off)")

    def install(self, name: object, data: bytes) -> dict:
        """Put a plugin (a ``.py`` file or a ``.zip`` with a package in it) in the plugin folder and load it."""
        self._guard()
        base = Path(str(name or "")).name
        stem, ext = os.path.splitext(base)
        ext = ext.lower()
        if ext not in (".py", ".zip"):
            raise ServiceError("invalid_plugin", "a plugin is a .py file or a .zip with a package in it", field="name")
        if ext == ".py":
            key, files = self._check_py(stem, data)
        else:
            key, files = self._check_zip(stem, data)
        with self._lock:
            m = self.manager
            m.discover()
            old = m.plugins.get(key)
            if old is not None:
                m.unload(old)
            self._write(key, files)
            m.discover()
            info = m.plugins[key]
            if info.enabled:
                m.load(info)
            return {"installed": key, "plugin": self._item(info), **self.listing()}

    def set(self, key: object, action: object) -> dict:
        """``enable``, ``disable``, ``reload`` or ``remove`` a plugin."""
        key = str(key or "")
        action = str(action or "")
        if action not in ("enable", "disable", "reload", "remove"):
            raise ServiceError("bad_request", "'action' must be enable, disable, reload or remove", field="action")
        if action in ("remove",):
            self._guard()
        elif os.environ.get("IYICAM_SERVICE_PLUGINS", "1") == "0":
            self._guard()
        with self._lock:
            m = self.manager
            m.discover()
            info = m.plugins.get(key)
            if info is None:
                raise ServiceError("plugin_not_found", f"there is no plugin {key!r}", key=key)
            if action == "enable" or action == "disable":
                m.set_enabled(key, action == "enable")
                self._save_disabled(m)
            elif action == "reload":
                if not info.enabled:
                    raise ServiceError("plugin_disabled", "a plugin that is off is not reloaded; switch it on", key=key)
                m.reload(info)
            else:
                if info.path is None or not self._inside(info.path):
                    raise ServiceError("plugin_not_removable", "this plugin was installed as a package of Python, "
                                                              "not from the plugin folder", key=key)
                m.unload(info)
                target = info.path.parent if info.path.name == "__init__.py" else info.path
                if target.is_dir():
                    shutil.rmtree(target, ignore_errors=True)
                else:
                    try:
                        target.unlink()
                    except OSError:
                        pass
                m.disabled.discard(key)
                self._save_disabled(m)
                m.plugins.pop(key, None)
            return self.listing()

    # -- looking at what was sent ---------------------------------------------------------------------
    @staticmethod
    def _check_py(stem: str, data: bytes) -> tuple[str, dict[str, bytes]]:
        if not KEY.match(stem):
            raise ServiceError("invalid_plugin", "the name of the file must be letters, digits and _, not starting with a digit "
                                                 "(my_plugin.py)", field="name")
        if not data or len(data) > MAX_FILE:
            raise ServiceError("invalid_plugin", f"the file must be between 1 byte and {MAX_FILE // 1024} KB", field="data")
        try:
            compile(data, f"{stem}.py", "exec")            # (compiled, not run)
        except (SyntaxError, ValueError) as exc:
            raise ServiceError("invalid_plugin", f"the file is not valid Python: {exc}", field="data") from exc
        return stem, {f"{stem}.py": data}

    @staticmethod
    def _check_zip(stem: str, data: bytes) -> tuple[str, dict[str, bytes]]:
        if not data or len(data) > MAX_ZIP:
            raise ServiceError("invalid_plugin", f"the zip must be between 1 byte and {MAX_ZIP // (1024 * 1024)} MB", field="data")
        try:
            zf = zipfile.ZipFile(io.BytesIO(data))
        except zipfile.BadZipFile as exc:
            raise ServiceError("invalid_plugin", "this is not a zip file", field="data") from exc
        infos = [i for i in zf.infolist() if not i.is_dir()]
        if not infos or len(zf.infolist()) > MAX_ENTRIES:
            raise ServiceError("invalid_plugin", f"the zip must hold 1 to {MAX_ENTRIES} files", field="data")
        if sum(i.file_size for i in infos) > MAX_UNPACKED:
            raise ServiceError("invalid_plugin", f"the zip is more than {MAX_UNPACKED // (1024 * 1024)} MB unpacked", field="data")
        names = []
        for i in infos:
            n = i.filename.replace("\\", "/")
            if n.startswith("/") or re.match(r"^[A-Za-z]:", n) or any(part in ("..", "") for part in n.split("/")):
                raise ServiceError("invalid_plugin", f"the path {i.filename!r} in the zip is not allowed", field="data")
            if stat.S_ISLNK(i.external_attr >> 16):
                raise ServiceError("invalid_plugin", f"{i.filename!r} is a link, which a plugin may not contain", field="data")
            if posixpath.splitext(n)[1].lower() not in PACKAGE_FILES:
                raise ServiceError("invalid_plugin", f"{i.filename!r}: a plugin holds Python files, text, data and pictures "
                                                     "only", field="data")
            names.append((n, i))
        tops = {n.split("/")[0] for n, _ in names}
        root_files = [n for n, _ in names if "/" not in n]
        if len(names) == 1 and root_files and root_files[0].lower().endswith(".py"):
            key = posixpath.splitext(root_files[0])[0]                      # (a zip with one .py in it)
            if not KEY.match(key):
                raise ServiceError("invalid_plugin", "the name of the .py file in the zip is not a valid plugin name", field="data")
            return key, {f"{key}.py": zf.read(names[0][1])}
        if len(tops) == 1 and not root_files:                               # (a package folder)
            key = next(iter(tops))
            files = {n: zf.read(i) for n, i in names}
        elif "__init__.py" in root_files:                                   # (the zip is the package itself: the file's name is its name)
            key = stem
            files = {f"{key}/{n}": zf.read(i) for n, i in names}
        else:
            raise ServiceError("invalid_plugin", "the zip must hold one package folder with an __init__.py, or one .py file",
                               field="data")
        if not KEY.match(key):
            raise ServiceError("invalid_plugin", "the name of the package must be letters, digits and _, not starting with a "
                                                 "digit", field="data")
        if f"{key}/__init__.py" not in files:
            raise ServiceError("invalid_plugin", "the package has no __init__.py", field="data")
        for path, blob in files.items():
            if path.endswith(".py"):
                try:
                    compile(blob, path, "exec")
                except (SyntaxError, ValueError) as exc:
                    raise ServiceError("invalid_plugin", f"{path} is not valid Python: {exc}", field="data") from exc
        return key, files

    @staticmethod
    def _write(key: str, files: dict[str, bytes]) -> None:
        """Write what was checked into the plugin folder, replacing the plugin of that name."""
        root = paths.plugins_dir()
        root.mkdir(parents=True, exist_ok=True)
        single = root / f"{key}.py"
        package = root / key
        if package.is_dir():
            shutil.rmtree(package, ignore_errors=True)
        if single.exists():
            single.unlink()
        for stale in (root / "__pycache__").glob(f"{key}.*.pyc"):      # (compiled code of the file that was there: a new file of the same size
            try:                                                      # written in the same second must not be taken for it)
                stale.unlink()
            except OSError:
                pass
        for rel, blob in files.items():
            target = (root / rel).resolve()
            target.relative_to(root.resolve())              # (never outside the folder: checked again)
            target.parent.mkdir(parents=True, exist_ok=True)
            tmp = target.with_name(target.name + ".tmp")
            tmp.write_bytes(blob)
            os.replace(tmp, target)


PLUGINS = PluginService()

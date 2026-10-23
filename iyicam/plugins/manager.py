"""Plugin discovery and loading.

A plugin is a Python module (``name.py``) or package (``name/__init__.py``) in
one of the plugin directories, or a module published through the
``iyicam.plugins`` entry point group.  It defines::

    PLUGIN = {"name": ..., "version": ..., "author": ..., "description": ...}
    def register(api): ...
    def unregister(api): ...   # optional
"""
from __future__ import annotations

import importlib
import importlib.util
import sys
import traceback
from dataclasses import dataclass
from importlib import metadata
from pathlib import Path
from types import ModuleType
from typing import Iterable

from ..core import paths
from ..core.registry import REGISTRY, Registry
from .api import PluginAPI


@dataclass
class PluginInfo:
    key: str                 # unique id (file stem or entry point name)
    name: str
    path: Path | None = None
    entry_point: str | None = None
    version: str = ""
    author: str = ""
    description: str = ""
    enabled: bool = True
    loaded: bool = False
    error: str = ""
    module: ModuleType | None = None


class PluginManager:
    def __init__(self, registry: Registry = REGISTRY, dirs: Iterable[Path] | None = None,
                 disabled: Iterable[str] = (), use_entry_points: bool = True):
        self.registry = registry
        self.dirs = [Path(d) for d in dirs] if dirs is not None else [paths.plugins_dir()]
        self.disabled = set(disabled)
        self.use_entry_points = use_entry_points
        self.plugins: dict[str, PluginInfo] = {}

    # -- discovery ------------------------------------------------------------------
    def discover(self) -> list[PluginInfo]:
        found: dict[str, PluginInfo] = {}
        for d in self.dirs:
            if not d.is_dir():
                continue
            for p in sorted(d.iterdir()):
                if p.name.startswith(("_", ".")):
                    continue
                if p.is_file() and p.suffix == ".py":
                    key = p.stem
                elif p.is_dir() and (p / "__init__.py").exists():
                    key = p.name
                    p = p / "__init__.py"
                else:
                    continue
                found[key] = PluginInfo(key, key, path=p)
        if self.use_entry_points:
            try:
                eps = metadata.entry_points(group="iyicam.plugins")
            except Exception:  # pragma: no cover - broken environments
                eps = []
            for ep in eps:
                found.setdefault(ep.name, PluginInfo(ep.name, ep.name, entry_point=ep.value))
        for key, info in found.items():
            old = self.plugins.get(key)
            if old is not None:
                info.loaded, info.module, info.error = old.loaded, old.module, old.error
                info.name, info.version = old.name, old.version
                info.author, info.description = old.author, old.description
            info.enabled = key not in self.disabled
        self.plugins = found
        return list(found.values())

    # -- loading ----------------------------------------------------------------------
    def _import(self, info: PluginInfo) -> ModuleType:
        if info.entry_point:
            return importlib.import_module(info.entry_point.split(":")[0])
        modname = f"iyicam_plugin_{info.key}"
        spec = importlib.util.spec_from_file_location(
            modname, info.path, submodule_search_locations=[str(info.path.parent)]
            if info.path.name == "__init__.py" else None)
        if spec is None or spec.loader is None:
            raise ImportError(f"cannot load {info.path}")
        mod = importlib.util.module_from_spec(spec)
        sys.modules[modname] = mod
        spec.loader.exec_module(mod)
        return mod

    def load(self, info: PluginInfo) -> bool:
        if info.loaded:
            return True
        try:
            mod = self._import(info)
            meta = getattr(mod, "PLUGIN", {}) or {}
            info.name = meta.get("name", info.key)
            info.version = str(meta.get("version", ""))
            info.author = meta.get("author", "")
            info.description = meta.get("description", (mod.__doc__ or "").strip().split("\n")[0])
            reg = getattr(mod, "register", None)
            if reg is None:
                raise AttributeError("plugin has no register(api) function")
            reg(PluginAPI(self.source_of(info), self.registry))
            info.module = mod
            info.loaded = True
            info.error = ""
        except Exception:
            self.registry.remove_source(self.source_of(info))
            info.error = traceback.format_exc(limit=6)
            info.loaded = False
        return info.loaded

    def unload(self, info: PluginInfo) -> None:
        if not info.loaded:
            return
        unreg = getattr(info.module, "unregister", None)
        if unreg is not None:
            try:
                unreg(PluginAPI(self.source_of(info), self.registry))
            except Exception:
                info.error = traceback.format_exc(limit=6)
        self.registry.remove_source(self.source_of(info))
        info.loaded = False
        info.module = None

    def reload(self, info: PluginInfo) -> bool:
        self.unload(info)
        return self.load(info)

    def load_all(self) -> list[PluginInfo]:
        self.discover()
        for info in self.plugins.values():
            if info.enabled:
                self.load(info)
        return list(self.plugins.values())

    def set_enabled(self, key: str, enabled: bool) -> None:
        info = self.plugins[key]
        info.enabled = enabled
        if enabled:
            self.disabled.discard(key)
            self.load(info)
        else:
            self.disabled.add(key)
            self.unload(info)

    @staticmethod
    def source_of(info: PluginInfo) -> str:
        return f"plugin:{info.key}"

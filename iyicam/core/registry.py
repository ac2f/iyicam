"""Central registry of everything that can be extended by plugins.

Built-in features register themselves exactly like third party plugins do
(see :mod:`iyicam.builtins`), which keeps the core small and modular.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable


@dataclass
class Importer:
    extensions: tuple[str, ...]
    label: str
    func: Callable  # func(path, project, sheet, layer_id, **options) -> list[VectorObject]
    source: str = "builtin"


@dataclass
class Exporter:
    extension: str
    label: str
    func: Callable  # func(path, project, sheet, objects, **options) -> None
    source: str = "builtin"
    params: list = field(default_factory=list)     # the options (Param) asked for before the file is written


@dataclass
class Registry:
    operations: dict[str, type] = field(default_factory=dict)
    commands: dict[str, Any] = field(default_factory=dict)
    posts: dict[str, Any] = field(default_factory=dict)
    importers: dict[str, Importer] = field(default_factory=dict)
    exporters: dict[str, Exporter] = field(default_factory=dict)
    gcode_filters: list[tuple[str, Callable]] = field(default_factory=list)
    hooks: dict[str, list[tuple[str, Callable]]] = field(default_factory=dict)
    gui_extensions: list[tuple[str, Callable]] = field(default_factory=list)
    sources: dict[tuple[str, str], str] = field(default_factory=dict)

    def remove_source(self, source: str) -> None:
        """Unregister everything a plugin added."""
        for (kind, key), src in list(self.sources.items()):
            if src != source:
                continue
            getattr(self, kind).pop(key, None)
            del self.sources[(kind, key)]
        self.gcode_filters = [f for f in self.gcode_filters if f[0] != source]
        self.gui_extensions = [f for f in self.gui_extensions if f[0] != source]
        for name in self.hooks:
            self.hooks[name] = [h for h in self.hooks[name] if h[0] != source]

    def emit(self, hook: str, *args, **kwargs) -> list:
        out = []
        for _, fn in self.hooks.get(hook, []):
            out.append(fn(*args, **kwargs))
        return out

    def importer_for(self, ext: str) -> Importer | None:
        ext = ext.lower().lstrip(".")
        for imp in self.importers.values():
            if ext in imp.extensions:
                return imp
        return None


REGISTRY = Registry()

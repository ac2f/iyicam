"""The API handed to plugins in ``register(api)``.

Example plugin (save as ``~/.iyicam/plugins/hello.py``)::

    PLUGIN = {"name": "Hello", "version": "1.0", "description": "Adds a square"}

    def register(api):
        def run(ctx, values):
            ctx.add(api.shapes.rectangle(0, 0, values["size"], values["size"]))
        api.add_command("hello.square", "Hello Square", run, menu="Plugins",
                        params=[api.Param("size", "Size", "length", 50.0)])
"""
from __future__ import annotations

from pathlib import Path
from typing import Callable

from ..cam import moves as _moves
from ..cam.builder import MoveBuilder
from ..cam.moves import Toolpath
from ..cam.operation import OpContext, Operation, depth_levels, depth_params, final_depth, stepdown
from ..core import clip, geom, shapes, vectorops
from ..core.commands import Command, CommandContext, CommandError
from ..core.params import Param
from ..core.registry import REGISTRY, Exporter, Importer, Registry
from ..post.engine import PostProcessor

HOOKS = ("project_loaded", "project_saved", "toolpath_computed", "before_post", "after_post",
         "gui_ready", "selection_changed")


class PluginAPI:
    # re-exported building blocks for plugin authors
    Param = Param
    Command = Command
    CommandContext = CommandContext
    CommandError = CommandError
    Operation = Operation
    OpContext = OpContext
    Toolpath = Toolpath
    MoveBuilder = MoveBuilder
    PostProcessor = PostProcessor
    depth_params = staticmethod(depth_params)
    depth_levels = staticmethod(depth_levels)
    final_depth = staticmethod(final_depth)
    stepdown = staticmethod(stepdown)
    geom = geom
    shapes = shapes
    vectorops = vectorops
    clip = clip
    moves = _moves

    def __init__(self, source: str, registry: Registry = REGISTRY):
        self.source = source
        self.registry = registry

    def _track(self, kind: str, key: str) -> None:
        self.registry.sources[(kind, key)] = self.source

    # -- toolpath strategies -------------------------------------------------------
    def add_operation(self, cls: type[Operation]) -> None:
        if not cls.id:
            raise ValueError("operation classes need an 'id'")
        self.registry.operations[cls.id] = cls
        self._track("operations", cls.id)

    # -- commands --------------------------------------------------------------------
    def add_command(self, cmd_id: str | Command, label: str = "", run: Callable | None = None,
                    menu: str = "Plugins", params: list[Param] | None = None, min_selection: int = 0,
                    shortcut: str | None = None, tooltip: str = "") -> Command:
        if isinstance(cmd_id, Command):
            cmd = cmd_id
            cmd.source = self.source
        else:
            if run is None:
                raise ValueError("add_command needs a run(ctx, values) function")
            cmd = Command(cmd_id, label or cmd_id, run, menu, list(params or []), min_selection,
                          shortcut, tooltip, self.source)
        self.registry.commands[cmd.id] = cmd
        self._track("commands", cmd.id)
        return cmd

    # -- post-processors ---------------------------------------------------------------
    def add_postprocessor(self, post: PostProcessor | dict | str | Path) -> PostProcessor:
        """Register a :class:`PostProcessor`, a definition dict or a ``.toml`` file."""
        from ..post.loader import builtin_definitions, load_post_file
        if isinstance(post, (str, Path)):
            known = {p.id: p.definition for p in self.registry.posts.values()}
            known = {**builtin_definitions(), **known}
            post = load_post_file(Path(post), known)
        elif isinstance(post, dict):
            post = PostProcessor(post)
        self.registry.posts[post.id] = post
        self._track("posts", post.id)
        return post

    # -- file formats -------------------------------------------------------------------
    def add_importer(self, extensions: str | tuple[str, ...], label: str, func: Callable) -> None:
        exts = (extensions,) if isinstance(extensions, str) else tuple(extensions)
        exts = tuple(e.lower().lstrip(".") for e in exts)
        key = exts[0]
        self.registry.importers[key] = Importer(exts, label, func, self.source)
        self._track("importers", key)

    def add_exporter(self, extension: str, label: str, func: Callable) -> None:
        ext = extension.lower().lstrip(".")
        self.registry.exporters[ext] = Exporter(ext, label, func, self.source)
        self._track("exporters", ext)

    # -- hooks ------------------------------------------------------------------------------
    def add_gcode_filter(self, func: Callable[[str, object, PostProcessor], str]) -> None:
        """``func(text, job, post) -> text`` runs on every generated G-code file."""
        self.registry.gcode_filters.append((self.source, func))

    def on(self, hook: str, func: Callable) -> None:
        if hook not in HOOKS:
            raise ValueError(f"unknown hook {hook!r}; available: {', '.join(HOOKS)}")
        self.registry.hooks.setdefault(hook, []).append((self.source, func))

    def add_gui_extension(self, func: Callable) -> None:
        """``func(main_window)`` is called once the GUI is ready (add menus, docks, ...)."""
        self.registry.gui_extensions.append((self.source, func))

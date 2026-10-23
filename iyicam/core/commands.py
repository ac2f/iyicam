"""Vector commands: named actions with a parameter schema operating on a selection.

Commands are GUI independent so they work from the GUI menus, the Python
console, the CLI and tests alike.  The GUI builds a dialog from ``params``.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Iterable

from .document import Project, Sheet, VectorObject
from .geom import Contour
from .params import Param


class CommandError(Exception):
    """Raised by a command to report a user error (shown as a message)."""


@dataclass
class Command:
    id: str
    label: str
    run: Callable[["CommandContext", dict], None]
    menu: str = "Vectors"
    params: list[Param] = field(default_factory=list)
    min_selection: int = 0
    shortcut: str | None = None
    tooltip: str = ""
    source: str = "builtin"


class CommandContext:
    def __init__(self, project: Project, sheet: Sheet, selection: Iterable[str] = (),
                 active_layer_id: str | None = None):
        self.project = project
        self.sheet = sheet
        self.selection_ids = [i for i in selection if sheet.vector(i) is not None]
        self.active_layer_id = active_layer_id or project.layers[0].id
        self.new_selection: list[str] | None = None
        self.messages: list[str] = []
        self.changed = False

    @property
    def selected(self) -> list[VectorObject]:
        return [v for v in (self.sheet.vector(i) for i in self.selection_ids) if v is not None]

    def add(self, contours: Contour | list[Contour], layer_id: str | None = None, name: str = "",
            select: bool = True) -> VectorObject:
        if isinstance(contours, Contour):
            contours = [contours]
        obj = VectorObject(list(contours), layer_id or self.active_layer_id, name)
        self.sheet.vectors.append(obj)
        if select:
            self.new_selection = (self.new_selection or []) + [obj.id]
        self.changed = True
        return obj

    def add_object(self, obj: VectorObject, select: bool = True) -> VectorObject:
        self.sheet.vectors.append(obj)
        if select:
            self.new_selection = (self.new_selection or []) + [obj.id]
        self.changed = True
        return obj

    def remove(self, ids: Iterable[str]) -> None:
        ids = set(ids)
        self.sheet.vectors = [v for v in self.sheet.vectors if v.id not in ids]
        self.changed = True

    def replace(self, obj: VectorObject, contours: list[Contour]) -> None:
        obj.contours = list(contours)
        self.changed = True

    def select(self, ids: Iterable[str]) -> None:
        self.new_selection = list(ids)

    def message(self, text: str) -> None:
        self.messages.append(text)


def run_command(cmd: Command, ctx: CommandContext, values: dict[str, Any] | None = None) -> CommandContext:
    from .params import resolve
    if len(ctx.selected) < cmd.min_selection:
        raise CommandError(f"{cmd.label}: select at least {cmd.min_selection} vector(s)")
    cmd.run(ctx, resolve(cmd.params, values))
    return ctx

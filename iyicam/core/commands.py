"""Vector commands: named actions with a parameter schema operating on a selection.

Commands are GUI independent so they work from the GUI menus, the Python
console, the CLI and tests alike.  The GUI builds a dialog from ``params``.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Iterable

from . import clip
from .document import Project, Sheet, VectorObject
from .geom import Contour
from .params import Param


class CommandError(Exception):
    """Raised by a command to report a user error (shown as a message)."""


@dataclass
class Command:
    id: str
    label: str
    run: Callable[["CommandContext", dict], Any]      # may return its answer (see CommandContext.absorb)
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
        self.selection_ids = [v.id for v in sheet.pick(selection)]
        self.active_layer_id = active_layer_id or project.layers[0].id
        self.new_selection: list[str] | None = None
        self.messages: list[str] = []
        self.changed = False
        # what a command hands back besides the drawing (see :meth:`absorb`): a title, report sections (a window), warnings, data
        self.title = ""
        self.sections: list[dict] = []
        self.warnings: list[str] = []
        self.data: dict = {}

    @property
    def selected(self) -> list[VectorObject]:
        return self.sheet.pick(self.selection_ids)

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
        if not obj.prune():   # a vector of nothing is not added
            return obj
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
        if not obj.prune():   # nothing is left of it
            self.remove([obj.id])
        self.changed = True

    def select(self, ids: Iterable[str]) -> None:
        self.new_selection = list(ids)

    def message(self, text: str) -> None:
        self.messages.append(str(text))

    def section(self, title: Any, rows: Iterable[tuple[Any, Any]] = ()) -> None:
        """A block of the report the user is shown: a title and ``(label, value)`` rows."""
        self.sections.append({"title": str(title), "rows": [[str(a), str(b)] for a, b in rows]})

    def warn(self, text: str) -> None:
        if str(text) not in self.warnings:
            self.warnings.append(str(text))

    def absorb(self, value: Any) -> None:
        """Take what ``run(ctx, values)`` *returned* (a command may hand its answer back like a function does):

        * ``None``: nothing;
        * a ``str``, or a list of them: messages;
        * a ``dict``: ``title``, ``messages`` (or ``headline``), ``report`` (or ``sections``: ``{"title", "rows"}``), ``warnings``
          and ``data``; any other key is data as well, so ``return {"total": 300.0}`` is an answer too;
        * anything else: ``data["value"]``.
        """
        if value is None:
            return
        if isinstance(value, str):
            self.message(value)
        elif isinstance(value, (list, tuple)) and all(isinstance(x, str) for x in value):
            for x in value:
                self.message(x)
        elif isinstance(value, dict):
            known = {"title", "messages", "headline", "report", "sections", "warnings", "data"}
            if value.get("title"):
                self.title = str(value["title"])
            for m in list(value.get("messages") or []) + list(value.get("headline") or []):
                self.message(m)
            for sec in list(value.get("report") or []) + list(value.get("sections") or []):
                self.section(sec.get("title", ""), sec.get("rows", ()))
            for w in value.get("warnings") or []:
                self.warn(w)
            data = value.get("data")
            if isinstance(data, dict):
                self.data.update(data)
            elif data is not None:
                self.data["value"] = data
            self.data.update({k: v for k, v in value.items() if k not in known})
        else:
            self.data["value"] = value

    def result(self) -> dict:
        """Everything the command said, as plain data (the page gets it as ``result``, the windows show it):
        ``{"title", "messages", "report", "warnings", "data"}``."""
        return {"title": self.title, "messages": list(self.messages), "report": [dict(s) for s in self.sections],
                "warnings": list(self.warnings), "data": dict(self.data)}


def report_text(result: dict) -> str:
    """The report of a result as plain text (the window, the console, copy to the clipboard)."""
    out: list[str] = []
    if result.get("title"):
        out += [str(result["title"]), ""]
    for sec in result.get("report") or []:
        out.append(str(sec.get("title", "")))
        rows = sec.get("rows") or []
        width = max((len(str(r[0])) for r in rows), default=0)
        out += [f"   {str(a).ljust(width)} : {b}" for a, b in rows]
        out.append("")
    out += [f"! {w}" for w in result.get("warnings") or []]
    return "\n".join(out).rstrip() + "\n"


def run_command(cmd: Command, ctx: CommandContext, values: dict[str, Any] | None = None) -> CommandContext:
    from .params import resolve
    if len(ctx.selected) < cmd.min_selection:
        raise CommandError(f"{cmd.label}: select at least {cmd.min_selection} vector(s)")
    try:
        ctx.absorb(cmd.run(ctx, resolve(cmd.params, values)))      # (what ``run`` returns counts, as well as ``ctx.message``)
    except clip.TooComplex as exc:
        raise CommandError(f"{cmd.label}: {exc}") from exc
    return ctx

"""The sheet sizes that are offered ready (New Project, Sheet properties, nesting plates), and the ways to start a project.

The sizes are the built-in list, the person's own list (editable: Settings.sheet_presets) and what plugins add or hide
(``api.add_sheet_preset`` / ``api.remove_sheet_preset``).  The ways to start a project are *Blank* and what plugins add
(``api.add_new_project_template``)."""
from __future__ import annotations

from .document import Project
from .registry import REGISTRY, Registry

BUILTIN = [("2800 x 2100", 2800.0, 2100.0), ("3660 x 1830", 3660.0, 1830.0), ("2440 x 1220", 2440.0, 1220.0),
           ("2500 x 1250", 2500.0, 1250.0), ("3050 x 1220", 3050.0, 1220.0), ("1220 x 610", 1220.0, 610.0)]


def clean(items) -> list[tuple[str, float, float]]:
    """``[(name, width, height)]`` from whatever was stored (bad entries are left out)."""
    out = []
    for it in items or []:
        try:
            name, w, h = str(it[0]).strip(), float(it[1]), float(it[2])
        except (TypeError, ValueError, IndexError, KeyError):
            continue
        if name and w > 0 and h > 0 and w < 1e6 and h < 1e6:
            out.append((name, w, h))
    return out


def sizes(user=None, registry: Registry = REGISTRY) -> list[tuple[str, float, float]]:
    """The sizes to offer, in order: the person's own list (the built-in ones when there is none), then the plugins'; a size a
    plugin hides (zero size) is left out."""
    base = clean(user) if user else list(BUILTIN)
    hidden = {n for n, (_n, w, h) in registry.sheet_presets.items() if w <= 0 or h <= 0}
    out = [s for s in base if s[0] not in hidden]
    names = {s[0] for s in out}
    for n, (_n, w, h) in registry.sheet_presets.items():
        if w > 0 and h > 0 and n not in names:
            out.append((n, w, h))
    return out


BLANK = "blank"           # the way to start a project with no sheet at all


def templates(registry: Registry = REGISTRY) -> list[dict]:
    """The ways to start a project: Blank first, then the plugins'."""
    out = [{"id": BLANK, "label": "Blank (no sheet)", "params": [],
            "description": "Nothing at all: the first thing is to add a sheet (Sheets panel, +), with its size and thickness"},
           {"id": "", "label": "One sheet of this size", "params": [], "description": "No toolpaths, one empty sheet of the size below"}]
    for t in registry.new_project_templates.values():
        out.append({"id": t.id, "label": t.label, "params": t.params, "description": t.description})
    return out


def create(width: float = 600.0, height: float = 400.0, thickness: float = 18.0, template: str = "", values: dict | None = None,
           registry: Registry = REGISTRY) -> Project:
    """A new project: blank with the size, or what the plugin's template makes of it (a template that fails is an error to say:
    the project is not made half)."""
    p = Project()
    p.sheets[0].width, p.sheets[0].height = width, height
    p.material.thickness = thickness              # (what a sheet the template adds without a thickness of its own is)
    p.sheets[0].thickness = thickness              # (every sheet has its own thickness)
    if template == BLANK:
        p.sheets = []
        return p
    if template:
        t = registry.new_project_templates.get(template)
        if t is None:
            raise KeyError(f"there is no way to start a project called {template!r}")
        from .params import resolve
        t.build(p, resolve(t.params, values or {}))
    return p

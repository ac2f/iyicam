"""Compute toolpaths for a project (per toolpath definition and sheet)."""
from __future__ import annotations

import traceback
from dataclasses import dataclass, field

from ..core.document import Project, Sheet, ToolpathDef
from ..core.registry import REGISTRY, Registry
from .moves import Toolpath
from .operation import OpContext


@dataclass
class ComputeResult:
    toolpaths: list[Toolpath] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    error: str | None = None


def sheets_for(project: Project, tdef: ToolpathDef) -> list[Sheet]:
    if tdef.sheet_ids is None:
        return list(project.sheets)
    wanted = set(tdef.sheet_ids)
    return [s for s in project.sheets if s.id in wanted]


def compute_toolpath(project: Project, tdef: ToolpathDef, sheet: Sheet,
                     registry: Registry = REGISTRY, tolerance: float = 0.005) -> ComputeResult:
    cls = registry.operations.get(tdef.op)
    res = ComputeResult()
    if cls is None:
        res.error = f"Unknown toolpath type '{tdef.op}' (missing plugin?)"
        return res
    objects = project.vectors_for(tdef, sheet)
    if not objects:
        return res
    if cls.tool_types and tdef.tool.type not in cls.tool_types:
        res.warnings.append(f"{cls.label} is not meant for a {tdef.tool.type.replace('_', ' ')} tool")
    ctx = OpContext(project, sheet, tdef, objects, tolerance)
    try:
        res.toolpaths = cls(tdef.params).compute(ctx)
    except Exception as exc:  # strategies (and plugins) must never crash the application
        res.error = f"{type(exc).__name__}: {exc}"
        res.warnings.append(traceback.format_exc(limit=4))
    res.warnings = ctx.warnings + res.warnings
    for tp in res.toolpaths:
        tp.warnings = list(res.warnings)
    registry.emit("toolpath_computed", project, tdef, sheet, res)
    return res


def compute_all(project: Project, registry: Registry = REGISTRY,
                only: set[str] | None = None) -> dict[tuple[str, str], ComputeResult]:
    out: dict[tuple[str, str], ComputeResult] = {}
    for tdef in project.toolpaths:
        if only is not None and tdef.id not in only:
            continue
        for sheet in sheets_for(project, tdef):
            out[(tdef.id, sheet.id)] = compute_toolpath(project, tdef, sheet, registry)
    return out


def sheet_toolpaths(project: Project, results: dict[tuple[str, str], ComputeResult], sheet: Sheet,
                    enabled_only: bool = True) -> list[Toolpath]:
    """Toolpaths of one sheet in machining order."""
    out: list[Toolpath] = []
    for tdef in project.toolpaths:
        if enabled_only and not tdef.enabled:
            continue
        r = results.get((tdef.id, sheet.id))
        if r:
            out += r.toolpaths
    return out

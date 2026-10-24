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
    res.warnings = ctx.warnings + res.warnings + safety_warnings(project, sheet, res.toolpaths)
    for tp in res.toolpaths:
        tp.warnings = list(res.warnings)
    registry.emit("toolpath_computed", project, tdef, sheet, res)
    return res


def safety_warnings(project: Project, sheet: Sheet, toolpaths: list[Toolpath],
                    spoilboard: float = 1.0, margin: float = 0.01) -> list[str]:
    """Checks every machine operator wants before pressing start."""
    out = []
    thick = project.sheet_thickness(sheet)
    for tp in toolpaths:
        st = tp.stats()
        if not st.feed_length:
            continue
        below = -st.zmin - thick
        if below > spoilboard + 1e-6:
            out.append(f"{tp.name}: goes {below:.2f} mm below the material (into the spoilboard)")
        if st.xmin < -margin or st.ymin < -margin or st.xmax > sheet.width + margin or st.ymax > sheet.height + margin:
            out.append(f"{tp.name}: cuts outside the sheet ({st.xmin:.1f}, {st.ymin:.1f}) - "
                       f"({st.xmax:.1f}, {st.ymax:.1f})")
        fl = tp.tool.flute_length
        if fl > 0 and -st.zmin > fl + 1e-6 and tp.tool.type in ("end_mill", "ball_nose", "bull_nose"):
            out.append(f"{tp.name}: {-st.zmin:.2f} mm deep but the tool's flutes are only {fl:g} mm long")
    return out


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

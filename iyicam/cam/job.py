"""Compute toolpaths for a project (per toolpath definition and sheet)."""
from __future__ import annotations

import traceback
from dataclasses import dataclass, field

import numpy as np

from ..core import clip
from ..core.document import Project, Sheet, ToolpathDef
from ..core.geom import BBox
from ..core.plate import Plate
from ..core.registry import REGISTRY, Registry
from .moves import ArcMove, DrillCycle, Feed, Toolpath
from .operation import OpContext


@dataclass
class ComputeResult:
    toolpaths: list[Toolpath] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    error: str | None = None
    order: list[str] | None = None                          # cut order (vector contour keys)
    start_updates: dict = field(default_factory=dict)       # key -> better start point of the vector


def sheets_for(project: Project, tdef: ToolpathDef) -> list[Sheet]:
    if tdef.sheet_ids is None:
        return list(project.sheets)
    wanted = set(tdef.sheet_ids)
    return [s for s in project.sheets if s.id in wanted]


def compute_toolpath(project: Project, tdef: ToolpathDef, sheet: Sheet,
                     registry: Registry = REGISTRY, tolerance: float = 0.005,
                     previous: list[Toolpath] | None = None, force_smart: bool = False) -> ComputeResult:
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
    ctx = OpContext(project, sheet, tdef, objects, tolerance, previous=list(previous or []),
                    force_smart=force_smart)
    try:
        res.toolpaths = cls(tdef.params).compute(ctx)
        res.order, res.start_updates = ctx.order, dict(ctx.start_updates or {})
    except Exception as exc:  # strategies (and plugins) must never crash the application
        res.error = f"{type(exc).__name__}: {exc}"
        res.warnings.append(traceback.format_exc(limit=4))
    res.warnings = (ctx.warnings + res.warnings + safety_warnings(project, sheet, res.toolpaths)
                    + loose_cut_warnings(previous or [], res.toolpaths))
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
        wraps = project.material.rotary  # Y beyond the circumference just turns further
        if (st.xmin < -margin or st.xmax > sheet.width + margin
                or (not wraps and (st.ymin < -margin or st.ymax > sheet.height + margin))):
            out.append(f"{tp.name}: cuts outside the sheet ({st.xmin:.1f}, {st.ymin:.1f}) - "
                       f"({st.xmax:.1f}, {st.ymax:.1f})")
        if sheet.outline and not wraps:
            pts = tp.cut_points()
            if pts:
                out_of = ~Plate.of(sheet).contains(np.asarray(pts), margin)
                if out_of.any():
                    x, y = pts[int(np.argmax(out_of))]
                    out.append(f"{tp.name}: cuts outside the outline of the plate ({sheet.name}) at "
                               f"({x:.1f}, {y:.1f})")
        fl = tp.tool.flute_length
        if fl > 0 and -st.zmin > fl + 1e-6 and tp.tool.type in ("end_mill", "ball_nose", "bull_nose"):
            out.append(f"{tp.name}: {-st.zmin:.2f} mm deep but the tool's flutes are only {fl:g} mm long")
    return out


def loose_cut_warnings(previous: list[Toolpath], toolpaths: list[Toolpath]) -> list[str]:
    """Cuts made inside material that an earlier toolpath already cut free (and holds no tabs)."""
    regions = []
    for prev in previous:
        for c in prev.cuts:
            if c.get("frees") and c.get("loop") is not None and not c.get("finish"):
                inner = clip.offset([c["loop"].flatten(0.1)], -c.get("radius", 0.0))
                if inner:
                    regions.append((prev.name, inner, BBox.of_points([q for poly in inner for q in poly])))
    if not regions:
        return []
    out = []
    for tp in toolpaths:
        pts = [(m.x, m.y) for m in tp.moves if isinstance(m, (Feed, ArcMove, DrillCycle)) and m.z < -1e-6]
        for name, inner, bb in regions:
            if any(bb.contains(q) and clip.point_in_region(q, inner) for q in pts):
                out.append(f"{tp.name} cuts inside parts that '{name}' has already cut free: "
                           f"move it before '{name}'")
                break
    return out


def compute_all(project: Project, registry: Registry = REGISTRY, only: set[str] | None = None,
                existing: dict[tuple[str, str], ComputeResult] | None = None,
                force_smart: set[str] | None = None) -> dict[tuple[str, str], ComputeResult]:
    """Calculate the toolpaths (``only`` some of them); every toolpath sees what the ones
    before it on the sheet machine (from this run or ``existing`` results)."""
    out: dict[tuple[str, str], ComputeResult] = {}
    done: dict[str, list[Toolpath]] = {}
    for tdef in project.toolpaths:
        for sheet in sheets_for(project, tdef):
            k = (tdef.id, sheet.id)
            if only is None or tdef.id in only:
                out[k] = compute_toolpath(project, tdef, sheet, registry, previous=done.get(sheet.id, []),
                                          force_smart=bool(force_smart) and tdef.id in force_smart)
                r = out[k]
            else:
                r = (existing or {}).get(k)
            if r is not None and tdef.enabled:
                done.setdefault(sheet.id, []).extend(r.toolpaths)
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

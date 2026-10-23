"""Turn vector objects into nestable parts and apply nesting results to a project."""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Callable

from ..core import clip, vectorops
from ..core.document import Project, Sheet, VectorObject
from ..core.geom import Transform
from .nest import NestResult, NestSettings, NestShape, nest


@dataclass
class Part:
    root: VectorObject
    members: list[VectorObject] = field(default_factory=list)  # holes, engraving, ... (root excluded)
    quantity: int = 1

    @property
    def objects(self) -> list[VectorObject]:
        return [self.root] + self.members


def _region(o: VectorObject, tol: float) -> list[clip.Poly]:
    closed = [c for c in o.contours if c.closed]
    return vectorops.region(closed, tol) if closed else []


def _rep_point(o: VectorObject):
    c = o.contours[0]
    return c.segs[0].point_at(0.5)


def group_parts(objects: list[VectorObject], tol: float = 0.05) -> list[Part]:
    """Group objects so that holes and decorations travel with their outline.

    An object nested inside an odd number of closed objects belongs to the
    smallest of them; even depths (0, 2, ...) start a new part (part in a hole).
    """
    objs = [o for o in objects if o.contours and o.contours[0].segs]
    info = []
    for o in objs:
        reg = _region(o, tol)
        info.append((o, reg, o.bbox(), abs(clip.area(reg)) if reg else 0.0))
    parent: dict[str, VectorObject | None] = {}
    depth: dict[str, int] = {}
    for o, _, bb, area in info:
        p = _rep_point(o)
        containers = []
        for c, creg, cbb, carea in info:
            if c is o or not creg or carea <= area:
                continue
            if not cbb.contains_box(bb, tol):
                continue
            if clip.point_in_region(p, creg):
                containers.append((carea, c))
        depth[o.id] = len(containers)
        parent[o.id] = min(containers, key=lambda t: t[0])[1] if containers else None
    by_id = {o.id: o for o in objs}

    def root_of(o: VectorObject) -> VectorObject:
        seen = set()
        while depth[o.id] % 2 == 1 and parent[o.id] is not None and o.id not in seen:
            seen.add(o.id)
            o = parent[o.id]
        return o

    parts: dict[str, Part] = {}
    order: list[str] = []
    for o in objs:
        r = root_of(o)
        if r.id not in parts:
            parts[r.id] = Part(r)
            order.append(r.id)
        if r is not o:
            parts[r.id].members.append(o)
    return [parts[i] for i in order if i in by_id]


def part_outline(part: Part, tol: float = 0.05) -> list[clip.Poly]:
    polys = _region(part.root, tol)
    opened = [c.flatten(tol) for o in part.objects for c in o.contours if not c.closed]
    if opened:
        polys += clip.offset_lines(opened, 0.5, "round")
    if not polys:
        bb = part.root.bbox()
        polys = [bb.corners()]
    return [p for p, _ in clip.polytree(polys, "nonzero")]


@dataclass
class NestOutcome:
    result: NestResult
    sheets: list[Sheet]
    placed: int
    unplaced: int


def nest_objects(project: Project, objects: list[VectorObject], settings: NestSettings,
                 quantities: dict[str, int] | None = None, target_sheets: list[Sheet] | None = None,
                 progress: Callable | None = None, cancel: Callable[[], bool] | None = None) -> NestOutcome:
    """Nest ``objects`` onto sheets of ``settings`` size and move them there.

    ``quantities`` maps a part's root object id to the number of copies.
    ``target_sheets`` are reused (in order) before new sheets are created.
    """
    parts = group_parts(objects)
    shapes = []
    for p in parts:
        q = (quantities or {}).get(p.root.id, p.quantity)
        shapes.append(NestShape(part_outline(p), q, p))
    res = nest(shapes, settings, progress, cancel)
    sheets = list(target_sheets or [])
    while len(sheets) < res.sheets:
        s = project.add_sheet(None, settings.sheet_width, settings.sheet_height)
        sheets.append(s)
    for s in sheets[:res.sheets]:
        s.width, s.height = settings.sheet_width, settings.sheet_height
    # parts that were placed leave their old position; unplaced parts stay untouched
    placed_parts = {pl.shape for pl in res.placements}
    moving = {o.id for i in placed_parts for o in parts[i].objects}
    pristine = {i: [o.copy(keep_id=True) for o in parts[i].objects] for i in placed_parts}
    for s in project.sheets:
        s.vectors = [v for v in s.vectors if v.id not in moving]
    for pl in res.placements:
        part = parts[pl.shape]
        T = Transform.translate(pl.x, pl.y) @ Transform.rotate(math.radians(pl.angle))
        if pl.copy == 0:
            objs = part.objects
            for o, src in zip(objs, pristine[pl.shape]):
                o.contours = [c.copy() for c in src.contours]
        else:
            objs = [o.copy() for o in pristine[pl.shape]]
        for o in objs:
            o.transform(T)
            sheets[pl.sheet].vectors.append(o)
    return NestOutcome(res, sheets[:res.sheets], len(res.placements), len(res.unplaced))

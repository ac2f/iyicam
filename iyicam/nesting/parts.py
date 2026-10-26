"""Turn vector objects into nestable parts and apply nesting results to a project."""
from __future__ import annotations

import math
from dataclasses import dataclass, field, replace
from typing import Callable

from ..core import clip, vectorops
from ..core.document import Project, Sheet, VectorObject
from ..core.geom import Transform
from ..core.stock import StockItem
from .nest import Container, NestResult, NestSettings, NestShape, Squeeze, nest


ROTATION_MODES = [
    ("default", "As set above"),
    ("none", "Never rotate"),
    ("flip", "0 / 180° only"),
    ("limit", "Tilt up to ±"),
    ("limit_flip", "Tilt up to ± or flipped 180°"),
    ("step", "Steps of"),
]
RULE_USES_VALUE = {"limit", "limit_flip", "step"}


def parse_angles(text: str) -> list[float]:
    """Angles in degrees from text like ``0, 45, 90.5`` (commas, semicolons or spaces)."""
    parts = [t for t in text.replace(";", " ").replace(",", " ").split() if t]
    angles = [float(t) for t in parts]  # raises ValueError for anything else
    return sorted(set(angles)) or [0.0]


def parse_rule(text: str) -> dict:
    """A rotation rule from text: ``none``, ``flip``, ``limit:10``, ``limit-flip:10``, ``step:15``, ``default``."""
    mode, _, value = text.strip().lower().replace("-", "_").partition(":")
    if mode not in dict(ROTATION_MODES) and mode != "inherit":
        raise ValueError(f"unknown rotation rule {text!r}")
    return {"mode": mode, "value": float(value) if value else 0.0}


def rotation_angles(rule: dict | None, tilt_step: float = 5.0) -> list[float] | None:
    """The angles a part may take under a rule (degrees); ``None``: the angles of the settings."""
    mode = (rule or {}).get("mode", "default")
    v = float((rule or {}).get("value", 0.0))
    if mode == "none":
        return [0.0]
    if mode == "flip":
        return [0.0, 180.0]
    if mode in ("limit", "limit_flip"):
        step = max(0.01, tilt_step)
        n = int(v / step + 1e-9)
        angles = [round(k * step, 6) for k in range(-n, n + 1)]
        if mode == "limit_flip":
            angles += [a + 180.0 for a in angles]
        return angles
    if mode == "step":
        return [0.0] if v <= 0 else [round(k * v, 6) for k in range(max(1, int(round(360.0 / v))))]
    return None


def rule_allows_tilt(rule: dict | None) -> bool:
    """Whether squeezing may tilt a part off its angles: not when it is told not to rotate (or only a little)."""
    return (rule or {}).get("mode", "default") not in ("none", "flip", "limit", "limit_flip")


def squeeze_lines(parts, squeezed) -> list[str]:
    """Plain words for the parts that were (or would be) squeezed in."""
    groups: dict[tuple, int] = {}
    for q in squeezed:
        key = (q.shape, round(abs(q.tilt), 3), round(q.relief, 3))
        groups[key] = groups.get(key, 0) + 1
    lines = []
    for (shape, tilt, relief), n in groups.items():
        how = []
        if tilt > 1e-9:
            how.append(f"tilted {tilt:g}°")
        if relief > 1e-9:
            how.append(f"{relief:g} mm closer to the edge")
        name = parts[shape].root.name or f"Part {shape + 1}"
        lines.append(f"  {name}{f' x{n}' if n > 1 else ''}: " + " and ".join(how))
    return lines


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
    plates: list = field(default_factory=list)  # what each sheet was made from: StockItem, Sheet or None (new)
    parts: list[Part] = field(default_factory=list)  # the parts, in the order the results refer to them
    squeezed: list[Squeeze] = field(default_factory=list)  # parts squeezed in (tilted / closer to the edge)
    suggestion: NestResult | None = None  # a better layout with parts squeezed in that was not taken


@dataclass
class NestJob:
    """Everything a nesting needs, made up front so that the heavy part (:func:`run_nesting`) can run in
    another thread while the rest of the program stays alive."""

    project: Project
    parts: list[Part]
    shapes: list[NestShape]
    settings: NestSettings
    use_plates: bool
    target_sheets: list[Sheet]


def prepare_nesting(project: Project, objects: list[VectorObject], settings: NestSettings,
                    quantities: dict[str, int] | None = None, target_sheets: list[Sheet] | None = None,
                    stock: list[StockItem] | None = None, use_sheets: list[Sheet] | None = None,
                    new_sheets: bool = True, rotation_rules: dict[str, dict] | None = None,
                    layer_rules: dict[str, dict] | None = None, tilt_step: float = 5.0) -> NestJob:
    """The parts, their outlines and the plates of a nesting (see :func:`nest_objects`); changes nothing."""
    from .stock import sheet_container, to_container
    parts = group_parts(objects)
    shapes = []
    for p in parts:
        q = (quantities or {}).get(p.root.id, p.quantity)
        rule = (rotation_rules or {}).get(p.root.id)
        if (rule or {}).get("mode", "inherit") == "inherit":
            rule = (layer_rules or {}).get(p.root.layer_id)
        shapes.append(NestShape(part_outline(p), q, p, rotation_angles(rule, tilt_step), rule_allows_tilt(rule)))
    use_plates = stock is not None or use_sheets is not None
    if use_plates:
        moving_ids = {o.id for p in parts for o in p.objects}
        containers = [sheet_container(s, moving_ids) for s in use_sheets or []]
        containers += [to_container(it) for it in stock or []]
        if new_sheets:
            containers.append(Container.rect(settings.sheet_width, settings.sheet_height))
        settings = replace(settings, containers=containers)
    return NestJob(project, parts, shapes, settings, use_plates, list(target_sheets or []))


def run_nesting(job: NestJob, progress: Callable | None = None,
                cancel: Callable[[], bool] | None = None) -> NestResult:
    """The layout of a prepared nesting. Reads nothing of the project, so it can run in a thread;
    ``NestCancelled`` is raised when ``cancel()`` says yes."""
    return nest(job.shapes, job.settings, progress, cancel)


def apply_nesting(job: NestJob, res: NestResult, choose: Callable[[NestResult, NestResult], bool] | None = None
                  ) -> NestOutcome:
    """Move the parts to where the layout ``res`` puts them; ``choose(layout, better)`` says whether to
    take the layout with parts squeezed in instead (``None``: no)."""
    project, parts, settings, use_plates = job.project, job.parts, job.settings, job.use_plates
    suggestion = res.suggestion
    if suggestion is not None and choose is not None and choose(res, suggestion):
        res, suggestion = suggestion, None  # the layout with parts squeezed in
    # the sheet of every plate that was used
    spare = list(job.target_sheets)
    sheets: list[Sheet] = []
    for k in range(res.sheets):
        c = settings.containers[res.stock[k]] if use_plates else None
        if c is not None and isinstance(c.payload, Sheet):
            sheets.append(c.payload)  # an existing sheet keeps its size and what is on it
            continue
        s = spare.pop(0) if spare else project.add_sheet(None, settings.sheet_width, settings.sheet_height)
        item = c.payload if c is not None else None
        if isinstance(item, StockItem):
            s.width, s.height = item.width, item.height
            s.outline = [list(p) for p in item.outline] if item.outline else None
            s.thickness = item.thickness
            if item.name:
                s.name = item.name if item.quantity == 1 else f"{item.name} {res.units[k] + 1}"
        else:
            s.width, s.height = settings.sheet_width, settings.sheet_height
            s.outline = None
        sheets.append(s)
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
    made_from = [settings.containers[i].payload for i in res.stock] if use_plates else [None] * res.sheets
    return NestOutcome(res, sheets, len(res.placements), len(res.unplaced), made_from, parts, list(res.squeezed),
                       suggestion)


def nest_objects(project: Project, objects: list[VectorObject], settings: NestSettings,
                 quantities: dict[str, int] | None = None, target_sheets: list[Sheet] | None = None,
                 progress: Callable | None = None, cancel: Callable[[], bool] | None = None,
                 stock: list[StockItem] | None = None, use_sheets: list[Sheet] | None = None,
                 new_sheets: bool = True, rotation_rules: dict[str, dict] | None = None,
                 layer_rules: dict[str, dict] | None = None, tilt_step: float = 5.0,
                 choose: Callable[[NestResult, NestResult], bool] | None = None) -> NestOutcome:
    """Nest ``objects`` onto plates and move them there.

    Without ``stock`` and ``use_sheets`` the parts go onto sheets of ``settings`` size
    (``target_sheets`` are reused, in order, before new sheets are created).

    With them the plates are, in the order they are filled: the free areas of ``use_sheets`` (around
    what stays on them), the ``stock`` plates (leftovers of any shape first) and, when ``new_sheets``
    is set, as many new sheets of ``settings`` size as needed. Stock plates become sheets with their
    own size, outline and thickness.

    ``quantities`` maps a part's root object id to the number of copies.

    ``rotation_rules`` (part root id) and ``layer_rules`` (layer id) say how far parts may turn, see
    :func:`rotation_angles`; a part's own rule wins over its layer's (``{"mode": "inherit"}`` or none
    means its layer's), then the angles of ``settings`` are used. ``tilt_step`` is the step of limited rotation.

    With squeezing set in ``settings`` (squeeze_angle / squeeze_margin) the nesting may find a better
    layout with parts tilted a little or closer to the edge: ``choose(layout, better)`` says whether to
    take it (``None``: no).

    ``cancel()`` is asked all the time; when it says yes ``NestCancelled`` is raised and nothing is changed.
    The three steps are also available alone: :func:`prepare_nesting`, :func:`run_nesting` (the long one,
    for a thread) and :func:`apply_nesting`.
    """
    job = prepare_nesting(project, objects, settings, quantities, target_sheets, stock, use_sheets, new_sheets,
                          rotation_rules, layer_rules, tilt_step)
    return apply_nesting(job, run_nesting(job, progress, cancel), choose)

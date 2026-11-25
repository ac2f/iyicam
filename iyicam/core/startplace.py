"""Where a closed vector starts: the places the Start Points menu names (a corner, the middle of a side) and the changes of a
whole project that start the selected contours there.  The desktop does the same in ``gui/cutcontrol.py``, which needs Qt; this
is the part that does not, for the web app's engine (the desktop keeps its own copy until it is moved here)."""
from __future__ import annotations

from . import vectorops
from .document import Project, Sheet, VectorObject
from .geom import Contour, Pt, dist

PLACES = {"sw": "bottom left corner", "se": "bottom right corner", "nw": "top left corner", "ne": "top right corner",
          "s": "bottom", "n": "top", "w": "left", "e": "right"}


def place_name(place: str) -> str:
    return PLACES.get(place, place)


def place_point(c: Contour, place: str) -> Pt | None:
    """The point of a closed contour for a place: its extreme point towards a corner (``sw``, ``se``, ``nw``, ``ne``) or
    the point of a side's middle (``s``, ``n``, ``w``, ``e``)."""
    bb = c.bbox()
    dirs = {"sw": (-1, -1), "se": (1, -1), "nw": (-1, 1), "ne": (1, 1)}
    if place in dirs:
        dx, dy = dirs[place]
        pts = c.nodes() + c.flatten(0.05)
        return max(pts, key=lambda p: (dx * p[0] + dy * p[1], -dist(p, bb.center) * 1e-9))
    mid = {"s": (bb.center[0], bb.ymin), "n": (bb.center[0], bb.ymax),
           "w": (bb.xmin, bb.center[1]), "e": (bb.xmax, bb.center[1])}.get(place)
    return None if mid is None else c.nearest(mid)[2]


def cut_keys(objs: list[VectorObject]) -> list[tuple[VectorObject, int]]:
    """``(vector, contour index)`` of the closed contours of vectors: the ones that have a start."""
    return [(o, i) for o in objs for i, c in enumerate(o.contours) if c.closed and c.segs]


def set_starts(items: list[tuple[VectorObject, int, Pt]], lock: bool = True) -> int:
    """Start contours at points (a new node where there is none) and keep them there (``lock``: smart start points leave them
    alone).  Starts that are there already and kept are not touched.  How many contours now start where they were asked to."""
    valid = 0
    for o, ci, point in items:
        if not 0 <= ci < len(o.contours) or not o.contours[ci].closed or not o.contours[ci].segs:
            continue
        valid += 1
        if dist(o.contours[ci].start, point) > 1e-6 or (lock and ci not in o.locked_starts):
            o.contours[ci] = vectorops.set_start_point(o.contours[ci], point)
            if lock and ci not in o.locked_starts:
                o.locked_starts.append(ci)
    return valid


def starts_at_place(keys: list[tuple[VectorObject, int]], place: str) -> int:
    """Start the contours ``keys`` (see :func:`cut_keys`) at a corner or the middle of a side, and keep them there."""
    items = []
    for o, ci in keys:
        q = place_point(o.contours[ci], place)
        if q is not None:
            items.append((o, ci, q))
    return set_starts(items)


def step_starts(keys: list[tuple[VectorObject, int]], direction: int = 1) -> int:
    """Start at the next (1) or previous (-1) corner or arc extreme."""
    items = []
    for o, ci in keys:
        q = vectorops.step_start(o.contours[ci], direction)
        if q is not None:
            items.append((o, ci, q))
    return set_starts(items)


def start_opposite(keys: list[tuple[VectorObject, int]]) -> int:
    """Start half way round from where they start now."""
    items = [(o, ci, o.contours[ci].point_at_length(o.contours[ci].length() / 2)) for o, ci in keys
             if o.contours[ci].length() > 1e-9]
    return set_starts(items)


def release_starts(objs: list[VectorObject], keys: list[tuple[VectorObject, int]] | None = None) -> int:
    """Give the start points back to smart start points (of the contours ``keys`` only, else of every contour of ``objs``):
    how many were kept by hand."""
    n = 0
    if keys is None:
        for o in objs:
            n += len(o.locked_starts)
            o.locked_starts = []
    else:
        for o, ci in keys:
            if ci in o.locked_starts:
                o.locked_starts.remove(ci)
                n += 1
    return n


def sequenced_toolpaths(project: Project, registry) -> list:
    """Toolpaths that cut vector by vector (profile, engrave, ...): the ones smart start points work for."""
    from ..cam.profile import ProfileOp
    out = []
    for t in project.toolpaths:
        op = registry.operations.get(t.op)
        if op is not None and issubclass(op, ProfileOp):
            out.append(t)
    return out


def default_toolpath(project: Project, sheet: Sheet, registry, selection: list[str], wanted: str | None = None) -> str | None:
    """The toolpath smart start points are calculated with: the one asked for, else one that machines the selected vectors, else
    the first."""
    tps = sequenced_toolpaths(project, registry)
    ids = {t.id for t in tps}
    if wanted in ids:
        return wanted
    sel = set(selection)
    for t in tps:
        if sel & {o.id for o in project.vectors_for(t, sheet)}:
            return t.id
    return tps[0].id if tps else None

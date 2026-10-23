"""Ordering helpers: containment levels and nearest-neighbour sequencing."""
from __future__ import annotations

import math
from typing import Callable, Sequence, TypeVar

from ..core.geom import BBox, Contour, Pt, dist, point_in_polygon

T = TypeVar("T")


def containment_levels(contours: Sequence[Contour], tol: float = 0.05) -> list[int]:
    """For each contour, how many *closed* contours contain its start point."""
    polys = [c.flatten(tol) if c.closed else None for c in contours]
    boxes = [c.bbox() for c in contours]
    levels = []
    for i, c in enumerate(contours):
        p = c.start
        n = 0
        for j, poly in enumerate(polys):
            if i == j or poly is None:
                continue
            if not boxes[j].contains_box(boxes[i]):
                continue
            if point_in_polygon(p, poly):
                n += 1
        levels.append(n)
    return levels


def nearest_order(items: Sequence[T], entry: Callable[[T, Pt], Pt], exit_: Callable[[T, Pt], Pt],
                  start: Pt = (0.0, 0.0)) -> list[T]:
    """Greedy nearest neighbour ordering.

    ``entry(item, pos)`` gives the point where the item would be entered from
    ``pos`` and ``exit_(item, entry_point)`` where it is left.
    """
    remaining = list(items)
    out: list[T] = []
    pos = start
    while remaining:
        best_i, best_d, best_e = 0, math.inf, pos
        for i, it in enumerate(remaining):
            e = entry(it, pos)
            d = dist(pos, e)
            if d < best_d:
                best_i, best_d, best_e = i, d, e
        it = remaining.pop(best_i)
        out.append(it)
        pos = exit_(it, best_e)
    return out


def leveled_order(items: Sequence[T], levels: Sequence[int], entry, exit_, start: Pt = (0.0, 0.0)) -> list[T]:
    """Deepest containment level first (holes before outlines), nearest neighbour inside a level."""
    out: list[T] = []
    pos = start
    for lv in sorted(set(levels), reverse=True):
        group = [it for it, l in zip(items, levels) if l == lv]
        ordered = nearest_order(group, entry, exit_, pos)
        out += ordered
        if ordered:
            last = ordered[-1]
            pos = exit_(last, entry(last, pos))
    return out


def bbox_center(c: Contour) -> Pt:
    return c.bbox().center


def union_bbox(contours: Sequence[Contour]) -> BBox:
    bb = BBox()
    for c in contours:
        bb = bb.union(c.bbox())
    return bb

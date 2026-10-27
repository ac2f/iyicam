"""Polygon offsetting and boolean operations (thin wrapper around pyclipper).

All polygons are lists of ``(x, y)`` float points in millimetres.  Closed
polygons do not repeat their first point.  Outer boundaries returned by these
functions are counter-clockwise and holes clockwise.
"""
from __future__ import annotations

from typing import Iterable, Sequence

import numpy as np
import pyclipper

from .geom import Contour, Pt, polygon_area

SCALE = 10000.0  # 0.1 micron resolution

Poly = list[Pt]

_JOIN = {"round": pyclipper.JT_ROUND, "miter": pyclipper.JT_MITER, "square": pyclipper.JT_SQUARE}
_END = {"round": pyclipper.ET_OPENROUND, "butt": pyclipper.ET_OPENBUTT, "square": pyclipper.ET_OPENSQUARE}
_FILL = {"evenodd": pyclipper.PFT_EVENODD, "nonzero": pyclipper.PFT_NONZERO,
         "positive": pyclipper.PFT_POSITIVE}
_OPS = {"union": pyclipper.CT_UNION, "difference": pyclipper.CT_DIFFERENCE,
        "intersection": pyclipper.CT_INTERSECTION, "xor": pyclipper.CT_XOR}


class Clipper(pyclipper.Pyclipper):
    """``pyclipper.Pyclipper`` that takes a path it cannot use (no area, a spike) as nothing instead of raising
    "All paths are invalid for clipping": a vector of such paths simply has no region."""

    _used = False

    def AddPath(self, path, poly_type, closed=True):
        try:
            ok = super().AddPath(path, poly_type, closed)
        except pyclipper.ClipperException:
            return False
        self._used = self._used or bool(ok)
        return ok

    def AddPaths(self, paths, poly_type, closed=True):
        try:
            ok = super().AddPaths(paths, poly_type, closed)
        except pyclipper.ClipperException:
            return False
        self._used = self._used or bool(ok)
        return ok

    def Execute(self, *args, **kwargs):
        return super().Execute(*args, **kwargs) if self._used else []


class Offsetter(pyclipper.PyclipperOffset):
    """``pyclipper.PyclipperOffset`` with the same tolerance for unusable paths."""

    _used = False

    def AddPath(self, path, join_type, end_type):
        try:
            super().AddPath(path, join_type, end_type)
        except pyclipper.ClipperException:
            return
        self._used = True

    def AddPaths(self, paths, join_type, end_type):
        try:
            super().AddPaths(paths, join_type, end_type)
        except pyclipper.ClipperException:
            return
        self._used = True

    def Execute(self, *args, **kwargs):
        return super().Execute(*args, **kwargs) if self._used else []


def to_int(pts: Sequence[Pt]) -> list[tuple[int, int]]:
    return [(round(x * SCALE), round(y * SCALE)) for x, y in pts]


def from_int(path: Sequence[Sequence[int]]) -> Poly:
    return [(x / SCALE, y / SCALE) for x, y in path]


def _clean(paths: Iterable[Sequence[Pt]], closed: bool = True) -> list[list[tuple[int, int]]]:
    out = []
    for p in paths:
        ip = to_int(p)
        # drop consecutive duplicates introduced by rounding
        dedup = [ip[0]] if ip else []
        for q in ip[1:]:
            if q != dedup[-1]:
                dedup.append(q)
        if closed and len(dedup) > 1 and dedup[0] == dedup[-1]:
            dedup.pop()
        if len(dedup) >= (3 if closed else 2) and not (closed and _on_a_line(dedup)):
            out.append(dedup)
    return out


def _on_a_line(path: Sequence[tuple[int, int]]) -> bool:
    """Whether all points of a path are on one straight line (a polygon with no area: Clipper refuses it)."""
    (x0, y0), (x1, y1) = path[0], path[1]
    for x, y in path[2:]:
        if (x1 - x0) * (y - y0) - (y1 - y0) * (x - x0) != 0:
            return False
    return True


def contours_to_polys(contours: Iterable[Contour], tol: float = 0.005) -> list[Poly]:
    """Flatten the closed contours of ``contours``."""
    return [c.flatten(tol) for c in contours if c.closed and len(c.segs) > 0]


class TooComplex(ValueError):
    """Shapes lie on top of each other so much that putting them together would take minutes (or forever:
    Clipper cannot be interrupted)."""


MAX_OVERLAPS = 300_000   # pairs of shapes that overlap, counted as ten-point shapes: about 15 seconds of Clipper
MAX_STACK = 120          # shapes on top of each other at one spot (a hundred squares on the same spot: a second)
CHECK_FROM = 100         # (fewer shapes cannot get there)


def check_overlaps(paths: Sequence[Sequence[tuple[int, int]]]) -> None:
    """Refuse a pile of shapes that would take Clipper too long: ``TooComplex`` with the reason.

    How many lie on top of each other is estimated from the boxes of the shapes on a grid (cells the size of a
    small shape), so a thousand parts side by side cost nothing and a thousand copies of one part on the same
    spot are refused."""
    n = len(paths)
    if n < CHECK_FROM:
        return
    boxes = []
    for p in paths:
        xs, ys = [q[0] for q in p], [q[1] for q in p]
        boxes.append((min(xs), max(xs), min(ys), max(ys)))
    sizes = sorted(max(b[1] - b[0], b[3] - b[2]) for b in boxes)
    base = max(sizes[n // 5], 1)
    occupied: dict[tuple[int, int, int], int] = {}
    for x0, x1, y0, y1 in boxes:
        cell, level = base, 0
        while (x1 // cell) - (x0 // cell) > 1 or (y1 // cell) - (y0 // cell) > 1:
            cell, level = cell * 2, level + 1       # (a shape is counted among those of its own size)
        for cx in range(x0 // cell, x1 // cell + 1):
            for cy in range(y0 // cell, y1 // cell + 1):
                occupied[(level, cx, cy)] = occupied.get((level, cx, cy), 0) + 1
    stack = max(occupied.values(), default=0)
    pairs = sum(k * (k - 1) // 2 for k in occupied.values())
    points = sum(len(p) for p in paths) / n
    if stack > MAX_STACK or pairs * max(1.0, points / 10) > MAX_OVERLAPS:
        raise TooComplex(f"{stack} of {n} shapes lie on top of each other at one spot: this would take far too "
                         f"long. Remove the copies that sit on the same spot, or select fewer vectors.")


def without_copies(paths: list[list[tuple[int, int]]], any_way_round: bool = True) -> list[list[tuple[int, int]]]:
    """The paths with each outline once, however many times and from whichever point it is drawn, and (with
    ``any_way_round``) in whichever direction (a doubled drawing in a file: with the even-odd rule two of the
    same would cancel out; with the non-zero rule, where direction counts, a hole drawn on a part is kept)."""
    seen = set()
    out = []
    for p in paths:
        q = p
        if any_way_round and sum(p[i][0] * p[i - 1][1] - p[i - 1][0] * p[i][1] for i in range(len(p))) < 0:
            q = p[::-1]
        k = min(range(len(q)), key=q.__getitem__)
        key = tuple(q[k:] + q[:k])
        if key not in seen:
            seen.add(key)
            out.append(p)
    return out


def normalize(polys: Iterable[Sequence[Pt]], fill: str = "evenodd") -> list[Poly]:
    """Resolve self-intersections / nesting into clean outer (CCW) + hole (CW) polygons."""
    paths = _clean(polys)
    if not paths:
        return []
    if len(paths) > 1:
        paths = without_copies(paths, fill == "evenodd")
    check_overlaps(paths)
    pc = Clipper()
    pc.AddPaths(paths, pyclipper.PT_SUBJECT, True)
    res = pc.Execute(pyclipper.CT_UNION, _FILL[fill], _FILL[fill])
    return [from_int(p) for p in res]


def boolean(subject: Iterable[Sequence[Pt]], clip: Iterable[Sequence[Pt]], op: str,
            fill: str = "nonzero") -> list[Poly]:
    s, c = _clean(subject), _clean(clip)
    if not s and op in ("intersection", "difference"):
        return []
    pc = Clipper()
    if s:
        pc.AddPaths(s, pyclipper.PT_SUBJECT, True)
    if c:
        pc.AddPaths(c, pyclipper.PT_CLIP, True)
    if not s and not c:
        return []
    res = pc.Execute(_OPS[op], _FILL[fill], _FILL[fill])
    return [from_int(p) for p in res]


def union(polys: Iterable[Sequence[Pt]], fill: str = "nonzero") -> list[Poly]:
    return normalize(polys, fill)


def offset(polys: Iterable[Sequence[Pt]], delta: float, join: str = "round",
           arc_tol: float = 0.002, miter_limit: float = 4.0) -> list[Poly]:
    """Offset closed polygons (outer CCW / holes CW); positive delta grows the area."""
    paths = _clean(polys)
    if not paths:
        return []
    po = Offsetter(miter_limit, max(1.0, arc_tol * SCALE))
    po.AddPaths(paths, _JOIN[join], pyclipper.ET_CLOSEDPOLYGON)
    res = po.Execute(delta * SCALE)
    return [from_int(p) for p in res]


def offset_lines(paths: Iterable[Sequence[Pt]], delta: float, end: str = "round",
                 join: str = "round", closed: bool = False, arc_tol: float = 0.002) -> list[Poly]:
    """Grow open (or closed, as lines) paths into polygons of half width ``delta``."""
    ints = _clean(paths, closed=closed)
    if not ints or delta <= 0:
        return []
    po = Offsetter(4.0, max(1.0, arc_tol * SCALE))
    et = pyclipper.ET_CLOSEDLINE if closed else _END[end]
    po.AddPaths(ints, _JOIN[join], et)
    return [from_int(p) for p in po.Execute(delta * SCALE)]


def clip_lines(lines: Iterable[Sequence[Pt]], polys: Iterable[Sequence[Pt]],
               inside: bool = True) -> list[Poly]:
    """Clip open polylines against polygons; returns the parts inside (or outside)."""
    ls = _clean(lines, closed=False)
    ps = _clean(polys)
    if not ls:
        return []
    if not ps:
        return [] if inside else [from_int(p) for p in ls]
    pc = Clipper()
    pc.AddPaths(ls, pyclipper.PT_SUBJECT, False)
    pc.AddPaths(ps, pyclipper.PT_CLIP, True)
    op = pyclipper.CT_INTERSECTION if inside else pyclipper.CT_DIFFERENCE
    tree = pc.Execute2(op, pyclipper.PFT_NONZERO, pyclipper.PFT_EVENODD)
    return [from_int(p) for p in pyclipper.OpenPathsFromPolyTree(tree)]


def polytree(polys: Iterable[Sequence[Pt]], fill: str = "evenodd") -> list[tuple[Poly, list[Poly]]]:
    """Group polygons into ``(outer, [holes])`` islands (islands inside holes are separate)."""
    paths = _clean(polys)
    if not paths:
        return []
    pc = Clipper()
    pc.AddPaths(paths, pyclipper.PT_SUBJECT, True)
    tree = pc.Execute2(pyclipper.CT_UNION, _FILL[fill], _FILL[fill])
    out: list[tuple[Poly, list[Poly]]] = []

    def walk(node):
        for ch in node.Childs:
            if not ch.IsHole:
                holes = [from_int(h.Contour) for h in ch.Childs]
                out.append((from_int(ch.Contour), holes))
                for h in ch.Childs:
                    walk(h)

    walk(tree)
    return out


def point_in(p: Pt, poly: Sequence[Pt]) -> int:
    """1 inside, 0 outside, -1 on the boundary."""
    return pyclipper.PointInPolygon(to_int([p])[0], to_int(poly))


def point_in_region(p: Pt, polys: Sequence[Sequence[Pt]]) -> bool:
    """Even-odd membership test against a set of polygons."""
    q = to_int([p])[0]
    inside = False
    for poly in polys:
        r = pyclipper.PointInPolygon(q, to_int(poly))
        if r != 0:
            inside = not inside
    return inside


def area(polys: Iterable[Sequence[Pt]]) -> float:
    return sum(polygon_area(p) for p in polys)


def simplify(polys: Iterable[Sequence[Pt]], tol: float) -> list[Poly]:
    """Simplify closed polygons with Douglas-Peucker style cleaning."""
    out = []
    for p in _clean(polys):
        c = pyclipper.CleanPolygon(p, tol * SCALE)
        if len(c) >= 3:
            out.append(from_int(c))
    return out


def region_mask(points, polys: Iterable[Sequence[Pt]], chunk: int = 20000) -> np.ndarray:
    """Even-odd membership of many points (n x 2) in a set of polygons, as a boolean array."""
    P = np.asarray(points, float).reshape(-1, 2)
    inside = np.zeros(len(P), bool)
    for poly in polys:
        A = np.asarray(poly, float)
        B = np.roll(A, -1, axis=0)
        dy = B[:, 1] - A[:, 1]
        slope = np.where(dy == 0, 0.0, (B[:, 0] - A[:, 0]) / np.where(dy == 0, 1.0, dy))
        for k in range(0, len(P), max(1, chunk // max(1, len(A)))):
            x, y = P[k:k + max(1, chunk // max(1, len(A))), :1], P[k:k + max(1, chunk // max(1, len(A))), 1:]
            crosses = ((A[:, 1] > y) != (B[:, 1] > y)) & (x < A[:, 0] + slope * (y - A[:, 1]))
            n = len(crosses)
            inside[k:k + n] ^= (crosses.sum(axis=1) % 2 == 1)
    return inside

"""True shape nesting with no-fit polygons.

Every part is inflated by half the spacing, then placed one by one: the
feasible positions on a plate are the plate's usable area minus the union of
the no-fit polygons (Minkowski differences) of the parts already placed.  The
best vertex of that region is chosen with a gravity rule.  Several randomised
orders can be tried and the best layout is kept.

Plates are :class:`Container`s: any shape (leftovers with cut-outs, damaged
corners, holes), possibly with parts already fixed on them, in any number of
sizes and quantities.  They are filled one after the other, leftovers first, so
new sheets are only started when the leftovers are full.
"""
from __future__ import annotations

import math
import random
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Sequence

import numpy as np
import pyclipper

from ..core import clip
from ..core.geom import Pt, polygon_area, simplify_polyline

IntPath = list[tuple[int, int]]


class NestCancelled(Exception):
    """Raised by a nesting that was cancelled (the ``cancel`` callback said so)."""


@dataclass
class NestShape:
    polys: list[list[Pt]]          # outline polygons (holes are ignored)
    quantity: int = 1
    payload: Any = None
    rotations: list[float] | None = None  # degrees; None = NestSettings.rotations
    tilt: bool = True              # may be tilted a little off its angles to squeeze it in (NestSettings.squeeze_*)
    holes: list[list[Pt]] = field(default_factory=list)  # holes that other parts may be placed in


@dataclass
class Container:
    """A plate the parts can be placed on.

    ``region`` are the outlines of its material (outer boundaries and holes; even-odd,
    in the plate's own coordinates); ``obstacles`` are parts already on the plate, which
    new parts keep the spacing to.  ``quantity`` is how many such plates there are
    (0 = as many as needed); ``leftover`` plates are used before new ones."""

    region: list[list[Pt]]
    obstacles: list[list[Pt]] = field(default_factory=list)
    quantity: int = 0
    leftover: bool = False
    name: str = ""
    payload: Any = None

    @classmethod
    def rect(cls, width: float, height: float, **kw) -> "Container":
        return cls([[(0.0, 0.0), (width, 0.0), (width, height), (0.0, height)]], **kw)


@dataclass
class NestSettings:
    sheet_width: float = 2800.0    # the plate when no ``containers`` are given
    sheet_height: float = 2100.0
    margin: float = 10.0
    spacing: float = 8.0
    rotations: list[float] = field(default_factory=lambda: [0.0, 90.0, 180.0, 270.0])
    gravity: str = "left"          # left | bottom | compact
    max_sheets: int = 100
    iterations: int = 1            # layouts tried (see nest()), the best is kept
    time_limit: float = 0.0        # seconds after which no further attempt is started (0 = no limit)
    seed: int = 1
    tolerance: float = 0.5         # outline simplification (parts get this much extra spacing at most)
    max_points: int = 40           # a part outlined with more points is simplified more: faster, a little looser
    containers: list[Container] | None = None  # the stock; None = unlimited sheets of the size above
    stock_order: str = "leftovers"  # leftovers (leftovers first, small to large) | listed
    # squeezing in: a part that does not fit may be tilted off its angles by up to ``squeeze_angle`` degrees
    # (in steps of ``squeeze_step``) and / or come up to ``squeeze_margin`` mm closer to the edge of the plate
    # than the border gap; 0 = not used. The result is a suggestion (NestResult.suggestion) if it is better.
    squeeze_angle: float = 0.0
    squeeze_margin: float = 0.0
    squeeze_step: float = 2.0


@dataclass
class Placement:
    shape: int
    copy: int
    sheet: int     # index into NestResult.stock / utilization (the plates used, in order)
    angle: float   # degrees, rotation about the shape's own coordinate origin
    x: float       # translation applied after the rotation
    y: float


@dataclass
class Squeeze:
    """A part that only fits squeezed in: tilted by ``tilt`` degrees off its nearest angle and / or
    ``relief`` mm closer to the edge of the plate than the border gap."""

    shape: int
    copy: int
    sheet: int
    tilt: float
    relief: float


@dataclass
class NestResult:
    placements: list[Placement] = field(default_factory=list)
    sheets: int = 0
    unplaced: list[tuple[int, int]] = field(default_factory=list)
    utilization: list[float] = field(default_factory=list)  # part area / free area, per plate used
    stock: list[int] = field(default_factory=list)          # container index of every plate used
    units: list[int] = field(default_factory=list)          # which of the container's plates (0, 1, ...)
    squeezed: list[Squeeze] = field(default_factory=list)   # parts that were squeezed in (squeeze runs only)
    suggestion: "NestResult | None" = None                  # a better layout with parts squeezed in, if there is one
    used: list[tuple[float, float, float, float]] = field(default_factory=list)  # bounding box of the parts, per plate
    layout: list[list[tuple]] = field(default_factory=list, repr=False)  # per plate: what was placed (for polishing)


def is_convex(path: IntPath) -> bool:
    """True for a counter-clockwise polygon that is convex up to integer rounding noise."""
    n = len(path)
    for i in range(n):
        ax, ay = path[i - 1]
        bx, by = path[i]
        cx, cy = path[(i + 1) % n]
        cr = (bx - ax) * (cy - by) - (by - ay) * (cx - bx)
        if cr < -2.0 * (math.hypot(bx - ax, by - ay) + math.hypot(cx - bx, cy - by)):
            return False
    return True


def convex_hull(pts: IntPath) -> IntPath:
    """Counter-clockwise convex hull (monotone chain)."""
    P = sorted(set(pts))
    if len(P) < 3:
        return P

    def half(points):
        h: list = []
        for p in points:
            while len(h) >= 2 and (h[-1][0] - h[-2][0]) * (p[1] - h[-2][1]) - \
                    (h[-1][1] - h[-2][1]) * (p[0] - h[-2][0]) <= 0:
                h.pop()
            h.append(p)
        return h

    lower, upper = half(P), half(reversed(P))
    return lower[:-1] + upper[:-1]


def convex_minkowski(a: IntPath, b: IntPath) -> IntPath:
    """Minkowski sum of two counter-clockwise convex polygons (O(n + m) edge merge)."""
    def start(p):
        return min(range(len(p)), key=lambda i: (p[i][1], p[i][0]))

    def edges(p, k):
        n = len(p)
        return [(p[(k + i + 1) % n][0] - p[(k + i) % n][0], p[(k + i + 1) % n][1] - p[(k + i) % n][1])
                for i in range(n)]

    ia, ib = start(a), start(b)
    ea, eb = edges(a, ia), edges(b, ib)
    x, y = a[ia][0] + b[ib][0], a[ia][1] + b[ib][1]
    out = [(x, y)]
    i = j = 0
    while i < len(ea) or j < len(eb):
        if j >= len(eb):
            e = ea[i]
            i += 1
        elif i >= len(ea):
            e = eb[j]
            j += 1
        else:
            cr = ea[i][0] * eb[j][1] - ea[i][1] * eb[j][0]
            if cr > 0:
                e = ea[i]
                i += 1
            elif cr < 0:
                e = eb[j]
                j += 1
            else:
                e = (ea[i][0] + eb[j][0], ea[i][1] + eb[j][1])
                i += 1
                j += 1
        x, y = x + e[0], y + e[1]
        out.append((x, y))
    out.pop()  # back at the start
    return out


def _cross(o, a, b) -> float:
    return (a[0] - o[0]) * (b[1] - o[1]) - (a[1] - o[1]) * (b[0] - o[0])


def _in_triangle(p, a, b, c) -> bool:
    return _cross(a, b, p) >= 0 and _cross(b, c, p) >= 0 and _cross(c, a, p) >= 0


def convex_parts(path: IntPath) -> list[IntPath] | None:
    """Split a simple counter-clockwise polygon into convex pieces.

    Ear clipping followed by Hertel-Mehlhorn merging.  Returns ``None`` when the
    polygon is degenerate (the caller then falls back to Clipper's Minkowski).
    """
    P = list(path)
    idx = list(range(len(P)))
    tris: list[list[int]] = []
    guard = 0
    while len(idx) > 3 and guard < 10 * len(P) + 10:
        guard += 1
        m = len(idx)
        for k in range(m):
            i0, i1, i2 = idx[k - 1], idx[k], idx[(k + 1) % m]
            a, b, c = P[i0], P[i1], P[i2]
            cr = _cross(a, b, c)
            if cr == 0:
                idx.pop(k)  # collinear vertex: no area to lose
                break
            if cr < 0:
                continue
            if any(_in_triangle(P[j], a, b, c) for j in idx if j not in (i0, i1, i2) and P[j] not in (a, b, c)):
                continue
            tris.append([i0, i1, i2])
            idx.pop(k)
            break
        else:
            return None
    if len(idx) == 3 and _cross(P[idx[0]], P[idx[1]], P[idx[2]]) > 0:
        tris.append(idx)
    elif len(idx) > 3:
        return None
    pieces = tris
    merged = True
    while merged:
        merged = False
        for a in range(len(pieces)):
            for b in range(a + 1, len(pieces)):
                pa, pb = pieces[a], pieces[b]
                for i in range(len(pa)):
                    u, v = pa[i], pa[(i + 1) % len(pa)]
                    if v in pb and pb[(pb.index(v) + 1) % len(pb)] == u:
                        # shared edge u->v in pa is v->u in pb: splice pb into pa
                        j = pb.index(u)
                        rest = [pb[(j + 1 + k) % len(pb)] for k in range(len(pb) - 2)]
                        cand = pa[:i + 1] + rest + pa[i + 1:]
                        if is_convex([P[t] for t in cand]):
                            pieces[a] = cand
                            del pieces[b]
                            merged = True
                        break
                if merged:
                    break
            if merged:
                break
    return [[P[t] for t in pc] for pc in pieces]


class _Variant:
    __slots__ = ("paths", "bbox", "area", "convex", "pieces", "sig", "holes", "solid")

    def __init__(self, paths: list[IntPath], holes: list[IntPath] | None = None):
        self.paths = paths
        self.holes = holes or []          # the room inside the part, counter-clockwise like the outline
        # outline minus holes as one set of paths (non-zero fill): what is really solid
        self.solid = paths + [list(reversed(h)) for h in self.holes]
        xs = [p[0] for path in paths for p in path]
        ys = [p[1] for path in paths for p in path]
        self.bbox = (min(xs), min(ys), max(xs), max(ys))
        # the shape wherever it lies: two angles with the same one only differ by a move (a rectangle at
        # 0 and 180 degrees)
        self.sig = frozenset([(x - self.bbox[0], y - self.bbox[1]) for path in paths for x, y in path]
                             + [(x - self.bbox[0], y - self.bbox[1], 1) for path in self.holes for x, y in path])
        self.area = sum(abs(pyclipper.Area(p)) for p in paths)
        # nearly convex outlines use their hull: a slightly larger, safe no-fit polygon
        self.convex = [convex_hull(p) if is_convex(p) else None for p in paths]
        pieces: list[IntPath] | None = []
        for p, hull in zip(paths, self.convex):
            parts = [hull] if hull else convex_parts(p)
            if parts is None:
                pieces = None
                break
            pieces += parts
        self.pieces = pieces


def _rotate_int(paths: list[list[Pt]], ang: float) -> list[IntPath]:
    c, s = math.cos(math.radians(ang)), math.sin(math.radians(ang))
    out = []
    for p in paths:
        rp = [(x * c - y * s, x * s + y * c) for x, y in p]
        ip = clip.to_int(rp)
        if pyclipper.Orientation(ip) is False:
            ip.reverse()
        out.append(ip)
    return out


def _same_outline(a: np.ndarray, b: np.ndarray, tol: float = 0.02) -> bool:
    """Whether two outlines (n x 2 points, the lower left of their bounding boxes at the origin) have the
    same points, whatever their order and start point, within ``tol`` mm."""
    if len(a) != len(b):
        return False
    xs = b[np.argsort(b[:, 0])]
    for x, y in a:
        lo, hi = np.searchsorted(xs[:, 0], [x - tol, x + tol])
        if lo == hi or not np.any(np.abs(xs[lo:hi, 1] - y) <= tol):
            return False
    return True


def _outline(outer: list[list[Pt]], half: float, tol: float, max_points: int) -> list[list[Pt]]:
    """The outline of a part for nesting: grown by half the gap and simplified.

    Round joins lie within tol/2 and the simplification within tol inside the true outline, so growing by
    an extra 1.5 tol keeps the simplified outline on the safe side. A part with more than ``max_points``
    points is simplified more (the tolerance grows by half each time, up to 4 mm), which makes the
    nesting a lot faster and leaves the part a little further from its neighbours."""
    t = max(0.01, tol)
    while True:
        grown = clip.offset(outer, half + 1.5 * t, "round", arc_tol=t / 2)
        grown = [q for q in (simplify_polyline(p + [p[0]], t)[:-1] for p in grown) if len(q) >= 3]
        grown = [p for p, _ in clip.polytree(grown, "nonzero")]
        if max_points <= 0 or sum(len(p) for p in grown) <= max_points or t >= 4.0:
            return grown
        t = min(4.0, t * 1.5)


def _rooms(holes: list[list[Pt]], half: float, tol: float, most: int = 6) -> list[list[Pt]]:
    """The room in the holes of a part for other (grown) parts: the holes shrunk by half the gap and a
    little more, simplified towards the inside. At most the ``most`` biggest holes are kept."""
    if not holes:
        return []
    t = max(0.01, tol)
    out = []
    for h in sorted(holes, key=lambda q: -abs(polygon_area(q)))[:most]:
        if polygon_area(h) < 0:
            h = h[::-1]   # counter-clockwise, so that a negative offset shrinks it
        shrunk = clip.offset([h], -(half + 1.5 * t), "round", arc_tol=t / 2)
        for q in shrunk:
            q = simplify_polyline(q + [q[0]], t)[:-1]
            if len(q) >= 3 and abs(polygon_area(q)) > 1.0:
                out.append(q)
    return out


def _turn(paths: list[IntPath], angle: float) -> list[IntPath]:
    """The paths turned by ``angle`` degrees about the origin (exact for quarter turns)."""
    angle = angle % 360.0
    k = round(angle / 90.0)
    if abs(angle - 90.0 * k) < 1e-9:
        k %= 4
        if k == 0:
            return paths
        f = ((lambda x, y: (-y, x)), (lambda x, y: (-x, -y)), (lambda x, y: (y, -x)))[k - 1]
        return [[f(x, y) for x, y in p] for p in paths]
    c, s = math.cos(math.radians(angle)), math.sin(math.radians(angle))
    return [[(round(x * c - y * s), round(x * s + y * c)) for x, y in p] for p in paths]


def _translate(paths: list[IntPath], dx: int, dy: int) -> list[IntPath]:
    return [[(x + dx, y + dy) for x, y in p] for p in paths]


def _axis_rect(polys: list[list[Pt]]) -> tuple[float, float, float, float] | None:
    """``(x0, y0, x1, y1)`` when the polygons are one axis-parallel rectangle."""
    if len(polys) != 1 or len(polys[0]) != 4:
        return None
    xs, ys = sorted({round(x, 6) for x, _ in polys[0]}), sorted({round(y, 6) for _, y in polys[0]})
    if len(xs) != 2 or len(ys) != 2:
        return None
    a, b, c, d = polys[0]
    if not ((a[0] == b[0] or a[1] == b[1]) and (b[0] == c[0] or b[1] == c[1])
            and (c[0] == d[0] or c[1] == d[1]) and (d[0] == a[0] or d[1] == a[1])):
        return None
    return (min(x for x, _ in polys[0]), min(y for _, y in polys[0]),
            max(x for x, _ in polys[0]), max(y for _, y in polys[0]))


class _Box:
    """A container prepared for nesting: the area the inflated parts must stay inside."""

    POINT_BUDGET = 400   # points of the usable area (see the simplification below)

    def __init__(self, index: int, c: Container, margin: float, spacing: float, tol: float, relief: float = 0.0):
        S = clip.SCALE
        half = spacing / 2.0
        self.index, self.c = index, c
        # parts may come ``relief`` closer to the edge of the plate (never past the edge itself)
        self.relief = min(relief, max(0.0, margin))
        margin = margin - self.relief
        region = clip.normalize(c.region)
        self.material = abs(clip.area(region))
        rect = None if c.obstacles else _axis_rect(region)
        if rect:
            x0, y0, x1, y1 = rect
            self.rect = (round((x0 + margin - half) * S), round((y0 + margin - half) * S),
                         round((x1 - margin + half) * S), round((y1 - margin + half) * S))
            self.paths: list[IntPath] = []
            self.bbox = self.rect
            self.free = max(0.0, (x1 - x0) * (y1 - y0))
            return
        # the margin keeps off the plate's edge, the spacing off the parts fixed on it; the outline
        # is simplified towards the inside so the parts stay on the safe side of it. A plate with a lot
        # of detail (many parts fixed on it, drawn with many points) is simplified more: every point costs
        # time in the no-fit polygons of every part, and the parts end up a little further from the detail
        ct = min(max(0.02, tol / 2), 0.1)
        fixed = clip.normalize(c.obstacles, "nonzero") if c.obstacles else []
        while True:
            usable = clip.offset(region, half - margin - 1.5 * ct, "round", arc_tol=ct / 2)
            usable = [q for q in (simplify_polyline(p + [p[0]], ct)[:-1] for p in usable) if len(q) >= 3]
            if fixed:
                grown = clip.offset(fixed, half + 1.5 * ct, "round", arc_tol=ct / 2)
                grown = [q for q in (simplify_polyline(p + [p[0]], ct)[:-1] for p in grown) if len(q) >= 3]
                usable = clip.boolean(usable, grown, "difference")
            if sum(len(q) for q in usable) <= self.POINT_BUDGET or ct >= 1.0:
                break
            ct = min(1.0, ct * 1.6)
        usable = clip.normalize(usable)
        self.free = abs(clip.area(clip.boolean(region, c.obstacles, "difference"))) if c.obstacles else self.material
        self.rect = None
        r = _axis_rect(usable)
        if r:
            self.rect = (round(r[0] * S), round(r[1] * S), round(r[2] * S), round(r[3] * S))
            self.paths = []
            self.bbox = self.rect
            return
        self.paths = [clip.to_int(p) for p in usable]
        if self.paths:
            xs = [q[0] for path in self.paths for q in path]
            ys = [q[1] for path in self.paths for q in path]
            self.bbox = (min(xs), min(ys), max(xs), max(ys))
        else:
            self.bbox = None

    @property
    def usable(self) -> bool:
        return self.bbox is not None and (self.rect is None or (self.rect[2] >= self.rect[0]
                                                                and self.rect[3] >= self.rect[1]))


class Nester:
    def __init__(self, shapes: Sequence[NestShape], settings: NestSettings):
        self.shapes = list(shapes)
        self.st = settings
        half = settings.spacing / 2.0
        conts = settings.containers or [Container.rect(settings.sheet_width, settings.sheet_height)]
        self.boxes = [_Box(i, c, settings.margin, settings.spacing, settings.tolerance)
                      for i, c in enumerate(conts)]
        # shapes with the same outline share their work: ``cls`` is the class of a shape; its outline is
        # kept with the lower left corner of the part's bounding box at the origin (``offset`` is where
        # that corner was) and turned about that corner
        self.cls: list[int] = []
        self.offset: list[tuple[float, float]] = []
        self.angles: list[list[float]] = []            # the angles of every shape, each look once
        self.skey: list[tuple] = []                    # shapes that are the same for placing
        self._grown: list[list[list[Pt]]] = []         # per class: its outline
        self._rooms: list[list[list[Pt]]] = []         # per class: the holes other parts may be placed in
        self._vcache: list[dict[float, _Variant]] = []  # per class: the outline at an angle, made when asked
        self._relieved_boxes: dict[tuple[int, float], _Box] = {}
        self.areas: list[float] = []                    # the material of a part: its outline minus its holes
        self.gross: list[float] = []                    # the area inside its outline
        classes: list[tuple[np.ndarray, float, np.ndarray]] = []  # per class: outline as drawn, area, holes as drawn
        for sh in self.shapes:
            outer = [p for p, _ in clip.polytree(sh.polys, "nonzero")] or sh.polys
            self.gross.append(sum(abs(polygon_area(p)) for p in outer))
            holes = [h for h in sh.holes if len(h) >= 3]
            hole_area = sum(abs(polygon_area(h)) for h in holes)
            self.areas.append(max(0.0, self.gross[-1] - hole_area))
            grown = _outline(outer, half, settings.tolerance, settings.max_points)
            if not grown:
                self.cls.append(-1)
                self.offset.append((0.0, 0.0))
                self.angles.append([])
                self.skey.append(())
                continue
            # the part as drawn decides what is the same (copies drawn anywhere, with any start point)
            mx = min(x for p in outer for x, _ in p)
            my = min(y for p in outer for _, y in p)
            pts = np.array([(x - mx, y - my) for p in outer for x, y in p])
            hpts = np.array([(x - mx, y - my) for h in holes for x, y in h]).reshape(-1, 2)
            c = next((i for i, (q, area, hq) in enumerate(classes)
                      if len(q) == len(pts) and abs(area - self.areas[-1]) <= 1e-4 * area + 1e-6
                      and np.allclose(q.max(axis=0), pts.max(axis=0), atol=0.05) and _same_outline(pts, q)
                      and len(hq) == len(hpts) and (not len(hq) or _same_outline(hpts, hq))), None)
            if c is None:
                c = len(classes)
                classes.append((pts, self.areas[-1], hpts))
                self._grown.append([[(x - mx, y - my) for x, y in p] for p in grown])
                self._rooms.append(_rooms([[(x - mx, y - my) for x, y in h] for h in holes], half,
                                          settings.tolerance))
                self._vcache.append({})
            self.cls.append(c)
            self.offset.append((mx, my))
            seen, keep = set(), []
            for ang in (sh.rotations if sh.rotations is not None else settings.rotations):
                a = round(float(ang) % 360.0, 6)
                v = self._variant(c, a)
                if a not in keep and v.sig not in seen:
                    seen.add(v.sig)
                    keep.append(a)
            self.angles.append(keep)
            self.skey.append((c, tuple(keep), sh.tilt))
        self._nfp_cache: dict[tuple, list[IntPath]] = {}
        self._nfp0_cache: dict[tuple, list[IntPath]] = {}
        self.stats = {"nfp": 0, "overlap_tests": 0}  # work done: no-fit polygons made, overlaps tested
        self.cancel: Callable[[], bool] | None = None
        self.gravity = settings.gravity
        self._feasible_cache: dict[tuple, tuple[list[IntPath], tuple] | None] = {}

    # -- geometry ----------------------------------------------------------------------
    def _nfp(self, a: int, aa: float, b: int, ba: float) -> list[IntPath]:
        """No-fit polygon of shape ``b`` at angle ``ba`` against shape ``a`` at angle ``aa``: where the
        reference of b may not be put. It only depends on the angle between the two, and turning the pair
        turns it, so one is computed per pair of shapes and angle between them and the others are turned
        copies of it (a reflected one for the pair the other way round)."""
        key = (a, aa, b, ba)
        hit = self._nfp_cache.get(key)
        if hit is not None:
            return hit
        if a <= b:
            res = _turn(self._nfp0(a, b, round((ba - aa) % 360.0, 6)), aa)
        else:  # NFP(A, B) = -NFP(B, A)
            res = _turn(self._nfp0(b, a, round((aa - ba) % 360.0, 6)), ba + 180.0)
        self._nfp_cache[key] = res
        return res

    def _nfp0(self, a: int, b: int, rel: float) -> list[IntPath]:
        """NFP of shape ``b`` turned by ``rel`` against shape ``a`` (not turned)."""
        key = (a, b, rel)
        hit = self._nfp0_cache.get(key)
        if hit is not None:
            return hit
        self._check()
        self.stats["nfp"] += 1
        VA, VB = self._variant(a, 0.0), self._variant(b, rel)
        A, B = VA.paths, VB.paths
        if len(A) == 1 and len(B) == 1 and VA.convex[0] and VB.convex[0]:
            res = [convex_minkowski(VA.convex[0], [(-x, -y) for x, y in VB.convex[0]])]
        elif VA.pieces is not None and VB.pieces is not None:
            # Minkowski sums distribute over unions: combine the convex pieces
            pc = pyclipper.Pyclipper()
            negs = [[(-x, -y) for x, y in pb] for pb in VB.pieces]
            for pa in VA.pieces:
                for nb in negs:
                    pc.AddPath(convex_minkowski(pa, nb), pyclipper.PT_SUBJECT, True)
            res = pc.Execute(pyclipper.CT_UNION, pyclipper.PFT_NONZERO, pyclipper.PFT_NONZERO)
        else:
            pc = pyclipper.Pyclipper()
            added = False
            for pa in A:
                for pb in B:
                    # pyclipper.MinkowskiDiff(p, q) = {q - p}: the NFP {a - b} is (pb, pa).  The
                    # result only covers the band swept along the boundary, so the interior is
                    # filled with A shifted by one point of B.
                    for r in pyclipper.MinkowskiDiff(pb, pa):
                        if len(r) >= 3:
                            pc.AddPath(r, pyclipper.PT_SUBJECT, True)
                            added = True
                    bx, by = pb[0]
                    pc.AddPath([(x - bx, y - by) for x, y in pa], pyclipper.PT_SUBJECT, True)
            res = pc.Execute(pyclipper.CT_UNION, pyclipper.PFT_NONZERO, pyclipper.PFT_NONZERO) if added else []
        if VA.holes or VB.holes:
            res = list(res) + self._room_terms(VA, VB)
        self._nfp0_cache[key] = res
        return res

    def _room_terms(self, VA: _Variant, VB: _Variant) -> list[IntPath]:
        """Where the no-fit polygon of two parts does not apply: when one lies inside a hole of the other.
        They are clockwise paths, which cancel the (counter-clockwise) no-fit polygon where they lie."""
        terms: list[IntPath] = []
        wb, hb = VB.bbox[2] - VB.bbox[0], VB.bbox[3] - VB.bbox[1]
        for h in VA.holes:  # B inside a hole of A
            xs, ys = [q[0] for q in h], [q[1] for q in h]
            if max(xs) - min(xs) >= wb and max(ys) - min(ys) >= hb:
                terms += [list(reversed(p)) for p in self._ifp_paths([h], VB)]
        wa, ha = VA.bbox[2] - VA.bbox[0], VA.bbox[3] - VA.bbox[1]
        for h in VB.holes:  # A inside a hole of B: B's reference is where A's would be, mirrored
            xs, ys = [q[0] for q in h], [q[1] for q in h]
            if max(xs) - min(xs) >= wa and max(ys) - min(ys) >= ha:
                terms += [list(reversed([(-x, -y) for x, y in p])) for p in self._ifp_paths([h], VA)]
        return terms

    def _check(self) -> None:
        if self.cancel is not None and self.cancel():
            raise NestCancelled()

    def _variant(self, c: int, ang: float) -> _Variant:
        """The outline of a class at an angle, made when first asked for."""
        ang = round(ang % 360.0, 6)
        v = self._vcache[c].get(ang)
        if v is None:
            v = self._vcache[c][ang] = _Variant(_rotate_int(self._grown[c], ang), _rotate_int(self._rooms[c], ang))
        return v

    def _relieved(self, box: _Box, relief: float) -> _Box:
        """The plate with parts allowed ``relief`` mm closer to its edge."""
        key = (box.index, relief)
        b = self._relieved_boxes.get(key)
        if b is None:
            b = self._relieved_boxes[key] = _Box(box.index, box.c, self.st.margin, self.st.spacing,
                                                 self.st.tolerance, relief)
        return b

    def _feasible(self, box: _Box, c: int, ang: float) -> tuple[list[IntPath], tuple] | None:
        """Where the reference of a class can be placed on an empty plate: ``(paths, bbox)`` or ``None``."""
        ang = round(ang % 360.0, 6)
        key = (box.index, box.relief, c, ang)
        if key in self._feasible_cache:
            return self._feasible_cache[key]
        res = self._compute_feasible(box, self._variant(c, ang))
        self._feasible_cache[key] = res
        return res

    def _compute_feasible(self, box: _Box, v: _Variant):
        if not box.usable:
            return None
        bx0, by0, bx1, by1 = v.bbox
        if box.rect is not None:
            ux0, uy0, ux1, uy1 = box.rect
            x0, x1, y0, y1 = ux0 - bx0, ux1 - bx1, uy0 - by0, uy1 - by1
            if x1 < x0 or y1 < y0:
                return None
            if x1 == x0:
                x1 += 1
            if y1 == y0:
                y1 += 1
            ifp = [(x0, y0), (x1, y0), (x1, y1), (x0, y1)]
            return [ifp], (x0, y0, x1, y1)
        cx0, cy0, cx1, cy1 = box.bbox
        if bx1 - bx0 > cx1 - cx0 or by1 - by0 > cy1 - cy0:
            return None
        feas = self._ifp_paths(box.paths, v)
        if not feas:
            return None
        xs = [q[0] for p in feas for q in p]
        ys = [q[1] for p in feas for q in p]
        return feas, (min(xs), min(ys), max(xs), max(ys))

    @staticmethod
    def _ifp_paths(region: list[IntPath], v: _Variant) -> list[IntPath]:
        """Where the reference of a part can be put so that the part lies inside ``region`` (counter-clockwise
        outlines, holes clockwise): the inner fit polygon, as paths."""
        # The part can be anywhere inside the region R except where it touches R's boundary: the band of
        # the boundary swept by the part, plus the reference point of every outline must lie in R (the part
        # is then inside, not in a hole or outside).
        band = pyclipper.Pyclipper()
        for pa in v.paths:
            for rb in region:
                for r in pyclipper.MinkowskiDiff(pa, rb):  # {rb - pa}
                    if len(r) >= 3:
                        band.AddPath(r, pyclipper.PT_SUBJECT, True)
        banned = band.Execute(pyclipper.CT_UNION, pyclipper.PFT_NONZERO, pyclipper.PFT_NONZERO)
        inside = None
        for pa in v.paths:
            px, py = pa[0]
            moved = [[(x - px, y - py) for x, y in rb] for rb in region]
            if inside is None:
                inside = moved
            else:
                pc = pyclipper.Pyclipper()
                pc.AddPaths(inside, pyclipper.PT_SUBJECT, True)
                pc.AddPaths(moved, pyclipper.PT_CLIP, True)
                inside = pc.Execute(pyclipper.CT_INTERSECTION, pyclipper.PFT_NONZERO, pyclipper.PFT_NONZERO)
        pc = pyclipper.Pyclipper()
        pc.AddPaths(inside or [], pyclipper.PT_SUBJECT, True)
        if banned:
            pc.AddPaths(banned, pyclipper.PT_CLIP, True)
        feas = pc.Execute(pyclipper.CT_DIFFERENCE, pyclipper.PFT_NONZERO, pyclipper.PFT_NONZERO)
        return [p for p in feas if len(p) >= 3]

    def _keys(self, V: np.ndarray, v: _Variant, used_bbox) -> tuple[np.ndarray, np.ndarray]:
        """Score (primary, secondary: smaller is better) of each position (n x 2) of a part, by the gravity.

        It is where the part ends up that counts (the lower left of its bounding box), not where its
        reference is, so that angles can be compared; equal ones keep the first angle.

        * ``left`` / ``bottom``: the leftmost / lowest place
        * ``compact``: the place that makes the rectangle round all the parts smallest
        * ``reach_x`` / ``reach_y``: the place that makes the layout least longer (wider), then the lowest / leftmost
        * ``diag``: the place nearest the lower left corner (x + y)"""
        g = self.gravity
        bx0, by0, bx1, by1 = v.bbox
        x, y = V[:, 0], V[:, 1]
        if g == "bottom":
            return y + by0, x + bx0
        if g == "compact":
            if used_bbox is None:
                w = np.full(len(V), bx1 - bx0, float)
                h = np.full(len(V), by1 - by0, float)
            else:
                w = np.maximum(used_bbox[2], x + bx1) - np.minimum(used_bbox[0], x + bx0)
                h = np.maximum(used_bbox[3], y + by1) - np.minimum(used_bbox[1], y + by0)
            return w * h + (w + h) * 1e3, x + y
        if g == "reach_x":
            far = used_bbox[2] if used_bbox is not None else x + bx1
            return np.maximum(far, x + bx1), (y + by0) + 1e-6 * (x + bx0)
        if g == "reach_y":
            far = used_bbox[3] if used_bbox is not None else y + by1
            return np.maximum(far, y + by1), (x + bx0) + 1e-6 * (y + by0)
        if g == "diag":
            return (x + bx0) + (y + by0), x + bx0
        return x + bx0, y + by0

    # a part that sticks out of the plate by less than this (mm^2, in units) is inside it: rounding
    _OUTSIDE_TOL = 0.05 * clip.SCALE ** 2

    def _inside(self, box: _Box, v: _Variant, x: int, y: int) -> bool:
        """Whether the part at ``(x, y)`` lies inside the usable area of a plate of any shape.

        The feasible area has zero width slivers where a part only touches the plate's boundary with its
        reference point (entirely outside, or in a hole): their corners must not be taken."""
        pc = pyclipper.Pyclipper()
        pc.AddPaths([[(px + x, py + y) for px, py in path] for path in v.paths], pyclipper.PT_SUBJECT, True)
        pc.AddPaths(box.paths, pyclipper.PT_CLIP, True)
        out = pc.Execute(pyclipper.CT_DIFFERENCE, pyclipper.PFT_NONZERO, pyclipper.PFT_NONZERO)
        return sum(abs(pyclipper.Area(path)) for path in out) <= self._OUTSIDE_TOL

    # two parts overlap when more than this (mm^2, in units) is common: touching, as at the edge of a
    # no-fit polygon, is rounding
    _OVERLAP_TOL = 0.1 * clip.SCALE ** 2

    def _overlaps(self, other: tuple[int, float, int, int], c: int, ang: float, x: int, y: int) -> bool:
        """Whether the part at ``(x, y)`` overlaps a placed one ``(class, angle, x, y)``."""
        pi, pa, px, py = other
        VA, VB = self._variant(pi, pa), self._variant(c, ang)
        ax0, ay0, ax1, ay1 = VA.bbox
        bx0, by0, bx1, by1 = VB.bbox
        if px + ax1 <= x + bx0 or x + bx1 <= px + ax0 or py + ay1 <= y + by0 or y + by1 <= py + ay0:
            return False
        self.stats["overlap_tests"] += 1
        pc = pyclipper.Pyclipper()
        pc.AddPaths([[(qx + px, qy + py) for qx, qy in path] for path in VA.solid], pyclipper.PT_SUBJECT, True)
        pc.AddPaths([[(qx + x, qy + y) for qx, qy in path] for path in VB.solid], pyclipper.PT_CLIP, True)
        out = pc.Execute(pyclipper.CT_INTERSECTION, pyclipper.PFT_NONZERO, pyclipper.PFT_NONZERO)
        return sum(abs(pyclipper.Area(path)) for path in out) > self._OVERLAP_TOL

    def _cheap(self, a: int, aa: float, b: int, ba: float) -> bool:
        """Whether the no-fit polygon of two placed shapes is there or quick to make (few convex pieces)."""
        if (a, aa, b, ba) in self._nfp_cache:
            return True
        pa, pb = self._variant(a, aa).pieces, self._variant(b, ba).pieces
        return pa is not None and pb is not None and len(pa) * len(pb) <= 8

    def _best_at_angle(self, box: _Box, placed: list[tuple[int, float, int, int]], c: int, ang: float,
                       used_bbox):
        """The best position of a part at one angle, ``(score, angle, x, y)``, or ``None``.

        The feasible area is the plate minus the no-fit polygons of the parts placed, but only the
        ones that matter are made: the best position of the area so far is tried against all placed
        parts, and the ones it overlaps are added (their no-fit polygons) until it overlaps none. That
        position is the best of the full area too, which is a part of the first."""
        v = self._variant(c, ang)
        base = self._feasible(box, c, ang)
        if base is None:
            return None
        ifp, (ix0, iy0, ix1, iy1) = base
        # the no-fit polygons that cost nothing are in from the start, the others only when needed
        included: set[int] = {j for j, (pi, pa, _, _) in enumerate(placed) if self._cheap(pi, pa, c, ang)}
        off_plate: set[tuple[int, int]] = set()
        while True:
            self._check()
            feas = ifp
            if included:
                pc = pyclipper.Pyclipper()
                n_added = 0
                for j in included:
                    pi, pa, px, py = placed[j]
                    for path in self._nfp(pi, pa, c, ang):
                        xs = [q[0] + px for q in path]
                        ys = [q[1] + py for q in path]
                        if max(xs) < ix0 or min(xs) > ix1 or max(ys) < iy0 or min(ys) > iy1:
                            continue
                        pc.AddPath([(q[0] + px, q[1] + py) for q in path], pyclipper.PT_CLIP, True)
                        n_added += 1
                if n_added:
                    pc.AddPaths(ifp, pyclipper.PT_SUBJECT, True)
                    feas = pc.Execute(pyclipper.CT_DIFFERENCE, pyclipper.PFT_NONZERO, pyclipper.PFT_NONZERO)
            V = np.array([q for poly in feas for q in poly], dtype=np.int64).reshape(-1, 2)
            if not len(V):
                return None
            k1, k2 = self._keys(V, v, used_bbox)
            conflicts: list[int] = []
            for i in np.lexsort((k2, k1)):  # best first
                x, y = int(V[i, 0]), int(V[i, 1])
                if (x, y) in off_plate:
                    continue
                if box.rect is None and not self._inside(box, v, x, y):
                    off_plate.add((x, y))
                    continue
                conflicts = [j for j in range(len(placed)) if j not in included
                             and self._overlaps(placed[j], c, ang, x, y)]
                if not conflicts:
                    return ((k1[i].item(), k2[i].item()), ang, x, y)
                break
            if not conflicts:
                return None  # no position is on the plate
            included.update(conflicts)

    def _try_sheet(self, box: _Box, placed: list[tuple[int, float, int, int]], si: int, used_bbox,
                   angles=None):
        """The best position of a part on a plate over its angles: ``(score, angle, x, y)`` or ``None``."""
        best = None
        c = self.cls[si]
        for ang in (self.angles[si] if angles is None else angles):
            cand = self._best_at_angle(box, placed, c, ang, used_bbox)
            if cand is not None and (best is None or cand[0] < best[0]):
                best = cand
        return best

    # -- main loop -----------------------------------------------------------------------
    def _plates(self) -> list[_Box]:
        """The containers in the order they are filled."""
        boxes = [b for b in self.boxes if b.usable]
        if self.st.stock_order == "listed":
            return boxes
        return sorted(boxes, key=lambda b: (0 if b.c.leftover else 1, b.free, b.index))

    def run_order(self, order: list[tuple[int, int]], progress: Callable | None = None,
                  cancel: Callable[[], bool] | None = None, squeeze: bool = False,
                  gravity: str | None = None) -> NestResult:
        """Place the items in this order; ``squeeze``: parts that do not fit are tilted a little or come
        closer to the plate's edge (the NestSettings.squeeze_* limits) before the next plate is started."""
        res = NestResult()
        self.cancel = cancel
        self.gravity = gravity or self.st.gravity
        remaining = [it for it in order if self.angles[it[0]]]
        res.unplaced = [it for it in order if not self.angles[it[0]]]
        total = len(order)
        for box in self._plates():
            unit = 0
            while remaining and res.sheets < self.st.max_sheets \
                    and (box.c.quantity <= 0 or unit < box.c.quantity):
                n = self._fill(box, remaining, res, progress, total, squeeze)
                if n == 0:
                    break  # nothing fits on a plate like this: the next ones would not do better
                res.stock.append(box.index)
                res.units.append(unit)
                res.sheets += 1
                unit += 1
            if not remaining:
                break
        res.unplaced += remaining
        for i in range(res.sheets):
            a = sum(self.areas[p.shape] for p in res.placements if p.sheet == i)
            free = self.boxes[res.stock[i]].free
            res.utilization.append(a / free if free > 0 else 0.0)
        return res

    def _squeeze_levels(self, si: int) -> list[tuple[float, float]]:
        """``(tilt, relief)`` to try for a part that does not fit, from the least to the most: tilting by
        one step, two steps, ..., then coming closer to the edge in four steps, then both."""
        st = self.st
        tilts = []
        if st.squeeze_angle > 0 and self.shapes[si].tilt and st.squeeze_step > 0:
            k = 1
            while k * st.squeeze_step <= st.squeeze_angle + 1e-9:
                tilts.append(k * st.squeeze_step)
                k += 1
        reliefs = [st.squeeze_margin * f for f in (0.25, 0.5, 0.75, 1.0)] if st.squeeze_margin > 0 else []
        levels = [(t, 0.0) for t in tilts] + [(0.0, r) for r in reliefs]
        if tilts and reliefs:
            levels += [(t, reliefs[-1]) for t in tilts]
        return levels

    def _squeeze(self, box: _Box, placed, si: int, used_bbox):
        """The gentlest way to squeeze a part onto the plate: ``(candidate, tilt, relief)`` or ``None``."""
        base = list(self.angles[si])
        for tilt, relief in self._squeeze_levels(si):
            b = self._relieved(box, relief) if relief > 0 else box
            if b.relief == 0 and relief > 0:
                continue  # no border gap to take from
            angles = base if tilt == 0 else sorted({round(a + sg * tilt, 6) for a in base for sg in (1, -1)})
            c = self._try_sheet(b, placed, si, used_bbox, angles)
            if c is not None:
                dev = min((((c[1] - a + 180.0) % 360.0) - 180.0 for a in base), key=abs)
                return c, round(dev, 6), b.relief
        return None

    def _fill(self, box: _Box, remaining: list[tuple[int, int]], res: NestResult, progress,
              total: int, squeeze: bool = False) -> int:
        """Place as many of ``remaining`` (largest first) on a new plate of ``box`` as fit; their count."""
        placed: list[tuple[int, float, int, int]] = []
        items: list[tuple] = []   # (shape, copy, class, angle, x, y) of what is placed
        used_bbox = None
        free = box.free
        sheet = res.sheets
        left = []
        dead: set[tuple] = set()  # shapes that do not fit any more: placing more parts only takes room away
        tight: set[tuple] = set()  # ... not without squeezing
        for k, item in enumerate(remaining):
            self._check()
            si, ci = item
            key = self.skey[si]
            if self.areas[si] > free + 1e-6 or key in dead:
                left.append(item)
                continue
            if progress:
                progress(total - len(remaining) + len(placed), total)
            squeezed = None
            b = None if key in tight else self._try_sheet(box, placed, si, used_bbox)
            if b is None:
                tight.add(key)
                if squeeze:
                    sq = self._squeeze(box, placed, si, used_bbox)
                    if sq is not None:
                        b, tilt, relief = sq
                        squeezed = Squeeze(si, ci, sheet, tilt, relief)
                if b is None:
                    dead.add(key)
                    left.append(item)
                    continue
            _, ang, x, y = b
            if squeezed is not None:
                res.squeezed.append(squeezed)
            c = self.cls[si]
            placed.append((c, ang, x, y))
            items.append((si, ci, c, ang, x, y))
            free -= self.areas[si]
            bx0, by0, bx1, by1 = self._variant(c, ang).bbox
            nb = (x + bx0, y + by0, x + bx1, y + by1)
            used_bbox = nb if used_bbox is None else (min(used_bbox[0], nb[0]), min(used_bbox[1], nb[1]),
                                                      max(used_bbox[2], nb[2]), max(used_bbox[3], nb[3]))
            res.placements.append(self._placement(si, ci, sheet, ang, x, y))
        remaining[:] = left
        if placed:
            res.used.append(tuple(v / clip.SCALE for v in used_bbox))
            res.layout.append(items)
        return len(placed)

    def _placement(self, si: int, ci: int, sheet: int, ang: float, x: int, y: int) -> Placement:
        """The placement of part ``si`` at ``(x, y)`` (units) turned by ``ang``: the outline was turned about the
        corner of its bounding box, the part about its origin."""
        mx, my = self.offset[si]
        ca, sa = math.cos(math.radians(ang)), math.sin(math.radians(ang))
        return Placement(si, ci, sheet, ang, x / clip.SCALE - (mx * ca - my * sa),
                         y / clip.SCALE - (mx * sa + my * ca))

    def default_order(self) -> list[tuple[int, int]]:
        return self.order_by("area")

    def order_by(self, how: str) -> list[tuple[int, int]]:
        """Every copy of every shape, in the order they are placed: biggest first by ``how`` (area, longest
        side, height, width, bounding box perimeter). Copies of a shape stay together."""
        S = clip.SCALE

        def size(i: int) -> tuple[float, float]:
            if self.cls[i] < 0 or not self.angles[i]:
                return 0.0, 0.0
            x0, y0, x1, y1 = self._variant(self.cls[i], self.angles[i][0]).bbox
            return (x1 - x0) / S, (y1 - y0) / S

        def key(i: int) -> tuple:
            w, h = size(i)
            major = {"area": 0.0, "side": max(w, h), "height": h, "width": w, "perimeter": w + h}[how]
            return (-round(major, 3), -self.gross[i], i)

        items = [(i, c) for i, sh in enumerate(self.shapes) for c in range(max(0, sh.quantity))]
        items.sort(key=lambda t: (key(t[0]), t[1]))
        return items

    def candidates(self, count: int) -> list[tuple[list[tuple[int, int]], str]]:
        """Up to ``count`` layouts to try, ``(order, gravity)``, the plainest first: the biggest parts first
        in the gravity chosen, then in the other gravities, then other ways to rank the parts."""
        g0 = self.st.gravity
        gravs = [g0] + [g for g in ("reach_x", "left", "reach_y", "bottom", "compact") if g != g0]
        plan = [("area", g) for g in gravs] + [("side", g) for g in gravs] + [
            ("height", gravs[0]), ("width", gravs[0]), ("perimeter", gravs[0]), ("height", gravs[1]),
            ("width", gravs[1]), ("perimeter", gravs[1])]
        out, seen = [], set()
        for how, g in plan:
            if len(out) >= max(1, count):
                break
            order = self.order_by(how)
            sig = (tuple(order), g)
            if sig not in seen:
                seen.add(sig)
                out.append((order, g))
        return out

    def quality(self, r: NestResult) -> tuple:
        """What is better: everything placed, then fewer (cheaper) plates, then the least area used on the
        last plate (the rectangle round the parts on it), then the shorter reach."""
        cost = sum(self.boxes[i].material for i in r.stock if not self.boxes[i].c.leftover)
        if r.used:
            x0, y0, x1, y1 = r.used[-1]
            area, reach = (x1 - x0) * (y1 - y0), x1 + y1
        else:
            area = reach = 0.0
        return (len(r.unplaced), cost, r.sheets, -sum(r.utilization[:-1]) if r.sheets > 1 else 0.0,
                round(area, 1), round(reach, 1))

    # -- polishing ----------------------------------------------------------------------
    def _bbox_of(self, items: list[tuple]):
        bb = None
        for _, _, c, ang, x, y in items:
            b0 = self._variant(c, ang).bbox
            nb = (x + b0[0], y + b0[1], x + b0[2], y + b0[3])
            bb = nb if bb is None else (min(bb[0], nb[0]), min(bb[1], nb[1]), max(bb[2], nb[2]), max(bb[3], nb[3]))
        return bb

    def polish(self, res: NestResult, trials: int, rng: random.Random, deadline: float | None = None) -> int:
        """Shrink the area used on the last plate: parts that set its extent (and some others) are taken out and
        put back where they fit best, the others staying where they are. A move is kept when the
        rectangle round all the parts gets smaller. Returns the number of moves that were kept."""
        if not res.layout or not res.used:
            return 0
        k = len(res.layout) - 1
        box = self.boxes[res.stock[k]]
        items = list(res.layout[k])
        if len(items) < 2:
            return 0

        def cost(bb):
            return ((bb[2] - bb[0]) * (bb[3] - bb[1]), bb[2] + bb[3])
        cur = cost(self._bbox_of(items))
        saved, kept = self.gravity, 0
        self.gravity = "compact"  # put a part where the rectangle round everything grows least
        try:
            for t in range(trials):
                self._check()
                if deadline is not None and time.perf_counter() > deadline:
                    break
                bb = self._bbox_of(items)
                tol = 1e-6 * self.st.sheet_width * clip.SCALE
                edge = [i for i, (_, _, c, ang, x, y) in enumerate(items)
                        if (lambda b: x + b[2] >= bb[2] - tol or y + b[3] >= bb[3] - tol)(self._variant(c, ang).bbox)]
                n_out = 1 if t % 4 else rng.choice([1, 2, 3])
                pool = edge if (edge and rng.random() < 0.75) else list(range(len(items)))
                out = set(rng.sample(pool, min(n_out, len(pool))))
                rest = [it for i, it in enumerate(items) if i not in out]
                taken = sorted((items[i] for i in out), key=lambda it: -self.areas[it[0]])
                new_rest = list(rest)
                ok = True
                for si, ci, c, ang, x, y in taken:
                    placed = [(c2, a2, x2, y2) for _, _, c2, a2, x2, y2 in new_rest]
                    ub = self._bbox_of(new_rest)
                    cand = self._try_sheet(box, placed, si, ub)
                    if cand is None:
                        ok = False
                        break
                    _, a, px, py = cand
                    new_rest.append((si, ci, self.cls[si], a, px, py))
                if not ok:
                    continue
                new = cost(self._bbox_of(new_rest))
                if new[0] < cur[0] * (1 - 1e-9) - 1e-6 or (abs(new[0] - cur[0]) <= 1e-6 * cur[0] and new[1] < cur[1] - 1):
                    items, cur, kept = new_rest, new, kept + 1
        finally:
            self.gravity = saved
        if kept:
            res.layout[k] = items
            res.used[k] = tuple(v / clip.SCALE for v in self._bbox_of(items))
            res.placements = [p for p in res.placements if p.sheet != k] + [
                self._placement(si, ci, k, ang, x, y) for si, ci, _, ang, x, y in items]
        return kept


def nest(shapes: Sequence[NestShape], settings: NestSettings, progress: Callable | None = None,
         cancel: Callable[[], bool] | None = None) -> NestResult:
    """Nest the shapes; ``cancel()`` is asked all the time and ``NestCancelled`` raised when it says yes.

    ``settings.iterations`` layouts are made and the one that uses the least material is kept: first the
    biggest parts first in the three gravities and a few other orders of the parts, then random changes
    of the best order; with more than one attempt the best layout is then polished (see
    :meth:`Nester.polish`). One attempt is the plain layout (biggest first, the gravity chosen).

    ``progress(done, total)`` counts the parts placed over all attempts."""
    n = Nester(shapes, settings)
    n.cancel = cancel
    squeezing = settings.squeeze_angle > 0 or settings.squeeze_margin > 0
    tries = max(1, settings.iterations)
    attempts = tries + (1 if squeezing else 0)
    plan = n.candidates(tries)
    count = sum(max(0, sh.quantity) for sh in shapes)

    def share(i: int):
        """Progress of attempt ``i`` as part of all of them."""
        return None if progress is None else (lambda k, t: progress(i * count + k, attempts * count))

    rng = random.Random(settings.seed)
    best = order = grav = None
    t0 = time.perf_counter()

    def out_of_time() -> bool:
        return settings.time_limit > 0 and time.perf_counter() - t0 > settings.time_limit
    for i in range(tries):
        n._check()
        if best is not None and out_of_time():
            break   # the layouts so far are what the time allowed
        if i < len(plan):
            cand, g = plan[i]
        else:  # a few swaps in the best order so far
            cand, g = list(order), grav
            if len(cand) < 2:
                break   # one part (or none) cannot be ordered any other way
            for _ in range(max(1, len(cand) // 4)):
                a = rng.randrange(len(cand))
                j = min(len(cand) - 1, a + rng.randint(1, 3))
                cand[a], cand[j] = cand[j], cand[a]
        r = n.run_order(cand, share(i), cancel, gravity=g)
        if best is None or n.quality(r) < n.quality(best):
            best, order, grav = r, cand, g
    if tries > 1 and not out_of_time():
        n.polish(best, 12 * tries, rng, None if settings.time_limit <= 0 else t0 + settings.time_limit)
    if squeezing and (best.unplaced or best.sheets > 1):
        # squeezing parts in may place what did not fit or save a plate: offered as a suggestion
        r = n.run_order(order, share(attempts - 1), cancel, squeeze=True, gravity=grav)
        if r.squeezed and n.quality(r)[:3] < n.quality(best)[:3]:
            best.suggestion = r
    return best

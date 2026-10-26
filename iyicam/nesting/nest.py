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
from dataclasses import dataclass, field
from typing import Any, Callable, Sequence

import pyclipper

from ..core import clip
from ..core.geom import Pt, polygon_area, simplify_polyline

IntPath = list[tuple[int, int]]


@dataclass
class NestShape:
    polys: list[list[Pt]]          # outline polygons (holes are ignored)
    quantity: int = 1
    payload: Any = None
    rotations: list[float] | None = None  # degrees; None = NestSettings.rotations
    tilt: bool = True              # may be tilted a little off its angles to squeeze it in (NestSettings.squeeze_*)


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
    iterations: int = 1            # randomised attempts, best result is kept
    seed: int = 1
    tolerance: float = 0.5         # outline simplification (parts get this much extra spacing at most)
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
    __slots__ = ("paths", "bbox", "area", "convex", "pieces")

    def __init__(self, paths: list[IntPath]):
        self.paths = paths
        xs = [p[0] for path in paths for p in path]
        ys = [p[1] for path in paths for p in path]
        self.bbox = (min(xs), min(ys), max(xs), max(ys))
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
        # is simplified towards the inside so the parts stay on the safe side of it
        ct = min(max(0.02, tol / 2), 0.1)
        usable = clip.offset(region, half - margin - 1.5 * ct, "round", arc_tol=ct / 2)
        usable = [q for q in (simplify_polyline(p + [p[0]], ct)[:-1] for p in usable) if len(q) >= 3]
        if c.obstacles:
            usable = clip.boolean(usable, clip.offset(clip.normalize(c.obstacles, "nonzero"), half), "difference")
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
        self.variants: list[dict[float, _Variant]] = []
        self.extra: list[dict[float, _Variant]] = []  # angles tried only when squeezing parts in
        self._grown: list[list] = []
        self._relieved_boxes: dict[tuple[int, float], _Box] = {}
        self.areas: list[float] = []
        for sh in self.shapes:
            outer = [p for p, _ in clip.polytree(sh.polys, "nonzero")] or sh.polys
            self.areas.append(sum(abs(polygon_area(p)) for p in outer))
            t = max(0.01, settings.tolerance)
            # round joins lie within t/2 and the simplification within t inside the true outline,
            # so growing by an extra 1.5 t keeps the simplified outline on the safe side
            grown = clip.offset(outer, half + 1.5 * t, "round", arc_tol=t / 2)
            grown = [q for q in (simplify_polyline(p + [p[0]], t)[:-1] for p in grown) if len(q) >= 3]
            grown = [p for p, _ in clip.polytree(grown, "nonzero")]
            vs = {}
            for ang in (sh.rotations if sh.rotations is not None else settings.rotations):
                if grown:
                    vs[float(ang)] = _Variant(_rotate_int(grown, ang))
            self.variants.append(vs)
            self.extra.append({})
            self._grown.append(grown)
        self._nfp_cache: dict[tuple, list[IntPath]] = {}
        self._feasible_cache: dict[tuple, tuple[list[IntPath], tuple] | None] = {}

    # -- geometry ----------------------------------------------------------------------
    def _nfp(self, a: int, aa: float, b: int, ba: float) -> list[IntPath]:
        key = (a, aa, b, ba)
        hit = self._nfp_cache.get(key)
        if hit is not None:
            return hit
        VA, VB = self._variant(a, aa), self._variant(b, ba)
        A, B = VA.paths, VB.paths
        if len(A) == 1 and len(B) == 1 and VA.convex[0] and VB.convex[0]:
            res = [convex_minkowski(VA.convex[0], [(-x, -y) for x, y in VB.convex[0]])]
            self._nfp_cache[key] = res
            return res
        if VA.pieces is not None and VB.pieces is not None:
            # Minkowski sums distribute over unions: combine the convex pieces
            pc = pyclipper.Pyclipper()
            negs = [[(-x, -y) for x, y in pb] for pb in VB.pieces]
            for pa in VA.pieces:
                for nb in negs:
                    pc.AddPath(convex_minkowski(pa, nb), pyclipper.PT_SUBJECT, True)
            res = pc.Execute(pyclipper.CT_UNION, pyclipper.PFT_NONZERO, pyclipper.PFT_NONZERO)
            self._nfp_cache[key] = res
            return res
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
        self._nfp_cache[key] = res
        return res

    def _variant(self, si: int, ang: float) -> _Variant:
        """The shape turned by ``ang``: one of its angles, or (when squeezing) any other, made when asked."""
        v = self.variants[si].get(ang) or self.extra[si].get(ang)
        if v is None:
            v = self.extra[si][ang] = _Variant(_rotate_int(self._grown[si], ang))
        return v

    def _relieved(self, box: _Box, relief: float) -> _Box:
        """The plate with parts allowed ``relief`` mm closer to its edge."""
        key = (box.index, relief)
        b = self._relieved_boxes.get(key)
        if b is None:
            b = self._relieved_boxes[key] = _Box(box.index, box.c, self.st.margin, self.st.spacing,
                                                 self.st.tolerance, relief)
        return b

    def _feasible(self, box: _Box, si: int, ang: float) -> tuple[list[IntPath], tuple] | None:
        """Where the part's reference can be placed on an empty plate: ``(paths, bbox)`` or ``None``."""
        key = (box.index, box.relief, si, ang)
        if key in self._feasible_cache:
            return self._feasible_cache[key]
        res = self._compute_feasible(box, self._variant(si, ang))
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
        # The part can be anywhere inside the plate's usable area R except where it touches R's
        # boundary: the band of the boundary swept by the part, plus the reference point of every
        # outline must lie in R (the part is then inside, not in a hole or outside).
        band = pyclipper.Pyclipper()
        for pa in v.paths:
            for rb in box.paths:
                for r in pyclipper.MinkowskiDiff(pa, rb):  # {rb - pa}
                    if len(r) >= 3:
                        band.AddPath(r, pyclipper.PT_SUBJECT, True)
        banned = band.Execute(pyclipper.CT_UNION, pyclipper.PFT_NONZERO, pyclipper.PFT_NONZERO)
        inside = None
        for pa in v.paths:
            px, py = pa[0]
            moved = [[(x - px, y - py) for x, y in rb] for rb in box.paths]
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
        feas = [p for p in feas if len(p) >= 3]
        if not feas:
            return None
        xs = [q[0] for p in feas for q in p]
        ys = [q[1] for p in feas for q in p]
        return feas, (min(xs), min(ys), max(xs), max(ys))

    def _score(self, x: int, y: int, v: _Variant, used_bbox) -> tuple:
        g = self.st.gravity
        if g == "bottom":
            return (y, x)
        if g == "compact":
            bx0, by0, bx1, by1 = v.bbox
            if used_bbox is None:
                w, h = bx1 - bx0, by1 - by0
            else:
                w = max(used_bbox[2], x + bx1) - min(used_bbox[0], x + bx0)
                h = max(used_bbox[3], y + by1) - min(used_bbox[1], y + by0)
            return (w * h + (w + h) * 1e3, x + y)
        return (x, y)

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

    def _try_sheet(self, box: _Box, placed: list[tuple[int, float, int, int]], si: int, used_bbox,
                   angles=None):
        best = None
        cands = []
        for ang in (self.variants[si] if angles is None else angles):
            v = self._variant(si, ang)
            base = self._feasible(box, si, ang)
            if base is None:
                continue
            ifp, (ix0, iy0, ix1, iy1) = base
            feas = ifp
            if placed:
                pc = pyclipper.Pyclipper()
                n_added = 0
                for (pi, pa, px, py) in placed:
                    nfp = self._nfp(pi, pa, si, ang)
                    for path in nfp:
                        xs = [q[0] + px for q in path]
                        ys = [q[1] + py for q in path]
                        if max(xs) < ix0 or min(xs) > ix1 or max(ys) < iy0 or min(ys) > iy1:
                            continue
                        pc.AddPath([(q[0] + px, q[1] + py) for q in path], pyclipper.PT_CLIP, True)
                        n_added += 1
                if n_added:
                    pc.AddPaths(ifp, pyclipper.PT_SUBJECT, True)
                    feas = pc.Execute(pyclipper.CT_DIFFERENCE, pyclipper.PFT_NONZERO, pyclipper.PFT_NONZERO)
            for poly in feas:
                for (x, y) in poly:
                    sc = self._score(x, y, v, used_bbox)
                    if box.rect is not None:
                        if best is None or sc < best[0]:
                            best = (sc, ang, x, y)
                    else:
                        cands.append((sc, ang, x, y))
        if box.rect is None:  # best first, the first one that is really on the plate
            cands.sort()
            for c in cands:
                if self._inside(box, self._variant(si, c[1]), c[2], c[3]):
                    return c
        return best

    # -- main loop -----------------------------------------------------------------------
    def _plates(self) -> list[_Box]:
        """The containers in the order they are filled."""
        boxes = [b for b in self.boxes if b.usable]
        if self.st.stock_order == "listed":
            return boxes
        return sorted(boxes, key=lambda b: (0 if b.c.leftover else 1, b.free, b.index))

    def run_order(self, order: list[tuple[int, int]], progress: Callable | None = None,
                  cancel: Callable[[], bool] | None = None, squeeze: bool = False) -> NestResult:
        """Place the items in this order; ``squeeze``: parts that do not fit are tilted a little or come
        closer to the plate's edge (the NestSettings.squeeze_* limits) before the next plate is started."""
        res = NestResult()
        remaining = [it for it in order if self.variants[it[0]]]
        res.unplaced = [it for it in order if not self.variants[it[0]]]
        total = len(order)
        stop = False
        for box in self._plates():
            unit = 0
            while remaining and not stop and res.sheets < self.st.max_sheets \
                    and (box.c.quantity <= 0 or unit < box.c.quantity):
                n = self._fill(box, remaining, res, progress, cancel, total, squeeze)
                if n < 0:
                    stop = True
                    break
                if n == 0:
                    break  # nothing fits on a plate like this: the next ones would not do better
                res.stock.append(box.index)
                res.units.append(unit)
                res.sheets += 1
                unit += 1
            if stop or not remaining:
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
        base = list(self.variants[si])
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

    def _fill(self, box: _Box, remaining: list[tuple[int, int]], res: NestResult, progress, cancel,
              total: int, squeeze: bool = False) -> int:
        """Place as many of ``remaining`` (largest first) on a new plate of ``box`` as fit; their count
        (-1: cancelled)."""
        placed: list[tuple[int, float, int, int]] = []
        used_bbox = None
        free = box.free
        sheet = res.sheets
        left = []
        dead: set[int] = set()  # shapes that do not fit any more: placing more parts only takes room away
        tight: set[int] = set()  # ... not without squeezing
        for k, item in enumerate(remaining):
            if cancel and cancel():
                remaining[:] = left + remaining[k:]
                return -1
            si, ci = item
            if self.areas[si] > free + 1e-6 or si in dead:
                left.append(item)
                continue
            if progress:
                progress(total - len(remaining) + len(placed), total)
            squeezed = None
            b = None if si in tight else self._try_sheet(box, placed, si, used_bbox)
            if b is None:
                tight.add(si)
                if squeeze:
                    sq = self._squeeze(box, placed, si, used_bbox)
                    if sq is not None:
                        b, tilt, relief = sq
                        squeezed = Squeeze(si, ci, sheet, tilt, relief)
                if b is None:
                    dead.add(si)
                    left.append(item)
                    continue
            _, ang, x, y = b
            if squeezed is not None:
                res.squeezed.append(squeezed)
            placed.append((si, ang, x, y))
            free -= self.areas[si]
            bx0, by0, bx1, by1 = self._variant(si, ang).bbox
            nb = (x + bx0, y + by0, x + bx1, y + by1)
            used_bbox = nb if used_bbox is None else (min(used_bbox[0], nb[0]), min(used_bbox[1], nb[1]),
                                                      max(used_bbox[2], nb[2]), max(used_bbox[3], nb[3]))
            res.placements.append(Placement(si, ci, sheet, ang, x / clip.SCALE, y / clip.SCALE))
        remaining[:] = left
        return len(placed)

    def default_order(self) -> list[tuple[int, int]]:
        items = [(i, c) for i, sh in enumerate(self.shapes) for c in range(max(0, sh.quantity))]
        items.sort(key=lambda t: -self.areas[t[0]])
        return items

    def quality(self, r: NestResult) -> tuple:
        last = [p for p in r.placements if p.sheet == r.sheets - 1]
        if self.st.gravity == "bottom":
            extent = max((p.y for p in last), default=0.0)
        else:
            extent = max((p.x for p in last), default=0.0)
        cost = sum(self.boxes[i].material for i in r.stock if not self.boxes[i].c.leftover)
        return (len(r.unplaced), cost, r.sheets, -sum(r.utilization[:-1]) if r.sheets > 1 else 0.0, extent)


def nest(shapes: Sequence[NestShape], settings: NestSettings, progress: Callable | None = None,
         cancel: Callable[[], bool] | None = None) -> NestResult:
    n = Nester(shapes, settings)
    order = n.default_order()
    best = n.run_order(order, progress, cancel)
    rng = random.Random(settings.seed)
    for _ in range(max(0, settings.iterations - 1)):
        if cancel and cancel():
            break
        cand = list(order)
        for _ in range(max(1, len(cand) // 4)):
            i = rng.randrange(len(cand))
            j = min(len(cand) - 1, i + rng.randint(1, 3))
            cand[i], cand[j] = cand[j], cand[i]
        r = n.run_order(cand)
        if n.quality(r) < n.quality(best):
            best, order = r, cand
    if (settings.squeeze_angle > 0 or settings.squeeze_margin > 0) and (best.unplaced or best.sheets > 1) \
            and not (cancel and cancel()):
        # squeezing parts in may place what did not fit or save a plate: offered as a suggestion
        r = n.run_order(order, None, cancel, squeeze=True)
        if r.squeezed and n.quality(r)[:3] < n.quality(best)[:3]:
            best.suggestion = r
    return best

"""True shape nesting with no-fit polygons.

Every part is inflated by half the spacing, then placed one by one: the
feasible positions on a sheet are the inner-fit rectangle minus the union of
the no-fit polygons (Minkowski differences) of the parts already placed.  The
best vertex of that region is chosen with a gravity rule.  Several randomised
orders can be tried and the best layout is kept.
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


@dataclass
class NestSettings:
    sheet_width: float = 2800.0
    sheet_height: float = 2100.0
    margin: float = 10.0
    spacing: float = 8.0
    rotations: list[float] = field(default_factory=lambda: [0.0, 90.0, 180.0, 270.0])
    gravity: str = "left"          # left | bottom | compact
    max_sheets: int = 100
    iterations: int = 1            # randomised attempts, best result is kept
    seed: int = 1
    tolerance: float = 0.5         # outline simplification (parts get this much extra spacing at most)


@dataclass
class Placement:
    shape: int
    copy: int
    sheet: int
    angle: float   # degrees, rotation about the shape's own coordinate origin
    x: float       # translation applied after the rotation
    y: float


@dataclass
class NestResult:
    placements: list[Placement] = field(default_factory=list)
    sheets: int = 0
    unplaced: list[tuple[int, int]] = field(default_factory=list)
    utilization: list[float] = field(default_factory=list)


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


class Nester:
    def __init__(self, shapes: Sequence[NestShape], settings: NestSettings):
        self.shapes = list(shapes)
        self.st = settings
        S = clip.SCALE
        half = settings.spacing / 2.0
        self.ux0 = round((settings.margin - half) * S)
        self.uy0 = round((settings.margin - half) * S)
        self.ux1 = round((settings.sheet_width - settings.margin + half) * S)
        self.uy1 = round((settings.sheet_height - settings.margin + half) * S)
        self.variants: list[dict[float, _Variant]] = []
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
        self._nfp_cache: dict[tuple, list[IntPath]] = {}

    # -- geometry ----------------------------------------------------------------------
    def _nfp(self, a: int, aa: float, b: int, ba: float) -> list[IntPath]:
        key = (a, aa, b, ba)
        hit = self._nfp_cache.get(key)
        if hit is not None:
            return hit
        VA, VB = self.variants[a][aa], self.variants[b][ba]
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

    def _ifp(self, v: _Variant) -> IntPath | None:
        bx0, by0, bx1, by1 = v.bbox
        x0, x1 = self.ux0 - bx0, self.ux1 - bx1
        y0, y1 = self.uy0 - by0, self.uy1 - by1
        if x1 < x0 or y1 < y0:
            return None
        if x1 == x0:
            x1 += 1
        if y1 == y0:
            y1 += 1
        return [(x0, y0), (x1, y0), (x1, y1), (x0, y1)]

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

    def _try_sheet(self, placed: list[tuple[int, float, int, int]], si: int, used_bbox):
        best = None
        for ang, v in self.variants[si].items():
            ifp = self._ifp(v)
            if ifp is None:
                continue
            if placed:
                pc = pyclipper.Pyclipper()
                ix0, iy0 = ifp[0]
                ix1, iy1 = ifp[2]
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
                pc.AddPath(ifp, pyclipper.PT_SUBJECT, True)
                feas = pc.Execute(pyclipper.CT_DIFFERENCE, pyclipper.PFT_NONZERO, pyclipper.PFT_NONZERO) \
                    if n_added else [ifp]
            else:
                feas = [ifp]
            for poly in feas:
                for (x, y) in poly:
                    sc = self._score(x, y, v, used_bbox)
                    if best is None or sc < best[0]:
                        best = (sc, ang, x, y)
        return best

    # -- main loop -----------------------------------------------------------------------
    def run_order(self, order: list[tuple[int, int]], progress: Callable | None = None,
                  cancel: Callable[[], bool] | None = None) -> NestResult:
        res = NestResult()
        sheets: list[list[tuple[int, float, int, int]]] = []
        bboxes: list[tuple | None] = []
        total = len(order)
        for k, (si, ci) in enumerate(order):
            if cancel and cancel():
                break
            if progress:
                progress(k, total)
            if not self.variants[si]:
                res.unplaced.append((si, ci))
                continue
            done = False
            for sheet_i, placed in enumerate(sheets):
                b = self._try_sheet(placed, si, bboxes[sheet_i])
                if b is not None:
                    self._commit(res, sheets, bboxes, sheet_i, si, ci, b)
                    done = True
                    break
            if not done and len(sheets) < self.st.max_sheets:
                b = self._try_sheet([], si, None)
                if b is not None:
                    sheets.append([])
                    bboxes.append(None)
                    self._commit(res, sheets, bboxes, len(sheets) - 1, si, ci, b)
                    done = True
            if not done:
                res.unplaced.append((si, ci))
        res.sheets = len(sheets)
        sheet_area = self.st.sheet_width * self.st.sheet_height
        for i in range(len(sheets)):
            a = sum(self.areas[p.shape] for p in res.placements if p.sheet == i)
            res.utilization.append(a / sheet_area if sheet_area > 0 else 0.0)
        return res

    def _commit(self, res, sheets, bboxes, sheet_i, si, ci, best) -> None:
        _, ang, x, y = best
        sheets[sheet_i].append((si, ang, x, y))
        bx0, by0, bx1, by1 = self.variants[si][ang].bbox
        nb = (x + bx0, y + by0, x + bx1, y + by1)
        ob = bboxes[sheet_i]
        bboxes[sheet_i] = nb if ob is None else (min(ob[0], nb[0]), min(ob[1], nb[1]),
                                                  max(ob[2], nb[2]), max(ob[3], nb[3]))
        res.placements.append(Placement(si, ci, sheet_i, ang, x / clip.SCALE, y / clip.SCALE))

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
        return (len(r.unplaced), r.sheets, -sum(r.utilization[:-1]) if r.sheets > 1 else 0.0, extent)


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
    return best

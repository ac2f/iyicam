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
from ..core.geom import Pt, polygon_area

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
    tolerance: float = 0.2         # outline simplification


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


class _Variant:
    __slots__ = ("paths", "bbox", "area")

    def __init__(self, paths: list[IntPath]):
        self.paths = paths
        xs = [p[0] for path in paths for p in path]
        ys = [p[1] for path in paths for p in path]
        self.bbox = (min(xs), min(ys), max(xs), max(ys))
        self.area = sum(abs(pyclipper.Area(p)) for p in paths)


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
            grown = clip.offset(outer, half + settings.tolerance, "round", arc_tol=max(0.01, settings.tolerance / 2))
            grown = clip.simplify(grown, settings.tolerance) or grown
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
        A = self.variants[a][aa].paths
        B = self.variants[b][ba].paths
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

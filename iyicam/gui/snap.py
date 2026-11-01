"""Snapping: the pointer, or a selection that is dragged, clings to the points that matter.

What clings is decided by where the *feature* is, not by where the mouse is: dragging a part by the middle of
its edge still makes its corner click onto the corner of another part, to the exact coordinates. The points to cling to
are nodes, middles of segments, centres and quadrant points of arcs, crossings of vectors (and, if asked for,
any point on a vector), the corners, middles and centre of the sheet, guides and the grid. Where there is no
point close enough, a point that is level with a node (same X or same Y) is the next best thing, with a dotted
line to show it. The grid is rigid: with it on, the coordinates are on the grid, whatever the zoom.

The reach is in screen pixels (the same at every zoom), and a point that has caught the pointer lets go only a
little later than it caught it (so it does not flicker between two near points).
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np
from PySide6.QtCore import Qt
from PySide6.QtGui import QGuiApplication

from ..core.geom import Arc, BBox, Pt

# kind -> (what it is called, weight): the weight multiplies the distance when two points compete (less wins)
KINDS: dict[str, tuple[str, float]] = {
    "node": ("Node", 1.0), "intersection": ("Intersection", 1.0), "sheet": ("Sheet", 1.0),
    "centre": ("Centre", 1.05), "quadrant": ("Quadrant", 1.1), "mid": ("Midpoint", 1.1),
    "guide": ("Guide", 1.15), "align": ("Aligned", 1.25), "grid": ("Grid", 1.6), "edge": ("On the vector", 1.8),
}
_POINT_KINDS = ["node", "centre", "quadrant", "mid", "sheet"]      # what the target arrays hold
_CODE = {k: i for i, k in enumerate(_POINT_KINDS)}
_WEIGHT = np.array([KINDS[k][1] for k in _POINT_KINDS])
STICKY = 1.4          # a point that has caught lets go at this many times the reach
STICKY_BONUS = 0.75   # and counts as this much nearer than it is while it holds


def label(kind: str) -> str:
    return KINDS.get(kind, ("", 1.0))[0]


def modifier_free() -> bool:
    """Alt held: no snapping, the pointer is where it is."""
    return bool(QGuiApplication.keyboardModifiers() & Qt.AltModifier)


@dataclass
class Snap:
    """The outcome of a snap: ``point`` (the pointer) or ``delta`` (a move), what it clung to and how to show it."""
    point: Pt | None = None
    delta: Pt | None = None
    kind: str = ""                                  # "" when nothing was caught
    target: Pt | None = None                        # where the marker goes
    source: Pt | None = None                        # the feature of the moved selection that is caught
    lines: list[tuple[Pt, Pt]] = field(default_factory=list)      # dotted lines of alignments

    @property
    def caught(self) -> bool:
        return bool(self.kind)


class Targets:
    """The points of a drawing to cling to, as arrays (built once for every change of the drawing)."""

    def __init__(self, objects, sheet_box: BBox | None):
        pts: list[Pt] = []
        kinds: list[int] = []
        owners: list[int] = []
        self.ids: dict[str, int] = {}

        def add(p: Pt, kind: str, owner: int) -> None:
            pts.append(p)
            kinds.append(_CODE[kind])
            owners.append(owner)

        for o in objects:
            oi = self.ids.setdefault(o.id, len(self.ids))
            for c in o.contours:
                for k, seg in enumerate(c.segs):
                    add(seg.a, "node", oi)
                    if k == len(c.segs) - 1 and not c.closed:
                        add(seg.b, "node", oi)
                    add(seg.point_at(0.5), "mid", oi)
                    if isinstance(seg, Arc):
                        try:
                            centre = seg.center
                            r, sa, sw = seg.radius, seg.start_angle, seg.sweep
                        except (ValueError, ZeroDivisionError):
                            continue
                        add(centre, "centre", oi)
                        for q in range(4):
                            ang = q * math.pi / 2
                            rel = (ang - sa) % math.tau if sw >= 0 else (sa - ang) % math.tau
                            if 1e-9 < rel < abs(sw) - 1e-9:
                                add((centre[0] + r * math.cos(ang), centre[1] + r * math.sin(ang)), "quadrant", oi)
        if sheet_box is not None:
            x0, y0, x1, y1 = sheet_box.xmin, sheet_box.ymin, sheet_box.xmax, sheet_box.ymax
            for q in (*sheet_box.corners(), sheet_box.center, ((x0 + x1) / 2, y0), ((x0 + x1) / 2, y1),
                      (x0, (y0 + y1) / 2), (x1, (y0 + y1) / 2)):
                add(q, "sheet", -1)
        self.P = np.array(pts, float).reshape(-1, 2)
        self.K = np.array(kinds, np.int8)
        self.O = np.array(owners, np.int32)
        self._axes: dict[frozenset, tuple] = {}

    def keep(self, exclude) -> np.ndarray | None:
        """A mask of the targets that do not belong to the ``exclude`` d vectors (``None``: all of them)."""
        if not exclude or not len(self.P):
            return None
        gone = [self.ids[i] for i in exclude if i in self.ids]
        return None if not gone else ~np.isin(self.O, gone)

    def axes(self, exclude) -> tuple[np.ndarray, np.ndarray]:
        """The points that something can be level with (nodes, centres, quadrants, the sheet), sorted by X and by Y."""
        key = frozenset(exclude)
        hit = self._axes.get(key)
        if hit is None:
            ok = (self.K != _CODE["mid"])
            keep = self.keep(exclude)
            if keep is not None:
                ok &= keep
            pts = self.P[ok]
            hit = (pts[np.argsort(pts[:, 0], kind="stable")], pts[np.argsort(pts[:, 1], kind="stable")])
            if len(self._axes) > 6:
                self._axes.clear()
            self._axes[key] = hit
        return hit


def _nearest_level(sorted_pts: np.ndarray, column: int, values: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """For every value the point of ``sorted_pts`` (sorted by ``column``) whose coordinate is nearest, and how far."""
    n = len(sorted_pts)
    if n == 0:
        return np.full(len(values), np.inf), np.zeros((len(values), 2))
    col = sorted_pts[:, column]
    i = np.clip(np.searchsorted(col, values), 1, max(1, n - 1)) if n > 1 else np.zeros(len(values), int)
    lo = np.clip(i - 1, 0, n - 1)
    hi = np.clip(i, 0, n - 1)
    pick = np.where(np.abs(col[lo] - values) <= np.abs(col[hi] - values), lo, hi)
    return np.abs(col[pick] - values), sorted_pts[pick]


class Snapper:
    """Snapping for one drawing (a canvas)."""

    def __init__(self, canvas):
        self.canvas = canvas
        self._targets: Targets | None = None
        self._key = None
        self._stick = None            # (kind, target point[, source index]) of the point that holds the pointer
        self._drag_exclude: frozenset = frozenset()

    # -- the drawing ----------------------------------------------------------------------------------
    def invalidate(self) -> None:
        self._targets = None

    def targets(self) -> Targets:
        if self._targets is None:
            c = self.canvas
            sh = c.ctrl.sheet
            self._targets = Targets(c.visible_objects(), BBox(0, 0, sh.width, sh.height))
        return self._targets

    def release(self) -> None:
        """The pointer let go of what it held (a press, a release)."""
        self._stick = None

    # -- settings --------------------------------------------------------------------------------------
    def tol(self) -> float:
        """The reach in world units: a number of pixels, whatever the zoom."""
        return self.canvas.px(float(self.canvas.ctrl.settings.snap_radius))

    @property
    def s(self):
        return self.canvas.ctrl.settings

    # -- the pointer -----------------------------------------------------------------------------------
    def point(self, p: Pt, exclude=frozenset(), raw: bool = False) -> Snap:
        """Where the pointer at ``p`` is, clung to what is near."""
        if raw or modifier_free():
            self._stick = None
            return Snap(point=p)
        s, tol = self.s, self.tol()
        cands: list[tuple[float, float, Pt, str]] = []        # (weighted distance, distance, point, kind)
        if s.snap_objects:
            T = self.targets()
            P, K = T.P, T.K
            if len(P):
                m = (np.abs(P[:, 0] - p[0]) <= tol) & (np.abs(P[:, 1] - p[1]) <= tol)
                keep = T.keep(exclude)
                if keep is not None:
                    m &= keep
                for j in np.flatnonzero(m):
                    d = math.hypot(P[j, 0] - p[0], P[j, 1] - p[1])
                    if d <= tol:
                        kind = _POINT_KINDS[K[j]]
                        cands.append((d * KINDS[kind][1], d, (float(P[j, 0]), float(P[j, 1])), kind))
            for q, d in self._crossings(p, tol, exclude):
                cands.append((d * KINDS["intersection"][1], d, q, "intersection"))
            if s.snap_edges:
                for q, d in self._edges(p, tol, exclude):
                    cands.append((d * KINDS["edge"][1], d, q, "edge"))
        for q, d in self._guide_points(p, tol):
            cands.append((d * KINDS["guide"][1], d, q, "guide"))
        best = self._hold(cands, p, tol)
        if best is not None:
            self._stick = (best[3], best[2])
            return Snap(point=best[2], kind=best[3], target=best[2])
        # nothing to cling to: level with a node on one axis or both, and the grid (rigid) on the others
        x = y = None
        lines: list[tuple[Pt, Pt]] = []
        if s.snap_objects and s.snap_align:
            by_x, by_y = self.targets().axes(exclude)
            dx, qx = _nearest_level(by_x, 0, np.array([p[0]]))
            dy, qy = _nearest_level(by_y, 1, np.array([p[1]]))
            if dx[0] <= tol:
                x = float(qx[0][0])
            if dy[0] <= tol:
                y = float(qy[0][1])
            if x is not None:
                lines.append(((x, p[1]), (float(qx[0][0]), float(qx[0][1]))))
            if y is not None:
                lines.append(((p[0], y), (float(qy[0][0]), float(qy[0][1]))))
        aligned = x is not None or y is not None
        grid = s.snap_grid and s.grid > 0
        if grid:
            g = s.grid
            gx = round(p[0] / g) * g
            gy = round(p[1] / g) * g
            if x is None:
                x = gx
            if y is None:
                y = gy
        if x is None and y is None:
            self._stick = None
            return Snap(point=p)
        q = (p[0] if x is None else x, p[1] if y is None else y)
        self._stick = None
        return Snap(point=q, kind="align" if aligned else "grid", target=q, lines=lines)

    def _hold(self, cands, p: Pt, tol: float):
        """The best candidate, with the point that holds the pointer favoured (it lets go a little later)."""
        if self._stick is not None and ((self._stick[0] != "guide" and not self.s.snap_objects)
                                        or (self._stick[0] == "edge" and not self.s.snap_edges)):
            self._stick = None                    # (what held the pointer has been switched off)
        if self._stick is not None:
            kind, tp = self._stick[0], self._stick[1]
            d = math.hypot(tp[0] - p[0], tp[1] - p[1])
            if d <= tol * STICKY:
                cands = [c for c in cands if not (c[3] == kind and c[2] == tp)]
                cands.append((d * KINDS[kind][1] * STICKY_BONUS, d, tp, kind))
        return min(cands, key=lambda c: c[0]) if cands else None

    # -- what is near the pointer ----------------------------------------------------------------------
    def _near_objects(self, p: Pt, r: float, exclude):
        c = self.canvas
        win = BBox(p[0] - r, p[1] - r, p[0] + r, p[1] + r)
        for o in c.visible_objects():
            if o.id not in exclude and c.obj_bbox(o).intersects(win):
                yield o

    def _crossings(self, p: Pt, tol: float, exclude) -> list[tuple[Pt, float]]:
        """Where vectors cross each other close to ``p``."""
        r = tol * 2.0
        flat = max(1e-6, min(self.canvas.px(0.25), 0.05))
        pieces: list[tuple[Pt, Pt, int, int]] = []           # (a, b, contour number, piece number)
        n = 0
        for o in self._near_objects(p, r, exclude):
            for c in o.contours:
                n += 1
                pts = c.flatten(flat)
                if c.closed and pts:
                    pts = pts + [pts[0]]
                for k in range(len(pts) - 1):
                    a, b = pts[k], pts[k + 1]
                    if (min(a[0], b[0]) <= p[0] + r and max(a[0], b[0]) >= p[0] - r
                            and min(a[1], b[1]) <= p[1] + r and max(a[1], b[1]) >= p[1] - r):
                        pieces.append((a, b, n, k))
        if len(pieces) < 2 or len(pieces) > 600:
            return []
        A = np.array([q[0] for q in pieces], float)
        B = np.array([q[1] for q in pieces], float)
        R = B - A
        cid = np.array([q[2] for q in pieces])
        pid = np.array([q[3] for q in pieces])
        i, j = np.triu_indices(len(pieces), 1)
        rr, ss, w = R[i], R[j], A[j] - A[i]
        den = rr[:, 0] * ss[:, 1] - rr[:, 1] * ss[:, 0]
        ok = np.abs(den) > 1e-12 * (np.hypot(rr[:, 0], rr[:, 1]) * np.hypot(ss[:, 0], ss[:, 1]) + 1e-300)
        den = np.where(ok, den, 1.0)
        t = (w[:, 0] * ss[:, 1] - w[:, 1] * ss[:, 0]) / den
        u = (w[:, 0] * rr[:, 1] - w[:, 1] * rr[:, 0]) / den
        eps = 1e-9
        ok &= (t >= -eps) & (t <= 1 + eps) & (u >= -eps) & (u <= 1 + eps)
        ok &= ~((cid[i] == cid[j]) & (np.abs(pid[i] - pid[j]) <= 1))     # (neighbours meet at a node, not cross)
        out = []
        for k in np.flatnonzero(ok):
            q = (float(A[i[k], 0] + t[k] * rr[k, 0]), float(A[i[k], 1] + t[k] * rr[k, 1]))
            d = math.hypot(q[0] - p[0], q[1] - p[1])
            if d <= tol and all(math.hypot(q[0] - o[0][0], q[1] - o[0][1]) > 1e-7 for o in out):
                out.append((q, d))
        return out

    def _edges(self, p: Pt, tol: float, exclude) -> list[tuple[Pt, float]]:
        """The nearest point of each vector that is within reach."""
        out = []
        for o in self._near_objects(p, tol, exclude):
            best = None
            for c in o.contours:
                if c.segs:
                    _, _, q, d = c.nearest(p)
                    if best is None or d < best[1]:
                        best = (q, d)
            if best is not None and best[1] <= tol:
                out.append(best)
        return out

    def _guide_points(self, p: Pt, tol: float) -> list[tuple[Pt, float]]:
        vs = [v for k, v in self.canvas.guides() if k == "v" and abs(v - p[0]) <= tol]
        hs = [v for k, v in self.canvas.guides() if k == "h" and abs(v - p[1]) <= tol]
        out = []
        for v in vs:
            for h in hs:
                out.append(((v, h), math.hypot(v - p[0], h - p[1])))
        if not out:
            out += [((v, p[1]), abs(v - p[0])) for v in vs] + [((p[0], h), abs(h - p[1])) for h in hs]
        return out

    # -- a selection that is dragged ---------------------------------------------------------------------
    def sources(self, objs, grab: Pt) -> tuple[np.ndarray, int]:
        """The points of a selection that cling: the corners, the middles of the sides and the centre of its box and,
        for a few vectors, their own nodes, middles and centres. ``grab`` is where it was taken: the feature
        nearest to it is the one the grid takes."""
        bb = BBox()
        for o in objs:
            bb = bb.union(self.canvas.obj_bbox(o))
        pts: list[Pt] = []
        if not bb.empty:
            x0, y0, x1, y1 = bb.xmin, bb.ymin, bb.xmax, bb.ymax
            xm, ym = (x0 + x1) / 2, (y0 + y1) / 2
            pts += [(x0, y0), (xm, y0), (x1, y0), (x0, ym), (xm, ym), (x1, ym), (x0, y1), (xm, y1), (x1, y1)]
        if 1 < len(objs) <= 60:                  # (each vector by its own box: parts lined up with parts)
            for o in objs:
                b = self.canvas.obj_bbox(o)
                pts += [(b.xmin, b.ymin), (b.xmax, b.ymin), (b.xmin, b.ymax), (b.xmax, b.ymax), b.center]
        if len(objs) <= 12:                       # (a few vectors: down to their own nodes)
            T = Targets(objs, None)
            if len(T.P) <= 400:
                pts += [(float(x), float(y)) for x, y in T.P]
        if not pts:
            return np.zeros((0, 2)), 0
        S = np.array(pts, float)
        g = int(np.argmin(np.hypot(S[:, 0] - grab[0], S[:, 1] - grab[1])))
        return S, g

    def move(self, sources: np.ndarray, grab: int, delta: Pt, exclude=frozenset(),
             axes: tuple[bool, bool] = (True, True)) -> Snap:
        """The move of a selection by ``delta``, clung to what is near: a feature of it on a point of the drawing,
        else level with one, else the grab point on the grid. ``axes``: which of X and Y may change."""
        if not len(sources) or modifier_free():
            self._stick = None
            return Snap(delta=delta)
        s, tol = self.s, self.tol()
        S = sources + np.asarray(delta, float)
        best = None                                   # (weighted, source index, target point, kind)
        if s.snap_objects:
            T = self.targets()
            if len(T.P):
                lo, hi = S.min(axis=0) - tol * STICKY, S.max(axis=0) + tol * STICKY
                m = ((T.P[:, 0] >= lo[0]) & (T.P[:, 0] <= hi[0]) & (T.P[:, 1] >= lo[1]) & (T.P[:, 1] <= hi[1]))
                keep = T.keep(exclude)
                if keep is not None:
                    m &= keep
                idx = np.flatnonzero(m)
                if len(idx):
                    TP, TK = T.P[idx], T.K[idx]
                    W = _WEIGHT[TK]
                    held = None                         # the target that held the selection a moment ago
                    if self._stick is not None and len(self._stick) == 3:
                        hit = np.flatnonzero((TP[:, 0] == self._stick[1][0]) & (TP[:, 1] == self._stick[1][1]))
                        if len(hit):
                            held = (int(self._stick[2]), int(hit[0]))
                    for a in range(0, len(S), 64):
                        part = S[a:a + 64]
                        D = np.hypot(part[:, None, 0] - TP[None, :, 0], part[:, None, 1] - TP[None, :, 1])
                        ok = self._axis_ok(part, TP, tol, axes)
                        score = np.where(ok & (D <= tol), D * W[None, :], np.inf)
                        if held is not None and a <= held[0] < a + 64:
                            i, j = held[0] - a, held[1]
                            if ok[i, j] and D[i, j] <= tol * STICKY:
                                score[i, j] = min(score[i, j], D[i, j] * W[j] * STICKY_BONUS)
                        i, j = np.unravel_index(int(np.argmin(score)), score.shape)
                        if np.isfinite(score[i, j]) and (best is None or score[i, j] < best[0]):
                            best = (float(score[i, j]), a + int(i), (float(TP[j, 0]), float(TP[j, 1])),
                                    _POINT_KINDS[TK[j]])
        if best is not None:
            _, i, q, kind = best
            src = sources[i]
            d = (q[0] - src[0] if axes[0] else delta[0], q[1] - src[1] if axes[1] else delta[1])
            self._stick = (kind, q, i)
            return Snap(delta=d, kind=kind, target=q, source=(src[0] + d[0], src[1] + d[1]))
        self._stick = None
        # no point: each axis on its own, guide > level with a node > the grid
        d = [delta[0], delta[1]]
        kinds = ["", ""]
        marks: list[tuple[Pt, Pt] | None] = [None, None]
        lines: list[tuple[Pt, Pt]] = []
        by = self.targets().axes(exclude) if s.snap_objects and s.snap_align else None
        for ax in (0, 1):
            if not axes[ax]:
                continue
            col = S[:, ax]
            choice = None                              # (distance, new delta along the axis, kind, source index, target)
            for k, v in self.canvas.guides():
                if (k == "v") == (ax == 0):
                    i = int(np.argmin(np.abs(col - v)))
                    dd = abs(col[i] - v)
                    if dd <= tol and (choice is None or dd * KINDS["guide"][1] < choice[0]):
                        choice = (dd * KINDS["guide"][1], v - sources[i][ax], "guide", i, None)
            if by is not None:
                dist_, pts = _nearest_level(by[ax], ax, col)
                i = int(np.argmin(dist_))
                if dist_[i] <= tol and (choice is None or dist_[i] * KINDS["align"][1] < choice[0]):
                    choice = (float(dist_[i]) * KINDS["align"][1], float(pts[i][ax] - sources[i][ax]), "align", i,
                              (float(pts[i][0]), float(pts[i][1])))
            if choice is not None:
                d[ax] = choice[1]
                kinds[ax] = choice[2]
                if choice[4] is not None:
                    src = sources[choice[3]]
                    marks[ax] = ((float(src[0] + (d[0] if ax == 0 else delta[0])),
                                  float(src[1] + (d[1] if ax == 1 else delta[1]))), choice[4])
        if s.snap_grid and s.grid > 0:
            g = s.grid
            gsrc = sources[grab]
            for ax in (0, 1):
                if axes[ax] and not kinds[ax]:
                    d[ax] = round((gsrc[ax] + delta[ax]) / g) * g - gsrc[ax]
                    kinds[ax] = "grid"
        if not any(kinds):
            return Snap(delta=delta)
        caught = [k for k in kinds if k]
        kind = "align" if "align" in caught else "guide" if "guide" in caught else "grid"
        for mk in marks:
            if mk is not None:
                lines.append(mk)
        feature = (float(sources[grab][0] + d[0]), float(sources[grab][1] + d[1]))
        return Snap(delta=(d[0], d[1]), kind=kind, target=feature, source=feature, lines=lines)

    def _axis_ok(self, S: np.ndarray, TP: np.ndarray, tol: float, axes: tuple[bool, bool]) -> np.ndarray:
        """With an axis locked (Shift) a point only counts when it is level with the feature on that axis."""
        if axes[0] and axes[1]:
            return np.ones((len(S), len(TP)), bool)
        if axes[0]:
            return np.abs(S[:, None, 1] - TP[None, :, 1]) <= tol
        return np.abs(S[:, None, 0] - TP[None, :, 0]) <= tol

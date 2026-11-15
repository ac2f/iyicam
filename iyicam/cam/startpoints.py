"""Smart start points: where a closed cut begins.

The start of a closed cut is where the tool goes down (at the end of its lead-in
when there is one) and where the cut finally closes. A good start

* lies in solid material: clear of everything machined before it (the kerf of
  earlier cuts and toolpaths) by a safety margin, so the tool never plunges
  next to a slot or into a sliver that is about to break free,
* keeps away from corners, where a plunge or a lead leaves a mark,
* closes where the sheet around it is still there: the material beyond the cut at the start
  and along the stretch cut last has not been cut away by earlier cuts, so every cut is held by the
  still fixed rest of the sheet to the very end,
* lies in the *closing zone* of the sweep: a little above the bottom right corner when the sweep
  goes from bottom left to top right (5 % of the side, never on the corner), and the same turned
  and mirrored for the other sweeps (:func:`closing_zone`),
* is not far from where the tool already is,
* stays where it is when it is (almost) as good as the best place, so that
  starts do not wander every time the job is recalculated.
"""
from __future__ import annotations

import math
from collections import defaultdict
from dataclasses import dataclass
from typing import Callable, Iterable, Sequence

import numpy as np

from ..core import clip
from ..core.geom import Arc, BBox, Line, Pt
from .moves import ArcMove, Feed, arc_sweep
from .pathutil import PathWalker


class Machined:
    """Machined areas as tool centre lines with a radius, indexed on a grid."""

    def __init__(self, cell: float = 25.0):
        self.cell = cell
        self.segs: list[tuple[float, float, float, float, float]] = []
        self.grid: dict[tuple[int, int], list[int]] = defaultdict(list)
        self.rmax = 0.0
        self._seen: set = set()  # depth passes repeat the same lines
        self._arr = np.empty((256, 5))  # the segments again, as a growing array

    def __len__(self) -> int:
        return len(self.segs)

    def add_polyline(self, pts: Sequence[Pt], r: float, closed: bool = False) -> None:
        pts = list(pts) + ([pts[0]] if closed and pts else [])
        for a, b in zip(pts, pts[1:]):
            self.add_segment(a, b, r)

    def add_segment(self, a: Pt, b: Pt, r: float) -> None:
        k = (round(a[0], 3), round(a[1], 3), round(b[0], 3), round(b[1], 3), round(r, 3))
        if k in self._seen:
            return
        self._seen.add(k)
        i = len(self.segs)
        self.segs.append((a[0], a[1], b[0], b[1], r))
        if i >= len(self._arr):
            self._arr = np.concatenate([self._arr, np.empty_like(self._arr)])
        self._arr[i] = self.segs[-1]
        self.rmax = max(self.rmax, r)
        c = self.cell
        for gx in range(math.floor((min(a[0], b[0]) - r) / c), math.floor((max(a[0], b[0]) + r) / c) + 1):
            for gy in range(math.floor((min(a[1], b[1]) - r) / c), math.floor((max(a[1], b[1]) + r) / c) + 1):
                self.grid[(gx, gy)].append(i)

    def add_moves(self, moves: Iterable, radius: Callable[[float], float], tol: float = 0.05) -> None:
        """The cutting moves (below the surface) of a toolpath; ``radius(depth)`` of the tool."""
        pos = None
        for m in moves:
            if not hasattr(m, "x") or not hasattr(m, "z"):
                continue
            if isinstance(m, (Feed, ArcMove)) and pos is not None and min(pos[2], m.z) < -1e-6:
                r = radius(-min(pos[2], m.z))
                if isinstance(m, ArcMove):
                    sw = arc_sweep(pos[0], pos[1], m)
                    rad = math.hypot(pos[0] - m.cx, pos[1] - m.cy)
                    n = max(1, math.ceil(abs(sw) * rad / max(2.0, 4 * tol)))
                    a0 = math.atan2(pos[1] - m.cy, pos[0] - m.cx)
                    pts = [(m.cx + rad * math.cos(a0 + sw * k / n), m.cy + rad * math.sin(a0 + sw * k / n))
                           for k in range(n + 1)]
                    self.add_polyline(pts, r)
                else:
                    self.add_segment(pos[:2], (m.x, m.y), r)
            pos = (m.x, m.y, m.z)

    def gap(self, pts: np.ndarray, reach: float) -> np.ndarray:
        """Distance from each point to the edge of the machined area (negative inside it);
        ``inf`` when nothing machined is within ``reach``."""
        pts = np.asarray(pts, float).reshape(-1, 2)
        out = np.full(len(pts), np.inf)
        if not self.segs or not len(pts):
            return out
        c = self.cell
        lo, hi = pts.min(axis=0) - reach, pts.max(axis=0) + reach
        idx: set[int] = set()
        for gx in range(math.floor(lo[0] / c), math.floor(hi[0] / c) + 1):
            for gy in range(math.floor(lo[1] / c), math.floor(hi[1] / c) + 1):
                idx.update(self.grid.get((gx, gy), ()))
        if not idx:
            return out
        S = self._arr[np.fromiter(idx, int, len(idx))]
        # segments whose reach cannot touch the points are dropped before the big arrays
        smin = np.minimum(S[:, 0:2], S[:, 2:4]) - S[:, 4:5]
        smax = np.maximum(S[:, 0:2], S[:, 2:4]) + S[:, 4:5]
        S = S[np.all(smin <= hi, axis=1) & np.all(smax >= lo, axis=1)]
        if not len(S):
            return out
        A, B, R = S[:, 0:2], S[:, 2:4], S[:, 4]
        AB = B - A
        L2 = np.maximum((AB * AB).sum(axis=1), 1e-18)
        for k in range(0, len(pts), 256):  # chunks keep the (points x segments) arrays small
            P = pts[k:k + 256, None, :]
            t = np.clip(((P - A) * AB).sum(axis=2) / L2, 0.0, 1.0)
            Q = A + t[..., None] * AB
            d = np.sqrt(((P - Q) ** 2).sum(axis=2)) - R
            out[k:k + 256] = d.min(axis=1)
        out[out > reach] = np.inf
        return out


@dataclass
class StartChoice:
    s: float           # arc length position on the loop
    point: Pt
    gap: float         # free material between the plunging tool and machined areas (inf = nothing near)
    corner: float      # distance along the loop to the nearest corner
    ok: bool           # clear of machined areas by the margin and away from corners
    kept: bool         # the previous start was good enough to keep
    held: float = 0.0  # 0..1, how firmly the stretch that closes the cut is held by the material around it
    support: float = 1.0   # 0..1, how much of the sheet beyond the cut is still there where it closes
    zone: float = 0.0      # distance along the loop from the target of the closing zone
    target: float = 0.0    # position of that target


def corners(walker: PathWalker, tight: float = 0.0, angle: float = 12.0) -> list[tuple[float, float]]:
    """``(from, to)`` arc length spans of the corners of a loop: where it changes direction by
    more than ``angle`` degrees, and arcs tighter than ``tight`` (an offset path rolls around a
    sharp corner of the vector on an arc of the tool radius)."""
    out = []
    n = len(walker.segs)
    L = walker.length
    lim = math.cos(math.radians(angle))
    for i in range(n if walker.closed else n - 1):
        a, b = walker.segs[i], walker.segs[(i + 1) % n]
        t1, t2 = a.tangent_at(1.0), b.tangent_at(0.0)
        if t1[0] * t2[0] + t1[1] * t2[1] < lim:
            s = walker.cum[i + 1] % L if L > 0 else 0.0
            out.append((s, s))
    # runs of tight arcs around one centre; a loop that is nothing but such a run is a small round
    # shape (a small hole), not a corner
    if all((isinstance(sg, Arc) and sg.radius < tight) or sg.length() < 0.1 * tight for sg in walker.segs):
        return [sp for sp in out if sp[0] == sp[1]]
    run: list[int] = []
    runs = []
    centre = None
    for i, seg in enumerate(walker.segs):
        if isinstance(seg, Arc) and seg.radius < tight:
            if run and run[-1] == i - 1 and math.dist(centre, seg.center) < 1e-3:
                run.append(i)
            else:
                run = [i]
                runs.append(run)
            centre = seg.center
        elif run and run[-1] == i - 1 and seg.length() < 0.1 * tight:
            run.append(i)  # a sliver left by arc fitting does not break the run
    if len(runs) > 1 and walker.closed and runs[0][0] == 0 and runs[-1][-1] == n - 1:
        a, b = (next(walker.segs[i] for i in r if isinstance(walker.segs[i], Arc)) for r in (runs[0], runs[-1]))
        if math.dist(a.center, b.center) < 1e-3:
            runs[0] = runs.pop() + runs[0]  # the run wraps around the start of the loop
    for r in runs:
        if len(set(r)) == n:
            continue  # the whole loop is one small round shape: nowhere is a corner
        a, b = walker.cum[r[0]], walker.cum[r[-1] + 1]
        if r[0] > r[-1]:  # wrapped: two spans
            out += [(a, L), (0.0, b)]
        else:
            out.append((a, b))
    return out


def _span_distance(s: np.ndarray, spans: list[tuple[float, float]], L: float) -> np.ndarray:
    """Distance along a closed loop of length ``L`` from positions ``s`` to the nearest span."""
    if not spans:
        return np.full(len(s), L)
    out = np.full(len(s), np.inf)
    for a, b in spans:
        inside = (s >= a - 1e-9) & (s <= b + 1e-9)
        da, db = np.abs(s - a) % L, np.abs(s - b) % L
        d = np.minimum(np.minimum(da, L - da), np.minimum(db, L - db))  # either way round the loop
        out = np.minimum(out, np.where(inside, 0.0, d))
    return out


def sample(walker: PathWalker, s: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Points and unit tangents at arc length positions ``s`` of a closed path (vectorised for
    lines and arcs)."""
    L = walker.length
    s = np.asarray(s, float) % L if L > 0 else np.zeros(len(s))
    cum = np.asarray(walker.cum)
    idx = np.clip(np.searchsorted(cum, s, side="right") - 1, 0, len(walker.segs) - 1)
    P = np.empty((len(s), 2))
    T = np.empty((len(s), 2))
    for i in np.unique(idx):
        sel = idx == i
        seg = walker.segs[i]
        u = (s[sel] - cum[i]) / max(cum[i + 1] - cum[i], 1e-12)
        if isinstance(seg, Line):
            a, b = np.asarray(seg.a), np.asarray(seg.b)
            P[sel] = a + u[:, None] * (b - a)
            d = b - a
            T[sel] = d / (np.hypot(*d) or 1.0)
        elif isinstance(seg, Arc):
            c = np.asarray(seg.center)
            ang = seg.start_angle + seg.sweep * u
            P[sel] = c + seg.radius * np.c_[np.cos(ang), np.sin(ang)]
            sgn = 1.0 if seg.sweep > 0 else -1.0
            T[sel] = sgn * np.c_[-np.sin(ang), np.cos(ang)]
        else:
            P[sel] = [walker.point_at(float(x)) for x in s[sel]]
            T[sel] = [walker.tangent_at(float(x)) for x in s[sel]]
    return P, T


DEFAULT_HOLD = (math.sqrt(0.5), math.sqrt(0.5))  # bottom left -> top right: held from the right and the top
EDGE_HOLD = 0.6        # beyond the sheet edge there is nothing, but the rim of the sheet is still held
SUPPORT_COST = 150.0   # mm along the loop that a whole missing support is worth (the two rings next to the cut)
ZONE_FRACTION = 0.05   # the cut closes this fraction of the side above (or before) the corner
ZONE_MAX = 100.0       # ... but never further than this from the corner
COMFORT = 4.0          # free material wanted between the plunging tool and the cuts before it, beyond the margin
COMFORT_WEIGHT = 2.0   # one mm short of that costs this many mm along the loop


@dataclass
class Zone:
    """Where a cut should close: the corner the sweep ends on and the target a little beyond it."""
    anchor: float      # arc length of the corner (its span edge the cut arrives at / leaves from)
    before: bool       # the target lies before the corner in the cutting direction, else after it
    delta: float       # distance of the target from the corner
    target: float      # arc length of the target
    corner: tuple[float, float] = (0.0, 0.0)   # the span of the corner: (from, to)


def closing_zone(walker: PathWalker, hold: Pt | None, cw: bool, corner: float, spans: list[tuple[float, float]],
                 inner: bool = False, fraction: float = ZONE_FRACTION) -> Zone:
    """The closing zone of a loop.

    The corner is the extreme of the loop a quarter turn from the hold direction, turned with the
    cutting direction: for a sweep from bottom left to top right (held from the right and the top) and
    clockwise cutting that is the bottom right corner, and the cut closes on the right side, ``fraction``
    of the side above the corner (at least the corner distance), so that the last stretch cut is the
    side facing the sheet that is still to be cut. The other sweeps are the same turned and mirrored.
    Counter-clockwise outlines take the mirrored corner; an inner cut (a hole) always takes the corner of
    the sweep itself.
    """
    L = walker.length
    H = np.asarray(hold if hold is not None else DEFAULT_HOLD, float)
    H = H / (np.hypot(*H) or 1.0)
    K = np.array([H[1], -H[0]]) if (inner or cw) else np.array([-H[1], H[0]])
    n = 360
    ss = np.concatenate([np.arange(n) * L / n, np.asarray(walker.cum[:-1], float)])  # the nodes as well
    P, T = sample(walker, ss)
    k = int(np.argmax(P @ (K + 0.02 * H) + 1e-9 * np.arange(len(ss))[::-1]))  # (towards the hold side on a tie)
    a_s = float(ss[k]) % L
    span = next(((a, b) for a, b in spans if a - 1e-6 <= a_s <= b + 1e-6), (a_s, a_s))
    before = True if not inner else bool(cw)
    t_at = (span[0] - 0.5) if before else (span[1] + 0.5)
    tan = sample(walker, np.array([t_at]))[1][0]
    ext = float(np.ptp(P[:, 1]) if abs(tan[0]) < abs(tan[1]) else np.ptp(P[:, 0]))  # the side we are on
    delta = min(max(corner, fraction * ext), ZONE_MAX)
    target = (span[0] - delta) % L if before else (span[1] + delta) % L
    return Zone(a_s, before, delta, target, span)


def zone_cost(zone: Zone, s: np.ndarray, L: float) -> np.ndarray:
    """How far (along the loop) positions ``s`` are from the target of the zone; closer to the corner
    than the target counts double, the wrong side of the corner (and anything far) a lot."""
    u = ((zone.corner[0] - s) if zone.before else (s - zone.corner[1])) % L   # distance from the corner
    return np.where(u >= zone.delta, u - zone.delta, 2.0 * (zone.delta - u))


def support(walker: PathWalker, r: float, machined: Machined, hold: Pt | None, sheet: BBox | None,
            n: int) -> tuple[np.ndarray, np.ndarray, np.ndarray, bool, np.ndarray]:
    """``(positions, points, strength, clockwise, material)`` of ``n`` samples along a closed path.

    The strength (0..1) of a point is how firmly the material on the far side of
    the cut is still held there: the hold direction of the sweep (material is held
    from where the sweep has not been yet) and what is really there now, probed
    beyond the cut width (already machined areas and the sheet edge hold nothing).
    ``material`` (0..1) is the second part alone: how much of the sheet beyond the cut is still
    there (the sheet edge counts :data:`EDGE_HOLD`: no sheet, but its rim is still held).
    """
    L = walker.length
    ss = np.arange(n) * L / n
    P, T = sample(walker, ss)
    area = 0.5 * float(np.sum(P[:, 0] * np.roll(P[:, 1], -1) - np.roll(P[:, 0], -1) * P[:, 1]))
    cw = area < 0
    # normals pointing away from what this cut sets free (its inside)
    N = np.c_[-T[:, 1], T[:, 0]] if cw else np.c_[T[:, 1], -T[:, 0]]
    H = np.asarray(hold if hold is not None else DEFAULT_HOLD, float)
    H = H / (np.hypot(*H) or 1.0)
    prior = (1.0 + N @ H) / 2
    rings = ((1.5, 0.5), (5.0, 0.3), (10.0, 0.2))
    Q = np.concatenate([P + N * (r + d) for d, _ in rings])
    cut = machined.gap(Q, 1.0) <= 0 if len(machined) else np.zeros(len(Q), bool)
    ones = np.ones(len(Q), bool)
    if sheet is not None:
        region = getattr(sheet, "region", None)
        if region:  # a leftover: nothing holds beyond its outline
            on = clip.region_mask(Q, region)
        else:
            on = ((Q[:, 0] >= sheet.xmin) & (Q[:, 0] <= sheet.xmax)
                  & (Q[:, 1] >= sheet.ymin) & (Q[:, 1] <= sheet.ymax))
    else:
        on = ones
    # beyond the sheet edge: its rim is still held where the cut is on the sheet; a cut outside the sheet
    # altogether (a part that does not fit on it) has no sheet to be held by, and nothing to lose either
    if sheet is not None:
        if getattr(sheet, "region", None):
            p_on = clip.region_mask(P, sheet.region)
        else:
            p_on = ((P[:, 0] >= sheet.xmin) & (P[:, 0] <= sheet.xmax) & (P[:, 1] >= sheet.ymin) & (P[:, 1] <= sheet.ymax))
        edge = np.where(np.tile(p_on, len(rings)), EDGE_HOLD, 1.0)
    else:
        edge = np.full(len(Q), EDGE_HOLD)
    present = np.where(cut, 0.0, np.where(on, 1.0, edge))
    local = (0.6 * present[0:n] + 0.4 * present[n:2 * n])      # the two rings next to the cut (a third one is too far to hold it)
    solid = sum(wgt * (~cut & on)[k * n:(k + 1) * n] for k, (_, wgt) in enumerate(rings))
    pw = 0.5 if hold is not None else 0.25
    return ss, P, pw * prior + (1 - pw) * solid, cw, local


def choose_start(walker: PathWalker, r: float, entry: Callable[[float], list[Pt]] | None, machined: Machined,
                 prev: Pt | None = None, hint: float | None = None, margin: float = 1.0,
                 corner: float = 0.0, locked: bool = False, hold: Pt | None = None,
                 sheet: BBox | None = None, shift: float = 0.0, inner: bool = False) -> StartChoice:
    """Best start position on the closed tool centre path ``walker`` (in cutting direction).

    A closed cut ends where it starts: the stretch just before the start is cut
    last and holds the part to the end. The start is chosen

    1. in the *closing zone* of the sweep (:func:`closing_zone`): a little above the bottom right
       corner for a sweep from bottom left to top right and clockwise cutting, the same turned and
       mirrored for the others;
    2. where that is clear: the plunge keeps ``margin`` from machined areas and the corner distance
       from corners, and the sheet beyond the cut at the close and along the stretch cut last (the
       *support*) has not been cut away by the cuts before. When the zone itself is not supported
       (a neighbour that is already cut lies there) the nearest place that is, along the loop, is
       taken, and when nowhere is, the best supported one.

    ``r`` is the tool radius, ``entry(s)`` the points the tool occupies at depth before it cuts from ``s``
    (``None``: just the plunge point). A ``locked`` start stays at ``hint``. ``inner``: a hole (it takes
    the corner of the sweep whatever its direction).

    Positions are where the cut closes. When the passes of a cut creep on (ramps,
    overlap) the tool goes down ``shift`` earlier along the path; the plunge
    clearance is checked there and both places keep off the corners.
    """
    L = walker.length
    corner = corner if corner > 0 else max(2.0, r)
    corner = min(corner, L / 8)       # (a loop of a few mm has no room for the distance: nothing is "clear")
    spans = corners(walker, tight=2 * r + 1.0)
    n = int(min(240, max(64, L / 1.5)))
    ss, SP, w, cw, loc = support(walker, r, machined, hold, sheet, n)
    zone = closing_zone(walker, hold, cw, corner, spans, inner)
    if locked and hint is not None:
        cand = np.array([hint % L])
    else:
        s_list = list(ss) + [(walker.cum[i] + walker.cum[i + 1]) / 2 for i in range(len(walker.segs))]
        for a, b in spans:  # exactly the corner distance off every corner, for the close and the plunge
            s_list += [a - corner, b + corner, a - corner + shift, b + corner + shift]
        s_list += [zone.target, zone.target + shift, zone.target - shift]
        if hint is not None:
            s_list.append(hint % L)
        cand = np.array(sorted(set(round(float(s) % L, 9) for s in s_list)))
    # along the loop to the nearest corner, from where the cut closes and from where the tool goes down
    cdist = np.minimum(_span_distance(cand, spans, L), _span_distance((cand - shift) % L, spans, L))
    pts = sample(walker, cand)[0]
    cap = 2 * r + margin + 5.0
    if entry is None:  # the tool goes straight down: just the plunge point
        plunge = sample(walker, (cand - shift) % L)[0]
        gap = machined.gap(plunge, cap + r) - r
        first = plunge
    else:
        entries = [entry(float(s - shift) % L) for s in cand]
        g = machined.gap(np.array([p for e in entries for p in e], float), cap + r) - r
        k = len(entries[0])
        gap = g.reshape(len(cand), k).min(axis=1)
        first = np.array([e[0] for e in entries])
    ok = (gap >= margin) & (cdist >= corner - 1e-6)
    # how long each sample (the stretch it stands for, judged at its middle) stays uncut
    late = (ss[None, :] + L / (2 * n) - cand[:, None]) % L / L
    keep = (late * w[None, :]).sum(axis=1) / max(float(w.sum()), 1e-9)
    bridge = min(L / 3, max(6 * r, 0.1 * L))  # the stretch that holds the part at the very end
    last = late >= 1.0 - bridge / L
    end = (last * w[None, :]).sum(axis=1) / np.maximum(last.sum(axis=1), 1)
    # the sheet beyond the cut where it closes and along the stretch cut last
    at = np.clip(np.rint(cand / (L / n)).astype(int) % n, 0, n - 1)
    near = np.minimum.reduce([loc[(at + d) % n] for d in (-1, 0, 1)])
    stretch = late >= 1.0 - min(L / 3, 6 * r + corner) / L       # the stretch cut last that has to be held: 6 tool radii
    held_end = (stretch * loc[None, :]).sum(axis=1) / np.maximum(stretch.sum(axis=1), 1)
    sup = np.minimum(near, held_end)
    travel = np.hypot(*(first - np.asarray(prev, float)).T) if prev is not None else np.zeros(len(cand))
    soft = keep + 0.6 * end - 0.02 * travel / (travel.max() + L)           # (the older, physical, taste)
    # room around the plunge: an earlier cut closer than COMFORT costs as much as a stretch along the loop
    cost = zone_cost(zone, cand, L) + COMFORT_WEIGHT * np.clip(margin + COMFORT - gap, 0.0, COMFORT)
    # what a place costs: the way along the loop to the zone, and every tenth of the sheet beyond the cut that has
    # already been cut away is worth SUPPORT_COST mm of it (a corner's worth is nothing, a neighbour cut is a lot)
    total = cost + SUPPORT_COST * (1.0 - np.minimum(sup, 1.0))
    if ok.any():
        pick = np.flatnonzero(ok)
        score = -total / L + 1e-4 * soft
        tol = max(1.0, 0.1 * zone.delta) / L
    elif (gap >= margin).any():
        pick = np.flatnonzero(gap >= margin)
        score = -total / L + 1e-4 * soft
        tol = max(1.0, 0.1 * zone.delta) / L
    else:
        pick = np.arange(len(cand))
        score = gap  # nothing is clear: get as far from machined areas as possible
        tol = 0.005
    best = int(pick[np.argmax(score[pick])])
    kept = False
    if hint is not None:
        h = int(np.argmin(np.abs(cand - hint % L)))
        if h in set(pick.tolist()) and score[h] >= score[best] - tol:
            best, kept = h, True
    return StartChoice(float(cand[best]), tuple(map(float, pts[best])), float(gap[best]), float(cdist[best]),
                       bool(ok[best]), kept or locked, float(end[best]), float(sup[best]),
                       float(cost[best]), float(zone.target))

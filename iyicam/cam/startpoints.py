"""Smart start points: where a closed cut begins.

The start of a closed cut is where the tool goes down (at the end of its lead-in
when there is one) and where the cut finally closes. A good start

* lies in solid material: clear of everything machined before it (the kerf of
  earlier cuts and toolpaths) by a safety margin, so the tool never plunges
  next to a slot or into a sliver that is about to break free,
* keeps away from corners, where a plunge or a lead leaves a mark,
* prefers straight stretches and gentle curves,
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

from ..core.geom import Arc, Pt, dist
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
        S = np.array([self.segs[i] for i in sorted(idx)])
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
    for i, seg in enumerate(walker.segs):
        if isinstance(seg, Arc) and seg.radius < tight:
            out.append((walker.cum[i], walker.cum[i + 1]))
    return out


def _span_distance(s: np.ndarray, spans: list[tuple[float, float]], L: float) -> np.ndarray:
    """Distance along a closed loop of length ``L`` from positions ``s`` to the nearest span."""
    if not spans:
        return np.full(len(s), L)
    out = np.full(len(s), np.inf)
    for a, b in spans:
        inside = (s >= a - 1e-9) & (s <= b + 1e-9)
        d = np.minimum(np.abs(s - a), np.abs(s - b))
        d = np.minimum(d, L - d)
        out = np.minimum(out, np.where(inside, 0.0, d))
    return out


def choose_start(walker: PathWalker, r: float, entry: Callable[[float], list[Pt]] | None, machined: Machined,
                 prev: Pt | None = None, hint: float | None = None, margin: float = 1.0,
                 corner: float = 0.0, locked: bool = False) -> StartChoice:
    """Best start position on the closed tool centre path ``walker``.

    ``r`` is the tool radius, ``entry(s)`` the points the tool occupies at depth
    before it starts cutting at ``s`` (samples along the lead-in; ``None``: just
    the plunge point). A ``locked`` start (picked by hand) stays at ``hint``.
    """
    L = walker.length
    corner = corner if corner > 0 else max(2.0, r)
    spans = corners(walker, tight=2 * r + 1.0)
    if locked and hint is not None:
        cand = np.array([hint % L])
    else:
        step = min(5.0, max(0.5, L / 100))
        s_list = list(np.arange(0.0, L, step))
        s_list += [(walker.cum[i] + walker.cum[i + 1]) / 2 for i in range(len(walker.segs))]
        if hint is not None:
            s_list.append(hint % L)
        cand = np.array(sorted(set(round(s, 9) for s in s_list)))
    cdist = _span_distance(cand, spans, L)  # along the loop to the nearest corner
    # along the loop to the nearest joint between segments: the middle of a stretch is calmer
    edist = _span_distance(cand, [(walker.cum[i], walker.cum[i]) for i in range(len(walker.segs))], L)
    ecap = max(3 * corner, 10.0)
    # curvature of the path (arcs bend, lines do not)
    kappa = np.zeros(len(cand))
    pts = []
    entries: list[list[Pt]] = []
    for k, s in enumerate(cand):
        i, t = walker.locate(float(s))
        seg = walker.segs[i]
        if isinstance(seg, Arc):
            kappa[k] = 1.0 / max(seg.radius, 1e-6)
        P = seg.point_at(t)
        pts.append(P)
        entries.append(entry(float(s)) if entry is not None else [P])
    cap = 2 * r + margin + 5.0
    flat = np.array([p for e in entries for p in e], float)
    g = machined.gap(flat, cap + r) - r
    gap = np.empty(len(cand))
    k0 = 0
    for k, e in enumerate(entries):
        gap[k] = g[k0:k0 + len(e)].min()
        k0 += len(e)
    ok = (gap >= margin) & (cdist >= corner)
    travel = np.array([dist(prev, e[0]) if prev is not None else 0.0 for e in entries])
    size = max(1.0, L / 4)
    score = (np.minimum(gap, cap) / cap
             + 0.7 * np.minimum(cdist, 3 * corner) / (3 * corner)
             + 0.3 * np.minimum(edist, ecap) / ecap
             - 0.3 * np.minimum(1.0, r * kappa)
             - 0.25 * travel / (travel.max() + size))
    pick = np.flatnonzero(ok) if ok.any() else np.flatnonzero(gap >= margin)
    if not len(pick):
        pick = np.arange(len(cand))
        score = gap  # nothing is clear: get as far from machined areas as possible
    best = int(pick[np.argmax(score[pick])])
    kept = False
    if hint is not None:
        h = int(np.argmin(np.abs(cand - hint % L)))
        if h in set(pick.tolist()) and score[h] >= score[best] - 0.02:
            best, kept = h, True
    return StartChoice(float(cand[best]), pts[best], float(gap[best]), float(cdist[best]), bool(ok[best]),
                       kept or locked)

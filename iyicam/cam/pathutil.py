"""Arc-length parametrised paths and emission of moves with varying Z."""
from __future__ import annotations

import bisect
import math
from typing import Callable, Sequence

from ..core import arcfit
from ..core.geom import Arc, Bezier, Contour, Line, Pt, Segment, dist
from .moves import ArcMove, Feed

ZProfile = list[tuple[float, float]]  # sorted (u, z) breakpoints, linear in between


def machinable(c: Contour, tol: float = 0.005) -> Contour:
    """Replace beziers by fitted lines/arcs so every segment maps to G1/G2/G3."""
    if not any(isinstance(s, Bezier) for s in c.segs):
        return c
    out: list[Segment] = []
    for s in c.segs:
        if isinstance(s, Bezier):
            pts = [s.a] + s.flatten(tol / 4)
            fitted = arcfit.fit_segments(pts, False, tol)
            if fitted:
                # keep exact end points so the chain stays connected
                fitted[0] = fitted[0].with_ends(s.a, fitted[0].b)
                fitted[-1] = fitted[-1].with_ends(fitted[-1].a, s.b)
            out.extend(fitted)
        else:
            out.append(s)
    return Contour(out, c.closed)


def subsegment(s: Segment, t0: float, t1: float) -> Segment:
    if t0 <= 1e-12 and t1 >= 1 - 1e-12:
        return s
    if isinstance(s, Line):
        return Line(s.point_at(t0), s.point_at(t1))
    if isinstance(s, Arc):
        sw = s.sweep
        a = s.point_at(t0)
        b = s.point_at(t1)
        return Arc(a, b, math.tan(sw * (t1 - t0) / 4.0))
    seg = s
    if t0 > 0:
        seg = seg.split(t0)[1]
    if t1 < 1:
        tt = (t1 - t0) / (1 - t0) if t0 < 1 else 1.0
        seg = seg.split(tt)[0]
    return seg


class PathWalker:
    """Segment chain with cumulative arc length."""

    def __init__(self, segs: Sequence[Segment], closed: bool):
        self.segs = list(segs)
        self.closed = closed
        self.cum = [0.0]
        for s in self.segs:
            self.cum.append(self.cum[-1] + s.length())

    @property
    def length(self) -> float:
        return self.cum[-1]

    def locate(self, s: float) -> tuple[int, float]:
        L = self.length
        if self.closed and L > 0:
            s = s % L
        s = max(0.0, min(L, s))
        i = max(0, min(len(self.segs) - 1, bisect.bisect_right(self.cum, s) - 1))
        sl = self.cum[i + 1] - self.cum[i]
        t = 0.0 if sl <= 1e-12 else (s - self.cum[i]) / sl
        seg = self.segs[i]
        if isinstance(seg, Bezier):
            t = seg.t_at_length(s - self.cum[i])
        return i, max(0.0, min(1.0, t))

    def point_at(self, s: float) -> Pt:
        i, t = self.locate(s)
        return self.segs[i].point_at(t)

    def tangent_at(self, s: float) -> Pt:
        i, t = self.locate(s)
        return self.segs[i].tangent_at(t)

    def pieces(self, s0: float, s1: float) -> list[Segment]:
        """Segments covering ``[s0, s1]``; wraps around for closed paths."""
        out: list[Segment] = []
        L = self.length
        if L <= 0 or s1 <= s0:
            return out
        if not self.closed:
            s0, s1 = max(0.0, s0), min(L, s1)
            return self._pieces_plain(s0, s1)
        base = math.floor(s0 / L) * L
        a = s0 - base
        b = s1 - base
        while b > 1e-12:
            hi = min(b, L)
            if hi - a > 1e-12:
                out += self._pieces_plain(a, hi)
            a = 0.0
            b -= L
        return out

    def _pieces_plain(self, s0: float, s1: float) -> list[Segment]:
        out = []
        for i, s in enumerate(self.segs):
            c0, c1 = self.cum[i], self.cum[i + 1]
            if c1 <= s0 + 1e-12 or c0 >= s1 - 1e-12:
                continue
            sl = c1 - c0
            if sl <= 1e-12:
                continue
            a = max(s0, c0)
            b = min(s1, c1)
            if isinstance(s, Bezier):
                t0, t1 = s.t_at_length(a - c0), s.t_at_length(b - c0)
            else:
                t0, t1 = (a - c0) / sl, (b - c0) / sl
            out.append(subsegment(s, t0, t1))
        return out


def eval_profile(prof: ZProfile, u: float) -> float:
    if not prof:
        raise ValueError("empty profile")
    if u <= prof[0][0]:
        return prof[0][1]
    if u >= prof[-1][0]:
        return prof[-1][1]
    us = [p[0] for p in prof]
    i = bisect.bisect_right(us, u) - 1
    (u0, z0), (u1, z1) = prof[i], prof[i + 1]
    if u1 - u0 <= 1e-12:
        return z1
    return z0 + (z1 - z0) * (u - u0) / (u1 - u0)


def emit_profile(moves: list, walker: PathWalker, s_start: float, length: float,
                 zfunc: Callable[[float], float], breaks: Sequence[float], feed: float,
                 start_pos: tuple[float, float, float], plunge: float | None = None) -> tuple[float, float, float]:
    """Emit feed/arc moves along ``walker`` from ``s_start`` for ``length`` with Z = zfunc(u).

    ``u`` runs from 0 to ``length``; ``breaks`` are the ``u`` values where the
    Z function may change slope or jump (the path is split there so Z stays
    linear on every piece; jumps become vertical moves).
    """
    cuts = sorted({0.0, length, *[b for b in breaks if 0.0 < b < length]})
    pos = start_pos
    eps = 1e-6
    for k in range(len(cuts) - 1):
        u0, u1 = cuts[k], cuts[k + 1]
        span = u1 - u0
        if span <= 1e-9:
            continue
        if span > 4 * eps:
            za, zb = zfunc(u0 + eps), zfunc(u1 - eps)
            slope = (zb - za) / (span - 2 * eps)
            z0, z1 = za - slope * eps, zb + slope * eps
        else:
            z0 = z1 = zfunc((u0 + u1) / 2)
        if abs(pos[2] - z0) > 1e-6:
            f = plunge if (plunge and z0 < pos[2]) else feed
            moves.append(Feed(pos[0], pos[1], z0, f))
            pos = (pos[0], pos[1], z0)
        segs = walker.pieces(s_start + u0, s_start + u1)
        total = sum(s.length() for s in segs) or 1.0
        acc = 0.0
        for s in segs:
            acc += s.length()
            z = z0 + (z1 - z0) * (acc / total)
            pos = emit_segment(moves, s, z, feed, pos)
    return pos


MIN_ARC_CHORD = 0.002  # shorter arcs are written as straight moves (never as full circles)


def emit_segment(moves: list, s: Segment, z: float, feed: float,
                 pos: tuple[float, float, float]) -> tuple[float, float, float]:
    """Emit one segment ending at height ``z`` (helical if Z changes)."""
    x, y = s.b
    if abs(x - pos[0]) < 1e-9 and abs(y - pos[1]) < 1e-9 and abs(z - pos[2]) < 1e-9:
        return pos
    if isinstance(s, Arc):
        if s.radius > 1e5 or dist(s.a, s.b) < MIN_ARC_CHORD:
            moves.append(Feed(x, y, z, feed))
        else:
            c = s.center
            moves.append(ArcMove(x, y, z, c[0], c[1], not s.ccw, feed))
    elif isinstance(s, Bezier):
        pts = s.flatten(0.002)
        n = len(pts)
        for i, p in enumerate(pts):
            zz = pos[2] + (z - pos[2]) * (i + 1) / n
            moves.append(Feed(p[0], p[1], zz, feed))
    else:
        moves.append(Feed(x, y, z, feed))
    return (x, y, z)


def pl_max_breaks(f: Callable[[float], float], g: Callable[[float], float],
                  breaks: Sequence[float]) -> list[float]:
    """Add the crossing points of two piecewise linear functions to ``breaks``."""
    bs = sorted(set(breaks))
    out = list(bs)
    for a, b in zip(bs, bs[1:]):
        da, db = f(a) - g(a), f(b) - g(b)
        if da * db < 0:
            out.append(a + (b - a) * da / (da - db))
    return sorted(out)


def nearest_on_polys(p: Pt, segs: Sequence[Segment]) -> tuple[int, float, float]:
    best = (0, 0.0, math.inf)
    for i, s in enumerate(segs):
        t = s.nearest_t(p)
        d = dist(p, s.point_at(t))
        if d < best[2]:
            best = (i, t, d)
    return best

"""Fit lines and arcs to dense polylines (e.g. offset results) so G-code can use G2/G3."""
from __future__ import annotations

import math
from typing import Sequence

import numpy as np

from .geom import Arc, Contour, Line, Pt, Segment, circumcenter, cross, dist, point_line_distance, sub


_VEC = 24  # long runs are checked with numpy, short ones in plain Python (less overhead)


def _line_ok(P: Sequence[Pt], i: int, j: int, tol: float, A: np.ndarray | None = None) -> bool:
    a, b = P[i], P[j]
    if A is not None and j - i > _VEC:
        q = A[i + 1:j] - A[i]
        ab = A[j] - A[i]
        L2 = float(ab @ ab)
        if L2 < 1e-18:
            d = np.hypot(q[:, 0], q[:, 1])
        else:
            t = np.clip(q @ ab / L2, 0.0, 1.0)
            d = np.hypot(q[:, 0] - t * ab[0], q[:, 1] - t * ab[1])
        return bool(np.all(d <= tol))
    for k in range(i + 1, j):
        if point_line_distance(P[k], a, b) > tol:
            return False
    return True


def _arc_ok(P: Sequence[Pt], i: int, j: int, tol: float, max_r: float, max_sweep: float,
            A: np.ndarray | None = None):
    """Return an Arc through P[i]..P[j] when all points fit within ``tol``."""
    m = (i + j) // 2
    c = circumcenter(P[i], P[m], P[j])
    if c is None:
        return None
    r = dist(c, P[i])
    if r > max_r or r < tol * 2:
        return None
    turn = cross(sub(P[i + 1], P[i]), sub(P[i + 2], P[i + 1]))
    if abs(turn) < 1e-14:
        return None
    ccw = turn > 0
    if A is not None and j - i > _VEC:
        seg = A[i:j + 1] - c
        if np.any(np.abs(np.hypot(seg[1:, 0], seg[1:, 1]) - r) > tol):
            return None
        dif = np.diff(seg, axis=0)
        chord = np.hypot(dif[:, 0], dif[:, 1])
        if np.any(chord > 2 * r) or np.any(r - np.sqrt(np.maximum(0.0, r * r - chord * chord / 4)) > tol):
            return None
        cr = seg[:-1, 0] * seg[1:, 1] - seg[:-1, 1] * seg[1:, 0]
        if np.any(((cr > 0) != ccw) & (np.abs(cr) > 1e-12)):
            return None
        if float(np.sum(2 * np.arcsin(np.minimum(1.0, chord / (2 * r))))) > max_sweep:
            return None
        try:
            return Arc.from_center_points(P[i], P[j], c, ccw)
        except ValueError:
            return None
    swept = 0.0
    for k in range(i, j):
        p, q = P[k], P[k + 1]
        if abs(dist(c, q) - r) > tol:
            return None
        chord = dist(p, q)
        if chord > 2 * r:
            return None
        # sagitta of the arc over this chord must stay within tolerance
        if r - math.sqrt(max(0.0, r * r - chord * chord / 4)) > tol:
            return None
        cr = cross(sub(p, c), sub(q, c))
        if (cr > 0) != ccw and abs(cr) > 1e-12:
            return None
        swept += 2 * math.asin(min(1.0, chord / (2 * r)))
    if swept > max_sweep:
        return None
    try:
        arc = Arc.from_center_points(P[i], P[j], c, ccw)
    except ValueError:
        return None
    return arc


def fit_segments(pts: Sequence[Pt], closed: bool, tol: float = 0.005,
                 max_radius: float = 5000.0, min_arc_points: int = 4,
                 max_sweep: float = math.pi, keep_start: bool = False) -> list[Segment]:
    """Convert a polyline into a list of Line/Arc segments within ``tol``.

    For closed input the seam is moved to the start of the longest edge (unless
    ``keep_start``) so that arcs are not cut in two by an arbitrary start point.
    """
    P = list(pts)
    if closed and len(P) > 1 and P[0] == P[-1]:
        P.pop()
    if closed and not keep_start and len(P) > 2:
        k = max(range(len(P)), key=lambda i: dist(P[i], P[(i + 1) % len(P)]))
        P = P[k:] + P[:k]
    if closed and P:
        P.append(P[0])
    n = len(P)
    A = np.asarray(P, float) if n > _VEC else None
    segs: list[Segment] = []
    i = 0
    while i < n - 1:
        # longest straight run
        jl = i + 1
        step = 1
        while jl + step < n and _line_ok(P, i, jl + step, tol, A):
            jl += step
            step *= 2
        while step > 1:
            step //= 2
            if jl + step < n and _line_ok(P, i, jl + step, tol, A):
                jl += step
        # longest arc
        ja, arc = i, None
        j = i + min_arc_points - 1
        if j < n:
            a = _arc_ok(P, i, j, tol, max_radius, max_sweep, A)
            if a is not None:
                ja, arc = j, a
                step = 1
                while ja + step < n:
                    a = _arc_ok(P, i, ja + step, tol, max_radius, max_sweep, A)
                    if a is None:
                        break
                    ja, arc = ja + step, a
                    step *= 2
                while step > 1:
                    step //= 2
                    if ja + step < n:
                        a = _arc_ok(P, i, ja + step, tol, max_radius, max_sweep, A)
                        if a is not None:
                            ja, arc = ja + step, a
        if arc is not None and ja > jl:
            segs.append(arc)
            i = ja
        else:
            if dist(P[i], P[jl]) > 1e-9:
                segs.append(Line(P[i], P[jl]))
            i = jl
    return segs


def fit_contour(pts: Sequence[Pt], closed: bool, tol: float = 0.005, keep_start: bool = False) -> Contour:
    return Contour(fit_segments(pts, closed, tol, keep_start=keep_start), closed)

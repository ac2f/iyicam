"""Contour intersections, sub-contours and trimming."""
from __future__ import annotations

import math
from typing import Iterable

from .geom import Arc, BBox, Contour, Line, Pt, Segment, subsegment

Key = float  # position along a contour: segment index + parameter t


def flatten_params(c: Contour, tol: float = 0.002) -> list[tuple[Pt, float]]:
    """Points along ``c`` with their key (segment index + t)."""
    out: list[tuple[Pt, float]] = []
    for i, s in enumerate(c.segs):
        if i == 0:
            out.append((s.a, 0.0))
        if isinstance(s, Line):
            out.append((s.b, i + 1.0))
        elif isinstance(s, Arc):
            n = s.segment_count(tol)
            for k in range(1, n + 1):
                out.append((s.point_at(k / n), i + k / n))
        else:
            n = max(2, len(s.flatten(tol)))
            for k in range(1, n + 1):
                out.append((s.point_at(k / n), i + k / n))
    return out


def _seg_x(p1: Pt, p2: Pt, q1: Pt, q2: Pt) -> tuple[float, float] | None:
    """Parameters (u, v) where p1-p2 crosses q1-q2."""
    rx, ry = p2[0] - p1[0], p2[1] - p1[1]
    sx, sy = q2[0] - q1[0], q2[1] - q1[1]
    den = rx * sy - ry * sx
    if abs(den) < 1e-15:
        return None
    qx, qy = q1[0] - p1[0], q1[1] - p1[1]
    u = (qx * sy - qy * sx) / den
    v = (qx * ry - qy * rx) / den
    if -1e-9 <= u <= 1 + 1e-9 and -1e-9 <= v <= 1 + 1e-9:
        return u, v
    return None


def contour_cuts(c: Contour, cutters: Iterable[Contour], tol: float = 0.002) -> list[Key]:
    """Sorted keys along ``c`` where it crosses any of ``cutters``."""
    A = flatten_params(c, tol)
    keys: list[Key] = []
    cbb = c.bbox()
    for other in cutters:
        if other is c or not other.segs or not cbb.intersects(other.bbox()):
            continue
        B = other.flatten(tol)
        if other.closed:
            B = B + [B[0]]
        bboxes = [BBox.of_points((B[j], B[j + 1])) for j in range(len(B) - 1)]
        for i in range(len(A) - 1):
            (a1, k1), (a2, k2) = A[i], A[i + 1]
            ab = BBox.of_points((a1, a2))
            for j in range(len(B) - 1):
                if not ab.intersects(bboxes[j]):
                    continue
                hit = _seg_x(a1, a2, B[j], B[j + 1])
                if hit is not None:
                    keys.append(k1 + (k2 - k1) * hit[0])
    n = len(c.segs)
    if c.closed:  # key n is the start point again
        keys = [k - n if k >= n - 1e-9 else k for k in keys]
    keys.sort()
    out: list[Key] = []
    for k in keys:
        if not out or k - out[-1] > 1e-6:
            out.append(k)
    if c.closed and len(out) > 1 and out[0] + n - out[-1] <= 1e-6:
        out.pop()
    return out


def point_at_key(c: Contour, k: Key) -> Pt:
    i = min(len(c.segs) - 1, max(0, int(math.floor(k))))
    return c.segs[i].point_at(min(1.0, max(0.0, k - i)))


def sub_contour(c: Contour, k0: Key, k1: Key) -> Contour:
    """Open contour from key ``k0`` to ``k1`` (wraps around closed contours when k1 < k0)."""
    n = len(c.segs)
    if k1 < k0:
        if not c.closed:
            raise ValueError("k1 < k0 on an open contour")
        k1 += n
    out: list[Segment] = []
    k = k0
    while k < k1 - 1e-12:
        i = int(math.floor(k + 1e-12))
        t0 = k - i
        end = min(k1, i + 1.0)
        t1 = end - i
        seg = c.segs[i % n]
        if t1 - t0 > 1e-12:
            piece = subsegment(seg, t0, t1)
            if piece.length() > 1e-9:
                out.append(piece)
        k = end
    return Contour(out, False)


def trim(c: Contour, pick: Pt, cutters: Iterable[Contour], tol: float = 0.002) -> list[Contour] | None:
    """Remove the piece of ``c`` around ``pick`` that lies between two crossings.

    Returns the remaining pieces, or ``None`` when no crossing bounds the piece.
    """
    cuts = contour_cuts(c, cutters, tol)
    i, t, _, _ = c.nearest(pick)
    kp = i + t
    n = float(len(c.segs))
    if c.closed:
        if len(cuts) < 2:
            return None
        lo = max((k for k in cuts if k < kp), default=None)
        hi = min((k for k in cuts if k > kp), default=None)
        if lo is None:
            lo = cuts[-1]
        if hi is None:
            hi = cuts[0]
        piece = sub_contour(c, hi, lo)
        return [piece] if piece.segs else []
    lo = max((k for k in cuts if k < kp - 1e-9), default=None)
    hi = min((k for k in cuts if k > kp + 1e-9), default=None)
    if lo is None and hi is None:
        return None
    out = []
    if lo is not None and lo > 1e-9:
        out.append(sub_contour(c, 0.0, lo))
    if hi is not None and hi < n - 1e-9:
        out.append(sub_contour(c, hi, n))
    return [p for p in out if p.segs]


def self_intersects(c: Contour, tol: float = 0.01) -> bool:
    """True when a closed contour crosses itself (figure eights, curls)."""
    if not c.closed:
        return False
    import pyclipper
    from .clip import to_int
    pts = to_int(c.flatten(tol))
    if len(pts) < 4:
        return False
    simple = pyclipper.SimplifyPolygon(pts, pyclipper.PFT_NONZERO)
    area = sum(abs(pyclipper.Area(p)) for p in simple)
    return len(simple) > 1 or abs(abs(pyclipper.Area(pts)) - area) > max(1.0, 1e-6 * area)


def nearly_closed(c: Contour, tol: float) -> bool:
    return not c.closed and len(c.segs) > 1 and math.dist(c.start, c.end) <= tol


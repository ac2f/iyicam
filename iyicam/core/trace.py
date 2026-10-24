"""Bitmap to vector tracing (marching squares + simplification + arc fitting)."""
from __future__ import annotations

import numpy as np

from . import arcfit
from .geom import Contour, polygon_area, simplify_polyline

# segments per marching squares case; edges: 0 top, 1 right, 2 bottom, 3 left
_CASES = {1: [(3, 2)], 2: [(2, 1)], 3: [(3, 1)], 4: [(0, 1)], 6: [(0, 2)], 7: [(3, 0)], 8: [(3, 0)],
          9: [(0, 2)], 11: [(0, 1)], 12: [(3, 1)], 13: [(2, 1)], 14: [(3, 2)]}


def trace_polylines(gray: np.ndarray, threshold: float = 128.0, invert: bool = False) -> list[list[tuple[float, float]]]:
    """Closed boundary loops in pixel coordinates (x right, y down) around dark pixels.

    ``gray`` is a 2D array; pixels darker than ``threshold`` are inside (or brighter
    ones with ``invert``).  Positions are interpolated between pixel centres.
    """
    v = np.asarray(gray, dtype=float)
    if invert:
        v = 255.0 - v
        threshold = 255.0 - threshold
    v = np.pad(v, 1, constant_values=max(255.0, threshold + 1.0))  # outside border closes every loop
    inside = v < threshold
    tl, tr = inside[:-1, :-1], inside[:-1, 1:]
    bl, br = inside[1:, :-1], inside[1:, 1:]
    case = tl * 8 + tr * 4 + br * 2 + bl * 1
    ii, jj = np.nonzero((case != 0) & (case != 15))

    def interp(a: float, b: float) -> float:
        d = b - a
        return 0.5 if abs(d) < 1e-12 else min(1.0, max(0.0, (threshold - a) / d))

    def edge_key(i: int, j: int, e: int):
        # horizontal edges ('h', i, j) join pixels (i,j)-(i,j+1); vertical ('v', i, j) join (i,j)-(i+1,j)
        return (("h", i, j), ("v", i, j + 1), ("h", i + 1, j), ("v", i, j))[e]

    points: dict = {}

    def point(key):
        p = points.get(key)
        if p is None:
            kind, i, j = key
            if kind == "h":
                p = (j + interp(v[i, j], v[i, j + 1]) - 1.0, i - 1.0)
            else:
                p = (j - 1.0, i + interp(v[i, j], v[i + 1, j]) - 1.0)
            points[key] = p
        return p

    adj: dict = {}
    for i, j in zip(ii.tolist(), jj.tolist()):
        c = int(case[i, j])
        if c in (5, 10):
            centre_in = (v[i, j] + v[i, j + 1] + v[i + 1, j] + v[i + 1, j + 1]) / 4.0 < threshold
            if c == 5:
                segs = [(3, 0), (2, 1)] if centre_in else [(0, 1), (3, 2)]
            else:
                segs = [(0, 1), (3, 2)] if centre_in else [(3, 0), (2, 1)]
        else:
            segs = _CASES[c]
        for a, b in segs:
            ka, kb = edge_key(i, j, a), edge_key(i, j, b)
            adj.setdefault(ka, []).append(kb)
            adj.setdefault(kb, []).append(ka)

    loops = []
    seen: set = set()
    for start in adj:
        if start in seen:
            continue
        loop = [start]
        seen.add(start)
        prev, cur = None, start
        while True:
            nxt = [k for k in adj[cur] if k != prev and k not in seen]
            if not nxt:
                break
            prev, cur = cur, nxt[0]
            seen.add(cur)
            loop.append(cur)
        if len(loop) >= 3:
            loops.append([point(k) for k in loop])
    return loops


def trace(gray: np.ndarray, threshold: float = 128.0, invert: bool = False, pixel_size: float = 1.0,
          min_area: float = 4.0, smoothing: float = 0.6, origin: tuple[float, float] = (0.0, 0.0)) -> list[Contour]:
    """Trace a grey image into closed contours in millimetres (Y up).

    ``min_area`` (pixels) drops specks, ``smoothing`` (pixels) is the tolerance used
    to simplify the outline and fit lines and arcs.
    """
    h = np.asarray(gray).shape[0]
    out = []
    for loop in trace_polylines(gray, threshold, invert):
        if abs(polygon_area(loop)) < min_area:
            continue
        pts = simplify_polyline(loop + [loop[0]], max(0.05, smoothing * 0.25))[:-1]
        if len(pts) < 3:
            continue
        mm = [(origin[0] + x * pixel_size, origin[1] + (h - 1 - y) * pixel_size) for x, y in pts]
        c = arcfit.fit_contour(mm, True, max(1e-3, 0.4 * smoothing * pixel_size))
        if len(c.segs) >= 2:
            out.append(c)
    return out

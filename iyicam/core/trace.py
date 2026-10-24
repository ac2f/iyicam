"""Bitmap to vector tracing (marching squares + simplification + arc fitting)."""
from __future__ import annotations

import numpy as np

from . import arcfit, clip
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


# ---------------------------------------------------------------------------
# centre lines
# ---------------------------------------------------------------------------

def thin(mask: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Zhang-Suen thinning to one pixel wide lines.

    Also returns the pass that removed each pixel: roughly its distance to the edge
    (the largest value is about the half width of the widest stroke).
    """
    img = np.pad(np.asarray(mask, dtype=bool), 1).astype(np.uint8)
    when = np.zeros(img.shape, np.int32)
    inner = when[1:-1, 1:-1]
    passes = 0
    while True:
        changed = False
        for step in (0, 1):
            c = img[1:-1, 1:-1]
            # neighbours clockwise from north: P2 .. P9
            nb = [img[:-2, 1:-1], img[:-2, 2:], img[1:-1, 2:], img[2:, 2:],
                  img[2:, 1:-1], img[2:, :-2], img[1:-1, :-2], img[:-2, :-2]]
            B = sum(n.astype(np.int8) for n in nb)
            A = sum(((nb[k] == 0) & (nb[(k + 1) % 8] == 1)).astype(np.int8) for k in range(8))
            p2, p4, p6, p8 = nb[0], nb[2], nb[4], nb[6]
            if step == 0:
                side = ((p2 & p4 & p6) == 0) & ((p4 & p6 & p8) == 0)
            else:
                side = ((p2 & p4 & p8) == 0) & ((p2 & p6 & p8) == 0)
            kill = (c == 1) & (B >= 2) & (B <= 6) & (A == 1) & side
            if kill.any():
                c[kill] = 0
                inner[kill] = passes + 1
                changed = True
        if not changed:
            break
        passes += 1
    return img[1:-1, 1:-1].astype(bool), inner.copy()


def skeleton_paths(sk: np.ndarray, spur: float = 0.0, cap: float = 0.0,
                   radius: np.ndarray | None = None) -> list[tuple[list[tuple[float, float]], bool]]:
    """Polylines ``(points, closed)`` along a one pixel wide skeleton, pixel (x, y) coordinates.

    Junction pixels next to each other are merged into one node. The two branches a
    stroke end forks into (towards its corners, each shorter than ``cap``) are
    removed, so are other branches shorter than ``spur`` hanging off a junction, and
    the remaining pieces are joined through nodes that connect just two of them. With
    ``radius`` (stroke half width per pixel) free ends are cut back where the line
    runs off into a corner of the stroke.
    """
    S = set(zip(*(a.tolist() for a in np.nonzero(sk))))

    def nbrs(p):
        i, j = p
        out = [q for q in ((i - 1, j), (i + 1, j), (i, j - 1), (i, j + 1)) if q in S]
        for di in (-1, 1):
            for dj in (-1, 1):
                q = (i + di, j + dj)
                # a diagonal step is redundant when the corner pixel joins the two already
                if q in S and (i + di, j) not in S and (i, j + dj) not in S:
                    out.append(q)
        return out

    adj = {p: nbrs(p) for p in S}
    nodes = {p for p, n in adj.items() if len(n) != 2}
    parent = {p: p for p in nodes}

    def find(p):
        while parent[p] != p:
            parent[p] = parent[parent[p]]
            p = parent[p]
        return p

    for p in nodes:
        for di in (-1, 0, 1):
            for dj in (-1, 0, 1):
                q = (p[0] + di, p[1] + dj)
                if q in nodes:
                    parent[find(q)] = find(p)
    members: dict = {}
    for p in nodes:
        members.setdefault(find(p), []).append(p)
    centre = {k: (sum(q[1] for q in v) / len(v), sum(q[0] for q in v) / len(v)) for k, v in members.items()}

    def xy(p):
        return (float(p[1]), float(p[0]))

    edges = []  # [cluster a, cluster b, points]
    seen = set()
    for n in nodes:
        for q in adj[n]:
            if (n, q) in seen:
                continue
            seen.add((n, q))
            seen.add((q, n))
            path = [n, q]
            prev, cur = n, q
            while cur not in nodes:
                nxt = adj[cur][0] if adj[cur][0] != prev else adj[cur][1]
                seen.add((cur, nxt))
                seen.add((nxt, cur))
                path.append(nxt)
                prev, cur = cur, nxt
            a, b = find(n), find(cur)
            if a == b and len(path) <= 3:
                continue  # inside a junction cluster
            edges.append([a, b, [centre[a]] + [xy(p) for p in path[1:-1]] + [centre[b]]])
    loops = []
    for p in S - nodes:  # rings without any junction
        if (p, adj[p][0]) in seen:
            continue
        path = [p]
        prev, cur = p, adj[p][0]
        seen.add((p, cur))
        seen.add((cur, p))
        while cur != p:
            path.append(cur)
            nxt = adj[cur][0] if adj[cur][0] != prev else adj[cur][1]
            seen.add((cur, nxt))
            seen.add((nxt, cur))
            prev, cur = cur, nxt
        loops.append(([xy(q) for q in path], True))

    def length(pts):
        return sum(np.hypot(b[0] - a[0], b[1] - a[1]) for a, b in zip(pts, pts[1:]))

    def degrees():
        d: dict = {}
        for a, b, _ in edges:
            d[a] = d.get(a, 0) + 1
            d[b] = d.get(b, 0) + 1
        return d

    if cap > 0:
        deg = degrees()
        drop = set()
        for c in [c for c, d in deg.items() if d == 3]:
            inc = [e for e in edges if c in (e[0], e[1])]
            if len(inc) != 3:
                continue
            ends = [e for e in inc if e[0] != e[1] and deg[e[1] if e[0] == c else e[0]] == 1
                    and length(e[2]) < cap]
            trunk = [e for e in inc if all(e is not x for x in ends)]
            if len(ends) == 2 and length(trunk[0][2]) > max(length(e[2]) for e in ends):
                drop.update(id(e) for e in ends)
        edges = [e for e in edges if id(e) not in drop]
    if spur > 0:
        deg = degrees()
        edges = [e for e in edges if not ((deg[e[0]] == 1) != (deg[e[1]] == 1) and length(e[2]) < spur)]
    # join pieces through nodes with exactly two ends
    closed = []
    while True:
        deg = degrees()
        k = next((c for c, d in deg.items() if d == 2 and not any(e[0] == e[1] == c for e in edges)), None)
        if k is None:
            break
        e1, e2 = [e for e in edges if k in (e[0], e[1])][:2]
        p1 = e1[2] if e1[1] == k else e1[2][::-1]
        a = e1[0] if e1[1] == k else e1[1]
        p2 = e2[2] if e2[0] == k else e2[2][::-1]
        b = e2[1] if e2[0] == k else e2[0]
        edges = [e for e in edges if e is not e1 and e is not e2]
        if a == k == b:
            continue
        edges.append([a, b, p1 + p2[1:]])
    deg = degrees()
    for a, b, pts in edges:
        ring = a == b and deg.get(a) == 2
        if not ring and radius is not None:
            if deg[b] == 1:
                pts = _trim_end(pts, radius)
            if deg[a] == 1:
                pts = _trim_end(pts[::-1], radius)[::-1]
        (closed if ring else loops).append((pts[:-1] if ring else pts, ring))
    return loops + closed


def _trim_end(pts: list, radius: np.ndarray) -> list:
    """Drop the end of a line where the stroke around it gets much thinner than along
    the line: that is the skeleton turning off into a corner."""
    h, w = radius.shape

    def rad(p):
        return radius[min(h - 1, max(0, int(round(p[1])))), min(w - 1, max(0, int(round(p[0]))))]

    ref = float(np.median([rad(p) for p in pts]))
    k, cut = len(pts) - 1, 0.0
    while k > 1 and rad(pts[k]) < 0.6 * ref and cut < 3 * ref:
        cut += float(np.hypot(pts[k][0] - pts[k - 1][0], pts[k][1] - pts[k - 1][1]))
        k -= 1
    pts = pts[:k + 1]
    # a short hook may remain: compare the end with the course of the line before it
    P = np.asarray(pts, float)
    back = np.concatenate([[0.0], np.cumsum(np.hypot(*np.diff(P[::-1], axis=0).T))])[::-1]  # length to the end
    L = max(ref, 1.0)
    if back[0] < 7 * L:
        return pts
    o = P[np.searchsorted(-back, -2.5 * L)]
    d = o - P[np.searchsorted(-back, -5 * L)]
    d /= np.hypot(*d)
    n = np.array([-d[1], d[0]])
    u, v = (P - o) @ d, (P - o) @ n
    body = (back >= 2.5 * L) & (back <= 7 * L)
    a, b, c = np.polyfit(u[body], v[body], 2)
    tail = np.nonzero(back < 2.5 * L)[0]
    off = v[tail] - np.polyval((a, b, c), u[tail])
    curved = abs(a) * (2.5 * L) ** 2 > 0.2 * L
    # turning harder than the line did before (or off a straight line) is a hook;
    # straightening out (the end of a curved stroke) is not
    hook = (np.abs(off) > 0.2 * L) & ((off * a > 0) | (not curved))
    if not hook.any():
        return pts
    k = int(tail[np.argmax(hook)]) - 1
    tan = np.array([1.0, 2 * a * u[k] + b])
    tan = (tan[0] * d + tan[1] * n) / np.hypot(*tan)
    return pts[:k + 1] + [tuple(P[k] + tan * max(0.0, float((P[-1] - P[k]) @ tan) - ref))]


def centerline(gray: np.ndarray, threshold: float = 128.0, invert: bool = False, pixel_size: float = 1.0,
               smoothing: float = 0.8, min_length: float = 4.0, spur: float = 0.0,
               origin: tuple[float, float] = (0.0, 0.0)) -> list[Contour]:
    """Single lines along the middle of the dark strokes of an image, in millimetres (Y up).

    ``spur`` (pixels, 0 = automatic) removes short side branches such as the ones
    the corners of a stroke grow; ``min_length`` (pixels) drops specks.
    """
    v = np.asarray(gray, dtype=float)
    ink = v > threshold if invert else v < threshold
    sk, when = thin(ink)
    passes = int(when.max())
    pad = np.pad(when, 1)
    rad = np.max([pad[1 + di:pad.shape[0] - 1 + di, 1 + dj:pad.shape[1] - 1 + dj]
                  for di in (-1, 0, 1) for dj in (-1, 0, 1)], axis=0)
    paths = skeleton_paths(sk, spur or 1.6 * passes + 2, 4.0 * passes + 4, rad)
    return _to_contours(paths, v.shape[0], pixel_size, smoothing, min_length, origin)


def _to_contours(paths, h: int, px: float, smoothing: float, min_length: float, origin) -> list[Contour]:
    out = []
    for pts, closed in paths:
        P = np.asarray(pts, float)
        if len(P) < 2 or np.hypot(*np.diff(P, axis=0).T).sum() < min_length:
            continue
        if len(P) >= 5:  # soften the pixel staircase, keep the ends where they are
            Q = P.copy()
            idx = slice(None) if closed else slice(1, -1)
            Q[idx] = ((np.roll(P, 1, 0) + 2 * P + np.roll(P, -1, 0)) / 4)[idx]
            P = Q
        pts = [tuple(p) for p in P]
        if closed:
            pts = simplify_polyline(pts + [pts[0]], smoothing * 0.5)[:-1]
            if len(pts) < 3:
                continue
        else:
            pts = simplify_polyline(pts, smoothing * 0.5)
        mm = [(origin[0] + x * px, origin[1] + (h - 1 - y) * px) for x, y in pts]
        c = arcfit.fit_contour(mm, closed, max(1e-3, 0.5 * smoothing * px))
        if c.segs:
            out.append(c)
    return out


def rasterize(contours: list[Contour], pixel_size: float, margin: int = 2) -> tuple[np.ndarray, tuple[float, float]]:
    """Even-odd fill of closed contours: ``(mask, origin)``, row 0 at the top, origin = lower left."""
    polys = [c.flatten(pixel_size / 4) for c in contours if c.closed]
    pts = [p for poly in polys for p in poly]
    if not pts:
        return np.zeros((1, 1), bool), (0.0, 0.0)
    xs, ys = [p[0] for p in pts], [p[1] for p in pts]
    x0, y0 = min(xs) - margin * pixel_size, min(ys) - margin * pixel_size
    nx = int(np.ceil((max(xs) - x0) / pixel_size)) + margin + 1
    ny = int(np.ceil((max(ys) - y0) / pixel_size)) + margin + 1
    E = np.array([(a[0], a[1], b[0], b[1]) for poly in polys for a, b in zip(poly, poly[1:] + poly[:1])])
    mask = np.zeros((ny, nx), bool)
    for r in range(ny):
        y = y0 + (ny - 1 - r) * pixel_size  # pixel centre of this row
        hit = (E[:, 1] <= y) != (E[:, 3] <= y)
        if not hit.any():
            continue
        e = E[hit]
        cx = np.sort(e[:, 0] + (y - e[:, 1]) * (e[:, 2] - e[:, 0]) / (e[:, 3] - e[:, 1]))
        for a, b in zip(cx[::2], cx[1::2]):
            j0, j1 = int(np.ceil((a - x0) / pixel_size)), int(np.floor((b - x0) / pixel_size))
            if j1 >= j0:
                mask[r, j0:j1 + 1] ^= True
    return mask, (x0, y0)


def contour_centerlines(contours: list[Contour], pixel_size: float = 0.0, smoothing: float = 0.8,
                        spur: float = 0.0) -> list[Contour]:
    """Single lines along the middle of the regions of closed contours (e.g. text outlines)."""
    if pixel_size <= 0:
        bbs = [c.bbox() for c in contours if c.closed]
        if not bbs:
            return []
        size = max(max(b.xmax for b in bbs) - min(b.xmin for b in bbs), max(b.ymax for b in bbs) - min(b.ymin for b in bbs))
        # strokes are about 2 * area / perimeter wide: give them some 40 pixels
        area = abs(clip.area(clip.normalize(clip.contours_to_polys([c for c in contours if c.closed]))))
        width = 2 * area / max(1e-9, sum(c.length() for c in contours if c.closed))
        pixel_size = max(width / 40, size / 4000, 0.005)
    mask, origin = rasterize(contours, pixel_size)
    gray = np.where(mask, 0.0, 255.0)
    out = centerline(gray, 128.0, False, pixel_size, smoothing, 4.0, spur, origin)
    # small round regions (dots of i and j, full stops) thin away: mark them with a short line
    boxes = [c.bbox() for c in out]
    closed = [c for c in contours if c.closed]
    polys = [c.flatten(pixel_size) for c in closed]
    for i, c in enumerate(closed):
        if sum(clip.point_in(polys[i][0], q) == 1 for k, q in enumerate(polys) if k != i) % 2:
            continue  # a hole
        bb = c.bbox()
        if not any(b.xmin >= bb.xmin and b.xmax <= bb.xmax and b.ymin >= bb.ymin and b.ymax <= bb.ymax
                   for b in boxes):
            cx, cy = bb.center
            h = min(bb.width, bb.height) / 4
            out.append(Contour.polyline([(cx - h, cy), (cx + h, cy)], False))
    return out

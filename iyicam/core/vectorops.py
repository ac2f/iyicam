"""Vector editing operations working on :class:`~iyicam.core.geom.Contour` lists.

Every function is pure: inputs are left untouched and new contours are returned.
"""
from __future__ import annotations

import math
from typing import Iterable, Sequence

from . import arcfit, clip
from .geom import (Arc, Bezier, Contour, Line, Pt, Segment, add, cross, dist, dot, lerp, mul, perp,
                   same, simplify_polyline, sub, unit)

FLAT_TOL = 0.002
FIT_TOL = 0.005


# ---------------------------------------------------------------------------
# regions / offsets / booleans
# ---------------------------------------------------------------------------

def region(contours: Iterable[Contour], tol: float = FLAT_TOL) -> list[clip.Poly]:
    """Even-odd region of the closed contours (outer CCW, holes CW)."""
    return clip.normalize(clip.contours_to_polys(contours, tol), "evenodd")


def polys_to_contours(polys: Iterable[Sequence[Pt]], fit_tol: float = FIT_TOL) -> list[Contour]:
    return [arcfit.fit_contour(p, True, fit_tol) for p in polys if len(p) >= 3]


def offset(contours: Sequence[Contour], distance: float, join: str = "round",
           tol: float = FLAT_TOL, fit_tol: float = FIT_TOL) -> list[Contour]:
    """Offset closed contours as a region (positive = outwards).

    Open contours are turned into a closed outline at ``|distance|``.
    """
    out: list[Contour] = []
    closed = [c for c in contours if c.closed]
    opened = [c for c in contours if not c.closed]
    if closed:
        out += polys_to_contours(clip.offset(region(closed, tol), distance, join), fit_tol)
    if opened and abs(distance) > 0:
        polys = clip.offset_lines([c.flatten(tol) for c in opened], abs(distance), "round", join)
        out += polys_to_contours(clip.normalize(polys, "nonzero"), fit_tol)
    return out


def boolean(a: Sequence[Contour], b: Sequence[Contour], op: str,
            tol: float = FLAT_TOL, fit_tol: float = FIT_TOL) -> list[Contour]:
    """``op`` in union / difference / intersection / xor between two regions."""
    return polys_to_contours(clip.boolean(region(a, tol), region(b, tol), op), fit_tol)


def weld(groups: Sequence[Sequence[Contour]], tol: float = FLAT_TOL, fit_tol: float = FIT_TOL) -> list[Contour]:
    """Union of several objects (each object is its own even-odd region)."""
    polys: list[clip.Poly] = []
    for g in groups:
        polys += region(g, tol)
    return polys_to_contours(clip.union(polys, "nonzero"), fit_tol)


# ---------------------------------------------------------------------------
# joining / closing
# ---------------------------------------------------------------------------

def _snap_start(c: Contour, p: Pt) -> Contour:
    segs = list(c.segs)
    segs[0] = segs[0].with_ends(p, segs[0].b)
    return Contour(segs, c.closed)


def close(c: Contour, tol: float = 1e-3) -> Contour:
    """Close an open contour (snapping the end if it is already near the start)."""
    if c.closed or not c.segs:
        return c
    segs = list(c.segs)
    if same(c.start, c.end, tol):
        if len(segs) == 1 and isinstance(segs[0], Line):
            return c
        segs[-1] = segs[-1].with_ends(segs[-1].a, segs[0].a)
    else:
        segs.append(Line(c.end, c.start))
    return Contour(segs, True)


def join(contours: Sequence[Contour], tol: float = 0.01, close_tol: float | None = None) -> list[Contour]:
    """Chain open contours whose end points meet (within ``tol``) into longer ones."""
    close_tol = tol if close_tol is None else close_tol
    result = [c for c in contours if c.closed]
    pending = [c for c in contours if not c.closed and c.segs]
    if not pending:
        return result
    cell = max(tol, 1e-6) * 2

    def key(p: Pt) -> tuple[int, int]:
        return (math.floor(p[0] / cell), math.floor(p[1] / cell))

    grid: dict[tuple[int, int], set[int]] = {}

    def index(i: int) -> None:
        for p in (pending[i].start, pending[i].end):
            grid.setdefault(key(p), set()).add(i)

    def unindex(i: int) -> None:
        for p in (pending[i].start, pending[i].end):
            grid.get(key(p), set()).discard(i)

    def find(p: Pt, used: list[bool]) -> tuple[int, bool] | None:
        kx, ky = key(p)
        best = None
        for dx in (-1, 0, 1):
            for dy in (-1, 0, 1):
                for i in grid.get((kx + dx, ky + dy), ()):
                    if used[i]:
                        continue
                    c = pending[i]
                    ds, de = dist(p, c.start), dist(p, c.end)
                    if ds <= tol and (best is None or ds < best[2]):
                        best = (i, False, ds)
                    if de <= tol and (best is None or de < best[2]):
                        best = (i, True, de)
        return None if best is None else (best[0], best[1])

    for i in range(len(pending)):
        index(i)
    used = [False] * len(pending)
    for i in range(len(pending)):
        if used[i]:
            continue
        used[i] = True
        unindex(i)
        segs = list(pending[i].segs)
        # grow forward
        while True:
            f = find(segs[-1].b, used)
            if f is None:
                break
            j, rev = f
            used[j] = True
            unindex(j)
            nxt = pending[j].reversed() if rev else pending[j]
            nxt = _snap_start(nxt, segs[-1].b)
            segs += nxt.segs
        # grow backward
        while True:
            f = find(segs[0].a, used)
            if f is None:
                break
            j, rev = f
            used[j] = True
            unindex(j)
            prv = pending[j] if rev else pending[j].reversed()
            pseg = list(prv.segs)
            pseg[-1] = pseg[-1].with_ends(pseg[-1].a, segs[0].a)
            segs = pseg + segs
        c = Contour(segs, False)
        if len(segs) > 1 and same(c.start, c.end, close_tol):
            c = close(c, close_tol)
        result.append(c)
    return result


def explode(c: Contour) -> list[Contour]:
    return [Contour([s], False) for s in c.segs]


# ---------------------------------------------------------------------------
# corner treatments
# ---------------------------------------------------------------------------

def _corner_lines(c: Contour) -> list[int]:
    """Indices of nodes joining two line segments (node i = start of segs[i])."""
    n = len(c.segs)
    idx = []
    for i in range(n):
        if i == 0 and not c.closed:
            continue
        if isinstance(c.segs[i - 1], Line) and isinstance(c.segs[i], Line):
            idx.append(i)
    return idx


def _rebuild_corners(c: Contour, corners: dict[int, tuple[Pt, Pt, list[Segment]]]) -> Contour:
    """Replace node ``i`` by ``q1 -> q2`` via ``extra`` segments (all lines around are trimmed)."""
    n = len(c.segs)
    starts = [s.a for s in c.segs]
    ends = [s.b for s in c.segs]
    for i, (q1, q2, _) in corners.items():
        ends[(i - 1) % n] = q1
        starts[i] = q2
    out: list[Segment] = []
    for i in range(n):
        if i in corners:
            out.extend(corners[i][2])
        s = c.segs[i]
        if not same(starts[i], ends[i], 1e-9):
            out.append(s.with_ends(starts[i], ends[i]))
    if c.closed and 0 in corners:
        # corner at the start node: rotate so the corner pieces end the contour
        k = len(corners[0][2])
        out = out[k:] + out[:k]
    return Contour(out, c.closed)


def fillet(c: Contour, radius: float, nodes: Iterable[int] | None = None) -> Contour:
    """Round line-line corners with tangent arcs of ``radius``."""
    if radius <= 0:
        return c
    cand = _corner_lines(c)
    if nodes is not None:
        wanted = set(nodes)
        cand = [i for i in cand if i in wanted]
    n = len(c.segs)
    corners = {}
    # a line can be shared by two corners: allow each to use at most half of it
    for i in cand:
        sin, sout = c.segs[i - 1], c.segs[i]
        v = sout.a
        d1 = unit(sub(sin.a, v))
        d2 = unit(sub(sout.b, v))
        cosang = max(-1.0, min(1.0, dot(d1, d2)))
        ang = math.acos(cosang)
        if ang < 1e-3 or ang > math.pi - 1e-3:
            continue
        t = radius / math.tan(ang / 2)
        lim_in = sin.length() * (0.5 if (i - 1) % n in cand else 1.0)
        lim_out = sout.length() * (0.5 if (i + 1) % n in cand else 1.0)
        if t > lim_in + 1e-9 or t > lim_out + 1e-9:
            continue
        q1 = add(v, mul(d1, t))
        q2 = add(v, mul(d2, t))
        turn = cross(sub(v, sin.a), sub(sout.b, v))
        sweep = math.pi - ang
        bulge = math.tan(sweep / 4) * (1 if turn > 0 else -1)
        corners[i] = (q1, q2, [Arc(q1, q2, bulge)])
    return _rebuild_corners(c, corners)


def chamfer(c: Contour, distance: float, nodes: Iterable[int] | None = None) -> Contour:
    """Cut line-line corners by ``distance`` along each side."""
    if distance <= 0:
        return c
    cand = _corner_lines(c)
    if nodes is not None:
        wanted = set(nodes)
        cand = [i for i in cand if i in wanted]
    n = len(c.segs)
    corners = {}
    for i in cand:
        sin, sout = c.segs[i - 1], c.segs[i]
        v = sout.a
        lim_in = sin.length() * (0.5 if (i - 1) % n in cand else 1.0)
        lim_out = sout.length() * (0.5 if (i + 1) % n in cand else 1.0)
        if distance > lim_in + 1e-9 or distance > lim_out + 1e-9:
            continue
        if abs(cross(unit(sub(v, sin.a)), unit(sub(sout.b, v)))) < 1e-6:
            continue
        q1 = add(v, mul(unit(sub(sin.a, v)), distance))
        q2 = add(v, mul(unit(sub(sout.b, v)), distance))
        corners[i] = (q1, q2, [Line(q1, q2)])
    return _rebuild_corners(c, corners)


def dogbone(c: Contour, tool_diameter: float, cut: str = "inside", style: str = "dogbone",
            clearance: float = 0.05, min_angle: float = 10.0, max_angle: float = 170.0) -> Contour:
    """Add dog-bone / T-bone relief to corners a round tool cannot reach.

    ``cut`` is ``inside`` (pockets, slots, holes: relieve convex corners of the
    contour) or ``outside`` (parts, tenons: relieve concave corners).  The relief
    circle is ``clearance`` larger than the tool so the tool really fits in it.
    """
    if not c.closed or tool_diameter <= 0:
        return c
    ccw = c.is_ccw()
    work = c if ccw else c.reversed()
    r = tool_diameter / 2
    rr = r + max(0.0, clearance)
    tool_left = cut == "inside"
    corners = {}
    for i in _corner_lines(work):
        sin, sout = work.segs[i - 1], work.segs[i]
        v = sout.a
        da, db = unit(sub(v, sin.a)), unit(sub(sout.b, v))
        turn = cross(da, db)
        interior = math.degrees(math.pi - math.acos(max(-1.0, min(1.0, dot(da, db)))))
        if abs(turn) < 1e-9 or not (min_angle <= interior <= max_angle):
            continue
        if (turn > 0) != tool_left:
            continue
        na = perp(da) if tool_left else mul(perp(da), -1)
        nb = perp(db) if tool_left else mul(perp(db), -1)
        back = mul(da, -1)  # from v towards the previous node
        # the tool centre that just touches the corner point
        u = nb if style == "tbone" else unit(add(na, nb))
        centre = add(v, mul(u, r))

        def hit(d: Pt) -> float:
            # distance along direction d from v to the relief circle (centre, rr)
            k = r * dot(d, u)
            return k + math.sqrt(max(0.0, k * k + rr * rr - r * r))

        s1, s2 = hit(back), hit(db)
        if s1 > sin.length() - 1e-6 or s2 > sout.length() - 1e-6:
            continue
        q1 = add(v, mul(back, s1))
        q2 = add(v, mul(db, s2))
        far = add(centre, mul(na if style == "tbone" else u, -rr))
        corners[i] = (q1, q2, [Arc.through(q1, far, q2)])
    out = _rebuild_corners(work, corners)
    return out if ccw else out.reversed()


# ---------------------------------------------------------------------------
# smoothing
# ---------------------------------------------------------------------------

def smooth(c: Contour, tol: float = 0.02) -> Contour:
    """Replace the contour by fitted lines and arcs (ArtCAM 'smooth / fit arcs')."""
    return arcfit.fit_contour(c.flatten(tol / 4), c.closed, tol, keep_start=True)


def simplify(c: Contour, tol: float = 0.05) -> Contour:
    pts = c.flatten(tol / 4)
    if c.closed:
        pts = pts + [pts[0]]
    pts = simplify_polyline(pts, tol)
    return Contour.polyline(pts, c.closed)


def to_polyline(c: Contour, tol: float = 0.01) -> Contour:
    return Contour.polyline(c.flatten(tol), c.closed)


# ---------------------------------------------------------------------------
# node editing
# ---------------------------------------------------------------------------

def node_count(c: Contour) -> int:
    return len(c.segs) if c.closed else len(c.segs) + 1


def move_node(c: Contour, node: int, p: Pt) -> Contour:
    segs = list(c.segs)
    n = len(segs)
    if node < n:
        segs[node] = segs[node].with_ends(p, segs[node].b)
    if c.closed or node > 0:
        k = (node - 1) % n
        segs[k] = segs[k].with_ends(segs[k].a, p)
    return Contour(segs, c.closed)


def move_control(c: Contour, seg: int, which: int, p: Pt) -> Contour:
    s = c.segs[seg]
    if not isinstance(s, Bezier):
        return c
    segs = list(c.segs)
    segs[seg] = Bezier(s.a, p, s.c2, s.b) if which == 1 else Bezier(s.a, s.c1, p, s.b)
    return Contour(segs, c.closed)


def insert_node(c: Contour, seg: int, t: float) -> Contour:
    segs = list(c.segs)
    s1, s2 = segs[seg].split(t)
    segs[seg:seg + 1] = [s1, s2]
    return Contour(segs, c.closed)


def delete_node(c: Contour, node: int) -> Contour:
    segs = list(c.segs)
    n = len(segs)
    if not c.closed:
        if n <= 1:
            return c
        if node == 0:
            return Contour(segs[1:], False)
        if node >= n:
            return Contour(segs[:-1], False)
        merged = Line(segs[node - 1].a, segs[node].b)
        segs[node - 1:node + 1] = [merged]
        return Contour(segs, False)
    if n <= 3:
        return c
    prev = (node - 1) % n
    merged = Line(segs[prev].a, segs[node].b)
    if node == 0:
        segs = segs[1:-1] + [merged]
    else:
        segs[prev:node + 1] = [merged]
    return Contour(segs, True)


def convert_segment(c: Contour, seg: int, kind: str) -> Contour:
    s = c.segs[seg]
    segs = list(c.segs)
    if kind == "line":
        new: list[Segment] = [Line(s.a, s.b)]
    elif kind == "arc":
        if isinstance(s, Arc):
            return c
        if isinstance(s, Bezier):
            new = [Arc.through(s.a, s.point_at(0.5), s.b)]
        else:
            m = lerp(s.a, s.b, 0.5)
            h = dist(s.a, s.b) * 0.2
            new = [Arc.through(s.a, add(m, mul(perp(unit(sub(s.b, s.a))), -h)), s.b)]
    elif kind == "bezier":
        if isinstance(s, Bezier):
            return c
        new = list(s.to_beziers()) if isinstance(s, Arc) else [
            Bezier(s.a, lerp(s.a, s.b, 1 / 3), lerp(s.a, s.b, 2 / 3), s.b)]
    else:
        raise ValueError(kind)
    segs[seg:seg + 1] = new
    return Contour(segs, c.closed)


def set_start_node(c: Contour, node: int) -> Contour:
    if not c.closed:
        return c
    node %= len(c.segs)
    return Contour(c.segs[node:] + c.segs[:node], True)


def set_start_point(c: Contour, p: Pt) -> Contour:
    """Move the start of a closed contour to the closest point to ``p``."""
    if not c.closed:
        return c
    i, t, q, _ = c.nearest(p)
    seg = c.segs[i]
    if t <= 1e-6 or dist(q, seg.a) < 1e-4:
        return set_start_node(c, i)
    if t >= 1 - 1e-6 or dist(q, seg.b) < 1e-4:
        return set_start_node(c, i + 1)
    c2 = insert_node(c, i, t)
    return set_start_node(c2, i + 1)


def break_at(c: Contour, seg: int, t: float) -> list[Contour]:
    """Cut the contour at ``(seg, t)``; a closed contour becomes one open contour."""
    segs = list(c.segs)
    if 1e-9 < t < 1 - 1e-9:
        s1, s2 = segs[seg].split(t)
        segs[seg:seg + 1] = [s1, s2]
        cut = seg + 1
    else:
        cut = seg + (1 if t >= 0.5 else 0)
    if c.closed:
        return [Contour(segs[cut:] + segs[:cut], False)]
    out = []
    if segs[:cut]:
        out.append(Contour(segs[:cut], False))
    if segs[cut:]:
        out.append(Contour(segs[cut:], False))
    return out


def reverse(c: Contour) -> Contour:
    return c.reversed()


# ---------------------------------------------------------------------------------------------------------
# the same vector twice
# ---------------------------------------------------------------------------------------------------------

def _near_polyline(points, poly, closed: bool, tol: float) -> bool:
    """Whether every point lies within ``tol`` of the polyline."""
    import numpy as np
    A = np.asarray(poly[:-1] if closed and len(poly) > 1 and poly[0] == poly[-1] else poly, float)
    B = np.roll(A, -1, axis=0) if closed else A[1:]
    A = A if closed else A[:-1]
    if len(A) == 0:
        return True
    AB = B - A
    L2 = np.maximum((AB * AB).sum(axis=1), 1e-18)
    P = np.asarray(points, float)
    t = np.clip(((P[:, None, :] - A) * AB).sum(axis=2) / L2, 0.0, 1.0)
    d = np.sqrt(((P[:, None, :] - (A + t[..., None] * AB)) ** 2).sum(axis=2)).min(axis=1)
    return bool((d <= tol).all())


def _sample(contour: Contour, n: int = 32) -> list[Pt]:
    """Points spread over the contour (its nodes and ``n`` points along it)."""
    pts = list(contour.nodes())
    L = contour.length()
    if L > 0:
        pts += [contour.point_at_length(L * k / n) for k in range(n)]
    return pts


def same_contour(a: Contour, b: Contour, tol: float = 0.05) -> bool:
    """Whether two contours are the same line in the same place (whatever their start and direction)."""
    if a.closed != b.closed or abs(a.length() - b.length()) > tol * 4 + 1e-4 * a.length():
        return False
    ba, bb = a.bbox(), b.bbox()
    if max(abs(ba.xmin - bb.xmin), abs(ba.ymin - bb.ymin), abs(ba.xmax - bb.xmax), abs(ba.ymax - bb.ymax)) > tol * 2:
        return False
    pa, pb = a.flatten(tol / 4), b.flatten(tol / 4)
    return (_near_polyline(_sample(a), pb, b.closed, tol) and _near_polyline(_sample(b), pa, a.closed, tol))


def same_vector(a, b, tol: float = 0.05) -> bool:
    """Whether two vectors are made of the same contours in the same place."""
    if len(a.contours) != len(b.contours):
        return False
    left = list(b.contours)
    for c in a.contours:
        k = next((i for i, d in enumerate(left) if same_contour(c, d, tol)), None)
        if k is None:
            return False
        del left[k]
    return True


def find_duplicates(objects: Sequence, tol: float = 0.05) -> list[list]:
    """Groups of vectors that are the same shape in the same place (a common fault of drawings): each group
    has the first of them and the others."""
    from collections import defaultdict
    cell = 20.0
    grid: dict[tuple[int, int], list[int]] = defaultdict(list)
    boxes = []
    for i, o in enumerate(objects):
        bb = o.bbox()
        boxes.append(bb)
        grid[(int((bb.xmin + bb.xmax) / 2 // cell), int((bb.ymin + bb.ymax) / 2 // cell))].append(i)
    parent = list(range(len(objects)))

    def find(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    for i, o in enumerate(objects):
        if not o.contours:
            continue
        cx, cy = int((boxes[i].xmin + boxes[i].xmax) / 2 // cell), int((boxes[i].ymin + boxes[i].ymax) / 2 // cell)
        for dx in (-1, 0, 1):
            for dy in (-1, 0, 1):
                for j in grid.get((cx + dx, cy + dy), ()):
                    if j > i and find(i) != find(j) and same_vector(o, objects[j], tol):
                        parent[find(j)] = find(i)
    groups: dict[int, list] = defaultdict(list)
    for i, o in enumerate(objects):
        groups[find(i)].append(o)
    return [g for g in groups.values() if len(g) > 1]

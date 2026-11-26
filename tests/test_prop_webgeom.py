"""The web app's geometry (geom.js, run by node) against the engine's (Hypothesis): the same node editing of the same contour
gives the same contour, the box and the nodes are the same, and a flattened contour lies on the engine's."""
import json
import math
import shutil
import subprocess
from pathlib import Path

import pytest
from hypothesis import HealthCheck, given, settings, strategies as st

from iyicam.core import vectorops as V
from iyicam.core.document import contour_from_dict, contour_to_dict

from test_prop_geom import contour

NODE = shutil.which("node")
pytestmark = pytest.mark.skipif(NODE is None, reason="node is not installed")
SET = settings(max_examples=150, deadline=None, suppress_health_check=list(HealthCheck))


class JS:
    def __init__(self):
        here = Path(__file__).resolve().parent / "webapp" / "geom_oracle.mjs"
        self.p = subprocess.Popen([NODE, str(here)], stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True, bufsize=1)

    def __call__(self, fn, *args):
        self.p.stdin.write(json.dumps({"fn": fn, "args": list(args)}) + "\n")
        out = json.loads(self.p.stdout.readline())
        if "error" in out:
            raise AssertionError(f"geom.js {fn}: {out['error']}")
        return out["ok"]


@pytest.fixture(scope="module")
def js():
    j = JS()
    yield j
    j.p.stdin.close()
    j.p.wait(5)


def same(a, b, tol=1e-6):
    """Two contours of the project JSON alike (numbers within tol)."""
    if a is None or b is None:
        return a is b
    if a["closed"] != b["closed"] or len(a["segs"]) != len(b["segs"]):
        return False
    if any(abs(x - y) > tol * max(1, abs(x)) for x, y in zip(a["start"], b["start"])):
        return False
    for s, t in zip(a["segs"], b["segs"]):
        if s[0] != t[0] or len(s) != len(t) or any(abs(x - y) > tol * max(1, abs(x)) for x, y in zip(s[1:], t[1:])):
            return False
    return True


def d(c):
    return json.loads(json.dumps(contour_to_dict(c)))


@SET
@given(contour())
def test_the_box_and_the_nodes_are_the_engines(js, c):
    bb = js("contourBBox", d(c))
    eb = c.bbox()
    got = (bb["x0"], bb["y0"], bb["x1"], bb["y1"]) if isinstance(bb, dict) and "x0" in bb else tuple(bb)
    assert all(abs(x - y) < 1e-6 * max(1, abs(y)) for x, y in zip(got, (eb.xmin, eb.ymin, eb.xmax, eb.ymax))), (got, eb)
    nodes = js("nodes", d(c))
    want = [s.a for s in c.segs] + ([] if c.closed else [c.segs[-1].b])
    assert len(nodes) == len(want) and all(math.dist(p, q) < 1e-6 for p, q in zip(nodes, want))


@SET
@given(contour(), st.data())
def test_a_node_put_in_is_the_engines(js, c, data):
    i = data.draw(st.integers(0, len(c.segs) - 1))
    u = data.draw(st.floats(0.05, 0.95))
    assert same(js("insertNode", d(c), i, u), d(V.insert_node(c, i, u)), 1e-6)


@SET
@given(contour(), st.data())
def test_a_node_taken_out_is_the_engines(js, c, data):
    k = data.draw(st.integers(0, V.node_count(c) - 1))
    got = js("deleteNode", d(c), k)
    want = d(V.delete_node(c, k))
    if got is None:                                   # (the web says "too little would be left": the engine leaves it)
        assert want == d(c)
    else:
        assert same(got, want), (got, want)


@SET
@given(contour(closed=True), st.data())
def test_a_new_start_node_is_the_engines(js, c, data):
    k = data.draw(st.integers(0, len(c.segs) - 1))
    assert same(js("setStart", d(c), k), d(V.set_start_node(c, k)))


@SET
@given(contour(), st.data())
def test_a_contour_broken_at_a_node_is_the_engines(js, c, data):
    k = data.draw(st.integers(0, V.node_count(c) - 1))
    got = js("breakAt", d(c), k)
    want = [d(x) for x in V.break_at(c, k, 0.0)] if k < len(c.segs) else [d(x) for x in V.break_at(c, len(c.segs) - 1, 1.0)]
    assert len(got) == len(want) and all(same(a, b) for a, b in zip(got, want)), (k, got, want)


@SET
@given(contour(closed=False))
def test_closing_is_the_engines(js, c):
    assert same(js("closeContour", d(c)), d(V.close(c, 1e-9)))


@SET
@given(contour(), st.data())
def test_a_span_made_a_line_is_the_engines(js, c, data):
    i = data.draw(st.integers(0, len(c.segs) - 1))
    assert same(js("convertSpan", d(c), i, "L"), d(V.convert_segment(c, i, "line")))


@SET
@given(contour())
def test_the_flattened_contour_lies_on_the_engines(js, c):
    pts = js("flatten", d(c))
    eng = c.flatten(0.001)
    def dist(p):
        best = math.inf
        for a, b in zip(eng, eng[1:]):
            dx, dy = b[0] - a[0], b[1] - a[1]
            L = dx * dx + dy * dy
            t = 0 if L == 0 else max(0.0, min(1.0, ((p[0] - a[0]) * dx + (p[1] - a[1]) * dy) / L))
            best = min(best, math.dist(p, (a[0] + t * dx, a[1] + t * dy)))
        return best
    assert math.dist(pts[0], c.start) < 1e-6 and math.dist(pts[-1], c.segs[-1].b) < 1e-6
    assert max(dist(p) for p in pts) < 0.01


# ---------------------------------------------------------------------------------------------- what the page itself uses
from iyicam.core import clip, shapes
from iyicam.core.geom import Contour, Transform

coord = st.integers(-20, 20).map(float)             # (whole numbers: windows and lines that meet exactly, as with snapping)


def _meets(a, b, c, e):
    """Whether the closed segments a-b and c-e have a point in common (exact on whole numbers)."""
    o = lambda p, q, r: (q[0] - p[0]) * (r[1] - p[1]) - (q[1] - p[1]) * (r[0] - p[0])
    on = lambda p, q, r: min(p[0], q[0]) <= r[0] <= max(p[0], q[0]) and min(p[1], q[1]) <= r[1] <= max(p[1], q[1])
    d1, d2, d3, d4 = o(c, e, a), o(c, e, b), o(a, b, c), o(a, b, e)
    if ((d1 > 0) != (d2 > 0)) and d1 and d2 and ((d3 > 0) != (d4 > 0)) and d3 and d4:
        return True
    return (d1 == 0 and on(c, e, a)) or (d2 == 0 and on(c, e, b)) or (d3 == 0 and on(a, b, c)) or (d4 == 0 and on(a, b, e))


@SET
@given(st.lists(st.tuples(coord, coord), min_size=1, max_size=6), st.booleans(), coord, coord, coord, coord)
def test_a_crossing_window_takes_what_touches_it(js, pts, closed, x0, y0, x1, y1):
    """A crossing takes a vector when any point of its line is in the window, its edges included: a line through two
    corners of the window, or along one edge, is taken too (a window snapped onto a line takes it)."""
    r = {"x0": min(x0, x1), "y0": min(y0, y1), "x1": max(x0, x1), "y1": max(y0, y1)}
    pts = [list(p) for p in pts]
    got = js("inRect", [{"pts": pts, "closed": closed}], r, True)
    inside = lambda p: r["x0"] <= p[0] <= r["x1"] and r["y0"] <= p[1] <= r["y1"]
    corners = [(r["x0"], r["y0"]), (r["x1"], r["y0"]), (r["x1"], r["y1"]), (r["x0"], r["y1"])]
    line = pts + ([pts[0]] if closed and len(pts) > 2 else [])
    want = any(inside(p) for p in pts) or any(_meets(p, q, corners[k], corners[(k + 1) % 4])
                                              for p, q in zip(line, line[1:]) for k in range(4))
    assert got == want, (pts, closed, r)


def test_a_line_through_two_corners_of_a_crossing_is_taken(js):
    r = {"x0": 0, "y0": 0, "x1": 10, "y1": 10}
    assert js("inRect", [{"pts": [[-5, -5], [15, 15]], "closed": False}], r, True)          # (the diagonal)
    assert js("inRect", [{"pts": [[-5, 10], [15, 10]], "closed": False}], r, True)          # (along the top edge)
    assert not js("inRect", [{"pts": [[-5, 11], [15, 11]], "closed": False}], r, True)
    assert not js("inRect", [{"pts": [[-5, -5], [15, 15]], "closed": False}], r, False)     # (a window: wholly inside only)


@SET
@given(contour(), st.data())
def test_a_point_along_a_segment_is_the_engines(js, c, data):
    i = data.draw(st.integers(0, len(c.segs) - 1))
    u = data.draw(st.floats(0, 1))
    dc = d(c)
    p0 = js("segStart", dc, i)
    got = js("evalSeg", p0, dc["segs"][i], u)
    assert math.dist(got, c.segs[i].point_at(u)) < 1e-6 * max(1, abs(got[0]), abs(got[1]))
    a, b = js("splitSeg", p0, dc["segs"][i], u)
    e1, e2 = c.segs[i].split(u)
    for s, e in ((a, e1), (b, e2)):
        if s[0] != "L" or e.kind == "line":
            assert same({"closed": False, "start": [0, 0], "segs": [s]},
                        {"closed": False, "start": [0, 0], "segs": [d(Contour([e], False))["segs"][0]]}, 1e-5)


@SET
@given(contour(), st.floats(-math.pi, math.pi), st.floats(0.2, 5), st.booleans(), st.floats(-500, 500), st.floats(-500, 500))
def test_a_turn_a_scale_and_a_flip_are_the_engines(js, c, ang, k, flip, dx, dy):
    t = Transform.rotate(ang, (dx, dy)) @ Transform.scale(k, k, (3.0, -7.0)) if hasattr(Transform, "__matmul__") else None
    m = js("rotateMap", math.degrees(ang), dx, dy)
    v = {"id": "x", "layer": "l", "name": "", "contours": [d(c)], "locked_starts": []}
    got = js("affine", v, m)["contours"][0]
    assert same(got, d(c.transformed(Transform.rotate(ang, (dx, dy)))), 1e-6)
    m = js("scaleMap", k, dx, dy)
    assert same(js("affine", v, m)["contours"][0], d(c.transformed(Transform.scale(k, k, (dx, dy)))), 1e-6)
    m = js("flipHMap", dx) if flip else js("flipVMap", dy)
    want = c.transformed(Transform.mirror(True, (dx, 0.0)) if flip else Transform.mirror(False, (0.0, dy)))
    assert same(js("affine", v, m)["contours"][0], d(want), 1e-6)
    del t


@SET
@given(st.floats(-500, 500), st.floats(-500, 500), st.floats(0.5, 400), st.floats(0.5, 400), st.integers(3, 12),
       st.floats(-360, 360))
def test_the_shapes_are_the_engines(js, cx, cy, r, r2, n, rot):
    assert same(js("circle", cx, cy, r), d(shapes.circle((cx, cy), r)))
    assert same(js("ellipse", cx, cy, r, r2), d(shapes.ellipse((cx, cy), r, r2)))
    assert same(js("polygon", cx, cy, r, n, rot), d(shapes.polygon((cx, cy), r, n, rot)), 1e-6)
    assert same(js("star", cx, cy, r, r2, n, rot), d(shapes.star((cx, cy), r, r2, n, rot)), 1e-6)


@SET
@given(contour(), st.floats(-600, 600), st.floats(-600, 600))
def test_the_nearest_point_is_the_engines(js, c, x, y):
    q = js("nearest", d(c), x, y)                      # (both search every valley of the distance: the same point)
    _, _, pt, dist_ = c.nearest((x, y))
    assert abs(q["d"] - math.dist(q["pt"], (x, y))) < 1e-6
    assert q["d"] <= dist_ + 1e-6 * max(1, dist_) and q["d"] >= dist_ - 1e-6 * max(1, dist_), (q, pt, dist_)


@SET
@given(st.tuples(st.floats(-100, 100), st.floats(-100, 100)), st.tuples(st.floats(-100, 100), st.floats(-100, 100)),
       st.tuples(st.floats(-100, 100), st.floats(-100, 100)))
def test_the_arc_through_three_points_passes_them(js, a, b, c):
    out = js("arc3", list(a), list(b), list(c))
    if out is None:
        return
    seg = contour_from_dict(out["contour"]).segs[0]
    if seg.kind != "arc":
        return
    r = seg.radius
    assert math.dist(seg.a, a) < 1e-6 and math.dist(seg.b, c) < 1e-6
    assert seg.distance_to(b) < 1e-6 * max(1, r)


@SET
@given(st.sampled_from(["line", "rect", "ellipse", "polygon", "circle"]), st.tuples(coord, coord), st.tuples(coord, coord),
       st.booleans(), st.booleans())
def test_a_drag_with_the_keys_draws_what_they_say(js, tool, a, b, ctrl, shift):
    out = js("dragShape", tool, list(a), list(b), {"ctrl": ctrl, "shift": shift})
    p, q = out["a"], out["b"]
    dx, dy = q[0] - a[0], q[1] - a[1]
    if ctrl and tool in ("line", "polygon"):
        ang = math.degrees(math.atan2(dy, dx)) if math.hypot(dx, dy) > 1e-9 else 0.0
        assert abs(ang / 15 - round(ang / 15)) < 1e-6 and abs(math.hypot(dx, dy) - math.dist(a, b)) < 1e-6
    if ctrl and tool in ("rect", "ellipse"):
        assert abs(abs(dx) - abs(dy)) < 1e-9
    if shift and tool in ("line", "rect", "ellipse"):
        assert math.dist(((p[0] + q[0]) / 2, (p[1] + q[1]) / 2), a) < 1e-9          # (drawn from the centre)
    else:
        assert p == list(a)
    if not ctrl:
        assert q == list(b)


@SET
@given(st.lists(contour(), min_size=1, max_size=5), st.floats(-300, 300), st.floats(-300, 300), st.floats(0.1, 50))
def test_a_tap_takes_the_nearest_vector_and_alt_walks_through_the_rest(js, cs, x, y, tol):
    vs = [{"id": f"v{i}", "layer": "l", "name": "", "contours": [d(c)], "locked_starts": []} for i, c in enumerate(cs)]
    flat = [js("flatten", v["contours"][0]) for v in vs]
    def dist(pts, closed):
        line = pts + ([pts[0]] if closed else [])
        best = math.inf
        for a, b in zip(line, line[1:]):
            dx, dy = b[0] - a[0], b[1] - a[1]
            L = dx * dx + dy * dy
            t = 0 if L == 0 else max(0.0, min(1.0, ((x - a[0]) * dx + (y - a[1]) * dy) / L))
            best = min(best, math.dist((x, y), (a[0] + t * dx, a[1] + t * dy)))
        return best
    ds = [dist(f, c.closed) for f, c in zip(flat, cs)]
    want = sorted((i for i in range(len(vs)) if ds[i] < tol), key=lambda i: (ds[i], -i))
    allv = js("hitAll", vs, x, y, tol)
    assert allv == [f"v{i}" for i in want]
    assert js("hit", vs, x, y, tol) == (allv[0] if allv else None)
    seen, cur = [], None
    for _ in range(len(allv)):
        cur = js("nextAfter", allv, cur)
        seen.append(cur)
    assert sorted(seen) == sorted(allv)                              # (tapping again reaches every one of them)


@SET
@given(contour(closed=True), st.floats(-300, 300), st.floats(-300, 300))
def test_inside_is_the_engines(js, c, x, y):
    pts = js("flatten", d(c))
    if c.nearest((x, y))[3] < 0.05:
        return                                                       # (on the line: either answer)
    from iyicam.core.geom import point_in_polygon
    assert js("inside", pts, x, y) == point_in_polygon((x, y), pts)

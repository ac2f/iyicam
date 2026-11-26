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

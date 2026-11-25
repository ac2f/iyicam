"""Properties of the node editing of vectors (Hypothesis): every edit leaves a contour whose segments join end to end, and
the edits that should not change the shape do not."""
import math

from hypothesis import given, settings, strategies as st

from iyicam.core import vectorops as V
from iyicam.core.geom import polygon_area

from test_prop_geom import contour, near, pt

SET = settings(max_examples=200, deadline=None)


def joined(c, tol=1e-6):
    for s, q in zip(c.segs, c.segs[1:]):
        assert near(s.b, q.a, tol), (s, q)
    if c.closed and c.segs:
        assert near(c.segs[-1].b, c.segs[0].a, tol)


def length(c):
    return sum(s.length() for s in c.segs)


@SET
@given(contour(), st.data())
def test_a_node_put_in_keeps_the_shape(c, data):
    i = data.draw(st.integers(0, len(c.segs) - 1))
    t = data.draw(st.floats(0.05, 0.95))
    d = V.insert_node(c, i, t)
    joined(d)
    assert V.node_count(d) == V.node_count(c) + 1
    assert math.isclose(length(d), length(c), rel_tol=1e-4, abs_tol=1e-4)


@SET
@given(contour(), st.data())
def test_a_node_taken_out_leaves_it_joined(c, data):
    n = V.node_count(c)
    node = data.draw(st.integers(0, n - 1))
    d = V.delete_node(c, node)
    joined(d)
    assert d.closed == c.closed
    assert V.node_count(d) in (V.node_count(c), V.node_count(c) - 1)


@SET
@given(contour(), st.data(), pt)
def test_a_node_moved_moves_only_its_ends(c, data, p):
    node = data.draw(st.integers(0, V.node_count(c) - 1))
    d = V.move_node(c, node, p)
    joined(d)
    pts = [s.a for s in d.segs] + ([] if d.closed else [d.segs[-1].b])
    assert near(pts[node], p)


@SET
@given(contour(), st.data(), st.sampled_from(["line", "arc", "bezier"]))
def test_a_segment_of_another_kind_keeps_its_ends(c, data, kind):
    i = data.draw(st.integers(0, len(c.segs) - 1))
    d = V.convert_segment(c, i, kind)
    joined(d, 1e-5)
    assert near(d.segs[0].a, c.segs[0].a, 1e-5) and near(d.segs[-1].b, c.segs[-1].b, 1e-5)


@SET
@given(contour(closed=True), pt)
def test_moving_the_start_of_a_closed_contour_keeps_its_shape(c, p):
    d = V.set_start_point(c, p)
    joined(d)
    assert d.closed
    assert math.isclose(length(d), length(c), rel_tol=1e-4, abs_tol=1e-4)
    a, b = polygon_area(c.flatten(0.01)), polygon_area(d.flatten(0.01))
    assert abs(a - b) <= 2 * 0.01 * length(c) + 1e-6        # (each is within 0.01 mm of the curve all round)
    i, t, q, _ = c.nearest(p)
    assert near(d.segs[0].a, q, 1e-4) or any(near(d.segs[0].a, s.a, 1e-4) for s in c.segs)


@SET
@given(contour(), st.data())
def test_a_contour_cut_in_two_is_as_long_as_it_was(c, data):
    i = data.draw(st.integers(0, len(c.segs) - 1))
    t = data.draw(st.floats(0.0, 1.0))
    parts = V.break_at(c, i, t)
    assert parts and all(not x.closed for x in parts)
    for x in parts:
        joined(x)
    assert math.isclose(sum(length(x) for x in parts), length(c), rel_tol=1e-4, abs_tol=1e-4)
    if c.closed:
        assert len(parts) == 1


@SET
@given(contour(closed=True), st.floats(0.01, 0.1))
def test_simplify_and_smooth_keep_a_closed_contour_near_itself(c, tol):
    for f in (V.simplify, V.smooth):
        d = f(c, tol)
        joined(d, 1e-4)
        a, b = abs(polygon_area(c.flatten(0.01))), abs(polygon_area(d.flatten(0.01)))
        L = length(c)
        assert abs(a - b) <= tol * L * 2 + 1e-3, (f.__name__, a, b)

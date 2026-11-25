"""Properties of the geometry core (Hypothesis): transforms, segments and contours over their whole input domain."""
import math

import pytest
from hypothesis import assume, example, given, settings, strategies as st

from iyicam.core.geom import Arc, BBox, Bezier, Contour, Line, Transform, make_arc, polygon_area

coord = st.floats(min_value=-1000, max_value=1000, allow_nan=False, allow_infinity=False)
pt = st.tuples(coord, coord)
angle = st.floats(min_value=-2 * math.pi, max_value=2 * math.pi, allow_nan=False)
bulge = st.floats(min_value=-3.0, max_value=3.0, allow_nan=False).filter(lambda b: abs(b) > 1e-3)
SET = settings(max_examples=200, deadline=None)


@st.composite
def rigid(draw):
    """A rotation, maybe a mirror, and a move."""
    T = Transform.rotate(draw(angle), draw(pt))
    if draw(st.booleans()):
        T = Transform.mirror(draw(st.booleans()), draw(pt)) @ T
    return Transform.translate(*draw(pt)) @ T


@st.composite
def segment(draw, start=None):
    a = start if start is not None else draw(pt)
    b = draw(pt.filter(lambda p: math.dist(p, a) > 1e-2))
    kind = draw(st.sampled_from(["line", "arc", "bezier"]))
    if kind == "line":
        return Line(a, b)
    if kind == "arc":
        return make_arc(a, b, draw(bulge))
    return Bezier(a, draw(pt), draw(pt), b)


@st.composite
def contour(draw, closed=None):
    n = draw(st.integers(min_value=1, max_value=6))
    segs = [draw(segment())]
    for _ in range(n - 1):
        segs.append(draw(segment(segs[-1].b)))
    closed = draw(st.booleans()) if closed is None else closed
    if closed and math.dist(segs[-1].b, segs[0].a) > 1e-2:
        segs.append(Line(segs[-1].b, segs[0].a))
    return Contour(segs, closed and len(segs) > 1)


def near(p, q, tol=1e-6):
    return math.dist(p, q) <= tol * max(1.0, abs(p[0]), abs(p[1]), abs(q[0]), abs(q[1]))


# -- transforms ------------------------------------------------------------------------------------------------------

@SET
@given(rigid(), pt)
def test_a_transform_and_its_inverse_give_the_point_back(T, p):
    assert near(T.inverse().apply(T.apply(p)), p, 1e-9)


@SET
@given(rigid(), rigid(), pt)
def test_composing_transforms_is_applying_them_in_turn(A, B, p):
    assert near((A @ B).apply(p), A.apply(B.apply(p)), 1e-9)


@SET
@given(rigid(), pt, pt)
def test_a_rigid_transform_keeps_distances(T, p, q):
    assert math.isclose(math.dist(T.apply(p), T.apply(q)), math.dist(p, q), rel_tol=1e-9, abs_tol=1e-6)
    assert T.is_conformal() and math.isclose(abs(T.det), 1.0, rel_tol=1e-9)


# -- segments --------------------------------------------------------------------------------------------------------

@SET
@given(segment())
def test_a_segment_reversed_twice_is_itself_and_reversed_once_swaps_its_ends(s):
    r = s.reversed()
    assert near(r.a, s.b) and near(r.b, s.a)
    rr = r.reversed()
    for t in (0.0, 0.3, 0.7, 1.0):
        assert near(rr.point_at(t), s.point_at(t), 1e-7)
    assert math.isclose(r.length(), s.length(), rel_tol=1e-6, abs_tol=1e-6)


@SET
@given(segment())
@example(make_arc((0.0, 0.0), (10.0, 0.0), 1.0))         # (a half circle)
@example(make_arc((0.0, 0.0), (10.0, 0.0), -2.5))        # (more than half, clockwise)
def test_the_box_of_a_segment_holds_every_point_of_it(s):
    bb = s.bbox().inflated(1e-6 * (1 + max(abs(v) for v in (*s.a, *s.b))))
    for k in range(21):
        assert bb.contains(s.point_at(k / 20))
    for p in s.flatten(0.01):
        assert bb.contains(p)


@SET
@given(segment())
def test_flattening_ends_at_the_end_and_is_never_longer(s):
    pts = [s.a] + s.flatten(0.01)                       # (a segment's flatten gives the points after its start)
    assert near(pts[-1], s.b) and not near(pts[1], s.a, 1e-12) if len(pts) > 1 else True
    poly = sum(math.dist(pts[i], pts[i + 1]) for i in range(len(pts) - 1))
    assert poly <= s.length() * (1 + 1e-6) + 1e-6
    assert poly >= s.length() * 0.97 - 0.05             # (a chord is shorter, but not by much at 0.01 mm)


@SET
@given(segment(), rigid())
def test_a_moved_segment_runs_where_its_points_are_moved_to(s, T):
    out = s.transformed(T)
    assert near(out[0].a, T.apply(s.a), 1e-7) and near(out[-1].b, T.apply(s.b), 1e-7)
    total = sum(x.length() for x in out)
    assert math.isclose(total, s.length(), rel_tol=1e-5, abs_tol=1e-5)


@SET
@given(st.tuples(coord, coord), st.tuples(coord, coord), bulge)
def test_an_arc_has_the_sweep_of_its_bulge(a, b, k):
    assume(math.dist(a, b) > 1e-2)
    s = make_arc(a, b, k)
    assert isinstance(s, Arc)
    assert math.isclose(s.sweep, 4 * math.atan(k), rel_tol=1e-9, abs_tol=1e-9)
    assert math.isclose(math.dist(s.center, a), s.radius, rel_tol=1e-9) and math.isclose(math.dist(s.center, b), s.radius, rel_tol=1e-9)


# -- contours --------------------------------------------------------------------------------------------------------

@SET
@given(contour(closed=True), rigid())
def test_a_rigid_transform_keeps_the_area_of_a_closed_contour(c, T):
    a = polygon_area(c.flatten(0.01))
    b = polygon_area(c.transformed(T).flatten(0.01))
    assert math.isclose(abs(a), abs(b), rel_tol=1e-3, abs_tol=0.05)
    assert (a > 0) == (b > 0) or abs(a) < 0.05 or T.det < 0 and (a > 0) != (b > 0)


@SET
@given(contour())
def test_a_contour_reversed_twice_is_itself(c):
    r = c.reversed().reversed()
    assert len(r.segs) == len(c.segs) and r.closed == c.closed
    for s, q in zip(c.segs, r.segs):
        assert near(s.a, q.a) and near(s.b, q.b)


@SET
@given(contour())
def test_the_box_of_a_contour_holds_its_points(c):
    bb = c.bbox().inflated(1e-6 * 1000)
    for p in c.flatten(0.01):
        assert bb.contains(p)


@SET
@given(contour())
def test_a_contour_copy_is_not_shared(c):
    d = c.copy()
    d.transformed(Transform.translate(5, 5))
    assert [s.a for s in d.segs] == [s.a for s in c.segs]

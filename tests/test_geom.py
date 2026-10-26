import math

import pytest

from iyicam.core import arcfit, clip, shapes, vectorops
from iyicam.core.geom import Arc, BBox, Bezier, Contour, Line, Transform, dist, same


def approx_pt(a, b, tol=1e-6):
    return dist(a, b) <= tol


def test_arc_from_bulge_center_and_radius():
    a = Arc((1, 0), (0, 1), math.tan(math.pi / 8))
    assert approx_pt(a.center, (0, 0))
    assert a.radius == pytest.approx(1.0)
    assert a.sweep == pytest.approx(math.pi / 2)
    assert a.length() == pytest.approx(math.pi / 2)
    assert approx_pt(a.point_at(0.5), (math.cos(math.pi / 4), math.sin(math.pi / 4)))


def test_arc_bbox_includes_extremes():
    a = Arc((1, 0), (-1, 0), 1.0)  # upper half circle, CCW
    bb = a.bbox()
    assert bb.ymax == pytest.approx(1.0)
    assert bb.ymin == pytest.approx(0.0, abs=1e-12)
    cw = Arc((1, 0), (-1, 0), -1.0)  # lower half
    assert cw.bbox().ymin == pytest.approx(-1.0)


def test_arc_through_three_points_and_split():
    a = Arc.through((10, 0), (0, 10), (-10, 0))
    assert approx_pt(a.center, (0, 0))
    assert a.ccw
    s1, s2 = a.split(0.25)
    assert s1.length() + s2.length() == pytest.approx(a.length())
    assert approx_pt(s1.b, s2.a)


def test_arc_nonuniform_scale_becomes_beziers():
    c = shapes.circle((0, 0), 10)
    e = c.transformed(Transform.scale(2, 1))
    assert all(isinstance(s, Bezier) for s in e.segs)
    bb = e.bbox()
    assert bb.width == pytest.approx(40, abs=1e-3)
    assert bb.height == pytest.approx(20, abs=1e-3)


def test_mirror_flips_arc_direction():
    c = shapes.circle((5, 5), 3)
    assert c.is_ccw()
    m = c.transformed(Transform.mirror(True, (0, 0)))
    assert not m.is_ccw()
    assert m.area() == pytest.approx(math.pi * 9)


def test_transform_compose_and_inverse():
    T = Transform.rotate(0.7, (3, 4)) @ Transform.scale(2, 3) @ Transform.translate(1, -2)
    p = (5.5, -1.25)
    assert approx_pt(T.inverse().apply(T.apply(p)), p)


def test_bezier_length_and_bbox():
    b = shapes.ellipse((0, 0), 10, 10).segs[0]
    assert b.length() == pytest.approx(math.pi * 5, rel=1e-3)
    bb = shapes.ellipse((0, 0), 20, 10).bbox()
    assert bb.width == pytest.approx(40, abs=1e-6)


def test_contour_area_orientation():
    r = shapes.rectangle(0, 0, 40, 20)
    assert r.signed_area() == pytest.approx(800)
    assert r.reversed().signed_area() == pytest.approx(-800)
    rr = shapes.rectangle(0, 0, 40, 20, corner_radius=5)
    assert rr.area() == pytest.approx(800 - (4 - math.pi) * 25)
    assert rr.is_closed_geometrically()


def test_contour_contains_and_nearest():
    c = shapes.circle((0, 0), 10)
    assert c.contains((1, 1))
    assert not c.contains((11, 0))
    i, t, q, d = c.nearest((20, 0))
    assert d == pytest.approx(10)


def test_offset_square_outward_has_round_corners():
    sq = shapes.rectangle(0, 0, 50, 30)
    out = vectorops.offset([sq], 3)
    assert len(out) == 1
    c = out[0]
    assert sum(isinstance(s, Arc) for s in c.segs) == 4
    assert c.area() == pytest.approx(50 * 30 + 2 * 3 * (50 + 30) + math.pi * 9, rel=1e-4)


def test_offset_inward_with_hole_region():
    outer = shapes.rectangle(0, 0, 100, 100)
    hole = shapes.circle((50, 50), 10)
    res = vectorops.offset([outer, hole], -5)
    areas = sorted(c.area() for c in res)
    assert len(res) == 2
    assert areas[0] == pytest.approx(math.pi * 15 ** 2, rel=1e-3)


def test_arcfit_recovers_circle():
    pts = shapes.circle((0, 0), 25).flatten(0.001)
    segs = arcfit.fit_segments(pts, True, 0.005)
    arcs = [s for s in segs if isinstance(s, Arc)]
    assert len(segs) <= 4
    for a in arcs:
        assert a.radius == pytest.approx(25, abs=0.005)


def test_arcfit_keeps_polygon_lines():
    pts = shapes.polygon((0, 0), 20, 6).flatten()
    segs = arcfit.fit_segments(pts, True, 0.005)
    assert len(segs) == 6
    assert all(isinstance(s, Line) for s in segs)


def test_boolean_and_weld():
    a = shapes.rectangle(0, 0, 20, 20)
    b = shapes.rectangle(10, 10, 20, 20)
    u = vectorops.weld([[a], [b]])
    assert len(u) == 1
    assert u[0].area() == pytest.approx(700)
    d = vectorops.boolean([a], [b], "difference")
    assert sum(c.area() for c in d) == pytest.approx(300)
    i = vectorops.boolean([a], [b], "intersection")
    assert i[0].area() == pytest.approx(100)


def test_join_chains_separate_lines_into_closed_contour():
    pts = [(0, 0), (10, 0), (10, 10), (0, 10)]
    lines = [Contour([Line(pts[i], pts[(i + 1) % 4])], False) for i in range(4)]
    lines[1] = lines[1].reversed()  # one segment drawn backwards
    lines[3] = Contour([Line((0, 10), (0.004, 0.003))], False)  # small gap
    out = vectorops.join(lines, tol=0.01)
    assert len(out) == 1
    assert out[0].closed
    assert out[0].area() == pytest.approx(100, abs=0.1)


def test_fillet_and_chamfer_square():
    sq = shapes.rectangle(0, 0, 20, 20)
    f = vectorops.fillet(sq, 2)
    assert f.closed and f.is_closed_geometrically()
    assert sum(isinstance(s, Arc) for s in f.segs) == 4
    assert f.area() == pytest.approx(400 - (4 - math.pi) * 4)
    ch = vectorops.chamfer(sq, 2)
    assert ch.area() == pytest.approx(400 - 4 * 2)
    assert len(ch.segs) == 8


def test_dogbone_inside_adds_relief_at_each_corner():
    sq = shapes.rectangle(0, 0, 30, 20)
    d = vectorops.dogbone(sq, 6, cut="inside")
    assert d.is_closed_geometrically()
    assert sum(isinstance(s, Arc) for s in d.segs) == 4
    assert d.area() > sq.area()
    # every original corner is now reachable by the 6 mm tool: the inward offset reaches it
    reach = vectorops.offset([d], -3)
    assert reach
    for corner in [(0, 0), (30, 0), (30, 20), (0, 20)]:
        best = min(c.nearest(corner)[3] for c in reach)
        assert best == pytest.approx(3 - 0.05, abs=0.02)


def test_tbone_and_outside_dogbone():
    sq = shapes.rectangle(0, 0, 30, 20)
    t = vectorops.dogbone(sq, 6, cut="inside", style="tbone")
    assert sum(isinstance(s, Arc) for s in t.segs) == 4
    assert t.is_closed_geometrically()
    # L-shape has one concave corner -> one relief for outside cutting
    L = Contour.polyline([(0, 0), (40, 0), (40, 10), (10, 10), (10, 40), (0, 40)], True)
    o = vectorops.dogbone(L, 6, cut="outside")
    assert sum(isinstance(s, Arc) for s in o.segs) == 1
    assert o.area() < L.area()


def test_node_editing_roundtrip():
    sq = shapes.rectangle(0, 0, 10, 10)
    m = vectorops.move_node(sq, 0, (-1, -1))
    assert same(m.segs[0].a, (-1, -1)) and same(m.segs[-1].b, (-1, -1))
    ins = vectorops.insert_node(sq, 0, 0.5)
    assert len(ins.segs) == 5
    dl = vectorops.delete_node(ins, 1)
    assert len(dl.segs) == 4
    assert dl.area() == pytest.approx(100)
    arc = vectorops.convert_segment(sq, 0, "arc")
    assert isinstance(arc.segs[0], Arc)
    assert arc.area() > 100  # bulges outward
    st = vectorops.set_start_point(sq, (10, 5))
    assert same(st.start, (10, 5))
    assert st.area() == pytest.approx(100)
    br = vectorops.break_at(sq, 2, 0.5)
    assert len(br) == 1 and not br[0].closed
    assert br[0].length() == pytest.approx(40)


def test_shapes():
    assert shapes.star((0, 0), 10, 5, 5).area() > 0
    assert shapes.polygon((0, 0), 10, 4).area() == pytest.approx(200)
    assert shapes.slot((0, 0), (20, 0), 10).area() == pytest.approx(200 + math.pi * 25)
    a = shapes.arc((0, 0), 5, 0, 270)
    assert a.length() == pytest.approx(5 * 1.5 * math.pi)
    rp = shapes.polygon((0, 0), 10, 6, corner_radius=1)
    assert rp.is_closed_geometrically()


def test_clip_lines_inside_polygon():
    sq = [(0, 0), (10, 0), (10, 10), (0, 10)]
    out = clip.clip_lines([[(-5, 5), (15, 5)]], [sq])
    assert len(out) == 1
    xs = sorted(p[0] for p in out[0])
    assert xs[0] == pytest.approx(0) and xs[-1] == pytest.approx(10)


def test_bbox_ops():
    bb = BBox.of_points([(0, 0), (2, 3)])
    assert bb.width == 2 and bb.height == 3
    assert bb.contains((1, 1))
    assert not BBox().intersects(bb)


def test_polygons_with_no_area_are_nothing_not_an_error():
    """Paths that look like polygons but are slits (a spike A-B-C-B, points on a line, one point repeated)
    used to make Clipper raise "All paths are invalid for clipping" and take a whole command down."""
    import random

    from iyicam.core import clip
    from iyicam.core.geom import polygon_area
    slits = [[(0, 0), (3, 1), (1, 2), (3, 1)], [(0, 0), (5, 0), (10, 0)], [(1, 1)] * 5,
             [(0, 0), (10, 1), (0, 0), (10, 1)], [(0, 0.0003), (0.0002, 0), (0, 0), (0.0001, 0), (0.0002, 0)]]
    square = [(0, 0), (10, 0), (10, 10), (0, 10)]
    for slit in slits:
        assert clip.normalize([slit]) == []
        clip.offset([slit], 1.0)                        # (a slit offset by something is a thin shape or nothing)
        assert clip.boolean([slit], [square], "union") != [] and clip.boolean([slit], [square], "intersection") == []
        assert clip.boolean([square], [slit], "difference") != []
        assert abs(sum(polygon_area(p) for p in clip.normalize([square, slit])) - 100) < 1e-6
    rnd = random.Random(1)              # and polygons made of few coordinates, most of them degenerate
    for _ in range(3000):
        k = rnd.choice([1e-4, 1.0])
        poly = [(rnd.randint(0, 3) * k, rnd.randint(0, 3) * k) for _ in range(rnd.randint(3, 6))]
        clip.normalize([poly])
        clip.offset([poly], 0.5)
        clip.boolean([poly], [square], "difference")

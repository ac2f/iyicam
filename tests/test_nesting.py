import itertools
import math

import pytest
from conftest import make_project

from iyicam.core import clip, shapes
from iyicam.core.geom import Contour, polygon_area
from iyicam.nesting.nest import NestSettings, NestShape, nest
from iyicam.nesting.parts import group_parts, nest_objects


def placed_polys(shapes_, res):
    out = []
    for pl in res.placements:
        c, s = math.cos(math.radians(pl.angle)), math.sin(math.radians(pl.angle))
        for poly in shapes_[pl.shape].polys:
            out.append((pl.sheet, [(x * c - y * s + pl.x, x * s + y * c + pl.y) for x, y in poly]))
    return out


def assert_valid(shapes_, res, st: NestSettings):
    polys = placed_polys(shapes_, res)
    for sheet, poly in polys:
        for x, y in poly:
            assert st.margin - 1e-3 <= x <= st.sheet_width - st.margin + 1e-3
            assert st.margin - 1e-3 <= y <= st.sheet_height - st.margin + 1e-3
    shrink = st.spacing / 2 - 0.05  # parts must keep the spacing between them
    for (s1, a), (s2, b) in itertools.combinations(polys, 2):
        if s1 != s2:
            continue
        ga = clip.offset([a], shrink)
        gb = clip.offset([b], shrink)
        inter = clip.boolean(ga, gb, "intersection")
        assert sum(abs(polygon_area(p)) for p in inter) < 1e-3


def rect_poly(w, h):
    return [(0, 0), (w, 0), (w, h), (0, h)]


def test_rectangles_fit_on_one_sheet_without_overlap():
    st = NestSettings(1000, 1000, margin=10, spacing=10, rotations=[0, 90])
    shapes_ = [NestShape([rect_poly(300, 200)], quantity=10)]
    res = nest(shapes_, st)
    assert res.sheets == 1 and not res.unplaced and len(res.placements) == 10
    assert_valid(shapes_, res, st)
    assert res.utilization[0] == pytest.approx(10 * 300 * 200 / 1e6)


def test_irregular_parts_interlock_and_rotate():
    st = NestSettings(600, 400, margin=5, spacing=4, rotations=[0, 90, 180, 270])
    L = [(0, 0), (200, 0), (200, 60), (60, 60), (60, 200), (0, 200)]
    tri = [(0, 0), (150, 0), (0, 150)]
    circ = shapes.circle((0, 0), 40).flatten(0.2)
    shapes_ = [NestShape([L], 3), NestShape([tri], 4), NestShape([circ], 3)]
    res = nest(shapes_, st)
    assert not res.unplaced
    assert_valid(shapes_, res, st)


def test_overflow_creates_more_sheets_and_reports_unplaced():
    st = NestSettings(500, 500, margin=0, spacing=0, rotations=[0])
    res = nest([NestShape([rect_poly(240, 240)], 9), NestShape([rect_poly(600, 10)], 1)], st)
    assert res.sheets == 3
    assert res.unplaced == [(1, 0)]  # does not fit any sheet


def test_iterations_never_worse():
    st = NestSettings(800, 400, margin=5, spacing=5)
    shapes_ = [NestShape([rect_poly(170, 90)], 6), NestShape([[(0, 0), (120, 0), (60, 100)]], 6)]
    one = nest(shapes_, st)
    st.iterations = 6
    many = nest(shapes_, st)
    assert (len(many.unplaced), many.sheets) <= (len(one.unplaced), one.sheets)
    assert_valid(shapes_, many, st)


def test_group_parts_holes_and_part_in_part():
    outer = shapes.rectangle(0, 0, 200, 200)
    hole = shapes.circle((100, 100), 60)
    disk = shapes.circle((100, 100), 30)         # a separate part sitting in the hole
    text = shapes.polyline([(10, 10), (40, 10)])  # engraving on the big part
    dot = shapes.circle((100, 100), 5)           # hole of the disk
    p, s, objs = make_project([outer, hole, disk, text, dot])
    parts = group_parts(objs)
    assert len(parts) == 2
    big = next(pt for pt in parts if pt.root is objs[0])
    small = next(pt for pt in parts if pt.root is objs[2])
    assert {o.id for o in big.members} == {objs[1].id, objs[3].id}
    assert [o.id for o in small.members] == [objs[4].id]


def test_nest_objects_moves_vectors_and_makes_copies():
    a = shapes.rectangle(0, 0, 100, 50)
    hole = shapes.circle((50, 25), 10)
    b = shapes.circle((300, 300), 30)
    p, s, objs = make_project([a, hole, b], size=(400, 300))
    st = NestSettings(400, 300, margin=10, spacing=6)
    out = nest_objects(p, objs, st, quantities={objs[0].id: 3}, target_sheets=[s])
    assert out.unplaced == 0 and out.placed == 4
    vecs = [v for sh in p.sheets for v in sh.vectors]
    assert len(vecs) == 3 * 2 + 1  # three rectangles with their hole + the disk
    for sh in out.sheets:
        for v in sh.vectors:
            bb = v.bbox()
            assert bb.xmin >= 10 - 1e-6 and bb.ymin >= 10 - 1e-6
            assert bb.xmax <= 390 + 1e-6 and bb.ymax <= 290 + 1e-6
    # holes stay centred in their rectangles
    rects = [v for sh in p.sheets for v in sh.vectors if len(v.contours[0].segs) == 4 and v.contours[0].area() > 4000]
    holes = [v for sh in p.sheets for v in sh.vectors if v.contours[0].area() == pytest.approx(math.pi * 100, rel=1e-6)]
    assert len(rects) == 3 and len(holes) == 3
    for r in rects:
        c = r.bbox().center
        assert any(math.dist(c, h.bbox().center) < 1e-6 for h in holes)


def test_non_convex_decomposition_keeps_parts_apart():
    from iyicam.core.geom import Contour
    st = NestSettings(700, 500, margin=5, spacing=3, rotations=[0, 45, 90, 135, 180, 225, 270, 315])
    star = shapes.star((0, 0), 70, 30, 7).flatten()
    U = [(0, 0), (150, 0), (150, 120), (110, 120), (110, 40), (40, 40), (40, 120), (0, 120)]
    shapes_ = [NestShape([star], 6), NestShape([U], 4), NestShape([shapes.circle((0, 0), 18).flatten(0.1)], 6)]
    res = nest(shapes_, st)
    assert not res.unplaced
    assert_valid(shapes_, res, st)


def test_convex_parts_cover_the_polygon():
    import pyclipper
    from iyicam.nesting.nest import convex_parts, is_convex
    for poly in ([(0, 0), (100, 0), (100, 100), (50, 40), (0, 100)], shapes.star((0, 0), 50, 20, 9).flatten()):
        ip = clip.to_int(poly)
        parts = convex_parts(ip)
        assert parts and all(is_convex(p) for p in parts)
        assert sum(abs(pyclipper.Area(p)) for p in parts) == pytest.approx(abs(pyclipper.Area(ip)))

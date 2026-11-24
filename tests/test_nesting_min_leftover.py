"""Nesting on what is left: the strips between the cuts already made and the pockets the parts close in, narrower than the
"leftover narrower than this is not used" of the nesting (150 mm in the window), are left alone. Before, a part went into a
corridor between two parts (or a gap of 4.1 mm next to a cut of a 4 mm tool) that is scrap in the workshop."""
import pytest

from conftest import make_project
from iyicam.core import clip, shapes
from iyicam.core.document import VectorObject
from iyicam.core.stock import StockItem
from iyicam.nesting.nest import NestSettings
from iyicam.nesting.parts import nest_objects
from iyicam.nesting.stock import sheet_container, to_container, wide_enough


def corridor_sheet():
    """Two parts that stay, 60 mm apart, on the left; the right half of the sheet free."""
    p, s, fixed = make_project([shapes.rectangle(10, 10, 200, 580), shapes.rectangle(270, 10, 200, 580)], size=(1000, 600))
    loose = VectorObject([shapes.rectangle(2000, 0, 40, 40)], p.layers[0].id, "small")
    s.vectors.append(loose)
    return p, s, fixed, loose


def nest_on(p, s, loose, min_leftover):
    st = NestSettings(1000, 600, 5, 6, [0.0], "left", gravity_only=True, min_leftover=min_leftover)
    return nest_objects(p, [loose], st, use_sheets=[s], stock=[], new_sheets=False)


def test_without_it_a_part_goes_into_the_corridor_between_two_parts():
    p, s, fixed, loose = corridor_sheet()
    out = nest_on(p, s, loose, 0.0)
    b = loose.bbox()
    assert out.placed == 1 and 210 < b.xmin and b.xmax < 270          # (the old way: in the 60 mm corridor)


def test_with_it_the_narrow_corridor_is_left_alone():
    p, s, fixed, loose = corridor_sheet()
    out = nest_on(p, s, loose, 150.0)
    b = loose.bbox()
    assert out.placed == 1 and b.xmin >= 470 + 6 - 0.05                # (right of the parts, in the wide free area)


def test_a_sliver_beside_a_cut_is_not_used_but_wide_material_is_kept_with_its_corners():
    # a sheet with one part, a gap of 4.1 mm to the edge on the left
    p, s, fixed = make_project([shapes.rectangle(4.1, 0, 300, 600)], size=(1000, 600))
    c = sheet_container(s, set(), min_width=150.0)
    narrow = c.obstacles[1:]
    assert narrow and max(max(x for x, _ in q) for q in narrow) <= 4.1 + 0.05   # (the strip on the left is an obstacle)
    free = clip.boolean(clip.normalize(s.region()), clip.normalize(c.obstacles, "nonzero"), "difference")
    xs = [x for q in free for x, _ in q]
    ys = [y for q in free for _, y in q]
    assert min(xs) == pytest.approx(304.1, abs=0.1) and max(xs) == pytest.approx(1000) and (min(ys), max(ys)) == (0, 600)
    corners = {(round(x), round(y)) for q in free for x, y in q}
    assert {(1000, 0), (1000, 600)} <= corners                             # (the corners of the sheet stay sharp)


def test_a_leftover_plate_loses_its_narrow_arms():
    # an L-shaped leftover: a wide body and an arm 80 mm wide
    item = StockItem("L", 800, 600, [[(0, 0), (800, 0), (800, 80), (300, 80), (300, 600), (0, 600)]], 1, True)
    region = to_container(item, 150.0).region
    xs = [x for q in region for x, _ in q]
    assert 300 <= max(xs) < 320                     # (the arm of 500 mm is gone, the body kept: a little wedge in the inner corner)
    assert min(xs) == 0 and abs(abs(clip.area(region)) - 300 * 600) < 0.05 * 300 * 600
    assert to_container(item, 0.0).region == item.region()
    full = StockItem("Sheet", 800, 600, None, 1, False)
    assert to_container(full, 150.0).region == full.region()               # (a whole plate is as it is)
    strip = StockItem("strip", 100, 400, None, 1, True)
    assert to_container(strip, 150.0).region == strip.region()             # (a narrow leftover kept on purpose: used)
    thin = StockItem("thin L", 120, 400, [[(0, 0), (120, 0), (120, 50), (60, 50), (60, 400), (0, 400)]], 1, True)
    assert to_container(thin, 150.0).region == thin.region()


def test_wide_enough_keeps_a_rectangle_as_it_is():
    r = [[(0.0, 0.0), (500.0, 0.0), (500.0, 300.0), (0.0, 300.0)]]
    got = wide_enough(r, 150.0)
    assert abs(abs(clip.area(got)) - 150000.0) < 1.0

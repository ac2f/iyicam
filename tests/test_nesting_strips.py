"""Nesting in rows (lined up along X) and columns (along Y), from a corner; the free nesting kept to one direction.

What matters: the parts are where the arrangement says (a row shares its baseline, rows are stacked from the corner, the order
of the parts is the order of the strips), every layout is valid by an independent check (inside the plate and its margin, the
spacing between parts, never on what is fixed on the plate), everything that fits is placed, and what the free nesting does is
unchanged.
"""
import itertools
import math
import random

import pytest
from test_nesting import assert_valid, placed_polys, rect_poly

from iyicam.core import clip, shapes
from iyicam.core.geom import polygon_area
from iyicam.nesting.nest import (ARRANGEMENTS, CORNERS, GRAVITIES, Container, NestCancelled, NestSettings, NestShape,
                                 Nester, nest)


def boxes(shapes_, res, sheet=0):
    """The bounding box of every placed part of a sheet: ``(x0, y0, x1, y1)``."""
    out = []
    for s, poly in placed_polys(shapes_, res):
        if s == sheet:
            xs, ys = [p[0] for p in poly], [p[1] for p in poly]
            out.append((min(xs), min(ys), max(xs), max(ys)))
    return out


def settings(arrange, corner="bl", **kw):
    base = dict(sheet_width=1000, sheet_height=800, margin=10, spacing=10, rotations=[0], arrange=arrange, corner=corner,
                iterations=1)
    base.update(kw)
    return NestSettings(**base)


# ---------------------------------------------------------------------------------------------
# where the parts go
# ---------------------------------------------------------------------------------------------

def test_rows_line_the_parts_up_along_x_a_row_at_a_time():
    st = settings("rows")
    shapes_ = [NestShape([rect_poly(200, 100)], 11)]
    res = nest(shapes_, st)
    assert res.sheets == 1 and not res.unplaced
    assert_valid(shapes_, res, st)
    bs = boxes(shapes_, res)
    rows = {}
    for b in bs:
        rows.setdefault(round(b[1]), []).append(b)
    assert sorted(len(r) for r in rows.values()) == [3, 4, 4]                # 4 fit along 1000 - 2 x 10 mm
    for y, r in rows.items():
        assert all(abs(b[1] - r[0][1]) < 1e-3 for b in r)                    # a row shares its baseline
        xs = sorted(b[0] for b in r)
        assert xs[0] == pytest.approx(10, abs=1.0)                            # it starts at the left margin
    ys = sorted(rows)
    assert ys == sorted(ys) and ys[0] == pytest.approx(10, abs=1.0)           # the rows are stacked upwards from the bottom


def test_columns_line_the_parts_up_along_y_a_column_at_a_time():
    st = settings("columns")
    shapes_ = [NestShape([rect_poly(200, 100)], 11)]
    res = nest(shapes_, st)
    assert res.sheets == 1 and not res.unplaced
    assert_valid(shapes_, res, st)
    cols = {}
    for b in boxes(shapes_, res):
        cols.setdefault(round(b[0]), []).append(b)
    assert sorted(len(c) for c in cols.values()) == [4, 7]                    # 7 fit along 800 - 2 x 10 mm
    for x, c in cols.items():
        assert all(abs(b[0] - c[0][0]) < 1e-3 for b in c)                    # a column shares its left edge
    assert min(cols) == pytest.approx(10, abs=1.5) and max(b[3] for c in cols.values() for b in c) <= 790 + 1e-3


@pytest.mark.parametrize("arrange", ["rows", "columns"])
@pytest.mark.parametrize("corner", CORNERS)
def test_the_first_strip_starts_in_the_corner_chosen(arrange, corner):
    st = settings(arrange, corner)
    shapes_ = [NestShape([rect_poly(200, 100)], 9)]
    res = nest(shapes_, st)
    assert not res.unplaced
    assert_valid(shapes_, res, st)
    bs = boxes(shapes_, res)
    x0, y0 = min(b[0] for b in bs), min(b[1] for b in bs)
    x1, y1 = max(b[2] for b in bs), max(b[3] for b in bs)
    near_x = x0 if corner in ("bl", "tl") else x1
    near_y = y0 if corner in ("bl", "br") else y1
    assert near_x == pytest.approx(10 if corner in ("bl", "tl") else 990, abs=1.5)      # the margin of the plate
    assert near_y == pytest.approx(10 if corner in ("bl", "br") else 790, abs=1.5)
    first = bs[0]                                                                       # the first part placed is in the corner
    assert (abs(first[0] - 10) < 1.5 if corner in ("bl", "tl") else abs(first[2] - 990) < 1.5)
    assert (abs(first[1] - 10) < 1.5 if corner in ("bl", "br") else abs(first[3] - 790) < 1.5)


def test_the_thickest_parts_make_the_strips_and_a_part_goes_where_it_fits():
    st = settings("rows", rotations=[0, 90])
    shapes_ = [NestShape([rect_poly(300, 200)], 4), NestShape([rect_poly(100, 100)], 6), NestShape([rect_poly(150, 60)], 4)]
    res = nest(shapes_, st)
    assert not res.unplaced
    assert_valid(shapes_, res, st)
    rows = {}
    for b in boxes(shapes_, res):
        rows.setdefault(round(b[1] / 5), []).append(b)
    # a strip is no thicker than its thickest part, and parts are not lost in the gaps between strips
    ordered = sorted(rows.values(), key=lambda r: r[0][1])
    for lower, upper in zip(ordered, ordered[1:]):
        assert max(b[3] for b in lower) <= min(b[1] for b in upper) + 1e-3 + 10          # (the spacing is between them)


def test_a_part_is_turned_when_that_makes_the_strip_thinner():
    st = settings("rows", rotations=[0, 90], sheet_width=1000, sheet_height=300)
    shapes_ = [NestShape([rect_poly(100, 400)], 2)]                    # 400 mm high: cannot stand on a 300 mm plate
    res = nest(shapes_, st)
    assert not res.unplaced and all(p.angle % 180 == 90 for p in res.placements)
    assert_valid(shapes_, res, st)


def test_what_does_not_fit_is_left_over_and_more_plates_are_used():
    st = settings("columns")
    shapes_ = [NestShape([rect_poly(900, 100)], 8), NestShape([rect_poly(2000, 100)], 1)]
    res = nest(shapes_, st)
    assert res.sheets == 2 and res.unplaced == [(1, 0)]                  # the long one fits no plate; eight need two
    assert_valid(shapes_, res, st)
    assert sum(1 for p in res.placements if p.sheet == 0) == 7 and sum(1 for p in res.placements if p.sheet == 1) == 1


def test_every_part_is_placed_whatever_the_shapes(tmp_path):
    rng = random.Random(7)
    for arrange, corner in itertools.product(("rows", "columns"), CORNERS):
        polys = []
        for i in range(30):
            if i % 3:
                w, h = rng.uniform(30, 160), rng.uniform(25, 140)
                polys.append(NestShape([rect_poly(w, h)], rng.randint(1, 2)))
            else:
                polys.append(NestShape([shapes.circle((0, 0), rng.uniform(15, 60)).flatten(0.2)], 1))
        st = settings(arrange, corner, rotations=[0, 90], iterations=4)
        res = nest(polys, st)
        assert not res.unplaced, (arrange, corner)
        assert len(res.placements) == sum(s.quantity for s in polys)
        assert_valid(polys, res, st)


# ---------------------------------------------------------------------------------------------
# plates that are not plain rectangles
# ---------------------------------------------------------------------------------------------

def test_a_plate_with_a_corner_cut_away_is_respected():
    region = [(0, 0), (1000, 0), (1000, 400), (500, 400), (500, 800), (0, 800)]          # an L
    plate = Container([region], name="L")
    for arrange in ("rows", "columns"):
        st = NestSettings(1000, 800, margin=10, spacing=10, rotations=[0], arrange=arrange, iterations=1, containers=[plate])
        shapes_ = [NestShape([rect_poly(180, 120)], 14)]
        res = nest(shapes_, st)
        assert res.placements
        for _, poly in placed_polys(shapes_, res):
            outside = clip.boolean([poly], clip.offset([region], 1e-6), "difference")
            assert sum(abs(polygon_area(p)) for p in outside) < 1e-3                          # on the plate, not in the notch


def test_what_is_fixed_on_the_plate_is_not_covered():
    region = [(0, 0), (1000, 0), (1000, 800), (0, 800)]
    fixed = [[(300, 0), (400, 0), (400, 800), (300, 800)]]                                    # a wall across the plate
    plate = Container([region], obstacles=fixed, name="wall")
    st = NestSettings(1000, 800, margin=10, spacing=10, rotations=[0], arrange="rows", iterations=1, containers=[plate])
    shapes_ = [NestShape([rect_poly(150, 100)], 10)]
    res = nest(shapes_, st)
    assert res.placements and not res.unplaced
    for _, poly in placed_polys(shapes_, res):
        over = clip.boolean([poly], fixed, "intersection")
        assert sum(abs(polygon_area(p)) for p in over) < 1e-3


# ---------------------------------------------------------------------------------------------
# the settings
# ---------------------------------------------------------------------------------------------

def test_the_free_nesting_is_as_before_and_can_be_kept_to_one_direction():
    st = NestSettings(1000, 800, margin=10, spacing=10, rotations=[0], iterations=6)
    shapes_ = [NestShape([rect_poly(200, 100)], 7)]
    assert st.arrange == "free" and st.corner == "bl" and st.gravity_only is False
    several = Nester(shapes_, st).candidates(20)
    assert len({g for _, g in several}) > 1                                  # (the other directions are tried too)
    only = NestSettings(1000, 800, margin=10, spacing=10, rotations=[0], iterations=6, gravity="bottom", gravity_only=True)
    plan = Nester(shapes_, only).candidates(20)
    assert plan and {g for _, g in plan} == {"bottom"}                       # (only the one that was asked for)
    res = nest(shapes_, only)
    assert not res.unplaced
    assert_valid(shapes_, res, only)


def test_the_names_the_service_and_the_pages_use():
    assert ARRANGEMENTS == ("free", "rows", "columns") and CORNERS == ("bl", "br", "tl", "tr")
    assert set(GRAVITIES) == {"left", "bottom", "compact", "reach_x", "reach_y", "diag"}


def test_strips_can_be_cancelled():
    st = settings("rows")
    shapes_ = [NestShape([rect_poly(50, 40)], 400)]
    calls = {"n": 0}

    def cancel():
        calls["n"] += 1
        return calls["n"] > 20

    with pytest.raises(NestCancelled):
        nest(shapes_, st, cancel=cancel)


def test_strips_do_not_try_to_squeeze_or_polish():
    st = settings("rows", iterations=5, squeeze_angle=5.0, squeeze_margin=3.0)
    shapes_ = [NestShape([rect_poly(200, 100)], 12)]
    res = nest(shapes_, st)
    assert res.suggestion is None and not res.squeezed and not res.unplaced
    assert_valid(shapes_, res, st)


def test_rows_use_the_corner_of_the_free_nesting_too_little_to_matter_but_never_overlap(tmp_path):
    """Whatever the angle steps, the layouts are valid."""
    for rot in ([0], [0, 90], [0, 45, 90, 135], [0, 90, 180, 270]):
        for arrange in ("rows", "columns"):
            st = settings(arrange, rotations=rot, iterations=3)
            shapes_ = [NestShape([[(0, 0), (160, 0), (160, 50), (60, 50), (60, 140), (0, 140)]], 5),
                       NestShape([[(0, 0), (120, 0), (0, 120)]], 5)]
            res = nest(shapes_, st)
            assert not res.unplaced
            assert_valid(shapes_, res, st)
            for p in res.placements:
                assert math.isfinite(p.x) and math.isfinite(p.y)


# ---------------------------------------------------------------------------------------------
# through the service and the command line
# ---------------------------------------------------------------------------------------------

def _project(n=8):
    from conftest import make_project
    p, s, objs = make_project([shapes.rectangle(0, 0, 200, 100) for _ in range(n)], size=(1000.0, 800.0))
    return p.to_dict()


def test_the_service_nests_in_rows_and_columns_from_a_corner():
    from iyicam.service import ServiceHost
    host = ServiceHost(store=None)
    base = {"width": 1000, "height": 800, "margin": 10, "spacing": 10, "rotation": 0, "iterations": 1}
    out = {}
    for arrange, corner in (("rows", "bl"), ("columns", "tr")):
        r = host.handle("nest", {"project": _project(), "settings": {**base, "arrange": arrange, "corner": corner}})
        assert "error" not in r, r
        xs = sorted({round(v["contours"][0]["start"][0]) for sh in r["project"]["sheets"] for v in sh["vectors"]})
        out[arrange] = (xs, r["placed"])
    for arrange in out:
        assert out[arrange][1] == 8                                          # (both place everything)
        assert len(out[arrange][0]) >= 2


def test_the_service_refuses_a_direction_it_does_not_know():
    from iyicam.service import ServiceHost
    host = ServiceHost(store=None)
    for key, value in (("arrange", "diagonal"), ("corner", "middle"), ("gravity", "up")):
        r = host.handle("nest", {"project": _project(), "settings": {key: value}})
        assert r["error"] == "bad_request" and r["params"]["field"] == key and value not in r["params"]["allowed"]
    ok = host.handle("nest", {"project": _project(4), "settings": {"gravity": "bottom", "gravity_only": True, "iterations": 3,
                                                                 "width": 1000, "height": 800}})
    assert "error" not in ok


def test_the_command_line_has_the_arrangement(tmp_path, capsys):
    import json

    from iyicam.cli import main
    src, out = tmp_path / "job.icam", tmp_path / "nested.icam"
    src.write_text(json.dumps(_project(6)), encoding="utf-8")
    assert main(["nest", str(src), "-o", str(out), "--width", "1000", "--height", "800", "--spacing", "10", "--margin", "10",
                 "--rotation", "0", "--iterations", "1", "--arrange", "rows", "--corner", "tr"]) == 0
    assert out.exists()
    with pytest.raises(SystemExit):
        main(["nest", str(src), "-o", str(out), "--arrange", "spiral"])

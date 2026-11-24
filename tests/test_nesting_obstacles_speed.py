"""Nesting the vectors that lie off a sheet onto it, around parts that stay on it.

The parts that stay were taken out of the outline of the plate: a polygon of hundreds of points, and where every part fits in
it was worked out against all of it, a second or more for every part and angle, in one call of Clipper that held the program.
Three minutes before the first part was placed, the progress at 0 and the window frozen, the time limit (only looked at
between layouts) far past. Now the plate is the rectangle and the parts that stay are obstacles taken out with their no-fit
polygons, the progress follows the clock, the no-fit polygons are kept for the next nesting, and many of them are worked out on
the processors of the computer at once."""
import time

import pytest

from conftest import make_project
from iyicam.core import clip
from iyicam.core.document import VectorObject
from iyicam.nesting import nest as N
from iyicam.nesting.nest import NestSettings, NestShape, Nester, nest
from iyicam.nesting.parts import group_parts, nest_objects, part_outline


def blob(cx, cy, r, n=24, bumps=6):
    """A curved part with concave bays (many convex pieces, as the letters and logos of a sign)."""
    import math
    pts = []
    for k in range(n * bumps):
        a = 2 * math.pi * k / (n * bumps)
        rr = r * (1 + 0.25 * math.sin(bumps * a))
        pts.append((cx + rr * math.cos(a), cy + rr * math.sin(a)))
    from iyicam.core.geom import Contour
    return Contour.polyline(pts, True)


def sheet_with_fixed(n_loose=8):
    p, s, objs = make_project([blob(300, 300, 120), blob(800, 600, 100)], size=(1200, 1000))
    loose = []
    for i in range(n_loose):
        o = VectorObject([blob(2000 + 300 * i, 300, 60 + 5 * (i % 3))], p.layers[0].id, f"p{i}")
        s.vectors.append(o)
        loose.append(o)
    return p, s, objs, loose


def gaps_ok(sheet, fixed_ids, gap):
    parts = group_parts(sheet.vectors)
    polys = [clip.normalize(part_outline(pt)) for pt in parts]
    moved = [not ({o.id for o in pt.objects} & fixed_ids) for pt in parts]
    for i, a in enumerate(polys):
        if not moved[i]:
            continue
        ga = clip.offset(a, gap / 2 - 0.05)
        for j, b in enumerate(polys):
            if j == i or (moved[j] and j < i):
                continue
            if abs(clip.area(clip.boolean(ga, clip.offset(b, gap / 2 - 0.05), "intersection"))) > 1e-6:
                return False
    return True


def test_parts_go_round_the_parts_that_stay_without_the_slow_fit_in_the_plate_outline(monkeypatch):
    p, s, fixed, loose = sheet_with_fixed()

    def slow(*a, **k):
        raise AssertionError("the outline of the plate was used: the slow way")
    monkeypatch.setattr(Nester, "_ifp_paths", staticmethod(slow))
    st = NestSettings(1200, 1000, 10, 6, [0, 90, 180, 270], "bottom", gravity_only=True)
    out = nest_objects(p, loose, st, use_sheets=[s], stock=[], new_sheets=False)
    assert out.placed == len(loose) and out.unplaced == 0
    assert gaps_ok(s, {o.id for o in fixed}, 6.0)
    for o in loose:
        b = o.bbox()
        assert b.xmin >= 10 - 0.05 and b.ymin >= 10 - 0.05 and b.xmax <= 1190 + 0.05 and b.ymax <= 990 + 0.05


def test_the_progress_follows_the_clock_and_never_goes_back():
    p, s, fixed, loose = sheet_with_fixed(6)
    shapes_ = [NestShape(part_outline(pt), 1) for pt in group_parts(loose)]
    seen = []
    st = NestSettings(1200, 1000, 10, 6, [0, 90, 180, 270], "bottom", iterations=50, time_limit=3.0, gravity_only=True)
    t0 = time.perf_counter()
    nest(shapes_, st, lambda k, n: seen.append((time.perf_counter() - t0, k / n)))
    assert seen and all(b[1] >= a[1] for a, b in zip(seen, seen[1:]))
    late = [f for t, f in seen if t > 2.0]
    assert late and late[-1] > 0.6                               # (it moved on with the time, to near the end)
    assert time.perf_counter() - t0 < 3.0 + 1.5                   # (the time limit is kept)


def test_no_fit_polygons_are_kept_for_the_next_nesting():
    p, s, fixed, loose = sheet_with_fixed(5)
    shapes_ = [NestShape(part_outline(pt), 1) for pt in group_parts(loose)]
    st = NestSettings(1200, 1000, 10, 6, [0, 90], "bottom", gravity_only=True)
    N._nfp_store.clear()
    N._nfp_store_points = 0
    first = Nester(shapes_, st)
    first.run_order(first.default_order())
    assert first.stats["nfp"] > 0
    again = Nester(shapes_, st)
    r = again.run_order(again.default_order())
    assert again.stats["nfp"] == 0 and not r.unplaced            # (all of them from the kept ones)


def test_the_helpers_give_the_same_layout_as_one_processor(monkeypatch):
    p, s, fixed, loose = sheet_with_fixed(6)
    shapes_ = [NestShape(part_outline(pt), 1) for pt in group_parts(loose)]
    st = NestSettings(1200, 1000, 10, 6, [0, 90, 180, 270], "bottom", gravity_only=True)

    def layout(helpers):
        N._nfp_store.clear()
        N._nfp_store_points = 0
        monkeypatch.setenv("IYICAM_NEST_PROCESSES", str(helpers))
        monkeypatch.setattr(N, "PARALLEL_WORK", 1)
        n = Nester(shapes_, st)
        made = n.precompute()
        r = n.run_order(n.default_order())
        return made, sorted((pl.shape, round(pl.x, 6), round(pl.y, 6), pl.angle) for pl in r.placements)
    made1, one = layout(0)
    made2, two = layout(2)
    assert made1 == 0 and made2 > 0 and one == two


def test_a_script_started_again_as_a_helper_starts_no_helpers(monkeypatch):
    import multiprocessing as mp
    monkeypatch.setattr(mp, "parent_process", lambda: object())
    monkeypatch.setenv("IYICAM_NEST_PROCESSES", "4")
    assert N.nest_processes() == 0

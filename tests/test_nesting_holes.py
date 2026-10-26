"""Inner pieces (göbek): kept with their part or nested on their own, and holes filled with other parts."""
import random

import pytest
from conftest import make_project
from nest_check import verify

from iyicam.core import shapes
from iyicam.nesting.nest import NestSettings, NestShape, nest
from iyicam.nesting.parts import group_parts, nest_objects


def rect(w, h, x=0, y=0):
    return [(x, y), (x + w, y), (x + w, y + h), (x, y + h)]


def settings(**kw):
    base = dict(sheet_width=1200, sheet_height=500, margin=10, spacing=8, rotations=[0, 90], gravity="left",
                iterations=1, tolerance=0.5)
    base.update(kw)
    return NestSettings(**base)


# -- the engine ------------------------------------------------------------------------------------------------
def test_parts_are_placed_in_the_hole_of_another():
    frame = NestShape([rect(600, 400)], 1, holes=[rect(400, 200, 100, 100)])
    small = NestShape([rect(150, 100)], 6)
    st = settings()
    res = nest([frame, small], st)
    assert verify([frame, small], res, st) == [] and not res.unplaced
    f = next(p for p in res.placements if p.shape == 0)
    inside = [p for p in res.placements if p.shape == 1 and f.x + 100 <= p.x <= f.x + 500 and f.y + 100 <= p.y <= f.y + 300]
    assert len(inside) >= 2


def test_without_holes_nothing_goes_in_them():
    frame = NestShape([rect(600, 400)], 1, holes=[])
    small = NestShape([rect(150, 100)], 6)
    st = settings()
    res = nest([frame, small], st)
    f = next(p for p in res.placements if p.shape == 0)
    assert not [p for p in res.placements if p.shape == 1 and f.x < p.x < f.x + 600 and f.y < p.y < f.y + 400]


def test_a_part_too_big_for_the_hole_stays_out_and_a_frame_fits_round_a_part():
    frame = NestShape([rect(600, 400)], 1, holes=[rect(300, 200, 150, 100)])
    big = NestShape([rect(320, 220)], 1)
    st = settings(sheet_width=1500)
    res = nest([frame, big], st)
    assert verify([frame, big], res, st) == []
    f = next(p for p in res.placements if p.shape == 0)
    b = next(p for p in res.placements if p.shape == 1)
    assert not (f.x < b.x < f.x + 600 and f.y < b.y < f.y + 400)
    # a part may also be put where a frame closes round it (the part is placed first)
    res = nest([big, frame], st)
    assert verify([big, frame], res, st) == []


@pytest.mark.parametrize("seed", range(10))
def test_random_frames_and_parts_are_valid(seed):
    rnd = random.Random(seed)
    sh = []
    for _ in range(rnd.randint(1, 3)):
        w, h = rnd.randint(300, 600), rnd.randint(250, 450)
        t = rnd.randint(40, 90)
        sh.append(NestShape([rect(w, h)], rnd.randint(1, 2), holes=[rect(w - 2 * t, h - 2 * t, t, t)]))
    for _ in range(rnd.randint(2, 5)):
        sh.append(NestShape([rect(rnd.randint(40, 200), rnd.randint(30, 150))], rnd.randint(1, 5)))
    st = settings(sheet_width=1400, sheet_height=900, rotations=rnd.choice([[0], [0, 90], [0, 45, 90, 135]]),
                  iterations=rnd.choice([1, 6]), gravity=rnd.choice(["left", "bottom", "compact"]))
    res = nest(sh, st)
    assert verify(sh, res, st) == []
    assert len(res.placements) + len(res.unplaced) == sum(s.quantity for s in sh)


def test_filling_holes_saves_material():
    frames = [NestShape([rect(500, 400)], 2, holes=[rect(380, 280, 60, 60)])]
    small = [NestShape([rect(170, 120)], 8)]
    st = settings(sheet_width=1100, sheet_height=500, rotations=[0])
    plain = nest([NestShape([rect(500, 400)], 2)] + small, st)
    filled = nest(frames + small, st)
    assert verify(frames + small, filled, st) == []
    assert (filled.sheets, filled.used[-1][2]) < (plain.sheets, plain.used[-1][2]) or filled.sheets < plain.sheets


# -- the grouping of inner pieces --------------------------------------------------------------------------------
def ring_with_hub():
    """A plate, its hole, and a hub piece in the hole (three vectors) and a lone small part."""
    return [[shapes.rectangle(0, 0, 200, 160)], [shapes.circle((100, 80), 50)], [shapes.circle((100, 80), 20)],
            [shapes.rectangle(300, 0, 60, 40)]]


def test_inner_pieces_are_separate_parts_or_stay_with_their_part():
    p, s, objs = make_project(ring_with_hub())
    sep = group_parts(objs)
    assert [len(x.members) for x in sep] == [1, 0, 0]           # plate + hole | hub | the lone part
    grouped = group_parts(objs, group_inner=True)
    assert [len(x.members) for x in grouped] == [2, 0]          # plate + hole + hub | the lone part
    assert {o.id for o in grouped[0].objects} == {objs[0].id, objs[1].id, objs[2].id}


def test_holes_of_a_part_are_found_unless_something_stays_in_them():
    p, s, objs = make_project(ring_with_hub())
    assert len(group_parts(objs)[0].holes) == 1                   # the hub is a part of its own: the hole is free
    assert group_parts(objs, group_inner=True)[0].holes == []     # the hub stays: nothing can be nested in


def test_a_hole_inside_the_outline_object_itself_is_a_hole_too():
    outline_and_hole = [shapes.rectangle(0, 0, 200, 160), shapes.circle((100, 80), 40)]
    p, s, objs = make_project([outline_and_hole])
    assert len(group_parts(objs)[0].holes) == 1


def census(project):
    from test_nesting_invariants import census as c
    return c(project)


def test_nesting_a_project_with_filled_holes_moves_whole_parts_into_the_hole():
    ring = [shapes.rectangle(0, 0, 500, 400), shapes.circle((250, 200), 170)]   # one object, a hole in it
    small = [[shapes.rectangle(700 + 120 * i, 0, 100, 60)] for i in range(4)]
    p, s, objs = make_project([ring] + small, size=(1200, 450))
    before = census(p)
    st = settings(sheet_width=1200, sheet_height=450, margin=10, spacing=8, rotations=[0, 90])
    out = nest_objects(p, list(s.vectors), st, fill_holes=True)
    assert census(p) == before and not out.unplaced
    # the small parts really lie inside the hole of the ring (nothing is cut through the ring's material)
    ring_obj = next(o for sh in p.sheets for o in sh.vectors if len(o.contours) == 2)
    hole = next(c for c in ring_obj.contours if c.bbox().width < 400)
    hb = hole.bbox()
    inside = [o for sh in p.sheets for o in sh.vectors if o is not ring_obj
              and hb.xmin <= o.bbox().xmin and o.bbox().xmax <= hb.xmax and hb.ymin <= o.bbox().ymin
              and o.bbox().ymax <= hb.ymax]
    assert len(inside) >= 2


def test_option_off_keeps_the_old_behaviour():
    ring = [shapes.rectangle(0, 0, 500, 400), shapes.circle((250, 200), 170)]
    small = [[shapes.rectangle(700 + 120 * i, 0, 100, 60)] for i in range(4)]
    p, s, objs = make_project([ring] + small, size=(1200, 450))
    out = nest_objects(p, list(s.vectors), settings(sheet_width=1200, sheet_height=450))
    ring_obj = next(o for sh in p.sheets for o in sh.vectors if len(o.contours) == 2)
    rb = ring_obj.bbox()
    others = [o for sh in p.sheets for o in sh.vectors if o is not ring_obj]
    assert not any(rb.xmin < o.bbox().xmin and o.bbox().xmax < rb.xmax and rb.ymin < o.bbox().ymin
                   and o.bbox().ymax < rb.ymax for o in others)
    assert not out.unplaced

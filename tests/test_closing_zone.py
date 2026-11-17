"""Where a cut closes, and the order it is made in, across many different layouts.

A closed cut ends where it starts, and the stretch cut last is all that holds the part. Cut for a sweep from bottom
left to top right (clockwise) it closes in its *closing zone*: on the right side, a little above the bottom right
corner (5 % of the side, never on the corner); and it must close held by sheet that is still there: what lies beyond
the cut where it closes (and along the stretch cut last) has not been cut away by earlier cuts, so every cut uses the
still fixed part of the sheet. These tests nest letter-like parts in many ways (free with every gravity, rows,
columns, from every corner, turned, tight and wide) and look at every cut with probes of their own, not the
algorithm's.
"""
import math
import random

import numpy as np
import pytest
from conftest import add_tp, end_mill, make_project
from test_sequence import inside_first_ok, replay

from iyicam.cam import sequence as seq
from iyicam.cam.job import compute_toolpath
from iyicam.cam.pathutil import PathWalker
from iyicam.cam.startpoints import closing_zone, corners, zone_cost
from iyicam.core import clip, shapes
from iyicam.core.document import Selection, ToolpathDef
from iyicam.core.geom import Contour, Transform
from iyicam.nesting.nest import NestSettings
from iyicam.nesting.parts import nest_objects

R = 3.0


# ---------------------------------------------------------------------------------------------
# letter-like parts
# ---------------------------------------------------------------------------------------------

def poly(pts):
    return shapes.polyline(pts, True)


def n_shape(w=70, h=150, t=20):
    return poly([(0, 0), (t, 0), (t, h - 50), (w - t, 0), (w, 0), (w, h), (w - t, h), (w - t, 50), (t, h), (0, h)])


def u_shape(w=75, h=150, t=22):
    return poly([(0, 20), (0, h), (t, h), (t, 30), (w - t, 30), (w - t, h), (w, h), (w, 20), (w - 20, 0), (20, 0)])


def t_shape(w=75, h=150, t=24, bar=28):
    return poly([(w / 2 - t / 2, 0), (w / 2 + t / 2, 0), (w / 2 + t / 2, h - bar), (w, h - bar), (w, h), (0, h),
                 (0, h - bar), (w / 2 - t / 2, h - bar)])


def keyhole_letter():
    """A letter with a counter that joins the outside by a channel narrower than the tool: the outside cut
    cannot enter the channel, so its counter is a loop of scrap of the same vector."""
    return poly([(0, 0), (70, 0), (70, 150), (36, 150), (36, 100), (42, 100), (42, 60), (28, 60), (28, 100),
                 (34, 100), (34, 150), (0, 150)])


def parts_set(seed=1, n=18):
    """Letter-like parts of the sizes of a 150 mm sign: strips, N, U, T, round letters and a keyhole letter."""
    rng = random.Random(seed)
    out = []
    makers = [lambda: shapes.rectangle(0, 0, rng.uniform(18, 40), 145),
              lambda: n_shape(rng.uniform(60, 80)),
              lambda: u_shape(rng.uniform(65, 80)),
              lambda: t_shape(rng.uniform(65, 85)),
              lambda: shapes.circle((40, 75), rng.uniform(32, 40)),
              keyhole_letter,
              lambda: shapes.rectangle(0, 0, rng.uniform(70, 100), 144, corner_radius=10)]
    for k in range(n):
        out.append([makers[k % len(makers)]()])
    rng.shuffle(out)
    return out


ROT4 = [0.0, 90.0, 180.0, 270.0]


def nested(parts, arrange="free", corner="bl", gravity="left", rot=(0.0,), spacing=8.0, margin=10.0, size=(480, 300)):
    p, s, objs = make_project(parts, size=size)
    for k, o in enumerate(objs):        # (apart: parts lying on each other would count as one part)
        bb = o.bbox()
        o.transform(Transform.translate(1000 + 200 * (k % 6) - bb.xmin, 1000 + 200 * (k // 6) - bb.ymin))
    st = NestSettings(size[0], size[1], margin, spacing, list(rot), gravity, gravity_only=True, arrange=arrange,
                      corner=corner)
    out = nest_objects(p, objs, st, {o.id: 1 for o in objs}, [p.sheets[0]], new_sheets=True)
    assert not out.unplaced
    return p


LAYOUTS = {
    "free-left": dict(gravity="left"),
    "free-bottom": dict(gravity="bottom"),
    "free-diag": dict(gravity="diag"),
    "free-compact-turned": dict(gravity="compact", rot=ROT4),
    "rows-bl": dict(arrange="rows", corner="bl"),
    "rows-tr": dict(arrange="rows", corner="tr"),
    "columns-bl": dict(arrange="columns", corner="bl"),
    "columns-br": dict(arrange="columns", corner="br"),
    "wide": dict(gravity="left", spacing=24.0, margin=20.0),
}


def cut_all(p, strategy="rows_bl", direction="toolpath", **seqkw):
    objs = [v for s in p.sheets for v in s.vectors]
    t = ToolpathDef("profile", "profile", end_mill(6), {"through": True}, Selection("vectors", [o.id for o in objs]))
    t.sequence = {"strategy": strategy, "direction": direction, "smart_starts": True, **seqkw}
    p.toolpaths = [t]
    out = []
    for sh in p.sheets:
        res = compute_toolpath(p, t, sh)
        assert res.error is None
        if res.toolpaths:
            out.append((sh, res.toolpaths[0]))
    return out


def earlier_kerf(tp, i):
    """The cut areas of the cuts before the ``i``-th (own probes: the loops are rebuilt from the cuts)."""
    polys = []
    for c in tp.cuts[:i]:
        if c.get("loop") is not None and c["closed"]:
            polys += clip.offset_lines([c["loop"].flatten(0.05)], c.get("radius", R), closed=True)
    return polys


def outline_cuts(tp):
    """The cuts that set a whole part free: the last, clockwise loop of every vector (not holes, not slots)."""
    last = {}
    for i, c in enumerate(tp.cuts):
        if c["closed"] and not c.get("finish"):
            last[c["key"]] = i
    return [(i, tp.cuts[i]) for i in sorted(last.values()) if not tp.cuts[i]["loop"].is_ccw()]


def probes_beyond(c, rings=(1.5, 5.0), n=7, back=18.0):
    """Points beyond the cut at the close and along the stretch cut last (the 18 mm before it)."""
    loop = c["loop"]
    L = loop.length()
    cw = not loop.is_ccw()
    pts = []
    for k in range(n + 1):
        s = (L - back * k / n) % L      # in cutting direction: the loop starts at the plunge, and ends at the close
        a, b = loop.point_at_length(s), loop.point_at_length((s + 0.3) % L)
        tx, ty = b[0] - a[0], b[1] - a[1]
        d = math.hypot(tx, ty) or 1.0
        tx, ty = tx / d, ty / d
        nx, ny = (-ty, tx) if cw else (ty, -tx)               # away from the part
        pts += [(a[0] + nx * (R + r), a[1] + ny * (R + r)) for r in rings]
    return np.array(pts)


def assert_supported(tp, label=""):
    """No probe beyond the close, nor along the stretch cut last, lies in an area cut before (the edge of
    the sheet is not a cut)."""
    for i, c in outline_cuts(tp):
        kerf = earlier_kerf(tp, i)
        if not kerf:
            continue
        hit = clip.region_mask(probes_beyond(c), kerf)
        assert not hit.any(), (label, c["key"], int(hit.sum()), "of", len(hit), "probes are in cut areas")


# ---------------------------------------------------------------------------------------------
# the zone itself
# ---------------------------------------------------------------------------------------------

def walker_of(c: Contour, clockwise: bool) -> PathWalker:
    if c.is_ccw() == clockwise:
        c = c.reversed()
    return PathWalker(c.segs, True)


def target_of(c, clockwise, hold, inner=False, r=R):
    w = walker_of(c, clockwise)
    z = closing_zone(w, hold, clockwise, max(2.0, r), corners(w, tight=2 * r + 1), inner)
    return w.point_at(z.target), z


H_BLTR = (math.sqrt(0.5), math.sqrt(0.5))


def test_clockwise_cuts_for_a_sweep_from_bottom_left_close_five_percent_above_the_bottom_right_corner():
    for w, h in ((40, 200), (80, 150), (300, 100), (25, 1000)):
        pt, z = target_of(shapes.rectangle(10, 20, w, h), True, H_BLTR)
        assert pt[0] == pytest.approx(10 + w)                       # on the right side
        assert pt[1] - 20 == pytest.approx(max(3.0, 0.05 * h)), (w, h)   # 5 % of the side above the corner
        assert z.before


def test_the_zone_is_the_mirror_image_for_the_other_sweeps_and_directions():
    rect = shapes.rectangle(0, 0, 60, 100)
    s = math.sqrt(0.5)
    cases = [((s, s), True, (60, 5)),           # bottom left -> top right, clockwise: bottom right, right side
             ((-s, s), False, (0, 5)),          # bottom right -> top left, counter-clockwise: bottom left
             ((s, -s), False, (60, 95)),        # top left -> bottom right: top right, right side
             ((-s, -s), True, (0, 95)),         # top right -> bottom left: top left
             ((s, s), False, None)]             # counter-clockwise for bottom left -> top right: top left, top side
    for hold, cw, want in cases:
        pt, z = target_of(rect, cw, hold)
        if want is None:
            assert pt[1] == pytest.approx(100) and pt[0] == pytest.approx(3.0), pt      # on the top, 5 % of the width in
        else:
            assert pt[0] == pytest.approx(want[0]) and pt[1] == pytest.approx(want[1]), (hold, cw, pt)


def test_a_hole_takes_the_corner_of_the_sweep_whatever_its_direction():
    hole = shapes.rectangle(0, 0, 30, 100)
    pt, z = target_of(hole, False, H_BLTR, inner=True)       # holes are cut counter-clockwise
    assert pt[0] == pytest.approx(30) and pt[1] == pytest.approx(5.0)    # bottom right, above the corner
    assert not z.before                                       # arrives at the corner, then goes up the side


def test_cost_is_zero_at_the_target_and_grows_away_from_it_most_on_the_wrong_side_of_the_corner():
    w = walker_of(shapes.rectangle(0, 0, 60, 200), True)
    z = closing_zone(w, H_BLTR, True, 3.0, corners(w, tight=7.0))
    L = w.length
    s = np.array([z.target, z.target - 10, z.target + 10, z.corner[0] + 3])
    c = zone_cost(z, s % L, L)
    assert c[0] == pytest.approx(0.0, abs=1e-6)
    assert c[1] == pytest.approx(10.0) and c[2] == pytest.approx(20.0)   # (counted once, and twice nearer the corner)
    assert c[3] > 100                                        # past the corner, on the bottom side


# ---------------------------------------------------------------------------------------------
# a vector whose scrap cannot be entered by the outside cut
# ---------------------------------------------------------------------------------------------

def test_the_scrap_of_a_counter_the_outside_cut_cannot_enter_is_cut_before_the_outline():
    p, s, objs = make_project([[keyhole_letter()]], size=(200, 250))
    t = add_tp(p, "profile", end_mill(6), {"through": True}, objs)
    t.sequence = {"smart_starts": True, "strategy": "rows_bl"}
    tp = compute_toolpath(p, t, s).toolpaths[0]
    loops = [c for c in tp.cuts if c["closed"]]
    assert len(loops) == 2
    inner, outer = loops
    assert inner["loop"].is_ccw() and not outer["loop"].is_ccw()        # the scrap loop first, then the outline
    assert inner["loop"].bbox().width < outer["loop"].bbox().width
    assert "ok" in inner and "ok" in outer                              # both are placed by smart start points
    assert replay(tp, s) == []


# ---------------------------------------------------------------------------------------------
# many layouts, one behaviour
# ---------------------------------------------------------------------------------------------

@pytest.mark.parametrize("layout", list(LAYOUTS))
@pytest.mark.parametrize("strategy", ["rows_bl", "diag_bl", "nearest"])
def test_every_cut_closes_in_its_zone_held_by_sheet_that_is_still_there(layout, strategy):
    p = nested(parts_set(seed=3), **LAYOUTS[layout])
    cuts = cut_all(p, strategy)
    n = zone_far = 0
    for sh, tp in cuts:
        assert inside_first_ok(tp, [v for v in sh.vectors])
        assert replay(tp, sh) == []                                     # never a cut in loose scrap
        assert_supported(tp, f"{layout}/{strategy}")
        for i, c in outline_cuts(tp):
            n += 1
            h = c["loop"].bbox().height
            assert c["ok"], (layout, strategy, c["key"])                 # clear of earlier cuts and of corners
            assert c["gap"] >= 1.0 - 1e-6
            zone_far += c["zone"] > max(12.0, 0.08 * h)                 # (how far from "5 % above the corner")
    assert n >= 15
    assert zone_far <= 0.05 * n, (layout, strategy, zone_far, n)


@pytest.mark.parametrize("layout", ["free-left", "rows-bl", "columns-br"])
def test_the_same_parts_close_the_same_way_in_every_layout(layout):
    """The share of cuts that close in the zone does not depend on how the parts are laid out."""
    p = nested(parts_set(seed=5, n=21), **LAYOUTS[layout])
    sh_tp = cut_all(p, "rows_bl")
    zones = [c["zone"] / max(c["loop"].bbox().height, 1.0) for _, tp in sh_tp for _, c in outline_cuts(tp)]
    assert np.percentile(zones, 95) < 0.1


def test_a_cut_is_made_before_the_neighbour_that_would_leave_its_closing_zone_without_sheet():
    """A tall part with a lower part standing next to its lower right side: the sweep (row by row, the lowest first)
    would cut the lower one first and take the sheet away where the tall one closes, so the tall one goes first."""
    tall = shapes.rectangle(20, 60, 60, 150)
    low = shapes.rectangle(88, 0, 50, 70)             # 8 mm right of the tall part's lower right corner
    p, s, objs = make_project([[tall], [low], [shapes.rectangle(20, 230, 60, 50)]], size=(220, 300))
    t = add_tp(p, "profile", end_mill(6), {"through": True}, objs)
    t.sequence = {"strategy": "rows_bl", "smart_starts": True}
    tp = compute_toolpath(p, t, s).toolpaths[0]
    ids = {o.id: n for n, o in enumerate(objs)}
    order = [ids[c["key"].split(":")[0]] for c in tp.cuts]
    assert order.index(0) < order.index(1)                              # the tall part before the lower one
    tall_cut = tp.cuts[order.index(0)]
    assert tall_cut["close"][0] == pytest.approx(80 + R)
    assert tall_cut["close"][1] - 60 < 20                               # at its bottom right corner, not high up
    assert replay(tp, s) == []


def test_without_smart_start_points_the_support_is_still_kept_and_nothing_else_is_moved():
    """The start points are the vectors' own then, but a part is still cut before the neighbour on the side where the sheet
    holds it: the tall part before the lower one beside its lower right side (the row alone would take the lowest first).
    Where nothing stands on a hold side the order is the strategy's own."""
    tall = shapes.rectangle(20, 60, 60, 150)
    low = shapes.rectangle(88, 0, 50, 70)
    p, s, objs = make_project([[tall], [low]], size=(220, 300))
    t = add_tp(p, "profile", end_mill(6), {"through": True}, objs)
    t.sequence = {"strategy": "rows_bl", "smart_starts": False}
    order = [c["key"].split(":")[0] for c in compute_toolpath(p, t, s).toolpaths[0].cuts]
    assert order == [objs[0].id, objs[1].id]                            # the tall part first: the lower one is on its hold side
    # two parts one behind the other in a row (nothing on the hold side of the first that is not behind it): the strategy's order
    a, b = shapes.rectangle(20, 20, 40, 60), shapes.rectangle(100, 20, 40, 60)
    p, s, objs = make_project([[b], [a]], size=(220, 300))
    t = add_tp(p, "profile", end_mill(6), {"through": True}, objs)
    t.sequence = {"strategy": "rows_bl", "smart_starts": False}
    order = [c["key"].split(":")[0] for c in compute_toolpath(p, t, s).toolpaths[0].cuts]
    assert order == [objs[1].id, objs[0].id]                            # left to right: a (the second object) then b


def test_parts_on_one_line_are_one_row_however_tall_they_are():
    """A sweep reaches a part at its corner: a tall and a short part standing on one baseline are one row, left to
    right, not the short one first because its centre is lower."""
    tall, short = shapes.rectangle(20, 20, 40, 150), shapes.rectangle(70, 20, 40, 30)
    items = [seq.SeqItem("tall", tall), seq.SeqItem("short", short)]
    seq.containment(items)
    assert seq.order_items(items, "rows_bl") == ["tall", "short"]
    top = [seq.SeqItem("a", shapes.rectangle(0, 100, 40, 50)), seq.SeqItem("b", shapes.rectangle(60, 140, 40, 10))]
    seq.containment(top)
    assert seq.order_items(top, "rows_tl") == ["a", "b"]                # both reach y=150: one row from the left


def test_two_parts_that_each_want_to_come_first_keep_the_strategys_order():
    """A cycle (A wants to be before B and B before A): the order the strategy made is kept for them."""
    def item(key, x, y):
        it = seq.SeqItem(key, shapes.rectangle(x, y, 20, 20), kerf=[[(x - 3, y - 3), (x + 23, y - 3), (x + 23, y + 23),
                                                                       (x - 3, y + 23)]])
        return it
    items = {k: item(k, 30 * i, 0) for i, k in enumerate("abc")}
    seq.containment(list(items.values()))
    out = seq.apply_zones(["a", "b", "c"], {"a": {"b"}, "b": {"a"}}, items)
    assert sorted(out) == ["a", "b", "c"] and out == ["a", "b", "c"]
    out = seq.apply_zones(["a", "b", "c"], {"c": {"a"}}, items)          # c before a: pulled to the front
    assert out == ["c", "a", "b"]


def test_a_part_the_pulls_would_leave_in_loose_scrap_is_not_pulled():
    """Pulling a cut forward must not close a ring of cuts round a part that is cut later: that part would be
    cut loose. Four bars round a small part, bars 8 mm from it: the part is cut before the last of them."""
    bars = [shapes.rectangle(50, 50, 100, 30), shapes.rectangle(50, 126, 100, 30),
            shapes.rectangle(50, 88, 30, 30), shapes.rectangle(120, 88, 30, 30)]
    vecs = [[b] for b in bars] + [[shapes.circle((100, 103), 6)]]
    for strategy in ("rows_bl", "diag_bl", "nearest", "smart"):
        p, s, objs = make_project(vecs, size=(250, 250))
        t = add_tp(p, "profile", end_mill(6), {"through": True}, objs)
        t.sequence = {"strategy": strategy, "smart_starts": True}
        tp = compute_toolpath(p, t, s).toolpaths[0]
        assert replay(tp, s) == [], strategy


# ---------------------------------------------------------------------------------------------
# stability
# ---------------------------------------------------------------------------------------------

def test_calculating_again_leaves_every_start_where_it_is():
    from iyicam.service.ops import calculate
    p = nested(parts_set(seed=7, n=14), **LAYOUTS["free-diag"])
    cut_all(p, "rows_bl")
    calculate(p)                                    # (moves the start points into the vectors)
    first = {(o.id, i): c.start for s in p.sheets for o in s.vectors for i, c in enumerate(o.contours)}
    res, changed = calculate(p)
    second = {(o.id, i): c.start for s in p.sheets for o in s.vectors for i, c in enumerate(o.contours)}
    assert not changed
    assert first == second


@pytest.mark.parametrize("strategy", ["rows_bl", "cols_bl", "diag_bl", "zigzag_up", "nearest", "smart"])
def test_the_order_is_never_loose_for_any_sweep(strategy):
    p = nested(parts_set(seed=11, n=16), **LAYOUTS["free-compact-turned"])
    for sh, tp in cut_all(p, strategy):
        assert replay(tp, sh) == []
        assert_supported(tp, strategy)


# ---------------------------------------------------------------------------------------------
# the replay that makes sure no cut is made in loose scrap
# ---------------------------------------------------------------------------------------------

def replay_items(tp, sheet):
    from test_sequence import cut_points
    from iyicam.core.geom import BBox
    items, order = {}, []
    for c in tp.cuts:
        pts = cut_points(tp, c)
        if not pts or not c["closed"]:
            continue
        kerf = clip.offset_lines([pts], R, closed=False)
        k = c["key"]
        if k in items:
            items[k].kerf += kerf
            continue
        items[k] = seq.SeqItem(k, c["loop"], frees=True, kerf=kerf)
        order.append(k)
    seq.containment(list(items.values()))
    return order, items, BBox(0, 0, sheet.width, sheet.height)


def ring_job():
    bars = [shapes.rectangle(50, 50, 100, 30), shapes.rectangle(50, 126, 100, 30),
            shapes.rectangle(50, 88, 30, 30), shapes.rectangle(120, 88, 30, 30)]
    vecs = [[b] for b in bars] + [[shapes.circle((100, 103), 6)]]
    p, s, objs = make_project(vecs, size=(250, 250))
    t = add_tp(p, "profile", end_mill(6), {"through": True}, objs)
    t.sequence = {"strategy": "rows_bl"}
    return compute_toolpath(p, t, s).toolpaths[0], s, objs


def test_the_fast_replay_agrees_with_the_slow_one_on_any_order():
    tp, s, objs = ring_job()
    order, items, box = replay_items(tp, s)
    rng = random.Random(4)
    seen = set()
    for _ in range(40):
        o = order[:]
        rng.shuffle(o)
        slow = bool(seq.verify_support(o, items, box))
        assert (seq.first_loose(o, items, box) is not None) == slow
        seen.add(slow)
    assert seen == {True, False}                                     # (both answers were met)


def test_a_cut_in_loose_scrap_is_brought_in_front_of_the_cut_that_closes_the_scrap():
    tp, s, objs = ring_job()
    order, items, box = replay_items(tp, s)
    small = next(k for k in order if items[k].bbox.width < 20)       # the little round part in the middle
    bad = [k for k in order if k != small] + [small]                 # the four bars first: it is then loose
    assert seq.first_loose(bad, items, box) is not None
    fixed, moved = seq.fix_loose(bad, items, box)
    assert moved == [small]
    assert seq.is_supported(fixed, items, box)
    assert fixed.index(small) < max(fixed.index(k) for k in order if k != small)
    assert sorted(fixed) == sorted(bad)


def test_a_sheet_that_is_not_a_rectangle_is_judged_by_the_slow_replay():
    tp, s, objs = ring_job()
    order, items, box = replay_items(tp, s)
    from iyicam.core.plate import Plate
    notched = Plate(0, 0, 250, 250, region=[[(0, 0), (250, 0), (250, 250), (0, 250)]])
    small = next(k for k in order if items[k].bbox.width < 20)
    bad = [k for k in order if k != small] + [small]
    assert seq.first_loose(bad, items, notched) is not None
    assert seq.fix_loose(bad, items, notched)[0] == bad              # (no paths to go by: it is left as it is)


def test_pulls_that_would_leave_a_cut_in_loose_scrap_are_given_up_and_the_rest_kept():
    """Four bars round a small part and a free part far away that wants to come before a bar: the pull that
    would close the ring round the small part is dropped, the harmless one is made."""
    tp, s, objs = ring_job()
    order, items, box = replay_items(tp, s)
    small = next(k for k in order if items[k].bbox.width < 20)
    bars = [k for k in order if k != small]
    base = bars + [small]
    base, _ = seq.fix_loose(base, items, box)                        # (a safe base order)
    assert seq.is_supported(base, items, box)
    wanted = {small: {base[0]}}                                      # the small part wants to be before the first bar
    out = seq.zone_order(base, wanted, items, 10, lambda o: (o, False, []), box)
    assert seq.is_supported(out, items, box)
    assert out.index(small) < out.index(base[0])
    # the same wish the other way round: the first bar wants to be after nothing but the small part is last
    out = seq.zone_order(base, {bars[-1]: {small}}, items, 10, lambda o: (o, False, []), box)
    assert seq.is_supported(out, items, box)


def test_the_replay_has_nothing_to_say_without_cut_areas_or_a_sheet():
    items = {k: seq.SeqItem(k, shapes.rectangle(30 * i, 0, 20, 20)) for i, k in enumerate("abc")}
    assert seq.first_loose(list("abc"), items, None) is None              # (no cut areas, no sheet: nothing is loose)
    assert seq.fix_loose(list("abc"), items, None) == (list("abc"), [])


def test_a_part_with_inner_pieces_is_moved_with_them_when_it_is_brought_forward():
    """The inner pieces of a cut that is moved in front of a wall can be walls themselves: it must not fail."""
    outer = seq.SeqItem("o", shapes.rectangle(0, 0, 100, 100), kerf=[[(-3, -3), (103, -3), (103, 103), (-3, 103)]])
    inner = seq.SeqItem("i", shapes.rectangle(40, 40, 20, 20), kerf=[[(37, 37), (63, 37), (63, 63), (37, 63)]])
    seq.containment([outer, inner])
    items = {"o": outer, "i": inner}
    order, moved = seq.fix_loose(["i", "o"], items, None)
    assert sorted(order) == ["i", "o"]


def test_the_shortened_route_keeps_the_cuts_that_have_to_come_first_in_front():
    """2-opt reverses stretches of the route; a stretch that holds a cut and the cut it has to come before is not
    reversed (here the shorter route 1, 3, 2 would cut the second before the first)."""
    pts = np.array([[1.0, 0.0], [3.0, 0.0], [2.0, 0.0]])
    start = np.array([0.0, 0.0])
    assert seq._two_opt(pts, start) == [0, 2, 1]                      # (shortest: left to right)
    assert seq._two_opt(pts, start, before=[(1, 2)]) == [0, 1, 2]     # point 1 first, then 2: the stretch stays
    assert seq._two_opt(pts, start, before=[(0, 1)]) == [0, 2, 1]     # (a wish the shorter route keeps: reversed)


def test_the_support_map_names_the_cuts_that_take_the_sheet_away_from_a_closing_zone():
    """A tall part with a lower part beside its lower right corner: cut after it, the lower part leaves the zone without sheet; the map
    says so, says the lower part is what would have to come after, and that nothing is wrong when it does."""
    tall = shapes.rectangle(20, 60, 60, 150)
    low = shapes.rectangle(88, 0, 50, 180)                           # beside the tall one's lower right, higher than its zone reaches
    p, s, objs = make_project([[tall], [low]], size=(220, 300))
    t = add_tp(p, "profile", end_mill(6), {"through": True}, objs)
    t.sequence = {"strategy": "rows_bl", "smart_starts": False}
    tp = compute_toolpath(p, t, s).toolpaths[0]
    items = []
    for o in objs:
        it = seq.SeqItem(f"{o.id}:0", o.contours[0], kerf=clip.offset_lines([o.contours[0].flatten(0.05)], R, closed=True))
        c = next(c for c in tp.cuts if c["key"] == it.key)
        it.loop, it.radius = c["loop"], R
        items.append(it)
    seq.containment(items)
    smap = seq.SupportMap(items, {"strategy": "rows_bl", "corner_distance": 0.0}, seq.BBox(0, 0, 220, 300))
    a, b = items[0].key, items[1].key
    sup, _, blockers = smap.best(a, {b}, band=True)
    assert sup < seq.FULL_SUPPORT and blockers == {b}                # the lower part first: no held place in the zone
    assert smap.cheapest(a, {b}, band=True) == {b}
    sup, _, blockers = smap.best(a, set(), band=True)
    assert sup >= seq.FULL_SUPPORT and not blockers                  # the tall part first: held


def _map_of(rects, size=(220, 300), cfg=None):
    """Support map of rectangles (x, y, w, h) cut with a 6 mm end mill, and their items."""
    objs_shapes = [[shapes.rectangle(*r)] for r in rects]
    p, s, objs = make_project(objs_shapes, size=size)
    t = add_tp(p, "profile", end_mill(6), {"through": True}, objs)
    t.sequence = {"strategy": "rows_bl", "smart_starts": False}
    tp = compute_toolpath(p, t, s).toolpaths[0]
    items = []
    for o in objs:
        it = seq.SeqItem(f"{o.id}:0", o.contours[0], kerf=clip.offset_lines([o.contours[0].flatten(0.05)], R, closed=True))
        c = next(c for c in tp.cuts if c["key"] == it.key)
        it.loop, it.radius = c["loop"], R
        items.append(it)
    seq.containment(items)
    return seq.SupportMap(items, dict({"strategy": "rows_bl", "corner_distance": 0.0}, **(cfg or {})), seq.BBox(0, 0, *size)), items


def test_the_support_map_keeps_the_plunge_clear_of_the_cut_areas_before_it():
    """A place where the plunging tool would touch the cut area of an earlier cut is no place to close at, even when
    the sheet beyond the cut is there (the rings only look away from the cut): a cut area that lies on the tool path
    itself, but not in the rings."""
    smap, items = _map_of([(20, 60, 60, 150), (150, 20, 6, 6)])
    a, small = items[0].key, items[1].key
    inf = smap.info[a]
    # the small cut's area lies right on the closing zone of the first cut (a square of 6 mm around its target)
    walker_pt = np.array(items[0].loop.point_at_length(inf["target"] / inf["S"] * items[0].loop.length()))
    small_item = items[1]
    small_item.kerf = [[(walker_pt[0] + dx, walker_pt[1] + dy) for dx, dy in ((-3, -3), (3, -3), (3, 3), (-3, 3))]]
    smap = seq.SupportMap(items, {"strategy": "rows_bl", "corner_distance": 0.0}, seq.BBox(0, 0, 220, 300))
    inf = smap.info[a]
    h1, h5, h0 = inf["hits"][small]
    assert h0.any() and not (h1 | h5).any()                       # (the plunge touches it, the rings do not)
    sup, k, blockers = smap.best(a, {small}, band=True)
    assert not h0[k]                                              # never the place it touches
    sup0, k0, _ = smap.best(a, set(), band=True)
    assert sup0 >= seq.FULL_SUPPORT


def test_the_closing_band_is_eight_mm_or_eight_percent_of_the_part():
    """The places that still count as the closing zone: 8 mm, or 8 % of the part's longer side, along the side."""
    smap, items = _map_of([(20, 60, 60, 150)])
    bb = items[0].loop.bbox()
    expect = max(seq.ZONE_BAND, seq.ZONE_BAND_FRACTION * max(bb.width, bb.height))
    assert abs(smap.info[items[0].key]["width"] - expect) < 1e-6
    assert seq.ZONE_BAND_FRACTION <= 0.1                        # (a wider band lets a cut close far from the corner)


def test_a_part_faces_the_neighbours_on_the_side_where_the_sheet_holds_it():
    """Left to right, bottom to top: a part is held from the right and from above. A neighbour on its right (or
    above it) is on its hold side; one on its left (or below) is not. The wish is: cut the part before the neighbour
    that stands on its hold side, never the other way round."""
    smap, items = _map_of([(20, 60, 50, 80), (78, 20, 50, 80)])         # the second one is 8 mm to the right and lower
    a, b = items[0].key, items[1].key
    face = seq.hold_contacts(items, {"strategy": "rows_bl"}, seq.BBox(0, 0, 220, 300))
    assert face[a][b] > 10                                              # a's right side looks at b
    assert face.get(b, {}).get(a, 0.0) < face[a][b]                     # b's side towards a is not its hold side
    wishes, strength = seq.hold_blockers(items, {"strategy": "rows_bl"}, seq.BBox(0, 0, 220, 300))
    assert wishes == {a: {b}} and strength[(a, b)] > 0                  # a before b


def test_the_part_underneath_is_cut_before_the_part_above_it_even_when_the_row_would_put_it_second():
    """Two parts one above the other, in one row by their corners (so left to right: the higher one, which stands
    further left, first). The lower part is held from above, by the higher one: the higher one is on its hold side, so
    the lower one goes first."""
    upper, lower = shapes.rectangle(20, 40, 50, 80), shapes.rectangle(30, 10, 50, 22)          # (8 mm between them)
    p, s, objs = make_project([[upper], [lower], [shapes.rectangle(150, 200, 50, 80)]], size=(220, 300))
    t = add_tp(p, "profile", end_mill(6), {"through": True}, objs)
    t.sequence = {"strategy": "rows_bl", "smart_starts": True}
    tp = compute_toolpath(p, t, s).toolpaths[0]
    ids = {o.id: n for n, o in enumerate(objs)}
    order = [ids[c["key"].split(":")[0]] for c in tp.cuts]
    assert order.index(1) < order.index(0)                              # the lower part first
    # (a row is no longer made of parts that are only near each other along the sweep: these two are two rows, so the
    # sweep alone puts the lower one first as well; the support is what the replay below checks)
    assert replay(tp, s) == []


def test_a_route_strategy_has_no_sweep_but_the_same_hold_side_as_the_start_points():
    """'Shortest travel' and 'smart route' have no sweep direction; the start points are made for bottom left -> top right
    then, and so is the hold side."""
    smap, items = _map_of([(20, 60, 50, 80), (78, 20, 50, 80)])
    a, b = items[0].key, items[1].key
    for strategy in ("nearest", "smart", "rows_bl", "diag_bl"):
        wishes, _ = seq.hold_blockers(items, {"strategy": strategy}, seq.BBox(0, 0, 220, 300))
        assert wishes == {a: {b}}, strategy
    wishes, _ = seq.hold_blockers(items, {"strategy": "rows_br"}, seq.BBox(0, 0, 220, 300))
    assert wishes == {b: {a}}                       # from the bottom right the other way round: the left one is on b's hold side


def test_the_wishes_do_not_depend_on_where_the_outline_starts():
    """Moving a start point (the loop is rotated) never changes the wishes: the samples start at the lowest left point."""
    smap, items = _map_of([(20, 60, 50, 80), (78, 20, 50, 80), (20, 150, 50, 80)])
    cfg, box = {"strategy": "rows_bl"}, seq.BBox(0, 0, 220, 300)
    before = seq.hold_contacts(items, cfg, box)
    from iyicam.core import vectorops
    for it in items:                                    # start every loop somewhere else
        start = it.loop.point_at_length(0.37 * it.loop.length())
        moved = vectorops.set_start_point(it.loop, start)
        assert moved.start != it.loop.start                 # (the start really moved)
        it.loop = moved
    after = seq.hold_contacts(items, cfg, box)
    assert {k: {j: round(v, 1) for j, v in d.items()} for k, d in before.items()} == \
           {k: {j: round(v, 1) for j, v in d.items()} for k, d in after.items()}


def test_a_big_sheet_is_replayed_too_so_its_support_is_kept_like_a_small_one():
    """The replay costs what lies near a cut (the cut areas are kept as pieces, a new one joins only what it touches), so a
    sheet of hundreds of cuts is replayed like any other: it used to be given up above 400 cuts, and then only neighbours
    traded places and the support was not kept."""
    assert seq.REPLAY_LIMIT >= 2000
    from conftest import make_project
    from iyicam.cam.job import compute_toolpath
    from conftest import add_tp
    upper, lower = shapes.rectangle(100, 40, 50, 80), shapes.rectangle(112, 10, 50, 22)       # one over the other, 8 mm apart
    vecs = [[shapes.rectangle(10 + c * 14, 200 + r * 14, 8, 8)] for r in range(6) for c in range(10)] + [[upper], [lower]]
    p, s, objs = make_project(vecs, size=(200, 300))
    t = add_tp(p, "profile", end_mill(2), {"through": True}, objs)
    t.sequence = {"strategy": "rows_bl", "smart_starts": True}
    old = seq.REPLAY_LIMIT
    seq.REPLAY_LIMIT = 20                       # (as if the sheet were bigger than the limit: no replay, only neighbours move)
    try:
        small_limit = compute_toolpath(p, t, s).toolpaths[0]
    finally:
        seq.REPLAY_LIMIT = old
    full = compute_toolpath(p, t, s).toolpaths[0]
    ids = {o.id: n for n, o in enumerate(objs)}
    order = [ids[c["key"].split(":")[0]] for c in full.cuts]
    assert order.index(len(objs) - 1) < order.index(len(objs) - 2)                          # the lower one first, even among 60 parts
    assert replay(full, s) == [] and len(small_limit.cuts) == len(full.cuts)

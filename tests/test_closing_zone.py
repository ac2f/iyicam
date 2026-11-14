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


def test_without_smart_start_points_the_order_is_the_strategys_own():
    tall = shapes.rectangle(20, 60, 60, 150)
    low = shapes.rectangle(88, 0, 50, 70)
    p, s, objs = make_project([[tall], [low]], size=(220, 300))
    t = add_tp(p, "profile", end_mill(6), {"through": True}, objs)
    t.sequence = {"strategy": "rows_bl", "smart_starts": False}
    order = [c["key"].split(":")[0] for c in compute_toolpath(p, t, s).toolpaths[0].cuts]
    assert order == [objs[1].id, objs[0].id]                            # row by row, the lowest first: nothing re-ordered


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

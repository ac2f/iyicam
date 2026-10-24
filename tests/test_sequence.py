"""Cut order and start points: nothing may ever be cut once it is loose.

The support checks here are independent of the sequencer: they rebuild the cut
areas from the toolpath moves and replay the job cut by cut.
"""
import math
import random

import pytest
from conftest import add_tp, end_mill, make_project

from iyicam.cam import profile
from iyicam.cam import sequence as seq
from iyicam.cam.job import compute_toolpath
from iyicam.cam.moves import ArcMove, DrillCycle, Feed, arc_sweep
from iyicam.core import clip, shapes, vectorops
from iyicam.core.geom import point_in_polygon

TOOL_R = 3.0
ALL = [k for k, _ in seq.STRATEGIES]


def cut_points(tp, cut):
    """XY points along the deepest cutting moves of one cut (the other passes follow the same path)."""
    a, b = cut["moves"]
    zmin = min((m.z for m in tp.moves[a:b] if hasattr(m, "z")), default=0.0)
    pos, pts = None, []
    for i, m in enumerate(tp.moves):
        if hasattr(m, "z") and not isinstance(m, DrillCycle):
            if (a <= i < b and isinstance(m, (Feed, ArcMove)) and pos is not None
                    and min(pos[2], m.z) < zmin + 1e-6 and min(pos[2], m.z) < -1e-6):
                if isinstance(m, ArcMove):
                    sw = arc_sweep(pos[0], pos[1], m)
                    r = math.hypot(pos[0] - m.cx, pos[1] - m.cy)
                    a0 = math.atan2(pos[1] - m.cy, pos[0] - m.cx)
                    n = max(2, int(abs(sw) * r / 0.5))
                    pts += [(m.cx + r * math.cos(a0 + sw * k / n), m.cy + r * math.sin(a0 + sw * k / n))
                            for k in range(n + 1)]
                else:
                    pts += [pos[:2], (m.x, m.y)]
            pos = (m.x, m.y, m.z)
    return pts


def plunge_point(tp, cut):
    """Where the tool first goes below the surface in a cut."""
    a, b = cut["moves"]
    pos = None
    for m in tp.moves[a:b]:
        if hasattr(m, "z") and not isinstance(m, DrillCycle):
            if m.z < -1e-6:
                return (m.x, m.y) if pos is None or pos[2] < -1e-6 else pos[:2]
            pos = (m.x, m.y, m.z)
    return None


def replay(tp, sheet, through=True):
    """Indices (in cut order) of cuts made in loose material, from the actual moves."""
    items, order = {}, []
    for c in tp.cuts:
        pts = cut_points(tp, c)
        if not pts:
            continue
        kerf = clip.offset_lines([pts], TOOL_R, closed=False)
        k = c["key"] + ("/f" if c.get("finish") else "")
        if k in items:
            items[k].kerf += kerf
            continue
        it = seq.SeqItem(k, c["loop"] if c.get("loop") is not None else
                         shapes.polyline(pts), frees=through and c["closed"], kerf=kerf)
        items[k] = it
        order.append(k)
    from iyicam.core.geom import BBox
    return seq.verify_support(order, items, BBox(0, 0, sheet.width, sheet.height))


def inside_first_ok(tp, objs):
    """Every vector contour is cut after all contours lying inside it."""
    src = {f"{o.id}:{i}": c for o in objs for i, c in enumerate(o.contours)}
    first = {}
    for c in tp.cuts:
        first.setdefault(c["key"], c["index"])
    for k, c in src.items():
        if not c.closed or k not in first:
            continue
        poly = c.flatten(0.05)
        for k2, c2 in src.items():
            if k2 == k or k2 not in first or not c.bbox().contains_box(c2.bbox()):
                continue
            L = c2.length()
            probes = [c2.point_at_length(L * f) for f in (0.0, 0.33, 0.66)]
            if sum(point_in_polygon(p, poly) for p in probes) >= 2 and first[k2] > first[k]:
                return False
    return True


def random_layout(seed, cols=3, rows=3, cell=95.0):
    """Parts with holes, parts nested in holes and engravings, on a grid."""
    rng = random.Random(seed)
    vecs = []
    for i in range(cols):
        for j in range(rows):
            x, y = 10 + i * cell, 10 + j * cell
            w, h = rng.uniform(70, 85), rng.uniform(70, 85)
            kind = rng.choice(["rect", "round", "circle"])
            if kind == "circle":
                outer = shapes.circle((x + 42, y + 42), min(w, h) / 2)
            else:
                outer = shapes.rectangle(x, y, w, h, corner_radius=8 if kind == "round" else 0)
            part = [outer]
            cx, cy = outer.bbox().center
            roll = rng.random()
            if roll < 0.35:  # a big hole with a small part in it
                part.append(shapes.rectangle(cx - 22, cy - 22, 44, 44))
                vecs.append(part)
                vecs.append([shapes.circle((cx, cy), 7)])
                continue
            if roll < 0.7:
                part.append(shapes.circle((cx - 15, cy), 6))
                part.append(shapes.circle((cx + 15, cy), 6))
            if rng.random() < 0.5:
                vecs.append([shapes.line((cx - 10, cy + 20), (cx + 10, cy + 20))])  # engraving
            vecs.append(part)
    rng.shuffle(vecs)
    return vecs


@pytest.mark.parametrize("seed", [1, 2])
@pytest.mark.parametrize("grouping", ["depth", "part"])
def test_every_strategy_keeps_material_supported(seed, grouping):
    vecs = random_layout(seed)
    p, s, objs = make_project(vecs, size=(300, 300))
    t = add_tp(p, "profile", end_mill(6), {"through": True}, objs)
    for strategy in ALL:
        t.sequence = {"strategy": strategy, "grouping": grouping}
        res = compute_toolpath(p, t, s)
        tp = res.toolpaths[0]
        assert res.error is None
        assert len({c["key"] for c in tp.cuts}) == sum(len(o.contours) for o in objs)
        assert inside_first_ok(tp, objs), strategy
        assert replay(tp, s) == [], strategy


def ring_with_small_part():
    """Four bars around a gap, so close that their cuts close a ring of scrap around a small part."""
    # 8 mm gaps: wider than the 6 mm tool (no part is touched), narrower than two cut widths
    bars = [shapes.rectangle(50, 50, 100, 30), shapes.rectangle(50, 126, 100, 30),
            shapes.rectangle(50, 88, 30, 30), shapes.rectangle(120, 88, 30, 30)]
    return [[b] for b in bars] + [[shapes.circle((100, 103), 6)]]


@pytest.mark.parametrize("strategy", ALL)
def test_scrap_enclosed_by_several_cuts_is_handled(strategy):
    p, s, objs = make_project(ring_with_small_part(), size=(250, 250))
    t = add_tp(p, "profile", end_mill(6), {"through": True}, objs)
    t.sequence = {"strategy": strategy}
    res = compute_toolpath(p, t, s)
    tp = res.toolpaths[0]
    assert replay(tp, s) == []
    first = {c["key"]: c["index"] for c in tp.cuts}
    small = f"{objs[4].id}:0"
    assert first[small] < max(first[f"{o.id}:0"] for o in objs[:4])


def test_island_rule_is_needed_for_the_ring():
    """Without the island analysis 'edges inwards' would cut the small part in loose scrap."""
    p, s, objs = make_project(ring_with_small_part(), size=(200, 206))  # the small part is in the centre
    t = add_tp(p, "profile", end_mill(6), {"through": True}, objs)
    t.sequence = {"strategy": "edges_in"}
    res = compute_toolpath(p, t, s)
    assert any("moved forward" in w for w in res.warnings)
    items = {}
    for c in res.toolpaths[0].cuts:
        items[c["key"]] = seq.SeqItem(c["key"], c["loop"], kerf=clip.offset_lines([c["loop"].flatten(0.05)],
                                                                                   TOOL_R, closed=True))
    seq.containment(list(items.values()))
    raw = seq.order_items(list(items.values()), "edges_in", "depth", (0, 0))
    assert seq.verify_support(raw, items) != []


def test_strategies_follow_their_pattern():
    sq = [[shapes.rectangle(10 + i * 60, 10 + j * 60, 40, 40)] for j in range(3) for i in range(3)]
    p, s, objs = make_project(sq, size=(200, 200))
    t = add_tp(p, "profile", end_mill(6), {"through": True}, objs)
    pos = {f"{o.id}:0": (round((o.bbox().center[0] - 30) / 60), round((o.bbox().center[1] - 30) / 60)) for o in objs}

    def cells(strategy):
        t.sequence = {"strategy": strategy}
        res = compute_toolpath(p, t, s)
        return [pos[k] for k in res.order]

    assert cells("rows_bl") == [(i, j) for j in range(3) for i in range(3)]
    assert cells("rows_tr") == [(i, j) for j in (2, 1, 0) for i in (2, 1, 0)]
    assert cells("zigzag_up") == [(0, 0), (1, 0), (2, 0), (2, 1), (1, 1), (0, 1), (0, 2), (1, 2), (2, 2)]
    assert cells("cols_bl") == [(i, j) for i in range(3) for j in range(3)]
    assert cells("zigzag_left")[:4] == [(2, 0), (2, 1), (2, 2), (1, 2)]
    diag = cells("diag_bl")
    assert [a + b for a, b in diag] == sorted(a + b for a, b in diag)
    assert cells("centre_out")[0] == (1, 1)
    assert cells("edges_in")[-1] == (1, 1)


def test_small_parts_first():
    vecs = [[shapes.circle((50 + i * 70, 50), 10 + 8 * k)] for i, k in enumerate([3, 0, 2, 1])]
    p, s, objs = make_project(vecs, size=(400, 150))
    t = add_tp(p, "profile", end_mill(6), {"through": True}, objs)
    t.sequence = {"strategy": "small_first"}
    order = compute_toolpath(p, t, s).order
    assert order == [f"{objs[i].id}:0" for i in (1, 3, 2, 0)]


def tight_grid(n=4, m=3, gap=8.0, corner_radius=0.0):
    return [[shapes.rectangle(10 + i * (50 + gap), 10 + j * (50 + gap), 50, 50, corner_radius=corner_radius)]
            for i in range(n) for j in range(m)]


@pytest.mark.parametrize("lead", ["none", "arc"])
def test_smart_starts_plunge_in_solid_material(lead):
    """Kerfs of neighbours overlap (gap 8 < 2 x 6 mm): plunging next to a cut neighbour would be in its
    slot. Every plunge keeps the margin from everything cut before it."""
    p, s, objs = make_project(tight_grid(), size=(260, 200))
    t = add_tp(p, "profile", end_mill(6), {"through": True, "lead": lead, "lead_length": 4}, objs)
    t.sequence = {"strategy": "zigzag_up", "smart_starts": True, "start_margin": 1.0}
    tp = compute_toolpath(p, t, s).toolpaths[0]
    done = []
    for c in tp.cuts:
        e = plunge_point(tp, c)
        for pts in done:
            d = min(math.dist(e, q) for q in pts)
            assert d - 2 * TOOL_R >= 1.0 - 0.05, (c["key"], d)
        assert c["ok"]
        done.append(cut_points(tp, c))


def test_vector_start_points_get_new_nodes_away_from_corners():
    p, s, objs = make_project(tight_grid(2, 1), size=(150, 80))
    before = [len(o.contours[0].segs) for o in objs]
    t = add_tp(p, "profile", end_mill(6), {"through": True}, objs)
    t.sequence = {"smart_starts": True}
    res = compute_toolpath(p, t, s)
    assert len(res.start_updates) == 2
    for o in objs:
        k = f"{o.id}:0"
        o.contours[0] = vectorops.set_start_point(o.contours[0], res.start_updates[k])
    for o, n in zip(objs, before):
        c = o.contours[0]
        assert len(c.segs) == n + 1  # a new node, not one of the corners
        assert min(math.dist(c.start, q) for q in o.bbox().corners()) >= 3.0
    again = compute_toolpath(p, t, s)
    assert again.start_updates == {} and again.order == res.order  # stable: nothing moves twice


def test_hand_set_start_points_are_kept():
    p, s, objs = make_project(tight_grid(2, 1), size=(150, 80))
    o = objs[0]
    o.contours[0] = vectorops.set_start_point(o.contours[0], (10.0, 30.0))  # middle of the left edge
    o.locked_starts = [0]
    t = add_tp(p, "profile", end_mill(6), {"through": True}, objs)
    t.sequence = {"smart_starts": True}
    res = compute_toolpath(p, t, s)
    assert f"{o.id}:0" not in res.start_updates
    cut = next(c for c in res.toolpaths[0].cuts if c["key"] == f"{o.id}:0")
    assert cut["start"] == pytest.approx((10.0 - TOOL_R, 30.0), abs=1e-6)


@pytest.mark.parametrize("smart", [False, True])
def test_moving_one_start_point_recalculates_only_from_that_cut(smart):
    p, s, objs = make_project(tight_grid(4, 2), size=(260, 140))
    t = add_tp(p, "profile", end_mill(6), {"through": True, "cut_depth": 18}, objs)
    t.sequence = {"strategy": "rows_bl", "smart_starts": smart}
    res = compute_toolpath(p, t, s)
    for k, q in res.start_updates.items():  # what the controller does with smart start points
        o = next(o for o in objs if o.id == k.split(":")[0])
        o.contours[0] = vectorops.set_start_point(o.contours[0], q)
    t.sequence["order"] = res.order
    base = compute_toolpath(p, t, s).toolpaths[0]
    k = 4
    key = res.order[k]
    o = next(o for o in objs if o.id == key.split(":")[0])
    x0, y0 = o.bbox().xmin, o.bbox().center[1]
    o.contours[0] = vectorops.set_start_point(o.contours[0], (x0, y0 + 7))
    o.locked_starts = [0]
    profile.CACHE_STATS.update(hits=0, misses=0)
    res2 = compute_toolpath(p, t, s)
    tp = res2.toolpaths[0]
    assert res2.order == res.order  # the order stays
    assert profile.CACHE_STATS["misses"] <= (len(res.order) - k if smart else 1)
    assert profile.CACHE_STATS["misses"] >= 1
    for c_old, c_new in zip(base.cuts[:k], tp.cuts[:k]):  # cuts before it are untouched
        a, b = c_old["moves"]
        assert tp.moves[slice(*c_new["moves"])] == base.moves[a:b]
    assert tp.cuts[k]["start"] == pytest.approx((x0 - TOOL_R, y0 + 7), abs=1e-6)


def test_manual_order_from_an_index_and_frozen_orders():
    assert seq.manual_from(list("abcdef"), 2, ["e", "a"]) == list("bceadf")
    assert seq.manual_from(list("abc"), 0, ["c"]) == list("cab")
    assert seq.manual_from(list("abc"), 9, ["a"]) == list("bca")
    sq = [[shapes.rectangle(10 + i * 60, 10, 40, 40)] for i in range(4)]
    p, s, objs = make_project(sq, size=(260, 80))
    t = add_tp(p, "profile", end_mill(6), {"through": True}, objs)
    keys = [f"{o.id}:0" for o in objs]
    t.sequence = {"auto_order": False, "order": [keys[2], keys[0], keys[3], keys[1]]}
    assert compute_toolpath(p, t, s).order == [keys[2], keys[0], keys[3], keys[1]]
    # a new vector joins at the end, a deleted one leaves
    new = __import__("iyicam.core.document", fromlist=["VectorObject"]).VectorObject(
        [shapes.rectangle(10, 60, 30, 10)], p.layers[0].id)
    s.vectors.append(new)
    t.selection.vector_ids.append(new.id)
    s.vectors.remove(objs[0])
    order = compute_toolpath(p, t, s).order
    assert order == [keys[2], keys[3], keys[1], f"{new.id}:0"]


def test_manual_order_cannot_cut_a_hole_after_its_outline():
    p, s, objs = make_project([[shapes.rectangle(10, 10, 80, 80), shapes.circle((50, 50), 10)]], size=(120, 120))
    t = add_tp(p, "profile", end_mill(6), {"through": True}, objs)
    outline, hole = f"{objs[0].id}:0", f"{objs[0].id}:1"
    t.sequence = {"auto_order": False, "order": [outline, hole]}
    res = compute_toolpath(p, t, s)
    assert res.order == [hole, outline]
    assert any("inside others are cut first" in w for w in res.warnings)


def test_parts_closer_than_the_tool_are_reported():
    p, s, objs = make_project(tight_grid(2, 1, gap=3.0), size=(150, 80))
    res = compute_toolpath(p, add_tp(p, "profile", end_mill(6), {"through": True}, objs), s)
    assert any("run into neighbouring parts" in w for w in res.warnings)


def test_later_toolpath_inside_freed_parts_is_reported():
    from iyicam.cam.job import compute_all
    part = shapes.rectangle(10, 10, 80, 80)
    hole = shapes.circle((50, 50), 4)
    p, s, objs = make_project([part, hole], size=(120, 120))
    add_tp(p, "profile", end_mill(6), {"through": True}, objs[:1])
    add_tp(p, "drill", end_mill(5, type="drill"), {"cut_depth": 10}, objs[1:])
    res = compute_all(p)
    drill = res[(p.toolpaths[1].id, s.id)]
    assert any("already cut free" in w for w in drill.warnings)
    p.toolpaths.reverse()  # drilling first is fine
    res = compute_all(p)
    assert not any("already cut free" in w for r in res.values() for w in r.warnings)


def test_sequence_settings_and_locked_starts_are_saved(tmp_path):
    from iyicam.core.document import Project
    p, s, objs = make_project(tight_grid(2, 1), size=(150, 80))
    objs[0].locked_starts = [0]
    t = add_tp(p, "profile", end_mill(6), {"through": True}, objs)
    t.sequence = {"strategy": "diag_tr", "auto_order": False, "order": ["x:0"], "smart_starts": True}
    p.save(tmp_path / "a.icam")
    q = Project.load(tmp_path / "a.icam")
    assert q.toolpaths[0].sequence == t.sequence
    assert q.sheets[0].vectors[0].locked_starts == [0]


def test_finish_pass_on_loose_parts_is_reported():
    p, s, objs = make_project([shapes.rectangle(10, 10, 50, 50)], size=(80, 80))
    res = compute_toolpath(p, add_tp(p, "profile", end_mill(6), {"through": True, "finish_pass": 0.5}, objs), s)
    assert any("already cut free: add tabs" in w for w in res.warnings)
    res = compute_toolpath(p, add_tp(p, "profile", end_mill(6), {"through": True, "finish_pass": 0.5,
                                                                   "tabs": True}, objs), s)
    assert not any("already cut free" in w for w in res.warnings)

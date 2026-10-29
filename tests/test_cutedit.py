"""Seeing and editing the cut order, the cutting directions and the start points: the smart strategy, what a
calculated cut says about itself, previews that change nothing, and start point edits that keep the order."""
import math
import random

import pytest

pytest.importorskip("PySide6.QtWidgets")

from gui_robot import process, rwin_gen  # noqa: E402

from iyicam.cam import cutinfo  # noqa: E402
from iyicam.cam import sequence as seq  # noqa: E402
from iyicam.core import shapes  # noqa: E402
from iyicam.core.document import Selection, ToolpathDef, VectorObject  # noqa: E402
from iyicam.core.tooling import Tool  # noqa: E402


@pytest.fixture
def rwin(tmp_path, monkeypatch):
    yield from rwin_gen(tmp_path, monkeypatch)


def parts(win, n=3, smart=False, **seqcfg):
    """``n`` rectangles in a row with a through profile toolpath: ``(ctrl, toolpath, vectors)``."""
    c = win.ctrl
    objs = [VectorObject([shapes.rectangle(50 + i * 120, 100, 100, 80)], c.layer_id, f"P{i}") for i in range(n)]
    c.add_vectors(objs, "t")
    t = ToolpathDef("Cut out", "profile", Tool("EM6", "end_mill", 6.0, 1, stepdown=6.0), {"through": True},
                    Selection("vectors", [o.id for o in objs]))
    t.sequence.update(seqcfg)
    if smart:
        t.sequence["smart_starts"] = True
    c.add_toolpath(t)
    process()
    return c, t, objs


# -- the smart strategy -----------------------------------------------------------------------------------------
@pytest.mark.parametrize("seed", range(12))
def test_smart_order_is_never_longer_than_nearest_next_and_keeps_every_item(seed):
    r = random.Random(seed)
    items = [seq.SeqItem(f"{i}:0", shapes.rectangle(r.uniform(0, 1000), r.uniform(0, 600), r.uniform(10, 60),
                                                    r.uniform(10, 60))) for i in range(r.randint(2, 80))]
    seq.containment(items)
    nearest = seq.rank(items, "nearest", (0, 0))
    smart = seq.rank(items, "smart", (0, 0))
    assert sorted(i.key for i in smart) == sorted(i.key for i in items)
    assert seq.route_length(smart, (0, 0)) <= seq.route_length(nearest, (0, 0)) + 1e-9


def test_smart_order_finds_a_shorter_route_than_nearest_next_on_most_layouts():
    shorter = 0
    for seed in range(20):
        r = random.Random(100 + seed)
        items = [seq.SeqItem(f"{i}:0", shapes.rectangle(r.uniform(0, 1000), r.uniform(0, 600), 20, 20))
                 for i in range(r.randint(15, 60))]
        seq.containment(items)
        a = seq.route_length(seq.rank(items, "nearest", (0, 0)), (0, 0))
        b = seq.route_length(seq.rank(items, "smart", (0, 0)), (0, 0))
        shorter += b < a - 1e-6
    assert shorter >= 14


def test_smart_order_still_cuts_inner_pieces_first():
    outer = seq.SeqItem("o:0", shapes.rectangle(0, 0, 300, 300))
    holes = [seq.SeqItem(f"h{i}:0", shapes.circle((60 + 60 * i, 150), 15)) for i in range(4)]
    other = [seq.SeqItem(f"p{i}:0", shapes.rectangle(400 + 40 * i, 20 * (i % 3), 30, 30)) for i in range(6)]
    items = [outer] + holes + other
    for grouping in ("depth", "part"):
        order, _ = seq.sequence(items, {"strategy": "smart", "grouping": grouping}, (0, 0))
        assert sorted(order) == sorted(i.key for i in items)
        assert all(order.index(h.key) < order.index("o:0") for h in holes)


def test_smart_order_of_a_big_job_does_not_take_forever():
    r = random.Random(5)
    items = [seq.SeqItem(f"{i}:0", shapes.rectangle(r.uniform(0, 3000), r.uniform(0, 2000), 20, 20)) for i in range(600)]
    seq.containment(items)
    assert len(seq.rank(items, "smart", (0, 0))) == 600           # (over the limit: the nearest-next order)


# -- what a cut says about itself --------------------------------------------------------------------------------
def test_every_cut_knows_the_path_it_walks_and_where_it_ends(rwin):
    win, _ = rwin
    c, t, objs = parts(win, smart=True)
    cuts = c.cuts_of(t.id)
    assert [x["index"] for x in cuts] == [0, 1, 2]
    for cut in cuts:
        assert cut["path"].closed and cut["loop"] is cut["path"]
        assert cut["end"] == pytest.approx(cut["start"], abs=0.5) or math.dist(cut["end"], cut["start"]) < 80
        assert 0.0 <= cut["held"] <= 1.0 and cut["corner"] >= 0.0
    links = cutinfo.route(cuts, c.route_home())
    assert len(links) == 3 and links[0][0] == tuple(c.route_home())
    assert links[1][0] == tuple(cuts[0]["end"]) and links[1][1] == tuple(cuts[1]["entry"])
    assert cutinfo.route_length(cuts, c.route_home()) == pytest.approx(sum(math.dist(a, b) for a, b in links))


def test_direction_of_a_cut_follows_the_toolpath_and_the_setting(rwin):
    win, _ = rwin
    c, t, _ = parts(win, directions={})
    for setting, want in (("cw", "cw"), ("ccw", "ccw")):
        c.set_sequence(t.id, direction=setting)
        assert {cutinfo.direction(x["path"]) for x in c.cuts_of(t.id)} == {want}
    assert cutinfo.direction(shapes.polyline([(0, 0), (1, 0), (2, 3)], False)) is None
    assert cutinfo.DIRECTION_ARROWS["cw"] != cutinfo.DIRECTION_ARROWS["ccw"]


def test_explanation_of_a_cut(rwin):
    win, _ = rwin
    c, t, objs = parts(win, smart=True)
    c.set_sequence(t.id, direction="cw")
    cut = c.cuts_of(t.id)[0]
    e = cutinfo.explain(cut)
    assert e["direction"] == "cw" and e["quality"] in ("good", "warn") and e["place"]
    assert any("clockwise" in ln for ln in e["lines"]) and any("Closes on the" in ln for ln in e["lines"])
    key = cut["key"]
    c.start_at_place(key, "se")
    hand = cutinfo.explain(next(x for x in c.cuts_of(t.id) if x["key"] == key))
    assert hand["quality"] == "hand" and any("by hand" in ln for ln in hand["lines"])
    c.set_sequence(t.id, smart_starts=False)
    plain = cutinfo.explain(next(x for x in c.cuts_of(t.id) if x["key"] == objs[1].id + ":0"))
    assert plain["quality"] == "plain" and any("smart start points are off" in ln for ln in plain["lines"])


def test_places_on_a_box_in_words():
    from iyicam.core.geom import BBox
    b = BBox(0, 0, 100, 100)
    assert cutinfo.place_on((100, 50), b) == "right side" and cutinfo.place_on((50, 0), b) == "bottom side"
    assert cutinfo.place_on((95, 95), b) == "top right corner" and cutinfo.place_on((5, 5), b) == "bottom left corner"
    assert cutinfo.place_on((50, 50), b) == "middle"
    assert cutinfo.held_word(0.9) == "well held" and cutinfo.held_word(0.5) == "held" and cutinfo.held_word(0.1) == "weakly held"


# -- previews -------------------------------------------------------------------------------------------------------
def test_a_preview_changes_nothing_until_it_is_applied(rwin):
    win, robot = rwin
    c, t, objs = parts(win, strategy="rows_bl")
    keys = [f"{o.id}:0" for o in objs]
    assert c.cut_order(t.id) == keys
    before_seq = dict(t.sequence)
    before_undo = c.undo_stack.undo_label
    seen = []
    c.cut_view_changed.connect(lambda: seen.append(1))
    p = c.preview_cut_order(t.id, {"strategy": "rows_br"})
    assert p is not None and c.cut_preview is p and seen
    assert p.order_before == keys and p.order_after == keys[::-1] and p.reordered == 2
    assert [x["key"] for x in c.cuts_of(t.id, preview=True)] == keys[::-1]
    assert c.cut_order(t.id) == keys and t.sequence == before_seq and c.undo_stack.undo_label == before_undo
    assert [x["key"] for x in c.cuts_of(t.id)] == keys                       # (what is calculated is untouched)
    assert "2 cut(s)" in p.summary() and p.changed
    assert c.apply_cut_preview() and c.cut_preview is None
    assert c.cut_order(t.id) == keys[::-1] and t.sequence["strategy"] == "rows_br"
    assert c.settings.sequence_defaults["strategy"] == "rows_br"            # (remembered for new toolpaths)
    c.undo()
    assert c.cut_order(t.id) == keys
    assert robot.problems() == []


def test_clearing_a_preview_leaves_everything_as_it_was(rwin):
    win, _ = rwin
    c, t, objs = parts(win)
    c.preview_cut_order(t.id, {"strategy": "rows_br"})
    c.clear_cut_preview()
    assert c.cut_preview is None and [x["key"] for x in c.cuts_of(t.id, preview=True)] == c.cut_order(t.id)
    assert t.sequence.get("strategy", "nearest") == "nearest"
    c.clear_cut_preview()                                                     # (nothing to clear: nothing happens)


def test_the_preview_of_a_direction_counts_the_cuts_that_turn_round(rwin):
    win, _ = rwin
    c, t, _ = parts(win)
    c.set_sequence(t.id, direction="cw")
    p = c.preview_cut_order(t.id, {"direction": "ccw"})
    assert p.directions == 3 and {cutinfo.direction(x["path"]) for x in p.cuts} == {"ccw"}
    assert {cutinfo.direction(x["path"]) for x in c.cuts_of(t.id)} == {"cw"}
    assert "3 cut(s) cut the other way round" in p.summary()
    assert c.preview_cut_order(t.id, {"direction": "cw"}).directions == 0


def test_the_preview_of_smart_start_points_shows_where_they_would_go(rwin):
    win, _ = rwin
    c, t, objs = parts(win, strategy="rows_bl")
    starts = [o.contours[0].start for o in objs]
    p = c.preview_cut_order(t.id, smart_starts=True)
    assert p.smart_starts and p.starts and all(k in {f"{o.id}:0" for o in objs} for k in p.starts)
    assert [o.contours[0].start for o in objs] == starts                       # (nothing moved yet)
    for key, (old, new) in p.starts.items():
        assert math.dist(old, new) > 1e-3
    assert f"{len(p.starts)} start point(s) would move" in p.summary()
    assert c.apply_cut_preview()
    after = {o.id: o.contours[0].start for o in objs}
    for key, (old, new) in p.starts.items():
        assert after[key.split(":")[0]] == pytest.approx(new, abs=1e-3)
    assert c.cut_order(t.id) == [f"{o.id}:0" for o in objs]                    # (the order stayed)


def test_a_preview_of_a_toolpath_that_does_not_exist_is_nothing(rwin):
    win, _ = rwin
    c, t, _ = parts(win)
    assert c.preview_cut_order("nope", {"strategy": "smart"}) is None and c.cut_preview is None
    assert c.cuts_of("nope") == [] and not c.apply_cut_preview()


def test_the_route_of_the_smart_order_is_shorter_than_the_route_of_a_bad_one(rwin):
    win, _ = rwin
    c, t, objs = parts(win, n=3)
    # parts in a scattered layout: the order of the strategy "rows" zig-zags, smart does not
    for i, o in enumerate(c.sheet.vectors):
        o.contours = [shapes.rectangle([50, 450, 250][i], [50, 50, 300][i], 60, 60)]
    c.set_sequence(t.id, strategy="small_first", auto_order=True)
    bad = c.route_length(t.id)
    p = c.preview_cut_order(t.id, {"strategy": "smart"})
    assert p.route_before == pytest.approx(bad) and p.route_after <= bad + 1e-9
    assert "travel between the cuts" in p.summary()


# -- start points -------------------------------------------------------------------------------------------------------
def test_moving_a_start_point_keeps_the_order_and_the_start_by_hand(rwin):
    win, _ = rwin
    c, t, objs = parts(win, smart=True)
    keys = [f"{o.id}:0" for o in objs]
    c.set_sequence(t.id, strategy="rows_br")
    order = c.cut_order(t.id)
    key = keys[1]
    o = objs[1]
    before = o.contours[0].start
    assert c.move_start(key, 30.0)
    assert math.dist(o.contours[0].start, before) > 1.0 and o.locked_starts == [0]
    assert c.cut_order(t.id) == order and [x["key"] for x in c.cuts_of(t.id)] == order
    assert c.move_start(key, -30.0) and o.contours[0].start == pytest.approx(before, abs=1e-6)
    assert not c.move_start(key, 0.0) and not c.move_start("nope:0", 5.0)
    c.undo()
    c.undo()
    back = c.find_vector(o.id)                                                 # (undo brings back new objects)
    assert back.locked_starts == [] and back.contours[0].start == pytest.approx(before, abs=1e-6)


@pytest.mark.parametrize("place,want", [("sw", (50, 100)), ("se", (150, 100)), ("nw", (50, 180)), ("ne", (150, 180)),
                                        ("s", (100, 100)), ("n", (100, 180)), ("w", (50, 140)), ("e", (150, 140))])
def test_a_vector_can_start_at_a_corner_or_the_middle_of_a_side(rwin, place, want):
    win, _ = rwin
    c, t, objs = parts(win, n=1)
    key = f"{objs[0].id}:0"
    assert c.start_at_place(key, place)
    assert c.find_vector(objs[0].id).contours[0].start == pytest.approx(want, abs=1e-6)
    assert c.find_vector(objs[0].id).locked_starts == [0]
    assert c.cut_order(t.id) == [key]


def test_a_start_that_is_not_possible_is_refused(rwin):
    win, _ = rwin
    c, t, objs = parts(win, n=1)
    open_line = VectorObject([shapes.polyline([(0, 0), (50, 0), (50, 50)], False)], c.layer_id, "open")
    c.add_vectors([open_line], "t")
    assert not c.start_at_place(f"{open_line.id}:0", "sw") and not c.move_start(f"{open_line.id}:0", 5.0)
    assert not c.start_at_place(f"{objs[0].id}:0", "middle") and not c.start_at_place("nope:0", "sw")
    assert not c.smart_start_one(t.id, f"{open_line.id}:0")


def test_smart_start_for_one_cut_takes_it_back_from_the_hand(rwin):
    win, _ = rwin
    c, t, objs = parts(win, smart=True)
    key = f"{objs[0].id}:0"
    c.start_at_place(key, "nw")
    hand = objs[0].contours[0].start
    assert c.find_vector(objs[0].id).locked_starts == [0]
    others = [o.contours[0].start for o in objs[1:]]
    moved = c.smart_start_one(t.id, key)
    o = c.find_vector(objs[0].id)
    assert o.locked_starts == []
    assert [x.contours[0].start for x in objs[1:]] == others                  # (only that cut)
    assert moved == (math.dist(o.contours[0].start, hand) > 1e-3)
    assert c.cut_preview is None


def test_releasing_one_start_point(rwin):
    win, _ = rwin
    c, t, objs = parts(win, smart=True)
    k0, k1 = f"{objs[0].id}:0", f"{objs[1].id}:0"
    c.start_at_place(k0, "sw")
    c.start_at_place(k1, "se")
    assert c.unlock_start(k0)
    assert c.find_vector(objs[0].id).locked_starts == [] and c.find_vector(objs[1].id).locked_starts == [0]
    assert not c.unlock_start(k0) and not c.unlock_start("nope:0")


def test_the_cut_that_is_looked_at(rwin):
    win, _ = rwin
    c, t, objs = parts(win)
    seen = []
    c.cut_view_changed.connect(lambda: seen.append(c.focus_cut))
    c.set_focus_cut(f"{objs[1].id}:0")
    c.set_focus_cut(f"{objs[1].id}:0")                                         # (the same: no second signal)
    c.set_focus_cut(None)
    assert seen == [f"{objs[1].id}:0", None]

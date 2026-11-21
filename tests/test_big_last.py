"""Big parts last: a big part is cut after the smaller parts beside it and between its arms; Auto picks the strategy."""
from iyicam.cam import sequence as seq
from iyicam.core.geom import Contour


def rect(key, x, y, w, h):
    return seq.SeqItem(key, Contour.polyline([(x, y), (x + w, y), (x + w, y + h), (x, y + h)], closed=True), radius=2.0)


def c_shape(key, x, y):
    """A C open to the right, 300 x 300, its arms 60 thick."""
    pts = [(x, y), (x + 300, y), (x + 300, y + 60), (x + 60, y + 60), (x + 60, y + 240), (x + 300, y + 240),
           (x + 300, y + 300), (x, y + 300)]
    return seq.SeqItem(key, Contour.polyline(pts, closed=True), radius=2.0)


def order(items, **cfg):
    return seq.sequence(items, {"strategy": "cols_bl", **cfg}, (0.0, 0.0))[0]


def test_a_part_between_the_arms_of_a_big_one_is_cut_before_it():
    items = [c_shape("C", 0, 0), rect("s", 120, 120, 80, 60)]       # in the mouth of the C, not inside it
    assert order(items, big_last=False) == ["C", "s"]                 # (columns: the C starts more to the left)
    assert order(items) == ["s", "C"]
    assert ("s", "C") in seq.big_last_constraints(items)


def test_a_small_part_beside_a_big_one_is_cut_before_it_a_far_one_is_not():
    big = rect("B", 100, 100, 400, 400)
    near = rect("n", 32, 200, 60, 60)           # 8 mm to the left: less than 3 tool diameters (12 mm)
    far = rect("f", 20, 20, 40, 40)             # far below on the left
    items = [big, near, far]
    o, _ = seq.sequence(items, {"strategy": "nearest"}, (300.0, 600.0))      # (the tool comes from above the big part)
    assert o.index("n") < o.index("B")
    pairs = seq.big_last_constraints(items, strategy="nearest")
    assert ("n", "B") in pairs and ("f", "B") not in pairs


def test_a_small_part_on_the_side_the_sheet_still_holds_keeps_the_order_of_the_sweep():
    big = rect("B", 0, 0, 400, 400)
    right = rect("r", 408, 100, 60, 60)         # right of it: columns from the bottom left still hold it there
    assert seq.big_last_constraints([big, right], strategy="cols_bl") == []
    assert order([big, right]) == ["B", "r"]


def test_parts_of_about_the_same_size_and_holes_are_not_moved():
    a, b = rect("a", 0, 0, 100, 100), rect("b", 105, 0, 120, 100)    # 1.2 times: not big
    assert seq.big_last_constraints([a, b]) == []
    outer, hole = rect("o", 0, 0, 300, 300), rect("h", 100, 100, 50, 50)
    seq.containment([outer, hole])
    assert seq.big_last_constraints([outer, hole]) == []             # (a hole is cut first anyway)


def test_a_frozen_order_and_the_orders_with_a_purpose_are_left_as_they_are():
    items = [c_shape("C", 0, 0), rect("s", 120, 120, 80, 60)]
    assert order(items, auto_order=False, order=["C", "s"]) == ["C", "s"]
    assert seq.sequence(items, {"strategy": "centre_out"}, (0.0, 0.0))[0] == \
        seq.sequence(items, {"strategy": "centre_out", "big_last": False}, (0.0, 0.0))[0]


def test_auto_chooses_a_strategy_for_the_vectors_and_says_which():
    items = [rect(f"r{i}", 200 * i, 0, 100, 100) for i in range(6)]          # a row along X
    info: dict = {}
    o, _ = seq.sequence(items, {"strategy": "auto"}, (0.0, 0.0), info=info)
    assert info["strategy"] in seq.AUTO_CANDIDATES
    best = min(seq.route_length([{i.key: i for i in items}[k] for k in seq.sequence(items, {"strategy": c}, (0.0, 0.0))[0]],
                                (0.0, 0.0)) for c in seq.AUTO_CANDIDATES)
    assert abs(seq.route_length([{i.key: i for i in items}[k] for k in o], (0.0, 0.0)) - best) < 1e-6
    assert seq.settings(None)["strategy"] == "auto" and seq.settings(None)["big_last"] is True


def test_apply_before_keeps_the_order_where_it_can():
    items = {k: rect(k, 0, 0, 1, 1) for k in "abcd"}
    assert seq.apply_before(list("abcd"), [("d", "b")], items) == list("acdb")      # (the big one waits; the rest keep their places)

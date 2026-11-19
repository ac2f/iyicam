"""Numbering the cuts by clicking them: from any number, from the end, swapping two; the numbers shown as they are made;
a project whose stored order does not hold every cut. Also: the follow-on strategy, the strategies in groups, a new
toolpath that starts outside with the default strategy."""
import pytest

pytest.importorskip("PySide6.QtWidgets")

from gui_robot import process, rwin_gen  # noqa: E402
from PySide6.QtCore import Qt  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402
from test_cutorder_panel import keys_of, opened  # noqa: E402
from test_gui import at  # noqa: E402

from iyicam.cam import sequence as seq  # noqa: E402
from iyicam.core import shapes  # noqa: E402
from iyicam.gui.tools import OrderTool  # noqa: E402


@pytest.fixture
def rwin(tmp_path, monkeypatch):
    yield from rwin_gen(tmp_path, monkeypatch)


def click(win, o, mods=Qt.NoModifier):
    bb = o.contours[0].bbox()
    QTest.mouseClick(win.canvas, Qt.LeftButton, mods, at(win.canvas, bb.xmin, (bb.ymin + bb.ymax) / 2))
    process()


def start(panel, mode, at_=1):
    panel.pick_mode.setCurrentIndex(panel.pick_mode.findData(mode))
    panel.start_at.setValue(at_)
    panel.pick.click()
    process()


@pytest.mark.parametrize("auto", [True, False])
def test_clicked_cuts_take_the_places_from_the_number_chosen(rwin, auto):
    win, _ = rwin
    c, t, objs, panel = opened(win, n=5, auto=auto)
    k = keys_of(objs)
    start(panel, "from", 2)
    tool = win.canvas.tool
    assert isinstance(tool, OrderTool) and tool.own_numbers
    click(win, objs[4])
    click(win, objs[3])
    assert tool.result() == [k[0], k[4], k[3], k[1], k[2]]          # (shown before it is applied)
    win.canvas.grab()                                               # (the numbers are drawn without trouble)
    QTest.keyClick(win.canvas, Qt.Key_Return)
    process()
    assert c.cut_order(t.id) == [k[0], k[4], k[3], k[1], k[2]] and t.sequence["auto_order"] is False


def test_numbering_from_the_end_and_a_cut_clicked_again_is_taken_back(rwin):
    win, _ = rwin
    c, t, objs, panel = opened(win, n=5)
    k = keys_of(objs)
    start(panel, "end", 1)
    click(win, objs[0])                                             # the first clicked is cut last
    click(win, objs[2])
    click(win, objs[1])
    click(win, objs[1])                                             # (taken back)
    QTest.keyClick(win.canvas, Qt.Key_Return)
    process()
    assert c.cut_order(t.id) == [k[1], k[3], k[4], k[2], k[0]]


def test_swapping_two_cuts(rwin):
    win, _ = rwin
    c, t, objs, panel = opened(win, n=4)
    k = keys_of(objs)
    start(panel, "swap")
    assert not panel.start_at.isEnabled()
    click(win, objs[0])
    click(win, objs[3])
    click(win, objs[1])                                             # (a third one waits for its pair: nothing changes)
    QTest.keyClick(win.canvas, Qt.Key_Return)
    process()
    assert c.cut_order(t.id) == [k[3], k[1], k[2], k[0]]


def test_ctrl_click_goes_on_after_that_cut(rwin):
    win, _ = rwin
    c, t, objs, panel = opened(win, n=5)
    k = keys_of(objs)
    start(panel, "from", 1)
    click(win, objs[2], Qt.ControlModifier)                        # numbering goes on after #3
    click(win, objs[0])
    QTest.keyClick(win.canvas, Qt.Key_Return)
    process()
    assert c.cut_order(t.id) == [k[1], k[2], k[0], k[3], k[4]]


def test_cuts_the_stored_order_does_not_have_are_numbered_too(rwin):
    win, _ = rwin
    c, t, objs, panel = opened(win, n=3)
    k = keys_of(objs)
    t.sequence["order"] = []                                        # (as a project opened with kept toolpaths can be)
    assert c.sheet_cut_keys(t.id) == k
    c.set_manual_order(t.id, 0, [k[2]])
    process()
    assert c.cut_order(t.id)[0] == k[2] and set(c.cut_order(t.id)) == set(k)


def test_order_helpers():
    assert seq.manual_from_end(list("abcde"), 0, ["a", "c"]) == list("bdeca")
    assert seq.manual_from_end(list("abcde"), 1, ["a"]) == list("bcdae")
    assert seq.swapped(list("abcd"), [("a", "d"), ("b", "c")]) == list("dcba")


def test_follow_on_goes_to_the_start_point_nearest_the_end_of_the_last_cut():
    # three squares; their start points decide: the next is the one whose start is nearest where the last cut ended
    a = shapes.rectangle(0, 0, 10, 10)                         # starts at (0, 0)
    b = shapes.rectangle(100, 0, 10, 10)
    c = shapes.rectangle(30, 0, 10, 10)
    items = [seq.SeqItem("a:0", a), seq.SeqItem("b:0", b), seq.SeqItem("c:0", c)]
    seq.containment(items)
    got = [it.key for it in seq.rank(items, "follow", (0, 0))]
    assert got == ["a:0", "c:0", "b:0"]
    assert seq.strategy_group("follow") == "Route (shortest travel)"
    order, _ = seq.sequence(items, {"strategy": "follow"})
    assert sorted(order) == ["a:0", "b:0", "c:0"]


def test_the_strategies_come_in_groups_and_none_is_lost():
    g = seq.grouped_strategies()
    assert {k for k, _, _ in g} == {k for k, _ in seq.STRATEGIES}
    groups = [x[2] for x in g]
    assert groups == sorted(groups, key=lambda x: [n for n, _ in seq.STRATEGY_GROUPS].index(x))   # (each group in one piece)
    assert "follow" in dict(seq.STRATEGIES) and "artcam" not in " ".join(v.lower() for _, v in seq.STRATEGIES)


def test_the_panel_lists_strategies_under_headings(rwin):
    win, _ = rwin
    c, t, objs, panel = opened(win, n=2)
    box = panel.strategy
    heads = [box.itemText(i) for i in range(box.count()) if box.itemData(i) is None]
    assert heads == [n for n, _ in seq.STRATEGY_GROUPS]
    assert box.currentData() == t.sequence.get("strategy", seq.DEFAULTS["strategy"])


def test_a_new_toolpath_starts_outside_and_with_the_default_strategy(rwin):
    win, _ = rwin
    c = win.ctrl
    s = c.settings
    s.remember("toolpath:profile:params", {"side": "inside", "through": True})
    s.sequence_defaults.update({"strategy": "rows_tr", "smart_starts": True})
    from iyicam.gui.dialogs import ToolpathDialog
    dlg = ToolpathDialog(c, "profile")
    assert dlg.form.values()["side"] == "outside" and dlg.form.values()["through"] is True
    dlg.close()
    _, t, _ = __import__("test_cutedit").parts(win, n=1)
    assert t.sequence.get("strategy", seq.DEFAULTS["strategy"]) == seq.DEFAULTS["strategy"]
    assert t.sequence.get("smart_starts") is True

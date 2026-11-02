"""Changing where the cuts of the vectors start, by hand and in a practical way: a click or a slide on the drawing
that clings to corners and middles, a ring that shows where it will go, the next and the previous corner on a key,
and the same for many vectors at once as one change."""
import math

import pytest

pytest.importorskip("PySide6.QtWidgets")

from gui_robot import process, rwin_gen  # noqa: E402
from PySide6.QtCore import QPointF, Qt  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402
from test_cutedit import parts  # noqa: E402
from test_cutorder_panel import near  # noqa: E402
from test_gui import at  # noqa: E402

from iyicam.core import shapes, vectorops  # noqa: E402
from iyicam.core.document import VectorObject  # noqa: E402
from iyicam.core.geom import Contour  # noqa: E402
from iyicam.gui import snap as snapping  # noqa: E402
from iyicam.gui.tools import StartPointTool  # noqa: E402

RING = (214, 36, 159)                  # the colour of the ring that shows where the start would go


@pytest.fixture
def rwin(tmp_path, monkeypatch):
    yield from rwin_gen(tmp_path, monkeypatch)


class Ev:
    def __init__(self, cv, world, button=Qt.LeftButton, mods=Qt.NoModifier):
        self._p, self._b, self._m = cv.world_to_screen(*world), button, mods

    def position(self):
        return QPointF(self._p)

    def button(self):
        return self._b

    def modifiers(self):
        return self._m


class Key:
    def __init__(self, key):
        self._k = key

    def key(self):
        return self._k


def steps(c) -> int:
    return len(c.undo_stack._undo)


@pytest.fixture
def view(rwin):
    """Three rectangles (50..150 x 100..180, then 120 mm further on each) with a profile toolpath, at 1.5 px/mm: the
    reach of 10 px is 6.7 mm."""
    win, robot = rwin
    c, cv = win.ctrl, win.canvas
    c, t, objs = parts(win, n=3)
    s = c.settings
    s.snap_objects, s.snap_grid, s.snap_align, s.snap_edges, s.snap_radius = True, False, True, False, 10.0
    cv.scale = 1.5
    cv.cx, cv.cy = 200.0, 150.0
    cv.update()
    process()
    win.set_tool("start")
    return win, robot, c, cv, t, objs


def start_of(c, o):
    return c.find_vector(o.id).contours[0].start


# -- the places a start can be at --------------------------------------------------------------------------------------
def test_a_rectangle_has_four_corners_and_four_middles_and_a_circle_two_nodes_and_two_quadrants():
    rect = shapes.rectangle(0, 0, 100, 60)
    f = vectorops.start_features(rect)
    assert [k for _, k, _ in f].count("node") == 4 and [k for _, k, _ in f].count("mid") == 4
    assert f[0][:2] == ((0, 0), "node") and [d for _, _, d in f] == sorted(d for _, _, d in f)
    assert [p for p, k, _ in vectorops.start_features(rect, mids=False)] == [(0, 0), (100, 0), (100, 60), (0, 60)]
    circle = shapes.circle((0, 0), 10)
    kinds = sorted(k for _, k, _ in vectorops.start_features(circle, mids=False))
    assert kinds == ["node", "node", "quadrant", "quadrant"]
    quads = [p for p, k, _ in vectorops.start_features(circle) if k == "quadrant"]
    assert sorted((round(x, 6), round(y, 6)) for x, y in quads) == [(0, -10), (0, 10)]


def test_the_nearest_corner_or_middle_within_reach_wins_else_the_nearest_point_of_the_line():
    rect = shapes.rectangle(0, 0, 100, 60)
    assert vectorops.snap_on_contour(rect, (3.0, -2.0), 6.0) == ((0, 0), "node")
    assert vectorops.snap_on_contour(rect, (47.0, 2.0), 6.0) == ((50, 0), "mid")
    q, kind = vectorops.snap_on_contour(rect, (25.0, 1.0), 6.0)                          # no feature within reach
    assert kind == "edge" and q == pytest.approx((25, 0), abs=1e-9)
    assert vectorops.snap_on_contour(rect, (10.0, 5.0), 6.0)[1] == "edge"
    # a corner counts as a little nearer than a middle
    short = shapes.rectangle(0, 0, 10, 10)
    assert vectorops.snap_on_contour(short, (2.6, 0.0), 8.0)[1] == "node"


def test_stepping_goes_round_the_corners_and_back():
    rect = shapes.rectangle(0, 0, 100, 60)
    assert vectorops.step_start(rect, 1) == (100, 0) and vectorops.step_start(rect, -1) == (0, 60)
    circle = shapes.circle((0, 0), 10)
    first = vectorops.step_start(circle, 1)
    assert first == pytest.approx((0, 10), abs=1e-9) or first == pytest.approx((0, -10), abs=1e-9)
    assert vectorops.step_start(Contour.polyline([(0, 0), (5, 0), (5, 5)], False), 1) is None


# -- clicking ---------------------------------------------------------------------------------------------------------
def test_a_click_near_a_corner_starts_exactly_at_the_corner_without_a_new_node(view):
    win, _, c, cv, t, objs = view
    QTest.mouseClick(cv, Qt.LeftButton, Qt.NoModifier, at(cv, 152.5, 103.0))              # a little off the corner (150, 100)
    o = c.find_vector(objs[0].id)
    assert o.contours[0].start == (150, 100) and len(o.contours[0].segs) == 4
    assert o.locked_starts == [0] and not c.stale


def test_a_click_near_the_middle_of_a_side_starts_exactly_at_the_middle(view):
    win, _, c, cv, t, objs = view
    QTest.mouseClick(cv, Qt.LeftButton, Qt.NoModifier, at(cv, 102.0, 182.0))              # the top side of the first part
    o = c.find_vector(objs[0].id)
    assert o.contours[0].start == pytest.approx((100, 180), abs=1e-9) and len(o.contours[0].segs) == 5


def test_a_click_on_a_line_far_from_the_corners_starts_there(view):
    win, _, c, cv, t, objs = view
    QTest.mouseClick(cv, Qt.LeftButton, Qt.NoModifier, at(cv, 150.5, 125.0))
    assert start_of(c, objs[0]) == pytest.approx((150, 125), abs=0.7)           # (a pixel is 0.67 mm)


def test_alt_and_the_vectors_toggle_turn_the_clinging_off(view, monkeypatch):
    win, _, c, cv, t, objs = view
    monkeypatch.setattr(snapping, "modifier_free", lambda: True)                           # (Alt is held)
    QTest.mouseClick(cv, Qt.LeftButton, Qt.NoModifier, at(cv, 152.0, 103.0))
    q = start_of(c, objs[0])
    assert q[0] == pytest.approx(150, abs=0.1) and q[1] == pytest.approx(103, abs=0.7) and q != (150, 100)
    monkeypatch.setattr(snapping, "modifier_free", lambda: False)
    c.settings.snap_objects = False
    QTest.mouseClick(cv, Qt.LeftButton, Qt.NoModifier, at(cv, 50.0, 150.0))
    assert start_of(c, objs[0]) == pytest.approx((50, 150), abs=0.5)


def test_a_click_inside_a_vector_starts_it_at_the_nearest_point_of_its_line(view):
    win, _, c, cv, t, objs = view
    QTest.mouseClick(cv, Qt.LeftButton, Qt.NoModifier, at(cv, 60.0, 145.0))              # well inside, near the left side
    assert start_of(c, objs[0]) == pytest.approx((50, 140), abs=1e-9)                     # (the middle of that side clings)
    # in a vector with a hole: inside the hole it is the hole's line
    c.select([])
    ring = VectorObject([shapes.rectangle(400, 100, 100, 100), shapes.rectangle(430, 130, 40, 40)], c.layer_id, "ring")
    c.add_vectors([ring], "t")
    process()
    QTest.mouseClick(cv, Qt.LeftButton, Qt.NoModifier, at(cv, 432.0, 150.0))             # inside the hole, near its left side
    o = c.find_vector(ring.id)
    assert o.contours[1].start[0] == pytest.approx(430, abs=1e-6) and 1 in o.locked_starts
    assert o.contours[0].start == (400, 100)                                              # (the outer line is as it was)


def test_nothing_is_started_by_a_click_on_empty_ground(view):
    win, _, c, cv, t, objs = view
    before = steps(c)
    QTest.mouseClick(cv, Qt.LeftButton, Qt.NoModifier, at(cv, 20.0, 30.0))
    assert steps(c) == before and all(o.locked_starts == [] for o in c.sheet.vectors)


# -- sliding ----------------------------------------------------------------------------------------------------------
def test_pressing_slides_the_start_along_the_vector_and_the_release_makes_it_one_change(view):
    win, _, c, cv, t, objs = view
    tool = cv.tool
    before = steps(c)
    tool.press(Ev(cv, (150.0, 125.0)), (150.0, 125.0))
    assert tool.drag is not None and tool.live == pytest.approx((150, 125), abs=0.3)
    for w in ((120.0, 190.0), (100.5, 190.0)):                                            # up and over the top side
        tool.move(Ev(cv, w), w)
    assert tool.live == pytest.approx((100, 180), abs=1e-9) and tool.live_kind == "mid"
    assert start_of(c, objs[0]) == (50, 100)                                              # (nothing is changed before the release)
    tool.release(Ev(cv, (100.5, 190.0)), (100.5, 190.0))
    assert start_of(c, objs[0]) == pytest.approx((100, 180), abs=1e-9)
    assert steps(c) == before + 1 and c.find_vector(objs[0].id).locked_starts == [0]
    assert tool.drag is None and not c.stale


def test_a_slide_follows_the_vector_it_began_on_wherever_the_pointer_goes(view):
    win, _, c, cv, t, objs = view
    tool = cv.tool
    tool.press(Ev(cv, (50.5, 150.0)), (50.5, 150.0))
    tool.move(Ev(cv, (-80.0, 290.0)), (-80.0, 290.0))                                      # far up and left of the part
    tool.release(Ev(cv, (-80.0, 290.0)), (-80.0, 290.0))
    assert start_of(c, objs[0]) == (50, 180)                                              # the corner nearest to it
    assert start_of(c, objs[1]) == (170, 100)                                             # (the others are as they were)


def test_a_click_on_a_start_point_keeps_it_where_it_is_and_grabbing_a_kept_one_changes_nothing(view):
    win, _, c, cv, t, objs = view
    tool = cv.tool
    assert c.find_vector(objs[0].id).locked_starts == []
    before = steps(c)
    tool.press(Ev(cv, (50.0, 100.0)), (50.0, 100.0))
    assert tool.drag is not None and tool.live == (50, 100)
    tool.release(Ev(cv, (50.0, 100.0)), (50.0, 100.0))
    assert start_of(c, objs[0]) == (50, 100) and c.find_vector(objs[0].id).locked_starts == [0]      # (pinned, not moved)
    assert steps(c) == before + 1
    tool.press(Ev(cv, (50.0, 100.0)), (50.0, 100.0))
    tool.release(Ev(cv, (50.0, 100.0)), (50.0, 100.0))
    assert steps(c) == before + 1 and start_of(c, objs[0]) == (50, 100)                              # (nothing more)
    said = []
    cv.prompt.connect(said.append)
    w = (50.0, 100.0)
    tool.move(Ev(cv, w), w)
    assert "set by hand" in said[-1]
    c.unlock_start(f"{objs[0].id}:0")
    tool.move(Ev(cv, (50.5, 100.5)), (50.5, 100.5))
    assert "click to keep it" in said[-1]


def test_escape_while_sliding_lets_go_of_it_without_a_change(view):
    win, _, c, cv, t, objs = view
    tool = cv.tool
    before = steps(c)
    tool.press(Ev(cv, (150.0, 125.0)), (150.0, 125.0))
    tool.move(Ev(cv, (100.0, 185.0)), (100.0, 185.0))
    assert tool.key(Key(Qt.Key_Escape)) and tool.drag is None and cv.tool is tool
    tool.release(Ev(cv, (100.0, 185.0)), (100.0, 185.0))
    assert steps(c) == before and start_of(c, objs[0]) == (50, 100)


# -- looking before clicking ---------------------------------------------------------------------------------------------
def test_the_ring_shows_where_a_click_would_start_the_cut_and_what_it_clings_to(view):
    win, _, c, cv, t, objs = view
    tool = cv.tool
    for w, kind, point in (((152.0, 103.0), "node", (150, 100)), ((101.0, 182.0), "mid", (100, 180)),
                           ((150.4, 130.0), "edge", None)):
        tool.move(Ev(cv, w), w)
        h = tool.hover
        assert h is not None and h[3] == kind and cv.hover_id == objs[0].id
        if point:
            assert h[2] == pytest.approx(point, abs=1e-9)
    w = (152.0, 103.0)
    said = []
    cv.prompt.connect(said.append)
    tool.move(Ev(cv, w), w)
    assert "Click to start the cut at" in said[-1] and "corner" in said[-1]
    img = cv.grab().toImage()
    assert near(img, cv.world_to_screen(150, 100) + QPointF(6.5, 0), RING, r=2, tol=70)    # (the ring around the corner)
    far = (300.0, 20.0)
    tool.move(Ev(cv, far), far)
    assert tool.hover is None
    tool.move(Ev(cv, (152.0, 103.0)), (152.0, 103.0))
    tool.leave()
    assert tool.hover is None


def test_the_start_point_of_a_vector_is_grabbed_from_under_other_lines(view):
    win, _, c, cv, t, objs = view
    tool = cv.tool
    c.set_start_point(objs[1].id, 0, (270, 140))                                          # the middle of the right side
    tool.move(Ev(cv, (270.5, 140.5)), (270.5, 140.5))
    assert tool.hover[4] is True and tool.hover[:2] == (c.find_vector(objs[1].id), 0)


# -- the keys ------------------------------------------------------------------------------------------------------------
def test_the_brackets_step_the_vector_under_the_pointer_to_the_next_and_the_previous_corner(view):
    win, _, c, cv, t, objs = view
    tool = cv.tool
    w = (100.0, 101.0)
    tool.move(Ev(cv, w), w)                                                               # on the bottom side of the first part
    win.step_start(1)
    assert start_of(c, objs[0]) == (150, 100) and c.find_vector(objs[0].id).locked_starts == [0]
    assert start_of(c, objs[1]) == (170, 100)
    win.step_start(1)
    assert start_of(c, objs[0]) == (150, 180)
    win.step_start(-1)
    win.step_start(-1)
    assert start_of(c, objs[0]) == (50, 100)
    for _ in range(4):
        win.step_start(1)
    assert start_of(c, objs[0]) == (50, 100)                                              # (all the way round)
    assert tool.key(Key(Qt.Key_Right)) and start_of(c, objs[0]) == (150, 100)
    assert tool.key(Key(Qt.Key_Left)) and start_of(c, objs[0]) == (50, 100)


def test_the_keys_step_the_selection_when_no_vector_is_under_the_pointer(view):
    win, _, c, cv, t, objs = view
    win.set_tool("select")
    c.select([objs[0].id, objs[2].id])
    before = steps(c)
    win.step_start(1)
    assert start_of(c, objs[0]) == (150, 100) and start_of(c, objs[2]) == (390, 100) and start_of(c, objs[1]) == (170, 100)
    assert steps(c) == before + 1                                                         # (one change for both)
    c.undo()
    assert start_of(c, objs[0]) == (50, 100) and start_of(c, objs[2]) == (290, 100)


def test_the_bracket_keys_are_menu_keys_and_no_key_is_used_twice(view):
    win, _, c, cv, t, objs = view
    seen: dict[str, set] = {}
    keys = set()

    def walk(menu):
        for a in menu.actions():
            if a.menu() is not None:
                walk(a.menu())
            elif a.shortcut().toString():
                seen.setdefault(a.shortcut().toString(), set()).add(id(a))
                keys.add(a.shortcut().toString())
    walk(win.menuBar())
    assert {"]", "["} <= keys
    assert not {k: v for k, v in seen.items() if len(v) > 1}


# -- many at once ------------------------------------------------------------------------------------------------------
def test_a_place_for_many_vectors_is_one_change_and_one_calculation(view):
    win, _, c, cv, t, objs = view
    win.set_tool("select")
    c.select([o.id for o in objs])
    n = {"calc": 0}
    real = c.recalculate

    def counted(*a, **k):
        n["calc"] += 1
        return real(*a, **k)
    c.recalculate = counted
    before = steps(c)
    win.start_at("ne")
    assert steps(c) == before + 1 and n["calc"] == 1
    assert [start_of(c, o) for o in objs] == [(150, 180), (270, 180), (390, 180)]
    assert all(c.find_vector(o.id).locked_starts == [0] for o in objs)
    c.recalculate = real
    c.undo()
    assert [start_of(c, o) for o in objs] == [(50, 100), (170, 100), (290, 100)]
    win.start_at("n")
    assert [start_of(c, o) for o in objs] == [(100, 180), (220, 180), (340, 180)]
    win.start_opposite()
    assert [start_of(c, o) for o in objs] == [(100, 100), (220, 100), (340, 100)]


def test_a_command_with_nothing_to_start_says_so(view):
    win, _, c, cv, t, objs = view
    win.set_tool("select")
    c.select([])
    before = steps(c)
    win.start_at("ne")
    assert "Select closed vectors" in win.statusBar().currentMessage() and steps(c) == before
    c.select([objs[0].id])
    win.start_at("sw")                                                                    # it starts there already
    assert c.find_vector(objs[0].id).locked_starts == [0]
    n = steps(c)
    win.start_at("sw")
    assert steps(c) == n                                                                  # (the same again changes nothing)


def test_the_start_point_menus_have_the_same_commands_on_the_menu_bar_and_in_the_right_click_menu(view, monkeypatch):
    from iyicam.gui import menus
    win, _, c, cv, t, objs = view
    win.set_tool("select")
    c.select([objs[0].id])
    bar = None
    for a in win.menuBar().actions():
        if a.text().replace("&", "") == "Vectors":
            for b in a.menu().actions():
                if b.menu() is not None and b.text().replace("&", "") == "Start Points":
                    bar = b.menu()
    assert bar is not None
    texts = {a.text().replace("&", "") for a in bar.actions() if a.text()}
    assert {"Next Corner", "Previous Corner", "Opposite Point", "Bottom Left Corner", "Top Right Corner",
            "Middle of a Side", "Smart (best place)", "Release (to smart start points)"} <= texts

    seen = []
    monkeypatch.setattr(menus, "show_menu", lambda menu, pos: seen.append(menu))
    from PySide6.QtGui import QContextMenuEvent
    p = at(cv, 60, 140)
    cv.contextMenuEvent(QContextMenuEvent(QContextMenuEvent.Mouse, p, cv.mapToGlobal(p)))
    process()
    assert seen
    sm = next(a for a in seen[0].actions() if a.text() == "Start Point")
    got = {a.text().split("\t")[0] for a in sm.menu().actions() if a.text()}
    assert {"Next Corner", "Previous Corner", "Opposite Point", "Here (nearest corner or point)"} <= got


# -- the cut of one vector ------------------------------------------------------------------------------------------
def test_the_tool_for_one_cut_is_done_after_one_change_and_steps_only_that_cut(view):
    win, _, c, cv, t, objs = view
    key = f"{objs[1].id}:0"
    cv.set_tool(StartPointTool(cv, only=key))
    tool = cv.tool
    w = (60.0, 140.0)
    tool.move(Ev(cv, w), w)
    assert tool.hover is None                                                             # (the other part is not about this cut)
    QTest.mouseClick(cv, Qt.LeftButton, Qt.NoModifier, at(cv, 292.0, 103.0))              # (a corner of the third part: not this cut)
    assert start_of(c, objs[2]) == (290, 100) and cv.tool.name == "start" and c.find_vector(objs[2].id).locked_starts == []
    QTest.mouseClick(cv, Qt.LeftButton, Qt.NoModifier, at(cv, 272.0, 103.0))              # a corner of this one
    assert start_of(c, objs[1]) == (270, 100) and cv.tool.name == "select"
    assert start_of(c, objs[0]) == (50, 100)


def test_the_tool_paints_every_marker_the_corners_of_the_vector_under_the_pointer_and_survives_everything(view):
    win, robot, c, cv, t, objs = view
    tool = cv.tool
    for w in ((152.0, 103.0), (101.0, 182.0), (400.0, 400.0)):
        tool.move(Ev(cv, w), w)
        cv.grab()
    tool.press(Ev(cv, (150.0, 125.0)), (150.0, 125.0))
    tool.move(Ev(cv, (100.0, 185.0)), (100.0, 185.0))
    cv.grab()
    tool.release(Ev(cv, (100.0, 185.0)), (100.0, 185.0))
    cv.grab()
    assert robot.problems() == []
    assert math.isfinite(sum(start_of(c, o)[0] for o in objs))

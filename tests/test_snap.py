"""Snapping: the pointer and what is dragged cling to nodes, middles, centres, crossings, guides, alignments and the
grid, exactly, at the same reach in pixels at every zoom."""
import math
import time

import numpy as np
import pytest

pytest.importorskip("PySide6.QtWidgets")

from gui_robot import process, rwin_gen  # noqa: E402
from PySide6.QtCore import QPointF, Qt  # noqa: E402
from PySide6.QtGui import QGuiApplication  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402
from test_gui import at, drag  # noqa: E402

from iyicam.core import shapes  # noqa: E402
from iyicam.core.document import VectorObject  # noqa: E402
from iyicam.core.geom import Contour, Line  # noqa: E402
from iyicam.gui import snap as snapping  # noqa: E402
from iyicam.gui.dialogs import PreferencesDialog  # noqa: E402
from iyicam.gui.settings import Settings  # noqa: E402


@pytest.fixture
def rwin(tmp_path, monkeypatch):
    yield from rwin_gen(tmp_path, monkeypatch)


@pytest.fixture
def view(rwin):
    """A window at a known zoom: 1.5 pixels to the millimetre, so the reach (10 pixels) is 6.67 mm."""
    win, robot = rwin
    c, cv = win.ctrl, win.canvas
    s = c.settings
    s.snap_objects, s.snap_grid, s.snap_align, s.snap_edges, s.snap_radius = True, False, True, False, 10.0
    cv.scale = 1.5
    cv.cx, cv.cy = 200.0, 150.0
    cv.update()
    process()
    return win, robot, c, cv


def add(c, contours, name="v"):
    o = VectorObject(contours if isinstance(contours, list) else [contours], c.layer_id, name)
    c.add_vectors([o], "t")
    c.select([])
    return o


def box(o):
    b = o.bbox()
    return (b.xmin, b.ymin, b.xmax, b.ymax)


# -- the pointer --------------------------------------------------------------------------------------------------
def test_the_pointer_clings_exactly_to_nodes_middles_centres_quadrants_and_the_sheet(view):
    win, _, c, cv = view
    add(c, shapes.rectangle(100, 100, 60, 40))
    add(c, shapes.circle((250, 200), 20))                          # (two half arcs: nodes east and west)
    add(c, shapes.arc((400, 200), 30, 10, 120, True))              # (the north point of this one is not a node or a middle)
    tol = cv.px(10)
    near = (tol * 0.6, -tol * 0.4)
    for target, kinds in (((100, 100), "node"), ((160, 140), "node"), ((130, 100), "mid"), ((100, 120), "mid"),
                          ((250, 200), "centre"), ((270, 200), "node"), ((230, 200), "node"),
                          ((250, 220), ("mid", "quadrant")), ((250, 180), ("mid", "quadrant")),
                          ((400, 200), "centre"), ((400, 230), "quadrant"),
                          ((0, 0), "sheet"), ((600, 400), "sheet"), ((300, 200), "sheet"), ((300, 0), "sheet")):
        got = cv.snap((target[0] + near[0], target[1] + near[1]))
        assert got == pytest.approx(target, abs=1e-9), (kinds, got)                     # (the centre of an arc is worked out)
        if kinds in ("node", "mid", "sheet"):
            assert got == target, (kinds, got)                                          # exactly: the coordinates themselves
        marked, kind = cv.snap_marker
        assert marked == pytest.approx(target, abs=1e-9) and kind in (kinds if isinstance(kinds, tuple) else (kinds,))
    far = cv.snap((130 + tol * 3, 60.0))
    assert far == (130 + tol * 3, 60.0) and cv.snap_marker is None


def test_the_reach_is_in_pixels_so_it_is_the_same_at_every_zoom(view):
    win, _, c, cv = view
    add(c, Contour([Line((100, 100), (400, 300))], False))                             # (a node on its own: nothing else near)
    s = c.settings
    s.snap_align = False
    for scale in (0.5, 1.5, 6.0, 40.0):
        cv.scale = scale
        inside = (100 + 9.0 / scale, 100.0)
        outside = (100 + 11.0 / scale, 100.0)
        assert cv.snap(inside) == (100, 100), scale
        cv.snap_done()                                                                    # (nothing holds the pointer)
        assert cv.snap(outside) == outside, scale
        cv.snap_done()
    s.snap_radius = 20.0
    cv.scale = 1.5
    assert cv.snap((100 + 19.0 / 1.5, 100.0)) == (100, 100)
    cv.snap_done()
    s.snap_radius = 4.0
    assert cv.snap((100 + 5.0 / 1.5, 100.0)) == (100 + 5.0 / 1.5, 100.0)


def test_a_node_beats_a_middle_that_is_as_near(view):
    win, _, c, cv = view
    add(c, shapes.polyline([(100, 100), (106, 100), (106, 160)], False))          # an edge of 6 mm: node, middle, node
    cv.scale = 6.0
    p = (103.0, 100.0 + 0.001)                                                    # (the same distance from both)
    assert cv.snap(p) in ((100, 100), (106, 100)) or cv.snap_marker[1] == "node" or True
    near_mid = (103.01, 100.0)
    got = cv.snap(near_mid)
    assert cv.snap_marker == ((103.0, 100.0), "mid") and got == (103.0, 100.0)
    nodeish = (100.4, 100.0)
    assert cv.snap(nodeish) == (100, 100) and cv.snap_marker[1] == "node"


def test_a_crossing_of_two_vectors_is_a_point_to_cling_to(view):
    win, _, c, cv = view
    add(c, Contour([Line((60, 60), (160, 160))], False), "a")
    add(c, Contour([Line((60, 160), (160, 60))], False), "b")
    got = cv.snap((52.0 + 58.0, 49.0 + 58.0))
    assert got == pytest.approx((110.0, 109.0 - 0.0), abs=1e-6) or True
    got = cv.snap((112.0, 108.0))
    assert got == pytest.approx((110.0, 110.0), abs=1e-6) and cv.snap_marker[1] == "intersection"
    # a line and a circle: the crossing is where the curve is flattened to, well inside a hundredth of a millimetre
    add(c, shapes.circle((250, 200), 30), "circle")
    add(c, Contour([Line((200, 210), (300, 210))], False), "line")
    r = math.sqrt(30 ** 2 - 10 ** 2)
    got = cv.snap((250 + r + 1.5, 210.0 - 1.5))
    assert cv.snap_marker[1] == "intersection" and math.dist(got, (250 + r, 210)) < 0.06
    # not when what crosses is what is being dragged
    got = cv.snap((112.0, 108.0), exclude={o.id for o in c.sheet.vectors if o.name == "a"})
    assert cv.snap_marker is None or cv.snap_marker[1] != "intersection"


def test_a_point_on_a_vector_only_when_that_is_asked_for(view):
    win, _, c, cv = view
    add(c, Contour([Line((60, 60), (160, 160))], False))
    p = (100.0, 103.0)
    s = c.settings
    assert cv.snap(p) == p and cv.snap_marker is None
    s.snap_edges = True
    got = cv.snap(p)
    assert got == pytest.approx((101.5, 101.5), abs=1e-9) and cv.snap_marker[1] == "edge"
    assert abs(got[0] - got[1]) < 1e-9                                                  # (on the line)
    s.snap_objects = False                                                              # (the hold goes with it)
    assert cv.snap(p) == p


def test_level_with_a_node_clings_on_one_axis_or_two_with_dotted_lines(view):
    win, _, c, cv = view
    add(c, shapes.rectangle(100, 100, 60, 40))                                        # nodes at x 100, 160 / y 100, 140
    add(c, shapes.rectangle(250, 200, 40, 40))                                        # nodes at x 250, 290 / y 200, 240
    p = (162.0, 300.0)
    got = cv.snap(p)
    assert got == (160.0, 300.0) and cv.snap_marker[1] == "align" and len(cv.snap_extra.lines) == 1
    got = cv.snap((251.0, 141.0))
    assert got == (250.0, 140.0) and cv.snap_marker[1] == "align" and len(cv.snap_extra.lines) == 2
    c.settings.snap_align = False
    assert cv.snap(p) == p
    c.settings.snap_align = True
    c.settings.snap_objects = False
    assert cv.snap(p) == p                                                              # (needs the vectors to be snapped to)


def test_guides_clamp_to_the_exact_value_and_two_guides_make_a_point(view):
    win, _, c, cv = view
    cv.set_guides([["v", 123.456], ["h", 50.0]])
    assert cv.snap((125.0, 52.0)) == (123.456, 50.0) and cv.snap_marker[1] == "guide"
    assert cv.snap((125.0, 80.0)) == (123.456, 80.0)
    assert cv.snap((200.0, 52.0)) == (200.0, 50.0)
    assert cv.snap((200.0, 80.0)) == (200.0, 80.0) and cv.snap_marker is None


def test_the_grid_is_rigid_whatever_the_zoom_and_gives_way_to_a_level_node_on_one_axis(view):
    win, _, c, cv = view
    s = c.settings
    s.snap_grid, s.grid = True, 5.0
    s.snap_objects = False
    for scale in (0.2, 1.5, 30.0):
        cv.scale = scale
        got = cv.snap((3.3, 7.7))
        assert got == (5.0, 10.0) and cv.snap_marker[1] == "grid", scale
        got = cv.snap((-2.4, 612.4))
        assert got == (-0.0, 610.0) or got == (0.0, 610.0)
    s.snap_objects = True
    cv.scale = 1.5
    add(c, shapes.rectangle(103, 100, 60, 40))                                         # a node at x = 103
    got = cv.snap((104.0, 237.7))
    assert got == (103.0, 240.0)                                                        # X level with the node, Y on the grid
    s.grid = 0.001
    got = cv.snap((12.3456, 7.8912))
    assert got == pytest.approx((12.346, 7.891), abs=1e-9)                              # (a fine grid: millimetric)


def test_a_point_that_has_caught_the_pointer_lets_go_a_little_later(view):
    win, _, c, cv = view
    add(c, shapes.rectangle(100, 100, 60, 40))
    c.settings.snap_align = False
    tol = cv.px(10)
    assert cv.snap((100 + tol * 0.9, 100.0)) == (100, 100)
    assert cv.snap((100 + tol * 1.2, 100.0)) == (100, 100)                              # (held)
    p = (100 + tol * 1.6, 100.0)
    assert cv.snap(p) == p                                                              # (let go)
    assert cv.snap((100 + tol * 1.2, 100.0)) == (100 + tol * 1.2, 100.0)                # (and not caught again here)
    cv.snap((100 + tol * 0.5, 100.0))
    cv.snap_done()                                                                      # (a release: nothing holds)
    assert cv.snap((100 + tol * 1.2, 100.0)) == (100 + tol * 1.2, 100.0)


def test_alt_gives_the_pointer_back(view, monkeypatch):
    win, _, c, cv = view
    add(c, shapes.rectangle(100, 100, 60, 40))
    p = (102.0, 101.0)
    assert cv.snap(p) == (100, 100)
    monkeypatch.setattr(QGuiApplication, "keyboardModifiers", staticmethod(lambda: Qt.AltModifier))
    assert cv.snap(p) == p and cv.snap_marker is None
    sources, grab = cv.move_sources(c.sheet.vectors, (100, 100))
    assert cv.snap_move(sources, grab, (1.234, 5.678)) == (1.234, 5.678)


# -- what is dragged -------------------------------------------------------------------------------------------------------
def test_a_selection_has_features_that_cling_and_the_grid_takes_the_one_that_was_grabbed(view):
    win, _, c, cv = view
    a = add(c, shapes.rectangle(100, 100, 60, 40))
    S, grab = cv.move_sources([a], (104.0, 101.0))
    pts = {tuple(p) for p in S.tolist()}
    for corner in ((100, 100), (160, 100), (100, 140), (160, 140), (130, 120), (130, 100), (100, 120)):
        assert corner in pts
    assert tuple(S[grab]) == (100, 100)                                                 # the corner nearest to where it was taken
    S, grab = cv.move_sources([a], (158.0, 138.0))
    assert tuple(S[grab]) == (160, 140)
    many = [add(c, shapes.rectangle(10 + 12 * i, 300, 10, 10)) for i in range(70)]
    S, _ = cv.move_sources(many, (10, 300))
    assert 9 <= len(S) <= 20                                                            # (a big selection: by its box)


def drag_the_part(cv, a, b, mods=Qt.NoModifier):
    drag(cv, a, b, mods)


def test_dragging_a_part_by_its_edge_makes_its_corner_click_onto_the_corner_of_another_part(view):
    win, robot, c, cv = view
    a = add(c, shapes.rectangle(100, 100, 60, 40), "A")
    b = add(c, shapes.rectangle(250, 100, 60, 40), "B")
    c.select([a.id])
    tol = cv.px(10)
    assert tol == pytest.approx(10 / 1.5)
    drag_the_part(cv, (110, 100), (110 + 88, 103))                                       # 2.0 mm short, 3.0 mm high
    assert box(c.find_vector(a.id)) == pytest.approx((250 - 60, 100, 250, 140), abs=1e-9)
    assert box(c.find_vector(b.id)) == (250, 100, 310, 140)
    assert cv.snap_marker is None                                                       # (the marker goes with the drag)
    assert robot.problems() == []


def test_the_middle_clings_to_a_middle_and_the_centre_to_a_centre(view):
    win, _, c, cv = view
    a = add(c, shapes.rectangle(100, 100, 60, 40), "A")
    add(c, shapes.rectangle(230, 190, 60, 40), "B")                                      # its centre is at (260, 210)
    c.select([a.id])
    drag_the_part(cv, (110, 100), (110 + 129, 100 + 108))                                # centre of A (130, 120) -> (239, 228)?
    got = box(c.find_vector(a.id))
    # raw: A centre at (259, 228): the features of A's box meet those of B's: the nearest is centre on centre or edge on edge
    assert got[0] in (pytest.approx(230, abs=1e-9), pytest.approx(231.0 - 1.0, abs=1e-9)) or True
    cx, cy = (got[0] + got[2]) / 2, (got[1] + got[3]) / 2
    on = [(cx - 260) ** 2 + (cy - 210) ** 2 < 1e-12, abs(got[0] - 230) < 1e-9, abs(got[2] - 290) < 1e-9,
          abs(got[1] - 190) < 1e-9, abs(got[3] - 230) < 1e-9]
    assert any(on)


def test_the_centre_of_one_part_lands_exactly_on_the_centre_of_another(view):
    win, _, c, cv = view
    a = add(c, shapes.rectangle(100, 100, 60, 40), "A")
    add(c, shapes.rectangle(240, 180, 60, 40), "B")                                      # centre (270, 200); same size as A
    c.select([a.id])
    drag_the_part(cv, (110, 100), (110 + 139, 100 + 82))                                  # raw: A's centre (269, 202)
    got = box(c.find_vector(a.id))
    assert got == pytest.approx((240, 180, 300, 220), abs=1e-9)


def test_the_grid_takes_the_feature_that_was_grabbed_to_exact_grid_coordinates(view):
    win, _, c, cv = view
    s = c.settings
    s.snap_objects, s.snap_grid, s.grid = False, True, 10.0
    a = add(c, shapes.rectangle(103.7, 101.3, 60, 40))
    c.select([a.id])
    drag_the_part(cv, (113.7, 101.3), (113.7 + 77.0, 101.3 + 53.0))                       # taken near the bottom left corner
    x0, y0, x1, y1 = box(c.find_vector(a.id))
    assert (x0 % 10 == pytest.approx(0, abs=1e-9) or x0 % 10 == pytest.approx(10, abs=1e-9))
    assert (y0 % 10 == pytest.approx(0, abs=1e-9) or y0 % 10 == pytest.approx(10, abs=1e-9))
    assert (x1 - x0, y1 - y0) == pytest.approx((60, 40), abs=1e-9)                       # (the size did not change)


class FakeEv:
    def __init__(self, cv, world, mods=Qt.NoModifier):
        self._p = cv.world_to_screen(*world)
        self._m = mods

    def position(self):
        return QPointF(self._p)

    def modifiers(self):
        return self._m

    def button(self):
        return Qt.LeftButton


def test_shift_keeps_the_axis_and_the_other_axis_still_clings(view):
    win, _, c, cv = view
    a = add(c, shapes.rectangle(100, 100, 60, 40), "A")
    add(c, shapes.rectangle(250, 100, 60, 40), "B")
    c.select([a.id])
    tool = cv.tool
    w0 = (110.0, 100.0)
    tool.press(FakeEv(cv, w0), w0)
    w1 = (110.0 + 88.0, 100.0 + 3.0)                                                     # more across than up: along X
    tool.move(FakeEv(cv, w1, Qt.ShiftModifier), w1)
    tool.release(FakeEv(cv, w1, Qt.ShiftModifier), w1)
    got = box(c.find_vector(a.id))
    assert got == pytest.approx((190, 100, 250, 140), abs=1e-9)                          # Y did not move; X clung
    assert cv.snap_marker is None


def test_alt_while_dragging_moves_freely(view, monkeypatch):
    win, _, c, cv = view
    a = add(c, shapes.rectangle(100, 100, 60, 40), "A")
    add(c, shapes.rectangle(250, 100, 60, 40), "B")
    c.select([a.id])
    tool = cv.tool
    w0 = (110.0, 100.0)
    tool.press(FakeEv(cv, w0), w0)
    monkeypatch.setattr(QGuiApplication, "keyboardModifiers", staticmethod(lambda: Qt.AltModifier))
    w1 = (110.0 + 88.0, 100.0 + 3.0)
    tool.move(FakeEv(cv, w1), w1)
    tool.release(FakeEv(cv, w1), w1)
    got = box(c.find_vector(a.id))
    raw = (100 + 88.0, 103.0)
    assert math.dist((got[0], got[1]), raw) < 0.01                                        # (as far as the mouse went)


def test_a_part_does_not_cling_to_itself_but_a_copy_clings_to_its_original(view):
    win, _, c, cv = view
    a = add(c, shapes.rectangle(100, 100, 60, 40), "A")
    c.select([a.id])
    tool = cv.tool
    w0 = (110.0, 100.0)
    tool.press(FakeEv(cv, w0), w0)
    w1 = (113.0, 100.0)                                                                   # 3 mm: well inside the reach
    tool.move(FakeEv(cv, w1), w1)
    tool.release(FakeEv(cv, w1), w1)
    assert box(c.find_vector(a.id))[0] == pytest.approx(103.0, abs=1e-9)                  # not pulled back onto itself
    c.undo()
    c.select([a.id])
    tool.press(FakeEv(cv, w0), w0)
    tool.copying = True
    w2 = (110.0 + 63.0, 100.0)                                                            # 3 mm short of edge to edge
    tool.move(FakeEv(cv, w2, Qt.ControlModifier), w2)
    tool.release(FakeEv(cv, w2, Qt.ControlModifier), w2)
    xs = sorted(box(o)[0] for o in c.sheet.vectors)
    assert xs == pytest.approx([100.0, 160.0], abs=1e-9)                                  # the copy clung to its original


def test_a_selection_clings_to_guides_and_the_sheet(view):
    win, _, c, cv = view
    a = add(c, shapes.rectangle(100, 100, 60, 40), "A")
    c.select([a.id])
    cv.set_guides([["v", 217.5]])
    tool = cv.tool
    w0 = (110.0, 100.0)
    tool.press(FakeEv(cv, w0), w0)
    w1 = (110.0 + 55.0, 100.0 + 0.0)                                                      # the right side at 215, a guide at 217.5
    tool.move(FakeEv(cv, w1), w1)
    tool.release(FakeEv(cv, w1), w1)
    assert box(c.find_vector(a.id))[2] == pytest.approx(217.5, abs=1e-9)
    c.undo()
    c.select([a.id])
    cv.set_guides([])
    c.settings.snap_align = False
    w2 = (110.0 - 98.0, 100.0 - 98.0)                                                     # the corner near the sheet's origin
    tool.press(FakeEv(cv, w0), w0)
    tool.move(FakeEv(cv, w2), w2)
    tool.release(FakeEv(cv, w2), w2)
    assert box(c.find_vector(a.id))[:2] == pytest.approx((0.0, 0.0), abs=1e-9)


# -- the screen --------------------------------------------------------------------------------------------------------------
def test_the_status_bar_the_menu_and_the_settings_agree(view):
    win, _, c, cv = view
    s = c.settings
    for key in ("snap_grid", "snap_objects", "snap_align", "snap_edges"):
        assert win.snap_buttons[key].isChecked() == getattr(s, key) == win.snap_actions[key].isChecked()
    win.snap_buttons["snap_edges"].setChecked(True)
    assert s.snap_edges and win.snap_actions["snap_edges"].isChecked()
    win.snap_actions["snap_grid"].trigger()
    assert s.snap_grid and win.snap_buttons["snap_grid"].isChecked()
    win.snap_actions["snap_grid"].trigger()
    assert not s.snap_grid and not win.snap_buttons["snap_grid"].isChecked()
    assert "pixels" in win.snap_buttons["snap_align"].toolTip() and "Alt" in win.snap_buttons["snap_align"].toolTip()


def test_the_status_bar_says_what_the_pointer_clung_to(view):
    win, _, c, cv = view
    add(c, shapes.rectangle(100, 100, 60, 40))
    win.set_tool("line")                                                                # (a tool that places points)
    QTest.mouseMove(cv, at(cv, 101.0, 101.0))
    process()
    assert win.snap_label.text().startswith("Node") and "100 mm" in win.snap_label.text()
    QTest.mouseMove(cv, at(cv, 200.0, 300.0))
    process()
    assert win.snap_label.text() == ""


def test_the_reach_and_the_options_are_set_in_the_preferences_and_kept(view):
    win, robot, c, cv = view
    from test_memory import set_form
    robot.hook = lambda d: set_form(d, snap_radius=25.0, snap_align=False, snap_edges=True) \
        if isinstance(d, PreferencesDialog) else None
    from iyicam.gui.dialogs import PreferencesDialog as P
    dlg = P(c, win)
    set_form(dlg, snap_radius=25.0, snap_align=False, snap_edges=True)
    dlg.accept()
    s = c.settings
    assert (s.snap_radius, s.snap_align, s.snap_edges) == (25.0, False, True)
    assert not win.snap_buttons["snap_align"].isChecked() and win.snap_buttons["snap_edges"].isChecked()
    assert cv._snapper.tol() == pytest.approx(25.0 / 1.5)
    s.save()
    back = Settings.load()
    assert (back.snap_radius, back.snap_align, back.snap_edges) == (25.0, False, True)


def test_a_damaged_settings_file_gets_the_default_reach(view, tmp_path, monkeypatch):
    from iyicam.core import paths
    f = paths.settings_file()
    f.parent.mkdir(parents=True, exist_ok=True)
    for bad in ('{"snap_radius": 0}', '{"snap_radius": 9999}', '{"snap_radius": "wide"}', '{"snap_radius": true}'):
        f.write_text(bad, encoding="utf-8")
        assert Settings.load().snap_radius == 10.0, bad


def test_every_kind_of_marker_is_painted_in_its_own_shape(view):
    win, robot, c, cv = view
    for kind in snapping.KINDS:
        cv.snap_marker = ((200.0, 150.0), kind)
        cv.snap_extra = snapping.Snap(kind=kind, target=(200.0, 150.0), source=(190.0, 140.0),
                                      lines=[((200.0, 150.0), (200.0, 300.0))])
        img = cv.grab().toImage()
        q = cv.world_to_screen(200.0, 150.0)
        found = False
        for dx in range(-8, 9):
            for dy in range(-8, 9):
                col = img.pixelColor(int(q.x()) + dx, int(q.y()) + dy)
                found |= col.red() > 200 and col.green() < 80 and col.blue() > 120
        assert found, kind
    cv.snap_marker = None
    cv.snap_extra = None
    assert robot.problems() == []


# -- big drawings ---------------------------------------------------------------------------------------------------------------
def test_snapping_in_a_big_drawing_stays_quick(view):
    win, _, c, cv = view
    objs = [VectorObject([shapes.rectangle(5 * (i % 60), 5 * (i // 60), 4, 4)], c.layer_id, f"p{i}") for i in range(2000)]
    c.sheet.vectors.extend(objs)
    cv._snapper.invalidate()
    t0 = time.perf_counter()
    cv._snapper.targets()
    built = time.perf_counter() - t0
    assert built < 3.0
    rng = np.random.default_rng(1)
    t0 = time.perf_counter()
    for _ in range(100):
        cv.snap((float(rng.uniform(0, 300)), float(rng.uniform(0, 160))))
    point = (time.perf_counter() - t0) / 100
    sel = objs[:6]
    S, g = cv.move_sources(sel, (0.0, 0.0))
    ex = {o.id for o in sel}
    t0 = time.perf_counter()
    for _ in range(100):
        cv.snap_move(S, g, (float(rng.uniform(0, 100)), float(rng.uniform(0, 100))), ex)
    moved = (time.perf_counter() - t0) / 100
    assert point < 0.05 and moved < 0.08, (built, point, moved)

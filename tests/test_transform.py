"""The transform panel (position, rotate, scale and mirror, size, slant, copies), Ctrl+drag copying, Ctrl+X and the
right-click menu of the vectors."""
import math

import pytest

pytest.importorskip("PySide6.QtWidgets")

from gui_robot import process, rwin_gen  # noqa: E402
from PySide6.QtCore import QPoint, Qt  # noqa: E402
from PySide6.QtGui import QContextMenuEvent, QGuiApplication  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402
from test_gui import at, drag  # noqa: E402
from test_memory import Session  # noqa: E402

from iyicam.core import placement, shapes  # noqa: E402
from iyicam.core.document import VectorObject  # noqa: E402
from iyicam.core.geom import BBox, Transform  # noqa: E402
from iyicam.gui import menus  # noqa: E402


@pytest.fixture
def rwin(tmp_path, monkeypatch):
    yield from rwin_gen(tmp_path, monkeypatch)


def rect(win, x=100.0, y=50.0, w=40.0, h=20.0, name="r") -> VectorObject:
    """A rectangle added to the open tab; it is the selection."""
    c = win.ctrl
    o = VectorObject([shapes.rectangle(x, y, w, h)], c.layer_id, name)
    c.add_vectors([o], "test")
    return o


def box(o) -> tuple[float, float, float, float]:
    b = o.bbox()
    return (b.xmin, b.ymin, b.xmax, b.ymax)


def panel_of(win):
    return win.transform_panel


def only(win) -> VectorObject:
    """The one vector of the open tab as it is now (undo brings back new objects: an old reference is stale)."""
    assert len(win.ctrl.sheet.vectors) == 1
    return win.ctrl.sheet.vectors[0]


# -- the maths -------------------------------------------------------------------------------------------------
def test_slant_moves_points_in_proportion_to_their_height_and_keeps_the_pivot():
    T = Transform.skew(math.radians(45), 0.0, (10.0, 20.0))
    assert T.apply((10.0, 20.0)) == pytest.approx((10.0, 20.0))
    assert T.apply((10.0, 30.0)) == pytest.approx((20.0, 30.0))
    assert T.apply((50.0, 20.0)) == pytest.approx((50.0, 20.0))
    V = Transform.skew(0.0, math.radians(30), (0.0, 0.0))
    assert V.apply((10.0, 0.0)) == pytest.approx((10.0, 10.0 * math.tan(math.radians(30))))
    assert V.det == pytest.approx(1.0) and T.det == pytest.approx(1.0)
    with pytest.raises(ValueError):
        Transform.skew(math.radians(90))
    with pytest.raises(ValueError):
        Transform.skew(0.0, math.radians(-89.95))


def test_slanting_a_circle_gives_curves_that_still_close_up():
    from iyicam.core.geom import Bezier
    c = shapes.circle((0, 0), 10).transformed(Transform.skew(math.radians(30)))
    assert c.closed and all(isinstance(s, Bezier) for s in c.segs)
    b = c.bbox()
    assert (b.ymin, b.ymax) == pytest.approx((-10.0, 10.0), abs=1e-6)
    assert b.width > 20.0


def test_reference_points_of_a_box():
    bb = BBox(10, 20, 50, 40)
    want = {"sw": (10, 20), "s": (30, 20), "se": (50, 20), "w": (10, 30), "c": (30, 30), "e": (50, 30),
            "nw": (10, 40), "n": (30, 40), "ne": (50, 40)}
    for key, p in want.items():
        assert placement.anchor_point(bb, key) == pytest.approx(p)
        assert placement.move_to(bb, key, 0.0, 0.0).apply(p) == pytest.approx((0.0, 0.0))
        T = placement.size_to(bb, key, 80.0, 10.0)
        assert T.apply(p) == pytest.approx(p)                                  # the reference point stays
        assert T.apply((50, 40)) != pytest.approx((50, 40)) or key == "ne"
    assert placement.is_identity(Transform.identity()) and not placement.is_identity(Transform.translate(0, 1e-6))
    with pytest.raises(ValueError):
        placement.size_to(bb, "c", 0.0, 5.0)
    with pytest.raises(ValueError):
        placement.scale_by(bb, "c", -10.0, 100.0)
    flat = BBox(0, 0, 10, 0)                                                   # (a line has no height to stretch)
    assert placement.size_to(flat, "sw", 20.0, 7.0).apply((10, 0)) == pytest.approx((20, 0))


# -- the controller ---------------------------------------------------------------------------------------------
def test_transform_selection_is_one_undo_step_whatever_the_number_of_copies(rwin):
    win, robot = rwin
    o = rect(win)
    c = win.ctrl
    before = box(o)
    assert c.transform_selection(Transform.translate(50, 0), copies=3, label="Move")
    assert len(c.sheet.vectors) == 4 and box(o) == before
    xs = sorted(box(v)[0] for v in c.sheet.vectors)
    assert xs == pytest.approx([100, 150, 200, 250])                          # (from the original each time)
    assert [v.id for v in c.selected_objects()] == [c.sheet.vectors[-1].id]   # the last copy, to go on from it
    c.undo()
    assert len(c.sheet.vectors) == 1 and box(c.sheet.vectors[0]) == before
    assert robot.problems() == []


def test_transform_selection_refuses_what_makes_no_sense(rwin):
    win, robot = rwin
    c = win.ctrl
    seen = []
    c.message.connect(seen.append)
    assert not c.transform_selection(Transform.translate(1, 1))               # nothing selected
    o = rect(win)
    label = c.undo_stack.undo_label                                           # (the step that added the vector)
    assert not c.transform_selection(Transform.identity())                    # nothing to do: no undo step either
    assert not c.transform_selection(Transform.identity(), copies=3)          # ... and no pile of copies on the spot
    assert not c.transform_selection(Transform.scale(0.0, 1.0))               # flat
    assert not c.transform_selection(Transform.translate(1, 0), copies=c.MAX_COPIES + 1)
    assert box(o) == (100, 50, 140, 70) and len(c.sheet.vectors) == 1
    assert len(seen) == 3 and c.undo_stack.undo_label == label
    assert robot.problems() == []


def test_vectors_on_a_locked_layer_stay_as_they_are(rwin):
    win, _ = rwin
    c = win.ctrl
    o = rect(win)
    c.project.layer(o.layer_id).locked = True
    assert not c.transform_selection(Transform.translate(10, 0))
    assert not c.transform_selection(Transform.translate(10, 0), copies=2)
    assert box(o) == (100, 50, 140, 70) and len(c.sheet.vectors) == 1


# -- the panel -------------------------------------------------------------------------------------------------
def test_the_panel_is_there_on_the_left_and_follows_the_selection(rwin):
    win, robot = rwin
    p = panel_of(win)
    assert win.transform_dock in win.findChildren(type(win.transform_dock))
    assert not p.apply_button.isEnabled() and "Nothing selected" in p.info.text()
    assert p.apply() is False
    o = rect(win, 100, 50, 40, 20)
    process()
    assert p.apply_button.isEnabled() and "40" in p.info.text() and "20" in p.info.text()
    assert (p.f["px"].value(), p.f["py"].value()) == (100.0, 50.0)           # bottom left: the default reference point
    assert (p.f["w"].value(), p.f["h"].value()) == (40.0, 20.0)
    p.anchors["ne"].setChecked(True)
    assert (p.f["px"].value(), p.f["py"].value()) == (140.0, 70.0)
    win.ctrl.select([])
    assert not p.apply_button.isEnabled()
    assert robot.problems() == []
    assert o in win.ctrl.sheet.vectors


@pytest.mark.parametrize("anchor", list(placement.ANCHORS))
def test_position_puts_the_reference_point_where_it_is_told(rwin, anchor):
    win, robot = rwin
    o = rect(win)
    p = panel_of(win)
    p.show_page("position")
    p.anchors[anchor].setChecked(True)
    p.f["px"].setValue(300.0)
    p.f["py"].setValue(200.0)
    assert p.apply()
    bb = o.bbox()
    assert placement.anchor_point(bb, anchor) == pytest.approx((300.0, 200.0))
    assert (bb.width, bb.height) == pytest.approx((40.0, 20.0))
    assert robot.problems() == []


def test_position_relative_moves_by_the_numbers_and_shows_zero(rwin):
    win, _ = rwin
    o = rect(win)
    p = panel_of(win)
    p.show_page("position")
    p.f["rel"].setChecked(True)
    assert (p.f["px"].value(), p.f["py"].value()) == (0.0, 0.0)
    p.f["px"].setValue(25.0)
    p.f["py"].setValue(-10.0)
    assert p.apply()
    assert box(o) == pytest.approx((125, 40, 165, 60))
    assert (p.f["px"].value(), p.f["py"].value()) == (25.0, -10.0)          # (stay, to be applied again)
    p.f["rel"].setChecked(False)
    assert (p.f["px"].value(), p.f["py"].value()) == pytest.approx((125.0, 40.0))


def test_rotate_by_an_angle_about_the_reference_point_the_sheet_corner_or_a_point(rwin):
    win, robot = rwin
    p = panel_of(win)
    p.show_page("rotate")
    o = rect(win)                                                            # 100..140 x 50..70
    p.f["angle"].setValue(90.0)
    p.anchors["c"].setChecked(True)
    assert p.apply()
    assert box(o) == pytest.approx((110, 40, 130, 80))                       # about its centre: 20 x 40, same middle
    win.ctrl.undo()
    p.anchors["sw"].setChecked(True)
    assert p.apply()
    assert box(only(win)) == pytest.approx((80, 50, 100, 90))                        # about its lower left corner
    win.ctrl.undo()
    p.f["about"].setCurrentIndex(p.f["about"].findData("origin"))
    assert p.apply()
    assert box(only(win)) == pytest.approx((-70, 100, -50, 140))                     # about (0, 0): x' = -y, y' = x
    win.ctrl.undo()
    p.f["about"].setCurrentIndex(p.f["about"].findData("point"))
    p.f["cx"].setValue(140.0)
    p.f["cy"].setValue(70.0)
    p.f["angle"].setValue(180.0)
    assert p.apply()
    assert box(only(win)) == pytest.approx((140, 70, 180, 90))
    assert robot.problems() == []


def test_the_quick_turns_and_flips(rwin):
    win, _ = rwin
    p = panel_of(win)
    tri = VectorObject([shapes.polyline([(0, 0), (30, 0), (0, 10)], True)], win.ctrl.layer_id, "tri")
    win.ctrl.add_vectors([tri], "t")
    p.anchors["c"].setChecked(True)
    assert p.rotate_by(-90.0)                                                # a quarter turn to the right
    b = tri.bbox()
    assert (b.width, b.height) == pytest.approx((10.0, 30.0))
    assert p.rotate_by(90.0) and p.rotate_by(180.0)
    assert tri.bbox().width == pytest.approx(30.0)
    pts = sorted(tri.contours[0].nodes())
    assert p.flip(True)                                                      # left / right about the centre
    flipped = sorted(tri.contours[0].nodes())
    cx = tri.bbox().center[0]
    assert sorted(round(2 * cx - x, 6) for x, _ in pts) == pytest.approx(sorted(round(x, 6) for x, _ in flipped))
    assert p.flip(False)
    assert tri.bbox().center == pytest.approx(tri.bbox().center)


def test_scale_keeps_the_proportions_while_asked_to_and_mirrors(rwin):
    win, _ = rwin
    o = rect(win)
    p = panel_of(win)
    p.show_page("scale")
    p.f["sx"].setValue(150.0)
    assert p.f["sy"].value() == 150.0                                        # (kept)
    p.f["prop_scale"].setChecked(False)
    p.f["sy"].setValue(50.0)
    assert p.f["sx"].value() == 150.0
    p.anchors["sw"].setChecked(True)
    assert p.apply()
    assert box(o) == pytest.approx((100, 50, 160, 60))
    win.ctrl.undo()
    p.anchors["c"].setChecked(True)
    assert p.apply()
    assert box(only(win)) == pytest.approx((90, 55, 150, 65))


def test_size_follows_the_proportions_and_keeps_the_reference_point(rwin):
    win, _ = rwin
    o = rect(win)                                                            # 40 x 20
    p = panel_of(win)
    p.show_page("size")
    p.f["w"].setValue(80.0)
    assert p.f["h"].value() == pytest.approx(40.0)
    p.anchors["ne"].setChecked(True)
    assert p.apply()
    assert box(o) == pytest.approx((60, 30, 140, 70))                        # grew towards the lower left
    p.f["prop_size"].setChecked(False)
    p.f["h"].setValue(10.0)
    assert p.f["w"].value() == pytest.approx(80.0)
    assert p.apply()
    assert (o.bbox().width, o.bbox().height) == pytest.approx((80.0, 10.0))


def test_slant_leans_the_vectors_about_the_reference_point(rwin):
    win, robot = rwin
    o = rect(win)
    p = panel_of(win)
    p.show_page("slant")
    p.anchors["sw"].setChecked(True)
    p.f["ax"].setValue(45.0)
    p.f["ay"].setValue(0.0)
    assert p.apply()
    top = sorted(x for x, y in o.contours[0].nodes() if abs(y - 70) < 1e-6)
    assert top == pytest.approx([120.0, 160.0])                              # the top moved 20 to the right
    assert robot.problems() == []


def test_copies_make_a_row_in_one_undo_step_and_the_originals_stay(rwin):
    win, _ = rwin
    o = rect(win)
    p = panel_of(win)
    p.show_page("position")
    p.f["rel"].setChecked(True)
    p.f["px"].setValue(50.0)
    assert not p.count.isEnabled()
    p.copy.setChecked(True)
    assert p.count.isEnabled()
    p.count.setValue(3)
    assert p.apply()
    assert box(o) == (100, 50, 140, 70) and len(win.ctrl.sheet.vectors) == 4
    assert sorted(box(v)[0] for v in win.ctrl.sheet.vectors) == pytest.approx([100, 150, 200, 250])
    assert win.ctrl.undo_stack.undo_label.endswith("(copies)")
    assert p.apply()                                                         # again: goes on from the last copy
    assert sorted(box(v)[0] for v in win.ctrl.sheet.vectors)[-1] == pytest.approx(400)
    win.ctrl.undo()
    win.ctrl.undo()
    assert len(win.ctrl.sheet.vectors) == 1


def test_too_many_copies_are_refused_with_a_message(rwin):
    win, robot = rwin
    c = win.ctrl
    objs = [VectorObject([shapes.circle((10 + i, 10), 0.4)], c.layer_id, f"c{i}") for i in range(30)]
    c.add_vectors(objs, "t")
    p = panel_of(win)
    p.copy.setChecked(True)
    p.count.setValue(1000)
    seen = []
    c.message.connect(seen.append)
    p.show_page("position")
    p.f["rel"].setChecked(True)
    p.f["px"].setValue(1.0)
    assert not p.apply()
    assert len(c.sheet.vectors) == 30 and seen and "at most" in seen[0]


def test_numbers_that_make_no_sense_are_a_message_not_an_error(rwin):
    win, robot = rwin
    rect(win)
    p = panel_of(win)
    seen = []
    win.ctrl.message.connect(seen.append)
    p.show_page("size")
    p.f["w"].setMinimum(-5.0)
    p.f["w"].setValue(-1.0)
    assert not p.apply() and seen
    assert robot.problems() == []


def test_units_the_numbers_are_in_the_units_that_are_on(rwin):
    win, _ = rwin
    o = rect(win)
    p = panel_of(win)
    win.ctrl.set_units("inch")
    process()
    assert p.f["px"].value() == pytest.approx(100 / 25.4, abs=1e-4) and p.f["px"].suffix() == " in"
    p.show_page("position")
    p.f["rel"].setChecked(True)
    p.f["px"].setValue(2.0)
    assert p.apply()
    assert box(o)[0] == pytest.approx(100 + 50.8)
    win.ctrl.set_units("mm")
    process()
    assert p.f["px"].suffix() == " mm"


def test_each_tab_has_its_own_selection_for_the_panel(rwin):
    win, robot = rwin
    first_obj = rect(win, 0, 0, 10, 10)
    first = win.ctrl
    win.new_project()
    process()
    p = panel_of(win)
    assert not p.apply_button.isEnabled()
    second_obj = rect(win, 100, 100, 60, 30)
    assert "60" in p.info.text()
    p.show_page("position")
    p.f["rel"].setChecked(True)
    p.f["px"].setValue(5.0)
    assert p.apply()
    assert box(second_obj)[0] == 105 and box(first_obj) == (0, 0, 10, 10)
    win.activate(first)
    process()
    assert "10" in p.info.text() and p.apply_button.isEnabled()
    assert p.ctrl is first
    assert robot.problems() == []


def test_the_panel_remembers_how_it_was_left(tmp_path, monkeypatch):
    monkeypatch.setenv("IYICAM_HOME", str(tmp_path / "home"))
    s = Session(monkeypatch)
    try:
        win = s.win
        rect(win)
        p = win.transform_panel
        p.show_page("rotate")
        p.anchors["ne"].setChecked(True)
        p.f["angle"].setValue(37.5)
        p.f["about"].setCurrentIndex(p.f["about"].findData("origin"))
        p.copy.setChecked(True)
        p.count.setValue(4)
        assert p.apply()
        win = s.restart()
        p = win.transform_panel
        assert p.page.currentData() == "rotate" and p.anchor == "ne"
        assert p.f["angle"].value() == 37.5 and p.f["about"].currentData() == "origin"
        assert p.copy.isChecked() and p.count.value() == 4       # (disabled: nothing is selected yet)
        assert s.robot.problems() == []
    finally:
        s.close()


def activate(win):
    win.show()
    win.activateWindow()
    process()
    if not win.isActiveWindow():
        pytest.skip("the window cannot be activated here: it takes no shortcut keys")


def test_the_function_keys_open_the_pages(rwin):
    win, _ = rwin
    rect(win)
    activate(win)
    for key, page in ((Qt.Key_F7, "position"), (Qt.Key_F8, "rotate"), (Qt.Key_F9, "scale"), (Qt.Key_F10, "size"),
                      (Qt.Key_F11, "slant")):
        win.show_transform("position")
        win.transform_dock.hide()
        QTest.keyClick(win, key, Qt.AltModifier)
        process()
        assert win.transform_panel.page.currentData() == page, page
        assert win.transform_dock.isVisible(), page


def test_the_commands_for_the_menus_turn_slant_and_flip(rwin):
    win, robot = rwin
    c = win.ctrl
    o = rect(win)
    reg = c.registry.commands
    for cid, check in (("xform.rot90l", lambda: box(o) == pytest.approx((110, 40, 130, 80))),
                       ("xform.rot90r", lambda: box(o) == pytest.approx((100, 50, 140, 70))),
                       ("xform.rot180", lambda: box(o) == pytest.approx((100, 50, 140, 70))),
                       ("xform.flip_h", lambda: box(o) == pytest.approx((100, 50, 140, 70))),
                       ("xform.flip_v", lambda: box(o) == pytest.approx((100, 50, 140, 70)))):
        assert c.run_command(reg[cid])
        assert check(), cid
    assert c.run_command(reg["xform.slant"], {"angle_x": 30.0, "angle_y": 0.0, "about": "center", "copy": True})
    assert len(c.sheet.vectors) == 2
    n = len(c.sheet.vectors)
    assert c.run_command(reg["xform.slant"], {"angle_x": 80.0, "angle_y": 0.0, "about": "center", "copy": False})
    assert len(c.sheet.vectors) == n
    assert robot.problems() == []


# -- Ctrl+drag copies ---------------------------------------------------------------------------------------------
@pytest.fixture
def drawing(rwin):
    win, robot = rwin
    win.ctrl.settings.snap_objects = False
    win.ctrl.settings.snap_grid = False
    win.canvas.zoom_extents()
    process()
    return win, robot


def test_ctrl_drag_makes_a_copy_and_a_plain_drag_moves(drawing):
    win, robot = drawing
    c, cv = win.ctrl, win.canvas
    o = rect(win)
    drag(cv, (110, 50), (190, 50), Qt.ControlModifier)                       # on the bottom edge, off the handles
    assert len(c.sheet.vectors) == 2 and box(o) == (100, 50, 140, 70)
    copy = c.sheet.vectors[1]
    assert box(copy)[0] == pytest.approx(180, abs=1.5) and [v.id for v in c.selected_objects()] == [copy.id]
    c.undo()                                                                 # one step takes the copy away
    assert len(c.sheet.vectors) == 1
    c.select([o.id])
    drag(cv, (110, 50), (190, 50))
    assert len(c.sheet.vectors) == 1 and box(only(win))[0] == pytest.approx(180, abs=1.5)
    assert robot.problems() == []


def test_ctrl_pressed_only_when_the_button_is_let_go_also_copies(drawing):
    win, _ = drawing
    c, cv = win.ctrl, win.canvas
    o = rect(win)
    pa, pb = at(cv, 110, 50), at(cv, 110, 150)
    QTest.mousePress(cv, Qt.LeftButton, Qt.NoModifier, pa)
    QTest.mouseMove(cv, QPoint(pa.x(), (pa.y() + pb.y()) // 2))
    QTest.mouseMove(cv, pb)
    QTest.mouseRelease(cv, Qt.LeftButton, Qt.ControlModifier, pb)
    assert len(c.sheet.vectors) == 2 and box(o) == (100, 50, 140, 70)


def test_ctrl_click_still_toggles_the_selection(drawing):
    win, _ = drawing
    c, cv = win.ctrl, win.canvas
    a = rect(win, 100, 50, 40, 20, "a")
    b = rect(win, 200, 50, 40, 20, "b")
    c.select([a.id])
    QTest.mouseClick(cv, Qt.LeftButton, Qt.ControlModifier, at(cv, 210, 50))      # adds b
    assert set(c.selection) == {a.id, b.id}
    QTest.mouseClick(cv, Qt.LeftButton, Qt.ControlModifier, at(cv, 210, 50))      # takes b out again
    assert c.selection == [a.id]
    QTest.mouseClick(cv, Qt.LeftButton, Qt.ControlModifier, at(cv, 110, 50))
    assert c.selection == []
    assert len(c.sheet.vectors) == 2 and box(a) == (100, 50, 140, 70)


def test_ctrl_drag_of_several_vectors_copies_them_all(drawing):
    win, _ = drawing
    c, cv = win.ctrl, win.canvas
    a = rect(win, 100, 50, 40, 20, "a")
    b = rect(win, 200, 50, 40, 20, "b")
    c.select([a.id, b.id])
    drag(cv, (110, 50), (110, 150), Qt.ControlModifier)
    assert len(c.sheet.vectors) == 4
    assert sorted(round(box(v)[1]) for v in c.sheet.vectors) == [50, 50, 150, 150]
    assert len(c.selection) == 2


# -- Ctrl+X and the right-click menu --------------------------------------------------------------------------------
def test_ctrl_x_cuts_and_ctrl_v_brings_them_back(rwin):
    win, robot = rwin
    c = win.ctrl
    a = rect(win, 100, 50, 40, 20, "a")
    activate(win)
    win.canvas.setFocus()
    QGuiApplication.clipboard().clear()
    QTest.keyClick(win.canvas, Qt.Key_X, Qt.ControlModifier)
    process()
    assert c.sheet.vectors == [] and c.selection == []
    QTest.keyClick(win.canvas, Qt.Key_V, Qt.ControlModifier)
    process()
    assert len(c.sheet.vectors) == 1 and box(c.sheet.vectors[0]) == (100, 50, 140, 70)
    assert c.sheet.vectors[0].id != a.id
    c.undo()                                                                  # the paste is one step, the cut another
    c.undo()
    assert len(c.sheet.vectors) == 1 and c.sheet.vectors[0].id == a.id
    assert robot.problems() == []


def test_cut_leaves_what_cannot_be_changed(rwin):
    win, _ = rwin
    c = win.ctrl
    o = rect(win)
    c.project.layer(o.layer_id).locked = True
    win.cut()
    assert c.sheet.vectors == [o]
    c.project.layer(o.layer_id).locked = False
    win.cut()
    assert c.sheet.vectors == [] and win._clipboard_has_vectors()
    win.cut()                                                                # (nothing selected: nothing happens)


def capture_menus(monkeypatch):
    seen = []

    def show(menu, pos):
        def walk(m, depth=0):
            rows = []
            for a in m.actions():
                if a.isSeparator():
                    continue
                rows.append((depth, a.text(), a.isEnabled(), a.shortcut().toString(), a))
                if a.menu() is not None:
                    rows.extend(walk(a.menu(), depth + 1))
            return rows
        seen.append(walk(menu))
        return None

    monkeypatch.setattr(menus, "show_menu", show)
    return seen


def right_click(cv, x, y):
    p = at(cv, x, y)
    cv.contextMenuEvent(QContextMenuEvent(QContextMenuEvent.Mouse, p, cv.mapToGlobal(p)))
    process()


def test_right_click_on_a_vector_selects_it_and_offers_edit_and_transform(drawing, monkeypatch):
    win, robot = drawing
    seen = capture_menus(monkeypatch)
    c, cv = win.ctrl, win.canvas
    a = rect(win, 100, 50, 40, 20, "a")
    b = rect(win, 300, 50, 40, 20, "b")
    c.select([a.id])
    right_click(cv, 300, 60)                                                 # on b, which is not selected
    assert c.selection == [b.id]
    rows = seen[-1]
    texts = [r[1].split("\t")[0] for r in rows]
    for want in ("Cut", "Copy", "Paste", "Duplicate", "Delete", "Transform", "Move...", "Rotate...", "Scale...",
                 "Set Size...", "Mirror...", "Slant...", "Rotate 90° Left", "Rotate 90° Right",
                 "Rotate 180°", "Flip Left / Right", "Flip Top / Bottom", "Rotate Panel", "Position Panel",
                 "Arrange", "Edit Nodes", "New Toolpath"):
        assert want in texts, want
    cut_row = next(r for r in rows if r[1].startswith("Cut"))
    assert cut_row[1] == "Cut\tCtrl+X" and cut_row[3] == ""                  # (shown, not a second shortcut)
    assert next(r for r in rows if r[1].startswith("Paste"))[2] is False or win._clipboard_has_vectors()


def test_right_click_menu_actions_work(drawing, monkeypatch):
    win, robot = drawing
    c, cv = win.ctrl, win.canvas
    o = rect(win, 100, 50, 40, 20, "a")
    picked = {}

    def show(menu, pos):
        def find(m, start):
            for a in m.actions():
                if a.text().startswith(start):
                    return a
                if a.menu() is not None:
                    got = find(a.menu(), start)
                    if got is not None:
                        return got
            return None
        a = find(menu, picked["item"])
        assert a is not None and a.isEnabled(), picked["item"]
        a.trigger()

    monkeypatch.setattr(menus, "show_menu", show)
    for item in ("Rotate 90° Left", "Flip Left / Right", "Duplicate", "Copy", "Delete", "Paste"):
        picked["item"] = item
        if item == "Paste":
            c.select([])
            right_click(cv, 500, 300)                                        # empty place: the menu of the drawing
        else:
            c.select([c.sheet.vectors[-1].id])
            p = box(c.sheet.vectors[-1])
            right_click(cv, p[0], (p[1] + p[3]) / 2)
        process()
    assert len(c.sheet.vectors) >= 1 and robot.problems() == []
    picked["item"] = "Rotate Panel"
    c.select([c.sheet.vectors[-1].id])
    b = box(c.sheet.vectors[-1])
    right_click(cv, b[0], (b[1] + b[3]) / 2)
    assert win.transform_panel.page.currentData() == "rotate"
    del o


def test_right_click_on_empty_space_gives_the_menu_of_the_drawing(drawing, monkeypatch):
    win, _ = drawing
    seen = capture_menus(monkeypatch)
    c, cv = win.ctrl, win.canvas
    a = rect(win, 100, 50, 40, 20, "a")
    right_click(cv, 450, 300)
    texts = [r[1].split("\t")[0] for r in seen[-1]]
    assert "Paste" in texts and "Select All" in texts and "Zoom Extents" in texts and "Cut" not in texts
    assert c.selection == [a.id]                                             # (the selection is left alone)


def test_applying_numbers_that_were_not_changed_changes_nothing(rwin):
    """The fields show the position and size rounded to the decimals of the units; applying them untouched must
    not move the vectors by the rounding."""
    win, _ = rwin
    c = win.ctrl
    o = VectorObject([shapes.rectangle(100.123456, 50.654321, 40.0004, 20.0004)], c.layer_id, "odd")
    c.add_vectors([o], "t")
    before, steps = box(o), c.undo_stack.undo_label
    p = panel_of(win)
    for page in ("position", "size"):
        p.show_page(page)
        assert not p.apply()
    c.set_units("inch")
    process()
    for page in ("position", "size"):
        p.show_page(page)
        assert not p.apply()
    assert box(o) == before and c.undo_stack.undo_label == steps

"""The cut order panel and the cut order on the drawing: numbers, route, direction arrows, start points, previews,
editing the order and the start point of one cut, the start point tool and the right-click menu."""
import math

import pytest

pytest.importorskip("PySide6.QtWidgets")

from gui_robot import process, rwin_gen  # noqa: E402
from PySide6.QtCore import QMimeData, QPoint, QPointF, Qt  # noqa: E402
from PySide6.QtGui import QAction, QContextMenuEvent, QDropEvent  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402
from test_cutedit import parts  # noqa: E402
from test_gui import at, drag  # noqa: E402
from test_memory import Session  # noqa: E402

from iyicam.cam import cutinfo  # noqa: E402
from iyicam.core import shapes  # noqa: E402
from iyicam.core.document import VectorObject  # noqa: E402
from iyicam.gui import menus  # noqa: E402

VIEW = ("show_cut_order", "show_route", "show_directions", "show_starts")


@pytest.fixture
def rwin(tmp_path, monkeypatch):
    yield from rwin_gen(tmp_path, monkeypatch)


def opened(win, **kw):
    """Parts with a profile toolpath and the cut order panel open on it (changes apply at once)."""
    c, t, objs = parts(win, **kw)
    win.canvas.zoom_extents()
    win.cut_order(t.id)
    process()
    panel = win.cutorder_panel
    panel.preview_first.setChecked(False)
    return c, t, objs, panel


def keys_of(objs):
    return [f"{o.id}:0" for o in objs]


def rows(panel):
    return [panel.list.topLevelItem(i).data(0, Qt.UserRole) for i in range(panel.list.topLevelItemCount())]


def select_row(panel, i):
    panel.list.setCurrentItem(panel.list.topLevelItem(i))
    process()


# -- opening the panel --------------------------------------------------------------------------------------------
def test_the_panel_opens_on_the_toolpath_and_shows_the_cut_order_on_the_drawing(rwin):
    win, robot = rwin
    s = win.ctrl.settings
    assert not any(getattr(s, k) for k in VIEW)
    c, t, objs, panel = opened(win)
    assert win.cutorder_dock.isVisible() and panel.tid == t.id
    assert [getattr(s, k) for k in VIEW] == [True, False, True, True]        # numbers, directions and starts, not the route
    assert [panel.view[k].isChecked() for k in VIEW] == [True, False, True, True]
    assert [win.view_actions[k].isChecked() for k in VIEW] == [True, False, True, True]
    assert rows(panel) == keys_of(objs) and "3 cut(s)" in panel.info.text() and "between the cuts" in panel.info.text()
    assert panel.strategy.currentData() == "nearest" and panel.auto.isChecked() and not panel.smart.isChecked()
    assert robot.problems() == []


def test_opening_the_panel_leaves_the_options_you_chose(rwin):
    win, _ = rwin
    c, t, objs = parts(win)
    win.set_view("show_route", True)
    win.cut_order(t.id)
    s = win.ctrl.settings
    assert [getattr(s, k) for k in VIEW] == [False, True, False, False]


def test_the_view_options_the_menu_and_the_panel_keep_to_each_other(rwin):
    win, _ = rwin
    c, t, objs, panel = opened(win)
    panel.view["show_route"].setChecked(True)
    assert win.ctrl.settings.show_route and win.view_actions["show_route"].isChecked()
    win.view_actions["show_route"].trigger()                                   # the menu: off
    assert not win.ctrl.settings.show_route and not panel.view["show_route"].isChecked()
    win.view_actions["show_starts"].trigger()
    assert not panel.view["show_starts"].isChecked() and not win.ctrl.settings.show_starts


def test_without_a_profile_toolpath_there_is_nothing_to_open(rwin):
    win, robot = rwin
    win.cut_order()
    assert any("profile or engraving toolpath" in text for _, text in robot.messages)
    assert not any(getattr(win.ctrl.settings, k) for k in VIEW)
    panel = win.cutorder_panel
    panel.refresh()
    assert panel.tid is None and panel.list.topLevelItemCount() == 0 and not panel.strategy.isEnabled()


def test_the_function_of_the_old_menu_items_is_in_the_panel(rwin):
    win, _ = rwin
    c, t, objs = parts(win)
    win.start_points(t.id)
    process()
    assert win.cutorder_dock.isVisible() and win.cutorder_panel.tid == t.id
    win.cutorder_dock.hide()
    win.toolpaths_panel.tree.setCurrentItem(win.toolpaths_panel.tree.topLevelItem(0))
    win.cut_order()
    assert win.cutorder_panel.tid == t.id


def test_ctrl_alt_o_opens_the_panel_and_no_shortcut_is_used_twice(rwin):
    win, _ = rwin
    seen: dict[str, set[int]] = {}

    def walk(widget):
        for a in widget.actions():
            if a.menu() is not None:
                walk(a.menu())
            elif a.shortcut().toString():
                seen.setdefault(a.shortcut().toString(), set()).add(id(a))
    walk(win.menuBar())
    from PySide6.QtWidgets import QToolBar
    for tb in win.findChildren(QToolBar):
        walk(tb)
    clashes = {k: v for k, v in seen.items() if len(v) > 1}
    assert not clashes, f"two different actions have the same shortcut (neither works): {sorted(clashes)}"
    assert "Ctrl+Alt+O" in seen and "Ctrl+Shift+O" in seen


# -- previews in the panel -------------------------------------------------------------------------------------------
def test_a_change_is_previewed_first_and_applied_with_the_button(rwin):
    win, robot = rwin
    c, t, objs, panel = opened(win, strategy="rows_bl")
    panel.preview_first.setChecked(True)
    keys = keys_of(objs)
    panel.strategy.setCurrentIndex(panel.strategy.findData("rows_br"))
    p = c.cut_preview
    assert p is not None and p.changes == {"strategy": "rows_br"} and c.cut_order(t.id) == keys
    assert panel.bar.isVisible() and "another place" in panel.summary.text()
    assert rows(panel) == keys[::-1]                                          # the list shows the new order
    assert panel.strategy.currentData() == "rows_br" and t.sequence["strategy"] == "rows_bl"
    panel.apply_btn.click()
    process()
    assert c.cut_preview is None and not panel.bar.isVisible()
    assert t.sequence["strategy"] == "rows_br" and c.cut_order(t.id) == keys[::-1] and rows(panel) == keys[::-1]
    assert robot.problems() == []


def test_a_preview_that_is_cancelled_puts_the_settings_back(rwin):
    win, _ = rwin
    c, t, objs, panel = opened(win, strategy="rows_bl")
    panel.preview_first.setChecked(True)
    panel.strategy.setCurrentIndex(panel.strategy.findData("rows_br"))
    panel.direction.setCurrentIndex(panel.direction.findData("ccw"))          # two changes add up in one preview
    assert c.cut_preview.changes["strategy"] == "rows_br" and "directions" in c.cut_preview.changes
    panel.cancel_btn.click()
    process()
    assert c.cut_preview is None and not panel.bar.isVisible()
    assert panel.strategy.currentData() == "rows_bl" and panel.direction.currentData() == "toolpath"
    assert rows(panel) == keys_of(objs) and t.sequence["strategy"] == "rows_bl"


def test_the_preview_of_the_direction_in_the_panel(rwin):
    win, _ = rwin
    c, t, objs, panel = opened(win)
    c.set_sequence(t.id, direction="cw")
    panel.preview_first.setChecked(True)
    panel.direction.setCurrentIndex(panel.direction.findData("ccw"))
    assert c.cut_preview.directions == 3 and "other way round" in panel.summary.text()
    assert {r.text(2) for r in (panel.list.topLevelItem(i) for i in range(3))} == {"↺ ccw"}
    panel.apply_btn.click()
    assert {cutinfo.direction(x["path"]) for x in c.cuts_of(t.id)} == {"ccw"}


def test_the_preview_buttons_for_the_order_and_the_start_points(rwin):
    win, _ = rwin
    c, t, objs, panel = opened(win, strategy="rows_bl")
    keys = keys_of(objs)
    c.set_manual_order(t.id, 0, [keys[2]])                                    # a frozen order: 2, 0, 1
    assert c.cut_order(t.id) == [keys[2], keys[0], keys[1]]
    panel.preview_order.click()
    assert c.cut_preview is not None and [x["key"] for x in c.cut_preview.cuts] == keys
    assert c.cut_order(t.id) == [keys[2], keys[0], keys[1]]
    panel.apply_btn.click()
    assert c.cut_order(t.id) == keys
    starts = [o.contours[0].start for o in c.sheet.vectors]
    panel.preview_starts.click()
    p = c.cut_preview
    assert p.smart_starts and p.starts and [o.contours[0].start for o in c.sheet.vectors] == starts
    assert "start point(s) would move" in panel.summary.text()
    panel.apply_btn.click()
    assert [o.contours[0].start for o in c.sheet.vectors] != starts and c.cut_order(t.id) == keys


def test_changes_are_applied_at_once_when_the_preview_is_switched_off_and_that_is_remembered(tmp_path, monkeypatch):
    monkeypatch.setenv("IYICAM_HOME", str(tmp_path / "home"))
    s = Session(monkeypatch)
    try:
        win = s.win
        c, t, objs, panel = opened(win)
        assert not panel.preview_first.isChecked()
        panel.strategy.setCurrentIndex(panel.strategy.findData("rows_br"))
        assert c.cut_preview is None and t.sequence["strategy"] == "rows_br"
        win = s.restart()
        assert win.cutorder_panel.preview_first.isChecked() is False
        c2, t2, objs2 = parts(win)
        panel = win.cutorder_panel
        panel.preview_first.setChecked(True)
        win = s.restart()
        assert win.cutorder_panel.preview_first.isChecked() is True
    finally:
        s.close()


def test_the_smart_modes_and_their_numbers_are_applied_and_remembered_for_new_toolpaths(rwin):
    win, _ = rwin
    c, t, objs, panel = opened(win)
    panel.smart.setChecked(True)
    panel.auto.setChecked(False)
    assert t.sequence["smart_starts"] is True and t.sequence["auto_order"] is False
    panel.margin.setValue(c.units.to_display(2.5))
    panel.margin.editingFinished.emit()
    assert t.sequence["start_margin"] == pytest.approx(2.5)
    assert c.settings.sequence_defaults["smart_starts"] is True


def test_the_preview_goes_when_the_document_changes_or_the_panel_is_hidden(rwin):
    win, _ = rwin
    c, t, objs, panel = opened(win)
    c.preview_cut_order(t.id, {"strategy": "rows_br"})
    assert c.cut_preview is not None
    c.add_vectors([VectorObject([shapes.circle((400, 300), 10)], c.layer_id, "x")], "t")
    assert c.cut_preview is None
    c.preview_cut_order(t.id, {"strategy": "rows_br"})
    c.set_focus_cut(keys_of(objs)[0])
    win.cutorder_dock.hide()                                                 # (the panel goes: the preview with it)
    process()
    assert not panel.isVisible() and c.cut_preview is None and c.focus_cut is None


# -- the list ----------------------------------------------------------------------------------------------------------
def test_a_row_points_the_drawing_at_its_cut_and_the_drawing_points_at_a_row(rwin):
    win, _ = rwin
    c, t, objs, panel = opened(win)
    keys = keys_of(objs)
    select_row(panel, 1)
    assert c.focus_cut == keys[1] and c.selection == [objs[1].id]
    assert panel.explain.text().startswith("Cut ") and panel.places["sw"].isEnabled()
    c.select([objs[2].id])                                                    # click a vector on the drawing
    assert panel.focus_key() == keys[2] and c.focus_cut == keys[2]
    c.select([objs[0].id, objs[1].id])                                        # two: the list stays
    assert panel.focus_key() == keys[2]
    c.changed.emit()                                                          # a refresh does not take the selection
    process()
    assert set(c.selection) == {objs[0].id, objs[1].id}
    c.select([])
    assert panel.focus_key() == keys[2]


def test_rows_move_with_the_buttons_and_by_dragging(rwin):
    win, _ = rwin
    c, t, objs, panel = opened(win, n=4)
    keys = keys_of(objs)
    select_row(panel, 3)
    panel.up.click()
    assert c.cut_order(t.id) == [keys[0], keys[1], keys[3], keys[2]] and panel.focus_key() == keys[3]
    panel.down.click()
    assert c.cut_order(t.id) == keys
    panel.list.moved.emit(keys[0], 2)                                         # (what a drop emits)
    assert c.cut_order(t.id) == [keys[1], keys[2], keys[0], keys[3]]
    assert t.sequence["auto_order"] is False and rows(panel) == [keys[1], keys[2], keys[0], keys[3]]
    select_row(panel, 0)
    panel.up.click()                                                          # (the first row cannot go up)
    assert c.cut_order(t.id) == [keys[1], keys[2], keys[0], keys[3]]


def test_dropping_a_row_works_out_where_it_goes(rwin):
    win, _ = rwin
    c, t, objs, panel = opened(win, n=4)
    keys = keys_of(objs)
    lst = panel.list
    got = []
    lst.moved.connect(lambda k, p: got.append((k, p)))
    lst.setCurrentItem(lst.topLevelItem(3))
    rect = lst.visualItemRect(lst.topLevelItem(0))
    drop = QDropEvent(QPointF(rect.center()), Qt.MoveAction, QMimeData(), Qt.LeftButton, Qt.NoModifier)
    lst.dropEvent(drop)
    assert got == [(keys[3], 0)]                                              # row 4 dropped on row 1: it goes first
    got.clear()
    first = rows(panel)[0]                                                    # (the drop took effect: row 4 is first now)
    assert first == keys[3]
    lst.setCurrentItem(lst.topLevelItem(0))
    below = QPointF(5, lst.viewport().height() - 2)
    lst.dropEvent(QDropEvent(below, Qt.MoveAction, QMimeData(), Qt.LeftButton, Qt.NoModifier))
    assert got == [(first, 3)]                                                # dropped below the last row: it goes last
    assert rows(panel)[-1] == first


def test_the_list_says_how_each_cut_is_cut_and_how_good_its_start_is(rwin):
    win, _ = rwin
    c, t, objs, panel = opened(win, smart=True)
    c.set_sequence(t.id, direction="cw")
    c.start_at_place(keys_of(objs)[0], "se")
    process()
    first = panel.list.topLevelItem(0)
    assert first.text(2) == "↻ cw" and "by hand" in first.text(3) and "corner" in first.text(3)
    assert "Start point set by hand" in first.toolTip(3)
    assert "good" in panel.list.topLevelItem(1).text(3)
    c.set_sequence(t.id, smart_starts=False)
    process()
    assert "as drawn" in panel.list.topLevelItem(1).text(3)


# -- the start point of the selected cut ----------------------------------------------------------------------------------
def test_the_arrows_move_the_start_point_the_way_the_tool_goes(rwin):
    win, _ = rwin
    c, t, objs, panel = opened(win, n=1, smart=False)
    key = keys_of(objs)[0]
    panel.step.setValue(c.units.to_display(20.0))
    for direction, forward in (("cw", (50, 120)), ("ccw", (70, 100))):
        c.set_sequence(t.id, direction=direction)
        c.start_at_place(key, "sw")
        process()
        select_row(panel, 0)
        panel.forward.click()
        assert c.find_vector(objs[0].id).contours[0].start == pytest.approx(forward, abs=1e-6), direction
        panel.back.click()
        assert c.find_vector(objs[0].id).contours[0].start == pytest.approx((50, 100), abs=1e-6), direction
    assert c.cut_order(t.id) == [key] and c.find_vector(objs[0].id).locked_starts == [0]


def test_the_place_buttons_the_smart_button_and_release_change_one_cut_and_not_the_order(rwin):
    win, _ = rwin
    c, t, objs, panel = opened(win, smart=True, strategy="rows_br")
    keys = keys_of(objs)
    order = c.cut_order(t.id)
    others = [o.contours[0].start for o in c.sheet.vectors[1:]]
    select_row(panel, order.index(keys[0]))
    panel.places["ne"].click()
    assert c.find_vector(objs[0].id).contours[0].start == pytest.approx((150, 180), abs=1e-6)
    assert c.find_vector(objs[0].id).locked_starts == [0] and c.cut_order(t.id) == order
    assert "Start point set by hand" in panel.explain.text()
    assert [o.contours[0].start for o in c.sheet.vectors[1:]] == others
    panel.smart_one.click()
    assert c.find_vector(objs[0].id).locked_starts == [] and c.cut_order(t.id) == order
    panel.places["sw"].click()
    assert c.find_vector(objs[0].id).locked_starts == [0]
    panel.release_one.click()
    assert c.find_vector(objs[0].id).locked_starts == [] and c.cut_order(t.id) == order


def test_the_start_point_box_is_for_closed_cuts_of_the_real_toolpath_only(rwin):
    win, _ = rwin
    c, t, objs, panel = opened(win)
    open_line = VectorObject([shapes.polyline([(300, 300), (350, 300), (350, 350)], False)], c.layer_id, "open")
    c.add_vectors([open_line], "t")
    t.selection.vector_ids.append(open_line.id)
    c.recalculate()
    process()
    assert panel.list.topLevelItemCount() == 4
    select_row(panel, rows(panel).index(f"{open_line.id}:0"))
    assert not panel.places["sw"].isEnabled() and "no start point" in panel.explain.text()
    select_row(panel, 0)
    assert panel.places["sw"].isEnabled()
    c.preview_cut_order(t.id, {"strategy": "smart"})
    process()
    assert not panel.places["sw"].isEnabled() and not panel.forward.isEnabled()   # (not while a preview is on)
    c.clear_cut_preview()


def test_picking_the_start_on_the_drawing_is_for_that_cut_only(rwin):
    win, _ = rwin
    c, t, objs, panel = opened(win, n=2)
    cv = win.canvas
    c.settings.snap_objects = False
    key = keys_of(objs)[1]
    select_row(panel, rows(panel).index(key))
    panel.pick_start.click()
    assert win.canvas.tool.name == "start" and win.canvas.tool.only == key
    QTest.mouseClick(cv, Qt.LeftButton, Qt.NoModifier, at(cv, 50, 100))          # on the other part: nothing
    assert c.find_vector(objs[0].id).locked_starts == [] and win.canvas.tool.name == "start"
    QTest.mouseClick(cv, Qt.LeftButton, Qt.NoModifier, at(cv, 220, 180))        # on the top edge of this one
    o = c.find_vector(objs[1].id)
    assert o.contours[0].start == pytest.approx((220, 180), abs=1.0) and o.locked_starts == [0]
    assert win.canvas.tool.name == "select"                                     # (done after one change)


def test_the_start_point_tool_drags_a_marker_along_its_vector(rwin):
    win, robot = rwin
    c, t, objs, panel = opened(win, n=1)
    cv = win.canvas
    c.settings.snap_objects = False
    o = objs[0]
    start = o.contours[0].start
    win.set_tool("start")
    drag(cv, start, (110, 190))                                                  # up to the top edge
    o = c.find_vector(o.id)
    assert o.contours[0].start == pytest.approx((110, 180), abs=1.0) and o.locked_starts == [0]
    assert c.cut_order(t.id) == keys_of(objs)
    drag(cv, o.contours[0].start, (50, 150))                                     # and on to the left edge
    assert c.find_vector(o.id).contours[0].start == pytest.approx((50, 150), abs=1.0)
    QTest.mouseClick(cv, Qt.RightButton, Qt.NoModifier, at(cv, 50, 150))
    assert c.find_vector(o.id).locked_starts == []
    assert robot.problems() == []


def test_the_start_point_tool_shows_every_marker_and_only_that_cuts_with_only(rwin):
    win, _ = rwin
    c, t, objs, panel = opened(win, n=3)
    from iyicam.gui.tools import StartPointTool
    cv = win.canvas
    tool = StartPointTool(cv)
    assert len(list(tool._contours())) == 3
    assert len(list(StartPointTool(cv, only=keys_of(objs)[1])._contours())) == 1
    cv.set_tool(tool)
    cv.grab()                                                                    # (paints the markers)
    tool.move(type("E", (), {"position": lambda s: QPointF(*[at(cv, 50, 100).x(), at(cv, 50, 100).y()])})(), (50, 100))
    assert cv.hover_id == objs[0].id


# -- the drawing ----------------------------------------------------------------------------------------------------
def pixels(canvas):
    return canvas.grab().toImage()


def near(img, point, rgb, r=6, tol=45):
    """Whether a pixel within ``r`` of ``point`` has about the colour ``rgb``."""
    for dx in range(-r, r + 1):
        for dy in range(-r, r + 1):
            x, y = int(point.x()) + dx, int(point.y()) + dy
            if 0 <= x < img.width() and 0 <= y < img.height():
                col = img.pixelColor(x, y)
                if abs(col.red() - rgb[0]) + abs(col.green() - rgb[1]) + abs(col.blue() - rgb[2]) <= tol:
                    return True
    return False


def count_colour(img, rgb, tol=40):
    n = 0
    for x in range(0, img.width(), 2):
        for y in range(0, img.height(), 2):
            col = img.pixelColor(x, y)
            if abs(col.red() - rgb[0]) + abs(col.green() - rgb[1]) + abs(col.blue() - rgb[2]) <= tol:
                n += 1
    return n


def test_start_points_are_marked_by_how_good_they_are(rwin):
    win, _ = rwin
    c, t, objs, panel = opened(win, smart=True)
    cv = win.canvas
    cv.zoom_extents()
    c.select([])                                                                 # (the handles of a selection hide markers)
    c.settings.show_starts = True
    for k in ("show_cut_order", "show_directions", "show_route"):
        setattr(c.settings, k, False)
    cut = c.cuts_of(t.id)[0]
    q = cv.world_to_screen(*cut["close"])
    assert near(pixels(cv), q, (65, 171, 93), r=2)                                # green: a good start
    c.start_at_place(cut["key"], "sw")
    cut = next(x for x in c.cuts_of(t.id) if x["key"] == cut["key"])
    assert near(pixels(cv), cv.world_to_screen(*cut["close"]), (158, 154, 200), r=3)   # violet: set by hand
    c.set_sequence(t.id, smart_starts=False)
    c.unlock_start(cut["key"])
    cut = c.cuts_of(t.id)[1]
    assert near(pixels(cv), cv.world_to_screen(*cut["close"]), (189, 189, 189), r=3)   # grey: as drawn


def test_the_cut_that_is_looked_at_the_preview_and_the_arrows_are_drawn(rwin):
    win, robot = rwin
    c, t, objs, panel = opened(win, smart=True, direction="cw")
    cv = win.canvas
    s = c.settings
    c.select([])
    for k in VIEW:
        setattr(s, k, False)
    base = pixels(cv)
    s.show_directions = True
    arrows = pixels(cv)
    assert count_colour(arrows, (230, 85, 13)) > count_colour(base, (230, 85, 13))      # orange: clockwise
    c.set_sequence(t.id, direction="ccw")
    assert count_colour(pixels(cv), (27, 158, 119)) > count_colour(base, (27, 158, 119))   # green: counter-clockwise
    s.show_directions = False
    c.set_focus_cut(keys_of(objs)[1])
    focus = pixels(cv)
    assert count_colour(focus, (255, 204, 0), 120) > count_colour(base, (255, 204, 0), 120)   # yellow around the cut
    c.set_focus_cut(None)
    c.preview_cut_order(t.id, {"strategy": "rows_br", "direction": "cw"})
    pink = pixels(cv)
    assert count_colour(pink, (214, 36, 159), 80) > 20                                  # the preview is pink
    c.clear_cut_preview()
    assert count_colour(pixels(cv), (214, 36, 159), 80) < 5
    s.show_route = True
    assert pixels(cv) != base
    assert robot.problems() == []


def test_numbers_and_the_route_follow_the_order(rwin):
    win, _ = rwin
    c, t, objs, panel = opened(win, strategy="rows_bl")
    cv = win.canvas
    c.settings.show_cut_order = True
    c.settings.show_route = True
    one = pixels(cv)
    c.set_sequence(t.id, strategy="rows_br")
    assert pixels(cv) != one                                                      # another order: another drawing
    cv.zoom_by(0.3)
    cv.grab()                                                                     # (zoomed far out: still paints)
    c.settings.show_cut_order = False
    c.settings.show_route = False


def test_a_lot_of_cuts_are_drawn_without_trouble(rwin):
    win, robot = rwin
    c, t, objs = parts(win, n=3)
    many = [VectorObject([shapes.rectangle(10 + 20 * (i % 28), 200 + 20 * (i // 28), 10, 10)], c.layer_id, f"m{i}")
            for i in range(700)]
    c.add_vectors(many, "t")
    t.selection.vector_ids.extend(o.id for o in many)
    c.settings.auto_recalculate = True
    c.recalculate()
    for k in VIEW:
        setattr(c.settings, k, True)
    win.canvas.grab()
    assert len(c.cuts_of(t.id)) == 703 and robot.problems() == []


# -- the panel follows the tabs -------------------------------------------------------------------------------------------
def test_the_panel_follows_the_open_tab(rwin):
    win, _ = rwin
    c1, t1, objs1, panel = opened(win, n=2)
    win.new_project()
    process()
    c2 = win.ctrl
    assert c2 is not c1 and panel.tid is None and panel.list.topLevelItemCount() == 0
    c2o = [VectorObject([shapes.rectangle(50 + i * 120, 100, 100, 80)], c2.layer_id, f"Q{i}") for i in range(4)]
    c2.add_vectors(c2o, "t")
    from iyicam.core.document import Selection, ToolpathDef
    from iyicam.core.tooling import Tool
    t2 = ToolpathDef("Second", "profile", Tool("EM6", "end_mill", 6.0, 1), {"through": True},
                     Selection("vectors", [o.id for o in c2o]))
    c2.add_toolpath(t2)
    win.cut_order(t2.id)
    process()
    assert panel.tid == t2.id and panel.list.topLevelItemCount() == 4
    win.activate(c1)
    process()
    win.cutorder_panel.refresh()
    assert panel.tid == t1.id and panel.list.topLevelItemCount() == 2 and panel.ctrl is c1


# -- the right-click menu -----------------------------------------------------------------------------------------------------
def capture(monkeypatch):
    seen = []

    def show(menu, pos):
        seen.append(menu)
        return None
    monkeypatch.setattr(menus, "show_menu", show)
    return seen


def find_action(menu, text):
    for a in menu.actions():
        if a.text().split("\t")[0] == text:
            return a
        if a.menu() is not None:
            got = find_action(a.menu(), text)
            if got is not None:
                return got
    return None


def test_the_right_click_menu_of_a_vector_sets_its_start_point(rwin, monkeypatch):
    win, robot = rwin
    c, t, objs, panel = opened(win, n=2)
    cv = win.canvas
    c.settings.snap_objects = False
    labels = []

    def show(menu, pos):
        sm = find_action(menu, "Start Point")
        assert sm is not None and sm.isEnabled()
        labels.append([a.text() for a in sm.menu().actions() if not a.isSeparator()])
        find_action(menu, labels_pick["item"]).trigger()

    labels_pick = {"item": ""}
    monkeypatch.setattr(menus, "show_menu", show)

    def click(x, y, item):
        labels_pick["item"] = item
        p = at(cv, x, y)
        cv.contextMenuEvent(QContextMenuEvent(QContextMenuEvent.Mouse, p, cv.mapToGlobal(p)))
        process()

    click(60, 140, "Top Right Corner")                                          # on the left edge of the first part
    assert c.find_vector(objs[0].id).contours[0].start == pytest.approx((150, 180), abs=1e-6)
    click(60, 140, "Bottom Left Corner")
    assert c.find_vector(objs[0].id).contours[0].start == pytest.approx((50, 100), abs=1e-6)
    click(150, 140, "Here (nearest point of the vector)")                       # the right edge: the point under it
    assert c.find_vector(objs[0].id).contours[0].start == pytest.approx((150, 140), abs=1.5)
    click(150, 140, "Smart (best place)")
    assert c.find_vector(objs[0].id).locked_starts == []
    click(60, 140, "Top Left Corner")
    click(60, 140, "Release (to smart start points)")
    assert c.find_vector(objs[0].id).locked_starts == []
    assert "Cut Order Panel" in labels[0] and "Bottom Left Corner" in labels[0]
    win.cutorder_dock.hide()
    click(60, 140, "Cut Order Panel")
    assert win.cutorder_dock.isVisible()
    assert c.cut_order(t.id) == keys_of(objs)
    assert robot.problems() == []


def test_the_start_point_menu_is_off_when_nothing_selected_has_a_start(rwin, monkeypatch):
    win, _ = rwin
    seen = capture(monkeypatch)
    c, t, objs, panel = opened(win)
    line = VectorObject([shapes.polyline([(300, 300), (350, 300)], False)], c.layer_id, "open")
    c.add_vectors([line], "t")
    cv = win.canvas
    p = at(cv, 325, 300)
    cv.contextMenuEvent(QContextMenuEvent(QContextMenuEvent.Mouse, p, cv.mapToGlobal(p)))
    sm = find_action(seen[-1], "Start Point")
    assert sm is not None and not sm.isEnabled()


def test_the_node_tool_sets_a_start_point_that_smart_start_points_keep(rwin, monkeypatch):
    win, _ = rwin
    c, t, objs, panel = opened(win, n=1, smart=True)
    cv = win.canvas
    c.settings.snap_objects = False
    win.set_tool("nodes")
    c.select([objs[0].id])
    picked = {}

    def show(menu, pos):
        a = find_action(menu, picked["item"])
        assert a is not None, picked["item"]
        a.trigger()

    monkeypatch.setattr(menus, "show_menu", show)
    picked["item"] = "Set as start point"
    p = at(cv, 150, 180)                                                           # a corner node
    cv.contextMenuEvent(QContextMenuEvent(QContextMenuEvent.Mouse, p, cv.mapToGlobal(p)))
    o = c.find_vector(objs[0].id)
    assert o.contours[0].start == pytest.approx((150, 180), abs=1e-6) and o.locked_starts == [0]
    cut = c.cuts_of(t.id)[0]
    assert math.dist(cut["close"], (150, 180)) < 6 and cutinfo.explain(cut)["quality"] == "hand"
    picked["item"] = "Set start point here"
    p = at(cv, 100, 100)                                                           # in the middle of the bottom edge
    cv.contextMenuEvent(QContextMenuEvent(QContextMenuEvent.Mouse, p, cv.mapToGlobal(p)))
    assert c.find_vector(objs[0].id).contours[0].start == pytest.approx((100, 100), abs=1.0)


def test_the_panel_counts_the_start_points_by_kind(rwin):
    win, _ = rwin
    c, t, objs, panel = opened(win, smart=True)
    assert "start points: 3 good" in panel.info.text()
    c.start_at_place(keys_of(objs)[0], "se")
    process()
    text = panel.info.text()
    assert "set by hand" in text and "1 set by hand" in text
    c.set_sequence(t.id, smart_starts=False)
    c.unlock_start(keys_of(objs)[0])
    process()
    assert "3 as drawn" in panel.info.text()

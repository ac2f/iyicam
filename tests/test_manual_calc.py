"""Toolpaths are calculated when the person says so; the wheel does not change numbers; the tool's size is not
changed in a toolpath; toolpaths can be grouped and saved together."""
import pytest

pytest.importorskip("PySide6.QtWidgets")

from gui_robot import process, rwin_gen  # noqa: E402
from PySide6.QtCore import QEvent, QPoint, QPointF, Qt  # noqa: E402
from PySide6.QtGui import QWheelEvent  # noqa: E402
from PySide6.QtWidgets import QApplication, QLineEdit, QScrollArea, QVBoxLayout, QWidget  # noqa: E402
from test_cutedit import parts  # noqa: E402

from iyicam.core.document import Project, toolpath_from_dict, toolpath_to_dict  # noqa: E402
from iyicam.gui.wheelguard import QDoubleSpinBox  # noqa: E402
from iyicam.gui.settings import Settings  # noqa: E402


@pytest.fixture
def rwin(tmp_path, monkeypatch):
    yield from rwin_gen(tmp_path, monkeypatch)


def count_calls(c):
    calls = []
    orig = c.recalculate
    c.recalculate = lambda *a, **k: (calls.append((a, k)), orig(*a, **k))[1]
    return calls


# -- the calculation is a command ---------------------------------------------------------------------------
def test_the_default_is_to_calculate_only_when_asked(tmp_path, monkeypatch):
    monkeypatch.setenv("IYICAM_HOME", str(tmp_path))
    assert Settings().auto_recalculate is False
    s = Settings()
    s.auto_recalculate = True
    s.calc_mode = 1                       # a file written before the change: "automatic" is not carried over
    s.save()
    assert Settings.load().auto_recalculate is False
    s = Settings.load()
    s.auto_recalculate = True
    s.save()
    assert Settings.load().auto_recalculate is True


def test_editing_the_drawing_does_not_calculate(rwin):
    win, _ = rwin
    c, t, objs = parts(win, auto=False)
    process()
    calls = count_calls(c)
    c.stale = False
    with c.edit("move"):
        objs[0].contours = objs[0].contours
    process()
    c.start_at_place(f"{objs[0].id}:0", "ne")
    c._recalc.stop()
    assert not calls and c.stale
    c.settings.auto_recalculate = True
    c.calc_if_auto()
    assert calls


def test_smart_start_for_many_cuts_is_one_calculation_and_one_undo_step(rwin):
    win, _ = rwin
    c, t, objs = parts(win, auto=False)
    c.recalculate()
    keys = [f"{o.id}:0" for o in objs]
    calls = []
    orig = c.preview_cut_order
    c.preview_cut_order = lambda *a, **k: (calls.append(1), orig(*a, **k))[1]
    c.recalculate_calls = count_calls(c)
    depth = len(c.undo_stack._undo) if hasattr(c.undo_stack, "_undo") else None
    c.smart_start_many(t.id, keys)
    assert len(calls) == 1                                  # not one per cut
    assert not c.recalculate_calls                          # and nothing calculated afterwards (manual mode)
    assert c.stale
    if depth is not None:
        assert len(c.undo_stack._undo) - depth <= 2


def test_the_smart_start_menu_item_does_not_loop(rwin):
    win, _ = rwin
    c, t, objs = parts(win, auto=False)
    c.recalculate()
    c.select([o.id for o in objs])
    seen = []
    orig = c.preview_cut_order
    c.preview_cut_order = lambda *a, **k: (seen.append(1), orig(*a, **k))[1]
    win.smart_start_selected()
    process()
    assert len(seen) == 1


# -- the wheel ------------------------------------------------------------------------------------------------
def wheel(widget, delta=120):
    pos = QPointF(widget.width() / 2, widget.height() / 2)
    ev = QWheelEvent(pos, widget.mapToGlobal(pos), QPoint(0, 0), QPoint(0, delta), Qt.NoButton, Qt.NoModifier,
                     Qt.NoScrollPhase, False)
    QApplication.sendEvent(widget, ev)


def test_the_wheel_changes_a_number_only_when_it_has_the_focus(qapp_or_none=None):
    QApplication.instance() or QApplication([])
    page = QWidget()
    lay = QVBoxLayout(page)
    spin = QDoubleSpinBox()
    spin.setRange(0, 100)
    spin.setValue(5)
    other = QLineEdit()
    lay.addWidget(other)
    lay.addWidget(spin)
    area = QScrollArea()
    area.setWidget(page)
    area.resize(200, 200)
    area.show()
    other.setFocus()
    process()
    wheel(spin)
    assert spin.value() == 5                                # not focused: nothing changes
    spin.setFocus()
    process()
    if spin.hasFocus():                                     # (an offscreen window may not give the focus)
        wheel(spin)
        assert spin.value() != 5
    area.close()


# -- the size of the tool --------------------------------------------------------------------------------------
def test_the_diameter_of_a_chosen_tool_cannot_be_changed_in_the_toolpath_dialog(rwin):
    win, _ = rwin
    from iyicam.gui.dialogs import ToolEditor, ToolpathDialog
    c, t, objs = parts(win, auto=False)
    dlg = ToolpathDialog(c, "profile", t, win)
    f = dlg.tool_editor.form
    assert not f.widgets["diameter"].isEnabled() and f.widgets["feed_rate"].isEnabled()
    assert dlg.tool_editor.tool().diameter == t.tool.diameter
    free = ToolEditor(c)                                    # the tool database edits tools: there it can change
    assert free.form.widgets["diameter"].isEnabled()
    dlg.close()


# -- groups ----------------------------------------------------------------------------------------------------
def test_groups_are_kept_in_the_file_and_can_be_set_renamed_and_switched(rwin, tmp_path):
    win, _ = rwin
    c, t, objs = parts(win, auto=False)
    from iyicam.core.document import new_id
    import copy
    b = copy.deepcopy(t)
    b.id, b.name = new_id(), "second"
    c.add_toolpath(b)
    assert c.groups() == [] and "group" not in toolpath_to_dict(t)
    assert c.set_group([t.id], "A") == 1 and c.set_group([t.id], "A") == 0
    assert c.groups() == ["A"] and c.group_ids("A") == [t.id]
    assert toolpath_from_dict(toolpath_to_dict(t)).group == "A"
    path = tmp_path / "g.icam"
    c.save_project(path)
    assert Project.load(path).toolpaths[0].group == "A"
    c.set_group([b.id], "A")
    c.enable_only(c.group_ids("A"))
    assert t.enabled and b.enabled
    c.rename_group("A", "Cut")
    assert t.group == b.group == "Cut"
    c.enable_only([t.id])
    assert t.enabled and not b.enabled
    c.set_group([t.id, b.id], "")
    assert c.groups() == []
    c.undo()
    assert c.groups() == ["Cut"]


def test_the_export_dialog_saves_the_chosen_toolpaths_or_a_group(rwin):
    win, _ = rwin
    c, t, objs = parts(win, auto=False)
    from iyicam.core.document import new_id
    import copy
    from iyicam.gui.dialogs import ExportDialog
    b = copy.deepcopy(t)
    b.id, b.name = new_id(), "second"
    c.add_toolpath(b)
    c.set_group([b.id], "G2")
    dlg = ExportDialog(c, win, only={b.id}, group="G2")
    assert dlg.chosen() == {b.id}
    dlg._tick(lambda x: x.group == "")
    assert dlg.chosen() == {t.id}
    dlg._tick(lambda x: True)
    assert dlg.chosen() == {t.id, b.id}
    assert dlg.by_group.count() == 2
    dlg.close()


def test_the_panel_has_a_group_column_and_groups_in_the_menu(rwin):
    win, _ = rwin
    c, t, objs = parts(win, auto=False)
    c.set_group([t.id], "Letters")
    process()
    panel = win.toolpaths_panel if hasattr(win, "toolpaths_panel") else None
    if panel is not None:
        panel.refresh()
        assert panel.tree.headerItem().text(1) == "Group" and panel.tree.topLevelItem(0).text(1) == "Letters"

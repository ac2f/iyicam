"""The panels (layers, sheets, toolpaths, properties) driven the way a person uses them."""
import pytest

pytest.importorskip("PySide6.QtWidgets")

from conftest import end_mill  # noqa: E402
from gui_robot import populate, process, rwin_gen  # noqa: E402
from PySide6.QtCore import Qt  # noqa: E402
from test_chaos import consistent  # noqa: E402

from iyicam.core.document import Selection, ToolpathDef  # noqa: E402


@pytest.fixture
def rwin(tmp_path, monkeypatch):
    yield from rwin_gen(tmp_path, monkeypatch)


def ok(win, robot, what=""):
    process()
    assert robot.problems() == [], what
    assert consistent(win.ctrl) is None, f"{what}: {consistent(win.ctrl)}"


def test_layers_panel(rwin):
    win, robot = rwin
    c, lp = win.ctrl, win.layers_panel
    populate(win, select=False)
    lp.add()
    lp.add()
    assert len(c.project.layers) >= 4
    lp.tree.setCurrentItem(lp.tree.topLevelItem(1))
    lp.rename()
    lp.color()
    lp.move(-1)
    lp.move(1)
    lp.move(-5)
    lp.select_layer()
    c.select([v.id for v in c.sheet.vectors[:2]])
    lp.tree.setCurrentItem(lp.tree.topLevelItem(lp.tree.topLevelItemCount() - 1))
    lp.move_selection()
    # show / lock toggles
    for i in range(lp.tree.topLevelItemCount()):
        for col in (1, 2):
            for state in (Qt.Unchecked, Qt.Checked):
                lp.tree.topLevelItem(i).setCheckState(col, state)   # the panel rebuilds its rows each time
    lp._menu(lp.tree.visualItemRect(lp.tree.topLevelItem(0)).center())
    n = len(c.project.layers)
    for _ in range(n + 2):          # delete layers until the last one: there is always one left
        lp.tree.setCurrentItem(lp.tree.topLevelItem(0))
        lp.delete()
        ok(win, robot, "delete layer")
    assert len(c.project.layers) >= 1
    c.undo()
    ok(win, robot, "undo")


def test_sheets_panel(rwin):
    win, robot = rwin
    c, sp = win.ctrl, win.sheets_panel
    populate(win, select=False)
    t = ToolpathDef("p", "profile", end_mill(6), {"through": True}, Selection("layers", [], [c.layer_id]))
    t.sheet_ids = [c.sheet_id]
    c.add_toolpath(t)
    sp.add()
    sp.duplicate()
    sp.properties()
    sp.list.setCurrentRow(0)
    sp.delete()
    ok(win, robot, "delete the sheet a toolpath was made for")
    while len(c.project.sheets) > 1:
        sp.delete()
    sp.delete()                     # the last sheet stays
    assert len(c.project.sheets) == 1
    ok(win, robot, "sheets")
    for _ in range(4):
        c.undo()
        ok(win, robot, "undo")


def test_toolpaths_panel(rwin):
    win, robot = rwin
    c, tp = win.ctrl, win.toolpaths_panel
    populate(win, select=False)
    for op in ("profile", "pocket", "engrave", "drill", "vcarve"):
        win.new_toolpath(op)
    assert len(c.project.toolpaths) == 5
    tp.tree.selectAll()
    tp.duplicate()
    assert len(c.project.toolpaths) == 10
    tp.tree.clearSelection()
    tp.tree.topLevelItem(0).setSelected(True)
    tp.edit()
    tp.move(1)
    tp.move(-3)
    tp.select_vectors()
    tp._menu(tp.tree.visualItemRect(tp.tree.topLevelItem(0)).center())
    for state in (Qt.Unchecked, Qt.Checked):
        tp.tree.topLevelItem(1).setCheckState(0, state)
    tp.recalculate()
    ok(win, robot, "toolpaths")
    tp.tree.selectAll()
    tp.delete()
    assert c.project.toolpaths == []
    ok(win, robot, "delete all")
    c.undo()
    assert len(c.project.toolpaths) == 10


def test_properties_panel_with_extreme_numbers(rwin):
    win, robot = rwin
    c, pp = win.ctrl, win.props_panel
    objs = populate(win, select=False)
    c.select([objs[0].id])
    before = objs[0].bbox()
    for key, v in (("w", 0.0), ("h", 0.0), ("w", -50.0), ("x", 1e6), ("y", -1e6), ("w", 1e6), ("h", 1e-6)):
        pp.spins[key].setValue(c.units.to_display(v))
        pp.apply()
        ok(win, robot, f"{key} = {v}")
        bb = c.selected_objects()[0].bbox()
        assert bb.width > 0 and bb.height > 0, f"setting {key} = {v} collapsed the vector"
    c.undo()
    c.undo()
    pp.name.setText("renamed")
    pp.rename()
    pp.set_layer(pp.layer.count() - 1)
    c.select([])
    pp.refresh()
    c.select([o.id for o in objs])
    pp.apply()
    pp.keep.setChecked(False)
    pp.spins["w"].setValue(c.units.to_display(77.0))
    pp.apply()
    ok(win, robot, "several vectors")
    assert before is not None

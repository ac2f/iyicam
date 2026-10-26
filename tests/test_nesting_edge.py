"""Nesting from the menu with awkward projects: nothing selected, locked layers, open vectors, a part bigger
than the sheet, nothing to place, text with counters, several sheets."""
import pytest

pytest.importorskip("PySide6.QtWidgets")

from gui_robot import rwin_gen  # noqa: E402
from test_nesting_invariants import census  # noqa: E402

from iyicam.core import shapes  # noqa: E402
from iyicam.core.document import VectorObject  # noqa: E402


@pytest.fixture
def rwin(tmp_path, monkeypatch):
    yield from rwin_gen(tmp_path, monkeypatch)


def set_nesting(robot, **values):
    def hook(dlg):
        if dlg.windowTitle() == "Nesting":
            w = dlg.form.widgets
            for k, v in values.items():
                wd = w[k]
                if hasattr(wd, "findData"):
                    wd.setCurrentIndex(wd.findData(v))
                elif hasattr(wd, "setChecked"):
                    wd.setChecked(v)
                else:
                    wd.setValue(v)
    robot.hook = hook


def texts(robot):
    return " | ".join(text for _, text in robot.messages)


def test_nothing_to_nest(rwin):
    win, robot = rwin
    win.nest()
    assert "no vectors to nest" in texts(robot) and robot.problems() == []


def test_vectors_on_a_locked_layer_are_not_moved(rwin):
    win, robot = rwin
    c = win.ctrl
    objs = [VectorObject([shapes.rectangle(100 * i, 0, 60, 40)], c.layer_id) for i in range(3)]
    c.add_vectors(objs, select=False)
    c.project.layers[0].locked = True
    before = census(c.project)
    win.nest()
    assert "locked" in texts(robot) and census(c.project) == before
    assert robot.problems() == []


def test_a_part_bigger_than_the_sheet_stays_and_is_reported(rwin):
    win, robot = rwin
    c = win.ctrl
    big = VectorObject([shapes.rectangle(0, 0, 5000, 5000)], c.layer_id)
    small = VectorObject([shapes.rectangle(6000, 0, 50, 50)], c.layer_id)
    c.add_vectors([big, small], select=False)
    before = big.bbox()
    set_nesting(robot, target="current", new_sheets=False, iterations=2)
    win.nest()
    assert "did not fit" in texts(robot)
    assert (big.bbox().xmin, big.bbox().ymin) == (before.xmin, before.ymin)      # stays where it was
    assert robot.problems() == []
    c.undo()
    assert robot.problems() == []


def test_open_vectors_and_text_with_counters(rwin):
    from iyicam.gui.qtgeom import text_contours
    win, robot = rwin
    c = win.ctrl
    lines = [VectorObject([shapes.polyline([(0, 100 * i), (80, 100 * i + 30)])], c.layer_id) for i in range(3)]
    text = VectorObject(text_contours("ROB", "DejaVu Sans", 80.0), c.layer_id, "Text")
    c.add_vectors(lines + [text], select=False)
    before = census(c.project)
    set_nesting(robot, fill_holes=True, group_inner=False, iterations=3, margin=5.0, spacing=3.0)
    win.nest()
    after = census(c.project)
    assert after == before, "nesting changed what there is"
    assert robot.problems() == []


def test_several_sheets_all_nested_and_undone(rwin):
    win, robot = rwin
    c = win.ctrl
    for k in range(3):
        sh = c.project.add_sheet(None, 400, 300) if k else c.sheet
        sh.width, sh.height = 400, 300
        sh.vectors.extend(VectorObject([shapes.rectangle(20 + 90 * i, 20, 80, 50)], c.layer_id) for i in range(3))
    before = census(c.project)
    n_sheets = len(c.project.sheets)
    set_nesting(robot, target="all", new_sheets=True, iterations=4)
    win.nest()
    assert census(c.project) == before
    assert robot.problems() == []
    c.undo()
    assert census(c.project) == before and len(c.project.sheets) == n_sheets
    c.redo()
    assert census(c.project) == before


def test_all_quantities_zero_changes_nothing(rwin):
    win, robot = rwin
    c = win.ctrl
    objs = [VectorObject([shapes.rectangle(100 * i, 0, 60, 40)], c.layer_id) for i in range(3)]
    c.add_vectors(objs, select=False)
    before = census(c.project)

    def hook(dlg):
        if dlg.windowTitle() == "Nesting":
            for sp in dlg.qty:
                sp.setValue(0)
    robot.hook = hook
    win.nest()
    assert census(c.project) == before and robot.problems() == []

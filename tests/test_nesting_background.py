"""Nesting in the background: the program is usable while it runs (other tabs, other sheets); the layout goes into
its project when it is over, or not at all when the vectors were changed meanwhile or it was cancelled; one nesting at
a time per project; the sheet it was nested on joins the group chosen for the new sheets."""
import pytest

pytest.importorskip("PySide6.QtWidgets")

from PySide6.QtWidgets import QDialog, QMessageBox  # noqa: E402

from gui_robot import rwin_gen  # noqa: E402
from iyicam.core import shapes  # noqa: E402
from iyicam.core.document import Project, Sheet, VectorObject  # noqa: E402
from iyicam.core.scope import group_new_sheets  # noqa: E402
from iyicam.gui.dialogs import NestingDialog  # noqa: E402


@pytest.fixture
def rwin(tmp_path, monkeypatch):
    yield from rwin_gen(tmp_path, monkeypatch)


def parts(win, n=6):
    c = win.ctrl
    objs = [VectorObject([shapes.rectangle(10 + 120 * i, 10, 100, 60)], c.layer_id) for i in range(n)]
    c.add_vectors(objs)
    return c, objs


def dialog(win, qty=30):
    nd = NestingDialog(win.ctrl, win)
    w = nd.form.widgets
    w["width"].setValue(win.ctrl.units.to_display(400.0))
    w["height"].setValue(win.ctrl.units.to_display(300.0))
    nd.qty[0].setValue(qty)
    return nd


def test_the_layout_comes_when_it_is_over_and_the_program_was_usable_meanwhile(rwin):
    win, _ = rwin
    c, objs = parts(win)
    nd = dialog(win)
    seen, done = [], []
    job = nd.start(done.append, lambda k, n: seen.append((k, n)))
    assert job.running and not done                         # (start came back at once: the work goes on)
    assert sum(len(s.vectors) for s in c.project.sheets) == 6
    job.wait(120)
    assert not job.running and done and done[0] is not None and done[0].placed > 6
    assert sum(len(s.vectors) for s in c.project.sheets) == done[0].placed
    c.undo()                                                 # (one undo step)
    assert sum(len(s.vectors) for s in c.project.sheets) == 6


def test_vectors_changed_meanwhile_keep_the_layout_out(rwin, monkeypatch):
    win, _ = rwin
    c, objs = parts(win)
    said = []
    monkeypatch.setattr(QMessageBox, "warning", lambda *a, **k: said.append(a[2]))
    done = []
    job = dialog(win).start(done.append)
    with c.edit("move one"):
        objs[0].transform(__import__("iyicam.core.geom", fromlist=["Transform"]).Transform.translate(5, 0))
    job.wait(120)
    assert done == [None] and said and "changed while it ran" in said[0]
    assert sum(len(s.vectors) for s in c.project.sheets) == 6


def test_cancel_changes_nothing(rwin):
    win, _ = rwin
    c, objs = parts(win)
    done = []
    job = dialog(win, qty=200).start(done.append)
    job.cancel()
    job.wait(120)
    assert done == [None] and sum(len(s.vectors) for s in c.project.sheets) == 6


def test_the_window_runs_it_in_the_background_one_per_project(rwin, monkeypatch):
    win, _ = rwin
    c, objs = parts(win)
    monkeypatch.setattr(NestingDialog, "exec", lambda self: QDialog.Accepted)
    said = []
    monkeypatch.setattr(QMessageBox, "information", lambda *a, **k: said.append(a[2]))
    monkeypatch.setattr("iyicam.gui.main_window.NewSheetsGroupDialog.exec", lambda self: 0)
    where = [(o.bbox().xmin, o.bbox().ymin) for o in objs]
    nest = type(win).nest.without_waiting                    # (the robot waits for a nesting: not here)
    nest(win)
    assert win.nesting_running(c)
    nest(win)                                                # (a second one of the same project is refused)
    assert said and "is running" in said[-1]
    win._nest_jobs[id(c)].wait(120)
    assert not win.nesting_running(c)
    live = {o.id: o for s in c.project.sheets for o in s.vectors}
    assert [(live[o.id].bbox().xmin, live[o.id].bbox().ymin) for o in objs] != where       # (the layout came in)
    assert not win.statusBar().findChildren(__import__("PySide6.QtWidgets", fromlist=["QProgressBar"]).QProgressBar) or \
        all(not b.isVisible() for b in win.statusBar().findChildren(__import__("PySide6.QtWidgets", fromlist=["QProgressBar"]).QProgressBar))


def test_the_sheet_nested_on_joins_the_group_chosen():
    p = Project("g")
    start = p.sheets[0]
    new1, new2 = Sheet("N1"), Sheet("N2")
    p.sheets += [new1, new2]
    assert group_new_sheets(p, [new1, new2], "Doors", start, [start]) == "Doors"
    assert start.group == new1.group == new2.group == "Doors"
    other = Sheet("O")
    p.sheets.append(other)
    assert group_new_sheets(p, [other], "", start, [start]) == ""      # ("no group": the sheets nested on stay where they are)
    assert start.group == "Doors" and other.group == ""

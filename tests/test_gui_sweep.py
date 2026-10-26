"""Drive the whole GUI: every menu action, every drawing tool, every toolpath type, in several states.

Nothing may raise, no "critical" box may appear and no warning may look like a program fault.
"""
import traceback

import pytest

pytest.importorskip("PySide6.QtWidgets")

from gui_robot import Robot, process  # noqa: E402
from PySide6.QtCore import QPoint, Qt  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402

from iyicam.core import shapes  # noqa: E402
from iyicam.core.document import VectorObject  # noqa: E402


@pytest.fixture
def rwin(tmp_path, monkeypatch):
    monkeypatch.setenv("IYICAM_HOME", str(tmp_path / "home"))
    robot = Robot().install(monkeypatch)
    from iyicam.gui.app import create_window
    app, w = create_window(["test"], load_plugins=False)
    w.resize(1200, 800)
    w.show()
    process()
    yield w, robot
    w.ctrl.dirty = False
    w.close()
    process()


def populate(win, select=True):
    """Parts of every kind on two layers and two sheets."""
    from iyicam.gui.qtgeom import text_contours
    c = win.ctrl
    lay2 = c.project.layer_by_name("Parts") or c.project.add_layer("Parts")
    base = c.layer_id
    objs = [
        VectorObject([shapes.rectangle(20, 20, 120, 80, corner_radius=8)], base, "Panel"),
        VectorObject([shapes.circle((200, 60), 30)], base, "Disc"),
        VectorObject([shapes.rectangle(260, 20, 110, 90), shapes.circle((315, 65), 18)], lay2.id, "Ring part"),
        VectorObject([shapes.polyline([(20, 150), (120, 160), (180, 150), (240, 190)])], base, "Open line"),
        VectorObject([shapes.polyline([(300, 150), (380, 150), (380, 220), (340, 250), (300, 220)], True)],
                     lay2.id, "Pentagon"),
        VectorObject(text_contours("Ab", "DejaVu Sans", 30.0), base, "Text"),
    ]
    from iyicam.core.geom import Transform
    objs[-1].transform(Transform.translate(40, 260))
    c.add_vectors(objs, select=False)
    if select:
        c.select([o.id for o in objs])
    return objs


def check(robot, what):
    bad = robot.problems()
    assert not bad, f"{what}:\n" + "\n---\n".join(bad)


def all_actions(menu):
    for a in menu.actions():
        if a.menu() is not None:
            yield from all_actions(a.menu())
        elif not a.isSeparator():
            yield a


# ---------------------------------------------------------------------------------------------------------
def test_every_menu_action_in_three_states(rwin):
    win, robot = rwin
    c = win.ctrl
    actions = [(m.menu().title(), a) for m in win.menuBar().actions() if m.menu() for a in all_actions(m.menu())]
    assert len(actions) > 80
    failures = []
    for state in ("everything selected", "nothing selected", "empty project"):
        for top, a in actions:
            name = a.text().replace("&", "")
            if name in ("Quit", "Python Console") or name.startswith(("Undo", "Redo")) and False:
                continue
            c.new_project(600, 400, 18)
            if state != "empty project":
                populate(win, select=state == "everything selected")
                if "Cut Order" in name or "Start" in name or "G-code" in name or "Simulate" in name:
                    from conftest import end_mill
                    from iyicam.core.document import Selection, ToolpathDef
                    c.project.toolpaths.append(ToolpathDef("p", "profile", end_mill(6), {"through": True},
                                                           Selection("layers", [], [l.id for l in c.project.layers])))
                    c.recalculate()
            robot.reset()
            try:
                a.trigger()
                process()
            except Exception as e:  # noqa: BLE001
                robot.errors.append(f"raised {type(e).__name__}: {e}\n{traceback.format_exc()[-1200:]}")
            bad = robot.problems()
            if bad:
                failures.append(f"[{state}] {top} > {name}: {bad[0][:600]}")
    assert not failures, "\n\n".join(failures)


def test_every_drawing_tool_with_mouse_and_keys(rwin):
    from iyicam.gui.tools import TOOLS
    win, robot = rwin
    c = win.ctrl
    cv = win.canvas
    win.canvas.zoom_extents()
    failures = []
    for name in TOOLS:
        c.new_project(600, 400, 18)
        populate(win, select=name in ("nodes", "start"))
        win.set_tool(name)
        robot.reset()
        pts = [(50, 50), (150, 60), (170, 150), (60, 140)]
        try:
            for mods in (Qt.NoModifier, Qt.ShiftModifier, Qt.ControlModifier):
                for i, (x, y) in enumerate(pts):
                    p = cv.world_to_screen(x, y).toPoint()
                    QTest.mouseMove(cv, p)
                    QTest.mouseClick(cv, Qt.LeftButton, mods, p)
                p = cv.world_to_screen(*pts[0]).toPoint()
                QTest.mouseDClick(cv, Qt.LeftButton, Qt.NoModifier, p)
                # a drag
                a_ = cv.world_to_screen(30, 30).toPoint()
                b_ = cv.world_to_screen(90, 80).toPoint()
                QTest.mousePress(cv, Qt.LeftButton, mods, a_)
                for t in (0.3, 0.7, 1.0):
                    QTest.mouseMove(cv, QPoint(int(a_.x() + (b_.x() - a_.x()) * t), int(a_.y() + (b_.y() - a_.y()) * t)))
                QTest.mouseRelease(cv, Qt.LeftButton, mods, b_)
                QTest.mouseClick(cv, Qt.RightButton, mods, p)
                for key in (Qt.Key_Return, Qt.Key_Escape, Qt.Key_Delete, Qt.Key_Backspace, Qt.Key_Tab):
                    QTest.keyClick(cv, key)
                QTest.mouseMove(cv, p)
                process()
        except Exception as e:  # noqa: BLE001
            robot.errors.append(f"raised {type(e).__name__}: {e}\n{traceback.format_exc()[-1200:]}")
        bad = robot.problems()
        if bad:
            failures.append(f"tool {name}: {bad[0][:700]}")
    assert not failures, "\n\n".join(failures)


@pytest.mark.parametrize("state", ["selected closed", "selected open", "layers", "nothing", "empty project",
                                   "no tools in the library"])
def test_every_toolpath_type_can_be_created(rwin, state):
    from iyicam.core.registry import REGISTRY
    win, robot = rwin
    c = win.ctrl
    failures = []
    for op_id in list(REGISTRY.operations):
        c.new_project(600, 400, 18)
        objs = [] if state == "empty project" else populate(win, select=False)
        if state == "selected closed":
            c.select([o.id for o in objs if any(k.closed for k in o.contours)])
        elif state == "selected open":
            c.select([o.id for o in objs if not any(k.closed for k in o.contours)])
        elif state == "no tools in the library":
            c.tools.tools.clear()
            c.select([o.id for o in objs])
        robot.reset()
        try:
            win.new_toolpath(op_id)
            process()
            for t in c.project.toolpaths:
                for r in c.results_for(t):
                    if r.error:
                        robot.errors.append(f"{op_id}: result error {r.error[:400]}")
        except Exception as e:  # noqa: BLE001
            robot.errors.append(f"raised {type(e).__name__}: {e}\n{traceback.format_exc()[-1200:]}")
        bad = robot.problems()
        if bad:
            failures.append(f"{op_id} [{state}]: {bad[0][:800]}")
    assert not failures, "\n\n".join(failures)

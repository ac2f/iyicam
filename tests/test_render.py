"""The drawing area paints whatever the project, the view and the display options are."""
import itertools
import random

import pytest

pytest.importorskip("PySide6.QtWidgets")

from conftest import end_mill  # noqa: E402
from gui_robot import populate, process, rwin_gen  # noqa: E402

from iyicam.core.document import Selection, ToolpathDef  # noqa: E402

@pytest.fixture
def rwin(tmp_path, monkeypatch):
    yield from rwin_gen(tmp_path, monkeypatch)


OPTIONS = ("show_toolpaths", "show_tool_width", "show_directions", "show_cut_order", "show_route", "show_starts",
           "snap_grid", "snap_objects")


def with_toolpaths(win):
    c = win.ctrl
    objs = populate(win, select=False)
    for op, tool in (("profile", end_mill(6)), ("pocket", end_mill(4)), ("engrave", end_mill(2)),
                     ("drill", end_mill(3))):
        t = ToolpathDef(op, op, tool, {}, Selection("layers", [], [la.id for la in c.project.layers]))
        c.project.toolpaths.append(t)
    c.recalculate()
    return objs


def test_every_display_option_zoom_and_size_paints(rwin):
    win, robot = rwin
    c, cv = win.ctrl, win.canvas
    with_toolpaths(win)
    rnd = random.Random(3)
    for combo in list(itertools.product([False, True], repeat=3)) + [tuple(rnd.random() < 0.5 for _ in range(3))
                                                                      for _ in range(6)]:
        for name, on in zip(OPTIONS, combo * 2):
            win._toggle(name, on)
        for grid in (0.5, 10.0, 1000.0):
            c.settings.grid = grid
            for size in ((1200, 800), (300, 200), (2, 2)):
                win.resize(*size)
                process()
                for zoom in (None, 1e-3, 0.05, 1.0, 40.0, 1e5):
                    cv.zoom_extents() if zoom is None else setattr(cv, "scale", zoom)
                    for centre in ((300.0, 200.0), (-1e6, 1e6), (0.0, 0.0)):
                        cv.cx, cv.cy = centre
                        cv.grab()
    assert robot.problems() == []


def test_painting_with_a_simulation_a_selection_a_guide_and_a_tool_in_use(rwin):
    win, robot = rwin
    c, cv = win.ctrl, win.canvas
    objs = with_toolpaths(win)
    c.select([o.id for o in objs[:3]])
    win.simulate()
    process()
    cv.cx, cv.cy = 100.0, 100.0
    for name in ("select", "nodes", "rectangle", "polyline", "circle", "start"):
        win.set_tool(name)
        for p in ((100, 100), (1e6, -1e6), (0, 0)):
            cv.mouse_world = p
            cv.snap_marker = (p, "grid")
            cv.update()
            cv.grab()
    win.clear_simulation()
    cv.grab()
    assert robot.problems() == []


def test_painting_an_empty_project_and_a_project_of_nothing_but_odd_vectors(rwin):
    from iyicam.core import shapes
    from iyicam.core.document import VectorObject
    win, robot = rwin
    c, cv = win.ctrl, win.canvas
    cv.grab()
    odd = [VectorObject([shapes.circle((0, 0), 1e-4)], c.layer_id), VectorObject([shapes.rectangle(-1e5, -1e5, 2e5, 2e5)],
                                                                                 c.layer_id),
           VectorObject([shapes.polyline([(5, 5), (5, 5.00001)])], c.layer_id)]
    c.add_vectors(odd)
    win.canvas.zoom_extents()
    cv.grab()
    c.project.sheets[0].width = 1e-3
    c.project.sheets[0].height = 1e-3
    c.sheet_changed.emit()
    win.canvas.zoom_extents()
    cv.grab()
    assert robot.problems() == []

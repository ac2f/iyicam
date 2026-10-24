"""Offscreen GUI smoke tests: drive the main window like a user would."""
import math

import pytest

pytest.importorskip("PySide6.QtWidgets")

from PySide6.QtCore import QPoint, QPointF, Qt  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from iyicam.core import shapes  # noqa: E402
from iyicam.core.document import VectorObject  # noqa: E402
from iyicam.core.geom import Bezier  # noqa: E402


@pytest.fixture
def win():
    from iyicam.gui.app import create_window
    app, w = create_window(["test"], load_plugins=False)
    w.resize(1200, 800)
    w.show()
    QApplication.processEvents()
    w.ctrl.settings.auto_recalculate = False
    w.canvas.zoom_extents()
    yield w
    w.ctrl.dirty = False
    w.close()
    QApplication.processEvents()


def at(canvas, x, y) -> QPoint:
    return canvas.world_to_screen(x, y).toPoint()


def drag(canvas, a, b, mods=Qt.NoModifier):
    pa, pb = at(canvas, *a), at(canvas, *b)
    QTest.mousePress(canvas, Qt.LeftButton, mods, pa)
    for t in (0.25, 0.5, 0.75, 1.0):
        p = QPoint(int(pa.x() + (pb.x() - pa.x()) * t), int(pa.y() + (pb.y() - pa.y()) * t))
        QTest.mouseMove(canvas, p)
    QTest.mouseRelease(canvas, Qt.LeftButton, mods, pb)
    QApplication.processEvents()


def test_draw_rectangle_circle_and_polyline(win):
    cv, ctrl = win.canvas, win.ctrl
    ctrl.settings.snap_objects = False
    win.set_tool("rectangle")
    drag(cv, (100, 100), (300, 200))
    assert len(ctrl.sheet.vectors) == 1
    bb = ctrl.sheet.vectors[0].bbox()
    assert bb.width == pytest.approx(200, abs=2) and bb.height == pytest.approx(100, abs=2)
    win.set_tool("circle")
    drag(cv, (450, 150), (480, 150))
    c = ctrl.sheet.vectors[-1]
    assert c.contours[0].closed and c.bbox().width == pytest.approx(60, abs=2)
    win.set_tool("polyline")
    for x, y in ((50, 300), (150, 350), (250, 300)):
        QTest.mouseClick(cv, Qt.LeftButton, Qt.NoModifier, at(cv, x, y))
    QTest.keyClick(cv, Qt.Key_Return)
    pl = ctrl.sheet.vectors[-1]
    assert not pl.closed and len(pl.contours[0].segs) == 2
    # bezier by dragging
    drag(cv, (50, 50), (50, 50))
    drag(cv, (150, 50), (200, 80))
    QTest.keyClick(cv, Qt.Key_Return)
    assert isinstance(ctrl.sheet.vectors[-1].contours[0].segs[0], Bezier)
    assert len(ctrl.sheet.vectors) == 4
    ctrl.undo()
    assert len(ctrl.sheet.vectors) == 3
    ctrl.redo()
    assert len(ctrl.sheet.vectors) == 4


def test_select_move_and_band(win):
    cv, ctrl = win.canvas, win.ctrl
    ctrl.settings.snap_objects = False
    ctrl.add_vectors([VectorObject([shapes.rectangle(100, 100, 50, 50)], ctrl.layer_id),
                      VectorObject([shapes.rectangle(300, 100, 50, 50)], ctrl.layer_id)])
    ctrl.select([])
    QTest.mouseClick(cv, Qt.LeftButton, Qt.NoModifier, at(cv, 100, 135))
    assert len(ctrl.selection) == 1
    drag(cv, (100, 135), (140, 175))  # grab the edge away from the scale handles
    o = ctrl.sheet.vector(ctrl.selection[0])
    assert o.bbox().xmin == pytest.approx(140, abs=2) and o.bbox().ymin == pytest.approx(140, abs=2)
    drag(cv, (50, 50), (500, 300))  # window selection
    assert len(ctrl.selection) == 2
    QTest.keyClick(cv, Qt.Key_Delete)
    assert not ctrl.sheet.vectors


def test_scale_and_rotate_handles(win):
    cv, ctrl = win.canvas, win.ctrl
    ctrl.settings.snap_objects = False
    ctrl.add_vectors([VectorObject([shapes.rectangle(100, 100, 100, 50)], ctrl.layer_id)])
    o = ctrl.sheet.vectors[0]
    drag(cv, (200, 150), (300, 200))  # NE corner, proportional
    bb = o.bbox()
    assert bb.width == pytest.approx(200, abs=3) and bb.xmin == pytest.approx(100, abs=1)
    handles = cv.tool._handles()
    rot = handles["rot"]
    cx, cy = o.bbox().center
    pa = rot.toPoint()
    pb = at(cv, cx + 200, cy)  # drag the rotation handle to the right of the centre: -90 degrees
    QTest.mousePress(cv, Qt.LeftButton, Qt.ShiftModifier, pa)
    QTest.mouseMove(cv, pb)
    QTest.mouseRelease(cv, Qt.LeftButton, Qt.ShiftModifier, pb)
    bb2 = o.bbox()  # QTest moves carry no Shift, so the angle is only about 90 degrees
    assert bb2.width == pytest.approx(bb.height, rel=0.03) and bb2.height == pytest.approx(bb.width, rel=0.03)


def test_node_editing(win):
    cv, ctrl = win.canvas, win.ctrl
    ctrl.settings.snap_objects = False
    ctrl.add_vectors([VectorObject([shapes.rectangle(100, 100, 100, 100)], ctrl.layer_id)])
    o = ctrl.sheet.vectors[0]
    win.set_tool("nodes")
    drag(cv, (200, 200), (250, 250))
    assert o.contours[0].bbox().xmax == pytest.approx(250, abs=2)
    QTest.mouseDClick(cv, Qt.LeftButton, Qt.NoModifier, at(cv, 150, 100))
    assert len(o.contours[0].segs) == 5
    QTest.mouseClick(cv, Qt.LeftButton, Qt.NoModifier, at(cv, 150, 100))
    QTest.keyClick(cv, Qt.Key_Delete)
    assert len(o.contours[0].segs) == 4
    ctrl.undo()
    assert len(ctrl.sheet.vectors[0].contours[0].segs) == 5


def test_commands_toolpaths_export_and_simulation(win, tmp_path):
    from iyicam.gui.dialogs import ExportDialog, ToolpathDialog
    ctrl = win.ctrl
    reg = ctrl.registry
    ctrl.run_command(reg.commands["create.rectangle"], {"width": 200, "height": 100, "radius": 10,
                                                        "x": 300, "y": 200})
    ctrl.run_command(reg.commands["create.circle"], {"diameter": 30, "x": 260, "y": 200})
    ctrl.select([v.id for v in ctrl.sheet.vectors])
    dlg = ToolpathDialog(ctrl, "profile", None, win)
    dlg.form.widgets["tabs"].setChecked(True)
    t = dlg.result()
    assert t.params["tabs"] and t.selection.vector_ids
    ctrl.add_toolpath(t)
    assert ctrl.sheet_toolpaths()
    ctrl.select([ctrl.sheet.vectors[1].id])
    dlg = ToolpathDialog(ctrl, "pocket", None, win)
    ctrl.add_toolpath(dlg.result())
    assert len(ctrl.sheet_toolpaths()) == 2
    win.toolpaths_panel.refresh()
    assert win.toolpaths_panel.tree.topLevelItemCount() == 2
    ex = ExportDialog(ctrl, win)
    for i in range(ex.post.count()):
        ex.post.setCurrentIndex(i)
        text = ex.text.toPlainText()
        assert ("G1" in text or "G01" in text) and "error" not in text.lower()
    from iyicam.post.export import write_gcode
    files = write_gcode(tmp_path / "job", ctrl.project, ctrl.sheet, ctrl.sheet_toolpaths(), reg.posts["mach3"])
    assert files[0].read_text().count("G2") + files[0].read_text().count("G3") > 0
    ctrl.settings.sim_resolution = 1.0
    win.simulate()
    assert win.canvas.sim_image is not None and win.canvas.show_sim
    win.canvas.grab()  # paint everything once
    html = __import__("iyicam.gui.main_window", fromlist=["job_report_html"]).job_report_html(ctrl)
    assert "Total" in html
    # moving geometry marks toolpaths stale; recalculation clears it
    ctrl.select([ctrl.sheet.vectors[0].id])
    ctrl.run_command(reg.commands["xform.move"], {"dx": 10, "dy": 0})
    assert ctrl.stale
    ctrl.recalculate()
    assert not ctrl.stale


def test_nesting_dialog(win):
    from iyicam.gui.dialogs import NestingDialog
    ctrl = win.ctrl
    objs = [VectorObject([shapes.rectangle(10 + 120 * i, 10, 100, 60)], ctrl.layer_id) for i in range(3)]
    ctrl.add_vectors(objs)
    dlg = NestingDialog(ctrl, win)
    dlg.qty[0].setValue(5)
    out = dlg.run()
    assert out.placed == 7 and out.unplaced == 0
    assert sum(len(s.vectors) for s in ctrl.project.sheets) == 7
    ctrl.undo()
    assert sum(len(s.vectors) for s in ctrl.project.sheets) == 3


def test_text_and_import(win, tmp_path):
    from iyicam.formats.dxf import export_dxf
    from iyicam.gui.qtgeom import text_contours
    cs = text_contours("Ob", "DejaVu Sans", 20.0)
    assert len(cs) >= 4  # O (outer + inner), b (outer + inner)
    outer = max(cs, key=lambda c: c.area())
    assert outer.bbox().height == pytest.approx(20, rel=0.1)
    ctrl = win.ctrl
    p2 = type(ctrl.project)()
    p2.sheets[0].vectors.append(VectorObject([shapes.circle((50, 50), 20)], p2.layers[0].id))
    f = tmp_path / "c.dxf"
    export_dxf(f, p2, p2.sheets[0])
    objs = win.import_vectors(str(f), {"units": "1", "join": 0.05, "keep_layers": True, "center": True})
    assert len(objs) == 1
    assert objs[0].bbox().center == pytest.approx((ctrl.sheet.width / 2, ctrl.sheet.height / 2))


def test_save_open_roundtrip_and_layers(win, tmp_path):
    ctrl = win.ctrl
    ctrl.run_command(ctrl.registry.commands["create.star"], {})
    with ctrl.edit("layer"):
        l = ctrl.project.add_layer("Cut")
    ctrl.sheet.vectors[0].layer_id = l.id
    f = tmp_path / "p.icam"
    ctrl.save_project(f)
    ctrl.new_project()
    assert not ctrl.sheet.vectors
    ctrl.open_project(f)
    assert ctrl.project.layer_by_name("Cut") and len(ctrl.sheet.vectors) == 1
    win.layers_panel.refresh()
    assert win.layers_panel.tree.topLevelItemCount() == 2


def test_console_runs_code(win):
    con = win.console
    con.inp.setText("add(shapes.circle((10, 10), 5))")
    con.execute()
    assert len(win.ctrl.sheet.vectors) == 1
    con.inp.setText("1/0")
    con.execute()
    assert "ZeroDivisionError" in con.out.toPlainText()


def test_trim_tool(win):
    cv, ctrl = win.canvas, win.ctrl
    ctrl.add_vectors([VectorObject([shapes.line((100, 100), (300, 100))], ctrl.layer_id),
                      VectorObject([shapes.line((200, 50), (200, 150))], ctrl.layer_id)])
    win.set_tool("trim")
    QTest.mouseClick(cv, Qt.LeftButton, Qt.NoModifier, at(cv, 250, 100))
    h = ctrl.sheet.vectors[0]
    assert h.bbox().xmax == pytest.approx(200, abs=1e-3)
    ctrl.undo()
    assert ctrl.sheet.vectors[0].bbox().xmax == pytest.approx(300)


def test_trace_bitmap(win, tmp_path):
    from PySide6.QtGui import QColor, QImage, QPainter
    img = QImage(200, 100, QImage.Format_ARGB32)
    img.fill(QColor(0, 0, 0, 0))  # transparent background counts as white
    p = QPainter(img)
    p.setRenderHint(QPainter.Antialiasing)
    p.setBrush(QColor("black"))
    p.drawEllipse(50, 0, 100, 100)
    p.end()
    f = tmp_path / "logo.png"
    img.save(str(f))
    objs = win.trace_bitmap(str(f), {"width": 100.0, "threshold": 128, "invert": False, "smoothing": 0.6,
                                     "min_area": 8, "center": True})
    assert len(objs) == 1 and len(objs[0].contours) == 1
    assert objs[0].contours[0].area() == pytest.approx(math.pi * 25 ** 2, rel=0.03)


def test_edit_tabs_tool(win):
    from iyicam.core.document import Selection, ToolpathDef
    cv, ctrl = win.canvas, win.ctrl
    obj = VectorObject([shapes.rectangle(100, 100, 200, 100)], ctrl.layer_id)
    ctrl.add_vectors([obj])
    t = ToolpathDef("p", "profile", ctrl.tools.tools[1].copy(), {"tabs": True, "tab_count": 4},
                    Selection("vectors", [obj.id]))
    ctrl.add_toolpath(t)
    win.edit_tabs(t.id)
    pts = ctrl.project.toolpath(t.id).params["tab_points"]
    assert len(pts) == 4 and ctrl.project.toolpath(t.id).params["tab_mode"] == "manual"
    QTest.mouseClick(cv, Qt.LeftButton, Qt.NoModifier, at(cv, 110, 100))  # add on the bottom edge
    assert len(ctrl.project.toolpath(t.id).params["tab_points"]) == 5
    first = ctrl.project.toolpath(t.id).params["tab_points"][0]
    QTest.mouseClick(cv, Qt.LeftButton, Qt.NoModifier, at(cv, *first))  # remove
    assert len(ctrl.project.toolpath(t.id).params["tab_points"]) == 4
    assert ctrl.sheet_toolpaths()

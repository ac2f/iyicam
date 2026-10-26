"""Offscreen GUI smoke tests: drive the main window like a user would."""
import math

import pytest

pytest.importorskip("PySide6.QtWidgets")

from PySide6.QtCore import QPoint, Qt  # noqa: E402
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
    ex.tile.setChecked(True)  # 100 mm tiles along Y: the rectangle (y 150..250) spans two of them
    ex.tile_len.setValue(ctrl.units.to_display(100.0))
    assert ex.text.toPlainText().count("_tile") >= 2
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


L_PLATE = [(0, 0), (500, 0), (500, 200), (250, 200), (250, 400), (0, 400)]


def test_stock_dialog_and_nesting_onto_a_leftover(win, tmp_path):
    from iyicam.core.stock import StockItem
    from iyicam.gui.dialogs import NestingDialog, StockDialog
    ctrl = win.ctrl
    # a leftover drawn as an outline with a hole, entered from the selected vectors
    outline = VectorObject([shapes.polyline([(1000 + x, 1000 + y) for x, y in L_PLATE], True)], ctrl.layer_id)
    hole = VectorObject([shapes.circle((1100, 1100), 30)], ctrl.layer_id)
    ctrl.add_vectors([outline, hole])
    ctrl.select([outline.id, hole.id])
    dlg = StockDialog(ctrl, win)
    dlg._from_vectors()
    assert len(dlg.items) == 1 and len(dlg.items[0].outline) == 2 and dlg.items[0].quantity == 1
    dlg.items.append(StockItem("Offcut", 700, 300, None, 2, True))
    dlg._build()
    dlg.table.qty[0].setValue(1)
    dlg.accept()
    assert [(i.name, i.quantity) for i in ctrl.project.stock][1:] == [("Offcut", 2)]
    ctrl.undo()
    assert ctrl.project.stock == []
    ctrl.redo()
    assert len(ctrl.project.stock) == 2
    # parts to nest onto the plates of the stock only
    ctrl.select([])
    ctrl.project.sheets[0].vectors = []
    ctrl.add_vectors([VectorObject([shapes.rectangle(0, 0, 120, 80)], ctrl.layer_id)])
    nd = NestingDialog(ctrl, win)
    nd.form.widgets["target"].setCurrentIndex(nd.form.widgets["target"].findData("new"))
    nd.form.widgets["new_sheets"].setChecked(False)
    nd.qty[0].setValue(14)
    out = nd.run()
    # the plates are used smallest first: the shaped leftover, then the two offcuts; what does not fit is reported
    assert out.placed + out.unplaced == 14 and out.placed > 8
    assert out.sheets[0].outline and out.sheets[0].name.startswith("Leftover")
    assert [sh.name for sh in out.sheets[1:]] == [f"Offcut {k + 1}" for k in range(len(out.sheets) - 1)]
    shaped = [sh for sh in out.sheets if sh.outline]
    assert len(shaped) == 1 and shaped[0].width == pytest.approx(500) and shaped[0].height == pytest.approx(400)
    # the shaped leftover (one only) is used up and leaves the stock, the offcuts are counted down
    used_offcuts = len(out.sheets) - 1
    left = {i.name: i.quantity for i in ctrl.project.stock}
    assert left == ({"Offcut": 2 - used_offcuts} if used_offcuts < 2 else {})
    ctrl.undo()
    assert len(ctrl.project.stock) == 2 and [i.quantity for i in ctrl.project.stock][1] == 2
    ctrl.redo()
    # the canvas draws a leftover and the simulator takes the outline
    ctrl.set_sheet(shaped[0].id)
    win.canvas.zoom_extents()
    QApplication.processEvents()
    assert not win.canvas.grab().isNull()
    # saving and loading keeps the plates
    ctrl.project.save(tmp_path / "x.icam")
    from iyicam.core.document import Project
    assert [sh.outline is not None for sh in Project.load(tmp_path / "x.icam").sheets].count(True) == len(shaped)


def test_leftover_of_a_sheet_goes_to_the_stock_and_the_outline_is_set_from_vectors(win):
    ctrl = win.ctrl
    ctrl.project.sheets[0].width, ctrl.project.sheets[0].height = 600, 400
    parts = [VectorObject([shapes.rectangle(20, 20, 250, 360)], ctrl.layer_id)]
    ctrl.add_vectors(parts, select=False)
    items = ctrl.leftover_to_stock(ctrl.sheet, 3.0, 30.0, 2500.0)
    assert len(items) == 1 and items[0].leftover
    assert items[0].area == pytest.approx(600 * 400 - 256 * 366 - 0, rel=0.4) and ctrl.project.stock == items
    ctrl.undo()
    assert ctrl.project.stock == []
    # a drawn outline makes the sheet a plate of that shape; everything on it moves with it
    ctrl.project.sheets[0].vectors = []
    mark = VectorObject([shapes.circle((1200, 1100), 10)], ctrl.layer_id)
    outline = VectorObject([shapes.polyline([(1000 + x, 1000 + y) for x, y in L_PLATE], True)], ctrl.layer_id)
    ctrl.add_vectors([mark, outline], select=False)
    ctrl.select([outline.id])
    assert ctrl.set_sheet_outline_from_selection()
    sh = ctrl.sheet
    assert (sh.width, sh.height) == pytest.approx((500, 400)) and sh.outline and sh.vector(outline.id) is None
    assert mark.bbox().center == pytest.approx((200, 100))  # moved with the sheet
    ctrl.undo()
    assert ctrl.sheet.outline is None and ctrl.sheet.vector(outline.id) is not None
    ctrl.redo()
    ctrl.set_sheet_outline(None)
    assert ctrl.sheet.outline is None
    ctrl.select([])
    assert not ctrl.set_sheet_outline_from_selection()  # nothing selected


def _bars(ctrl, n=2, length=300):
    """Bars that fit a 100 x 400 strip of the stock only turned, one per layer; the strip is the only plate."""
    from iyicam.core.stock import StockItem
    ctrl.project.sheets[0].vectors = []
    second = ctrl.project.add_layer("Grain")
    objs = [VectorObject([shapes.rectangle(0, 120 * i, length, 20)], ctrl.layer_id if i == 0 else second.id)
            for i in range(n)]
    ctrl.add_vectors(objs)
    ctrl.project.stock = [StockItem("strip", 100, 400, None, 1, True)]
    return objs


def _nest_dialog(win, **values):
    from iyicam.gui.dialogs import NestingDialog
    nd = NestingDialog(win.ctrl, win)
    w = nd.form.widgets
    w["target"].setCurrentIndex(w["target"].findData("new"))
    w["new_sheets"].setChecked(False)
    w["use_up"].setChecked(False)
    w["margin"].setValue(win.ctrl.units.to_display(5.0))
    w["spacing"].setValue(win.ctrl.units.to_display(4.0))
    for k, v in values.items():
        wd = w[k]
        if hasattr(wd, "findData"):
            wd.setCurrentIndex(wd.findData(v))
        elif hasattr(wd, "setText"):
            wd.setText(v)
        else:
            wd.setValue(v)
    return nd


def test_nesting_dialog_free_angles_and_rotation_rules(win, monkeypatch):
    from PySide6.QtWidgets import QMessageBox
    ctrl = win.ctrl
    objs = _bars(ctrl)
    # a list of any angles: 0 and 45 do not let a 300 mm bar into the strip, 0 and 90 do
    nd = _nest_dialog(win, rotation="list", angles="0, 45")
    assert nd.rotations(nd.form.values()) == [0.0, 45.0]
    out = nd.run()
    assert out.placed == 0 and out.unplaced == 2
    ctrl.undo()
    out = _nest_dialog(win, rotation="list", angles="0, 90").run()
    assert out.placed == 2
    ctrl.undo()
    # own step: 30 degrees (90 is a multiple)
    nd = _nest_dialog(win, rotation="custom", rotation_step=30.0)
    assert nd.rotations(nd.form.values()) == [float(a) for a in range(0, 360, 30)]
    assert nd.run().placed == 2
    ctrl.undo()
    # a bad list is refused before the dialog closes
    warned = []
    monkeypatch.setattr(QMessageBox, "warning", lambda *a, **k: warned.append(a[2]))
    nd = _nest_dialog(win, rotation="list", angles="0, ninety")
    nd.accept()
    assert warned and nd.result() == 0
    # rules by layer: "Grain" never rotates
    nd = _nest_dialog(win)
    names = [nd.layer_table.item(r, 0).text() for r in range(nd.layer_table.rowCount())]
    nd.layer_rules[names.index("Grain")].set_rule({"mode": "none"})
    out = nd.run()
    assert out.placed == 1 and out.unplaced == 1
    saved = ctrl.project.metadata["nesting"]
    assert list(saved["layer_rules"].values()) == [{"mode": "none", "value": pytest.approx(10.0)}]
    ctrl.undo()
    # ... which the dialog remembers, and a part's own rule beats its layer
    nd = _nest_dialog(win)
    assert nd.layer_rules[names.index("Grain")].rule()["mode"] == "none"
    nd.rules[1].set_rule({"mode": "default"})
    assert nd.run().placed == 2
    ctrl.undo()
    # rules for several selected parts at once
    nd = _nest_dialog(win)
    nd.table.selectAll()
    nd.bulk.set_rule({"mode": "limit", "value": 15.0})
    nd._apply_bulk()
    assert [r.rule()["mode"] for r in nd.rules] == ["limit", "limit"]
    assert nd.run().placed == 0  # 15 degrees does not get a bar into the strip
    ctrl.undo()
    assert objs[0].id in {p.root.id for p in nd.parts}


def test_nesting_dialog_squeezing_in_asks_applies_or_is_off(win):
    from iyicam.core.stock import StockItem
    ctrl = win.ctrl
    ctrl.project.sheets[0].vectors = []
    ctrl.add_vectors([VectorObject([shapes.rectangle(0, 0, 280, 100)], ctrl.layer_id)])
    ctrl.project.stock = [StockItem("plate", 300, 200, None, 1, True)]
    asked = []

    def run(answer, **values):
        nd = _nest_dialog(win, margin=10.0, spacing=8.0, rotation="90", **values)
        nd._ask_squeeze = lambda text: (asked.append(text), answer)[1]
        out = nd.run()
        ctrl.undo()
        return out

    # off: it does not fit and nothing is said
    out = run(True)
    assert out.unplaced == 1 and not asked
    # suggest: the parts that would fit are named, the answer decides
    out = run(False, squeeze="ask", squeeze_margin=4.0, squeeze_angle=0.0)
    assert out.unplaced == 1 and len(asked) == 1
    assert "place 1 part(s) that do not fit" in asked[0] and "1 mm closer to the edge" in asked[0]
    out = run(True, squeeze="ask", squeeze_margin=4.0, squeeze_angle=0.0)
    assert out.unplaced == 0 and out.placed == 1 and len(asked) == 2
    assert [q.relief for q in out.squeezed] == [pytest.approx(1.0)]
    # automatic: it is done without asking
    out = run(False, squeeze="auto", squeeze_margin=4.0, squeeze_angle=0.0)
    assert out.unplaced == 0 and len(asked) == 2
    # the limits are only offered while squeezing is on
    nd = _nest_dialog(win)
    nd.form.widgets["squeeze"].setCurrentIndex(nd.form.widgets["squeeze"].findData("ask"))
    assert nd.form.widgets["squeeze_angle"].isEnabled()


def _wobbly_parts(ctrl, n, points=200):
    from iyicam.core.geom import Transform
    objs = []
    for i in range(n):
        rx, ry = 300.0 + 12 * i, 200.0 + 9 * i
        pts = [(rx + rx * math.cos(2 * math.pi * k / points) * (1 + 0.1 * math.sin(5 * 6.28 * k / points + i)),
                ry + ry * math.sin(2 * math.pi * k / points) * (1 + 0.08 * math.cos(3 * 6.28 * k / points + 2 * i)))
               for k in range(points)]
        o = VectorObject([shapes.polyline(pts, True)], ctrl.layer_id)
        o.transform(Transform.translate(700 * (i % 4), 500 * (i // 4)))
        objs.append(o)
    ctrl.add_vectors(objs)
    return objs


def _positions(ctrl):
    """Ids and positions of the vectors of the first sheet, for comparing (positions only roughly: they
    go through saving when an action is undone)."""
    vs = ctrl.project.sheets[0].vectors
    return [o.id for o in vs], [c for o in vs for c in (o.bbox().xmin, o.bbox().ymin)]


def _same_positions(ctrl, before) -> bool:
    ids, pos = _positions(ctrl)
    return ids == before[0] and pos == pytest.approx(before[1])


def test_nesting_does_not_freeze_the_program_and_cancel_changes_nothing(win, monkeypatch):
    import time

    from PySide6.QtCore import QTimer
    from PySide6.QtWidgets import QProgressDialog
    ctrl = win.ctrl
    ctrl.project.sheets[0].vectors = []
    _wobbly_parts(ctrl, 9)
    ctrl.project.stock = []
    before = _positions(ctrl)
    undo_before = len(ctrl.undo_stack._undo)

    def dialog():
        nd = _nest_dialog(win, max_points=0)  # slow on purpose: the real outlines
        w = nd.form.widgets
        w["target"].setCurrentIndex(w["target"].findData("new"))
        w["new_sheets"].setChecked(True)
        w["width"].setValue(ctrl.units.to_display(2800.0))
        w["height"].setValue(ctrl.units.to_display(2100.0))
        return nd

    # the program keeps running while it nests: a timer fires all the time
    ticks = []
    timer = QTimer()
    timer.setInterval(10)
    timer.timeout.connect(lambda: ticks.append(1))
    timer.start()
    out = dialog().run()
    timer.stop()
    assert out is not None and out.placed == 9
    assert len(ticks) >= 5, "the interface did not get a chance to run during the nesting"
    ctrl.undo()
    assert _same_positions(ctrl, before) and len(ctrl.undo_stack._undo) == undo_before
    # cancelling: nothing changes, no undo step is left, and it ends quickly
    monkeypatch.setattr(QProgressDialog, "wasCanceled", lambda self: True)
    t = time.time()
    assert dialog().run() is None
    assert time.time() - t < 3.0
    assert _same_positions(ctrl, before) and len(ctrl.undo_stack._undo) == undo_before
    assert len(ctrl.project.sheets) == 1


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
    from PySide6.QtGui import QColor, QImage, QPainter, QPen
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
    p = QPainter(img)
    p.setPen(QPen(QColor("black"), 10))
    p.drawLine(10, 50, 190, 50)
    p.end()
    img.save(str(f))
    line = win.trace_bitmap(str(f), {"width": 100.0, "mode": "centerline", "center": False})
    longest = max((c for o in line for c in o.contours), key=lambda c: c.length())
    assert not longest.closed and longest.length() > 80


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


def test_open_gcode_backplot(win, tmp_path):
    f = tmp_path / "x.nc"
    f.write_text("G21 G90\nT1 M6\nG0 X10 Y10 Z5\nG1 Z-2 F500\nG2 X30 Y10 I10 J0\nG1 X30 Y40\nG0 Z5\nM30\n")
    tps = win.open_gcode(str(f))
    assert len(tps) == 1 and tps[0].stats().feed_length > 40
    win.canvas.grab()
    win.ctrl.settings.sim_resolution = 1.0
    win.simulate()
    assert win.canvas.sim_image is not None
    win.clear_backplot()
    assert not win.ctrl.backplot


def test_feeds_dialog_applies_to_tool(win):
    from iyicam.gui.dialogs import FeedsDialog
    t = win.ctrl.tools.tools[1]
    dlg = FeedsDialog(win.ctrl, t, win)
    new = dlg.apply_to(t)
    assert new.feed_rate > 0 and new.id == t.id
    assert "Chip load" in dlg.result_label.text()


def test_text_on_curve(win):
    ctrl = win.ctrl
    circ = VectorObject([shapes.circle((300, 200), 100)], ctrl.layer_id)
    ctrl.add_vectors([circ])
    obj = win.text_on_curve({"text": "ABC", "font": "DejaVu Sans", "height": 10.0, "spacing": 1.0,
                             "offset": 0.0, "align": "center"})
    assert obj is not None and len(obj.contours) >= 4  # A (2), B (3), C (1)
    for c in obj.contours:
        for x, y in c.flatten(0.1):
            assert 88 < math.hypot(x - 300, y - 200) < 101  # letters stand on the circle, inside it


def test_rulers_and_guides(win):
    cv, ctrl = win.canvas, win.ctrl
    ctrl.settings.snap_grid = False
    start = QPoint(at(cv, 200, 0).x(), cv.RULER // 2)
    end = at(cv, 200, 150)
    QTest.mousePress(cv, Qt.LeftButton, Qt.NoModifier, start)
    QTest.mouseMove(cv, end)
    QTest.mouseRelease(cv, Qt.LeftButton, Qt.NoModifier, end)
    gs = cv.guides()
    assert len(gs) == 1 and gs[0][0] == "h" and gs[0][1] == pytest.approx(150, abs=1)
    y = gs[0][1]
    assert cv.snap((123.0, y + cv.px(3))) == pytest.approx((123.0, y))
    cv.grab()  # rulers and guides paint
    # pick it up on the left ruler and drop it back onto the ruler: deleted
    on_left = QPoint(cv.RULER // 2, int(cv.world_to_screen(0, y).y()))
    QTest.mousePress(cv, Qt.LeftButton, Qt.NoModifier, on_left)
    QTest.mouseRelease(cv, Qt.LeftButton, Qt.NoModifier, on_left)
    assert not cv.guides()
    ctrl.undo()
    assert len(cv.guides()) == 1
    ctrl.undo()
    assert not cv.guides()


def _parts_with_profile(win, n=3):
    from iyicam.core.document import Selection, ToolpathDef
    from iyicam.core.tooling import Tool
    ctrl = win.ctrl
    objs = [VectorObject([shapes.rectangle(50 + i * 120, 100, 100, 80)], ctrl.layer_id, f"P{i}") for i in range(n)]
    ctrl.add_vectors(objs)
    t = ToolpathDef("Cut out", "profile", Tool("EM6", "end_mill", 6.0, 1, stepdown=6.0),
                    {"through": True}, Selection("vectors", [o.id for o in objs]))
    ctrl.add_toolpath(t)
    win.canvas.zoom_extents()
    QApplication.processEvents()
    return ctrl, t, objs


def test_cut_order_dialog_and_picking_the_order(win):
    from iyicam.gui.sequence_dialogs import CutOrderDialog
    ctrl, t, objs = _parts_with_profile(win)
    keys = [f"{o.id}:0" for o in objs]
    dlg = CutOrderDialog(ctrl, t.id, win)
    dlg.strategy.setCurrentIndex(dlg.strategy.findData("rows_br"))  # from the right
    assert ctrl.cut_order(t.id) == keys[::-1]
    assert dlg.list.count() == 3 and dlg.list.item(0).text().endswith("P2")
    dlg.list.setCurrentRow(2)
    dlg._move(-1)
    assert ctrl.cut_order(t.id) == [keys[2], keys[0], keys[1]] and t.sequence["auto_order"] is False
    # pick the order on the drawing from #1: click inside P1 then P2
    dlg.start_at.setValue(1)
    dlg._pick()
    cv = win.canvas
    for o in (objs[1], objs[2]):
        QTest.mouseClick(cv, Qt.LeftButton, Qt.NoModifier, at(cv, *o.bbox().center))
    QTest.keyClick(cv, Qt.Key_Return)
    assert ctrl.cut_order(t.id) == [keys[1], keys[2], keys[0]]
    assert [c["key"] for c in ctrl.sheet_toolpaths()[0].cuts] == [keys[1], keys[2], keys[0]]
    ctrl.undo()
    ctrl.recalculate()
    assert ctrl.cut_order(t.id) == [keys[2], keys[0], keys[1]]
    win.canvas.grab()  # paints the cut numbers


def test_start_points_dialog_and_tool(win):
    from iyicam.gui.sequence_dialogs import StartPointsDialog
    ctrl, t, objs = _parts_with_profile(win)
    starts = [o.contours[0].start for o in objs]
    dlg = StartPointsDialog(ctrl, t.id, win)
    dlg._now()  # optimise once: new nodes away from the corners, undoable
    moved = [o.contours[0].start for o in objs]
    assert all(m != s for m, s in zip(moved, starts))
    assert all(len(o.contours[0].segs) == 5 for o in objs)
    ctrl.undo()
    assert [o.contours[0].start for o in ctrl.sheet.vectors] == starts
    # the start point tool puts a start (and a node) where you click and keeps it there
    win.set_tool("start")
    cv = win.canvas
    o = ctrl.sheet.vectors[0]
    QTest.mouseClick(cv, Qt.LeftButton, Qt.NoModifier, at(cv, 50, 150))
    assert o.contours[0].start == pytest.approx((50, 150), abs=0.5) and o.locked_starts == [0]
    dlg.smart.setChecked(True)  # automatic: the other start points move, the hand-set one stays
    assert o.contours[0].start == pytest.approx((50, 150), abs=0.5)
    cut = next(c for c in ctrl.sheet_toolpaths()[0].cuts if c["key"] == f"{o.id}:0")
    assert cut["start"] == pytest.approx((47, 150), abs=0.5)
    QTest.mouseClick(cv, Qt.RightButton, Qt.NoModifier, at(cv, 50, 150))
    assert o.locked_starts == []


def test_cutting_direction_per_strategy(win):
    from iyicam.gui.sequence_dialogs import CutOrderDialog, DirectionsDialog
    ctrl, t, objs = _parts_with_profile(win)
    dlg = CutOrderDialog(ctrl, t.id, win)
    dlg.strategy.setCurrentIndex(dlg.strategy.findData("rows_br"))
    dlg.direction.setCurrentIndex(dlg.direction.findData("conventional"))
    dlg.strategy.setCurrentIndex(dlg.strategy.findData("rows_bl"))
    assert dlg.direction.currentData() == "toolpath"  # rows_bl keeps its own setting
    dlg.direction.setCurrentIndex(dlg.direction.findData("climb"))
    assert t.sequence["directions"] == {"rows_br": "conventional", "rows_bl": "climb"}
    cuts = ctrl.sheet_toolpaths()[0].cuts
    assert all(not c["loop"].is_ccw() for c in cuts)  # climb outside: clockwise
    dlg.strategy.setCurrentIndex(dlg.strategy.findData("rows_br"))
    assert dlg.direction.currentData() == "conventional"
    assert all(c["loop"].is_ccw() for c in ctrl.sheet_toolpaths()[0].cuts)
    table = DirectionsDialog(ctrl, t.sequence, win)
    table.combos["diag_tr"].setCurrentIndex(table.combos["diag_tr"].findData("cw"))
    assert table.directions()["diag_tr"] == "cw" and table.directions()["rows_br"] == "conventional"
    # defaults for new toolpaths
    from iyicam.core.document import Selection, ToolpathDef
    ctrl.settings.strategy_directions = table.directions()
    t2 = ToolpathDef("Second", "profile", t.tool, {"through": True}, Selection("vectors", [objs[0].id]))
    ctrl.add_toolpath(t2)
    assert t2.sequence["directions"]["diag_tr"] == "cw"

"""What the program remembers: dialog values, last folders, defaults of new projects and toolpaths.

Every test closes the window and opens a new one (a new session) to see that the values really went to the
settings file and come back from it.
"""
import json

import pytest

pytest.importorskip("PySide6.QtWidgets")

from gui_robot import Robot, process, release  # noqa: E402

from iyicam.core import shapes  # noqa: E402
from iyicam.core.document import VectorObject  # noqa: E402
from iyicam.core.params import Param  # noqa: E402
from iyicam.gui.forms import ParamForm  # noqa: E402
from iyicam.gui.settings import Settings  # noqa: E402


class Session:
    """A window; ``restart`` closes it and opens a new one on the same settings file."""

    def __init__(self, mp):
        self.mp = mp
        self.robot = Robot().install(mp)
        self.win = None
        self.restart()

    def restart(self, resize=True):
        if self.win is not None:
            for c in self.win.documents():
                c.dirty = False
            self.win.close()
            release(self.win)
        from iyicam.gui.app import create_window
        app, self.win = create_window(["test"], load_plugins=False)
        if resize:
            self.win.resize(1200, 800)
        self.win.show()
        process()
        self.robot.reset()
        return self.win

    @property
    def ctrl(self):
        return self.win.ctrl

    def close(self):
        for c in self.win.documents():
            c.dirty = False
        self.win.close()
        release(self.win)


@pytest.fixture
def sess(tmp_path, monkeypatch):
    monkeypatch.setenv("IYICAM_HOME", str(tmp_path / "home"))
    s = Session(monkeypatch)
    yield s
    s.close()


def set_form(dlg, **values):
    """Set the widgets of the ParamForm in a dialog by parameter name."""
    form = dlg.findChild(ParamForm) if not hasattr(dlg, "form") else dlg.form
    for k, v in values.items():
        w = form.widgets[k]
        if hasattr(w, "findData"):
            w.setCurrentIndex(w.findData(v))
        elif hasattr(w, "setChecked"):
            w.setChecked(v)
        elif hasattr(w, "setText"):
            w.setText(str(v))
        else:
            w.setValue(v)
    return form


# -- the settings file ------------------------------------------------------------------------------------------
def test_values_come_back_in_range_and_of_the_right_kind(tmp_path, monkeypatch):
    monkeypatch.setenv("IYICAM_HOME", str(tmp_path / "home"))
    params = [Param("depth", "Depth", "length", 5.0, min=1.0, max=50.0), Param("n", "N", "int", 3, min=1, max=9),
              Param("mode", "Mode", "choice", "a", choices=[("a", "A"), ("b", "B")]),
              Param("on", "On", "bool", False), Param("name", "Name", "str", "x"),
              Param("at", "At", "point", (0.0, 0.0)), Param("tool", "Tool", "tool", None),
              Param("opt", "Opt", "float", None)]
    s = Settings()
    s.remember("k", {"depth": 7.5, "n": 4, "mode": "b", "on": True, "name": "hello", "at": (1.0, 2.0),
                     "tool": object(), "opt": None, "gone": 1})
    back = Settings.load().recall("k", params)
    assert back == {"depth": 7.5, "n": 4, "mode": "b", "on": True, "name": "hello", "at": (1.0, 2.0), "opt": None}
    # a hand-edited file: wrong kinds, out of range, an unknown choice
    f = tmp_path / "home" / "settings.json"
    data = json.loads(f.read_text())
    data["memory"]["k"].update({"depth": 1e9, "n": "many", "mode": "zzz", "on": "yes", "at": "far"})
    f.write_text(json.dumps(data))
    odd = Settings.load().recall("k", params)
    assert odd["depth"] == 50.0 and odd["mode"] == "a" and odd["on"] is True and "n" not in odd and "at" not in odd


def test_a_damaged_settings_file_gives_defaults_and_is_mended_by_the_next_save(tmp_path, monkeypatch):
    monkeypatch.setenv("IYICAM_HOME", str(tmp_path / "home"))
    f = tmp_path / "home" / "settings.json"
    f.parent.mkdir(parents=True)
    for text in ("{not json", "[1, 2]", '{"grid": "wide", "memory": 5, "dirs": [], "units": 3, "recent_files": "x"}',
                 '{"grid": 25, "snap_grid": true, "units": "inch"}'):
        f.write_text(text)
        s = Settings.load()
        assert isinstance(s.memory, dict) and isinstance(s.dirs, dict) and isinstance(s.recent_files, list)
        assert s.units in ("mm", "inch") and isinstance(s.grid, float)
    assert s.grid == 25 and s.snap_grid is True and s.units == "inch"
    s.save()
    assert json.loads(f.read_text())["grid"] == 25
    assert not list(f.parent.glob("*.tmp"))          # the file was replaced in one piece


def test_saving_into_a_folder_that_cannot_be_written_is_not_an_error(tmp_path, monkeypatch):
    blocked = tmp_path / "file"
    blocked.write_text("x")
    monkeypatch.setenv("IYICAM_HOME", str(blocked / "home"))   # a folder below a file
    Settings().save()


def test_last_folder_is_kept_per_kind_and_falls_back_to_the_last_one_used(tmp_path, monkeypatch):
    monkeypatch.setenv("IYICAM_HOME", str(tmp_path / "home"))
    a, b = tmp_path / "a", tmp_path / "b"
    a.mkdir()
    b.mkdir()
    s = Settings()
    s.set_last_dir("import", str(a / "drawing.dxf"))
    s.set_last_dir("gcode", str(b))
    s = Settings.load()
    assert s.last_dir("import") == str(a) and s.last_dir("gcode") == str(b)
    assert s.last_dir("project") == str(b)          # never used: where the last file dialog was
    a.rmdir()
    assert s.last_dir("import") == str(b)           # a folder that is gone is not offered


# -- new projects ---------------------------------------------------------------------------------------------
def test_new_project_and_material_setup_are_remembered(sess):
    win, robot = sess.win, sess.robot

    def hook(dlg):
        if dlg.windowTitle() == "New Project":
            set_form(dlg, width=2440.0, height=1220.0, thickness=12.0)
        if dlg.windowTitle() == "Material Setup":
            set_form(dlg, safe_z=25.0, clearance=4.0, xy_origin="top_right", z_zero="bottom", home_z=60.0)
    robot.hook = hook
    win.new_project()
    assert sess.ctrl.sheet.width == 2440 and sess.ctrl.project.material.thickness == 12
    win.ctrl.project.material.thickness = 12.0
    from iyicam.gui.dialogs import MaterialDialog
    MaterialDialog(sess.ctrl, win).exec()
    assert sess.ctrl.project.material.safe_z == 25.0
    win = sess.restart()
    seen = {}

    def look(dlg):
        if dlg.windowTitle() == "New Project":
            f = dlg.findChild(ParamForm)
            seen.update(f.values())
    sess.robot.hook = look
    win.new_project()
    assert (seen["width"], seen["height"], seen["thickness"]) == (2440.0, 1220.0, 12.0)
    m = sess.ctrl.project.material                       # and the new project is set up the way the last was
    assert (m.safe_z, m.clearance, m.xy_origin, m.z_zero, m.home[2]) == (25.0, 4.0, "top_right", "bottom", 60.0)
    assert sess.robot.problems() == []


# -- toolpaths ----------------------------------------------------------------------------------------------
def test_toolpath_settings_and_tool_are_remembered_per_kind(sess):
    from conftest import end_mill
    win, robot, c = sess.win, sess.robot, sess.ctrl
    obj = VectorObject([shapes.rectangle(10, 10, 100, 60)], c.layer_id)
    c.add_vectors([obj])

    def hook(dlg):
        if hasattr(dlg, "tool_editor") and hasattr(dlg, "form"):
            dlg.name.setText("My cut")
            dlg.tool_editor.set_tool(end_mill(8.0, name="Eight", feed_rate=4321))
            set_form(dlg, side="inside", overcut=0.7, allowance=0.4, tabs=True, tab_count=6, through=False,
                     cut_depth=c.units.to_display(7.0))
            dlg.all_sheets.setChecked(False)
    robot.hook = hook
    win.new_toolpath("profile")
    first = c.project.toolpaths[-1]
    assert first.tool.diameter == 8.0 and first.sheet_ids == [c.sheet_id]
    assert (first.params["side"], first.params["tab_count"], first.params["cut_depth"]) == ("inside", 6, 7.0)
    win = sess.restart()
    c = sess.ctrl
    c.add_vectors([VectorObject([shapes.rectangle(10, 10, 100, 60)], c.layer_id)])
    made = {}

    def look(dlg):
        if hasattr(dlg, "tool_editor") and hasattr(dlg, "form"):
            made["tool"] = dlg.tool_editor.tool()
            made["values"] = dlg.form.values()
            made["all_sheets"] = dlg.all_sheets.isChecked()
            made["name"] = dlg.name.text()
    sess.robot.hook = look
    win.new_toolpath("profile")
    v = made["values"]
    assert made["tool"].diameter == 8.0 and made["tool"].feed_rate == 4321 and made["all_sheets"] is False
    assert (v["side"], v["overcut"], v["allowance"], v["tabs"], v["tab_count"], v["through"]) == \
        ("inside", 0.7, 0.4, True, 6, False)
    # the depth of a cut follows the material of the project, not what was typed last time
    assert v["cut_depth"] == pytest.approx(c.project.material.thickness)
    assert made["name"] != "My cut"                      # names are not remembered
    # another kind of toolpath does not get the profile's settings
    made.clear()
    win.new_toolpath("pocket")
    assert made["tool"].feed_rate != 4321 and made["values"]["through"] is False
    # editing a toolpath shows what it has, not what was remembered
    t = c.project.toolpaths[0]
    t.params["overcut"] = 1.1
    made.clear()
    win.edit_toolpath(t.id)
    assert made["values"]["overcut"] == pytest.approx(1.1)
    assert sess.robot.problems() == []


def test_cut_order_choice_is_the_start_of_the_next_toolpaths(sess):
    from conftest import end_mill

    from iyicam.core.document import Selection, ToolpathDef
    c = sess.ctrl
    obj = VectorObject([shapes.rectangle(10, 10, 100, 60)], c.layer_id)
    c.add_vectors([obj], select=False)
    t = ToolpathDef("a", "profile", end_mill(6), {"through": True}, Selection("vectors", [obj.id]))
    c.add_toolpath(t)
    c.set_sequence(t.id, strategy="rows_tr", grouping="part", smart_starts=True, start_margin=2.5)
    c.remember_cut_order(strategy="rows_tr", grouping="part", smart_starts=True, start_margin=2.5)
    sess.restart()
    c = sess.ctrl
    obj = VectorObject([shapes.rectangle(10, 10, 100, 60)], c.layer_id)
    c.add_vectors([obj], select=False)
    t2 = ToolpathDef("b", "profile", end_mill(6), {"through": True}, Selection("vectors", [obj.id]))
    c.add_toolpath(t2)
    assert (t2.sequence["strategy"], t2.sequence["grouping"], t2.sequence["smart_starts"],
            t2.sequence["start_margin"]) == ("rows_tr", "part", True, 2.5)


# -- nesting ---------------------------------------------------------------------------------------------------
def test_nesting_settings_are_remembered_but_the_sheet_size_follows_the_sheet(sess):
    from iyicam.gui.dialogs import NestingDialog
    c = sess.ctrl
    objs = [VectorObject([shapes.rectangle(0, 0, 100, 60)], c.layer_id, f"P{i}") for i in range(3)]
    c.add_vectors(objs, select=False)
    nd = NestingDialog(c, sess.win)
    set_form(nd, spacing=11.0, margin=7.0, rotation="45", gravity="reach_x", iterations=5, group_inner=True,
             fill_holes=True, squeeze="ask", squeeze_angle=4.0, tolerance=0.3, width=999.0)
    nd.qty[0].setValue(3)
    nd.accept()
    nd.run()
    sess.restart()
    c = sess.ctrl
    c.add_vectors([VectorObject([shapes.rectangle(0, 0, 100, 60)], c.layer_id, "P")], select=False)
    nd = NestingDialog(c, sess.win)
    v = nd.form.values()
    assert (v["spacing"], v["margin"], v["rotation"], v["gravity"], v["iterations"]) == (11.0, 7.0, "45", "reach_x", 5)
    assert v["group_inner"] and v["fill_holes"] and v["squeeze"] == "ask" and v["squeeze_angle"] == 4.0
    assert v["tolerance"] == pytest.approx(0.3)
    assert v["width"] == c.sheet.width            # the size of the sheet, not the one typed last time
    assert sess.robot.problems() == []


def test_part_quantities_stay_with_the_project(sess):
    from iyicam.gui.dialogs import NestingDialog
    c = sess.ctrl
    objs = [VectorObject([shapes.rectangle(0, 100 * i, 80, 60)], c.layer_id, f"P{i}") for i in range(3)]
    c.add_vectors(objs, select=False)
    nd = NestingDialog(c, sess.win)
    nd.qty[1].setValue(4)
    nd.qty[2].setValue(0)
    nd.form.widgets["target"].setCurrentIndex(nd.form.widgets["target"].findData("new"))
    nd.run()
    c.undo()
    again = NestingDialog(c, sess.win)
    assert [sp.value() for sp in again.qty] == [1, 4, 0]


# -- importing, exporting, commands --------------------------------------------------------------------------------
def test_import_options_and_the_last_folder_are_remembered(sess, tmp_path):
    import ezdxf
    d1, d2 = tmp_path / "drawings", tmp_path / "other"
    d1.mkdir()
    d2.mkdir()
    doc = ezdxf.new()
    doc.modelspace().add_circle((0, 0), 10)
    doc.saveas(d1 / "a.dxf")
    doc.saveas(d2 / "b.dxf")
    win, robot = sess.win, sess.robot

    def hook(dlg):
        if dlg.windowTitle().startswith("Import"):
            set_form(dlg, units="25.4", join=0.2, center=True, keep_layers=False)
    robot.hook = hook
    robot.files.append(str(d1 / "a.dxf"))
    assert len(win.import_vectors()) == 1
    assert robot.problems() == []
    win = sess.restart()
    robot = sess.robot
    seen = {}

    def look(dlg):
        if dlg.windowTitle().startswith("Import"):
            seen.update(dlg.form.values())
    robot.hook = look
    robot.files.append(str(d2 / "b.dxf"))
    win.import_vectors()
    assert robot.file_calls[0][1] == str(d1)                 # the folder of the last import
    assert seen["units"] == "25.4" and seen["join"] == pytest.approx(0.2) and seen["center"] is True
    assert seen["keep_layers"] is False
    # an import into a folder the project never saw does not move the project folder
    assert Settings.load().last_dir("import") == str(d2)


def test_dxf_export_asks_for_the_autocad_version_and_remembers_it(sess, tmp_path):
    import ezdxf

    from iyicam.core import shapes
    from iyicam.core.document import VectorObject
    win, robot = sess.win, sess.robot
    layer = sess.ctrl.project.add_layer("Cut/Out", "#ff0000")
    sess.ctrl.add_vectors([VectorObject([shapes.rectangle(0, 0, 40, 30)], layer.id, "r")], "r")
    robot.hook = lambda dlg: set_form(dlg, version="R2000") if dlg.windowTitle().startswith("Export") else None
    robot.files.append(str(tmp_path / "a.dxf"))
    win.export_vectors()
    assert robot.problems() == []
    doc = ezdxf.readfile(str(tmp_path / "a.dxf"))
    assert doc.acad_release == "R2000"
    assert [layer.dxf.name for layer in doc.layers if layer.dxf.name != "0"][-1] == "Cut_Out"
    assert any(t.startswith("Export AutoCAD DXF") for t in robot.dialogs)
    win = sess.restart()
    robot = sess.robot
    seen = {}
    robot.hook = lambda dlg: seen.update(dlg.form.values()) if dlg.windowTitle().startswith("Export") else None
    robot.files.append(str(tmp_path / "b.dxf"))
    win.export_vectors()
    assert seen["version"] == "R2000"                         # what it was set to last time
    robot.files.append(str(tmp_path / "c.dxf"))
    robot.refuse = True                                       # a dialog that is cancelled writes nothing
    win.export_vectors()
    assert not (tmp_path / "c.dxf").exists()


def test_export_that_cannot_be_written_says_so_instead_of_crashing(sess, tmp_path):
    win, robot = sess.win, sess.robot
    win.export_vectors(str(tmp_path / "no" / "such" / "folder" / "a.dxf"), {"version": "R2010"})
    assert robot.errors == []
    assert any(kind == "warning" and "could not be written" in text for kind, text in robot.messages)
    robot.messages.clear()
    win.export_vectors(str(tmp_path / "a.svg"))              # (svg has no options: no dialog)
    assert (tmp_path / "a.svg").exists() and robot.problems() == [] and robot.dialogs == []


def test_every_kind_of_file_has_its_own_last_folder(sess, tmp_path):
    from iyicam.gui.files import open_file, save_file
    win, robot, s = sess.win, sess.robot, sess.ctrl.settings
    for kind in ("project", "import", "gcode", "stock", "tools", "image", "template", "export", "plugin"):
        (tmp_path / kind).mkdir()
        robot.files.append(str(tmp_path / kind / "f.x"))
        open_file(win, s, kind, "t", "")
        robot.file_calls.clear()
        open_file(win, s, kind, "t", "")
        assert robot.file_calls[-1][1] == str(tmp_path / kind), kind
    robot.files.append(str(tmp_path / "gcode" / "o.nc"))
    save_file(win, s, "gcode", "t", "job.nc", "")
    assert robot.file_calls[-1][1] == str(tmp_path / "gcode" / "job.nc")


def test_gcode_export_options_and_folder_are_remembered(sess, tmp_path):
    from conftest import end_mill

    from iyicam.core.document import Selection, ToolpathDef
    from iyicam.gui.dialogs import ExportDialog
    c = sess.ctrl
    obj = VectorObject([shapes.rectangle(10, 10, 100, 60)], c.layer_id)
    c.add_vectors([obj], select=False)
    c.add_toolpath(ToolpathDef("p", "profile", end_mill(6), {"through": True}, Selection("vectors", [obj.id])))
    c.recalculate()
    out = tmp_path / "nc"
    out.mkdir()
    dlg = ExportDialog(c, sess.win)
    dlg.sheets.setCurrentIndex(dlg.sheets.findData("all"))
    dlg.tile.setChecked(True)
    dlg.tile_axis.setCurrentIndex(dlg.tile_axis.findData("x"))
    dlg.tile_len.setValue(c.units.to_display(75.0))
    sess.robot.files.append(str(out / "job.nc"))
    dlg.save()
    assert (out / "job_1_tile1.nc").exists() or list(out.glob("*.nc"))
    sess.restart()
    c = sess.ctrl
    c.add_toolpath(ToolpathDef("p", "profile", end_mill(6), {"through": True},
                               Selection("layers", [], [c.layer_id])))
    dlg = ExportDialog(c, sess.win)
    assert dlg.sheets.currentData() == "all" and dlg.tile.isChecked() and dlg.tile_axis.currentData() == "x"
    assert dlg.tile_len.value() == pytest.approx(75.0)
    sess.robot.files.append(str(out / "again.nc"))
    sess.robot.file_calls.clear()
    dlg.save()
    assert sess.robot.file_calls[0][1].startswith(str(out))


def test_command_trace_text_and_feeds_dialogs_remember_their_values(sess):
    from iyicam.gui.dialogs import FeedsDialog, TextDialog
    win, c = sess.win, sess.ctrl
    obj = VectorObject([shapes.rectangle(10, 10, 100, 60)], c.layer_id)
    c.add_vectors([obj])
    cmd = next(cm for cm in c.registry.commands.values() if cm.params and any(p.kind == "length" for p in cm.params))
    p0 = next(p for p in cmd.params if p.kind == "length")
    from iyicam.gui.dialogs import CommandDialog
    d = CommandDialog(c, cmd, win)
    set_form(d, **{p0.name: 12.5})
    assert d.values()[p0.name] == 12.5
    td = TextDialog(c, win)
    td.edit.setPlainText("Remember me")
    td.height.setValue(c.units.to_display(42.0))
    td.bold.setChecked(True)
    td.contours()
    from conftest import end_mill
    fd = FeedsDialog(c, end_mill(6), win)
    fd.form.widgets["material"].setCurrentIndex(fd.form.widgets["material"].count() - 1)
    material = fd.form.widgets["material"].currentData()
    fd.apply_to(end_mill(6))
    win = sess.restart()
    c = sess.ctrl
    d = CommandDialog(c, cmd, win)
    assert d.form.values()[p0.name] == 12.5
    td = TextDialog(c, win)
    assert td.text() == "Remember me" and td.height.value() == pytest.approx(c.units.to_display(42.0))
    assert td.bold.isChecked()
    assert FeedsDialog(c, end_mill(6), win).form.values()["material"] == material
    assert sess.robot.problems() == []


def test_the_window_geometry_is_saved_when_it_is_closed(sess):
    """(The offscreen screen is 800 x 600, so the size cannot be compared here: that the geometry is saved, valid
    and taken back by Qt is what counts.)"""
    from PySide6.QtCore import QByteArray
    sess.win.resize(1010, 707)
    process()
    win = sess.restart(resize=False)
    saved = Settings.load().window_geometry
    assert saved and win.restoreGeometry(QByteArray.fromBase64(saved.encode()))
    assert Settings.load().window_state


def test_dialogs_open_at_the_size_they_were_closed_with(sess):
    from PySide6.QtGui import QGuiApplication

    from iyicam.gui.dialogs import ExportDialog, NestingDialog
    avail = QGuiApplication.primaryScreen().availableGeometry()
    w, h = min(500, avail.width()), min(430, avail.height())
    dlg = NestingDialog(sess.win.ctrl, sess.win)
    first = (dlg.width(), dlg.height())
    dlg.resize(w, h)
    dlg.reject()
    win = sess.restart()                          # a new run of the program
    again = NestingDialog(win.ctrl, win)
    assert (again.width(), again.height()) == (w, h) != first
    other = ExportDialog(win.ctrl, win)           # each dialog has its own size
    assert (other.width(), other.height()) != (w, h)
    again.accept()
    # nonsense in the file is ignored: the dialog opens at its usual size
    win.ctrl.settings.memory["size:nesting"] = {"width": "wide", "height": None}
    assert NestingDialog(win.ctrl, win).width() == first[0]
    win.ctrl.settings.memory["size:nesting"] = {"width": 10 ** 9, "height": 10 ** 9}
    big = NestingDialog(win.ctrl, win)            # never bigger than the screen
    assert big.width() <= avail.width() and big.height() <= avail.height()

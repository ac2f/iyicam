"""Toolpaths chosen together saved as one file each, in one folder."""
import pytest

from conftest import add_tp, end_mill, make_project
from iyicam.builtins import ensure_registered
from iyicam.core import shapes
from iyicam.core.registry import REGISTRY

ensure_registered(REGISTRY)


@pytest.fixture(autouse=True)
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("IYICAM_HOME", str(tmp_path / "home"))


def project():
    p, s1, objs = make_project([shapes.rectangle(10, 10, 100, 60), shapes.rectangle(200, 10, 100, 60)], size=(600, 400))
    a = add_tp(p, "profile", end_mill(6), {"through": True}, objs[:1])
    b = add_tp(p, "profile", end_mill(6), {"through": True}, objs[1:])
    c = add_tp(p, "pocket", end_mill(6), {"cut_depth": 3}, objs[1:])
    a.name, b.name, c.name = "Dis kesim", "Ic kesim", "Cep"
    return p, (a, b, c)


def test_the_engine_writes_a_file_for_every_toolpath_chosen():
    from iyicam.service import ops
    p, (a, b, c) = project()
    post = next(iter(REGISTRY.posts))
    r = ops.op_gcode({"project": p.to_dict(), "post": post, "file_name": "is", "per_toolpath": True, "toolpaths": [a.id, c.id]})
    names = [f["name"].rsplit(".", 1)[0] for f in r["files"]]
    assert names == ["is_Dis kesim", "is_Cep"]
    one = ops.op_gcode({"project": p.to_dict(), "post": post, "file_name": "is", "toolpaths": [a.id, c.id]})
    assert len(one["files"]) == 1


def test_names_made_of_the_toolpaths_are_kept_and_two_of_one_name_differ():
    from iyicam.service import ops
    p, (a, b, c) = project()
    b.name = a.name
    post = next(iter(REGISTRY.posts))
    r = ops.op_gcode({"project": p.to_dict(), "post": post, "per_toolpath": True, "name_parts": ["{toolpath_name}"]})
    names = [f["name"].rsplit(".", 1)[0] for f in r["files"]]
    assert len(names) == 3 and len(set(n.lower() for n in names)) == 3 and names[2] == "Cep"


def test_the_desktop_saves_one_file_per_toolpath_in_the_folder(tmp_path):
    pytest.importorskip("PySide6.QtWidgets")
    from PySide6.QtWidgets import QApplication
    from iyicam.gui.app import create_window
    from iyicam.gui.dialogs import ExportDialog
    app, w = create_window(["test"], load_plugins=False)
    c = w.ctrl
    c.settings.auto_recalculate = False
    p, tds = project()
    c.project.sheets = p.sheets
    c.project.layers = p.layers
    c.project.toolpaths = p.toolpaths
    c.set_sheet(p.sheets[0].id)
    c.recalculate()
    dlg = ExportDialog(c, w, only={tds[0].id, tds[1].id})
    dlg.folder.setText(str(tmp_path / "out"))
    dlg.per_toolpath.setChecked(True)
    dlg.save()
    files = sorted(x.stem for x in (tmp_path / "out").iterdir())
    assert len(files) == 2 and any("Dis kesim" in f for f in files) and any("Ic kesim" in f for f in files)
    dlg.close()
    c.dirty = False
    w.close()
    QApplication.processEvents()


def test_the_save_window_ticks_the_toolpaths_of_the_sheet_shown():
    pytest.importorskip("PySide6.QtWidgets")
    from PySide6.QtWidgets import QApplication
    from iyicam.gui.app import create_window
    from iyicam.gui.dialogs import ExportDialog
    app, w = create_window(["test"], load_plugins=False)
    c = w.ctrl
    c.settings.auto_recalculate = False
    p, (a, b, cc) = project()
    s2 = p.add_sheet("Pleksi 1", 600, 400)
    s2.group = "Pleksi"
    b.sheet_group = "Pleksi"                                  # (b is kept to the other sheet's group)
    c.project.sheets, c.project.layers, c.project.toolpaths = p.sheets, p.layers, p.toolpaths
    c.set_sheet(p.sheets[0].id)
    dlg = ExportDialog(c, w)
    dlg.sheets.setCurrentIndex(dlg.sheets.findData("all"))
    dlg.tick_sheet()
    assert dlg.chosen() == {a.id, cc.id} and dlg.sheets.currentData() == "current"
    dlg.close()
    c.dirty = False
    w.close()
    QApplication.processEvents()

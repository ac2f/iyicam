"""The names of the G-code files: prefixes with variables (iyicam/post/naming.py), the engine, and the Save G-code window."""
import copy
import datetime as dt

import pytest
from conftest import add_tp, end_mill, make_project

from iyicam.core import shapes
from iyicam.post import naming
from iyicam.service import ServiceHost


def test_the_prefixes_are_joined_with_their_variables_put_in():
    v = naming.values("Master döner", "Plate 2", 2, 3, "Mach3", "Letters", 4, 1, "EM6", 18, 1250, 3200,
                      now=dt.datetime(2026, 11, 18, 9, 30))
    assert naming.render(["{project_name}", "P{sheet_number}", "{date}"], v) == "Master döner_P2_2026-11-18"
    assert naming.render(["{group}", "T{tool_number}"], v, "-") == "Letters-T1"
    assert naming.render(["{group}", "x"], {**v, "group": ""}) == "x"                  # (an empty prefix is left out)
    assert naming.render(["a/b:c"], v) == "a_b_c"                                       # (nothing that is a path)
    assert naming.render([" ", ""], v) == "Master döner"                                  # (the default: the project)


def test_what_is_wrong_is_said():
    with pytest.raises(naming.NamingError, match="nope"):
        naming.check(["{project_name}", "{nope}"])
    with pytest.raises(naming.NamingError):
        naming.check("x")
    with pytest.raises(naming.NamingError):
        naming.check(["a"], "/")
    assert naming.check(["a"] * 20)[0] == ["a"] * naming.MAX_PARTS


def test_the_engine_names_the_files_by_the_prefixes():
    p, s, objs = make_project([[shapes.rectangle(10, 10, 100, 60)]], size=(500, 300))
    add_tp(p, "profile", end_mill(6), {"through": True}, objs)
    p.toolpaths[0].group = "Letters"
    h = ServiceHost()
    g = h.handle("gcode", {"project": p.to_dict(), "post": "mach3", "name_parts": ["{project_name}", "{group}", "T{tool_number}"]})
    assert g["ok"] and g["files"][0]["name"].endswith("_Letters_T1.tap"), g["files"][0]["name"]
    two = copy.deepcopy(p)
    two.add_sheet("Second", 300, 200)
    two.sheets[1].vectors = [copy.deepcopy(v) for v in two.sheets[0].vectors]
    g = h.handle("gcode", {"project": two.to_dict(), "post": "mach3", "name_parts": ["{project_name}"], "name_sep": "-"})
    names = [f["name"] for f in g["files"]]
    assert len(names) == len(set(names)) == 2 and all("_" in n for n in names)               # (a number keeps them apart)
    g = h.handle("gcode", {"project": two.to_dict(), "post": "mach3", "name_parts": ["{sheet_name}"]})
    assert len({f["name"] for f in g["files"]}) == 2
    assert h.handle("gcode", {"project": p.to_dict(), "post": "mach3", "name_parts": ["{zzz}"]})["error"] == "bad_request"
    assert "naming" in h.handle("schema", {}) and h.handle("schema", {})["naming"]["variables"]


def test_the_save_window_names_the_files_by_the_prefixes(tmp_path, monkeypatch):
    pytest.importorskip("PySide6.QtWidgets")
    from gui_robot import rwin_gen
    from test_cutedit import parts
    from iyicam.gui.dialogs import ExportDialog
    gen = rwin_gen(tmp_path, monkeypatch)
    win, _ = next(gen)
    try:
        c, t, objs = parts(win)
        d = ExportDialog(c, win)
        assert d.name_parts() == ["{project_name}"]
        d._add_prefix("{date}")
        d._add_prefix("T{tool_number}")
        stem = d.file_stem(1, c.sheet, d.toolpaths(c.sheet))
        assert stem.endswith("_T1") and dt.date.today().isoformat() in stem
        d.name_var.setCurrentIndex(0)
        d._active = d._name_rows.itemAt(0).layout().itemAt(0).widget()
        d._insert_var(d.name_var.findData("sheet_number"))
        assert "{sheet_number}" in d.name_parts()[0]
        d._add_prefix("{bad}")
        assert "unknown variable" in d.name_preview.text()
        d.close()
    finally:
        gen.close()


def test_the_save_window_saves_into_the_folder_by_revision_and_counts_up(tmp_path, monkeypatch):
    pytest.importorskip("PySide6.QtWidgets")
    from gui_robot import rwin_gen
    from test_cutedit import parts
    from iyicam.gui.dialogs import ExportDialog
    gen = rwin_gen(tmp_path / "h", monkeypatch)
    win, _ = next(gen)
    try:
        c, t, objs = parts(win)
        c.recalculate()
        out = tmp_path / "nc"
        out.mkdir()
        d = ExportDialog(c, win)
        d.sheets.setCurrentIndex(d.sheets.findData("current"))
        d._add_prefix("R{revision}")
        d.subfolder.setText("Rev{revision}")
        d.revision.setValue(4)
        assert not list(out.rglob("*.tap"))                  # (nothing is written before Save is pressed)
        d.save()                                             # (no folder yet: it asks)
        assert not list(out.rglob("*.tap"))
        d.folder.setText(str(out))
        d.save()
        files = list(out.rglob("*.tap"))
        assert len(files) == 1 and files[0].parent.name == "Rev4" and files[0].stem.endswith("_R4")
        assert d.revision.value() == 5 and c.project.metadata["revisions"]["project"] == 5     # counted up
        assert "Last saved" in d.last_saved.text() and files[0].name in d.last_saved.text()
        d.rev_auto.setChecked(False)
        d.revision.setValue(9)
        d.save()
        assert (out / "Rev9").is_dir() and d.revision.value() == 9
        for v in ("toolpath_name", "safe_z", "cut_depth", "tool_diameter", "sheet_group", "revision"):
            assert v in naming.NAMES
        d.close()
    finally:
        gen.close()


def test_renaming_a_toolpath_can_make_a_new_one_and_the_window_follows_the_selection(tmp_path, monkeypatch):
    pytest.importorskip("PySide6.QtWidgets")
    from gui_robot import rwin_gen
    from test_cutedit import parts
    gen = rwin_gen(tmp_path / "h", monkeypatch)
    win, robot = next(gen)
    try:
        c, t, objs = parts(win)
        n = len(c.project.toolpaths)
        # the name is changed and "keep the original" is ticked: a new toolpath, the old one is as it was
        robot.hook = lambda d: (d.name.setText("Second try"), d.keep_old.setChecked(True)) if hasattr(d, "keep_old") else None
        win.edit_toolpath(t.id)
        assert len(c.project.toolpaths) == n + 1 and c.project.toolpath(t.id).name == t.name
        assert c.project.toolpaths[-1].name == "Second try" and c.project.toolpaths[-1].id != t.id
        # the setting makes the name do it by itself
        def hook(d):
            if hasattr(d, "auto_new"):
                d.auto_new.setChecked(True)
                d.name.setText("Third")
                assert d.keep_old.isChecked()
        robot.hook = hook
        win.edit_toolpath(t.id)
        assert len(c.project.toolpaths) == n + 2 and c.settings.rename_makes_new is True
        # without it the toolpath is just renamed
        c.settings.rename_makes_new = False
        robot.hook = lambda d: d.name.setText("Plain rename") if hasattr(d, "keep_old") else None
        win.edit_toolpath(t.id)
        assert len(c.project.toolpaths) == n + 2 and c.project.toolpath(t.id).name == "Plain rename"
        # a new toolpath follows what is selected on the drawing while its window is open
        from iyicam.gui.dialogs import ToolpathDialog
        c.select([])
        dlg = ToolpathDialog(c, "profile", None, win)
        c.select([objs[0].id, objs[1].id])
        assert dlg.vector_ids == [objs[0].id, objs[1].id] and "(2)" in dlg.rb_sel.text()
        dlg.follow.setChecked(False)
        c.select([objs[2].id])
        assert len(dlg.vector_ids) == 2
        dlg.reject()
        c.select([objs[0].id])                                # (the window let go of the selection)
        dlg.close()
    finally:
        gen.close()


def test_a_project_that_is_opened_has_its_toolpaths_calculated_once(tmp_path, monkeypatch):
    pytest.importorskip("PySide6.QtWidgets")
    from gui_robot import rwin_gen
    from test_cutedit import parts
    gen = rwin_gen(tmp_path / "h", monkeypatch)
    win, robot = next(gen)
    try:
        c, t, objs = parts(win, auto=False)
        c.recalculate()
        path = tmp_path / "p.icam"
        c.save_project(path)
        c.open_project(path)
        assert c.results and not c.stale
        c.settings.calculate_on_open = False
        c.open_project(path)
        assert not c.results and c.stale
    finally:
        gen.close()

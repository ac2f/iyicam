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

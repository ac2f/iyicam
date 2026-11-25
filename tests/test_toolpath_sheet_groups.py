"""Toolpaths and groups of sheets: ``{sheet_group}`` in a name, and a toolpath kept to a group listed on its sheets only."""
import pytest

from conftest import add_tp, end_mill, make_project
from iyicam.cam.job import shown_on
from iyicam.core import shapes
from iyicam.core.document import Sheet
from iyicam.post.naming import toolpath_name


def grouped():
    p, s1, objs = make_project([shapes.rectangle(10, 10, 100, 60)], size=(600, 400))
    s2 = Sheet("Pleksi 1", 600, 400)
    s2.group = "Pleksi"
    p.sheets.append(s2)
    return p, s1, s2, objs


def test_sheet_group_in_a_name_is_that_of_the_sheet_shown_when_the_toolpath_has_none():
    p, s1, s2, objs = grouped()
    t = add_tp(p, "profile", end_mill(6), {"through": True}, objs)
    t.name = "Kesim {sheet_group} S{sheet_number}"
    assert toolpath_name(p, t, s2) == "Kesim Pleksi S2"
    assert toolpath_name(p, t, s1) == "Kesim  S1"
    t.sheet_group = "Pleksi"
    assert toolpath_name(p, t, s1) == "Kesim Pleksi S1"        # (its own group comes first)


def test_a_toolpath_kept_to_a_group_belongs_on_its_sheets_only():
    p, s1, s2, objs = grouped()
    t = add_tp(p, "profile", end_mill(6), {"through": True}, objs)
    assert shown_on(p, t, s1) and shown_on(p, t, s2)
    t.sheet_group = "Pleksi"
    assert not shown_on(p, t, s1) and shown_on(p, t, s2)


def test_the_view_op_gives_names_and_the_toolpaths_of_the_sheet(tmp_path, monkeypatch):
    monkeypatch.setenv("IYICAM_HOME", str(tmp_path))
    from iyicam.core.document import project_to_dict
    from iyicam.service import ops
    p, s1, s2, objs = grouped()
    t = add_tp(p, "profile", end_mill(6), {"through": True}, objs)
    t.name, t.sheet_group = "{sheet_group} kesim", "Pleksi"
    d = project_to_dict(p)
    assert ops.op_toolpath_view({"project": d, "sheet": s1.id})["shown"] == []
    r = ops.op_toolpath_view({"project": d, "sheet": s2.id})
    assert r["shown"] == [t.id] and r["names"][t.id] == "Pleksi kesim"


def test_the_desktop_lists_the_toolpaths_of_the_sheet_shown():
    pytest.importorskip("PySide6.QtWidgets")
    from PySide6.QtWidgets import QApplication
    from iyicam.gui.app import create_window
    app, w = create_window(["test"], load_plugins=False)
    c = w.ctrl
    c.settings.auto_recalculate = False
    s2 = c.project.add_sheet("Pleksi 1", 600, 400)
    s2.group = "Pleksi"
    t = add_tp(c.project, "profile", end_mill(6), {"through": True}, [])
    t.sheet_group = "Pleksi"
    c.set_sheet(c.project.sheets[0].id)
    w.toolpaths_panel.refresh()
    assert w.toolpaths_panel.tree.topLevelItemCount() == 0
    c.set_sheet(s2.id)
    w.toolpaths_panel.refresh()
    assert w.toolpaths_panel.tree.topLevelItemCount() == 1
    c.settings.toolpaths_of_sheet_only = False
    c.set_sheet(c.project.sheets[0].id)
    w.toolpaths_panel.refresh()
    assert w.toolpaths_panel.tree.topLevelItemCount() == 1
    c.dirty = False
    w.close()
    QApplication.processEvents()

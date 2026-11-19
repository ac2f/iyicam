"""File names of several toolpaths, sums and zeros in the prefixes; the toolpath list stacked by group with columns that can
be sized; a toolpath is old (red) only when its own vectors or settings change."""
import pytest

from conftest import add_tp, end_mill, make_project

from iyicam.builtins import ensure_registered
from iyicam.cam.job import compute_all, sheet_toolpaths
from iyicam.core import shapes
from iyicam.core.registry import REGISTRY
from iyicam.post import naming


def test_formats_and_sums_in_the_prefixes():
    v = {"revision": "7", "sheet_number": "2", "sheet_width": "600", "toolpath_name": "A+B", "date": "2026-11-19"}
    f = naming.field_text
    assert f("revision:03", v) == "007" and f("revision:3", v) == "007" and f("sheet_number + 100", v) == "102"
    assert f("sheet_width:.1", v) == "600.0" and f("sheet_width/4:04", v) == "0150" and f("toolpath_name", v) == "A+B"
    assert f("revision*2:03", v) == "014" and f("nope + 1", v) == ""
    assert naming.render(["{project_name}", "R{revision:03}"], {"project_name": "P", "revision": "12"}) == "P_R012"
    assert naming.unknown(["{revision:03}", "{sheet_number+1}", "{foo*2}"]) == ["foo"]
    with pytest.raises(naming.NamingError):
        naming.check(["{bar:03}"])


def two_toolpaths():
    ensure_registered(REGISTRY)
    p, s, objs = make_project([[shapes.rectangle(10, 10, 100, 60)]], size=(500, 300))
    a = add_tp(p, "profile", end_mill(6), {"through": True}, objs)
    a.name = "Outline"
    b = add_tp(p, "profile", end_mill(3, number=2), {"through": True, "cut_depth": 5.0}, objs)
    b.name = "Holes"
    res = compute_all(p, REGISTRY)
    return p, s, sheet_toolpaths(p, res, s)


def test_every_toolpath_of_a_file_counts_in_its_name():
    p, s, tps = two_toolpaths()
    v = naming.values_for(p, s, 1, 1, "Mach3", "", tps, revision=3)
    assert v["toolpath_name"] == "Outline+Holes" and v["tool_number"] == "1+2" and v["toolpath_count"] == "2"
    assert v["tool_diameter"] == "6+3" and "5" in v["cut_depth"].split("+")
    assert naming.render(["{toolpath_name}", "T{tool_number}", "{revision:02}"], v) == "Outline+Holes_T1+2_03"


def test_one_file_per_tool_is_named_by_its_own_toolpaths():
    from iyicam.post.export import named_files
    p, s, tps = two_toolpaths()
    base = REGISTRY.posts["mach3"]
    post = type(base)(base.definition)
    post.options["split_by_tool"] = True
    stem = lambda g: naming.render(["{toolpath_name}", "T{tool_number:02}"], naming.values_for(p, s, 1, 1, "x", "", g))
    files = named_files(p, s, tps, post, stem)
    assert [n for n, _ in files] == ["Outline_T01", "Holes_T02"]
    assert "T2 M6" in files[1][1] and "T2 M6" not in files[0][1]
    same = named_files(p, s, tps, post, lambda g: "job")                      # (one name for both: the post's _1_T1 ...)
    assert [n for n, _ in same] == ["job_1_T1", "job_2_T2"]


# -- the desktop list -------------------------------------------------------------------------------------------
pytest.importorskip("PySide6.QtWidgets")


@pytest.fixture
def rwin(tmp_path, monkeypatch):
    from gui_robot import rwin_gen
    yield from rwin_gen(tmp_path, monkeypatch)


def test_only_the_toolpath_whose_vectors_changed_is_old(rwin):
    from gui_robot import process
    from test_cutedit import parts
    from iyicam.core.document import Selection, ToolpathDef, VectorObject
    from iyicam.core.tooling import Tool
    win, _ = rwin
    c, t, objs = parts(win, n=3, auto=False)
    t2 = ToolpathDef("Second", "profile", Tool("EM3", "end_mill", 3.0, 2, stepdown=3.0), {"through": True},
                     Selection("vectors", [objs[0].id]))
    c.add_toolpath(t2)
    process()
    assert not c.stale and not c.stale_ids
    c.add_vectors([VectorObject([shapes.rectangle(500, 300, 50, 50)], c.layer_id, "X")], "t")   # (no toolpath machines it)
    process()
    assert not c.stale and not c.stale_ids
    with c.edit("move"):
        c.find_vector(objs[1].id).contours[0] = shapes.rectangle(175, 100, 100, 80)      # (only the first toolpath has it)
    process()
    assert c.stale and c.stale_ids == {t.id}
    panel = win.toolpaths_panel
    rows = {it.data(0, 0x0100): it for it in panel._rows()}
    assert rows[t.id].foreground(0).color().name() == "#c00000"
    assert rows[t2.id].foreground(0).color().name() != "#c00000"
    c._auto_recalculate()
    process()
    assert not c.stale and not c.stale_ids


def test_the_list_stacks_toolpaths_by_group_and_columns_can_be_sized(rwin):
    from gui_robot import process
    from test_cutedit import parts
    from PySide6.QtWidgets import QHeaderView
    win, _ = rwin
    c, t, objs = parts(win, n=2, auto=False)
    from iyicam.core.document import Selection, ToolpathDef
    from iyicam.core.tooling import Tool
    t2 = ToolpathDef("B", "profile", Tool("EM3", "end_mill", 3.0, 2, stepdown=3.0), {"through": True},
                     Selection("vectors", [objs[0].id]))
    c.add_toolpath(t2)
    c.set_group([t.id, t2.id], "Letters")
    process()
    panel = win.toolpaths_panel
    tree = panel.tree
    assert tree.topLevelItemCount() == 1
    head = tree.topLevelItem(0)
    assert head.data(0, panel.GROUP_ROLE) == "Letters" and head.childCount() == 2 and head.text(0).startswith("Letters")
    head.setSelected(True)
    assert set(panel.selected_ids()) == {t.id, t2.id}
    head.setCheckState(0, __import__("PySide6.QtCore", fromlist=["Qt"]).Qt.Unchecked)                                # (unticking the heading switches the group off)
    process()
    assert not t.enabled and not t2.enabled
    assert all(tree.header().sectionResizeMode(i) == QHeaderView.Interactive for i in range(4))
    tree.setColumnWidth(1, 150)
    assert c.settings.recall("toolpaths_panel")["widths"][1] == 150
    tree.collapseItem(tree.topLevelItem(0))
    assert "Letters" in c.settings.recall("toolpaths_panel")["folded"]


def test_a_toolpath_name_with_variables_and_sums():
    from iyicam.post.naming import toolpath_name, unknown
    p, s, objs = make_project([[shapes.rectangle(10, 10, 100, 60)]], size=(500, 300))
    t = add_tp(p, "profile", end_mill(6, feed_rate=4800, spindle_speed=18000), {"through": True}, objs)
    t.name = "Profil Ø{tool_diameter} F{feed_rate/60:.1} S{spindle_speed} D{cut_depth} {nope}"
    assert toolpath_name(p, t, s) == f"Profil Ø6 F80.0 S18000 D{p.sheet_thickness(s) + float(t.params.get('overcut') or 0):g} {{nope}}"
    t.name = "Plain"
    assert toolpath_name(p, t, s) == "Plain"
    t.name = "Cut {tool_number:02}"
    res = compute_all(p, REGISTRY)
    tp = sheet_toolpaths(p, res, s)[0]
    assert tp.name == "Cut 01"                                         # (what the G-code and the simulation say)
    v = naming.values_for(p, s, 1, 1, "x", "", [tp])
    assert v["toolpath_name"] == "Cut 01"


def test_the_list_and_the_window_show_the_worked_out_name(rwin):
    from gui_robot import process
    from test_cutedit import parts
    from iyicam.gui.dialogs import ToolpathDialog
    win, _ = rwin
    c, t, objs = parts(win, n=1, auto=False)
    with c.edit("rename", geometry=False):
        t.name = "Cut Ø{tool_diameter}"
    c.toolpaths_changed.emit()
    process()
    row = next(win.toolpaths_panel._rows())
    assert row.text(0) == "Cut Ø6" and "Cut Ø{tool_diameter}" in row.toolTip(0)
    d = ToolpathDialog(c, t.op, t)
    d.name.setText("X {feed_rate/60:.1}")
    assert d.name_shown.text().startswith("→ X ")
    d.close()

"""Safe Z and the other heights: where they are set, that they are seen and that every toolpath keeps to them."""
import re

import pytest

pytest.importorskip("PySide6.QtWidgets")

from conftest import add_tp, end_mill, make_project  # noqa: E402
from PySide6.QtWidgets import QDialog  # noqa: E402
from gui_robot import process, rwin_gen  # noqa: E402
from test_cutedit import parts  # noqa: E402

from iyicam.builtins import ensure_registered  # noqa: E402
from iyicam.cam.job import compute_all, sheet_toolpaths  # noqa: E402
from iyicam.cam.moves import Rapid  # noqa: E402
from iyicam.core import shapes  # noqa: E402
from iyicam.core.document import Project, VectorObject, toolpath_from_dict, toolpath_to_dict  # noqa: E402
from iyicam.core.registry import REGISTRY  # noqa: E402
from iyicam.gui.dialogs import ExportDialog, MaterialDialog, ToolpathDialog  # noqa: E402
from iyicam.gui.material_info import material_summary  # noqa: E402
from iyicam.post.export import post_toolpaths  # noqa: E402


@pytest.fixture
def rwin(tmp_path, monkeypatch):
    yield from rwin_gen(tmp_path, monkeypatch)


def top_of(project, tdef_id):
    """The highest Z the tool is moved to (the safe Z when the surface is at 0)."""
    res = compute_all(project)
    tps = [tp for r in res.values() for tp in r.toolpaths if tp.def_id == tdef_id]
    assert tps and not [r.error for r in res.values() if r.error]
    return max(m.z for tp in tps for m in tp.moves if hasattr(m, "z")), tps


def project_with(op="profile", params=None, **material):
    p, s, objs = make_project([shapes.rectangle(20, 20, 100, 60), shapes.circle((200, 50), 20)])
    for k, v in material.items():
        setattr(p.material, k, v)
    t = add_tp(p, op, end_mill(6), params or {"through": True}, objs)
    return p, s, t


# -- the engine ------------------------------------------------------------------------------------------------
@pytest.mark.parametrize("op,params", [("profile", {"through": True}), ("pocket", {"cut_depth": 4.0}),
                                       ("drill", {"cut_depth": 6.0}), ("engrave", {"cut_depth": 1.0})])
def test_a_toolpath_travels_at_the_safe_z_of_the_project_or_its_own(op, params):
    ensure_registered(REGISTRY)
    p, s, t = project_with(op, params, safe_z=12.0, clearance=3.0)
    top, _ = top_of(p, t.id)
    assert top == pytest.approx(12.0)
    t.safe_z = 25.0
    top, tps = top_of(p, t.id)
    assert top == pytest.approx(25.0) and tps[0].safe_z == 25.0 and p.material.safe_z == 12.0
    t.safe_z = None
    t.clearance = 1.0
    assert top_of(p, t.id)[0] == pytest.approx(12.0)


def test_the_rapid_down_clearance_of_a_toolpath_is_where_the_tool_goes_down_to():
    ensure_registered(REGISTRY)
    p, s, t = project_with(safe_z=20.0, clearance=5.0)
    t.clearance = 2.0
    _, tps = top_of(p, t.id)
    below_safe = sorted({round(m.z, 6) for m in tps[0].moves if isinstance(m, Rapid) and m.z < 19.9})
    assert below_safe and max(below_safe) == pytest.approx(2.0)


def test_heights_of_a_toolpath_are_saved_and_a_damaged_file_means_the_project_s():
    p, s, t = project_with()
    assert toolpath_to_dict(t)["safe_z"] is None
    t.safe_z, t.clearance = 30.0, 4.0
    back = toolpath_from_dict(toolpath_to_dict(t))
    assert (back.safe_z, back.clearance) == (30.0, 4.0)
    d = toolpath_to_dict(t)
    for bad in ("high", True, float("nan"), [1], {}):
        d["safe_z"], d["clearance"] = bad, bad
        got = toolpath_from_dict(d)
        assert (got.safe_z, got.clearance) == (None, None), bad
    old = toolpath_to_dict(t)
    del old["safe_z"], old["clearance"]                                        # a project of an earlier version
    assert toolpath_from_dict(old).safe_z is None


def test_the_heights_survive_saving_the_project(tmp_path):
    p, s, t = project_with()
    t.safe_z = 33.0
    f = tmp_path / "h.icam"
    p.save(f)
    q = Project.load(f)
    assert q.toolpaths[0].safe_z == 33.0 and q.toolpaths[0].clearance is None


def test_the_end_of_the_program_does_not_go_lower_than_the_toolpaths_traveled():
    ensure_registered(REGISTRY)
    p, s, t = project_with(safe_z=10.0)
    post = REGISTRY.posts["mach3"]

    def footer_z():
        res = compute_all(p)
        tps = sheet_toolpaths(p, res, s)
        text = post_toolpaths(p, s, tps, post)[0][1]
        zs = re.findall(r"G0 Z(-?[\d.]+)", text)
        assert zs, text[-200:]
        return float(zs[-1])                                                    # (the last: the end of the program)
    assert footer_z() == pytest.approx(10.0)
    t.safe_z = 40.0                                                             # (clamps: it travels higher)
    assert footer_z() == pytest.approx(40.0)
    t.safe_z = 4.0                                                              # lower than the project's: the project's
    assert footer_z() == pytest.approx(10.0)


def test_a_clearance_higher_than_the_safe_z_is_warned_about():
    ensure_registered(REGISTRY)
    p, s, t = project_with(safe_z=5.0, clearance=8.0)
    res = compute_all(p)
    assert any("rapid-down clearance" in w for r in res.values() for w in r.warnings)
    p.material.clearance = 2.0
    res = compute_all(p)
    assert not any("rapid-down clearance" in w for r in res.values() for w in r.warnings)


def test_a_v_carve_with_flats_keeps_the_heights_of_its_toolpath():
    ensure_registered(REGISTRY)
    from conftest import vbit
    p, s, objs = make_project([shapes.rectangle(20, 20, 80, 50)])
    t = add_tp(p, "vcarve", vbit(60, 12.0), {"max_depth": 3.0, "flat_depth": 1.0}, objs)
    t.safe_z = 27.0
    res = compute_all(p)
    tps = [tp for r in res.values() for tp in r.toolpaths]
    assert tps and all(max(m.z for m in tp.moves if hasattr(m, "z")) == pytest.approx(27.0) for tp in tps)


def test_the_summary_of_the_material_says_where_the_tool_travels(rwin):
    win, _ = rwin
    c = win.ctrl
    m = c.project.material
    m.safe_z, m.clearance, m.thickness = 12.0, 3.0, 18.0
    text = material_summary(c)
    assert "18 mm thick" in text and "safe Z 12 mm" in text and "rapid-down 3 mm" in text and "Z zero at the top" in text
    c0, t, objs = parts(win)
    t.safe_z = 30.0
    assert "1 toolpath(s) with their own heights" in material_summary(c)
    c.set_units("inch")
    assert "in thick" in material_summary(c)


# -- the dialogs --------------------------------------------------------------------------------------------------
def test_the_toolpath_dialog_shows_the_safe_z_and_can_give_a_toolpath_its_own(rwin):
    win, robot = rwin
    c = win.ctrl
    c.project.material.safe_z, c.project.material.clearance = 15.0, 3.0
    c.add_vectors([VectorObject([shapes.rectangle(10, 10, 50, 50)], c.layer_id, "a")], "t")
    dlg = ToolpathDialog(c, "profile", None, win)
    assert "safe Z <b>15 mm</b>" in dlg.heights_note.text() and "rapid-down clearance <b>3 mm</b>" in dlg.heights_note.text()
    assert not dlg.own_heights.isChecked() and not dlg.safe_z.isEnabled()
    assert dlg.safe_z.value() == 15.0 and dlg.clearance.value() == 3.0            # (what it will use)
    assert dlg.heights() == (None, None)
    assert dlg.toolpath().safe_z is None
    dlg.own_heights.setChecked(True)
    assert dlg.safe_z.isEnabled() and dlg.clearance.isEnabled()
    dlg.safe_z.setValue(40.0)
    dlg.clearance.setValue(6.0)
    t = dlg.toolpath()
    assert (t.safe_z, t.clearance) == (40.0, 6.0)
    c.add_toolpath(t)
    process()
    tp = c.sheet_toolpaths()[0]
    assert tp.safe_z == 40.0 and max(m.z for m in tp.moves if hasattr(m, "z")) == pytest.approx(40.0)
    again = ToolpathDialog(c, "profile", c.project.toolpaths[0], win)               # editing it: its own are shown
    assert again.own_heights.isChecked() and again.safe_z.value() == 40.0 and again.safe_z.isEnabled()
    again.own_heights.setChecked(False)                                              # back to the project's
    assert again.safe_z.value() == 15.0
    t2 = again.toolpath()
    assert (t2.safe_z, t2.clearance) == (None, None)
    assert robot.problems() == []


def test_the_heights_box_is_in_inches_when_the_units_are(rwin):
    win, _ = rwin
    c = win.ctrl
    c.project.material.safe_z = 25.4
    c.set_units("inch")
    dlg = ToolpathDialog(c, "profile", None, win)
    assert dlg.safe_z.value() == pytest.approx(1.0) and "1 in" in dlg.heights_note.text()
    dlg.own_heights.setChecked(True)
    dlg.safe_z.setValue(2.0)
    assert dlg.heights()[0] == pytest.approx(50.8)


def test_material_setup_is_reachable_from_the_toolpath_dialog_and_changes_what_it_shows(rwin):
    win, robot = rwin
    c = win.ctrl
    dlg = ToolpathDialog(c, "profile", None, win)

    def hook(d):
        if isinstance(d, MaterialDialog):
            d.form.widgets["safe_z"].setValue(c.units.to_display(33.0))
    robot.hook = hook
    dlg._material_setup()
    assert c.project.material.safe_z == 33.0 and dlg.safe_z.value() == 33.0 and "33 mm" in dlg.heights_note.text()


def test_material_setup_refuses_a_clearance_above_the_safe_z(rwin):
    win, robot = rwin
    c = win.ctrl
    before = (c.project.material.safe_z, c.project.material.clearance)
    dlg = MaterialDialog(c, win)
    dlg.form.widgets["safe_z"].setValue(5.0)
    dlg.form.widgets["clearance"].setValue(9.0)
    dlg.accept()
    assert (c.project.material.safe_z, c.project.material.clearance) == before
    assert any("cannot be higher than the safe Z" in text for _, text in robot.messages)
    assert dlg.result() != QDialog.Accepted                                       # (the dialog stays open)
    dlg.form.widgets["clearance"].setValue(2.0)
    dlg.accept()
    assert (c.project.material.safe_z, c.project.material.clearance) == (5.0, 2.0)


def test_the_toolpath_panel_the_toolbar_and_the_gcode_window_show_the_heights(rwin):
    win, robot = rwin
    c, t, objs = parts(win)
    c.project.material.safe_z = 14.0
    c.changed.emit()
    process()
    panel = win.toolpaths_panel
    assert "safe Z 14 mm" in panel.material.text() and "18 mm thick" in panel.material.text()
    assert "material" in win.quick and win.quick["material"].toolTip().startswith("Material and heights")
    robot.hook = lambda d: d.form.widgets["safe_z"].setValue(c.units.to_display(22.0)) if isinstance(d, MaterialDialog) else None
    win.quick["material"].trigger()
    process()
    assert c.project.material.safe_z == 22.0 and "safe Z 22 mm" in panel.material.text()
    exp = ExportDialog(c, win)
    assert "safe Z 22 mm" in exp.heights.text() and "home" in exp.heights.text()
    robot.hook = lambda d: d.form.widgets["safe_z"].setValue(c.units.to_display(31.0)) if isinstance(d, MaterialDialog) else None
    exp._material_setup()
    assert "safe Z 31 mm" in exp.heights.text() and "G0 Z31" in exp.text.toPlainText().replace("G00", "G0")
    assert robot.problems() == []


def test_the_panel_follows_the_material_of_the_open_tab(rwin):
    win, _ = rwin
    c1, t, objs = parts(win)
    c1.project.material.safe_z = 17.0
    c1.changed.emit()
    win.new_project()
    process()
    c2 = win.ctrl
    c2.project.material.safe_z = 6.0
    c2.changed.emit()
    assert "safe Z 6 mm" in win.toolpaths_panel.material.text()
    win.activate(c1)
    process()
    assert "safe Z 17 mm" in win.toolpaths_panel.material.text()

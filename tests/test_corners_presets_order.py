"""Corners straight through (G64) or with a stop (G61), presets of toolpaths with sums, and the order of the sheets."""
import pytest
from conftest import add_tp, end_mill, make_project

from iyicam.builtins import ensure_registered
from iyicam.cam import timing, tp_presets
from iyicam.cam.moves import Feed, Rapid
from iyicam.core import shapes
from iyicam.core.document import Project, project_from_dict
from iyicam.core.registry import REGISTRY
from iyicam.service import ServiceHost


@pytest.fixture(autouse=True)
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("IYICAM_HOME", str(tmp_path / "home"))


def test_the_corner_mode_is_written_and_the_estimate_follows_it():
    p, s, objs = make_project([[shapes.rectangle(10 + 60 * (i % 6), 10 + 60 * (i // 6), 50, 50)] for i in range(18)], size=(400, 300))
    add_tp(p, "profile", end_mill(6), {"through": True}, objs)
    h = ServiceHost()
    times, codes = {}, {}
    for cm in ("", "blend", "exact"):
        p.material.corner_mode = cm
        lines = h.handle("gcode", {"project": p.to_dict(), "post": "mach3", "recalculate": True})["files"][0]["text"].splitlines()
        codes[cm] = [ln for ln in lines if ln.strip() in ("G64", "G61")]
        times[cm] = h.handle("compute", {"project": p.to_dict(), "preview": False})["results"][0]["stats"]["time_s"]
    assert codes == {"": [], "blend": ["G64"], "exact": ["G61"]}
    assert times["blend"] < times[""] < times["exact"]
    assert project_from_dict(p.to_dict()).material.corner_mode == "exact"
    square = [Rapid(0, 0, 0), Feed(100, 0, 0, 3000), Feed(100, 100, 0, 3000), Feed(0, 100, 0, 3000), Feed(0, 0, 0, 3000)]
    flat = sum([100] * 4) / 50
    assert timing.duration(square, timing.Machine(accel=1e7, corners="blend")) == pytest.approx(flat, rel=0.01)   # (no slowing at all)


def test_a_header_that_says_g64_itself_is_not_written_twice():
    import copy
    from iyicam.post import editing
    from iyicam.post.export import post_toolpaths
    from iyicam.cam.job import compute_all, sheet_toolpaths
    ensure_registered(REGISTRY)
    p, s, objs = make_project([[shapes.rectangle(10, 10, 100, 60)]], size=(500, 300))
    add_tp(p, "profile", end_mill(6), {"through": True}, objs)
    p.material.corner_mode = "blend"
    d = copy.deepcopy(editing.info(REGISTRY.posts["mach3"]))
    d["id"], d["name"] = "own", "own"
    d["blocks"]["header"] = "G21 G90 G64 P0.05"
    text = post_toolpaths(p, s, sheet_toolpaths(p, compute_all(p, REGISTRY), s), editing.build_post(d), "x")[0][1]
    assert text.count("G64") == 1


def test_a_preset_keeps_what_was_chosen_and_works_out_its_sums():
    ensure_registered(REGISTRY)
    p, s, objs = make_project([[shapes.rectangle(10, 10, 100, 60)]], size=(500, 300))
    p.material.thickness = 18
    t = add_tp(p, "profile", end_mill(6), {"through": False, "cut_depth": 18.0, "side": "inside"}, objs)
    op = REGISTRY.operations["profile"]
    keys = {f["key"] for f in tp_presets.fields(t, op.params())}
    assert {"name", "tool", "params.cut_depth", "params.side", "safe_z"} <= keys
    pre = tp_presets.save({"name": "Through + 0.3", "op": "profile",
                           "fields": {"name": {"value": "Cut {material_thickness} mm"}, "params.cut_depth": {"expr": "material_thickness + 0.3"},
                                      "params.side": {"value": "outside"}}})
    assert [x["name"] for x in tp_presets.load()] == ["Through + 0.3"]
    p.material.thickness = 12                            # (another job: the preset follows its material)
    other = add_tp(p, "profile", end_mill(3), {"cut_depth": 5.0, "side": "on"}, objs)
    notes = tp_presets.apply(pre, other, p, s, op.params())
    assert not notes and other.name == "Cut 12 mm" and other.params["cut_depth"] == pytest.approx(12.3) and other.params["side"] == "outside"
    assert other.tool.diameter == 3                      # (the tool was not kept: it stays)
    with pytest.raises(tp_presets.PresetError):
        tp_presets.save({"name": "bad", "fields": {"params.cut_depth": {"expr": "thickness_of_the_moon + 1"}}})
    with pytest.raises(tp_presets.PresetError):
        tp_presets.save({"name": "", "fields": {"name": {"value": "x"}}})
    assert tp_presets.delete(pre["id"]) and tp_presets.load() == []


def test_the_engine_lists_saves_applies_and_deletes_presets():
    p, s, objs = make_project([[shapes.rectangle(10, 10, 100, 60)]], size=(500, 300))
    t = add_tp(p, "profile", end_mill(6), {"through": True}, objs)
    from iyicam.core.document import toolpath_to_dict
    h = ServiceHost()
    info = h.handle("tp_presets", {"project": p.to_dict(), "toolpath": toolpath_to_dict(t)})
    assert info["presets"] == [] and info["fields"] and info["variables"]
    saved = h.handle("tp_preset_save", {"preset": {"name": "Deep", "op": "profile", "fields": {"params.cut_depth": {"expr": "material_thickness * 2"}}}})
    pid = saved["saved"]["id"]
    out = h.handle("tp_preset_apply", {"project": p.to_dict(), "toolpath": toolpath_to_dict(t), "id": pid})
    assert out["toolpath"]["params"]["cut_depth"] == pytest.approx(p.material.thickness * 2)
    assert h.handle("tp_preset_save", {"preset": {"name": "x", "fields": {"nope": {"value": 1}}}})["error"] == "bad_request"
    assert h.handle("tp_preset_delete", {"id": pid})["presets"] == []
    assert h.handle("tp_preset_apply", {"project": p.to_dict(), "toolpath": toolpath_to_dict(t), "id": pid})["error"] == "not_found"


def test_the_toolpath_window_saves_and_applies_presets(tmp_path, monkeypatch):
    pytest.importorskip("PySide6.QtWidgets")
    from gui_robot import rwin_gen
    from test_cutedit import parts
    from iyicam.gui.dialogs import PresetSaveDialog, ToolpathDialog
    gen = rwin_gen(tmp_path / "h", monkeypatch)
    win, robot = next(gen)
    try:
        c, t, objs = parts(win)

        def hook(d):
            if isinstance(d, PresetSaveDialog):
                d.name.setText("Mine")
                for r, f in enumerate(d.fields):
                    if f["key"] == "params.cut_depth":
                        d.table.item(r, 0).setCheckState(__import__("PySide6.QtCore").QtCore.Qt.Checked)
                        d.table.item(r, 2).setText("material_thickness + 1")
        robot.hook = hook
        dlg = ToolpathDialog(c, "profile", None, win)
        dlg.save_preset()
        assert [x["name"] for x in tp_presets.load()] == ["Mine"]
        dlg2 = ToolpathDialog(c, "profile", None, win)
        dlg2.form.set_values({"through": False})
        dlg2.preset_box.setCurrentIndex(dlg2.preset_box.findData(tp_presets.load()[0]["id"]))
        dlg2.apply_preset()
        assert dlg2.toolpath().params["cut_depth"] == pytest.approx(c.project.material.thickness + 1)
        dlg.close()
        dlg2.close()
    finally:
        gen.close()


def test_sheets_can_be_moved_up_and_down(tmp_path, monkeypatch):
    pytest.importorskip("PySide6.QtWidgets")
    from gui_robot import rwin_gen
    gen = rwin_gen(tmp_path / "h", monkeypatch)
    win, robot = next(gen)
    try:
        c = win.ctrl
        with c.edit("s", geometry=False):
            c.project.add_sheet("B")
            c.project.add_sheet("C")
        c.set_sheet(c.project.sheets[2].id)
        win.sheets_panel.move(-1)
        assert [s.name for s in c.project.sheets] == ["Sheet 1", "C", "B"]
        assert not c.move_sheet(c.project.sheets[0].id, -1)
        c.undo()
        assert [s.name for s in c.project.sheets] == ["Sheet 1", "B", "C"]
    finally:
        gen.close()

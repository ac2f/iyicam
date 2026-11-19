"""The time estimate of a machine, toolpaths kept in the file, layers of one sheet or group, ready sizes and templates."""
import math

import pytest
from conftest import add_tp, end_mill, make_project

from iyicam.builtins import ensure_registered
from iyicam.cam import store, timing
from iyicam.cam.job import compute_all, sheet_toolpaths
from iyicam.cam.moves import ArcMove, Dwell, Feed, Rapid
from iyicam.core import presets, shapes
from iyicam.core.document import Layer, Project, project_from_dict
from iyicam.core.registry import REGISTRY, Registry
from iyicam.plugins.api import PluginAPI
from iyicam.service import ServiceHost


# -- the time of a machine -----------------------------------------------------------------------------------
def test_a_long_straight_cut_is_near_length_over_feed_and_corners_cost_time():
    line = [Rapid(0, 0, 5), Feed(0, 0, 0, 1000), Feed(2000, 0, 0, 6000)]
    t = timing.duration(line, timing.Machine(accel=200))
    assert 2000 / 100 < t < 2000 / 100 * 1.08                 # (2000 mm at 100 mm/s: 20 s and a little for the speed up and down)
    square = [Rapid(0, 0, 5), Feed(0, 0, 0, 1000), Feed(50, 0, 0, 6000), Feed(50, 50, 0, 6000), Feed(0, 50, 0, 6000), Feed(0, 0, 0, 6000)]
    flat = timing.duration(square, timing.Machine(accel=1e6, corner_dev=1.0))
    assert timing.duration(square, timing.Machine(accel=200)) > flat * 1.5     # (a small square: nearly all of it is speeding up and corners)


def test_the_time_falls_as_the_acceleration_grows_and_follows_the_scale():
    job = [Rapid(0, 0, 5)] + [Feed(10 * i % 70, 10 * i % 50, 0, 3000) for i in range(1, 60)]
    ts = [timing.duration(job, timing.Machine(accel=a)) for a in (50, 100, 200, 400, 4000)]
    assert ts == sorted(ts, reverse=True)
    assert timing.duration(job, timing.Machine(accel=200, scale=1.2)) == pytest.approx(timing.duration(job, timing.Machine(accel=200)) * 1.2)


def test_a_small_arc_cannot_keep_the_feed_and_a_pause_is_a_stop():
    big = [Rapid(0, 0, 0), Feed(100, 0, 0, 6000), ArcMove(100, 0, 0, 100, 50, True, 6000)]      # a circle of 50 mm radius
    small = [Rapid(0, 0, 0), Feed(100, 0, 0, 6000), ArcMove(100, 0, 0, 100, 2, True, 6000)]
    m = timing.Machine(accel=200)
    assert timing.duration(small[:2] + [small[2]], m) / (2 * math.pi * 2) > timing.duration(big, m) / (2 * math.pi * 50)
    base = [Rapid(0, 0, 0), Feed(100, 0, 0, 3000), Feed(200, 0, 0, 3000)]
    paused = [Rapid(0, 0, 0), Feed(100, 0, 0, 3000), Dwell(3.0), Feed(200, 0, 0, 3000)]
    assert timing.duration(paused, m) > timing.duration(base, m) + 3.0                # (3 s, and the stop and start again)


def test_the_calibration_finds_the_acceleration_that_gives_the_measured_time():
    job = [Rapid(0, 0, 5), Feed(0, 0, -3, 1000)] + [Feed(300 * (i % 2), 40 * (i // 2 + 1), -3, 4800) for i in range(60)]
    real = timing.duration(job, timing.Machine(accel=170))
    mc = timing.calibrate(job, real, timing.Machine(accel=900))
    assert mc.accel == pytest.approx(170, rel=0.01) and mc.scale == 1.0
    mc = timing.calibrate(job, real * 0.5, timing.Machine(accel=900))              # (quicker than any acceleration can give)
    assert timing.duration(job, mc) == pytest.approx(real * 0.5, rel=1e-6)
    assert timing.parse_duration("1:23:45") == 5025 and timing.parse_duration("83,5") == 5010 and timing.parse_duration("x") is None


def test_the_estimate_differs_by_the_kind_of_job_the_way_a_machine_does():
    ensure_registered(REGISTRY)
    few = make_project([[shapes.rectangle(10, 10, 900, 500)]], size=(1000, 600))
    many = make_project([[shapes.rectangle(10 + 60 * (i % 15), 10 + 60 * (i // 15), 50, 50)] for i in range(60)], size=(1000, 600))
    ratios = []
    for p, s, objs in (few, many):
        add_tp(p, "profile", end_mill(6), {"through": True}, objs)
        tps = sheet_toolpaths(p, compute_all(p, REGISTRY), s)
        old = sum(tp.stats(timing.Machine(accel=1e9, corner_dev=1e3, z_rapid_rate=0)).time_s for tp in tps)
        ratios.append(sum(tp.stats(p.material.machine()).time_s for tp in tps) / old)
    assert 1.0 < ratios[0] < ratios[1]                  # (a few long cuts lose little, many small parts lose a lot)


def test_the_machine_of_a_project_is_kept_in_the_file():
    p = Project()
    p.material.accel, p.material.time_scale, p.material.z_rapid_rate = 333.0, 1.17, 2500.0
    q = project_from_dict(p.to_dict())
    assert (q.material.accel, q.material.time_scale, q.material.z_rapid_rate) == (333.0, 1.17, 2500.0)
    d = p.to_dict()
    d["material"]["accel"] = -5
    d["material"]["time_scale"] = "x"
    r = project_from_dict(d)
    assert r.material.accel == 200.0 and r.material.time_scale == 1.0            # (a bad number is the default)


def test_the_service_calibrates_from_a_measured_time():
    p, s, objs = make_project([[shapes.rectangle(10 + 60 * (i % 8), 10 + 60 * (i // 8), 50, 50)] for i in range(24)], size=(600, 300))
    add_tp(p, "profile", end_mill(6), {"through": True}, objs)
    h = ServiceHost()
    est = h.handle("calibrate_time", {"project": p.to_dict(), "seconds": 3000})
    assert est["estimate_after"] == pytest.approx(3000, rel=0.02) and est["accel"] > 0
    assert h.handle("calibrate_time", {"project": p.to_dict(), "seconds": 0})["error"] == "bad_request"


# -- toolpaths kept in the file ----------------------------------------------------------------------------------
def test_a_file_keeps_its_toolpaths_as_they_were_calculated(tmp_path):
    ensure_registered(REGISTRY)
    p, s, objs = make_project([[shapes.rectangle(10, 10, 100, 60)], [shapes.circle((200, 60), 20)]], size=(500, 300))
    add_tp(p, "profile", end_mill(6), {"through": True}, objs)
    res = compute_all(p, REGISTRY)
    p.save(tmp_path / "a.icam", store.results_to_dict(p, res))
    q = Project.load(tmp_path / "a.icam")
    assert store.usable(q, q.calc_cache)
    back = store.results_from_dict(q.calc_cache)
    key = next(iter(res))
    same = lambda a, b: type(a) is type(b) and all(abs(float(getattr(a, f)) - float(getattr(b, f))) < 1e-8 for f in a.__slots__)
    assert len(res[key].toolpaths[0].moves) == len(back[key].toolpaths[0].moves)
    assert all(same(a, b) for a, b in zip(res[key].toolpaths[0].moves, back[key].toolpaths[0].moves))
    assert res[key].order == back[key].order and res[key].toolpaths[0].cuts[0]["key"] == back[key].toolpaths[0].cuts[0]["key"]
    # changed by hand outside: no longer the toolpaths of this project
    q.sheets[0].vectors[0].contours = q.sheets[0].vectors[1].contours
    assert not store.usable(q, q.calc_cache)
    assert store.usable(Project.load(tmp_path / "a.icam"), Project.load(tmp_path / "a.icam").calc_cache)
    q2 = Project.load(tmp_path / "a.icam")
    q2.name, q2.toolpaths[0].name = "other name", "renamed"
    assert store.usable(q2, q2.calc_cache)                       # (a name is not what the toolpath is made of)


def test_the_desktop_opens_a_file_without_calculating_it_again(tmp_path, monkeypatch):
    pytest.importorskip("PySide6.QtWidgets")
    from gui_robot import rwin_gen
    from test_cutedit import parts
    gen = rwin_gen(tmp_path / "h", monkeypatch)
    win, _ = next(gen)
    try:
        c, t, objs = parts(win, auto=False)
        c.recalculate()
        c.set_manual_order(t.id, 0, [f"{objs[2].id}:0"])           # (a hand-made order)
        c.recalculate()
        order = c.cut_order(t.id)
        path = tmp_path / "k.icam"
        c.save_project(path)
        calls = []
        real = c.recalculate
        c.recalculate = lambda *a, **k: (calls.append(1), real(*a, **k))[1]
        c.open_project(path)
        assert not calls and c.results and not c.stale
        assert c.cut_order(c.project.toolpaths[0].id) == order
        c.stale = True
        c.save_project(path)                                       # (nothing kept while the toolpaths are old)
        assert not Project.load(path).calc_cache
    finally:
        gen.close()


# -- layers of a sheet or a group ---------------------------------------------------------------------------------
def test_a_layer_of_one_sheet_or_group_is_not_on_the_others():
    p = Project()
    a = p.sheets[0]
    b = p.add_sheet("B")
    c = p.add_sheet("C")
    b.group = "G"
    c.group = "G"
    l_all, l_a, l_g = p.layers[0], p.add_layer("A only"), p.add_layer("Group")
    l_a.sheet_ids = [a.id]
    l_g.sheet_group = "G"
    assert [l.name for l in p.layers_for(a)] == [l_all.name, "A only"]
    assert [l.name for l in p.layers_for(b)] == [l_all.name, "Group"] and p.layers_for(b) == p.layers_for(c)
    q = project_from_dict(p.to_dict())
    assert q.layers[1].sheet_ids == [a.id] and q.layers[2].sheet_group == "G" and q.layers[0].sheet_ids is None


def test_the_desktop_hides_the_layers_and_vectors_of_other_sheets(tmp_path, monkeypatch):
    pytest.importorskip("PySide6.QtWidgets")
    from gui_robot import process, rwin_gen
    from test_cutedit import parts
    from iyicam.gui.dialogs import LayerScopeDialog
    gen = rwin_gen(tmp_path / "h", monkeypatch)
    win, robot = next(gen)
    try:
        c, t, objs = parts(win)
        with c.edit("sheet", geometry=False):
            b = c.project.add_sheet("B")
        extra = c.project.add_layer("Only on B")
        extra.sheet_ids = [b.id]
        c.set_layer(extra.id)
        process()
        assert c.layer.id != extra.id and extra.id not in [c.project.layers_for(c.sheet)[i].id for i in range(len(c.project.layers_for(c.sheet)))]
        names = [win.layers_panel.tree.topLevelItem(i).text(0) for i in range(win.layers_panel.tree.topLevelItemCount())]
        assert not any("Only on B" in n for n in names)
        c.set_sheet(b.id)
        process()
        names = [win.layers_panel.tree.topLevelItem(i).text(0) for i in range(win.layers_panel.tree.topLevelItemCount())]
        assert any("Only on B" in n for n in names)
        dlg = LayerScopeDialog(c, extra, win)
        assert dlg.some.isChecked() and dlg.chosen()[0] == [b.id]
        dlg.every.setChecked(True)
        assert dlg.chosen() == (None, "")
        dlg.close()
    finally:
        gen.close()


# -- ready sizes and ways to start a project ------------------------------------------------------------------------
def test_sizes_can_be_edited_and_plugins_add_and_hide_sizes_and_templates():
    reg = Registry()
    assert presets.sizes(None, reg)[0][0] == "2800 x 2100"
    mine = [["Door", 900, 2100], ["bad", -1, 5], ["Wall", "1200", "2400"]]
    assert [s[0] for s in presets.sizes(mine, reg)] == ["Door", "Wall"]
    api = PluginAPI("test", reg)
    api.add_sheet_preset("Plugin size", 500, 400)
    api.remove_sheet_preset("Door")
    assert [s[0] for s in presets.sizes(mine, reg)] == ["Wall", "Plugin size"]
    made = []
    api.add_new_project_template("kit", "Kitchen kit", lambda p, v: (made.append(v), p.add_sheet("Kit sheet", 100, 100)),
                                 [PluginAPI.Param("n", "Count", "int", 3)], "A sheet for the kit")
    assert [t["id"] for t in presets.templates(reg)] == [presets.BLANK, "", "kit"]
    p = presets.create(1000, 500, 12, "kit", {"n": 5}, reg)
    assert made == [{"n": 5}] and [s.name for s in p.sheets][-1] == "Kit sheet" and p.sheets[0].width == 1000
    blank = presets.create(700, 300, 10, "", None, reg)
    assert len(blank.sheets) == 1 and not blank.toolpaths and blank.material.thickness == 10
    with pytest.raises(KeyError):
        presets.create(1, 1, 1, "nope", None, reg)
    reg.remove_source("test")
    assert not reg.new_project_templates and not reg.sheet_presets


def test_the_engine_makes_a_new_project_blank_or_from_a_template():
    h = ServiceHost()
    s = h.handle("schema", {})
    assert s["new_project"]["templates"][0]["id"] == "" and s["new_project"]["presets"]
    r = h.handle("new_project", {"width": 800, "height": 400, "thickness": 9})
    assert r["project"]["sheets"][0]["width"] == 800 and r["project"]["toolpaths"] == []
    assert h.handle("new_project", {"template": "nope"})["error"] == "bad_request"


def test_the_new_project_window_offers_the_sizes_and_the_templates(tmp_path, monkeypatch):
    pytest.importorskip("PySide6.QtWidgets")
    from gui_robot import rwin_gen
    from iyicam.gui.dialogs import PresetsDialog
    gen = rwin_gen(tmp_path / "h", monkeypatch)
    win, robot = next(gen)
    try:
        c = win.ctrl
        api = PluginAPI("t", c.registry)
        api.add_new_project_template("kit", "Kit", lambda p, v: p.add_sheet("Kit sheet", 100, 100), [])
        titles = []
        robot.hook = lambda d: titles.append(d.windowTitle())
        win.new_project()
        assert "New Project" in titles
        assert win.ctrl.project.sheets[0].width == 600.0
        d = PresetsDialog(c, win)
        d.table.item(0, 0).setText("My size")
        d.table.item(0, 1).setText("1000+200")
        d.accept()
        assert c.sheet_sizes()[0] == ("My size", 1200.0, 2100.0) and c.settings.sheet_presets[0][0] == "My size"
        c.registry.remove_source("t")
    finally:
        gen.close()

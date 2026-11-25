"""The operations of the job features of the web app (iyicam/service/ops_job.py): job report, backplot, packages and
templates, stock of plates, the cut order panel, the 2D simulation and the Python console.  They are plain JSON in and out."""
import json

import pytest
from conftest import add_tp, end_mill, make_project

from iyicam.core import shapes
from iyicam.service import ServiceError, ops


@pytest.fixture
def job():
    p, s, objs = make_project([[shapes.rectangle(10, 10, 100, 60)], [shapes.circle((200, 60), 30)],
                               [shapes.rectangle(250, 20, 80, 40)]], size=(500, 300))
    add_tp(p, "profile", end_mill(6), {"through": True}, objs)
    d = p.to_dict()
    return d, d["sheets"][0]["id"], d["toolpaths"][0]["id"]


def strict(obj):
    return json.loads(json.dumps(obj, allow_nan=False))


def test_every_op_is_an_op_of_ops_py():
    from iyicam.service import ops_job
    for name in ops_job.__all__:
        assert callable(getattr(ops, name))


def test_the_job_report_lists_the_toolpaths(job):
    d, _, _ = job
    r = ops.op_job_report({"project": d})
    assert r["name"].endswith("_report.html") and "<table>" in r["html"] and "End Mill" in r["html"]
    assert "in</td>" in ops.op_job_report({"project": d, "inch": True})["html"]


def test_backplot_reads_gcode_over_the_sheet(job):
    d, sid, _ = job
    r = ops.op_backplot({"project": d, "sheet": sid, "text": "G21 G90\nT1 M6\nG0 X10 Y10 Z5\nG1 Z-2 F500\nG1 X60 Y10\nG1 X60 Y50\n"})
    assert r["tools"][0]["number"] == 1 and r["preview"]["cuts"] and r["stats"]["feed_length"] > 90
    strict(r)
    with pytest.raises(ServiceError):
        ops.op_backplot({"project": d, "sheet": sid})


def test_packages_are_kept_made_from_toolpaths_and_put_on_vectors(job):
    d, sid, tid = job
    assert ops.op_packages({})["packages"] == []
    r = ops.op_package_from_toolpaths({"project": d, "toolpaths": [tid], "name": "Contour"})
    pid = r["saved"][0]
    assert [p["name"] for p in r["packages"]] == ["Contour"] and r["variables"] and r["operations"]
    info = ops.op_package_info({"id": pid, "project": d, "sheet": sid})
    assert info["group"].startswith("Contour S1-") and info["package"]["items"][0]["asked"]
    vids = [v["id"] for v in d["sheets"][0]["vectors"]][:2]
    a = ops.op_package_apply({"id": pid, "project": d, "sheet": sid, "selection": {"mode": "vectors", "vectors": vids},
                              "values": {"0": {"cut_depth": 5}}, "names": {"0": "Cut {package}"}})
    assert len(a["toolpaths"]) == 1 and a["toolpaths"][0]["params"]["cut_depth"] == 5 and a["group"]
    assert a["toolpaths"][0]["selection"]["vectors"] == vids and a["toolpaths"][0]["name"] == "Cut Contour"
    with pytest.raises(ServiceError):
        ops.op_package_apply({"id": pid, "project": d, "sheet": sid, "selection": {"mode": "vectors", "vectors": []}})
    items = ops.op_package_items({"project": d, "toolpaths": [tid], "tool": info["package"]["tool"]})["items"]
    assert items[0]["op"] == "profile" and items[0]["tool"] is None and "cut_depth" in items[0]["ask"]
    exp = ops.op_package_export({"id": pid})
    assert exp["name"] == "Contour.icampkg"
    back = ops.op_package_import({"text": exp["text"]})
    assert len(back["packages"]) == 2 and back["imported"][0]["id"] != pid
    assert len(ops.op_package_delete({"id": pid})["packages"]) == 1
    with pytest.raises(ServiceError):
        ops.op_package_delete({"id": "nope"})
    with pytest.raises(ServiceError):
        ops.op_package_import({"text": "[1, 2]"})
    p = ops.op_package_save({"package": {"name": "Mine", "tool": None, "items": []}})
    assert len(p["packages"]) == 2


def test_a_package_on_layers_uses_the_layers_of_its_toolpaths(job):
    d, sid, tid = job
    layer = d["layers"][0]
    d["toolpaths"][0]["selection"] = {"mode": "layers", "vectors": [], "layers": [layer["id"]]}
    pid = ops.op_package_from_toolpaths({"project": d, "name": "On layers"})["saved"][0]
    a = ops.op_package_apply({"id": pid, "project": d, "sheet": sid, "selection": {"mode": "layers", "layers": []}})
    assert a["toolpaths"][0]["selection"]["layers"] == [layer["id"]]


def test_a_template_keeps_the_layers_by_name(job):
    d, _, tid = job
    d["toolpaths"][0]["selection"] = {"mode": "layers", "vectors": [], "layers": [d["layers"][0]["id"]]}
    t = ops.op_template_save({"project": d})
    assert t["count"] == 1 and "layer_names" in t["text"]
    from iyicam.core.document import Project
    empty = Project("x").to_dict()
    r = ops.op_template_load({"project": empty, "text": t["text"]})
    assert len(r["project"]["toolpaths"]) == 1 and r["project"]["toolpaths"][0]["id"] != tid
    assert r["project"]["toolpaths"][0]["selection"]["mode"] == "layers"
    assert d["layers"][0]["name"] in [l["name"] for l in r["project"]["layers"]]
    with pytest.raises(ServiceError):
        ops.op_template_load({"project": empty, "text": "{}"})
    with pytest.raises(ServiceError):
        ops.op_template_save({"project": empty})


def test_stock_plates_from_shapes_vectors_and_leftovers(job):
    d, sid, _ = job
    pl = ops.op_stock_plate({"shape": "circle", "diameter": 200, "quantity": 2})["plate"]
    assert pl["quantity"] == 2 and pl["outline"] and pl["id"]
    left = ops.op_stock_leftover({"project": d, "sheet": sid, "clearance": 4})
    assert left["plates"] and all(p["leftover"] and p["id"] for p in left["plates"])
    p, s, objs = make_project([[shapes.rectangle(0, 0, 100, 50)]], size=(300, 200))
    r = ops.op_stock_from_vectors({"project": p.to_dict(), "sheet": s.id, "selection": [objs[0].id]})
    assert len(r["plates"]) == 1 and r["plates"][0]["width"] == 100
    assert ops.op_stock_from_vectors({"project": p.to_dict(), "sheet": s.id, "selection": []})["plates"] == []
    out = ops.op_stock_export({"plates": r["plates"]})
    back = ops.op_stock_import({"text": out["text"]})
    assert back["plates"][0]["width"] == 100 and back["plates"][0]["id"] != r["plates"][0]["id"]
    with pytest.raises(ServiceError):
        ops.op_stock_import({"text": "{}"})
    with pytest.raises(ServiceError):
        ops.op_stock_plate({"shape": "star"})


def test_cut_order_lists_the_cuts_and_previews_a_change(job):
    d, sid, tid = job
    r = ops.op_cut_order({"project": d, "sheet": sid, "toolpath": tid, "options": True})
    assert [t["id"] for t in r["toolpaths"]] == [tid] and len(r["cuts"]) == 3 and r["options"]["strategies"][0]["group"] == "Automatic"
    assert r["settings"]["strategy"] == "auto" and "order" not in r["settings"] and r["route"] > 0
    strict(r)
    only = ops.op_cut_order({"project": d, "sheet": sid})
    assert only["toolpaths"] and "cuts" not in only
    pv = ops.op_cut_order({"project": d, "sheet": sid, "toolpath": tid, "changes": {"strategy": "rows_tl"}, "smart_starts": True, "polylines": True})
    assert pv["preview"]["route_before"] == r["route"] and pv["settings"]["strategy"] == "rows_tl" and pv["cuts"][0]["poly"]
    assert d["toolpaths"][0]["sequence"].get("strategy") in (None, "auto")      # (a preview changes nothing)


def test_cut_order_edits_the_order_directions_and_start_points(job):
    d, sid, tid = job
    base = {"project": d, "sheet": sid, "toolpath": tid}
    cuts = ops.op_cut_order(base)["cuts"]
    last = cuts[-1]["key"]
    e = ops.op_cut_edit({**base, "action": "manual_order", "keys": [last], "index": 0})
    assert e["ok"] and e["project"]["toolpaths"][0]["sequence"]["order"][0] == last
    assert e["project"]["toolpaths"][0]["sequence"]["auto_order"] is False
    again = ops.op_cut_order({**base, "project": e["project"]})
    assert again["cuts"][0]["key"] == last
    e2 = ops.op_cut_edit({**base, "action": "sequence", "changes": {"direction": "cw"}, "reset": True})
    assert e2["project"]["toolpaths"][0]["sequence"]["direction"] == "cw" and e2["project"]["toolpaths"][0]["sequence"]["order"] == []
    # a start point at a corner, then moved along the cut, then given back
    k = cuts[0]["key"]
    e3 = ops.op_cut_edit({**base, "action": "place", "key": k, "place": "ne"})
    vec = next(v for v in e3["project"]["sheets"][0]["vectors"] if v["id"] == k.split(":")[0])
    assert e3["changed"] and vec["locked_starts"] == [0]
    s0 = vec["contours"][0]["start"]
    e4 = ops.op_cut_edit({**base, "project": e3["project"], "action": "move_start", "key": k, "delta": 10})
    vec4 = next(v for v in e4["project"]["sheets"][0]["vectors"] if v["id"] == k.split(":")[0])
    assert vec4["contours"][0]["start"] != s0
    e5 = ops.op_cut_edit({**base, "project": e4["project"], "action": "release", "key": k})
    assert not next(v for v in e5["project"]["sheets"][0]["vectors"] if v["id"] == k.split(":")[0]).get("locked_starts")
    e6 = ops.op_cut_edit({**base, "action": "start_at", "key": k, "point": [10, 10]})
    assert e6["ok"]
    assert ops.op_cut_edit({**base, "action": "optimise"})["project"]
    assert ops.op_cut_edit({**base, "action": "smart_one", "key": k})["ok"] in (True, False)
    with pytest.raises(ServiceError):
        ops.op_cut_edit({**base, "action": "dance"})
    with pytest.raises(ServiceError):
        ops.op_cut_edit({**base, "toolpath": "nope", "action": "release"})


def test_the_simulation_timeline_has_times_and_depths(job):
    d, sid, tid = job
    s = ops.op_sim_timeline({"project": d, "sheet": sid})
    n = s["moves"]
    assert n > 10 and len(s["x"]) == len(s["y"]) == len(s["z"]) == len(s["t"]) == n + 1 and len(s["c"]) == len(s["w"]) == len(s["k"]) == n
    assert s["total"] > 0 and abs(s["t"][-1] - s["total"]) < 0.01 and 2 in s["c"] and -1 in s["c"]
    assert all(b >= a - 1e-6 for a, b in zip(s["t"], s["t"][1:]))
    strict(s)
    played = ops.op_sim_timeline({"project": d, "sheet": sid, "toolpaths": [tid], "backplot": "G21\nG1 X5 Y5 Z-3 F300\nG1 X50 Y5\n"})
    assert played["moves"] > n and len(played["tools"]) == 2 and played["diameters"][0] == 6
    d["toolpaths"] = []
    with pytest.raises(ServiceError):
        ops.op_sim_timeline({"project": d, "sheet": sid})


def test_the_console_runs_python_on_the_project(job):
    d, sid, _ = job
    ops.op_console_reset()
    base = {"project": d, "sheet": sid}
    n = len(d["sheets"][0]["vectors"])
    r = ops.op_console_run({**base, "line": "add(shapes.circle((100, 100), 25))"})
    assert r["changed"] and len(r["project"]["sheets"][0]["vectors"]) == n + 1 and len(r["selection"]) == 1
    r = ops.op_console_run({**base, "line": "print(len(sheet().vectors), len(project.sheets))"})
    assert not r["changed"] and r["output"].strip() == f"{n} 1" and "project" not in r
    ops.op_console_run({**base, "line": "answer = 41"})
    assert ops.op_console_run({**base, "line": "print(answer + 1)"})["output"].strip() == "42"        # (variables are kept)
    assert ops.op_console_run({**base, "line": "for i in range(2):"})["more"] is True
    assert ops.op_console_run({**base, "line": "    print(i)"})["more"] is True
    assert ops.op_console_run({**base, "line": ""})["output"].split() == ["0", "1"]
    err = ops.op_console_run({**base, "line": "1/0"})
    assert "ZeroDivisionError" in err["output"] and not err["changed"]
    r = ops.op_console_run({**base, "line": "r = run('edit.doctor'); print(sorted(r))"})
    assert "messages" in r["output"]
    r = ops.op_console_run({**base, "selection": [d["sheets"][0]["vectors"][0]["id"]], "line": "print(len(selected()), ctrl.layer_id == project.layers[0].id)"})
    assert r["output"].strip() == "1 True"
    with pytest.raises(ServiceError):
        ops.op_console_run({**base})
    assert "Python console" in ops.op_console_reset()["banner"]
    assert "answer" not in ops.op_console_run({**base, "line": "print('answer' in dir())"})["output"].replace("False", "")

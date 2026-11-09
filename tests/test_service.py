"""The service layer: the engine behind a JSON interface, with no window and no Controller.

What matters most is that it gives the SAME answers as the program itself: G-code equal to the command line's,
a command equal to running it directly, a nesting equal to ``iyicam nest``.  Everything it returns must be plain
JSON (no NaN, no objects), and it must never raise for a request that is wrong.
"""
import base64
import json
import struct
import time
import zlib

import pytest
from conftest import add_tp, end_mill, make_project

from iyicam import cli
from iyicam.core import shapes
from iyicam.core.commands import CommandContext, run_command
from iyicam.core.document import Project
from iyicam.core.registry import REGISTRY
from iyicam.service import API_VERSION, ServiceError, ServiceHost
from iyicam.service.jobs import JobManager
from iyicam.service.png import encode_png
from iyicam.service.preview import toolpath_preview


@pytest.fixture
def host():
    return ServiceHost()


@pytest.fixture
def job_project():
    p, s, objs = make_project([[shapes.rectangle(10, 10, 100, 60), shapes.circle((60, 40), 15)],
                               [shapes.circle((200, 60), 30)], [shapes.rectangle(250, 20, 80, 40)]], size=(500, 300))
    add_tp(p, "profile", end_mill(6), {"through": True}, objs)
    return p


def strict_json(obj):
    """Round trip through strict JSON: fails on NaN / infinity and on anything that is not plain data."""
    return json.loads(json.dumps(obj, allow_nan=False))


def b64(data: bytes) -> str:
    return base64.b64encode(data).decode()


# -- schema and plain data ---------------------------------------------------------------------

def test_ping_and_schema(host):
    pong = host.handle("ping")
    assert pong["ok"] and pong["api"] == API_VERSION
    s = strict_json(host.handle("schema"))
    assert {o["id"] for o in s["operations"]} >= {"profile", "pocket", "drill", "engrave", "vcarve"}
    assert {"xform.move", "xform.rotate", "edit.weld", "edit.offset", "create.rectangle"} <= {c["id"] for c in s["commands"]}
    assert {"mach3", "ncstudio", "fanuc"} <= {p["id"] for p in s["posts"]}
    assert {"dxf", "svg", "icam"} <= {e["extension"] for e in s["exporters"]}
    assert {"dxf", "svg", "icam"} <= {x for i in s["importers"] for x in i["extensions"]}
    assert s["tools"] and s["tools"][0]["diameter"] > 0
    assert ["end_mill", "End Mill"] in s["tool_types"]
    assert s["sequence"]["strategies"][0][0] == "smart" and "climb" in [d[0] for d in s["sequence"]["directions"]]
    assert s["sequence"]["defaults"]["smart_starts"] is False and "order" not in s["sequence"]["defaults"]
    prof = next(o for o in s["operations"] if o["id"] == "profile")
    side = next(p for p in prof["params"] if p["name"] == "side")
    assert side["kind"] == "choice" and side["choices"] and all(len(c) == 2 for c in side["choices"])
    assert any(p["visible"] for p in prof["params"])       # the show-when conditions travel too


def test_every_param_kind_in_the_schema_is_known(host):
    from iyicam.core.params import KINDS
    s = host.handle("schema")
    kinds = {p["kind"] for o in s["operations"] for p in o["params"]} | \
            {p["kind"] for c in s["commands"] for p in c["params"]}
    assert kinds <= set(KINDS)


def test_garbage_never_raises(host):
    for op, payload in [("nope", {}), ("compute", {}), ("compute", {"project": 5}), ("import", {}),
                        ("export", {"project": {"format": "x"}}), ("command", {"project": None}),
                        ("job.status", {"id": "zz"}), ("job.start", {"op": "ping"}), ("gcode", {"project": []}),
                        ("simulate", {"project": {}}), ("nest", {"project": "x"}), ("validate", {"project": {}})]:
        out = host.handle(op, payload)
        assert "error" in out and "message" in out, (op, payload, out)
    assert host.handle("compute", "not an object")["error"] == "bad_request"


def test_validate_round_trips(host, job_project):
    d = job_project.to_dict()
    out = host.handle("validate", {"project": d})
    assert out["ok"] and out["project"] == d and out["counts"]["vectors"] == 3
    bad = host.handle("validate", {"project": {**d, "format": "other"}})
    assert bad["error"] == "invalid_project"


# -- import / export -----------------------------------------------------------------------------

def test_dxf_export_then_import_keeps_the_drawing(host, job_project):
    d = job_project.to_dict()
    exp = host.handle("export", {"project": d, "format": "dxf"})
    assert len(exp["files"]) == 1 and exp["files"][0]["name"].endswith(".dxf")
    imp = host.handle("import", {"name": "back.dxf", "data_b64": exp["files"][0]["data_b64"]})
    assert "error" not in imp, imp
    assert imp["report"]["clean"] and len(imp["added"]) >= 3
    sheet = imp["project"]["sheets"][0]
    assert sheet["width"] >= 330 and sheet["height"] >= 70          # the sheet grew to hold the drawing
    strict_json(imp)


def test_svg_export_and_import(host, job_project):
    d = job_project.to_dict()
    svg = host.handle("export", {"project": d, "format": "svg"})["files"][0]
    assert base64.b64decode(svg["data_b64"]).lstrip().startswith(b"<")
    imp = host.handle("import", {"name": "x.svg", "data_b64": svg["data_b64"]})
    assert "error" not in imp and imp["added"]


def test_icam_import_is_validated(host, job_project):
    text = json.dumps(job_project.to_dict()).encode()
    imp = host.handle("import", {"name": "job.icam", "data_b64": b64(text)})
    assert imp["project"] == job_project.to_dict()
    assert host.handle("import", {"name": "job.icam", "data_b64": b64(b"{not json")})["error"] == "invalid_project"
    assert host.handle("import", {"name": "job.icam", "data_b64": b64(b'{"format": "nope"}')})["error"] == "invalid_project"


def test_import_errors_are_codes(host):
    assert host.handle("import", {"name": "a.xyz", "data_b64": b64(b"123")})["error"] == "unsupported_file"
    assert host.handle("import", {"name": "a.dxf", "data_b64": ""})["error"] == "empty_file"
    assert host.handle("import", {"name": "a.dxf", "data_b64": b64(b"this is not a dxf")})["error"] == "import_failed"


def test_import_into_an_existing_project(host, job_project):
    d = job_project.to_dict()
    exp = host.handle("export", {"project": d, "format": "dxf"})["files"][0]
    out = host.handle("import", {"name": "more.dxf", "data_b64": exp["data_b64"], "project": d})
    assert "error" not in out
    n_before = len(d["sheets"][0]["vectors"])
    assert len(out["project"]["sheets"][0]["vectors"]) == n_before + len(out["added"]) and out["added"]
    assert out["project"]["toolpaths"] == d["toolpaths"]            # the rest of the project is untouched


def test_export_selection_and_errors(host, job_project):
    d = job_project.to_dict()
    ids = [v["id"] for v in d["sheets"][0]["vectors"]]
    one = host.handle("export", {"project": d, "format": "svg", "selection": ids[:1]})["files"][0]
    allv = host.handle("export", {"project": d, "format": "svg"})["files"][0]
    assert len(base64.b64decode(one["data_b64"])) < len(base64.b64decode(allv["data_b64"]))
    assert host.handle("export", {"project": d, "format": "pdf"})["error"] == "unsupported_format"
    old = host.handle("export", {"project": d, "format": "dxf", "options": {"version": "R2000"}})
    assert "error" not in old
    icam = host.handle("export", {"project": d, "format": "icam"})["files"][0]
    assert json.loads(base64.b64decode(icam["data_b64"])) == d


def test_export_all_sheets_gives_a_file_each(host, job_project):
    job_project.add_sheet("Second", 200, 200)
    out = host.handle("export", {"project": job_project.to_dict(), "format": "dxf", "sheet": "all"})
    assert [f["name"] for f in out["files"]][0].endswith("_1.dxf") and len(out["files"]) == 2


# -- commands ---------------------------------------------------------------------------------------

def strip_ids(d):
    """The project with the ids of the vectors a command made renumbered (they are random)."""
    d = json.loads(json.dumps(d))
    for s in d["sheets"]:
        for i, v in enumerate(s["vectors"]):
            v["id"] = f"v{i}"
    return d


def run_direct(project_dict, cmd_id, selection, values):
    from iyicam.core.document import project_from_dict
    p = project_from_dict(project_dict)
    s = p.sheets[0]
    ctx = CommandContext(p, s, selection)
    run_command(REGISTRY.commands[cmd_id], ctx, values)
    return p.to_dict(), ctx


@pytest.mark.parametrize("cmd,values", [
    ("xform.move", {"dx": 12.5, "dy": -3}),
    ("xform.rotate", {"angle": 33.0}),
    ("xform.scale", {"sx": 150.0, "sy": 150.0}),
    ("xform.flip_h", {}),
    ("xform.rot90l", {}),
    ("edit.offset", {"distance": 4.0}),
    ("edit.weld", {}),
    ("corner.fillet", {"radius": 3.0}),
])
def test_command_equals_running_it_directly(host, job_project, cmd, values):
    d = job_project.to_dict()
    ids = [v["id"] for v in d["sheets"][0]["vectors"]]
    want, ctx = run_direct(d, cmd, ids, values)
    got = host.handle("command", {"project": d, "sheet": d["sheets"][0]["id"], "command": cmd,
                                  "selection": ids, "values": values})
    assert "error" not in got, got
    assert strip_ids(got["project"]) == strip_ids(want)
    strict_json(got)


def test_command_new_selection_and_messages(host, job_project):
    d = job_project.to_dict()
    got = host.handle("command", {"project": d, "command": "create.rectangle",
                                  "values": {"width": 40, "height": 20, "corner": [5, 5]}})
    assert "error" not in got and len(got["selection"]) == 1
    assert len(got["project"]["sheets"][0]["vectors"]) == len(d["sheets"][0]["vectors"]) + 1
    assert got["project"]["sheets"][0]["vectors"][-1]["id"] == got["selection"][0]


def test_command_failures_are_codes_and_change_nothing(host, job_project):
    d = job_project.to_dict()
    before = json.dumps(d, sort_keys=True)
    one = [d["sheets"][0]["vectors"][0]["id"]]
    assert host.handle("command", {"project": d, "command": "edit.weld", "selection": one})["error"] == "command_failed"
    assert host.handle("command", {"project": d, "command": "no.such"})["error"] == "unknown_command"
    assert host.handle("command", {"project": d, "command": "xform.move", "sheet": "zzz"})["error"] == "sheet_not_found"
    assert json.dumps(d, sort_keys=True) == before


def test_every_command_runs_through_the_service(host, job_project):
    """Each registered command, with its defaults, either works or says why not: never an exception."""
    d = job_project.to_dict()
    ids = [v["id"] for v in d["sheets"][0]["vectors"]]
    for cmd in host.handle("schema")["commands"]:
        out = host.handle("command", {"project": d, "command": cmd["id"], "selection": ids})
        assert "ok" in out or out.get("error") in ("command_failed",), (cmd["id"], out)
        strict_json(out)


# -- calculation ------------------------------------------------------------------------------------

def test_compute_results_and_preview(host, job_project):
    out = strict_json(host.handle("compute", {"project": job_project.to_dict()}))
    assert "error" not in out
    (r,) = out["results"]
    assert r["error"] is None and r["stats"]["time_s"] > 0 and r["stats"]["zmin"] < 0
    cuts = r["preview"]["cuts"]
    assert len(cuts) >= 3 and all(len(c["p"]) >= 4 and len(c["p"]) % 2 == 0 for c in cuts)
    assert any(c["z"] <= -17.9 for c in cuts)                       # through cut: 18 mm material
    assert r["preview"]["rapids"] and len(r["preview"]["rapids"]) % 4 == 0
    assert out["totals"][job_project.sheets[0].id] == r["stats"]["time_s"]
    bb = r["stats"]["bbox"]
    assert bb[0] < 10 and bb[2] > 330                               # tool path goes round the outside


def test_compute_agrees_with_compute_all(host, job_project):
    from iyicam.cam.job import compute_all, sheet_toolpaths
    res = compute_all(job_project)
    tps = sheet_toolpaths(job_project, res, job_project.sheets[0])
    want = sum(tp.stats(job_project.material.rapid_rate).time_s for tp in tps)
    got = host.handle("compute", {"project": job_project.to_dict(), "preview": False})
    assert got["results"][0]["preview"] is None
    assert got["totals"][job_project.sheets[0].id] == pytest.approx(want, abs=0.01)


def test_compute_reports_problems_per_toolpath(host):
    p, s, objs = make_project([shapes.rectangle(10, 10, 50, 50)])
    add_tp(p, "profile", end_mill(6), {"cut_depth": 3, "through": False}, objs)
    add_tp(p, "pocket", end_mill(6), {"cut_depth": 100, "through": False}, objs)     # far deeper than the flutes
    out = host.handle("compute", {"project": p.to_dict()})
    assert len(out["results"]) == 2
    assert any("flutes" in w for r in out["results"] for w in r["warnings"])


def test_smart_start_points_travel_back_as_a_patch(host):
    p, s, objs = make_project([shapes.rectangle(10, 10, 80, 40), shapes.rectangle(120, 10, 80, 40)])
    t = add_tp(p, "profile", end_mill(6), {"through": True, "side": "outside"}, objs)
    t.sequence["smart_starts"] = True
    d = p.to_dict()
    out = host.handle("compute", {"project": d})
    assert "error" not in out
    patch = out["patch"]
    assert set(patch["sequences"]) == {t.id}
    assert patch["sequences"][t.id].get("order")                    # the cut order is kept
    # whatever moved is given as whole vectors the caller can swap in
    for vid, vec in patch["vectors"].items():
        assert vec["id"] == vid and vec["contours"]
    # the toolpath a second calculation gives from the patched project is the same as the first (it settled)
    d2 = json.loads(json.dumps(d))
    for sh in d2["sheets"]:
        sh["vectors"] = [patch["vectors"].get(v["id"], v) for v in sh["vectors"]]
    for tp in d2["toolpaths"]:
        tp["sequence"] = patch["sequences"][tp["id"]]
    again = host.handle("compute", {"project": d2})
    assert again["totals"] == out["totals"]


def test_compute_only_some_sheets(host, job_project):
    s2 = job_project.add_sheet("Second", 300, 200)
    from iyicam.core.document import VectorObject
    s2.vectors.append(VectorObject([shapes.rectangle(10, 10, 50, 50)], job_project.layers[0].id))
    job_project.toolpaths[0].selection.mode = "layers"
    job_project.toolpaths[0].selection.layer_ids = [job_project.layers[0].id]
    d = job_project.to_dict()
    both = host.handle("compute", {"project": d, "preview": False})
    one = host.handle("compute", {"project": d, "preview": False, "sheets": [s2.id]})
    assert len(both["results"]) == 2 and len(one["results"]) == 1 and one["results"][0]["sheet"] == s2.id
    assert host.handle("compute", {"project": d, "sheets": ["nope"]})["error"] == "sheet_not_found"


# -- G-code: the same text as the command line ----------------------------------------------------------

@pytest.mark.parametrize("post", ["mach3", "fanuc", "ncstudio", "linuxcnc"])
def test_gcode_is_the_command_lines_gcode(host, job_project, tmp_path, capsys, post):
    path = tmp_path / "job.icam"
    job_project.save(path)
    out = tmp_path / f"out.{REGISTRY.posts[post].extension}"
    assert cli.main(["gcode", str(path), "--post", post, "-o", str(out)]) == 0
    written = sorted(tmp_path.glob("out*"))
    got = host.handle("gcode", {"project": job_project.to_dict(), "post": post, "file_name": "out"})
    assert got["ok"] and not got["errors"]
    assert len(got["files"]) == len(written)
    for f, w in zip(got["files"], written):
        assert f["name"] == w.name
        assert f["text"].encode("ascii", "replace") == w.read_bytes()


def test_gcode_errors_and_all_sheets(host, job_project):
    d = job_project.to_dict()
    assert host.handle("gcode", {"project": d, "post": "nope"})["error"] == "unknown_post"
    job_project.add_sheet("Empty", 100, 100)
    got = host.handle("gcode", {"project": job_project.to_dict(), "post": "mach3"})
    assert got["ok"] and len(got["files"]) == 1                      # a sheet with no toolpath writes no file
    assert got["files"][0]["name"].startswith(job_project.name) and "_1." in got["files"][0]["name"]
    empty = host.handle("gcode", {"project": Project("x").to_dict(), "post": "mach3"})
    assert empty["ok"] is False and empty["files"] == []


# -- simulation -------------------------------------------------------------------------------------------

def test_simulate_gives_a_valid_png(host, job_project):
    out = strict_json(host.handle("simulate", {"project": job_project.to_dict(), "cells": 40_000}))
    assert "error" not in out, out
    png = base64.b64decode(out["png_b64"])
    assert png[:8] == b"\x89PNG\r\n\x1a\n"
    w, h = struct.unpack(">II", png[16:24])
    assert (w, h) == (out["width_px"], out["height_px"]) and w * h <= 60_000
    pos, idat = 8, b""
    while pos < len(png):
        n, kind = struct.unpack(">I4s", png[pos:pos + 8])
        if kind == b"IDAT":
            idat += png[pos + 8:pos + 8 + n]
        pos += 12 + n
    assert len(zlib.decompress(idat)) == h * (1 + 3 * w)
    x0, y0, x1, y1 = out["extent"]
    assert x0 < 0.5 and y0 < 0.5 and x1 >= 500 and y1 >= 300


def test_simulate_without_toolpaths_says_so(host):
    assert host.handle("simulate", {"project": Project("x").to_dict()})["error"] == "no_toolpaths"


def test_png_writer_round_trip():
    import numpy as np
    img = (np.arange(7 * 5 * 3) % 256).astype(np.uint8).reshape(7, 5, 3)
    png = encode_png(img)
    pos, idat = 8, b""
    while pos < len(png):
        n, kind = struct.unpack(">I4s", png[pos:pos + 8])
        if kind == b"IDAT":
            idat += png[pos + 8:pos + 8 + n]
        crc = struct.unpack(">I", png[pos + 8 + n:pos + 12 + n])[0]
        assert crc == zlib.crc32(png[pos + 4:pos + 8 + n]) & 0xFFFFFFFF
        pos += 12 + n
    raw = np.frombuffer(zlib.decompress(idat), np.uint8).reshape(7, 16)
    assert (raw[:, 0] == 0).all() and (raw[:, 1:].reshape(7, 5, 3) == img).all()
    with pytest.raises(ValueError):
        encode_png(np.zeros((3, 3), np.uint8))


# -- nesting: the same layout as `iyicam nest` --------------------------------------------------------------

def _geometry(d):
    """The drawing of a project without ids: what must be equal for two nestings to be the same layout."""
    return [(round(s["width"]), round(s["height"]),
             sorted(json.dumps(c, sort_keys=True) for v in s["vectors"] for c in v["contours"])) for s in d["sheets"]]


def test_nest_is_the_command_lines_layout(host, tmp_path, capsys):
    p, s, objs = make_project([shapes.rectangle(0, 0, 90, 60), shapes.rectangle(0, 0, 70, 70),
                               shapes.circle((0, 0), 35), shapes.rectangle(0, 0, 120, 40)], size=(500, 300))
    path, out = tmp_path / "n.icam", tmp_path / "nested.icam"
    p.save(path)
    assert cli.main(["nest", str(path), "-o", str(out), "--width", "300", "--height", "200", "--spacing", "6",
                     "--margin", "8", "--iterations", "1"]) == 0
    want = Project.load(out).to_dict()
    got = host.handle("nest", {"project": p.to_dict(),
                               "settings": {"width": 300, "height": 200, "spacing": 6, "margin": 8, "iterations": 1}})
    assert "error" not in got, got
    assert got["unplaced"] == 0 and got["placed"] == 4 and got["plates"] == len(want["sheets"])
    assert _geometry(got["project"]) == _geometry(want)
    assert all(0 < sh["utilization"] <= 1 for sh in got["sheets"])
    strict_json(got)


def test_nest_bad_settings_are_codes(host, job_project):
    d = job_project.to_dict()
    assert host.handle("nest", {"project": d, "settings": {"width": "wide"}})["error"] == "bad_request"
    assert host.handle("nest", {"project": d, "settings": {"angles": "a,b"}})["error"] == "bad_request"
    assert host.handle("nest", {"project": d, "settings": {"rotate_layers": {"Nope": "none"}}})["error"] == "bad_request"
    assert host.handle("nest", {"project": Project("x").to_dict()})["error"] == "nothing_to_nest"


# -- jobs ----------------------------------------------------------------------------------------------------

def wait(host, jid, timeout=60.0):
    t0 = time.time()
    while time.time() - t0 < timeout:
        st = host.handle("job.status", {"id": jid})
        if st.get("state") in ("done", "error", "cancelled"):
            return st
        time.sleep(0.02)
    raise AssertionError("the job did not finish")


def test_a_job_runs_to_the_end(host, job_project):
    start = host.handle("job.start", {"op": "compute", "payload": {"project": job_project.to_dict()}})
    assert start["state"] == "queued" and start["id"]
    st = wait(host, start["id"])
    assert st["state"] == "done" and st["error"] is None
    res = host.handle("job.result", {"id": start["id"]})
    assert res["results"][0]["stats"]["time_s"] > 0
    assert host.handle("job.result", {"id": start["id"]})["totals"] == res["totals"]     # fetchable again


def test_a_failing_job_reports_the_error_code(host):
    start = host.handle("job.start", {"op": "gcode", "payload": {"project": Project("x").to_dict(), "post": "nope"}})
    st = wait(host, start["id"])
    assert st["state"] == "error" and st["error"]["error"] == "unknown_post"
    assert host.handle("job.result", {"id": start["id"]})["error"] == "unknown_post"


def test_a_command_can_run_as_a_job_too(host, job_project):
    d = job_project.to_dict()
    ids = [v["id"] for v in d["sheets"][0]["vectors"]]
    start = host.handle("job.start", {"op": "command", "payload": {"project": d, "command": "xform.move",
                                                                  "selection": ids, "values": {"dx": 5}}})
    assert wait(host, start["id"])["state"] == "done"
    want = host.handle("command", {"project": d, "command": "xform.move", "selection": ids, "values": {"dx": 5}})
    assert host.handle("job.result", {"id": start["id"]}) == want


def test_job_start_refuses_quick_and_unknown_operations(host):
    assert host.handle("job.start", {"op": "ping", "payload": {}})["error"] == "unknown_operation"
    assert host.handle("job.start", {"op": "compute", "payload": 3})["error"] == "bad_request"
    assert host.handle("job.result", {"id": "nothing"})["error"] == "job_not_found"


def test_a_long_nesting_can_be_cancelled(host):
    shapes_list = [shapes.rectangle(0, 0, 30 + (i % 7) * 4, 20 + (i % 5) * 5) for i in range(60)]
    p, s, objs = make_project(shapes_list, size=(2000, 1000))
    start = host.handle("job.start", {"op": "nest", "payload": {"project": p.to_dict(), "settings": {
        "width": 800, "height": 600, "iterations": 200, "rotation": 15, "max_points": 0}}})
    t0 = time.time()
    while time.time() - t0 < 20 and host.handle("job.status", {"id": start["id"]})["state"] == "queued":
        time.sleep(0.01)
    time.sleep(0.3)
    assert host.handle("job.status", {"id": start["id"]})["state"] == "running"
    host.handle("job.cancel", {"id": start["id"]})
    st = wait(host, start["id"], 30)
    assert st["state"] == "cancelled" and st["elapsed"] < 30
    assert host.handle("job.result", {"id": start["id"]})["error"] == "job_cancelled"


# -- the job manager on its own ------------------------------------------------------------------------------

def test_job_manager_progress_cancel_and_forgetting():
    jm = JobManager(workers=1, ttl=0.2, keep=5)
    gate = []

    def slow(ctl):
        for k in range(1000):
            ctl.progress(k, 1000, "working")
            ctl.check()
            time.sleep(0.005)
            gate.append(k)
        return {"done": True}

    jid = jm.start("slow", slow)
    time.sleep(0.1)
    st = jm.status(jid)
    assert st["state"] == "running" and st["label"] == "working" and st["n"] == 1000 and st["k"] >= 1
    queued = jm.start("quick", lambda ctl: {"x": 1})        # one worker: waits behind the slow one
    assert jm.status(queued)["state"] == "queued"
    jm.cancel(queued)                                       # cancelled before it ever started
    assert jm.status(queued)["state"] == "cancelled"
    jm.cancel(jid)
    t0 = time.time()
    while jm.status(jid)["state"] == "running" and time.time() - t0 < 5:
        time.sleep(0.01)
    assert jm.status(jid)["state"] == "cancelled" and len(gate) < 1000
    time.sleep(0.3)
    jm.start("again", lambda ctl: {"y": 2})                 # starting a job forgets the old finished ones
    with pytest.raises(ServiceError) as e:
        jm.status(jid)
    assert e.value.code == "job_not_found"


def test_job_manager_an_exception_is_an_error_not_a_crash(capsys):
    jm = JobManager()
    jid = jm.start("bad", lambda ctl: 1 / 0)
    t0 = time.time()
    while jm.status(jid)["state"] in ("queued", "running") and time.time() - t0 < 5:
        time.sleep(0.01)
    st = jm.status(jid)
    assert st["state"] == "error" and st["error"]["error"] == "internal_error" and "ZeroDivisionError" in st["error"]["message"]
    with pytest.raises(ServiceError):
        jm.result(jid)
    capsys.readouterr()


# -- the preview ------------------------------------------------------------------------------------------------

def test_preview_flattens_arcs_drops_plunges_and_keeps_drills():
    from iyicam.cam.moves import ArcMove, DrillCycle, Feed, Rapid, Toolpath
    tp = Toolpath("t", end_mill(6), [Rapid(0, 0, 10), Rapid(10, 0, 10), Feed(10, 0, -3, 500),   # a plunge
                                     Feed(30, 0, -3, 1000), ArcMove(30, 20, -3, 30, 10, False, 1000),
                                     Rapid(30, 20, 10), DrillCycle(5, 5, -8, 2, 300)])
    out = toolpath_preview([tp], step=0.5)
    (cut,) = out["cuts"]
    pts = list(zip(cut["p"][0::2], cut["p"][1::2]))
    assert pts[0] == (10.0, 0.0) and pts[-1] == (30.0, 20.0) and len(pts) > 10     # the half circle has many points
    assert pts.count((10.0, 0.0)) == 1                                              # the plunge added no point
    assert cut["z"] == -3.0
    assert out["drills"] == [[5.0, 5.0, -8.0]]
    assert out["rapids"][:4] == [0.0, 0.0, 10.0, 0.0]


def test_preview_coarsens_until_it_fits():
    from iyicam.cam.moves import Feed, Rapid, Toolpath
    import math
    moves = [Rapid(0, 0, 5)] + [Feed(100 + 50 * math.cos(i / 50), 50 + 50 * math.sin(i / 50), -1, 1000) for i in range(20000)]
    tp = Toolpath("t", end_mill(6), moves)
    big = toolpath_preview([tp], step=0.01, cap=10_000_000)
    small = toolpath_preview([tp], step=0.01, cap=2000)
    assert big["points"] > 5000 and small["points"] <= 2000 and small["step"] > 0.01 and not small["truncated"]


def test_the_job_manager_refuses_a_flood():
    jm = JobManager(workers=1, max_active=3)
    gate = []

    def hold(ctl):
        while not gate:
            ctl.check()
            time.sleep(0.01)
        return {"ok": True}

    ids = [jm.start("hold", hold) for _ in range(3)]
    with pytest.raises(ServiceError) as e:
        jm.start("hold", hold)
    assert e.value.code == "too_busy" and e.value.params["limit"] == 3
    gate.append(1)
    t0 = time.time()
    while any(jm.status(i)["state"] in ("queued", "running") for i in ids) and time.time() - t0 < 5:
        time.sleep(0.01)
    assert jm.start("after", lambda ctl: {"x": 1})          # finished jobs make room again

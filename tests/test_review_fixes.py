"""Bugs found by the reviews of the whole program, each with the test that would have caught it."""
import threading

import pytest
from conftest import add_tp, end_mill, make_project

from iyicam.builtins import ensure_registered
from iyicam.cam.moves import ArcMove, Feed, Rapid, Toolpath
from iyicam.core import shapes
from iyicam.core.calc import evaluate
from iyicam.core.registry import REGISTRY
from iyicam.post import editing, naming
from iyicam.post.export import post_toolpaths
from iyicam.service import ServiceHost


def test_calc_reads_minus_two_squared_as_minus_four_and_survives_deep_brackets():
    assert evaluate("-2^2") == -4 and evaluate("2^-1") == 0.5 and evaluate("5 5") is None
    assert evaluate("(" * 3000 + "1" + ")" * 3000) is None            # (no RecursionError into the box)
    assert evaluate("-" * 3000 + "1") is None


def test_names_with_line_breaks_and_brackets_cannot_inject_gcode():
    p, s, objs = make_project([[shapes.rectangle(10, 10, 100, 60)]], size=(500, 300))
    add_tp(p, "profile", end_mill(6), {"through": True}, objs)
    p.name = "proj\nM3 S99999"
    p.sheets[0].name = "sh\nG1 X5"
    p.toolpaths[0].name = "a)\nM30\nG0 X999 (b"
    g = ServiceHost().handle("gcode", {"project": p.to_dict(), "post": "mach3"})
    lines = [ln.strip() for ln in g["files"][0]["text"].splitlines()]
    assert not any(ln.startswith(("M3 S99999", "G1 X5", "M30 ", "G0 X999")) for ln in lines[:12])
    assert lines.count("M30") == 1


def test_a_full_circle_is_written_and_a_program_number_line_has_no_line_number():
    ensure_registered(REGISTRY)
    base = REGISTRY.posts["mach3"]
    post = type(base)({**base.definition, "options": {**base.definition.get("options", {}), "safe_arcs": False}})
    tp = Toolpath("t", end_mill(6), [Rapid(10, 0, 5), Feed(10, 0, -1, 500), ArcMove(10, 0, -1, 0, 0, False, 500), Feed(20, 0, -1, 500)])
    p, s, _ = make_project([[shapes.rectangle(0, 0, 50, 50)]], size=(100, 100))
    text = post_toolpaths(p, s, [tp], post, "x")[0][1]
    assert sum(1 for ln in text.splitlines() if " I" in ln) >= 2                    # (in pieces: one G3 would be no circle)
    fan = REGISTRY.posts["fanuc"]
    fan.options["line_numbers"] = True
    try:
        out = post_toolpaths(p, s, [tp], fan, "x")[0][1].splitlines()
    finally:
        fan.options["line_numbers"] = False
    assert any(ln.startswith("O") for ln in out) and not any(ln.startswith("N") and " O" in ln for ln in out)


def test_two_jobs_at_once_do_not_mix_their_gcode():
    ensure_registered(REGISTRY)

    def job(name, w):
        p, s, objs = make_project([[shapes.rectangle(10, 10, w, 60)]], size=(500, 300))
        add_tp(p, "profile", end_mill(6), {"through": True}, objs)
        p.name = name
        return p

    a, b = job("AAA", 100), job("BBB", 200)
    h = ServiceHost()
    want = {n: h.handle("gcode", {"project": p.to_dict(), "post": "mach3"})["files"][0]["text"] for n, p in (("AAA", a), ("BBB", b))}
    strip = lambda t: "\n".join(ln for ln in t.splitlines() if "Generated" not in ln)
    bad = []

    def run(name, p):
        for _ in range(8):
            t = h.handle("gcode", {"project": p.to_dict(), "post": "mach3", "recalculate": True})["files"][0]["text"]
            if strip(t) != strip(want[name]):
                bad.append(name)
    th = [threading.Thread(target=run, args=("AAA", a)), threading.Thread(target=run, args=("BBB", b))]
    [t.start() for t in th]
    [t.join() for t in th]
    assert not bad


def test_files_of_two_sheets_never_share_a_name_and_a_renamed_toolpath_is_written_by_its_name():
    p, s, objs = make_project([[shapes.rectangle(10, 10, 100, 60)]], size=(500, 300))
    add_tp(p, "profile", end_mill(6), {"through": True}, objs)
    import copy
    from iyicam.core.document import Selection
    p.toolpaths[0].selection = Selection("layers", [], [p.layers[0].id])
    p.add_sheet("a?b", 500, 300)
    p.sheets[0].name = "a:b"
    p.sheets[1].vectors = [copy.deepcopy(v) for v in p.sheets[0].vectors]
    for v in p.sheets[1].vectors:
        v.id += "x"
    h = ServiceHost()
    g = h.handle("gcode", {"project": p.to_dict(), "post": "mach3", "name_parts": ["{sheet_name}"]})
    names = [f["name"].lower() for f in g["files"]]
    assert len(names) == len(set(names)) == 2
    c = h.handle("compute", {"project": p.to_dict(), "preview": False})
    q = p
    for tid, seq in c["patch"]["sequences"].items():
        q.toolpath(tid).sequence = seq
    q.toolpaths[0].name = "NEWNAME"
    g = h.handle("gcode", {"project": q.to_dict(), "post": "mach3"})
    assert "NEWNAME" in g["files"][0]["text"]


def test_wrong_input_is_a_bad_request_and_windows_names_and_huge_tools_are_refused():
    h = ServiceHost()
    p, s, objs = make_project([[shapes.rectangle(10, 10, 100, 60)]], size=(500, 300))
    add_tp(p, "profile", end_mill(6), {"through": True}, objs)
    for payload in ({"toolpaths": 5}, {"optimise_starts": 5}):
        assert h.handle("compute", {"project": p.to_dict(), **payload})["error"] == "bad_request"
    assert h.handle("export", {"project": p.to_dict(), "format": "dxf", "options": 5})["error"] == "bad_request"
    assert naming.render(["CON"], {}) == "_CON" and naming.values(revision="abc")["revision"] == ""
    p.toolpaths[0].tool.diameter = 20000
    r = h.handle("compute", {"project": p.to_dict(), "preview": False})
    assert r["results"][0]["error"] and "diameter" in r["results"][0]["error"]


def test_a_project_file_is_written_whole(tmp_path):
    from iyicam.core.document import Project
    p = Project("x")
    p.save(tmp_path / "a.icam")
    assert Project.load(tmp_path / "a.icam").name == "x" and not list(tmp_path.glob("*.tmp"))


def test_core_edge_cases_found_by_the_review(tmp_path):
    import math
    from iyicam.core.document import VectorObject
    from iyicam.core.geom import Contour
    from iyicam.core.params import Param
    from iyicam.core.tooling import ToolLibrary
    from iyicam.core.undo import UndoStack
    from iyicam.core import vectorops
    assert vectorops.find_duplicates([VectorObject([Contour([])], "l")]) == []
    assert Param("f", "F", "feed", 1.0, min=1, max=100).coerce(float("nan")) == 1.0
    assert Param("n", "N", "int", 5).coerce(float("inf")) == 5
    assert Param("b", "B", "bool", False).coerce(1.0) is True and Param("n", "N", "int", 1, min=0.5).coerce(0) == 1
    (tmp_path / "tools.json").write_text("[]")
    assert ToolLibrary.load_or_default(tmp_path / "tools.json").tools              # (the default tools, not a crash)
    (tmp_path / "tools.json").write_text('{"tools": [5]}')
    assert ToolLibrary.load_or_default(tmp_path / "tools.json").tools
    from iyicam.core.document import Project
    p = Project()
    u = UndoStack()
    u.checkpoint(p, "a")
    p.add_layer("x")
    assert u.undo(p) and u.can_redo
    u.checkpoint(p, "node press")
    u.discard_last()                                                              # (a press without a move: the redo steps stay)
    assert u.can_redo
    assert math.isfinite(1.0)


def test_a_plugin_that_exits_and_its_helper_modules(tmp_path):
    from iyicam.core.registry import Registry
    from iyicam.plugins.manager import PluginManager
    (tmp_path / "exiter.py").write_text("import sys\nsys.exit(3)\n")
    pkg = tmp_path / "pkgp"
    pkg.mkdir()
    (pkg / "__init__.py").write_text("from . import helper\nPLUGIN = {'name': 'pkgp'}\ndef register(api):\n    api.value = helper.VALUE\n")
    (pkg / "helper.py").write_text("VALUE = 1\n")
    reg = Registry()
    m = PluginManager(reg, [tmp_path], [])
    infos = m.load_all()
    assert any(i.key == "exiter" and i.error for i in infos) and any(i.key == "pkgp" and i.loaded for i in infos)
    (pkg / "helper.py").write_text("VALUE = 222\n")
    info = next(i for i in infos if i.key == "pkgp")
    m.unload(info)
    assert m.load(info) and info.module.helper.VALUE == 222


def test_cam_and_nesting_edge_cases_found_by_the_review():
    import time
    from conftest import end_mill
    from iyicam.cam import audit
    from iyicam.cam.job import compute_toolpath
    from iyicam.core.geom import BBox, Bezier, Contour, Line
    from iyicam.nesting.parts import rotation_angles
    from iyicam.nesting.stock import StockItem, use_up
    ensure_registered(REGISTRY)
    # the handles of a curve are part of its shape
    def shape(h):
        return Contour([Bezier((0, 0), (0, h), (100, h), (100, 0)), Line((100, 0), (0, 0))], True)
    tops = []
    for h in (100, 10, 100):
        p, s, objs = make_project([shape(h)], size=(300, 300))
        add_tp(p, "profile", end_mill(6), {"side": "outside", "through": True}, objs)
        tps = compute_toolpath(p, p.toolpaths[0], s).toolpaths
        tops.append(round(tps[0].stats().ymax))
    assert tops[0] == tops[2] and tops[1] < tops[0] - 20
    # a vector of no size does not take the toolpath of the others down
    p, s, objs = make_project([shapes.rectangle(50, 50, 40, 40), shapes.rectangle(10, 10, 0, 0)], size=(300, 300))
    add_tp(p, "profile", end_mill(6), {"side": "on", "through": True}, objs)
    r = compute_toolpath(p, p.toolpaths[0], s)
    assert not r.error and r.toolpaths
    # the check of a layout is not exponential
    boxes = {f"{i}:{j}": BBox(25 * i, 25 * j, 25 * i + 20, 25 * j + 20) for i in range(10) for j in range(10)}
    order = sorted(boxes, key=lambda k: (-int(k.split(":")[0]), -int(k.split(":")[1])))
    t0 = time.perf_counter()
    audit.violations(order, boxes)
    assert time.perf_counter() - t0 < 5
    # stock is not changed in place, and angles are not repeated
    a = StockItem("a", 100, 100, None, 2, True)
    kept = use_up([a], [a, a])
    assert a.quantity == 2 and kept == []
    ang = rotation_angles({"mode": "limit_flip", "value": 90}, 5.0)
    assert len(ang) == len(set(ang)) == 72

"""A CNC user's session in Chromium (explore.js): desktop (draw, import SVG and DXF, profile, pocket, tools, undo, nest, simulate)
and phones (iPhone 13, iPhone SE).  Each test is one thing a person would notice."""
import json
import subprocess
from pathlib import Path

import ezdxf
import pytest

import webapp_env as w
from webapp_env import local_server  # noqa: F401  (the fixture)

SCRIPT = Path(__file__).with_name("explore.js")


def _run(local_server, **extra):
    node, nenv, url, key, base = local_server
    files = base / "files"
    files.mkdir(exist_ok=True)
    if True:                                                     # (made again: it changed)
        doc = ezdxf.new(units=4)                                 # (INSUNITS 4: millimetres; ezdxf says metres by default and the importer believes it)
        m = doc.modelspace()
        # (apart from one another and from what is drawn: vectors on one another are not cut any more)
        m.add_circle((350, 200), 20)
        m.add_line((300, 150), (380, 160))
        m.add_lwpolyline([(410, 160), (460, 160), (460, 190), (410, 190)], close=True)
        m.add_arc((400, 250), 15, 0, 180)
        doc.saveas(files / "t.dxf")
    env = {**nenv, "URL": url, "KEY": key, "FILES": str(files), "FIXTURE": str(w.FIXTURES / "project.json"), **extra}
    run = subprocess.run([nenv.get("NODE", "node"), str(SCRIPT)], env=env, capture_output=True, text=True, timeout=900)
    lines = [ln for ln in run.stdout.splitlines() if ln.startswith("RESULT ")]
    assert run.returncode == 0 and lines, run.stdout[-2000:] + run.stderr[-3000:]
    return json.loads(lines[-1][7:])


@pytest.fixture(scope="module")
def desktop(local_server):
    return _run(local_server, MODE="desktop")


@pytest.fixture(scope="module", params=["iPhone 13", "iPhone SE"])
def phone(request, local_server):
    return _run(local_server, MODE="phone", DEVICE=request.param)[request.param]



def test_drawing_and_importing(desktop):
    assert desktop["drawn"]["n"] == 2
    assert desktop["svg"]["n"] == 5                              # (2 drawn + the SVG's rectangle, circle and curve)
    assert desktop["svgBox"][2] - desktop["svgBox"][0] == pytest.approx(85, abs=1.5)      # (the SVG is in mm: 5..90)
    assert desktop["dxf"]["n"] == 9                              # (+ circle, line, closed polyline, arc)


def test_profile_and_pocket_are_calculated_and_simulated(desktop):
    t = desktop["toolpaths"]
    assert [x.split()[0] for x in t["tps"]] == ["Profile", "Pocket"] and t["results"] == 2 and not t["stale"] and not t["errs"]
    assert all(s and s.get("time_s", 1) > 0 for s in desktop["stats"])
    assert desktop["sim"]["sim"]


def test_undo_and_redo_walk_back_and_forth(desktop):
    assert desktop["undone"]["n"] == 1 and desktop["undone"]["tps"] == []
    assert desktop["redone"]["n"] == desktop["dxf"]["n"] and len(desktop["redone"]["tps"]) == 2
    assert desktop["recalc"]["results"] == 2 and not desktop["recalc"]["stale"]


def test_nesting(desktop):
    assert "9 part(s) placed" in desktop["nestReport"]           # (9 vectors apart from one another: each a part)
    assert desktop["nested"]["n"] == 9


def test_the_tool_database_survives_a_reload(desktop):
    assert desktop["toolsBefore"][0][0] != "My Cutter"
    assert desktop["toolsSaved"][0][0] == "My Cutter"
    assert desktop["toolsReloaded"] == desktop["toolsSaved"]


def test_no_page_errors_on_the_desktop(desktop):
    assert desktop["errors"] == []


def test_phone_has_no_sideways_page_scroll_and_dialogs_fit(phone):
    assert phone["pageOverflow"]["page"] <= 0
    for name, d in phone["dialogs"].items():
        assert d and d["left"] >= 0 and d["right"] <= d["vw"] + 1 and d["overflow"] == 0 and d["primaryVisible"], (name, d)


def test_phone_touch_drawing(phone):
    assert phone["touchDraw"]["n"] == 20 and phone["touchDraw"]["tool"] == "rect"
    assert phone["errors"] == []


def test_phone_layer_buttons_can_be_reached_in_the_drawer(phone):
    bad = [b["title"] for b in phone["layerButtons"] if not b["hit"]]
    assert phone["layerButtons"] and not bad, phone["layerButtons"]

"""The engine in the browser (Pyodide, in Chromium) gives what the native engine gives.

Serves the app with ``iyicam serve --local`` (in a thread), drives it with Playwright through ``engine_parity.js`` (node) and
compares: the schema, an import, the toolpaths of ``docs/webapp/fixtures/project.json``, and the G-code, which must be the
same text.  It also checks the errors, the cancelling, that the user folder survives a restart of the engine, and the files
API.  Skipped when node, Playwright or the network (Pyodide comes from a CDN) are missing.
"""
import json
import os
import shutil
import subprocess
import threading
import urllib.request
from pathlib import Path

import pytest

from iyicam.service import ops
from iyicam.service.keys import KeyStore
from iyicam.share import server as share_server

ROOT = Path(__file__).resolve().parents[2]
FIXTURES = ROOT / "docs" / "webapp" / "fixtures"
SCRIPT = Path(__file__).with_name("engine_parity.js")
KEY = "ABCDEF-GHJKMN-PQRSTU-VWXYZ2"


def _node_env():
    node = shutil.which("node")
    npm = shutil.which("npm")
    if not node or not npm:
        pytest.skip("node is not installed")
    modules = subprocess.run([npm, "root", "-g"], capture_output=True, text=True).stdout.strip()
    if not (Path(modules) / "playwright").is_dir():
        pytest.skip("playwright is not installed (npm i -g playwright)")
    try:
        urllib.request.urlopen(urllib.request.Request("https://cdn.jsdelivr.net/pyodide/v0.27.7/full/pyodide.mjs", method="HEAD"), timeout=10)
    except OSError:
        pytest.skip("the Pyodide CDN is not reachable")
    return node, {**os.environ, "NODE_PATH": modules}


def _gcode_text(text: str) -> str:
    return "\n".join(line for line in text.splitlines() if not line.startswith("(DATE "))     # (the minute it was made)


def test_the_browser_engine_gives_what_the_native_engine_gives(tmp_path):
    node, env = _node_env()
    shared = tmp_path / "jobs"
    shared.mkdir()
    (shared / "a.svg").write_bytes(b'<svg xmlns="http://www.w3.org/2000/svg" width="100" height="50"><rect x="5" y="5" width="40" height="30"/></svg>')
    srv = share_server.make_server("127.0.0.1", 0, keys=KeyStore(fixed=KEY), local=True, roots=[str(shared)],
                                   sessions_path=tmp_path / "sessions.json")
    threading.Thread(target=srv.serve_forever, kwargs={"poll_interval": 0.1}, daemon=True).start()
    out_file = tmp_path / "out.json"
    try:
        run = subprocess.run([node, str(SCRIPT), f"http://127.0.0.1:{srv.server_address[1]}", KEY,
                              str(FIXTURES / "project.json"), str(out_file)], env=env, capture_output=True, text=True, timeout=600)
    finally:
        srv.shutdown()
        srv.server_close()
    assert run.returncode == 0, run.stderr + run.stdout
    got = json.loads(out_file.read_text())

    assert got["isolated"] and got["cancel_mode"] == "flag"
    assert not [p for p in got["problems"] if p.startswith("pageerror")]

    # the same schema, project, toolpaths and G-code as the native engine for the same payloads
    ops.ensure_ready()
    project = json.loads((FIXTURES / "project.json").read_text())
    native_schema = json.loads(json.dumps(ops.op_schema()))
    for schema in (got["schema"], native_schema):          # (the default tools get new ids at each start; a browser has no LibreOffice for .cdr)
        schema["tools"] = [{k: v for k, v in t.items() if k != "id"} for t in schema["tools"]]
        schema["cdr"].pop("converters")
    assert got["schema"] == native_schema
    native = ops.op_compute({"project": project})
    assert [(r["toolpath"], r["sheet"], r["stats"], r["warnings"], r["preview"]) for r in got["compute"]["results"]] == \
        [(r["toolpath"], r["sheet"], r["stats"], r["warnings"], r["preview"]) for r in json.loads(json.dumps(native))["results"]]
    assert got["compute"]["totals"] == native["totals"]
    assert got["progress"] >= 1
    nat_g = ops.op_gcode({"project": project, "post": got["schema"]["posts"][0]["id"]})
    assert [f["name"] for f in got["gcode"]["files"]] == [f["name"] for f in nat_g["files"]]
    for a, b in zip(got["gcode"]["files"], nat_g["files"]):
        assert _gcode_text(a["text"]) == _gcode_text(b["text"]) and len(a["text"]) > 200
    assert got["imported"]["project"]["sheets"][0]["vectors"], "the SVG was read"

    # errors, cancelling, the user folder
    assert got["errors"] == {"bad": "bad_request", "unknown": "unknown_operation"}
    assert got["cancel"] == "cancelled" and got["after_cancel"] is True
    assert got["presets_after_restart"] == ["parity"] and got["persistent"] is True

    # the files API through the server
    f = got["files"]
    assert f["before"] is False and f["unsigned"] == "not_logged_in"
    assert f["status"]["agent"]["connected"] and f["roots"] == ["jobs"] and f["items"] == ["a.svg"]
    assert f["read_len"] > 50 and f["exists"] == "file_exists" and f["escape"] == "path_not_allowed"
    assert (shared / "written.nc").read_text() == "again"

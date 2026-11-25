"""What the browser tests of the web app share: node + Playwright + the Pyodide CDN, and real `iyicam serve` / `iyicam agent` processes."""
import os
import shutil
import socket
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
FIXTURES = ROOT / "docs" / "webapp" / "fixtures"
SCRIPTS = Path(__file__).with_name("ui_scripts")


def node_env():
    """(node, env) for Playwright scripts, or skip when node, Playwright or the Pyodide CDN are missing."""
    node, npm = shutil.which("node"), shutil.which("npm")
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


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def iyicam(args, env, **kw):
    return subprocess.Popen([sys.executable, "-m", "iyicam", *args], env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, **kw)


def show_key(env) -> str:
    return subprocess.run([sys.executable, "-m", "iyicam", "serve", "--show-key"], env=env, capture_output=True, text=True, check=True).stdout.strip()


def wait_http(url: str, proc, timeout=30):
    end = time.time() + timeout
    while time.time() < end:
        if proc.poll() is not None:
            raise RuntimeError("the process ended: " + (proc.stdout.read() or ""))
        try:
            urllib.request.urlopen(url, timeout=2)
            return
        except OSError:
            time.sleep(0.2)
    raise RuntimeError("no answer from " + url)


def stop(proc):
    if proc and proc.poll() is None:
        proc.terminate()
        try:
            proc.wait(10)
        except subprocess.TimeoutExpired:
            proc.kill()


def native(op: str, payload=None):
    """What the desktop engine's `op_<op>(payload)` gives, from a FRESH process with its own empty IYICAM_HOME.

    (In the pytest process the registries are global and other tests of the suite register posts / tools into them: the
    comparison with a clean browser engine would depend on which tests ran before in the same worker.)"""
    import json
    import tempfile
    code = ("import json,sys\nfrom iyicam.service import ops\nops.ensure_ready()\np=json.load(sys.stdin)\n"
            "r=getattr(ops,'op_'+sys.argv[1])(p) if p is not None else getattr(ops,'op_'+sys.argv[1])()\nprint(json.dumps(r))\n")
    with tempfile.TemporaryDirectory() as home:
        run = subprocess.run([sys.executable, "-c", code, op], input=json.dumps(payload), env={**os.environ, "IYICAM_HOME": home},
                             capture_output=True, text=True, timeout=600)
    if run.returncode:
        raise RuntimeError(run.stderr[-3000:])
    return json.loads(run.stdout.splitlines()[-1])


# One `iyicam serve --local` per test module (imported by test_ui.py and test_explore.py: a conftest.py here would clash with
# tests/conftest.py, which the other tests import by the same module name).
@pytest.fixture(scope="module")
def local_server(tmp_path_factory):
    node, nenv = node_env()
    base = tmp_path_factory.mktemp("ui")
    share = base / "share"                  # (check_flows.js opens share > jobs > demo.icam)
    jobs = share / "jobs"
    jobs.mkdir(parents=True)
    (jobs / "demo.icam").write_bytes((FIXTURES / "project.json").read_bytes())
    env = {**os.environ, "IYICAM_HOME": str(base / "home")}
    port = free_port()
    proc = iyicam(["serve", "--local", "--root", str(share), "--port", str(port), "--quiet"], env)
    try:
        wait_http(f"http://127.0.0.1:{port}/app/config.json", proc)
        yield node, nenv, f"http://127.0.0.1:{port}/app/", show_key(env), base
    finally:
        stop(proc)

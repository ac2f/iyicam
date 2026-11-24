"""The designer's Playwright scripts (ui_scripts/check_*.js) as pytest tests, against one `iyicam serve --local`.

Each script prints ok / FAIL lines and exits non-zero on a failure (or 2 when it crashes).  One server and one temp
IYICAM_HOME serve them all.  Skipped when node, Playwright or the Pyodide CDN are missing.
"""
import os
import subprocess

import pytest

import webapp_env as w

SCRIPT_NAMES = ["check_ui", "check_panels", "check_export", "check_flows", "check_touch"]


@pytest.fixture(scope="module")
def local_server(tmp_path_factory):
    node, nenv = w.node_env()
    base = tmp_path_factory.mktemp("ui")
    jobs = base / "jobs"
    jobs.mkdir()
    (jobs / "demo.icam").write_bytes((w.FIXTURES / "project.json").read_bytes())
    env = {**os.environ, "IYICAM_HOME": str(base / "home")}
    port = w.free_port()
    proc = w.iyicam(["serve", "--local", "--root", str(jobs), "--port", str(port), "--quiet"], env)
    try:
        w.wait_http(f"http://127.0.0.1:{port}/app/config.json", proc)
        yield node, nenv, f"http://127.0.0.1:{port}/app/", w.show_key(env), base
    finally:
        w.stop(proc)


@pytest.mark.parametrize("script", SCRIPT_NAMES)
def test_ui_script(script, local_server):
    node, nenv, url, key, base = local_server
    env = {**nenv, "URL": url, "KEY": key, "OUT": str(base / "shots")}
    run = subprocess.run([node, str(w.SCRIPTS / f"{script}.js")], env=env, capture_output=True, text=True, timeout=900, cwd=w.SCRIPTS)
    fails = [ln for ln in run.stdout.splitlines() if ln.startswith("FAIL")]
    assert run.returncode == 0 and not fails, f"exit {run.returncode}\n" + "\n".join(fails) + "\n" + run.stdout[-3000:] + run.stderr[-3000:]

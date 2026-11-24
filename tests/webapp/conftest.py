"""One `iyicam serve --local` per test module for the browser tests (webapp_env.py has the helpers)."""
import os

import pytest

import webapp_env as w


@pytest.fixture(scope="module")
def local_server(tmp_path_factory):
    node, nenv = w.node_env()
    base = tmp_path_factory.mktemp("ui")
    share = base / "share"                  # (check_flows.js opens share > jobs > demo.icam)
    jobs = share / "jobs"
    jobs.mkdir(parents=True)
    (jobs / "demo.icam").write_bytes((w.FIXTURES / "project.json").read_bytes())
    env = {**os.environ, "IYICAM_HOME": str(base / "home")}
    port = w.free_port()
    proc = w.iyicam(["serve", "--local", "--root", str(share), "--port", str(port), "--quiet"], env)
    try:
        w.wait_http(f"http://127.0.0.1:{port}/app/config.json", proc)
        yield node, nenv, f"http://127.0.0.1:{port}/app/", w.show_key(env), base
    finally:
        w.stop(proc)

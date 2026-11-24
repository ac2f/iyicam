"""End to end with a REAL agent: `iyicam serve` (not --local) + `iyicam agent` as separate processes, Chromium driving the app.

Open a .icam of the agent's folder, recalculate (F5), save G-code to the agent's folder; the file there is what the native
`op_gcode` gives (the DATE line masked).  Then the agent is stopped: the app notices and still opens device files.
"""
import json
import os
import re
import subprocess
import time

import pytest

import webapp_env as w
from iyicam.service import ops

SCRIPT = os.path.join(os.path.dirname(__file__), "e2e_agent.js")


def _mask(text: str) -> str:
    return "\n".join(line for line in text.splitlines() if not line.startswith("(DATE "))


def _run(node, nenv, url, key, phase, tmp, between=None):
    env = {**nenv, "URL": url, "KEY": key, "PHASE": phase, "OUT": str(tmp / "shots"), "FIXTURE": str(w.FIXTURES / "project.json")}
    proc = subprocess.Popen([node, SCRIPT], env=env, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    if between:                                     # (the script says READY, we change the world, it goes on)
        for line in proc.stdout:
            if line.strip() == "READY":
                between()
                proc.stdin.write("go\n")
                proc.stdin.flush()
                break
    stdout, stderr = proc.communicate(timeout=600)
    run = subprocess.CompletedProcess([], proc.returncode, stdout, stderr)
    lines = [ln for ln in run.stdout.splitlines() if ln.startswith("RESULT ")]
    assert run.returncode == 0 and lines, run.stdout[-2000:] + run.stderr[-3000:]
    return json.loads(lines[-1][7:])


@pytest.fixture(scope="module")
def stack(tmp_path_factory):
    node, nenv = w.node_env()
    base = tmp_path_factory.mktemp("e2e")
    jobs = base / "shop"
    jobs.mkdir()
    (jobs / "project.icam").write_bytes((w.FIXTURES / "project.json").read_bytes())
    env = {**os.environ, "IYICAM_HOME": str(base / "home")}
    port = w.free_port()
    url = f"http://127.0.0.1:{port}/app/"
    server = w.iyicam(["serve", "--port", str(port), "--quiet"], env)
    agent = None
    try:
        w.wait_http(f"http://127.0.0.1:{port}/app/config.json", server)
        key = w.show_key(env)
        agent = w.iyicam(["agent", "--server", f"http://127.0.0.1:{port}", "--key", key, "--root", str(jobs), "--name", "Workshop test", "--quiet"],
                         {**env, "IYICAM_HOME": str(base / "agent_home")})
        end = time.time() + 30                      # (the agent is "here" after its first poll)
        import urllib.request
        while time.time() < end:
            time.sleep(0.5)
            if agent.poll() is not None:
                raise RuntimeError("the agent ended: " + agent.stdout.read())
            req = urllib.request.Request(f"http://127.0.0.1:{port}/api/login", data=json.dumps({"key": key}).encode(), headers={"Content-Type": "application/json", "Origin": f"http://127.0.0.1:{port}"})
            cookie = urllib.request.urlopen(req).headers.get("Set-Cookie", "").split(";")[0]
            st = json.loads(urllib.request.urlopen(urllib.request.Request(f"http://127.0.0.1:{port}/api/files/status", headers={"Cookie": cookie})).read())
            if st.get("agent") and st["agent"].get("connected"):
                break
        else:
            raise RuntimeError("the agent did not connect")
        yield dict(node=node, nenv=nenv, url=url, key=key, jobs=jobs, agent=agent, tmp=base)
    finally:
        w.stop(agent)
        w.stop(server)


def test_open_recalculate_and_save_gcode_through_a_real_agent(stack):
    r = _run(stack["node"], stack["nenv"], stack["url"], stack["key"], "work", stack["tmp"])
    assert not r["errors"], r["errors"]
    assert r["status"]["agent"]["connected"] and r["status"]["agent"]["name"] == "Workshop test"
    assert r["roots"] == ["shop"] and "project.icam" in r["listing"]
    assert r["opened"]["n"] == 19 and r["opened"]["tps"] == 2
    assert r["recalculated"]["results"] >= 2 and not r["recalculated"]["stale"]
    written = sorted(p.name for p in stack["jobs"].iterdir() if p.suffix not in (".icam",))
    assert len(written) == 1, written
    ops.ensure_ready()
    project = json.loads((w.FIXTURES / "project.json").read_text())
    native = ops.op_gcode({"project": project, "post": r["post"], "file_name": "project", "program_number": 1000})
    assert [f["name"] for f in native["files"]] == written
    text = (stack["jobs"] / written[0]).read_text()
    assert len(text) > 200 and _mask(text) == _mask(native["files"][0]["text"])


def test_when_the_agent_is_gone_the_app_says_so_and_still_opens_device_files(stack):
    r = _run(stack["node"], stack["nenv"], stack["url"], stack["key"], "gone", stack["tmp"], between=lambda: w.stop(stack["agent"]))
    assert not [e for e in r["errors"] if "status of 503" not in e], r["errors"]     # (503: the server says the agent is offline, as it should)
    assert r["statusGone"]["agent"] is None or r["statusGone"]["agent"]["connected"] is False
    assert r["beforeChooser"] >= 2
    assert r["freshChooser"] == 0 and r["freshFileChooser"], "a page opened after the agent left still offers the workshop PC"
    assert r["deviceOpened"]["n"] == 19, "Open goes to the device's file picker and opens the file"
    stack["gone"] = r
    # the page that was open when the agent left says what is wrong when it browses the dead agent
    assert r["staleChooser"] <= 1 or re.search(r"no agent|offline|gone|not connected", r.get("staleBrowserText", ""), re.I), r


@pytest.mark.xfail(reason="BUG: ui/files.js agent() caches the status for good (nobody calls agent(true)): an open page still offers the workshop PC after the agent left (and does not see one that came later)", strict=True)
def test_an_open_page_stops_offering_an_agent_that_left(stack):
    r = stack.get("gone")
    if r is None:
        pytest.skip("needs the previous test")
    assert r["staleChooser"] <= 1

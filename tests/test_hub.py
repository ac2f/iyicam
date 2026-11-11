"""The hub (``iyicam serve --hub``) and the agent (``iyicam agent``): a browser signs in on the hub with the client key of
an agent and works with the engine and the files of the agent's computer.

What matters: an agent gets in only with the server key (and an address that keeps trying waits), a browser gets in only
with the client key of an agent (never with the server key), what the browser asks is answered by the agent with the
SAME answers as the service gives directly, a hub whose agent is away or silent says so (and does not hang), a new
client key ends the sessions of the old one, and a restart of the hub keeps its agents.
"""
import http.client
import json
import threading
import time
import uuid

import pytest

from iyicam.service import ServiceHost
from iyicam.service import agent as agent_mod
from iyicam.service import server as srv
from iyicam.service.hub import Hub
from iyicam.service.keys import KeyStore, make_key

SERVER_KEY = "SRVKEY-ABCDEF-GHJKMN-PQRSTU"
TOKEN = "ABCDEF-GHJKMN-PQRSTU-VWXYZ2"


class Client:
    """A browser (cookie kept) or, with ``headers``, an agent's raw calls."""

    def __init__(self, port):
        self.port, self.cookie = port, None

    def request(self, method, path, body=None, headers=None):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=30)
        h = dict(headers or {})
        if self.cookie:
            h["Cookie"] = self.cookie
        data = None
        if body is not None:
            data = json.dumps(body).encode()
            h.setdefault("Content-Type", "application/json")
        conn.request(method, path, data, h)
        r = conn.getresponse()
        payload = r.read()
        sc = r.getheader("Set-Cookie")
        if sc and sc.startswith(srv.COOKIE + "="):
            value = sc.split(";", 1)[0].split("=", 1)[1]
            self.cookie = f"{srv.COOKIE}={value}" if value else None
        conn.close()
        return r.status, (json.loads(payload) if payload else None)

    def api(self, op, payload=None):
        return self.request("POST", f"/api/{op}", payload or {})

    def login(self, key=TOKEN):
        return self.request("POST", "/api/login", {"key": key})

    def agent_call(self, action, body, key=SERVER_KEY):
        return self.request("POST", f"/agent/{action}", body, headers={"X-Server-Key": key})


@pytest.fixture(autouse=True)
def home(tmp_path, monkeypatch):
    """Nothing here may touch the real ~/.iyicam."""
    monkeypatch.setenv("IYICAM_HOME", str(tmp_path / "home"))
    return tmp_path / "home"


@pytest.fixture
def folder(tmp_path):
    f = tmp_path / "files"
    f.mkdir()
    return f


class Setup:
    def __init__(self, tmp_path, folder, hold=0.3, job_timeout=20.0):
        self.hub = Hub(KeyStore(fixed=SERVER_KEY), tmp_path / "agents.json", hold=hold, job_timeout=job_timeout)
        self.server = srv.make_server("127.0.0.1", 0, hub=self.hub)
        self.port = self.server.server_address[1]
        self.thread = threading.Thread(target=self.server.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True)
        self.thread.start()
        self.folder = folder
        self.agents: list[agent_mod.Agent] = []

    def agent(self, token=TOKEN, start=True, server_key=SERVER_KEY, agent_id=None, **kw):
        a = agent_mod.Agent(f"http://127.0.0.1:{self.port}", server_key, agent_id or uuid.uuid4().hex, token,
                            name="Workshop", roots=[str(self.folder)], poll_timeout=5, **kw)
        self.agents.append(a)
        if start:
            a.start()
            self.wait(lambda: a.state == "connected")
        return a

    @staticmethod
    def wait(cond, seconds=10.0):
        end = time.time() + seconds
        while time.time() < end:
            if cond():
                return True
            time.sleep(0.02)
        raise AssertionError("it did not happen in time")

    def browser(self, token=TOKEN):
        c = Client(self.port)
        assert c.login(token)[0] == 200
        return c

    def close(self):
        for a in self.agents:
            a.stop()
        self.server.shutdown()
        self.server.server_close()


@pytest.fixture
def hub(tmp_path, folder):
    s = Setup(tmp_path, folder)
    yield s
    s.close()


# ---------------------------------------------------------------------------------------------
# who gets in
# ---------------------------------------------------------------------------------------------

def test_an_agent_registers_only_with_the_server_key(hub):
    raw = Client(hub.port)
    body = {"agent_id": uuid.uuid4().hex, "token": TOKEN, "name": "x"}
    assert raw.agent_call("register", body, key="WRONG-WRONG-WRONG-WRONG")[0] == 403
    assert raw.agent_call("register", body, key="")[0] == 403
    status, out = raw.agent_call("register", body)
    assert status == 200 and out["ok"] is True


def test_an_address_that_keeps_guessing_the_server_key_waits(tmp_path, folder):
    s = Setup(tmp_path, folder)
    try:
        raw = Client(s.port)
        codes = [raw.agent_call("poll", {"agent_id": uuid.uuid4().hex}, key=f"GUESS{i}-AAAAAA-BBBBBB-CCCCCC")[0]
                 for i in range(12)]
        assert codes[:10] == [403] * 10 and codes[10:] == [429, 429]
        assert raw.agent_call("poll", {"agent_id": uuid.uuid4().hex})[0] == 429      # (even the right key waits)
    finally:
        s.close()


def test_the_agent_endpoints_do_not_exist_on_a_server_that_is_not_a_hub():
    s = srv.make_server("127.0.0.1", 0, key=SERVER_KEY, files=False)
    t = threading.Thread(target=s.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True)
    t.start()
    try:
        c = Client(s.server_address[1])
        assert c.agent_call("register", {"agent_id": uuid.uuid4().hex, "token": TOKEN})[0] == 404
        assert c.login(SERVER_KEY)[0] == 200                       # (it is the plain server: its key signs in)
    finally:
        s.shutdown()
        s.server_close()


def test_register_refuses_bad_ids_bad_keys_and_a_key_that_is_taken(hub):
    raw = Client(hub.port)
    assert raw.agent_call("register", {"agent_id": "not-an-id", "token": TOKEN})[1]["error"] == "invalid_agent"
    assert raw.agent_call("register", {"agent_id": uuid.uuid4().hex, "token": "abc"})[1]["error"] == "invalid_client_key"
    one = uuid.uuid4().hex
    assert raw.agent_call("register", {"agent_id": one, "token": TOKEN})[0] == 200
    status, out = raw.agent_call("register", {"agent_id": uuid.uuid4().hex, "token": TOKEN.lower().replace("-", " ")})
    assert (status, out["error"]) == (409, "key_taken")
    assert raw.agent_call("register", {"agent_id": one, "token": TOKEN})[0] == 200      # (the same agent again is fine)


def test_a_browser_signs_in_with_the_client_key_and_never_with_the_server_key(hub):
    hub.agent()
    c = Client(hub.port)
    assert c.login(SERVER_KEY)[0] == 401
    assert c.api("ping")[0] == 401
    status, out = c.login("ZZZZZZ-ZZZZZZ-ZZZZZZ-ZZZZZZ")
    assert (status, out["error"]) == (401, "invalid_key")
    assert c.login("abcdef ghjkmn pqrstu vwxyz2")[0] == 200       # (dashes, spaces and case do not matter)
    assert c.api("ping")[0] == 200


def test_an_address_that_keeps_guessing_client_keys_waits(hub):
    hub.agent()
    c = Client(hub.port)
    codes = [c.login(f"GUESS{i}-AAAAAA-BBBBBB-CCCCCC")[0] for i in range(12)]
    assert codes[:10] == [401] * 10 and codes[10:] == [429, 429]


# ---------------------------------------------------------------------------------------------
# the agent answers
# ---------------------------------------------------------------------------------------------

def test_what_the_agent_answers_is_what_the_service_answers(hub):
    hub.agent()
    c = hub.browser()
    direct = ServiceHost()

    def plain(answer):                  # (what a JSON round trip makes of it: tuples are lists)
        return json.loads(json.dumps(answer))

    assert c.api("ping") == (200, plain(direct.handle("ping", {})))
    status, out = c.api("schema")
    want = plain(direct.handle("schema", {}))
    # (the tools of a fresh service have ids of their own; everything else is the same)
    assert status == 200 and {k: v for k, v in out.items() if k != "tools"} == {k: v for k, v in want.items() if k != "tools"}
    assert [t["name"] for t in out["tools"]] == [t["name"] for t in want["tools"]]
    status, out = c.api("validate", {"project": {"not": "a project"}})
    assert status == 400 and out == plain(direct.handle("validate", {"project": {"not": "a project"}}))
    assert c.api("no_such_operation")[1]["error"] == "unknown_operation"


def test_the_files_are_those_of_the_agents_computer(hub):
    (hub.folder / "part.dxf").write_text("0\nSECTION\n2\nENTITIES\n0\nENDSEC\n0\nEOF\n")
    (hub.folder / "notes.docx").write_text("x")
    hub.agent()
    c = hub.browser()
    status, out = c.api("status")
    assert status == 200 and out["files"] is True and out["roots"] == [str(hub.folder.resolve())]
    status, out = c.api("list", {"path": str(hub.folder)})
    assert status == 200 and [e["name"] for e in out["entries"]] == ["part.dxf"]
    status, out = c.api("list", {"path": str(hub.folder.parent)})              # outside the roots
    assert (status, out["error"]) == (403, "path_not_allowed")
    status, out = c.api("save_files", {"folder": str(hub.folder), "files": [{"name": "a.nc", "text": "G0 X0\n"}]})
    assert status == 200 and (hub.folder / "a.nc").read_text() == "G0 X0\n"       # written on the agent's disk
    status, out = c.api("save_files", {"folder": str(hub.folder), "files": [{"name": "a.nc", "text": "G0 X1\n"}]})
    assert (status, out["error"]) == (409, "file_exists")


def test_an_agent_without_files_says_so(tmp_path, folder):
    s = Setup(tmp_path, folder)
    try:
        s.agent(use_files=False)
        c = s.browser()
        assert c.api("status")[1] == {"files": False, "roots": [], "limits": {}}
        assert c.api("list", {"path": str(folder)})[1]["error"] == "files_disabled"
    finally:
        s.close()


def test_two_agents_two_keys_each_browser_gets_its_own_computer(tmp_path, folder):
    other = tmp_path / "other"
    other.mkdir()
    (other / "other.dxf").write_text("x")
    (folder / "mine.dxf").write_text("x")
    s = Setup(tmp_path, folder)
    try:
        s.agent()
        b = agent_mod.Agent(f"http://127.0.0.1:{s.port}", SERVER_KEY, uuid.uuid4().hex, "BBBBBB-CCCCCC-DDDDDD-EEEEEE",
                            name="Office", roots=[str(other)], poll_timeout=5)
        s.agents.append(b)
        b.start()
        s.wait(lambda: b.state == "connected")
        one, two = s.browser(), s.browser("BBBBBB-CCCCCC-DDDDDD-EEEEEE")
        assert [e["name"] for e in one.api("list", {"path": str(folder)})[1]["entries"]] == ["mine.dxf"]
        assert two.api("list", {"path": str(folder)})[1]["error"] == "path_not_allowed"
        assert [e["name"] for e in two.api("list", {"path": str(other)})[1]["entries"]] == ["other.dxf"]
    finally:
        s.close()


def test_calls_at_the_same_time_are_all_answered(hub):
    hub.agent()
    c = hub.browser()
    results = []

    def go(i):
        cc = Client(hub.port)
        cc.cookie = c.cookie
        results.append(cc.api("ping")[0])

    threads = [threading.Thread(target=go, args=(i,)) for i in range(8)]
    [t.start() for t in threads]
    [t.join(30) for t in threads]
    assert results == [200] * 8


def test_an_answer_too_large_for_the_hub_is_told_instead_of_lost():
    a = agent_mod.Agent("http://127.0.0.1:1", "K", uuid.uuid4().hex, TOKEN)
    a.max_body = 10_000
    small = json.loads(a._reply_body("j1", 200, {"x": "y"}))
    assert small["status"] == 200 and small["out"] == {"x": "y"}
    big = json.loads(a._reply_body("j2", 200, {"x": "y" * 20_000}))
    assert big["status"] == 502 and big["out"]["error"] == "reply_too_large"
    odd = json.loads(a._reply_body("j3", 200, {"x": float("nan")}))
    assert odd["status"] == 500 and odd["out"]["error"] == "internal_error"


# ---------------------------------------------------------------------------------------------
# an agent that is away or silent
# ---------------------------------------------------------------------------------------------

def test_no_agent_connected_is_told_at_once(hub):
    raw = Client(hub.port)
    assert raw.agent_call("register", {"agent_id": uuid.uuid4().hex, "token": TOKEN, "name": "x"})[0] == 200
    hub.hub.online_sec = 0.0                                       # (it registered but never polls: not connected)
    c = hub.browser()
    status, out = c.api("ping")
    assert (status, out["error"]) == (503, "agent_offline")


def test_the_agent_going_away_is_noticed(hub):
    a = hub.agent()
    c = hub.browser()
    assert c.api("ping")[0] == 200
    a.stop()
    hub.hub.online_sec = 0.2
    time.sleep(0.4)
    assert c.api("ping")[1]["error"] == "agent_offline"
    hub.hub.online_sec = 60
    a2 = hub.agent(agent_id=a.agent_id)                           # it comes back (the same agent) and all is well again
    assert a2.state == "connected" and c.api("ping")[0] == 200


def test_an_agent_that_takes_a_call_and_never_answers_times_out(tmp_path, folder):
    s = Setup(tmp_path, folder, job_timeout=0.6)
    try:
        raw = Client(s.port)
        agent_id = uuid.uuid4().hex
        assert raw.agent_call("register", {"agent_id": agent_id, "token": TOKEN})[0] == 200
        c = s.browser()
        got = {}
        t = threading.Thread(target=lambda: got.update(r=c.api("ping")))
        t.start()
        job = None
        for _ in range(20):                                        # the agent takes the call ... and sits on it
            job = raw.agent_call("poll", {"agent_id": agent_id})[1].get("job")
            if job:
                break
        t.join(10)
        assert job and job["op"] == "ping"
        assert got["r"][0] == 504 and got["r"][1]["error"] == "agent_timeout"
        late = raw.agent_call("reply", {"agent_id": agent_id, "job_id": job["id"], "status": 200, "out": {"ok": 1}})
        assert late[1] == {"ok": False}                            # (nobody waits for a late answer any more)
    finally:
        s.close()


def test_an_agent_that_goes_silent_while_a_call_waits_is_noticed_not_waited_for(tmp_path, folder):
    s = Setup(tmp_path, folder, job_timeout=120.0)              # (a long wait that must not be used)
    try:
        s.hub.online_sec = 1.0
        raw = Client(s.port)
        agent_id = uuid.uuid4().hex
        assert raw.agent_call("register", {"agent_id": agent_id, "token": TOKEN})[0] == 200
        c = s.browser()
        begun = time.time()
        status, out = c.api("ping")                              # nobody polls: after a second it is "not connected"
        assert (status, out["error"]) == (503, "agent_offline") and time.time() - begun < 10
    finally:
        s.close()


def test_the_page_gets_the_error_of_the_agent_with_its_status(hub):
    hub.agent()
    c = hub.browser()
    status, out = c.api("job.status", {"id": "nope"})
    assert (status, out["error"]) == (404, "job_not_found")


# ---------------------------------------------------------------------------------------------
# keys that change, a hub that restarts
# ---------------------------------------------------------------------------------------------

def test_a_new_client_key_ends_the_sessions_of_the_old_one(hub):
    a = hub.agent()
    c = hub.browser()
    assert c.api("ping")[0] == 200
    new = make_key()
    a.register(new)                                                # what 'iyicam agent --new-key' does
    assert c.api("ping")[1]["error"] == "not_logged_in"
    assert Client(hub.port).login(TOKEN)[0] == 401
    assert hub.browser(new).api("ping")[0] == 200


def test_a_restart_of_the_hub_keeps_its_agents_and_the_agent_comes_back_by_itself(tmp_path, folder):
    s = Setup(tmp_path, folder)
    a = s.agent()
    c = s.browser()
    assert c.api("ping")[0] == 200
    s.hub.close()
    s.server.shutdown()
    s.server.server_close()
    port = s.port
    # the same hub state on the same port: the registered agents are remembered; sessions are not (they are in memory)
    hub2 = Hub(KeyStore(fixed=SERVER_KEY), tmp_path / "agents.json", hold=0.3)
    s2 = None
    for _ in range(50):
        try:
            s2 = srv.make_server("127.0.0.1", port, hub=hub2)
            break
        except OSError:
            time.sleep(0.1)
    assert s2 is not None
    threading.Thread(target=s2.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True).start()
    try:
        assert hub2.agent_for_key(TOKEN) == a.agent_id             # (the key still opens a door before the agent polls)
        Setup.wait(lambda: hub2.is_online(a.agent_id))
        assert Client(port).login()[0] == 200
    finally:
        a.stop()
        s2.shutdown()
        s2.server_close()


def test_the_registry_keeps_digests_never_keys(tmp_path, folder):
    s = Setup(tmp_path, folder)
    try:
        s.agent()
        text = (tmp_path / "agents.json").read_text()
        assert TOKEN not in text and TOKEN.replace("-", "") not in text and "SRVKEY" not in text
        assert json.loads(text)["agents"]
    finally:
        s.close()


# ---------------------------------------------------------------------------------------------
# the agent as a program
# ---------------------------------------------------------------------------------------------

def test_an_agent_with_a_wrong_server_key_says_so_and_does_not_hammer_the_hub(hub):
    a = hub.agent(server_key="WRONG-WRONG-WRONG-WRONG", start=False)
    a.start()
    Setup.wait(lambda: a.state == "rejected")
    assert "refused" in a._message and "server key" in a._message
    time.sleep(0.3)
    assert hub.hub.agent_for_key(TOKEN) is None                    # it never got in


def test_an_agent_with_an_address_that_does_not_answer_keeps_trying(tmp_path):
    log = tmp_path / "agent.log"
    state = tmp_path / "agent.state.json"
    a = agent_mod.Agent("http://127.0.0.1:1", SERVER_KEY, uuid.uuid4().hex, TOKEN, poll_timeout=1,
                        log_path=log, state_path=state)
    a.start()
    Setup.wait(lambda: a.state == "offline")
    a.stop()
    assert "no connection to the hub" in log.read_text()
    data = json.loads(state.read_text())
    assert data["state"] == "stopped"
    assert TOKEN not in log.read_text() and TOKEN not in state.read_text() and SERVER_KEY not in state.read_text()


def test_the_state_file_tells_that_the_agent_is_connected(tmp_path, folder):
    s = Setup(tmp_path, folder)
    try:
        state = tmp_path / "agent.state.json"
        a = s.agent(state_path=state, log_path=tmp_path / "agent.log")
        data = json.loads(state.read_text())
        assert data["state"] == "connected" and data["name"] == "Workshop" and data["last_ok"] > 0
        assert "connected to the hub" in (tmp_path / "agent.log").read_text()
        a.stop()
    finally:
        s.close()


def test_iyicam_agent_makes_its_settings_and_a_new_key(hub, home, capsys):
    url = f"http://127.0.0.1:{hub.port}"
    assert agent_mod.run(show_key=True) == 1                       # nothing set up yet
    assert agent_mod.run(url, SERVER_KEY, name="Bench", roots=[str(hub.folder)], new_key=True) == 0
    first = capsys.readouterr().out.strip().splitlines()[-1]
    assert agent_mod.run(show_key=True) == 0
    shown = capsys.readouterr().out.strip()
    settings = json.loads((home / "agent.json").read_text())
    assert settings["server_url"] == url and settings["server_key"] == SERVER_KEY and settings["name"] == "Bench"
    assert settings["roots"] == [str(hub.folder)] and len(settings["agent_id"]) == 32
    assert shown == first == settings["token"]                     # the new key is the one that is kept and shown
    assert hub.hub.agent_for_key(settings["token"]) == settings["agent_id"]
    assert Client(hub.port).login(settings["token"])[0] == 200     # (no agent polls: the key still opens the door)
    # another new key: the old one is gone from the hub and from the file
    old = settings["token"]
    assert agent_mod.run(new_key=True) == 0
    newer = json.loads((home / "agent.json").read_text())["token"]
    assert newer != old and hub.hub.agent_for_key(old) is None and hub.hub.agent_for_key(newer) == settings["agent_id"]


def test_a_new_key_that_the_hub_does_not_take_changes_nothing(hub, home, capsys):
    url = f"http://127.0.0.1:{hub.port}"
    assert agent_mod.run(url, SERVER_KEY, new_key=True) == 0
    before = (home / "agent.json").read_text()
    assert agent_mod.run(url, "WRONG-WRONG-WRONG-WRONG", new_key=True) == 1
    settings = json.loads((home / "agent.json").read_text())
    assert settings["token"] == json.loads(before)["token"] and settings["server_key"] == SERVER_KEY
    assert "nothing was changed" in capsys.readouterr().err


def test_setup_keeps_the_settings_only_when_the_hub_takes_them(hub, home, capsys):
    url = f"http://127.0.0.1:{hub.port}"
    assert agent_mod.run(url, "WRONG-WRONG-WRONG-WRONG", setup=True) == 1
    assert not (home / "agent.json").exists()                      # (a wrong key leaves nothing behind)
    assert agent_mod.run(url, SERVER_KEY, name="Bench", setup=True) == 0
    key = capsys.readouterr().out.strip().splitlines()[-1]
    settings = json.loads((home / "agent.json").read_text())
    assert settings["token"] == key and hub.hub.agent_for_key(key) == settings["agent_id"]
    assert agent_mod.run(setup=True) == 0                          # again: the same agent, the same key
    assert capsys.readouterr().out.strip().splitlines()[-1] == key
    assert json.loads((home / "agent.json").read_text())["agent_id"] == settings["agent_id"]


def test_the_command_line_knows_the_agent_and_the_hub():
    import subprocess
    import sys
    out = subprocess.run([sys.executable, "-m", "iyicam", "agent", "--help"], capture_output=True, text=True,
                         timeout=60).stdout
    for flag in ("--server-url", "--server-key", "--root", "--no-files", "--show-key", "--new-key"):
        assert flag in out, flag
    out = subprocess.run([sys.executable, "-m", "iyicam", "serve", "--help"], capture_output=True, text=True,
                         timeout=60).stdout
    assert "--hub" in out

"""``iyicam serve`` and ``iyicam agent``: the app and the zip, the key, the files of this computer (--local) and of an agent
that connects out, several browsers on one agent, and what a stranger can not do."""
import http.client
import io
import json
import threading
import time
import zipfile

import pytest

from iyicam.service.keys import KeyStore
from iyicam.share import server as share_server
from iyicam.share.agent import Agent
from iyicam.share.relay import Relay

KEY = "ABCDEF-GHJKMN-PQRSTU-VWXYZ2"


class Browser:
    """A little HTTP client with a cookie, like a tab."""

    def __init__(self, port):
        self.port, self.cookie = port, None

    def req(self, method, path, body=None, headers=None):
        c = http.client.HTTPConnection("127.0.0.1", self.port, timeout=20)
        h = dict(headers or {})
        if self.cookie:
            h["Cookie"] = self.cookie
        if isinstance(body, (dict, list)):
            body, h["Content-Type"] = json.dumps(body), "application/json"
        c.request(method, path, body=body, headers=h)
        r = c.getresponse()
        data = r.read()
        sc = r.getheader("Set-Cookie")
        if sc and "Max-Age=0" not in sc:
            self.cookie = sc.split(";")[0]
        elif sc:
            self.cookie = None
        c.close()
        return r.status, dict(r.getheaders()), data

    def json(self, method, path, body=None, headers=None):
        st, h, data = self.req(method, path, body, headers)
        return st, (json.loads(data) if data and h.get("Content-Type", "").startswith("application/json") else data)

    def login(self, key=KEY, **kw):
        return self.json("POST", "/api/login", {"key": key, **kw})


def start(tmp_path, **kw):
    relay = kw.pop("relay", None)
    srv = share_server.make_server("127.0.0.1", 0, keys=KeyStore(fixed=KEY), sessions_path=tmp_path / "sessions.json", relay=relay, **kw)
    threading.Thread(target=srv.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True).start()
    return srv


@pytest.fixture
def shared(tmp_path):
    d = tmp_path / "jobs"
    (d / "sub").mkdir(parents=True)
    (d / "a.icam").write_text('{"x":1}')
    return d


@pytest.fixture
def local(tmp_path, shared):
    srv = start(tmp_path, local=True, roots=[str(shared)], max_file_mb=1)
    yield srv, Browser(srv.server_address[1])
    srv.shutdown()
    srv.server_close()


@pytest.fixture
def relayed(tmp_path, shared):
    """A server without files of its own and an agent that connects to it."""
    srv = start(tmp_path, relay=Relay(hold=0.3, online_sec=2.0, job_timeout=8.0), max_file_mb=1)
    url = f"http://127.0.0.1:{srv.server_address[1]}"
    agent = Agent(url, KEY, [str(shared)], name="workshop", max_bytes=1024 * 1024, quiet=True, hold_timeout=10)
    t = threading.Thread(target=agent.run, daemon=True)
    t.start()
    yield srv, agent, shared
    agent.stop.set()
    t.join(5)
    srv.shutdown()
    srv.server_close()


def wait_for(cond, seconds=5.0):
    end = time.time() + seconds
    while time.time() < end:
        if cond():
            return True
        time.sleep(0.05)
    return False


# ---------------------------------------------------------------------------------------------
# the app
# ---------------------------------------------------------------------------------------------

def test_the_app_and_the_package_need_no_key_and_are_cross_origin_isolated(local):
    srv, b = local
    st, h, _ = b.req("GET", "/")
    assert st == 302 and h["Location"] == "/app/"
    st, h, body = b.req("GET", "/app/bridge.py")
    assert st == 200 and b"def call(" in body
    assert h["Cross-Origin-Opener-Policy"] == "same-origin" and h["Cross-Origin-Embedder-Policy"] == "require-corp"
    assert "wasm-unsafe-eval" in h["Content-Security-Policy"] and "unsafe-eval'" not in h["Content-Security-Policy"].replace("wasm-unsafe-eval", "")
    st, cfg = b.json("GET", "/app/config.json")
    assert st == 200 and cfg["pyodide"].startswith("https://cdn.jsdelivr.net/pyodide/v0.27.7") and cfg["zip"] == "/app/py/iyicam.zip"


def test_the_package_zip_holds_the_engine_and_not_the_window_or_the_server(local):
    srv, b = local
    st, h, data = b.req("GET", "/app/py/iyicam.zip")
    assert st == 200 and h["Content-Type"] == "application/zip"
    names = zipfile.ZipFile(io.BytesIO(data)).namelist()
    assert "iyicam/service/ops.py" in names and "iyicam/cam/job.py" in names and "iyicam/__init__.py" in names
    assert not [n for n in names if n.startswith(("iyicam/gui/", "iyicam/web/", "iyicam/webapp/", "iyicam/share/")) or n.endswith(".pyc") or "__pycache__" in n]
    assert "iyicam/cli.py" not in names
    st, _, _ = b.req("GET", "/app/py/iyicam.zip", headers={"If-None-Match": h["ETag"]})
    assert st == 304                                                       # (revalidated, not sent again)


def test_static_files_cannot_be_reached_outside_the_app(local):
    srv, b = local
    for path in ("/app/../share/server.py", "/app/%2e%2e/share/server.py", "/app/.hidden", "/app/engine/../../cli.py",
                 "/app/__pycache__/x", "/app/nothing.js"):
        assert b.req("GET", path)[0] == 404, path
    assert b.req("GET", "/app/engine/engine.js")[0] == 200


def test_a_folder_of_pyodide_of_its_own_is_served_and_announced(tmp_path):
    py = tmp_path / "pyodide"
    py.mkdir()
    (py / "pyodide.mjs").write_text("export {}")
    (py / "ezdxf-1.0-py3-none-any.whl").write_bytes(b"PK")
    (py / "numpy-2.whl").write_bytes(b"PK")
    srv = start(tmp_path, pyodide_dir=py)
    try:
        b = Browser(srv.server_address[1])
        st, cfg = b.json("GET", "/app/config.json")
        assert cfg["pyodide"] == "/app/pyodide/" and cfg["wheels"] == ["/app/pyodide/ezdxf-1.0-py3-none-any.whl"]
        assert b.req("GET", "/app/pyodide/pyodide.mjs")[0] == 200
        assert b.req("GET", "/app/pyodide/../bridge.py")[0] == 404
    finally:
        srv.shutdown()
        srv.server_close()


# ---------------------------------------------------------------------------------------------
# the key
# ---------------------------------------------------------------------------------------------

def test_sign_in_with_the_key_and_out(local):
    srv, b = local
    assert b.json("GET", "/api/session")[1]["signed_in"] is False
    assert b.json("GET", "/api/files/list?path=")[0] == 401
    assert b.login("WRONG-KEY-000000-000000")[0] == 401
    st, out = b.login("abcdef ghjkmn pqrstu vwxyz2")                       # (case, dashes and spaces do not matter)
    assert st == 200 and out["signed_in"] is True
    assert "HttpOnly" in b.req("POST", "/api/login", {"key": KEY}, {"Content-Type": "application/json"})[1]["Set-Cookie"]
    assert b.json("GET", "/api/session")[1] == {"signed_in": True, "mode": "local"}
    assert b.json("POST", "/api/logout")[0] == 200
    assert b.json("GET", "/api/session")[1]["signed_in"] is False


def test_wrong_keys_make_an_address_wait(local):
    srv, b = local
    srv.auth.max_fail = 3
    assert [b.login("NOPE-NOPE-NOPE-NOPE-00")[0] for _ in range(3)] == [401, 401, 401]
    st, out = b.login(KEY)                                                 # (even the right key waits now)
    assert st == 429 and out["retry_after"] > 0


def test_a_new_key_ends_every_session(tmp_path, shared):
    keys = KeyStore(path=tmp_path / "k.key")
    srv = share_server.make_server("127.0.0.1", 0, keys=keys, local=True, roots=[str(shared)], sessions_path=tmp_path / "s.json")
    threading.Thread(target=srv.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True).start()
    try:
        b = Browser(srv.server_address[1])
        assert b.login(keys.get())[0] == 200 and b.json("GET", "/api/files/list?path=")[0] == 200
        keys.rotate()
        assert b.json("GET", "/api/files/list?path=")[0] == 401
    finally:
        srv.shutdown()
        srv.server_close()


def test_a_page_of_another_site_cannot_sign_in_or_write(local):
    srv, b = local
    evil = {"Origin": "https://evil.example", "Content-Type": "application/json"}
    assert b.req("POST", "/api/login", json.dumps({"key": KEY}), evil)[0] == 403
    b.login()
    assert b.req("PUT", "/api/files/write?path=jobs/x.nc", b"x", {"Origin": "https://evil.example"})[0] == 403


# ---------------------------------------------------------------------------------------------
# the files of this computer (--local)
# ---------------------------------------------------------------------------------------------

def test_local_files(local, shared):
    srv, b = local
    st = b.json("GET", "/api/files/status")[1]
    assert st == {"signed_in": False, "agent": None, "roots": []}          # (nothing is told to a stranger)
    b.login()
    st = b.json("GET", "/api/files/status")[1]
    assert st["signed_in"] and st["agent"]["local"] is True and st["roots"] == [{"name": "jobs", "path": "jobs"}]
    assert [e["name"] for e in b.json("GET", "/api/files/list?path=")[1]["entries"]] == ["jobs"]
    assert [e["name"] for e in b.json("GET", "/api/files/list?path=jobs")[1]["entries"]] == ["sub", "a.icam"]
    s, h, body = b.req("GET", "/api/files/read?path=jobs/a.icam")
    assert s == 200 and body == b'{"x":1}' and h["Content-Type"] == "application/octet-stream"
    assert b.json("GET", "/api/files/stat?path=jobs/a.icam")[1]["size"] == 7
    st, out = b.json("PUT", "/api/files/write?path=jobs/sub/out.nc", b"G1 X1\n")
    assert st == 200 and out["size"] == 6 and (shared / "sub" / "out.nc").read_bytes() == b"G1 X1\n"
    assert b.json("PUT", "/api/files/write?path=jobs/sub/out.nc", b"x")[0] == 409
    assert b.json("PUT", "/api/files/write?path=jobs/sub/out.nc&overwrite=1", b"x")[0] == 200
    assert b.json("GET", "/api/files/read?path=jobs/nothere.icam")[0] == 404
    st, out = b.json("GET", "/api/files/list?path=jobs/../..")
    assert st == 403 and out["error"] == "path_not_allowed"
    assert b.json("PUT", "/api/files/write?path=jobs/run.exe", b"x")[0] == 415


def test_a_file_over_the_limit_is_refused_and_not_written(local, shared):
    srv, b = local
    b.login()
    st, out = b.json("PUT", "/api/files/write?path=jobs/big.nc", b"x" * (1024 * 1024 + 1))
    assert st == 413 and out["error"] == "file_too_large" and not (shared / "big.nc").exists()


# ---------------------------------------------------------------------------------------------
# an agent
# ---------------------------------------------------------------------------------------------

def test_without_an_agent_the_files_api_says_so(tmp_path):
    srv = start(tmp_path)
    try:
        b = Browser(srv.server_address[1])
        b.login()
        assert b.json("GET", "/api/files/status")[1] == {"signed_in": True, "agent": None, "roots": []}
        st, out = b.json("GET", "/api/files/list?path=")
        assert st == 503 and out["error"] == "no_files"
    finally:
        srv.shutdown()
        srv.server_close()


def test_files_through_an_agent_for_several_browsers(relayed):
    srv, agent, shared = relayed
    port = srv.server_address[1]
    assert wait_for(lambda: srv.relay.status() is not None)
    one, two = Browser(port), Browser(port)
    for b in (one, two):
        assert b.login()[0] == 200
    st = one.json("GET", "/api/files/status")[1]
    assert st["agent"] == {"connected": True, "name": "workshop", "local": False} and st["roots"] == [{"name": "jobs", "path": "jobs"}]
    assert [e["name"] for e in two.json("GET", "/api/files/list?path=jobs")[1]["entries"]] == ["sub", "a.icam"]
    assert one.req("GET", "/api/files/read?path=jobs/a.icam")[2] == b'{"x":1}'
    payload = bytes(range(256)) * 100                                      # (bytes all the way, not text)
    st, out = two.json("PUT", "/api/files/write?path=jobs/sub/blob.nc", payload)
    assert st == 200 and (shared / "sub" / "blob.nc").read_bytes() == payload
    assert one.json("PUT", "/api/files/write?path=jobs/sub/blob.nc", b"x")[1]["error"] == "file_exists"
    assert one.req("GET", "/api/files/read?path=jobs/sub/blob.nc")[2] == payload
    # the agent checks the paths itself: a server that was told to ask for more gets nothing
    st, out = one.json("GET", "/api/files/read?path=jobs/../../etc/passwd")
    assert st == 403 and out["error"] == "path_not_allowed"
    # several at once
    results = []
    threads = [threading.Thread(target=lambda b=b: results.append(b.json("GET", "/api/files/list?path=jobs")[0])) for b in (one, two) * 4]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert results == [200] * 8


def test_the_agent_needs_the_key(relayed):
    srv, agent, _ = relayed
    b = Browser(srv.server_address[1])
    assert wait_for(lambda: srv.relay.status() is not None)
    body = json.dumps({"id": "x" * 12, "name": "evil", "roots": []})
    assert b.req("POST", "/agent/poll", body, {"Authorization": "Bearer WRONG-KEY-0000-0000", "Content-Type": "application/json"})[0] == 401
    assert b.req("POST", "/agent/poll", body, {"Content-Type": "application/json"})[0] == 401
    assert b.req("GET", "/agent/body/abc", headers={"Authorization": "Bearer nope"})[0] == 401
    # a second agent is refused while the first is there
    st, h, out = b.req("POST", "/agent/poll", body, {"Authorization": "Bearer " + KEY, "Content-Type": "application/json"})
    assert st == 409 and json.loads(out)["error"] == "agent_taken"


def test_an_agent_that_goes_away_is_noticed(relayed):
    srv, agent, _ = relayed
    b = Browser(srv.server_address[1])
    b.login()
    assert wait_for(lambda: srv.relay.status() is not None)
    agent.stop.set()
    assert wait_for(lambda: srv.relay.status() is None, 6.0)              # (it said goodbye, or the time ran out)
    assert b.json("GET", "/api/files/status")[1]["agent"] is None
    assert b.json("GET", "/api/files/list?path=jobs")[1]["error"] == "no_files"


def test_the_agent_stops_when_the_key_is_wrong(tmp_path, shared):
    srv = start(tmp_path, relay=Relay(hold=0.2))
    try:
        agent = Agent(f"http://127.0.0.1:{srv.server_address[1]}", "WRONG-WRONG-WRONG-WRONG", [str(shared)], quiet=True)
        assert agent.run() == 2
    finally:
        srv.shutdown()
        srv.server_close()

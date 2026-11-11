"""The web service (``iyicam serve``): a key to get in, then the operations of the service as JSON.

What matters: nothing but the page is reachable without the key, a wrong key never gets a session (and an address
that keeps trying waits), a new key ends every session, only the operations of the service can be called, a request
from another site is refused, and the answers are the SAME as those of the service called directly.
"""
import gzip
import http.client
import json
import threading

import pytest
from conftest import add_tp, end_mill, make_project

from iyicam.core import shapes
from iyicam.service import ServiceHost
from iyicam.service import server as srv

KEY = "ABCDEF-GHJKMN-PQRSTU-VWXYZ2"


class Client:
    """A browser: keeps the cookie the server gives."""

    def __init__(self, server):
        self.port = server.server_address[1]
        self.cookie = None

    def request(self, method, path, body=None, headers=None, raw=None):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=60)
        h = dict(headers or {})
        if self.cookie:
            h["Cookie"] = self.cookie
        data = raw
        if body is not None:
            data = json.dumps(body).encode()
            h.setdefault("Content-Type", "application/json")
        conn.request(method, path, data, h)
        r = conn.getresponse()
        payload = r.read()
        set_cookie = r.getheader("Set-Cookie")
        if set_cookie and set_cookie.startswith(srv.COOKIE + "="):
            value = set_cookie.split(";", 1)[0].split("=", 1)[1]
            self.cookie = f"{srv.COOKIE}={value}" if value else None
        conn.close()
        return r, payload

    def json(self, method, path, body=None, **kw):
        r, payload = self.request(method, path, body, **kw)
        return r.status, (json.loads(payload) if payload else None), r

    def api(self, op, payload=None):
        status, out, _ = self.json("POST", f"/api/{op}", payload or {})
        return status, out

    def login(self, key=KEY):
        return self.json("POST", "/api/login", {"key": key})


@pytest.fixture
def web():
    s = srv.make_server("127.0.0.1", 0, key=KEY)
    t = threading.Thread(target=s.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True)
    t.start()
    yield s
    s.shutdown()
    s.server_close()


@pytest.fixture
def me(web):
    c = Client(web)
    assert c.login()[0] == 200
    return c


@pytest.fixture
def job_project():
    p, s, objs = make_project([[shapes.rectangle(10, 10, 100, 60), shapes.circle((60, 40), 15)],
                               [shapes.circle((200, 60), 30)]], size=(500, 300))
    add_tp(p, "profile", end_mill(6), {"through": True}, objs)
    return p


def wait_job(client, jid, timeout=60):
    import time
    end = time.time() + timeout
    while time.time() < end:
        status, st = client.api("job.status", {"id": jid})
        assert status == 200, st
        if st["state"] in ("done", "error", "cancelled"):
            return st
        time.sleep(0.05)
    raise AssertionError("the job did not finish")


# -- the page -------------------------------------------------------------------------------------

def test_page_and_assets_need_no_key(web):
    c = Client(web)
    r, body = c.request("GET", "/")
    assert r.status == 200 and b"iyiCAM" in body and r.getheader("Content-Type").startswith("text/html")
    assert "default-src 'self'" in r.getheader("Content-Security-Policy")
    assert r.getheader("X-Content-Type-Options") == "nosniff"
    for name, kind in (("app.js", "javascript"), ("app.css", "css"), ("icon.svg", "svg")):
        r, body = c.request("GET", f"/static/{name}")
        assert r.status == 200 and kind in r.getheader("Content-Type") and body
    assert c.request("GET", "/health")[1] == b"ok"


def test_only_the_files_of_the_page_are_served(web):
    c = Client(web)
    for path in ("/static/../service/server.py", "/static/%2e%2e/service/server.py", "/static/__init__.py",
                 "/static/..%5Cservice%5Cserver.py", "/server.py", "/static/nothing.js", "/etc/passwd"):
        r, body = c.request("GET", path)
        assert r.status == 404, path
        assert b"class ServiceServer" not in body


def test_big_answers_are_compressed_when_the_browser_accepts_it(web):
    c = Client(web)
    plain = c.request("GET", "/static/app.js")[1]
    r, packed = c.request("GET", "/static/app.js", headers={"Accept-Encoding": "gzip"})
    assert r.getheader("Content-Encoding") == "gzip" and len(packed) < len(plain)
    assert gzip.decompress(packed) == plain


# -- the key --------------------------------------------------------------------------------------

def test_the_api_is_closed_without_a_session(web):
    c = Client(web)
    assert c.json("GET", "/api/session")[0] == 401
    status, out = c.api("ping")
    assert status == 401 and out["error"] == "not_logged_in"
    status, out = c.api("schema")
    assert status == 401


def test_a_wrong_key_gets_nothing(web):
    c = Client(web)
    for bad in ("", "WRONG", KEY[:-1], KEY + "A", None, 12345, {"a": 1}):
        status, out, r = c.json("POST", "/api/login", {"key": bad})
        assert status == 401 and out["error"] == "invalid_key", bad
        assert r.getheader("Set-Cookie") is None
    assert c.json("POST", "/api/login", {})[0] == 401
    assert c.cookie is None


def test_the_right_key_gives_a_session_cookie(web):
    c = Client(web)
    status, out, r = c.login(KEY.lower().replace("-", " "))          # case, dashes and spaces do not matter
    assert status == 200 and out["ok"]
    cookie = r.getheader("Set-Cookie")
    assert "HttpOnly" in cookie and "SameSite=Strict" in cookie and "Path=/" in cookie and "Secure" not in cookie
    assert c.json("GET", "/api/session")[0] == 200
    status, out = c.api("ping")
    assert status == 200 and out["ok"]


def test_logout_ends_the_session(me):
    assert me.api("ping")[0] == 200
    stale = me.cookie
    status, out, _ = me.json("POST", "/api/logout", {})
    assert status == 200
    me.cookie = stale                                       # a browser that kept the old cookie: it is dead
    assert me.api("ping")[0] == 401


def test_an_address_that_keeps_guessing_waits(web):
    web.auth.max_fail = 3
    c = Client(web)
    for _ in range(3):
        assert c.login("WRONG")[0] == 401
    status, out, r = c.login(KEY)                           # even the right key now waits
    assert status == 429 and out["error"] == "too_many_attempts" and int(r.getheader("Retry-After")) >= 1
    assert c.cookie is None


def test_throttle_forgets_after_its_window(web):
    web.auth.max_fail, web.auth.window = 2, 0.2
    c = Client(web)
    c.login("X")
    c.login("X")
    assert c.login(KEY)[0] == 429
    import time
    time.sleep(0.3)
    assert c.login(KEY)[0] == 200


def test_proxy_headers_count_only_when_trusted(web):
    web.auth.max_fail = 2
    c = Client(web)
    for i in range(2):          # an address cannot pretend to be another one
        c.json("POST", "/api/login", {"key": "X"}, headers={"X-Forwarded-For": f"10.0.0.{i}"})
    assert c.login(KEY)[0] == 429
    web.auth._fails.clear()
    web.trust_proxy = True      # behind a proxy the address is what the proxy saw (the last entry)
    for _ in range(2):
        c.json("POST", "/api/login", {"key": "X"}, headers={"X-Forwarded-For": "6.6.6.6, 10.1.1.1"})
    assert c.json("POST", "/api/login", {"key": KEY}, headers={"X-Forwarded-For": "10.1.1.1"})[0] == 429
    assert c.json("POST", "/api/login", {"key": KEY}, headers={"X-Forwarded-For": "10.2.2.2",
                                                                "X-Forwarded-Proto": "https"})[0] == 200
    r = c.json("POST", "/api/login", {"key": KEY}, headers={"X-Forwarded-For": "10.2.2.2",
                                                             "X-Forwarded-Proto": "https"})[2]
    assert "Secure" in r.getheader("Set-Cookie")


def test_a_new_key_ends_every_session(tmp_path):
    keyfile = tmp_path / "server.key"
    s = srv.make_server("127.0.0.1", 0, key_path=keyfile)
    threading.Thread(target=s.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True).start()
    try:
        old = srv.KeyStore(keyfile).get()
        c = Client(s)
        assert c.login(old)[0] == 200 and c.api("ping")[0] == 200
        new = srv.KeyStore(keyfile).rotate()
        assert new != old
        assert c.api("ping")[0] == 401                       # at once, in the running server
        assert Client(s).login(old)[0] == 401
        c2 = Client(s)
        assert c2.login(new)[0] == 200 and c2.api("ping")[0] == 200
    finally:
        s.shutdown()
        s.server_close()


def test_key_file_is_made_once_and_repaired_when_damaged(tmp_path):
    f = tmp_path / "k" / "server.key"
    ks = srv.KeyStore(f)
    key = ks.get()
    assert f.exists() and len(srv.normalise_key(key)) == 24
    assert all(c in srv.KEY_ALPHABET for c in srv.normalise_key(key))
    assert srv.KeyStore(f).get() == key                          # a second start keeps it
    f.write_text("short\n")                                       # a damaged file is never an open door
    again = srv.KeyStore(f).get()
    assert len(srv.normalise_key(again)) == 24 and again != "short"
    assert len({srv.make_key() for _ in range(50)}) == 50


def test_sessions_are_limited_and_expire(web):
    web.auth.max_sessions = 3
    cs = [Client(web) for _ in range(4)]
    for c in cs:
        assert c.login()[0] == 200
    assert cs[0].api("ping")[0] == 401 and cs[3].api("ping")[0] == 200     # the oldest went
    web.auth.ttl = -1
    c = Client(web)
    c.login()
    assert c.api("ping")[0] == 401


# -- what may be called --------------------------------------------------------------------------

def test_only_service_operations_can_be_called(me):
    for op in ("os.system", "login2", "../ping", "shutdown", "job", "Ping", ""):
        status, out = me.api(op)
        assert status == 404 and out["error"] in ("unknown_operation", "not_found"), op
    assert me.json("POST", "/elsewhere", {})[0] == 404


def test_a_bad_request_is_refused(me, web):
    r, body = me.request("POST", "/api/ping", raw=b"{}", headers={"Content-Type": "text/plain"})
    assert r.status == 415
    r, body = me.request("POST", "/api/ping", raw=b"{not json", headers={"Content-Type": "application/json"})
    assert r.status == 400 and json.loads(body)["error"] == "bad_request"
    r, body = me.request("POST", "/api/ping", raw=b"[1,2]", headers={"Content-Type": "application/json"})
    assert r.status == 400
    r, body = me.request("POST", "/api/ping", raw=b"\xff\xfe", headers={"Content-Type": "application/json"})
    assert r.status == 400
    web.max_body = 1000
    r, body = me.request("POST", "/api/import", raw=b'{"x":"' + b"a" * 2000 + b'"}', headers={"Content-Type": "application/json"})
    assert r.status == 413 and json.loads(body)["error"] == "too_large"


def test_a_request_from_another_site_is_refused(me):
    r, body = me.request("POST", "/api/ping", {}, headers={"Origin": "https://evil.example"})
    assert r.status == 403 and json.loads(body)["error"] == "bad_origin"
    host = f"127.0.0.1:{me.port}"
    r, body = me.request("POST", "/api/ping", {}, headers={"Origin": f"http://{host}"})
    assert r.status == 200
    r, body = me.request("POST", "/api/login", {"key": KEY}, headers={"Origin": "https://evil.example"})
    assert r.status == 403


def test_service_errors_are_http_errors_with_the_service_code(me, job_project):
    status, out = me.api("gcode", {"project": job_project.to_dict(), "post": "nope"})
    assert status == 400 and out["error"] == "unknown_post"
    status, out = me.api("job.status", {"id": "nothing"})
    assert status == 404 and out["error"] == "job_not_found"
    status, out = me.api("validate", {"project": {"a": 1}})
    assert status == 400 and out["error"] == "invalid_project"
    status, out = me.api("import", {"name": "a.xyz", "data_b64": "AAAA"})
    assert status == 400 and out["error"] == "unsupported_file"


# -- the same answers as the service -----------------------------------------------------------------

def test_schema_and_ping_equal_the_service(me):
    host = ServiceHost()
    status, out = me.api("ping")
    assert status == 200 and out["ok"] and out["api"] == host.handle("ping")["api"]
    status, schema = me.api("schema")
    direct = json.loads(json.dumps(host.handle("schema"), allow_nan=False))
    for s in (schema, direct):      # (without a tool file the default tools get new random ids at every call)
        s["tools"] = [{k: v for k, v in t.items() if k != "id"} for t in s["tools"]]
    assert status == 200 and schema == direct


def test_a_job_over_http_gives_the_gcode_of_the_service(me, job_project):
    d = job_project.to_dict()
    direct = ServiceHost().handle("gcode", {"project": d, "post": "mach3"})
    assert direct["ok"]
    status, job = me.api("job.start", {"op": "gcode", "payload": {"project": d, "post": "mach3"}})
    assert status == 200 and job["state"] == "queued"
    assert wait_job(me, job["id"])["state"] == "done"
    status, out = me.api("job.result", {"id": job["id"]})
    assert status == 200 and out["ok"]
    assert [(f["name"], f["text"]) for f in out["files"]] == [(f["name"], f["text"]) for f in direct["files"]]


def test_a_failed_job_is_told_by_its_result(me):
    status, job = me.api("job.start", {"op": "gcode", "payload": {"project": {"not": "a project"}, "post": "mach3"}})
    assert status == 200
    st = wait_job(me, job["id"])                  # the status of a failed job is an answer, its result the error
    assert st["state"] == "error" and st["error"]["error"] == "invalid_project"
    status, out = me.api("job.result", {"id": job["id"]})
    assert status == 400 and out["error"] == "invalid_project"


def test_compute_and_simulate_over_http(me, job_project):
    d = job_project.to_dict()
    status, job = me.api("job.start", {"op": "compute", "payload": {"project": d}})
    assert wait_job(me, job["id"])["state"] == "done"
    out = me.api("job.result", {"id": job["id"]})[1]
    assert out["results"] and out["results"][0]["preview"]["cuts"] and not out["results"][0]["error"]
    status, job = me.api("job.start", {"op": "simulate", "payload": {"project": d}})
    assert wait_job(me, job["id"])["state"] == "done"
    sim = me.api("job.result", {"id": job["id"]})[1]
    import base64
    assert base64.b64decode(sim["png_b64"])[:8] == b"\x89PNG\r\n\x1a\n"


def test_import_a_dxf_and_export_it_over_http(me):
    import base64
    import io

    import ezdxf
    doc = ezdxf.new("R2010")
    doc.modelspace().add_lwpolyline([(0, 0), (100, 0), (100, 50), (0, 50)], close=True)
    buf = io.StringIO()
    doc.write(buf)
    status, out = me.api("import", {"name": "plate.dxf", "data_b64": base64.b64encode(buf.getvalue().encode()).decode(),
                                    "thickness": 12})
    assert status == 200 and out["project"]["material"]["thickness"] == 12
    assert sum(len(s["vectors"]) for s in out["project"]["sheets"]) == 1
    status, exp = me.api("export", {"project": out["project"], "format": "svg"})
    assert status == 200 and exp["files"][0]["name"].endswith(".svg")


def test_many_requests_at_once(web):
    results = []

    def one():
        c = Client(web)
        c.login()
        results.append([c.api("ping")[0] for _ in range(5)])
    ts = [threading.Thread(target=one) for _ in range(8)]
    for t in ts:
        t.start()
    for t in ts:
        t.join(60)
    assert len(results) == 8 and all(r == [200] * 5 for r in results)

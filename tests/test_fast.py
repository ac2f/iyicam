"""Opening files fast: what keeps a big drawing from being slow.

A CorelDRAW file of 25 MB (14 000 vectors, 260 000 curves) took half a minute to read and 17 MB of JSON to send; the
page then drew every curve again at every frame.  What is tested here: a drawing that took long to read is kept (and
read again at once), the file window sorts and searches (also names with Turkish letters), a request may come gzipped
(and a "zip bomb" is not unpacked), the files of the page are sent with an ETag, a quick job answers in the call that
starts it, the reader of CorelDRAW files reads numbers as it did, and an agent and a hub send big answers gzipped."""
import base64
import gzip
import http.client
import json
import os
import struct
import threading
import time
import uuid

import pytest

from iyicam.formats import cdr_native as cn
from iyicam.service import ServiceHost
from iyicam.service import agent as agent_mod
from iyicam.service import files as files_mod
from iyicam.service import server as srv
from iyicam.service.files import Files, _fold

SVG = (b'<svg xmlns="http://www.w3.org/2000/svg" width="100mm" height="50mm" viewBox="0 0 100 50">'
       b'<rect x="5" y="5" width="90" height="40" fill="none" stroke="black"/></svg>')


@pytest.fixture
def env(tmp_path, monkeypatch):
    state = tmp_path / "state"
    state.mkdir()
    monkeypatch.setenv("IYICAM_HOME", str(state))
    monkeypatch.delenv("IYICAM_ROOTS", raising=False)
    root = tmp_path / "root"
    root.mkdir()

    class E:
        pass
    e = E()
    e.tmp, e.state, e.host = tmp_path, state, ServiceHost()
    e.root = os.path.realpath(root)
    e.files = Files(e.host, [e.root])
    return e


def put(env, name, data=SVG, age_days=0):
    path = os.path.join(env.root, name)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as f:
        f.write(data)
    if age_days:
        t = time.time() - age_days * 86400
        os.utime(path, (t, t))
    return path


def names(out):
    return [e["name"] for e in out["entries"]]


# -- the file window: sorting and searching ----------------------------------------------------------------

def test_a_name_is_compared_without_capitals_accents_or_the_turkish_i():
    assert _fold("Çelik KAPI İşleri ığ ÖĞRENCİ") == "celik kapi isleri ig ogrenci"
    assert _fold("Şablon-İç") == "sablon-ic" and _fold("ŞABLON-IÇ") == "sablon-ic"


def test_a_folder_is_sorted_by_name_date_or_size_and_the_folders_stay_first(env):
    put(env, "b.svg", SVG + b" " * 500, age_days=10)
    put(env, "A.svg", SVG, age_days=30)
    put(env, "c.svg", SVG + b" " * 5000, age_days=1)
    os.makedirs(os.path.join(env.root, "zz folder"))
    by = lambda **kw: names(env.files.handle("list", {"path": env.root, **kw}))     # noqa: E731
    assert by() == ["zz folder", "A.svg", "b.svg", "c.svg"]
    assert by(desc=True) == ["zz folder", "c.svg", "b.svg", "A.svg"]
    assert by(sort="date") == ["zz folder", "A.svg", "b.svg", "c.svg"]                      # oldest first
    assert by(sort="date", desc=True) == ["zz folder", "c.svg", "b.svg", "A.svg"]
    assert by(sort="size", desc=True) == ["zz folder", "c.svg", "b.svg", "A.svg"]


def test_a_folder_is_filtered_by_a_part_of_the_name(env):
    for n in ("Çelik Kapı.svg", "kapak_01.svg", "KAPAK_02.svg", "Şablon-İç.svg"):
        put(env, n)
    os.makedirs(os.path.join(env.root, "Kapaklar eski"))
    q = lambda text: names(env.files.handle("list", {"path": env.root, "q": text}))          # noqa: E731
    assert q("kapak") == ["Kapaklar eski", "kapak_01.svg", "KAPAK_02.svg"]
    assert q("CELIK") == ["Çelik Kapı.svg"] and q("sablon-ic") == ["Şablon-İç.svg"] and q("yok") == []


def test_a_folder_that_is_too_big_is_cut_after_it_is_sorted(env, monkeypatch):
    monkeypatch.setattr(files_mod, "LIST_LIMIT", 3)
    for i in range(6):
        put(env, f"f{i}.svg", age_days=10 - i)                 # f5 is the newest
    out = env.files.handle("list", {"path": env.root, "sort": "date", "desc": True})
    assert out["truncated"] is True and names(out) == ["f5.svg", "f4.svg", "f3.svg"]       # (the newest, not the first found)


def test_the_search_looks_into_the_sub_folders_but_not_into_hidden_ones(env, monkeypatch):
    put(env, "Müşteri İşleri/Dolap Kapağı.svg")
    put(env, "a/b/c/derin kapak.svg")
    put(env, ".gizli/kapak.svg")
    put(env, "kapak notları.txt", b"x")                         # (not a drawing)
    put(env, "root kapak.svg")
    out = env.files.handle("search", {"q": "KAPA"})              # (the g with a breve of Kapağı is a g: "kapa" is in all of them)
    assert sorted(names(out)) == ["Dolap Kapağı.svg", "derin kapak.svg", "root kapak.svg"] and out["truncated"] is False
    assert names(env.files.handle("search", {"path": os.path.join(env.root, "a"), "q": "kapa"})) == ["derin kapak.svg"]
    assert env.files.handle("search", {"q": "  "})["error"] == "bad_request"
    assert env.files.handle("search", {"path": str(env.tmp), "q": "kapa"})["error"] == "path_not_allowed"
    monkeypatch.setattr(files_mod, "SEARCH_LIMIT", 2)
    cut = env.files.handle("search", {"q": "kapa"})
    assert len(cut["entries"]) == 2 and cut["truncated"] is True


# -- a drawing that took long to read is kept ------------------------------------------------------------------

def test_a_drawing_that_took_long_to_read_opens_at_once_the_next_time(env, monkeypatch):
    monkeypatch.setattr(files_mod, "CACHE_MIN_SECONDS", 0.0)
    path = put(env, "big.svg")
    first = env.files.handle("open", {"path": path})
    assert "error" not in first and "cached" not in first and first["hash"]
    second = env.files.handle("open", {"path": path})
    assert second["cached"] is True and second["project"] == first["project"] and second["kind"] == "drawing"
    assert second["path"] == path and second["size"] == os.path.getsize(path)
    cache = [f for f in os.listdir(env.state / "cache")]
    assert len(cache) == 1 and cache[0].endswith(".json.gz")


def test_a_changed_file_is_read_again_and_a_drawing_added_to_a_project_is_not_kept(env, monkeypatch):
    monkeypatch.setattr(files_mod, "CACHE_MIN_SECONDS", 0.0)
    path = put(env, "plate.svg")
    first = env.files.handle("open", {"path": path})
    assert env.files.handle("open", {"path": path})["cached"] is True
    put(env, "plate.svg", SVG.replace(b"90", b"80"))                                       # (another size and time)
    third = env.files.handle("open", {"path": path})
    assert "cached" not in third and third["project"] != first["project"]
    added = env.files.handle("open", {"path": path, "project": first["project"]})
    assert "cached" not in added and "cached" not in env.files.handle("open", {"path": path, "project": first["project"]})


def test_a_project_file_and_a_quick_drawing_are_not_kept(env):
    path = put(env, "plate.svg")                                                           # (read in no time: the default limit)
    assert "cached" not in env.files.handle("open", {"path": path}) and "cached" not in env.files.handle("open", {"path": path})
    assert not (env.state / "cache").exists()


def test_the_cache_keeps_only_the_newest_files(env, monkeypatch):
    monkeypatch.setattr(files_mod, "CACHE_MIN_SECONDS", 0.0)
    monkeypatch.setattr(files_mod, "CACHE_KEEP", 2)
    for i in range(4):
        env.files.handle("open", {"path": put(env, f"p{i}.svg", SVG.replace(b"90", str(80 + i).encode()))})
        time.sleep(0.02)
    assert len(os.listdir(env.state / "cache")) == 2


def test_the_engine_takes_the_bytes_of_a_file_without_base64(env):
    a = env.host.handle("import", {"name": "x.svg", "data": SVG})
    b = env.host.handle("import", {"name": "x.svg", "data_b64": base64.b64encode(SVG).decode()})
    assert "error" not in a and len(a["project"]["sheets"][0]["vectors"]) == len(b["project"]["sheets"][0]["vectors"]) == 1


# -- a quick job answers in the call that starts it ------------------------------------------------------------------------

def test_a_job_that_is_over_in_time_answers_in_the_call_that_starts_it(env):
    payload = {"name": "x.svg", "data_b64": base64.b64encode(SVG).decode()}
    out = env.host.handle("job.start", {"op": "import", "payload": payload, "wait": 20000})
    assert out["state"] == "done" and len(out["result"]["project"]["sheets"][0]["vectors"]) == 1
    assert env.host.handle("job.status", {"id": out["id"]})["error"] == "job_not_found"       # (nothing is kept for later)
    bad = env.host.handle("job.start", {"op": "import", "payload": {"name": "x.dxf", "data_b64": base64.b64encode(b"nonsense").decode()}, "wait": 20000})
    assert bad["state"] == "error" and isinstance(bad["error"].get("error"), str)
    plain = env.host.handle("job.start", {"op": "import", "payload": payload})                 # (without ``wait``: as before)
    assert plain["state"] == "queued" and env.host.handle("job.status", {"id": plain["id"]})["id"] == plain["id"]


# -- the server: gzip requests and the files of the page -----------------------------------------------------------------

KEY = "ABCDEF-GHJKMN-PQRSTU-VWXYZ2"


@pytest.fixture
def web():
    s = srv.make_server("127.0.0.1", 0, key=KEY, files=False, max_body=1 << 20)
    threading.Thread(target=s.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True).start()
    yield s
    s.shutdown()
    s.server_close()


def call(server, method, path, body=None, headers=None, cookie=None):
    conn = http.client.HTTPConnection("127.0.0.1", server.server_address[1], timeout=30)
    h = dict(headers or {})
    if cookie:
        h["Cookie"] = cookie
    conn.request(method, path, body, h)
    r = conn.getresponse()
    data = r.read()
    out = (r.status, dict(r.getheaders()), data)
    conn.close()
    return out


def sign_in(server):
    status, hdr, _ = call(server, "POST", "/api/login", json.dumps({"key": KEY}).encode(), {"Content-Type": "application/json"})
    assert status == 200
    return hdr["Set-Cookie"].split(";", 1)[0]


def test_a_gzipped_request_is_unpacked_and_a_zip_bomb_is_not(web):
    cookie = sign_in(web)
    body = json.dumps({"pad": "x" * 5000}).encode()                                               # (ping takes anything)
    status, _, data = call(web, "POST", "/api/ping", gzip.compress(body),
                           {"Content-Type": "application/json", "Content-Encoding": "gzip"}, cookie)
    assert status == 200 and json.loads(data)["ok"] is True
    bomb = gzip.compress(b'{"x":"' + b"0" * (60 * 1024 * 1024) + b'"}')                      # (a small body that is 60 MB inside)
    assert len(bomb) < 1 << 20
    status, _, data = call(web, "POST", "/api/ping", bomb, {"Content-Type": "application/json", "Content-Encoding": "gzip"}, cookie)
    assert status == 413 and json.loads(data)["error"] == "too_large"
    status, _, data = call(web, "POST", "/api/ping", b"not gzip", {"Content-Type": "application/json", "Content-Encoding": "gzip"}, cookie)
    assert status == 400 and json.loads(data)["error"] == "bad_request"


def test_the_files_of_the_page_come_with_an_etag_and_then_as_not_modified(web):
    status, hdr, body = call(web, "GET", "/static/app.js", headers={"Accept-Encoding": "gzip"})
    assert status == 200 and hdr["ETag"] and hdr["Content-Encoding"] == "gzip" and gzip.decompress(body)
    etag = hdr["ETag"]
    status, hdr2, body2 = call(web, "GET", "/static/app.js", headers={"If-None-Match": etag})
    assert status == 304 and body2 == b"" and hdr2["ETag"] == etag
    status, _, plain = call(web, "GET", "/static/app.js")                                    # (without gzip: the file itself)
    assert status == 200 and plain == gzip.decompress(body)
    assert call(web, "GET", "/static/app.js", headers={"If-None-Match": '"other"'})[0] == 200


def test_a_file_can_be_sent_as_it_is_and_is_read_like_any_import(web):
    cookie = sign_in(web)
    status, _, data = call(web, "POST", "/api/import_file?name=plate%20%C3%A7.svg&thickness=12", SVG,
                           {"Content-Type": "application/octet-stream"}, cookie)
    out = json.loads(data)
    assert status == 200 and out["project"]["name"] == "plate ç" and out["project"]["material"]["thickness"] == 12.0
    assert len(out["project"]["sheets"][0]["vectors"]) == 1
    same = ServiceHost().handle("import", {"name": "plate ç.svg", "data": SVG, "thickness": 12})
    assert same["project"]["sheets"][0]["width"] == out["project"]["sheets"][0]["width"]
    # what is not right is refused, not swallowed
    h = {"Content-Type": "application/octet-stream"}
    assert call(web, "POST", "/api/import_file?name=a.svg", b"", h, cookie)[0] == 400
    assert call(web, "POST", "/api/import_file?name=a.svg&thickness=abc", SVG, h, cookie)[0] == 400
    assert call(web, "POST", "/api/import_file?name=a.svg", b"x" * ((1 << 20) + 1), h, cookie)[0] == 413          # (the limit of this server)
    status, _, data = call(web, "POST", "/api/import_file?name=a.dxf", b"nonsense", h, cookie)
    assert status == 400 and json.loads(data)["error"]
    assert call(web, "POST", "/api/import_file?name=a.svg", SVG, h)[0] == 401                                      # (no key, no import)


# -- the reader of CorelDRAW files ---------------------------------------------------------------------------------------

def test_the_reader_reads_its_numbers_as_it_did_and_stops_at_the_end():
    data = struct.pack("<BHIhid", 7, 513, 0xDEADBEEF, -2, -70000, 1.5) + b"\x01\x02\x03"
    r = cn._R(data)
    assert (r.u8(), r.u16(), r.u32(), r.s16(), r.s32(), r.f64()) == (7, 513, 0xDEADBEEF, -2, -70000, 1.5)
    assert r.p == len(data) - 3 and not r.end()
    with pytest.raises(cn._Short):
        r.u32()
    assert r.p == len(data) - 3                                              # (a failed read moves nothing)
    assert list(cn._Parser._types(r, 3)) == [1, 2, 3] and r.end()
    with pytest.raises(cn._Short):
        cn._Parser._types(cn._R(b"\x01"), 2)


def test_a_path_is_read_in_one_go_to_the_same_points():
    pts = [(10.5, -3.25), (0.0, 7.0), (12.345, 0.001)]
    for bits, div, fmt in ((32, 254000.0, "<i"), (16, 1000.0, "<h")):
        raw = b"".join(struct.pack(fmt, round(v * div)) for p in pts for v in p)
        parser = cn._Parser(object(), [])
        parser.prec = bits
        got = parser._points(cn._R(raw), len(pts))
        want = [(round(x * div) / div, round(y * div) / div) for x, y in pts]
        assert got == want
    parser = cn._Parser(object(), [])
    with pytest.raises(cn.Unsupported):                                       # (the numbers before the version are not read)
        parser._points(cn._R(b"\0" * 16), 2)
    parser.prec = 32
    with pytest.raises(cn._Short):
        parser._points(cn._R(b"\0" * 7), 1)


def test_an_agent_sends_plain_to_a_hub_that_does_not_know_gzip():
    """A hub of the version before this one answers "not valid JSON" to a gzipped body: the agent tells it plain."""
    from http.server import BaseHTTPRequestHandler, HTTPServer
    seen = []

    class Old(BaseHTTPRequestHandler):
        def do_POST(self):                                              # noqa: N802
            raw = self.rfile.read(int(self.headers["Content-Length"]))
            seen.append(self.headers.get("Content-Encoding"))
            try:
                json.loads(raw.decode("utf-8"))
                code, out = 200, b'{"ok":true}'
            except (UnicodeDecodeError, ValueError):
                code, out = 400, b'{"error":"bad_request","message":"the body is not valid JSON"}'
            self.send_response(code)
            self.send_header("Content-Length", str(len(out)))
            self.end_headers()
            self.wfile.write(out)

        def log_message(self, *a):
            pass

    httpd = HTTPServer(("127.0.0.1", 0), Old)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    try:
        a = agent_mod.Agent(f"http://127.0.0.1:{httpd.server_address[1]}", "K", uuid.uuid4().hex, "ABCDEF-GHJKMN-PQRSTU-VWXYZ2")
        code, out = a.request("/agent/reply", {"pad": "x" * 10000}, 10)
        assert (code, out) == (200, {"ok": True}) and seen == ["gzip", None] and a._gzip_ok is False
        assert a.request("/agent/reply", {"pad": "y" * 10000}, 10)[0] == 200 and seen == ["gzip", None, None]   # (plain from now on)
    finally:
        httpd.shutdown()
        httpd.server_close()


# -- a hub and an agent: big answers go gzipped ----------------------------------------------------------------------------------

def test_a_big_answer_and_a_big_call_go_through_the_hub_gzipped(tmp_path, monkeypatch):
    from test_hub import SERVER_KEY, TOKEN, Client, Setup                     # (the same hub, agent and browser as there)
    monkeypatch.setenv("IYICAM_HOME", str(tmp_path / "home"))
    folder = tmp_path / "files"
    folder.mkdir()
    sent = []
    real = agent_mod.gzip.compress
    monkeypatch.setattr(agent_mod.gzip, "compress", lambda data, level=9: (sent.append(len(data)), real(data, level))[1])
    s = Setup(tmp_path, folder)
    try:
        s.agent()
        c = Client(s.port)
        assert c.login(TOKEN)[0] == 200
        circle = {"closed": True, "start": [10, 0], "segs": [["A", -10, 0, 1], ["A", 10, 0, 1]]}
        vectors = [{"id": uuid.uuid4().hex[:12], "layer": "L", "name": f"v{i}", "contours": [circle]} for i in range(400)]
        project = {"format": "iyicam-project", "version": 1, "name": "big",
                   "layers": [{"id": "L", "name": "Default", "color": "#1f77b4", "visible": True, "locked": False}],
                   "sheets": [{"id": "S", "name": "Sheet 1", "width": 600, "height": 400, "thickness": None, "vectors": vectors, "outline": None}]}
        status, out = c.api("validate", {"project": project})
        assert status == 200 and out["counts"]["vectors"] == 400
        assert len(json.dumps(out)) > agent_mod.GZIP_FROM and sent and max(sent) > agent_mod.GZIP_FROM       # (the answer went gzipped)
        # a file sent as it is is passed on to the agent (as base64 between the hub and the agent) and read there
        status, _, data = call(s.server, "POST", "/api/import_file?name=plate.svg&thickness=15", SVG,
                               {"Content-Type": "application/octet-stream"}, c.cookie)
        got = json.loads(data)
        assert status == 200 and got["project"]["material"]["thickness"] == 15.0 and len(got["project"]["sheets"][0]["vectors"]) == 1
    finally:
        s.close()

"""Files of the computer for the web page (``iyicam.service.files``) and their door in the server.

What matters: nothing outside the roots is reachable (a ``..``, a symbolic link, a hidden folder, the folder in
which iyiCAM keeps its key), only known kinds of files are read or written and never a program, a file that exists is
replaced only knowingly, and every failure is an answer, not an exception.  The engine is the real one."""
import base64
import hashlib
import json
import os
import threading

import pytest
from conftest import add_tp, end_mill, make_project

from iyicam.core import shapes
from iyicam.service import ServiceHost
from iyicam.service import files as files_mod
from iyicam.service import server as srv
from iyicam.service.files import Files

SVG = (b'<svg xmlns="http://www.w3.org/2000/svg" width="100mm" height="50mm" viewBox="0 0 100 50">'
       b'<rect x="5" y="5" width="90" height="40" fill="none" stroke="black"/></svg>')


@pytest.fixture
def env(tmp_path, monkeypatch):
    """A root with a few files, a state folder of iyiCAM beside it, and the engine."""
    state = tmp_path / "state"
    state.mkdir()
    monkeypatch.setenv("IYICAM_HOME", str(state))
    monkeypatch.delenv("IYICAM_ROOTS", raising=False)
    root = tmp_path / "root"
    (root / "jobs" / "sub").mkdir(parents=True)
    (root / "jobs" / "plate.svg").write_bytes(SVG)
    (root / "readme.md").write_text("not a drawing")
    (root / "program.py").write_text("print(1)")
    (root / "out.nc").write_bytes(b"G0 X0\n")                 # (6 bytes, on every system)
    (root / ".secret").mkdir()
    (root / ".secret" / "a.svg").write_bytes(SVG)
    (root / "outside-name.exe").write_bytes(b"MZ")
    outside = tmp_path / "elsewhere"
    outside.mkdir()
    (outside / "other.svg").write_bytes(SVG)
    host = ServiceHost()

    class E:
        pass
    e = E()
    e.tmp, e.state, e.outside, e.host = tmp_path, state, outside, host
    e.root = os.path.realpath(root)
    e.files = Files(host, [e.root])
    return e


def p(env, *parts):
    return os.path.join(env.root, *parts)


def ok(out):
    assert "error" not in out, out
    return out


def code(out):
    assert isinstance(out.get("error"), str), out
    return out["error"]


@pytest.fixture
def project():
    pr, s, objs = make_project([[shapes.rectangle(10, 10, 100, 60), shapes.circle((60, 40), 15)]], size=(300, 200))
    add_tp(pr, "profile", end_mill(6), {"through": True}, objs)
    return pr.to_dict()


# -- the roots --------------------------------------------------------------------------------------

def test_without_roots_the_home_folder_is_the_root(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    monkeypatch.delenv("IYICAM_ROOTS", raising=False)
    assert Files(ServiceHost()).roots() == [os.path.realpath(home)]


def test_roots_come_from_the_environment_and_a_named_root_wins(tmp_path, monkeypatch):
    a, b, c = (tmp_path / n for n in "abc")
    for d in (a, b, c):
        d.mkdir()
    monkeypatch.setenv("IYICAM_ROOTS", os.pathsep.join([str(a), str(b), str(tmp_path / "missing")]))
    assert Files(ServiceHost()).roots() == [os.path.realpath(a), os.path.realpath(b)]     # (a missing one is left out)
    assert Files(ServiceHost(), [str(c)]).roots() == [os.path.realpath(c)]


def test_status_names_the_roots(env):
    st = ok(env.files.handle("status"))
    assert st["files"] is True and st["roots"] == [env.root] and st["limits"]["open"] > 0


@pytest.mark.parametrize("target", ["parent", "other", "dotdot", "relative", "empty", "nul", "number"])
def test_paths_outside_the_roots_are_refused(env, target):
    path = {"parent": str(env.tmp), "other": str(env.outside / "other.svg"),
            "dotdot": os.path.join(env.root, "jobs", "..", "..", "elsewhere", "other.svg"),
            "relative": "readme.md", "empty": "  ", "nul": os.path.join(env.root, "a\0b"), "number": 5}[target]
    for op in ("list", "open"):
        out = env.files.handle(op, {"path": path})
        assert code(out) in ("path_not_allowed", "invalid_path"), (op, out)
    out = env.files.handle("save_files", {"folder": path, "files": [{"name": "x.nc", "text": "G0"}]})
    assert code(out) in ("path_not_allowed", "invalid_path")
    assert not (env.outside / "x.nc").exists()


def test_a_symbolic_link_cannot_lead_out(env):
    link = os.path.join(env.root, "link")
    try:
        os.symlink(str(env.outside), link, target_is_directory=True)
    except (OSError, NotImplementedError):
        if os.name != "nt":
            pytest.skip("no permission to make symbolic links here")
        import subprocess            # (Windows without the privilege: a junction leads out just the same)
        made = subprocess.run(["cmd", "/c", "mklink", "/J", link, str(env.outside)], capture_output=True)
        if made.returncode != 0:
            pytest.skip("neither a symbolic link nor a junction could be made here")
    assert code(env.files.handle("list", {"path": link})) == "path_not_allowed"
    assert code(env.files.handle("open", {"path": os.path.join(link, "other.svg")})) == "path_not_allowed"
    assert code(env.files.handle("save_files", {"folder": link, "files": [{"name": "x.nc", "text": "G0"}]})) \
        == "path_not_allowed"
    assert not (env.outside / "x.nc").exists()


def test_hidden_folders_are_never_reachable(env):
    for path in (p(env, ".secret"), p(env, ".secret", "a.svg")):
        assert code(env.files.handle("list" if os.path.isdir(path) else "open", {"path": path})) == "path_not_allowed"
    assert code(env.files.handle("save_files", {"folder": p(env, ".secret"),
                                                "files": [{"name": "x.nc", "text": "G0"}]})) == "path_not_allowed"
    # ... and the listing does not show them
    names = [e["name"] for e in ok(env.files.handle("list", {"path": env.root}))["entries"]]
    assert ".secret" not in names


def test_the_state_folder_of_iyicam_is_closed_even_inside_a_root(env):
    (env.state / "server.key").write_text("ABCDEF-GHJKMN-PQRSTU-VWXYZ2\n")
    (env.state / "sub").mkdir()
    wide = Files(env.host, [str(env.tmp)])                    # a root that contains the state folder
    assert code(wide.handle("list", {"path": str(env.state)})) == "path_not_allowed"
    assert code(wide.handle("list", {"path": str(env.state / "sub")})) == "path_not_allowed"
    assert code(wide.handle("save_files", {"folder": str(env.state),
                                           "files": [{"name": "x.txt", "text": "x"}]})) == "path_not_allowed"
    assert not (env.state / "x.txt").exists()
    assert ok(wide.handle("list", {"path": env.root}))["entries"]          # the rest of that root works


def test_a_root_put_inside_the_state_folder_on_purpose_works(env):
    mine = env.state / "files"                                # what the installer on a server does
    mine.mkdir()
    (mine / "a.svg").write_bytes(SVG)
    f = Files(env.host, [str(mine)])
    assert [e["name"] for e in ok(f.handle("list", {"path": str(mine)}))["entries"]] == ["a.svg"]
    assert code(f.handle("open", {"path": str(env.state / "server.key")})) == "path_not_allowed"
    assert code(Files(env.host, [str(env.state)]).handle("list", {"path": str(env.state)})) == "path_not_allowed"


# -- looking ----------------------------------------------------------------------------------------

def test_without_a_path_the_roots_are_listed(env):
    out = ok(env.files.handle("list", {}))
    assert out["roots"] is True and out["parent"] is None
    assert out["entries"] == [{"name": env.root, "path": env.root, "type": "dir"}]


def test_a_folder_lists_folders_first_and_only_known_files(env):
    out = ok(env.files.handle("list", {"path": env.root}))
    assert [(e["type"], e["name"]) for e in out["entries"]] == [("dir", "jobs"), ("file", "out.nc")]
    f = out["entries"][1]
    assert f["ext"] == "nc" and f["size"] == 6 and f["mtime"] > 0 and f["path"] == p(env, "out.nc")
    assert out["parent"] is None                              # (the parent of a root is outside)
    sub = ok(env.files.handle("list", {"path": p(env, "jobs")}))
    assert [e["name"] for e in sub["entries"]] == ["sub", "plate.svg"] and sub["parent"] == env.root


def test_a_long_folder_is_cut_and_said(env, monkeypatch):
    monkeypatch.setattr(files_mod, "LIST_LIMIT", 3)
    big = os.path.join(env.root, "big")
    os.mkdir(big)
    for i in range(6):
        open(os.path.join(big, f"f{i}.nc"), "w").close()
    out = ok(env.files.handle("list", {"path": big}))
    assert out["truncated"] is True and len(out["entries"]) == 3


def test_a_missing_folder_or_a_file_is_not_a_folder(env):
    assert code(env.files.handle("list", {"path": p(env, "nope")})) == "folder_not_found"
    assert code(env.files.handle("list", {"path": p(env, "out.nc")})) == "folder_not_found"


# -- opening ----------------------------------------------------------------------------------------

def test_a_drawing_opens_as_a_new_project(env):
    out = ok(env.files.handle("open", {"path": p(env, "jobs", "plate.svg"), "thickness": 12}))
    assert out["kind"] == "drawing" and out["name"] == "plate.svg" and out["path"] == p(env, "jobs", "plate.svg")
    assert out["hash"] == hashlib.sha256(SVG).hexdigest() and out["size"] == len(SVG)
    assert out["project"]["material"]["thickness"] == 12
    assert sum(len(s["vectors"]) for s in out["project"]["sheets"]) >= 1 and out["report"]["errors"] == []


def test_a_drawing_can_be_added_to_the_open_project(env):
    first = ok(env.files.handle("open", {"path": p(env, "jobs", "plate.svg")}))["project"]
    both = ok(env.files.handle("open", {"path": p(env, "jobs", "plate.svg"), "project": first}))["project"]
    count = lambda pr: sum(len(s["vectors"]) for s in pr["sheets"])          # noqa: E731
    assert count(both) == 2 * count(first)


def test_a_project_opens_as_it_is_with_its_hash(env, project):
    raw = json.dumps(project).encode()
    with open(p(env, "jobs", "cut.icam"), "wb") as f:
        f.write(raw)
    out = ok(env.files.handle("open", {"path": p(env, "jobs", "cut.icam")}))
    assert out["kind"] == "project" and out["hash"] == hashlib.sha256(raw).hexdigest()
    assert len(out["project"]["toolpaths"]) == 1


def test_open_refuses_what_is_not_a_drawing_or_is_too_big(env, monkeypatch):
    assert code(env.files.handle("open", {"path": p(env, "readme.md")})) == "unsupported_file"
    assert code(env.files.handle("open", {"path": p(env, "program.py")})) == "unsupported_file"
    assert code(env.files.handle("open", {"path": p(env, "jobs", "missing.svg")})) == "file_not_found"
    assert code(env.files.handle("open", {"path": p(env, "jobs")})) == "unsupported_file"
    monkeypatch.setattr(files_mod, "MAX_OPEN_BYTES", 10)
    assert code(env.files.handle("open", {"path": p(env, "jobs", "plate.svg")})) == "file_too_large"


def test_a_damaged_file_is_an_error_of_the_engine_not_a_crash(env):
    with open(p(env, "bad.icam"), "wb") as f:
        f.write(b"{ this is not json")
    assert code(env.files.handle("open", {"path": p(env, "bad.icam")})) == "invalid_project"
    with open(p(env, "empty.svg"), "wb"):
        pass
    assert code(env.files.handle("open", {"path": p(env, "empty.svg")})) == "empty_file"


# -- saving a project -------------------------------------------------------------------------------

def test_save_and_the_conflict_check(env, project):
    path = p(env, "jobs", "mine.icam")
    saved = ok(env.files.handle("save", {"path": path, "project": project}))
    assert saved["path"] == path and os.path.getsize(path) == saved["size"]
    assert hashlib.sha256(open(path, "rb").read()).hexdigest() == saved["hash"]
    # a second save without knowing the file, or with a stale hash, does not replace it
    assert code(env.files.handle("save", {"path": path, "project": project})) == "file_exists"
    assert code(env.files.handle("save", {"path": path, "project": project, "base_hash": "0" * 64})) == "file_changed"
    # with the hash of what was seen it does, and the new hash is given back
    again = ok(env.files.handle("save", {"path": path, "project": project, "base_hash": saved["hash"]}))
    assert again["path"] == path
    # a file changed on the computer in the meantime is not lost, unless the caller says so
    with open(path, "ab") as f:
        f.write(b" ")
    assert code(env.files.handle("save", {"path": path, "project": project, "base_hash": again["hash"]})) \
        == "file_changed"
    ok(env.files.handle("save", {"path": path, "project": project, "overwrite": True}))
    assert not [n for n in os.listdir(p(env, "jobs")) if n.endswith(".tmp")]          # no leftover of the atomic write
    # and what was written opens again
    assert ok(env.files.handle("open", {"path": path}))["kind"] == "project"


def test_save_checks_the_project_the_name_and_the_folder(env, project):
    assert code(env.files.handle("save", {"path": p(env, "jobs", "x.dxf"), "project": project})) == "invalid_path"
    assert code(env.files.handle("save", {"path": p(env, "jobs", "x.icam"), "project": {"nope": 1}})) == "invalid_project"
    assert code(env.files.handle("save", {"path": p(env, "jobs", "x.icam")})) in ("bad_request", "invalid_project")
    assert code(env.files.handle("save", {"path": p(env, "none", "x.icam"), "project": project})) == "folder_not_found"
    os.mkdir(p(env, "dir.icam"))
    assert code(env.files.handle("save", {"path": p(env, "dir.icam"), "project": project})) == "invalid_path"
    assert not os.path.exists(p(env, "jobs", "x.icam"))


# -- writing exported files -------------------------------------------------------------------------

def test_save_files_writes_and_does_not_replace(env):
    files = [{"name": "part.tap", "text": "G0 X1\r\nM30\r\n"},
             {"name": "part.dxf", "data_b64": base64.b64encode(b"0\nEOF\n").decode()}]
    out = ok(env.files.handle("save_files", {"folder": p(env, "jobs"), "files": files}))
    assert out["folder"] == p(env, "jobs") and out["written"] == [p(env, "jobs", "part.tap"), p(env, "jobs", "part.dxf")]
    assert open(p(env, "jobs", "part.tap"), "rb").read() == b"G0 X1\r\nM30\r\n"       # (the post's line ends kept)
    assert open(p(env, "jobs", "part.dxf"), "rb").read() == b"0\nEOF\n"
    # one name taken: nothing at all is written, and the answer names it
    again = env.files.handle("save_files", {"folder": p(env, "jobs"), "files": [
        {"name": "fresh.nc", "text": "G0"}, {"name": "part.tap", "text": "new"}]})
    assert code(again) == "file_exists" and again["params"]["names"] == ["part.tap"]
    assert not os.path.exists(p(env, "jobs", "fresh.nc"))
    assert open(p(env, "jobs", "part.tap"), "rb").read() == b"G0 X1\r\nM30\r\n"
    ok(env.files.handle("save_files", {"folder": p(env, "jobs"), "overwrite": True,
                                       "files": [{"name": "part.tap", "text": "new"}]}))
    assert open(p(env, "jobs", "part.tap"), "rb").read() == b"new"


@pytest.mark.parametrize("name", ["../escape.nc", "a/b.nc", "a\\b.nc", ".hidden.nc", "", "run.exe", "run.py", "go.sh",
                                  "noext", "x.nc\0", 5, None])
def test_save_files_refuses_dangerous_names_and_types(env, name):
    out = env.files.handle("save_files", {"folder": p(env, "jobs"), "files": [{"name": name, "text": "x"}]})
    assert code(out) in ("invalid_name", "unsupported_file")
    assert sorted(os.listdir(p(env, "jobs"))) == ["plate.svg", "sub"]
    assert not os.path.exists(p(env, "escape.nc"))


def test_save_files_takes_the_extension_of_a_post(env):
    class Engine:                    # (no built-in post has an extension of its own: a plugin's does)
        def handle(self, op, payload=None):
            return {"posts": [{"id": "mine", "extension": "zzz"}, {"id": "bad", "extension": "exe"}]}
    f = Files(Engine(), [env.root])
    ok(f.handle("save_files", {"folder": p(env, "jobs"), "files": [{"name": "j.zzz", "text": "G0"}]}))
    assert os.path.exists(p(env, "jobs", "j.zzz"))
    assert code(f.handle("save_files", {"folder": p(env, "jobs"), "files": [{"name": "j.exe", "text": "G0"}]})) \
        == "unsupported_file"                                    # (a post cannot make a program writable)
    assert code(env.files.handle("save_files", {"folder": p(env, "jobs"),
                                                "files": [{"name": "k.zzz", "text": "G0"}]})) == "unsupported_file"


def test_save_files_asks_for_something_to_write_and_limits_the_size(env, monkeypatch):
    for files in (None, [], "x", [5]):
        assert code(env.files.handle("save_files", {"folder": p(env, "jobs"), "files": files})) in (
            "bad_request", "invalid_name")
    assert code(env.files.handle("save_files", {"folder": p(env, "none"), "files": [{"name": "a.nc", "text": "x"}]})) \
        in ("folder_not_found", "path_not_allowed")
    monkeypatch.setattr(files_mod, "MAX_WRITE_BYTES", 8)
    assert code(env.files.handle("save_files", {"folder": p(env, "jobs"),
                                                "files": [{"name": "a.nc", "text": "x" * 20}]})) == "payload_too_large"
    assert not os.path.exists(p(env, "jobs", "a.nc"))


def test_handle_never_raises(env):
    for op, data in (("list", None), ("open", 5), ("save", []), ("save_files", "x"), ("nope", {}), ("status", None),
                     ("open", {"path": None}), ("save", {"path": 3, "project": 3})):
        out = env.files.handle(op, data)
        assert isinstance(out, dict)
    assert code(env.files.handle("nope", {})) == "unknown_operation"


# -- the door in the server -------------------------------------------------------------------------

KEY = "ABCDEF-GHJKMN-PQRSTU-VWXYZ2"


class Browser:
    def __init__(self, server):
        import http.client
        self.http, self.port, self.cookie = http.client, server.server_address[1], None

    def call(self, path, body=None):
        conn = self.http.HTTPConnection("127.0.0.1", self.port, timeout=60)
        headers = {"Content-Type": "application/json"}
        if self.cookie:
            headers["Cookie"] = self.cookie
        conn.request("POST", path, json.dumps(body or {}).encode(), headers)
        r = conn.getresponse()
        raw = r.read()
        sc = r.getheader("Set-Cookie")
        if sc and sc.startswith(srv.COOKIE + "="):
            self.cookie = sc.split(";", 1)[0]
        conn.close()
        return r.status, json.loads(raw)

    def api(self, op, body=None):
        return self.call("/api/" + op, body)


def serving(env, **kw):
    s = srv.make_server("127.0.0.1", 0, key=KEY, service=env.host, roots=[env.root], **kw)
    threading.Thread(target=s.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True).start()
    return s


@pytest.fixture
def browser(env):
    s = serving(env)
    b = Browser(s)
    assert b.call("/api/login", {"key": KEY})[0] == 200
    yield b
    s.shutdown()
    s.server_close()


def test_the_file_operations_need_the_key(env):
    s = serving(env)
    try:
        b = Browser(s)
        for op in ("status", "list", "open", "save", "save_files"):
            status, out = b.api(op, {"path": env.root})
            assert status == 401 and out["error"] == "not_logged_in", op
    finally:
        s.shutdown()
        s.server_close()


def test_the_page_browses_opens_saves_and_writes_through_the_server(browser, env, project):
    status, st = browser.api("status")
    assert status == 200 and st["files"] is True and st["roots"] == [env.root]
    status, ls = browser.api("list", {"path": env.root})
    assert status == 200 and [e["name"] for e in ls["entries"]] == ["jobs", "out.nc"]
    status, op = browser.api("open", {"path": p(env, "jobs", "plate.svg")})
    assert status == 200 and op["kind"] == "drawing" and op["project"]["sheets"]
    status, sv = browser.api("save", {"path": p(env, "jobs", "mine.icam"), "project": project})
    assert status == 200 and os.path.exists(p(env, "jobs", "mine.icam"))
    status, again = browser.api("save", {"path": p(env, "jobs", "mine.icam"), "project": project})
    assert status == 409 and again["error"] == "file_exists"
    status, wr = browser.api("save_files", {"folder": p(env, "jobs"), "files": [{"name": "a.nc", "text": "G0"}]})
    assert status == 200 and wr["written"] == [p(env, "jobs", "a.nc")]


def test_the_status_codes_tell_what_went_wrong(browser, env):
    assert browser.api("list", {"path": str(env.outside)})[0] == 403
    assert browser.api("list", {"path": p(env, "none")})[0] == 404
    assert browser.api("open", {"path": p(env, "jobs", "missing.svg")})[0] == 404
    assert browser.api("open", {"path": p(env, "readme.md")})[0] == 400
    assert browser.api("nothing")[0] == 404                         # (a made-up operation is still unknown)


def test_without_files_the_server_gives_no_access_to_the_disk(env):
    s = serving(env, files=False)
    try:
        b = Browser(s)
        assert b.call("/api/login", {"key": KEY})[0] == 200
        status, st = b.api("status")
        assert status == 200 and st["files"] is False and st["roots"] == []
        for op in ("list", "open", "save", "save_files"):
            status, out = b.api(op, {"path": env.root})
            assert status == 403 and out["error"] == "files_disabled", op
        assert b.api("ping")[0] == 200                              # the engine itself works as before
    finally:
        s.shutdown()
        s.server_close()

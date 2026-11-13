"""Staying signed in: a browser that is "kept signed in" has a session of 90 days that starts again every time the page is
opened, the sessions are in a file so that restarting the server signs nobody out, only hashes of the cookies are kept, and
a new key (or Sign out) still ends them.
"""
import http.client
import json
import os
import threading
import time
import uuid

import pytest

from iyicam.service import server as srv
from iyicam.service.hub import Hub
from iyicam.service.keys import KeyStore
from iyicam.service.sessions import REMEMBER_TTL, RENEW_AFTER, SESSION_TTL, SessionStore, token_id

KEY = "ABCDEF-GHJKMN-PQRSTU-VWXYZ2"
DAY = 86400.0


# ---------------------------------------------------------------------------------------------
# the store
# ---------------------------------------------------------------------------------------------

def test_a_session_is_there_until_it_is_over():
    s = SessionStore()
    end = s.add("tok", 100.0, "who", "proof", now=1000.0)
    assert end == 1100.0
    assert s.get("tok", now=1050.0)[:3] == (1100.0, "who", "proof")
    assert s.get("tok", now=1100.0) is None and s.get("nope", now=1050.0) is None
    assert s.get("", now=1.0) is None and s.get(None, now=1.0) is None


def test_the_file_holds_hashes_of_the_tokens_and_is_for_the_owner_only(tmp_path):
    path = tmp_path / "sessions.json"
    s = SessionStore(path)
    s.add("secret-cookie-value", 3600.0, "", "proof")
    text = path.read_text(encoding="utf-8")
    assert "secret-cookie-value" not in text and token_id("secret-cookie-value") in text
    if os.name == "posix":
        assert (path.stat().st_mode & 0o077) == 0
    assert not list(tmp_path.glob("*.tmp"))


def test_sessions_survive_a_restart_but_not_their_end(tmp_path):
    path = tmp_path / "sessions.json"
    a = SessionStore(path)
    a.add("kept", REMEMBER_TTL, "who", "proof")
    a.add("short", 5.0, "", "proof", now=time.time() - 60)                  # (over already)
    b = SessionStore(path)                                                    # (the server was restarted)
    assert b.get("kept")[1:3] == ("who", "proof") and b.get("short") is None and len(b) == 1
    b.remove("kept")
    assert SessionStore(path).get("kept") is None                              # (signing out is for good)


@pytest.mark.parametrize("content", ["", "{not json", "[]", '{"sessions": 5}', '{"sessions": {"x": [1]}}',
                                     '{"sessions": {"%s": ["a", "b"]}}' % ("0" * 64), "\x00\x01"])
def test_a_damaged_file_means_nobody_is_signed_in_nothing_more(tmp_path, content):
    path = tmp_path / "sessions.json"
    path.write_text(content, encoding="utf-8")
    s = SessionStore(path)
    assert len(s) == 0
    s.add("tok", 60.0)
    assert SessionStore(path).get("tok") is not None                          # (and it is written again as it should)


def test_a_folder_that_cannot_be_written_to_is_not_an_error(tmp_path):
    blocker = tmp_path / "file"
    blocker.write_text("x")
    s = SessionStore(blocker / "sessions.json")                              # (a "folder" that is a file)
    s.add("tok", 60.0)
    assert s.get("tok") is not None                                           # (it lives as long as the server)


def test_only_a_kept_session_is_renewed_and_not_more_than_once_a_day():
    s = SessionStore()
    s.add("kept", REMEMBER_TTL, now=0.0)
    s.add("short", SESSION_TTL, now=0.0)
    assert s.renew("kept", now=RENEW_AFTER / 2) is None                       # (a few hours old: nothing to do)
    assert s.renew("short", now=3600.0) is None                               # (a session of 12 hours stays that)
    assert s.renew("kept", now=30 * DAY) == REMEMBER_TTL
    assert s.get("kept", now=30 * DAY)[0] == 30 * DAY + REMEMBER_TTL
    assert s.renew("kept", now=30 * DAY + 3600) is None
    assert s.renew("nope", now=1.0) is None
    assert s.renew("kept", now=30 * DAY + REMEMBER_TTL + 1) is None           # (too late)


def test_the_oldest_session_goes_when_there_are_too_many():
    s = SessionStore(max_sessions=3)
    for i in range(4):
        s.add(f"t{i}", 1000.0 + i, now=0.0)
    assert s.get("t0", now=1.0) is None and all(s.get(f"t{i}", now=1.0) for i in (1, 2, 3))


# ---------------------------------------------------------------------------------------------
# the server
# ---------------------------------------------------------------------------------------------

class Browser:
    def __init__(self, port):
        self.port, self.cookie, self.max_age = port, None, None

    def request(self, method, path, body=None):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=30)
        h = {"Cookie": self.cookie} if self.cookie else {}
        data = json.dumps(body).encode() if body is not None else None
        if data:
            h["Content-Type"] = "application/json"
        conn.request(method, path, data, h)
        r = conn.getresponse()
        r.read()
        sc = r.getheader("Set-Cookie")
        if sc and sc.startswith(srv.COOKIE + "="):
            value = sc.split(";", 1)[0].split("=", 1)[1]
            self.cookie = f"{srv.COOKIE}={value}" if value else None
            self.max_age = int(sc.split("Max-Age=")[1].split(";")[0])
        conn.close()
        return r.status

    def login(self, remember=None, key=KEY):
        return self.request("POST", "/api/login", {"key": key} if remember is None else {"key": key, "remember": remember})

    def signed_in(self):
        return self.request("GET", "/api/session") == 200


class Running:
    """A server on a key file in ``folder`` (so its sessions are kept in a file next to it); ``restart()`` is what an update
    or a reboot of the computer does."""

    def __init__(self, folder):
        self.folder = folder
        self.server = None
        self.start()

    def start(self):
        self.server = srv.make_server("127.0.0.1", 0, key_path=self.folder / "server.key")
        threading.Thread(target=self.server.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True).start()
        self.port = self.server.server_address[1]

    def stop(self):
        self.server.shutdown()
        self.server.server_close()

    def restart(self):
        self.stop()
        self.start()


@pytest.fixture
def running(tmp_path):
    (tmp_path / "server.key").write_text(KEY + "\n", encoding="utf-8")
    r = Running(tmp_path)
    yield r
    r.stop()


def test_the_cookie_lasts_90_days_when_the_browser_is_kept_signed_in_and_12_hours_when_not(running):
    kept, plain, old_page = Browser(running.port), Browser(running.port), Browser(running.port)
    assert kept.login(remember=True) == 200 and plain.login(remember=False) == 200 and old_page.login() == 200
    assert kept.max_age == int(REMEMBER_TTL) or abs(kept.max_age - REMEMBER_TTL) < 5
    assert plain.max_age == old_page.max_age and abs(plain.max_age - SESSION_TTL) < 5      # (a page that does not ask: as before)
    assert Browser(running.port).login(remember="yes") == 200                              # (only true is a yes)


def test_a_restart_of_the_server_signs_nobody_out(running):
    kept, plain = Browser(running.port), Browser(running.port)
    kept.login(remember=True)
    plain.login(remember=False)
    assert kept.signed_in() and plain.signed_in()
    running.restart()
    kept.port = plain.port = running.port
    assert kept.signed_in() and plain.signed_in()                    # (the cookies are the same, the sessions were in the file)
    assert (running.folder / "sessions.json").exists()


def test_a_new_key_ends_every_session_also_across_a_restart(running):
    kept = Browser(running.port)
    kept.login(remember=True)
    KeyStore(running.folder / "server.key").rotate()                  # (iyicam serve --new-key)
    assert not kept.signed_in()                                        # (a running server notices at once)
    running.restart()
    kept.port = running.port
    assert not kept.signed_in()                                        # (and so does a new one reading the old file)
    assert kept.request("POST", "/api/ping", {}) == 401


def test_signing_out_is_for_good(running):
    kept = Browser(running.port)
    kept.login(remember=True)
    cookie = kept.cookie
    assert kept.request("POST", "/api/logout", {}) == 200
    running.restart()
    again = Browser(running.port)
    again.cookie = cookie                                              # (somebody kept the cookie: it is of no use)
    assert not again.signed_in()


def test_a_wrong_key_gets_no_session_and_writes_nothing(running):
    b = Browser(running.port)
    assert b.login(remember=True, key="WRONG-WRONG-WRONG-WRONG") == 401 and b.cookie is None
    assert not (running.folder / "sessions.json").exists()


def test_a_page_that_is_opened_keeps_the_session_going(running, monkeypatch):
    """A phone that is used stays signed in for good; one that is not used for three months asks for the key again."""
    now = [time.time()]
    monkeypatch.setattr(time, "time", lambda: now[0])
    kept, plain = Browser(running.port), Browser(running.port)
    kept.login(remember=True)
    plain.login(remember=False)
    now[0] += 60 * DAY
    assert kept.signed_in() and abs(kept.max_age - REMEMBER_TTL) < 5      # (opened: the cookie is told 90 days again)
    now[0] += 40 * DAY                                                       # 100 days after the sign in: the first 90 are over
    assert kept.signed_in()
    assert not plain.signed_in()                                             # (12 hours were over long ago)
    abandoned = Browser(running.port)
    abandoned.login(remember=True)
    now[0] += 91 * DAY
    assert not abandoned.signed_in()                                         # (never opened again in 90 days)


def test_the_cookie_is_http_only_strict_and_secure_behind_https(tmp_path):
    (tmp_path / "server.key").write_text(KEY + "\n", encoding="utf-8")
    s = srv.make_server("127.0.0.1", 0, key_path=tmp_path / "server.key", trust_proxy=True)
    threading.Thread(target=s.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True).start()
    try:
        conn = http.client.HTTPConnection("127.0.0.1", s.server_address[1], timeout=30)
        conn.request("POST", "/api/login", json.dumps({"key": KEY, "remember": True}),
                     {"Content-Type": "application/json", "X-Forwarded-Proto": "https"})
        r = conn.getresponse()
        r.read()
        cookie = r.getheader("Set-Cookie")
        assert "HttpOnly" in cookie and "SameSite=Strict" in cookie and "Secure" in cookie
        assert f"Max-Age={int(REMEMBER_TTL)}" in cookie or "Max-Age=77759" in cookie
    finally:
        s.shutdown()
        s.server_close()


def test_a_server_with_a_key_given_to_it_keeps_nothing_on_disk(tmp_path):
    """Tests and callers that hold the key themselves: nothing is written."""
    s = srv.make_server("127.0.0.1", 0, key=KEY)
    threading.Thread(target=s.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True).start()
    try:
        b = Browser(s.server_address[1])
        b.login(remember=True)
        assert b.signed_in() and s.auth._sessions.path is None
    finally:
        s.shutdown()
        s.server_close()


# ---------------------------------------------------------------------------------------------
# the hub
# ---------------------------------------------------------------------------------------------

def test_a_browser_stays_signed_in_on_a_hub_that_is_restarted(tmp_path):
    server_key = "SRVKEY-ABCDEF-GHJKMN-PQRSTU"
    (tmp_path / "server.key").write_text(server_key + "\n", encoding="utf-8")

    def start():
        hub = Hub(KeyStore(tmp_path / "server.key"), tmp_path / "agents.json")
        s = srv.make_server("127.0.0.1", 0, hub=hub)
        threading.Thread(target=s.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True).start()
        return s

    s = start()
    try:
        conn = http.client.HTTPConnection("127.0.0.1", s.server_address[1], timeout=30)
        conn.request("POST", "/agent/register", json.dumps({"agent_id": uuid.uuid4().hex, "token": KEY, "name": "Workshop"}),
                     {"Content-Type": "application/json", "X-Server-Key": server_key})
        r = conn.getresponse()
        r.read()
        assert r.status == 200
        b = Browser(s.server_address[1])
        assert b.login(remember=True) == 200 and abs(b.max_age - REMEMBER_TTL) < 5 and b.signed_in()
        assert (tmp_path / "hub-sessions.json").exists()
    finally:
        s.shutdown()
        s.server_close()
    s = start()                                                              # (the hub is restarted)
    try:
        b.port = s.server_address[1]
        assert b.signed_in()
        wrong = Browser(s.server_address[1])
        wrong.cookie = b.cookie.replace(b.cookie[-3:], "xyz")
        assert not wrong.signed_in()
    finally:
        s.shutdown()
        s.server_close()

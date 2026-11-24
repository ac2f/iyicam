"""``iyicam serve``: the web app, and the meeting point of browsers and the agent.  It does NO CAM work.

* ``/app/``      the static app (``iyicam/webapp``), with ``config.json`` (where Pyodide and the wheels are), the engine's
                 package ``/app/py/iyicam.zip`` (built from the installed package, see :mod:`iyicam.share.pack`) and, with
                 ``--pyodide DIR``, that folder as ``/app/pyodide/``.  None of it is secret, so none of it needs the key.
* ``/api/...``   sign in with the **key** (a cookie session, ``HttpOnly`` and ``SameSite=Strict``), then the files API:
                 the files of the roots of the agent (relayed, :mod:`iyicam.share.relay`) or, with ``--local``, of this
                 computer (:mod:`iyicam.share.rootfs`).
* ``/agent/...`` what ``iyicam agent`` calls (the same key, as a bearer token).

Every answer carries ``Cross-Origin-Opener-Policy: same-origin`` and ``Cross-Origin-Embedder-Policy: require-corp``.  They
make the page *cross-origin isolated*, which is what lets it share a SharedArrayBuffer with the engine's worker, so that a
calculation can be cancelled without restarting Python.  They also mean that everything the page loads from another
origin must be sent with CORS or ``Cross-Origin-Resource-Policy: cross-origin`` (jsDelivr, PyPI and Google Fonts are).

The server speaks plain HTTP: on a trusted network that is fine, on the internet put it behind a reverse proxy with HTTPS
(``--trust-proxy`` then takes the address and the scheme from the proxy).
"""
from __future__ import annotations

import gzip
import hashlib
import hmac
import json
import mimetypes
import os
import secrets
import socket
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, quote, urlsplit

from ..service.keys import KeyStore, Throttled, normalise_key
from .pack import Bundle
from .relay import Relay, RelayError, RemoteFS
from .rootfs import MAX_FILE_BYTES, FsError, RootFS, default_roots
from .sessions import REMEMBER_TTL, SESSION_TTL, SessionStore

WEBAPP_DIR = Path(__file__).resolve().parent.parent / "webapp"
COOKIE = "iyicam_session"
DEFAULT_PORT = 8300
PYODIDE_CDN = "https://cdn.jsdelivr.net/pyodide/v0.27.7/full/"
SMALL_BODY = 64 * 1024                                   # login and the agent's polls

# The page needs: its own files, Pyodide (from the CDN unless self-hosted), the wheels from PyPI, WebAssembly, a blob worker
# and Google Fonts for the designer's choice of type.  'unsafe-eval' is not allowed: Pyodide runs with 'wasm-unsafe-eval'.
CSP = ("default-src 'self'; script-src 'self' https://cdn.jsdelivr.net 'wasm-unsafe-eval'; "
       "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; font-src 'self' data: https://fonts.gstatic.com; "
       "img-src 'self' data: blob:; worker-src 'self' blob:; "
       "connect-src 'self' https://cdn.jsdelivr.net https://pypi.org https://files.pythonhosted.org; "
       "frame-ancestors 'none'; base-uri 'none'; form-action 'self'")
ISOLATION = {"Cross-Origin-Opener-Policy": "same-origin", "Cross-Origin-Embedder-Policy": "require-corp",
             "Cross-Origin-Resource-Policy": "same-origin"}

mimetypes.add_type("application/wasm", ".wasm")
mimetypes.add_type("text/javascript", ".mjs")
mimetypes.add_type("text/x-python", ".py")
GZIP_TYPES = ("text/", "application/json", "application/javascript", "application/wasm", "image/svg", "application/manifest")


class Auth:
    """Signing in with the key, and checking it for the agent.  Wrong keys are counted per address: after ``max_fail``
    within ``window`` seconds that address waits (HTTP 429)."""

    def __init__(self, keys: KeyStore, ttl: float = SESSION_TTL, remember_ttl: float = REMEMBER_TTL, max_fail: int = 10,
                 window: float = 300.0, sessions_path: Path | None = None, max_sessions: int = 64):
        self.keys, self.ttl, self.remember_ttl, self.max_fail, self.window = keys, ttl, remember_ttl, max_fail, window
        self._lock = threading.Lock()
        self._sessions = SessionStore(sessions_path, max_sessions)
        self._fails: dict[str, list[float]] = {}

    @staticmethod
    def _fingerprint(key: str) -> str:
        return hashlib.sha256(normalise_key(key).encode()).hexdigest()

    def _recent(self, ip: str, now: float) -> list[float]:
        fails = [t for t in self._fails.get(ip, []) if now - t < self.window]
        if fails:
            self._fails[ip] = fails
        else:
            self._fails.pop(ip, None)
        return fails

    def check(self, ip: str, given: object) -> bool:
        """Whether ``given`` is the key; counts a failure; raises :class:`Throttled` for an address that keeps trying."""
        now = time.time()
        with self._lock:
            fails = self._recent(ip, now)
            if len(fails) >= self.max_fail:
                raise Throttled(self.window - (now - fails[0]))
        ok = hmac.compare_digest(normalise_key(given).encode(), normalise_key(self.keys.get()).encode())
        with self._lock:
            if ok:
                self._fails.pop(ip, None)
            else:
                self._fails.setdefault(ip, []).append(now)
                if len(self._fails) > 2000:                      # (a flood of addresses must not grow the table)
                    for k in list(self._fails):
                        self._recent(k, now)
        return ok

    def login(self, ip: str, given: object, remember: bool = False) -> str | None:
        if not self.check(ip, given):
            return None
        token = secrets.token_urlsafe(32)
        self._sessions.add(token, self.remember_ttl if remember else self.ttl, "", self._fingerprint(self.keys.get()))
        return token

    def valid(self, token: str | None) -> bool:
        entry = self._sessions.get(token)
        return entry is not None and hmac.compare_digest(entry[2], self._fingerprint(self.keys.get()))   # (a new key ends old sessions)

    def lifetime(self, token: str | None) -> int:
        entry = self._sessions.get(token)
        return max(0, int(entry[0] - time.time())) if entry is not None else 0

    def renew(self, token: str | None) -> int | None:
        if not self.valid(token):
            return None
        left = self._sessions.renew(token)
        return int(left) if left else None

    def logout(self, token: str | None) -> None:
        self._sessions.remove(token)


class ShareServer(ThreadingHTTPServer):
    daemon_threads = True
    request_queue_size = 64
    allow_reuse_address = os.name != "nt"        # (on Windows this would let a second program take the same port)

    def __init__(self, address: tuple[str, int], auth: Auth, *, local: RootFS | None = None, pyodide_dir: Path | None = None,
                 trust_proxy: bool = False, max_file: int = MAX_FILE_BYTES, verbose: bool = False,
                 webapp_dir: Path = WEBAPP_DIR, relay: Relay | None = None):
        if ":" in address[0]:
            self.address_family = socket.AF_INET6
        self.auth, self.local = auth, local
        self.relay = relay or Relay()
        self.remote = RemoteFS(self.relay, max_file)
        self.pyodide_dir = Path(pyodide_dir).resolve() if pyodide_dir else None
        self.webapp_dir = Path(webapp_dir).resolve()
        self.trust_proxy, self.max_file, self.verbose = trust_proxy, max_file, verbose
        self.bundle = Bundle()
        self._gz: dict[tuple, bytes] = {}
        super().__init__(address, Handler)

    def log(self, text: str) -> None:
        print(time.strftime("%H:%M:%S"), text, file=sys.stderr, flush=True)

    def server_close(self) -> None:
        self.relay.close()                       # (the agent's poll that waits is let go)
        super().server_close()

    def handle_error(self, request, client_address) -> None:     # (a browser that went away is not news)
        exc = sys.exc_info()[1]
        if isinstance(exc, (ConnectionError, TimeoutError, socket.timeout)):
            return
        super().handle_error(request, client_address)

    def files(self):
        """What answers the files API now: this computer's roots (``--local``), the agent, or nothing."""
        if self.local is not None:
            return self.local
        return self.remote if self.relay.status() is not None else None

    def wheels(self) -> list[str]:
        if self.pyodide_dir is None:
            return []
        return sorted(f"/app/pyodide/{p.name}" for p in self.pyodide_dir.glob("*.whl")
                      if p.name.split("-")[0].lower() in ("ezdxf", "svgelements", "pyparsing", "typing_extensions", "fonttools"))


class Handler(BaseHTTPRequestHandler):
    server_version = "iyiCAM"
    sys_version = ""
    protocol_version = "HTTP/1.1"
    timeout = 180                                        # a client that stops sending is let go

    server: ShareServer

    def log_message(self, fmt, *args) -> None:
        if self.server.verbose:
            self.server.log(f"{self.client_address[0]} {fmt % args}")

    # -- small things ---------------------------------------------------------------------
    def _ip(self) -> str:
        if self.server.trust_proxy:
            fwd = self.headers.get("X-Forwarded-For", "")
            if fwd.strip():
                return fwd.split(",")[-1].strip()            # what the proxy itself saw
        return self.client_address[0]

    def _https(self) -> bool:
        return self.server.trust_proxy and self.headers.get("X-Forwarded-Proto", "").lower() == "https"

    def _token(self) -> str | None:
        for part in self.headers.get("Cookie", "").split(";"):
            name, _, value = part.strip().partition("=")
            if name == COOKIE:
                return value
        return None

    def _cookie(self, token: str, seconds: int) -> str:
        return (f"{COOKIE}={token}; Path=/; Max-Age={seconds}; HttpOnly; SameSite=Strict" + ("; Secure" if self._https() else ""))

    def _drain(self) -> None:
        """A request refused before its body was read: the little there is is read first, because closing a connection
        with unread data resets it and the client would lose the answer."""
        if getattr(self, "_body_taken", False):
            return
        self._body_taken = True
        try:
            n = int(self.headers.get("Content-Length", "0") or 0)
            if 0 < n <= 1 << 20:
                self.rfile.read(n)
        except (ValueError, OSError):
            pass

    def _send(self, status: int, body: bytes, ctype: str, headers: dict | None = None, close: bool = False,
              gz: bytes | None = None) -> None:
        if close:
            self._drain()
        headers = dict(headers or {})
        if len(body) > 1024 and "gzip" in self.headers.get("Accept-Encoding", "") and ctype.startswith(GZIP_TYPES):
            body = gz if gz is not None else gzip.compress(body, 5)
            headers["Content-Encoding"] = "gzip"
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Vary", "Accept-Encoding")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Content-Security-Policy", CSP)
        for k, v in ISOLATION.items():
            self.send_header(k, v)
        for k, v in headers.items():
            self.send_header(k, v)
        if close:
            self.close_connection = True
            self.send_header("Connection", "close")
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _json(self, status: int, obj: dict, headers: dict | None = None, close: bool = False) -> None:
        body = json.dumps(obj, separators=(",", ":")).encode("utf-8")
        self._send(status, body, "application/json", {"Cache-Control": "no-store", **(headers or {})}, close)

    def _fail(self, status: int, code: str, message: str, close: bool = False, **extra) -> None:
        self._json(status, {"error": code, "message": message, **extra}, close=close)

    def _fs_fail(self, exc: FsError) -> None:
        self._json(exc.status, exc.to_dict())

    def _body(self, limit: int) -> bytes | None:
        """The request's body; answers the error itself and gives ``None`` when it cannot."""
        try:
            length = int(self.headers.get("Content-Length", ""))
        except ValueError:
            self._fail(411, "bad_request", "Content-Length is required", close=True)
            return None
        if length < 0 or length > limit:
            if 0 < length <= 4 * limit:             # (read and thrown away, so the browser gets the answer and not a broken connection)
                left = length
                while left > 0:
                    chunk = self.rfile.read(min(left, 1 << 20))
                    if not chunk:
                        break
                    left -= len(chunk)
                self._body_taken = True
            self._fail(413, "file_too_large", f"the request is larger than {limit // 1024} KB", close=True, limit=limit)
            return None
        raw = self.rfile.read(length) if length else b""
        self._body_taken = True
        if len(raw) != length:
            self.close_connection = True
            return None
        return raw

    def _json_body(self) -> dict | None:
        raw = self._body(SMALL_BODY)
        if raw is None:
            return None
        try:
            data = json.loads(raw.decode("utf-8")) if raw else {}
        except (UnicodeDecodeError, ValueError):
            self._fail(400, "bad_request", "the body is not valid JSON")
            return None
        if not isinstance(data, dict):
            self._fail(400, "bad_request", "the request must be an object")
            return None
        return data

    def _same_origin(self) -> bool:
        """A request that a page of another site sent (the browser says where it came from) is refused."""
        origin = self.headers.get("Origin")
        if not origin:
            return True
        host = (self.headers.get("X-Forwarded-Host") if self.server.trust_proxy else None) or self.headers.get("Host", "")
        return urlsplit(origin).netloc.lower() == host.lower()

    def _bearer(self) -> bool:
        """The agent's key; answers the refusal itself."""
        h = self.headers.get("Authorization", "")
        given = h[7:] if h[:7].lower() == "bearer " else ""
        try:
            if given and self.server.auth.check(self._ip(), given):
                return True
        except Throttled as t:
            self._fail(429, "throttled", "too many wrong keys; wait", close=True, retry_after=int(t.wait) + 1)
            return False
        self._fail(401, "bad_key", "the key is not right", close=True)
        return False

    def _signed_in(self) -> bool:
        return self.server.auth.valid(self._token())

    # -- GET / HEAD -------------------------------------------------------------------------
    def do_GET(self) -> None:           # noqa: N802
        url = urlsplit(self.path)
        path = url.path
        if path in ("/", "/index.html", "/app"):
            self.send_response(302)
            self.send_header("Location", "/app/")
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        if path.startswith("/app/"):
            return self._app(path[len("/app/"):])
        if path == "/health":
            return self._send(200, b"ok", "text/plain; charset=utf-8", {"Cache-Control": "no-store"})
        if path == "/api/session":
            return self._session()
        if path.startswith("/api/files/"):
            return self._files(path[len("/api/files/"):], parse_qs(url.query, keep_blank_values=True))
        if path.startswith("/agent/body/"):
            return self._agent_body(path[len("/agent/body/"):])
        self._fail(404, "not_found", "there is nothing here")

    do_HEAD = do_GET            # noqa: N815

    def _session(self) -> None:
        token = self._token()
        ok = self.server.auth.valid(token)
        left = self.server.auth.renew(token) if ok else None      # (a kept session starts its 90 days again)
        self._json(200, {"signed_in": ok, "mode": "local" if self.server.local is not None else "agent"},
                   {"Set-Cookie": self._cookie(token, left)} if left else None)

    # -- the app --------------------------------------------------------------------------------
    def _app(self, rel: str) -> None:
        srv = self.server
        if rel == "":
            rel = "index.html"
        if rel == "config.json":
            cfg = {"pyodide": "/app/pyodide/" if srv.pyodide_dir else PYODIDE_CDN, "zip": "/app/py/iyicam.zip",
                   "bridge": "/app/bridge.py", "wheels": srv.wheels()}
            return self._json(200, cfg, {"Cache-Control": "no-cache"})
        if rel == "py/iyicam.zip":
            b = srv.bundle.refresh()
            return self._cached(b.data, b.etag, "application/zip")
        if rel.startswith("pyodide/") and srv.pyodide_dir is not None:
            return self._file(srv.pyodide_dir, rel[len("pyodide/"):])
        self._file(srv.webapp_dir, rel)

    def _file(self, base: Path, rel: str) -> None:
        """A file below ``base``.  The name from the URL is turned into a real path and must stay inside ``base`` and
        have no hidden part; a folder gives its ``index.html``."""
        parts = [p for p in rel.split("/") if p]
        if "\0" in rel or "\\" in rel or any(p.startswith(".") or p == "__pycache__" for p in parts):
            return self._fail(404, "not_found", "there is nothing here")
        full = (base.joinpath(*parts)).resolve()
        if full.is_dir():
            full = full / "index.html"
        try:
            full.relative_to(base)
            st = full.stat()
            if not full.is_file():
                raise OSError
        except (ValueError, OSError):
            return self._fail(404, "not_found", "there is nothing here")
        etag = f'"{st.st_mtime_ns:x}-{st.st_size:x}"'
        ctype = mimetypes.guess_type(full.name)[0] or "application/octet-stream"
        if ctype.startswith("text/") or ctype in ("application/json", "application/javascript"):
            ctype += "; charset=utf-8"
        if self.headers.get("If-None-Match") == etag:
            return self._send(304, b"", ctype, {"ETag": etag, "Cache-Control": "no-cache"})
        data = full.read_bytes()
        self._cached(data, etag, ctype, key=(str(full), etag))

    def _cached(self, data: bytes, etag: str, ctype: str, key: tuple | None = None) -> None:
        """A body that is revalidated every time (``no-cache``): an unchanged file is answered 304 and costs nothing."""
        if self.headers.get("If-None-Match") == etag:
            return self._send(304, b"", ctype, {"ETag": etag, "Cache-Control": "no-cache"})
        gz = None
        if key is not None and len(data) > 1024 and ctype.startswith(GZIP_TYPES) and "gzip" in self.headers.get("Accept-Encoding", ""):
            gz = self.server._gz.get(key)
            if gz is None:
                gz = self.server._gz[key] = gzip.compress(data, 6)
                if len(self.server._gz) > 64:                      # (a changed file leaves its old copy: the oldest go)
                    self.server._gz.pop(next(iter(self.server._gz)))
        self._send(200, data, ctype, {"ETag": etag, "Cache-Control": "no-cache"}, gz=gz)

    # -- the files API ------------------------------------------------------------------------------
    def _files(self, what: str, q: dict) -> None:
        srv = self.server
        signed = self._signed_in()
        if what == "status":
            fs = srv.files() if signed else None
            agent = None
            if fs is not None:
                if srv.local is not None:
                    agent = {"connected": True, "name": socket.gethostname(), "local": True}
                else:
                    st = srv.relay.status()
                    agent = {"connected": True, "name": st["name"], "local": False} if st else None
            return self._json(200, {"signed_in": signed, "agent": agent, "roots": fs.roots() if fs is not None else []},
                              {"Cache-Control": "no-store"})
        if not signed:
            return self._fail(401, "not_logged_in", "enter the key first")
        fs = srv.files()
        if fs is None:
            return self._fs_fail(FsError("no_files", "no agent is connected and this server does not share its own folders"))
        path = (q.get("path") or [""])[0]
        try:
            if what == "list":
                return self._json(200, fs.list(path))
            if what == "stat":
                return self._json(200, fs.stat(path))
            if what == "read":
                data = fs.read(path)
                name = path.rsplit("/", 1)[-1]
                return self._send(200, data, "application/octet-stream",
                                  {"Cache-Control": "no-store", "Content-Disposition": f"attachment; filename*=UTF-8''{quote(name)}"})
        except FsError as exc:
            return self._fs_fail(exc)
        self._fail(404, "not_found", "there is no such operation")

    # -- the agent ----------------------------------------------------------------------------------
    def _agent_body(self, job_id: str) -> None:
        if not self._bearer():
            return
        agent_id = self.headers.get("X-Agent-Id", "")
        try:
            data = self.server.relay.body(agent_id, job_id)
        except RelayError as exc:
            return self._fail(exc.status, exc.code, exc.message)
        self._send(200, data, "application/octet-stream", {"Cache-Control": "no-store"})

    # -- POST / PUT ---------------------------------------------------------------------------
    def do_POST(self) -> None:          # noqa: N802
        path = urlsplit(self.path).path
        if path.startswith("/agent/"):                       # (a program, not a page: no Origin to check, the key is the proof)
            return self._agent_post(path[len("/agent/"):])
        if not self._same_origin():
            return self._fail(403, "bad_origin", "the request comes from another site", close=True)
        if path == "/api/login":
            data = self._json_body()
            if data is None:
                return
            try:
                token = self.server.auth.login(self._ip(), data.get("key"), bool(data.get("remember")))
            except Throttled as t:
                return self._fail(429, "throttled", "too many wrong keys; wait a little", retry_after=int(t.wait) + 1)
            if token is None:
                return self._fail(401, "bad_key", "the key is not right")
            secs = int(self.server.auth.lifetime(token))
            return self._json(200, {"ok": True, "signed_in": True}, {"Set-Cookie": self._cookie(token, secs)})
        if path == "/api/logout":
            self._drain()
            self.server.auth.logout(self._token())
            return self._json(200, {"ok": True}, {"Set-Cookie": self._cookie("", 0)})
        self._fail(404, "not_found", "there is nothing here", close=True)

    def _agent_post(self, what: str) -> None:
        if not self._bearer():
            return
        relay = self.server.relay
        if what == "poll":
            data = self._json_body()
            if data is None:
                return
            try:
                job = relay.poll(data.get("id"), data.get("name"), data.get("roots"))
            except RelayError as exc:
                return self._fail(exc.status, exc.code, exc.message)
            if job is None:
                return self._json(200, {"job": None})
            return self._json(200, {"job": job})
        if what == "bye":
            self._drain()
            relay.bye(self.headers.get("X-Agent-Id", ""))
            return self._json(200, {"ok": True})
        if what.startswith("reply/"):
            job_id = what[len("reply/"):]
            raw = self._body(self.server.max_file + 64 * 1024)
            if raw is None:
                return
            try:
                status = int(self.headers.get("X-Status", "200"))
                ok = relay.reply(self.headers.get("X-Agent-Id", ""), job_id, status, raw)
            except RelayError as exc:
                return self._fail(exc.status, exc.code, exc.message)
            except ValueError:
                return self._fail(400, "bad_request", "X-Status must be a number")
            return self._json(200, {"ok": ok})
        self._fail(404, "not_found", "there is nothing here", close=True)

    def do_PUT(self) -> None:           # noqa: N802
        url = urlsplit(self.path)
        if url.path != "/api/files/write":
            return self._fail(404, "not_found", "there is nothing here", close=True)
        if not self._same_origin():
            return self._fail(403, "bad_origin", "the request comes from another site", close=True)
        if not self._signed_in():
            return self._fail(401, "not_logged_in", "enter the key first", close=True)
        fs = self.server.files()
        if fs is None:
            return self._fs_fail(FsError("no_files", "no agent is connected and this server does not share its own folders"))
        q = parse_qs(url.query, keep_blank_values=True)
        data = self._body(self.server.max_file)
        if data is None:
            return
        try:
            out = fs.write((q.get("path") or [""])[0], data, (q.get("overwrite") or ["0"])[0] in ("1", "true"))
        except FsError as exc:
            return self._fs_fail(exc)
        self._json(200, out)

    def do_DELETE(self) -> None:        # noqa: N802
        self._fail(405, "method_not_allowed", "there is no such operation", close=True)


def lan_addresses() -> list[str]:
    """The addresses of this computer on the networks it is on (what a phone on the same Wi-Fi types)."""
    found: list[str] = []
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            ip = info[4][0]
            if not ip.startswith(("127.", "169.254.")) and ip not in found:
                found.append(ip)
    except OSError:
        pass
    try:                                         # the address the system would use to reach the outside
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("192.0.2.1", 9))          # (nothing is sent: UDP connect only picks a route)
            ip = s.getsockname()[0]
            if ip not in found and not ip.startswith("127."):
                found.insert(0, ip)
    except OSError:
        pass
    return found


def make_server(host: str = "127.0.0.1", port: int = DEFAULT_PORT, *, keys: KeyStore | None = None, local: bool = False,
                roots: list[str] | None = None, pyodide_dir: str | None = None, trust_proxy: bool = False,
                max_file_mb: int = 64, verbose: bool = False, sessions_path: Path | None = None, **kw) -> ShareServer:
    keys = keys or KeyStore()
    from ..core import paths
    auth = Auth(keys, sessions_path=sessions_path if sessions_path is not None else paths.user_dir() / "sessions.json")
    limit = max_file_mb * 1024 * 1024
    fs = RootFS(roots or default_roots(), limit) if local else None
    return ShareServer((host, port), auth, local=fs, pyodide_dir=Path(pyodide_dir) if pyodide_dir else None,
                       trust_proxy=trust_proxy, max_file=limit, verbose=verbose, **kw)


def serve(host: str = "127.0.0.1", port: int = DEFAULT_PORT, *, local: bool = False, roots: list[str] | None = None,
          pyodide_dir: str | None = None, trust_proxy: bool = False, max_file_mb: int = 64, verbose: bool = True) -> int:
    if pyodide_dir and not (Path(pyodide_dir) / "pyodide.mjs").is_file():
        print(f"error: {pyodide_dir} has no pyodide.mjs (it must be a Pyodide 0.27.7 'full' folder)", file=sys.stderr)
        return 1
    try:
        srv = make_server(host, port, local=local, roots=roots, pyodide_dir=pyodide_dir, trust_proxy=trust_proxy,
                          max_file_mb=max_file_mb, verbose=verbose)
    except OSError as exc:
        print(f"error: cannot listen on {host}:{port}: {exc}", file=sys.stderr)
        return 1
    shown = srv.server_address[1]
    print(f"iyiCAM web app on port {shown}")
    loopback = host in ("127.0.0.1", "::1", "localhost") or host.startswith("127.")
    print(f"  this computer:        http://localhost:{shown}/app/")
    if not loopback:
        for ip in lan_addresses():
            print(f"  phone on the same Wi-Fi: http://{ip}:{shown}/app/")
        if not trust_proxy:
            print("  plain HTTP: use it on a network you trust, or behind an HTTPS proxy (docs/web.md)")
    else:
        print("  (a phone needs --host 0.0.0.0)")
    if local:
        shared = srv.local.roots()
        print("  files:    this computer: " + (", ".join(r["name"] for r in shared) if shared else "no folder (see --root)"))
    else:
        print("  files:    through an agent: iyicam agent --server http://THIS-ADDRESS:%d --key KEY --root FOLDER" % shown)
    print(f"  engine:   {'Pyodide from ' + pyodide_dir if pyodide_dir else 'Pyodide from the jsDelivr CDN'}")
    print(f"  key file: {srv.auth.keys.path}")
    if sys.stdout is not None and sys.stdout.isatty():       # (pythonw has no stdout at all)
        print(f"  key:      {srv.auth.keys.get()}")
    else:
        print("  key:      iyicam serve --show-key")
    print("  Ctrl+C stops it.", flush=True)
    try:
        srv.serve_forever(poll_interval=0.5)
    except KeyboardInterrupt:
        print("stopped")
    finally:
        srv.server_close()
    return 0

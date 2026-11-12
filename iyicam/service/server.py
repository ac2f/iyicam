"""iyiCAM for a phone: the service behind a small web server, with a page and a key of its own.

It needs nothing but this package (no Qt): ``iyicam serve`` listens on a port, serves the page in ``iyicam/web`` and
answers the operations of :class:`~iyicam.service.ServiceHost` as JSON under ``/api/<operation>``.  It is a program of
its own: CNC-Assistant is not needed.  The project travels as data, as everywhere in the service, and the page can also
open and save files of the computer the server runs on, but only inside the folders named at the start (``--root``,
see :mod:`iyicam.service.files`; ``--no-files`` turns that off).

Access is by a **key** (``~/.iyicam/server.key``): the page asks for it once, the server answers with a session
cookie (12 hours; ``HttpOnly``, ``SameSite=Strict``) and every ``/api`` call needs that cookie.  After 10 wrong keys
within 5 minutes an address waits (HTTP 429).  A new key (``iyicam serve --new-key``) ends every session at once.

With ``--hub`` the server has no engine and no disk of its own: it is the meeting point of agents (``iyicam agent`` on
the computers of the people, see :mod:`iyicam.service.hub`).  A browser signs in with the client key of an agent and
every operation is passed on to that agent, which answers it with the files of its own computer.

The server speaks plain HTTP.  On a home network that is fine; on the internet put it behind a reverse proxy that
adds HTTPS (``--trust-proxy`` then takes the client address and the scheme from the proxy), see ``docs/web.md``.
"""
from __future__ import annotations

import base64
import gzip
import hashlib
import hmac
import json
import os
import secrets
import socket
import sys
import threading
import time
import zlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from .api import API_OPS, ERROR_STATUS, call, status_of  # noqa: F401  (API_OPS, ERROR_STATUS: part of this module's face)
from .files import FILE_OPS, Files
from .host import ServiceHost
from .hub import Hub, HubAuth, HubError
from .keys import KEY_ALPHABET, KeyStore, Throttled, key_file, make_key, normalise_key  # noqa: F401  (re-exported)
from .ops import API_VERSION

WEB_DIR = Path(__file__).resolve().parent.parent / "web"
COOKIE = "iyicam_session"
SESSION_TTL = 12 * 3600.0
DEFAULT_PORT = 8300
DEFAULT_MAX_BODY = 64 * 1024 * 1024                    # a big drawing as base64 is still well below this

STATIC_TYPES = {".html": "text/html; charset=utf-8", ".js": "text/javascript; charset=utf-8",
                ".css": "text/css; charset=utf-8", ".svg": "image/svg+xml", ".json": "application/json",
                ".webmanifest": "application/manifest+json", ".png": "image/png"}

CSP = ("default-src 'self'; img-src 'self' data: blob:; style-src 'self' 'unsafe-inline'; connect-src 'self'; "
       "frame-ancestors 'none'; base-uri 'none'; form-action 'self'")


# ---------------------------------------------------------------------------------------------
# sessions and failed attempts
# ---------------------------------------------------------------------------------------------

class Auth:
    def __init__(self, keys: KeyStore, ttl: float = SESSION_TTL, max_fail: int = 10, window: float = 300.0,
                 max_sessions: int = 64):
        self.keys, self.ttl, self.max_fail, self.window, self.max_sessions = keys, ttl, max_fail, window, max_sessions
        self._lock = threading.Lock()
        self._sessions: dict[str, tuple[float, str]] = {}      # token -> (expires, fingerprint of the key)
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

    def login(self, ip: str, given: object) -> str | None:
        """A session token for the right key, ``None`` for a wrong one; raises :class:`Throttled`."""
        now = time.time()
        with self._lock:
            fails = self._recent(ip, now)
            if len(fails) >= self.max_fail:
                raise Throttled(self.window - (now - fails[0]))
        key = self.keys.get()
        ok = hmac.compare_digest(normalise_key(given).encode(), normalise_key(key).encode())
        with self._lock:
            if not ok:
                self._fails.setdefault(ip, []).append(now)
                if len(self._fails) > 2000:                    # (a flood of addresses must not grow the table)
                    for k in list(self._fails):
                        self._recent(k, now)
                return None
            self._fails.pop(ip, None)
            self._sessions = {t: v for t, v in self._sessions.items() if v[0] > now}
            while len(self._sessions) >= self.max_sessions:    # the oldest goes
                del self._sessions[min(self._sessions, key=lambda t: self._sessions[t][0])]
            token = secrets.token_urlsafe(32)
            self._sessions[token] = (now + self.ttl, self._fingerprint(key))
            return token

    def valid(self, token: str | None) -> bool:
        if not token:
            return False
        with self._lock:
            entry = self._sessions.get(token)
        if entry is None or entry[0] <= time.time():
            return False
        return hmac.compare_digest(entry[1], self._fingerprint(self.keys.get()))    # a new key ends old sessions

    def logout(self, token: str | None) -> None:
        with self._lock:
            self._sessions.pop(token or "", None)


# ---------------------------------------------------------------------------------------------
# the server
# ---------------------------------------------------------------------------------------------

def _is_loopback(host: str) -> bool:
    return host in ("127.0.0.1", "::1", "localhost") or host.startswith("127.")


class ServiceServer(ThreadingHTTPServer):
    daemon_threads = True
    request_queue_size = 64
    allow_reuse_address = os.name != "nt"       # (on Windows this would let a second program take the same port)

    def __init__(self, address: tuple[str, int], service: ServiceHost | None, auth: Auth | HubAuth, *,
                 trust_proxy: bool = False, max_body: int = DEFAULT_MAX_BODY, verbose: bool = False,
                 files: Files | None = None, hub: Hub | None = None):
        if ":" in address[0]:
            self.address_family = socket.AF_INET6
        self.service, self.auth, self.files = service, auth, files      # (files None: no access to the disk at all)
        self.hub = hub                                  # (a hub has no service and no files: the agents have)
        self.trust_proxy, self.max_body, self.verbose = trust_proxy, max_body, verbose
        # only these files are ever served: a name from the URL is looked up here, never turned into a path
        self.assets = {p.name: p for p in WEB_DIR.iterdir() if p.is_file() and p.suffix in STATIC_TYPES} \
            if WEB_DIR.is_dir() else {}
        self.asset_cache: dict[str, tuple] = {}         # name -> (etag, bytes, gzipped bytes)
        super().__init__(address, Handler)

    def log(self, text: str) -> None:
        print(time.strftime("%H:%M:%S"), text, file=sys.stderr, flush=True)

    def server_close(self) -> None:
        if self.hub is not None:                        # (the agents' polls that wait for a call are let go)
            self.hub.close()
        super().server_close()

    def handle_error(self, request, client_address) -> None:     # (a phone that went away is not news)
        exc = sys.exc_info()[1]
        if isinstance(exc, (ConnectionError, TimeoutError, socket.timeout)):
            return
        super().handle_error(request, client_address)


class Handler(BaseHTTPRequestHandler):
    server_version = "iyiCAM"
    sys_version = ""
    protocol_version = "HTTP/1.1"
    timeout = 120                                       # a client that stops sending is let go

    server: ServiceServer

    def log_message(self, fmt, *args) -> None:
        if self.server.verbose:
            self.server.log(f"{self.client_address[0]} {fmt % args}")

    # -- small things ---------------------------------------------------------------------
    def _ip(self) -> str:
        if self.server.trust_proxy:
            fwd = self.headers.get("X-Forwarded-For", "")
            if fwd.strip():
                return fwd.split(",")[-1].strip()           # what the proxy itself saw
        return self.client_address[0]

    def _https(self) -> bool:
        return self.server.trust_proxy and self.headers.get("X-Forwarded-Proto", "").lower() == "https"

    def _token(self) -> str | None:
        for part in self.headers.get("Cookie", "").split(";"):
            name, _, value = part.strip().partition("=")
            if name == COOKIE:
                return value
        return None

    def _drain(self) -> None:
        """A request that is refused before its body was read: the little there is is read first, because closing a
        connection with unread data resets it and the client would lose the answer (Windows does this at once)."""
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
        """``gz``: the body already gzipped (a page file that is kept so), used when the client takes gzip."""
        if close:
            self._drain()
        if len(body) > 1024 and "gzip" in self.headers.get("Accept-Encoding", "") and \
                (ctype.startswith(("text/", "application/json", "image/svg"))):
            body = gz if gz is not None else gzip.compress(body, 5 if len(body) < (1 << 20) else 3)   # (a big answer: quick first)
            headers = {**(headers or {}), "Content-Encoding": "gzip"}
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Vary", "Accept-Encoding")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Content-Security-Policy", CSP)
        for k, v in (headers or {}).items():
            self.send_header(k, v)
        if close:
            self.close_connection = True
            self.send_header("Connection", "close")
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _json(self, status: int, obj: dict, headers: dict | None = None, close: bool = False) -> None:
        try:
            body = json.dumps(obj, separators=(",", ":"), allow_nan=False).encode("utf-8")
        except (TypeError, ValueError) as exc:         # (the service promises plain JSON; if it ever fails, say so)
            status, body = 500, json.dumps({"error": "internal_error", "message": f"answer is not JSON: {exc}"}).encode()
        self._send(status, body, "application/json", {"Cache-Control": "no-store", **(headers or {})}, close)

    def _fail(self, status: int, code: str, message: str, close: bool = False, **extra) -> None:
        self._json(status, {"error": code, "message": message, **extra}, close=close)

    def _read_json(self) -> dict | None:
        """The body as an object; answers the error itself and gives ``None`` when it cannot."""
        if "application/json" not in self.headers.get("Content-Type", "").lower():
            self._fail(415, "bad_request", "the request must be JSON (Content-Type: application/json)", close=True)
            return None
        try:
            length = int(self.headers.get("Content-Length", ""))
        except ValueError:
            self._fail(411, "bad_request", "Content-Length is required", close=True)
            return None
        if length < 0 or length > self.server.max_body:
            self._fail(413, "too_large", f"the request is larger than {self.server.max_body // (1024 * 1024)} MB",
                       close=True, limit_bytes=self.server.max_body)
            return None
        raw = self.rfile.read(length) if length else b""
        self._body_taken = True
        if len(raw) != length:
            self.close_connection = True
            return None
        if raw and self.headers.get("Content-Encoding", "").lower() == "gzip":       # (the page and the agents send big bodies gzipped)
            try:
                d = zlib.decompressobj(16 + zlib.MAX_WBITS)
                raw = d.decompress(raw, self.server.max_body + 1)
            except zlib.error:
                self._fail(400, "bad_request", "the body is not valid gzip")
                return None
            if d.unconsumed_tail or len(raw) > self.server.max_body:        # (a "zip bomb" is not unpacked beyond the limit)
                self._fail(413, "too_large", f"the request is larger than {self.server.max_body // (1024 * 1024)} MB",
                           limit_bytes=self.server.max_body)
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

    # -- GET ------------------------------------------------------------------------------
    def do_GET(self) -> None:           # noqa: N802
        path = urlsplit(self.path).path
        if path in ("/", "/index.html"):
            return self._static("index.html")
        if path.startswith("/static/"):
            return self._static(path[len("/static/"):])
        if path == "/health":
            return self._send(200, b"ok", "text/plain; charset=utf-8", {"Cache-Control": "no-store"})
        if path == "/api/session":
            if self.server.auth.valid(self._token()):
                return self._json(200, {"ok": True, "api": API_VERSION})
            return self._fail(401, "not_logged_in", "enter the key first")
        self._fail(404, "not_found", "there is nothing here")

    do_HEAD = do_GET            # noqa: N815

    def _static(self, name: str) -> None:
        """A file of the page: kept in memory (and gzipped once) while it does not change; a browser that has it asks
        with its ETag and is answered 304 without a body, so the page opens from the phone's own copy."""
        p = self.server.assets.get(name)
        if p is None:
            return self._fail(404, "not_found", "there is nothing here")
        try:
            st = p.stat()
        except OSError:
            return self._fail(404, "not_found", "there is nothing here")
        etag = f'"{st.st_mtime_ns:x}-{st.st_size:x}"'
        if self.headers.get("If-None-Match") == etag:
            self.send_response(304)
            self.send_header("ETag", etag)
            self.send_header("Cache-Control", "no-cache")
            self.end_headers()
            return
        cached = self.server.asset_cache.get(name)
        if cached is None or cached[0] != etag:
            raw = p.read_bytes()
            cached = self.server.asset_cache[name] = (etag, raw, gzip.compress(raw, 9) if len(raw) > 1024 else None)
        self._send(200, cached[1], STATIC_TYPES[p.suffix], {"Cache-Control": "no-cache", "ETag": etag}, gz=cached[2])

    # -- POST -----------------------------------------------------------------------------
    def do_POST(self) -> None:          # noqa: N802
        self._body_taken = False
        path = urlsplit(self.path).path
        if path.startswith("/agent/"):
            return self._agent(path[len("/agent/"):])
        if not path.startswith("/api/"):
            return self._fail(404, "not_found", "there is nothing here", close=True)
        if not self._same_origin():
            return self._fail(403, "bad_origin", "the request came from another site", close=True)
        name = path[len("/api/"):]
        if name == "login":
            return self._login()
        if name == "logout":
            self.server.auth.logout(self._token())
            return self._json(200, {"ok": True}, {"Set-Cookie": self._cookie("", 0)})
        if not self.server.auth.valid(self._token()):
            return self._fail(401, "not_logged_in", "enter the key first", close=True)
        if name == "import_file":
            return self._import_file()
        if name not in API_OPS and name not in FILE_OPS:
            return self._fail(404, "unknown_operation", f"there is no operation '{name}'", close=True,
                              operation=name)
        payload = self._read_json()
        if payload is None:
            return
        if self.server.hub is not None:                 # a hub: the agent of this session answers
            agent_id = self.server.auth.agent_of(self._token())
            if agent_id is None:                        # (the key was replaced while the body was read)
                return self._fail(401, "not_logged_in", "enter the key first", close=True)
            status, out = self.server.hub.submit(agent_id, name, payload)
        else:
            status, out = call(self.server.service, self.server.files, name, payload)
        self._json(status, out)

    def _import_file(self) -> None:
        """``POST /api/import_file?name=...&thickness=...``: the bytes of a drawing as the body, answered like ``import``.
        A phone that opens a big file this way does not make a base64 copy of it (and a JSON copy of that, and a gzipped
        copy of that) in its memory: the file is sent as it is."""
        query = parse_qs(urlsplit(self.path).query)
        name = (query.get("name") or ["file"])[0][:255]
        payload: dict = {"name": name}
        try:
            if query.get("thickness"):
                payload["thickness"] = float(query["thickness"][0])
            length = int(self.headers.get("Content-Length", ""))
        except ValueError:
            return self._fail(400, "bad_request", "thickness must be a number and Content-Length is required", close=True)
        if length <= 0 or length > self.server.max_body:
            return self._fail(413 if length > 0 else 400, "too_large" if length > 0 else "empty_file",
                              f"the file is larger than {self.server.max_body // (1024 * 1024)} MB" if length > 0 else "the file is empty",
                              close=True, limit_bytes=self.server.max_body)
        raw = self.rfile.read(length)
        self._body_taken = True
        if len(raw) != length:
            self.close_connection = True
            return None
        if self.server.hub is not None:                 # a hub passes jobs as JSON: the bytes go as base64 from here
            agent_id = self.server.auth.agent_of(self._token())
            if agent_id is None:
                return self._fail(401, "not_logged_in", "enter the key first", close=True)
            payload["data_b64"] = base64.b64encode(raw).decode("ascii")
            status, out = self.server.hub.submit(agent_id, "import", payload)
        else:
            payload["data"] = raw
            status, out = call(self.server.service, self.server.files, "import", payload)
        self._json(status, out)

    # -- the agents of a hub ---------------------------------------------------------------------
    def _agent(self, action: str) -> None:
        """``/agent/register``, ``/agent/poll`` and ``/agent/reply``: what an agent does.  They need the server key
        (header ``X-Server-Key``); a browser never calls them."""
        hub = self.server.hub
        if hub is None or action not in ("register", "poll", "reply"):
            return self._fail(404, "not_found", "there is nothing here", close=True)
        ip = self._ip()
        try:
            hub.check_server_key(ip, self.headers.get("X-Server-Key"))
        except Throttled as exc:
            wait = max(1, int(exc.wait))
            self.server.log(f"{ip} too many wrong server keys, waits {wait} s")
            return self._json(429, {"error": "too_many_attempts", "message": "too many wrong keys; wait a moment",
                                    "retry_after": wait}, {"Retry-After": str(wait)}, close=True)
        except HubError as exc:
            self.server.log(f"{ip} wrong server key")
            return self._fail(exc.status, exc.code, exc.message, close=True)
        body = self._read_json()
        if body is None:
            return
        try:
            if action == "register":
                hub.register(body.get("agent_id"), body.get("token"), body.get("name"))
                self.server.log(f"{ip} agent {str(body.get('name') or '')[:30]!r} registered")
                return self._json(200, {"ok": True, "max_body": self.server.max_body, "api": API_VERSION})
            if action == "poll":
                job = hub.poll(body.get("agent_id"))
                return self._json(200, {"job": job} if job else {})
            taken = hub.reply(body.get("agent_id"), body.get("job_id"), body.get("status"), body.get("out"))
            return self._json(200, {"ok": taken})
        except HubError as exc:
            return self._fail(exc.status, exc.code, exc.message)

    def _cookie(self, token: str, max_age: int) -> str:
        return (f"{COOKIE}={token}; Path=/; HttpOnly; SameSite=Strict; Max-Age={max_age}"
                + ("; Secure" if self._https() else ""))

    def _login(self) -> None:
        body = self._read_json()
        if body is None:
            return
        ip = self._ip()
        try:
            token = self.server.auth.login(ip, body.get("key"))
        except Throttled as exc:
            wait = max(1, int(exc.wait))
            self.server.log(f"{ip} too many wrong keys, waits {wait} s")
            return self._json(429, {"error": "too_many_attempts", "message": "too many wrong keys; wait a moment",
                                    "retry_after": wait}, {"Retry-After": str(wait)})
        if token is None:
            self.server.log(f"{ip} wrong key")
            return self._fail(401, "invalid_key", "the key is not right")
        self.server.log(f"{ip} signed in")
        self._json(200, {"ok": True, "api": API_VERSION}, {"Set-Cookie": self._cookie(token, int(SESSION_TTL))})


# ---------------------------------------------------------------------------------------------
# starting it
# ---------------------------------------------------------------------------------------------

def make_server(host: str = "127.0.0.1", port: int = DEFAULT_PORT, *, key: str | None = None,
                key_path: Path | None = None, trust_proxy: bool = False, max_body: int = DEFAULT_MAX_BODY,
                service: ServiceHost | None = None, verbose: bool = False, roots: list[str] | None = None,
                files: bool = True, hub: bool | Hub = False, hub_state: Path | None = None) -> ServiceServer:
    """A server that is bound but not yet serving (``serve_forever()``); ``port=0`` takes a free port.  ``key``
    (instead of the key file) is for tests.  The page may open and save files inside ``roots`` (default: those of
    ``IYICAM_ROOTS``, else the home folder); ``files=False`` gives no access to the disk at all.

    ``hub=True`` makes a hub (:mod:`iyicam.service.hub`): no engine and no disk here, the agents have them; the key
    is then the server key that lets agents register.  A ready :class:`~iyicam.service.hub.Hub` can be given too
    (tests); ``hub_state`` is where the registered agents are kept (default ``~/.iyicam/agents.json``)."""
    if isinstance(hub, Hub):
        keys = hub.keys
    else:
        keys = KeyStore(key_path, fixed=key)
    keys.get()                                   # the key file exists from now on
    if hub:
        the_hub = hub if isinstance(hub, Hub) else Hub(keys, hub_state)
        return ServiceServer((host, port), None, HubAuth(the_hub), trust_proxy=trust_proxy, max_body=max_body,
                             verbose=verbose, files=None, hub=the_hub)
    service = service or ServiceHost()
    return ServiceServer((host, port), service, Auth(keys), trust_proxy=trust_proxy, max_body=max_body,
                         verbose=verbose, files=Files(service, roots) if files else None)


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


def serve(host: str = "127.0.0.1", port: int = DEFAULT_PORT, *, trust_proxy: bool = False,
          max_body: int = DEFAULT_MAX_BODY, verbose: bool = True, roots: list[str] | None = None,
          files: bool = True, hub: bool = False) -> int:
    try:
        srv = make_server(host, port, trust_proxy=trust_proxy, max_body=max_body, verbose=verbose, roots=roots,
                          files=files, hub=hub)
    except OSError as exc:
        print(f"error: cannot listen on {host}:{port}: {exc}", file=sys.stderr)
        return 1
    shown = srv.server_address[1]
    print(f"iyiCAM {'hub' if hub else 'web service'} on port {shown}")
    if _is_loopback(host):
        print(f"  this computer only:   http://localhost:{shown}")
        print("  (a phone needs --host 0.0.0.0)")
    else:
        print(f"  this computer:        http://localhost:{shown}")
        for ip in lan_addresses():
            print(f"  phone on the same Wi-Fi: http://{ip}:{shown}")
        if not trust_proxy:
            print("  plain HTTP: use it on a network you trust, or behind an HTTPS proxy (docs/web.md)")
    print(f"  key file: {srv.auth.keys.path}")
    if hub:
        print("  hub:      agents (iyicam agent) register with the server key; a browser signs in with the client key")
        print("            of an agent and works with the files of that agent's computer")
    elif srv.files is None:
        print("  files:    off (--no-files): the page cannot open or save files of this computer")
    else:
        shown_roots = srv.files.roots()
        print("  files:    " + ("; ".join(shown_roots) if shown_roots else "no folder to open files from (see --root)"))
    label = "server key" if hub else "key"
    if sys.stdout is not None and sys.stdout.isatty():       # (pythonw has no stdout at all)
        print(f"  {label + ':':<11}{srv.auth.keys.get()}")
    else:
        print(f"  {label + ':':<11}iyicam serve --show-key")
    print("  Ctrl+C stops it.", flush=True)
    try:
        srv.serve_forever(poll_interval=0.5)
    except KeyboardInterrupt:
        print("stopped")
    finally:
        srv.server_close()
    return 0

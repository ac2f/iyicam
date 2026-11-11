"""iyiCAM for a phone: the service behind a small web server, with a page and a key of its own.

It needs nothing but this package (no Qt): ``iyicam serve`` listens on a port, serves the page in ``iyicam/web`` and
answers the operations of :class:`~iyicam.service.ServiceHost` as JSON under ``/api/<operation>``.  It is a program of
its own: CNC-Assistant is not needed.  The project travels as data, as everywhere in the service, and the page can also
open and save files of the computer the server runs on, but only inside the folders named at the start (``--root``,
see :mod:`iyicam.service.files`; ``--no-files`` turns that off).

Access is by a **key** (``~/.iyicam/server.key``): the page asks for it once, the server answers with a session
cookie (12 hours; ``HttpOnly``, ``SameSite=Strict``) and every ``/api`` call needs that cookie.  After 10 wrong keys
within 5 minutes an address waits (HTTP 429).  A new key (``iyicam serve --new-key``) ends every session at once.

The server speaks plain HTTP.  On a home network that is fine; on the internet put it behind a reverse proxy that
adds HTTPS (``--trust-proxy`` then takes the client address and the scheme from the proxy), see ``docs/web.md``.
"""
from __future__ import annotations

import gzip
import hashlib
import hmac
import json
import os
import re
import secrets
import socket
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit

from ..core import paths
from .files import FILE_OPS, Files
from .host import ServiceHost
from .ops import API_VERSION

WEB_DIR = Path(__file__).resolve().parent.parent / "web"
COOKIE = "iyicam_session"
SESSION_TTL = 12 * 3600.0
KEY_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"      # no 0 O 1 I: a key is read from a screen and typed on a phone
DEFAULT_PORT = 8300
DEFAULT_MAX_BODY = 64 * 1024 * 1024                    # a big drawing as base64 is still well below this

# what /api/<operation> may call (the whole service interface, nothing else)
API_OPS = frozenset({"ping", "schema", "validate", "import", "export", "command", "compute", "gcode", "simulate",
                     "nest", "job.start", "job.status", "job.cancel", "job.result"})

# an error code that is not "the request was wrong" (400) answers with another HTTP status
ERROR_STATUS = {"unknown_operation": 404, "job_not_found": 404, "job_not_finished": 409, "too_busy": 503,
                "internal_error": 500,
                # files of the computer (iyicam.service.files)
                "path_not_allowed": 403, "files_disabled": 403, "folder_not_found": 404, "file_not_found": 404,
                "file_exists": 409, "file_changed": 409, "file_too_large": 413, "payload_too_large": 413,
                "os_error": 500}

STATIC_TYPES = {".html": "text/html; charset=utf-8", ".js": "text/javascript; charset=utf-8",
                ".css": "text/css; charset=utf-8", ".svg": "image/svg+xml", ".json": "application/json",
                ".webmanifest": "application/manifest+json", ".png": "image/png"}

CSP = ("default-src 'self'; img-src 'self' data: blob:; style-src 'self' 'unsafe-inline'; connect-src 'self'; "
       "frame-ancestors 'none'; base-uri 'none'; form-action 'self'")


# ---------------------------------------------------------------------------------------------
# the key
# ---------------------------------------------------------------------------------------------

def key_file() -> Path:
    return paths.user_dir() / "server.key"


def make_key() -> str:
    """``ABCDEF-GHJKLM-NPQRST-UVWXYZ``: 24 characters of 32, about 120 bits."""
    chars = "".join(secrets.choice(KEY_ALPHABET) for _ in range(24))
    return "-".join(chars[i:i + 6] for i in range(0, 24, 6))


def normalise_key(text: object) -> str:
    """Dashes, spaces and case do not matter when a key is typed."""
    return re.sub(r"[^A-Za-z0-9]", "", str(text or "")).upper()


class KeyStore:
    """The key in its file, created at the first use.  ``fixed`` (tests, a caller that keeps the key itself) is
    used instead of the file.  The file is read again when it changes, so ``--new-key`` takes effect in a running
    server."""

    def __init__(self, path: Path | None = None, fixed: str | None = None):
        self.path = Path(path) if path else key_file()
        self.fixed = fixed
        self._lock = threading.Lock()
        self._cache: tuple[tuple[int, int], str] | None = None

    def _write(self, key: str) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_name(self.path.name + ".tmp")
        fd = os.open(str(tmp), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)     # (Windows ignores the mode)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(key + "\n")
        os.replace(tmp, self.path)
        self._cache = None

    def get(self) -> str:
        """The key as shown (``ABCDEF-...``)."""
        if self.fixed is not None:
            return self.fixed
        with self._lock:
            try:
                st = self.path.stat()
            except FileNotFoundError:
                self._write(make_key())
                st = self.path.stat()
            sig = (st.st_mtime_ns, st.st_size)
            if self._cache is None or self._cache[0] != sig:
                key = self.path.read_text(encoding="utf-8").strip()
                if len(normalise_key(key)) < 16:         # empty or damaged: a new one, never an open door
                    self._write(make_key())
                    st = self.path.stat()
                    sig = (st.st_mtime_ns, st.st_size)
                    key = self.path.read_text(encoding="utf-8").strip()
                self._cache = (sig, key)
            return self._cache[1]

    def rotate(self) -> str:
        if self.fixed is not None:
            raise ValueError("this key is not kept in a file")
        with self._lock:
            key = make_key()
            self._write(key)
            return key


# ---------------------------------------------------------------------------------------------
# sessions and failed attempts
# ---------------------------------------------------------------------------------------------

class Throttled(Exception):
    def __init__(self, wait: float):
        super().__init__("too many failed attempts")
        self.wait = wait


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

    def __init__(self, address: tuple[str, int], service: ServiceHost, auth: Auth, *, trust_proxy: bool = False,
                 max_body: int = DEFAULT_MAX_BODY, verbose: bool = False, files: Files | None = None):
        if ":" in address[0]:
            self.address_family = socket.AF_INET6
        self.service, self.auth, self.files = service, auth, files      # (files None: no access to the disk at all)
        self.trust_proxy, self.max_body, self.verbose = trust_proxy, max_body, verbose
        # only these files are ever served: a name from the URL is looked up here, never turned into a path
        self.assets = {p.name: p for p in WEB_DIR.iterdir() if p.is_file() and p.suffix in STATIC_TYPES} \
            if WEB_DIR.is_dir() else {}
        super().__init__(address, Handler)

    def log(self, text: str) -> None:
        print(time.strftime("%H:%M:%S"), text, file=sys.stderr, flush=True)

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

    def _send(self, status: int, body: bytes, ctype: str, headers: dict | None = None, close: bool = False) -> None:
        if len(body) > 1024 and "gzip" in self.headers.get("Accept-Encoding", "") and \
                (ctype.startswith(("text/", "application/json", "image/svg"))):
            body = gzip.compress(body, 5)
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
        if len(raw) != length:
            self.close_connection = True
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
        p = self.server.assets.get(name)
        if p is None:
            return self._fail(404, "not_found", "there is nothing here")
        self._send(200, p.read_bytes(), STATIC_TYPES[p.suffix], {"Cache-Control": "no-cache"})

    # -- POST -----------------------------------------------------------------------------
    def do_POST(self) -> None:          # noqa: N802
        path = urlsplit(self.path).path
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
        if name not in API_OPS and name not in FILE_OPS:
            return self._fail(404, "unknown_operation", f"there is no operation '{name}'", close=True,
                              operation=name)
        payload = self._read_json()
        if payload is None:
            return
        if name in FILE_OPS:
            if self.server.files is not None:
                out = self.server.files.handle(name, payload)
            elif name == "status":                      # (the page asks first, and learns that there is no disk to browse)
                out = {"files": False, "roots": [], "limits": {}}
            else:
                out = {"error": "files_disabled", "message": "this server does not give access to files"}
        else:
            out = self.server.service.handle(name, payload)
        code = out.get("error")
        # an answer is an error when it carries an error CODE; job.status has an 'error' field of its own (null
        # while the job runs, the job's error object after it failed) and is an answer like any other
        status = ERROR_STATUS.get(code, 400) if isinstance(code, str) else 200
        self._json(status, out)

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
                files: bool = True) -> ServiceServer:
    """A server that is bound but not yet serving (``serve_forever()``); ``port=0`` takes a free port.  ``key``
    (instead of the key file) is for tests.  The page may open and save files inside ``roots`` (default: those of
    ``IYICAM_ROOTS``, else the home folder); ``files=False`` gives no access to the disk at all."""
    keys = KeyStore(key_path, fixed=key)
    keys.get()                                   # the key file exists from now on
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
          files: bool = True) -> int:
    try:
        srv = make_server(host, port, trust_proxy=trust_proxy, max_body=max_body, verbose=verbose, roots=roots,
                          files=files)
    except OSError as exc:
        print(f"error: cannot listen on {host}:{port}: {exc}", file=sys.stderr)
        return 1
    shown = srv.server_address[1]
    print(f"iyiCAM web service on port {shown}")
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
    if srv.files is None:
        print("  files:    off (--no-files): the page cannot open or save files of this computer")
    else:
        shown_roots = srv.files.roots()
        print("  files:    " + ("; ".join(shown_roots) if shown_roots else "no folder to open files from (see --root)"))
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

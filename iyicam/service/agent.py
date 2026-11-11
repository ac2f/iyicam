"""The agent: iyiCAM's engine and the files of THIS computer, for a hub somewhere else.

``iyicam agent`` runs on the computer of the person (the one in the workshop, with the drawings and the folders the
machine reads).  It connects OUTWARD to a hub (``iyicam serve --hub``, usually a small server on the internet): it
registers, asks "is there anything for me?" over and over (``/agent/poll``), does what a browser asked of the hub, with
the engine and the files of this computer (:func:`iyicam.service.api.call`, the same answers as ``iyicam serve`` gives)
and sends the answer back.  Nothing has to reach into this computer; a browser signs in on the hub with the **client
key** of this agent, which it makes itself and which is shown here, never on the server.

First run (the server key is the one of the hub, ``iyicam serve --show-key`` there):

    iyicam agent --server-url https://hub.example.com --server-key ABCDEF-...

The settings are kept in ``~/.iyicam/agent.json`` (``IYICAM_HOME``), so later runs need nothing:

    iyicam agent                    runs the agent (Ctrl+C stops it)
    iyicam agent --show-key         prints the client key and stops
    iyicam agent --new-key          makes a new client key (the old one stops working at once)

The agent has no window of its own when it runs in the background (``pythonw``), so it writes what it does to
``agent.log`` and its state to ``agent.state.json`` in the same folder (never a key).
"""
from __future__ import annotations

import getpass
import gzip
import http.client
import json
import os
import socket
import ssl
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
import zlib
from pathlib import Path

from ..core import paths
from .api import call
from .files import Files
from .host import ServiceHost
from .keys import is_key, make_key

USER_AGENT = "iyicam-agent"
POLL_TIMEOUT = 40.0              # a little more than the hub holds a poll (25 s)
MAX_JOBS = 4                     # calls done at the same time; one more is answered "too busy"
GZIP_FROM = 4096                 # a body bigger than this goes to the hub gzipped
LOG_LIMIT = 512 * 1024           # the log is cut to its end when it grows beyond this, at the start


class ServerError(Exception):
    """The hub refused the registration."""


# ---------------------------------------------------------------------------------------------
# files of the agent
# ---------------------------------------------------------------------------------------------

def settings_file() -> Path:
    return paths.user_dir() / "agent.json"


def state_file() -> Path:
    return paths.user_dir() / "agent.state.json"


def log_file() -> Path:
    return paths.user_dir() / "agent.log"


def load_settings(path: Path | None = None) -> dict:
    try:
        data = json.loads((path or settings_file()).read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _write_private(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    fd = os.open(str(tmp), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)        # (Windows ignores the mode)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(text)
    os.replace(tmp, path)


def save_settings(settings: dict, path: Path | None = None) -> None:
    _write_private(path or settings_file(), json.dumps(settings, indent=2))


def normalize_url(url: object) -> str:
    url = str(url or "").strip().rstrip("/")
    if url and "://" not in url:
        url = "https://" + url
    return url


def connection_hint(exc: BaseException, url: str = "") -> str | None:
    """A plain explanation of a certificate error (it is easily taken for a wrong key; it is not: it happens before
    any key is sent), or ``None``."""
    reason = getattr(exc, "reason", exc)
    text = str(exc)
    if not (isinstance(reason, ssl.SSLCertVerificationError) or "CERTIFICATE_VERIFY_FAILED" in text):
        return None
    host = urllib.parse.urlsplit(url).hostname or ""
    return (f"The HTTPS certificate of {host or 'the server'} was not accepted (this is not about the keys: they are "
            "only sent after a secure connection exists). Open the address followed by /health in a browser: it must "
            "open without a warning. Do not turn certificate checking off.")


# ---------------------------------------------------------------------------------------------
# the agent
# ---------------------------------------------------------------------------------------------

class Agent:
    def __init__(self, server_url: str, server_key: str, agent_id: str, token: str, *, name: str = "",
                 service: ServiceHost | None = None, files: Files | None = None, use_files: bool = True,
                 roots: list[str] | None = None, settings_path: Path | None = None, state_path: Path | None = None,
                 log_path: Path | None = None, poll_timeout: float = POLL_TIMEOUT, echo: bool = False):
        self.server_url = normalize_url(server_url)
        self.server_key, self.agent_id, self.token, self.name = server_key, agent_id, token, name
        self.settings_path = settings_path
        self.state_path, self.log_path = state_path, log_path
        self.poll_timeout = poll_timeout
        self.echo = echo                                      # also print to the console
        self.max_body = 64 * 1024 * 1024                       # (the hub tells at registration)
        self._service, self._files, self._use_files, self._roots = service, files, use_files, roots
        self._gzip_ok = True                                  # (until a hub shows that it does not take gzipped bodies)
        self._engine_lock = threading.Lock()
        self._state_lock = threading.Lock()
        self._lock = threading.Lock()
        self._active = 0
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self.state = ""
        self.last_ok = 0.0
        self._state_written = 0.0

    # -- log and state -------------------------------------------------------------------------
    def log(self, text: str) -> None:
        line = f"{time.strftime('%Y-%m-%d %H:%M:%S')} {text}"
        if self.log_path is not None:
            try:
                self.log_path.parent.mkdir(parents=True, exist_ok=True)
                with open(self.log_path, "a", encoding="utf-8") as f:
                    f.write(line + "\n")
            except OSError:
                pass
        if self.echo and sys.stdout is not None:
            print(line, flush=True)

    def _write_state(self) -> None:
        """(called with ``_state_lock`` held: the loop and ``stop()`` must not write the file at the same time)"""
        if self.state_path is None:
            return
        self._state_written = time.time()
        data = {"state": self.state, "message": self._message, "pid": os.getpid(), "server_url": self.server_url,
                "name": self.name, "updated": time.time(), "last_ok": self.last_ok}
        try:
            _write_private(self.state_path, json.dumps(data))
        except OSError:
            pass

    _message = ""

    def _set_state(self, state: str, message: str) -> None:
        """Said in the log (and written to the state file) only when it changes."""
        with self._state_lock:
            if state != self.state:
                self.state, self._message = state, message
                self.log(message)
                self._write_state()

    def _beat(self) -> None:
        with self._state_lock:
            self.last_ok = time.time()
            if self.last_ok - self._state_written > 15:       # (the state file tells that it is alive)
                self._write_state()

    # -- talking to the hub --------------------------------------------------------------------
    def _post(self, path: str, data: bytes, timeout: float) -> tuple[int, bytes]:
        """One call to the hub: a body over a few KB goes gzipped (a drawing is mostly numbers: a fifth of the size) and
        the answer may come gzipped too.  A hub of an older version does not take a gzipped body ("not valid JSON"): it
        is told the same plainly, and plain from then on."""
        if len(data) > GZIP_FROM and self._gzip_ok:
            code, raw = self._send(path, gzip.compress(data, 3), timeout, True)
            if code == 400 and b"not valid JSON" in raw:
                self._gzip_ok = False
                self.log("the hub does not take gzipped bodies (an older version): sending them plain")
                return self._send(path, data, timeout, False)
            return code, raw
        return self._send(path, data, timeout, False)

    def _send(self, path: str, data: bytes, timeout: float, gzipped: bool) -> tuple[int, bytes]:
        headers = {"Content-Type": "application/json", "X-Server-Key": self.server_key, "User-Agent": USER_AGENT,
                   "Accept-Encoding": "gzip"}
        if gzipped:
            headers["Content-Encoding"] = "gzip"
        req = urllib.request.Request(self.server_url + path, data=data, method="POST", headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                code, raw, enc = r.status, r.read(), r.headers.get("Content-Encoding", "")
        except urllib.error.HTTPError as exc:
            code, raw, enc = exc.code, exc.read(), exc.headers.get("Content-Encoding", "")
        if raw and enc.lower() == "gzip":
            try:
                raw = gzip.decompress(raw)
            except (OSError, EOFError, zlib.error):
                raw = b""
        return code, raw

    def request(self, path: str, body: dict, timeout: float) -> tuple[int, dict]:
        data = json.dumps(body, separators=(",", ":"), allow_nan=False).encode("utf-8")
        code, raw = self._post(path, data, timeout)
        try:
            obj = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, ValueError):
            obj = {"error": "bad_response", "message": raw[:200].decode("utf-8", "replace")}
        return code, obj if isinstance(obj, dict) else {"error": "bad_response", "message": "not an object"}

    def _current_token(self) -> str:
        """May have been changed by another process (``--new-key``)."""
        t = load_settings(self.settings_path).get("token") if self.settings_path else None
        return t if isinstance(t, str) and is_key(t) else self.token

    def register(self, token: str | None = None) -> None:
        code, g = self.request("/agent/register", {"agent_id": self.agent_id, "token": token or self._current_token(),
                                                   "name": self.name}, 30)
        if code != 200:
            raise ServerError(f"registration refused ({code}): {g.get('message') or g.get('error')}")
        if isinstance(g.get("max_body"), int) and g["max_body"] > 0:
            self.max_body = g["max_body"]

    # -- doing what was asked ------------------------------------------------------------------------
    def _engine(self) -> tuple[ServiceHost, Files | None]:
        with self._engine_lock:                              # (made at the first call, not at the start)
            if self._service is None:
                self._service = ServiceHost()
            if self._files is None and self._use_files:
                self._files = Files(self._service, self._roots)
            return self._service, self._files

    def _answer(self, job: dict) -> tuple[int, dict]:
        op, payload = job.get("op"), job.get("payload")
        with self._lock:
            busy = self._active >= MAX_JOBS
            if not busy:
                self._active += 1
        if busy:
            return 503, {"error": "too_busy", "message": "the agent is doing too many things at once"}
        try:
            service, files = self._engine()
            return call(service, files, op if isinstance(op, str) else "", payload if isinstance(payload, dict) else {})
        except Exception as exc:                            # noqa: BLE001
            return 500, {"error": "internal_error", "message": f"{type(exc).__name__}: {exc}"}
        finally:
            with self._lock:
                self._active -= 1

    def _reply_body(self, job_id: object, status: int, out: dict) -> bytes:
        def encode(st: int, o: dict) -> bytes:
            return json.dumps({"agent_id": self.agent_id, "job_id": job_id, "status": st, "out": o},
                              separators=(",", ":"), allow_nan=False).encode("utf-8")
        try:
            data = encode(status, out)
        except (TypeError, ValueError) as exc:
            return encode(500, {"error": "internal_error", "message": f"answer is not JSON: {exc}"})
        if len(data) > self.max_body - 4096:               # (the hub would refuse it: say so instead)
            return encode(502, {"error": "reply_too_large",
                                "message": f"the answer is larger than the hub takes ({self.max_body // (1024 * 1024)} MB)"})
        return data

    def _run_job(self, job: dict) -> None:
        status, out = self._answer(job)
        data = self._reply_body(job.get("id"), status, out)
        for attempt in range(3):
            try:
                code, _ = self._post("/agent/reply", data, 120)
                if code not in (200, 409):                  # (409: the hub forgot us; the call is gone with it)
                    self.log(f"the hub refused an answer ({code})")
                return
            except (OSError, ValueError, http.client.HTTPException):
                if self._stop.wait(2 * (attempt + 1)):
                    return

    # -- the main loop ----------------------------------------------------------------------------
    def run_loop(self) -> None:
        wait = 2.0
        registered = False
        self._set_state("connecting", f"connecting to {self.server_url} ...")
        while not self._stop.is_set():
            try:
                if not registered:
                    self.register()
                    registered = True
                    self._beat()
                    self._set_state("connected", "connected to the hub; waiting for calls")
                code, g = self.request("/agent/poll", {"agent_id": self.agent_id}, self.poll_timeout)
                if code == 200:
                    self._beat()
                    self._set_state("connected", "connected to the hub; waiting for calls")
                    wait = 2.0
                    job = g.get("job")
                    if isinstance(job, dict):
                        threading.Thread(target=self._run_job, args=(job,), daemon=True).start()
                elif code == 409:                           # the hub forgot us (it was restarted without its list)
                    registered = False
                elif code == 429:
                    self._set_state("throttled", "the hub asks to wait (too many failed tries)")
                    self._stop.wait(max(5.0, float(g.get("retry_after") or 60)))
                elif code == 403:
                    self._set_state("rejected", "the server key was refused by the hub: get the current one with "
                                                "'iyicam serve --show-key' there and run 'iyicam agent --server-key KEY'")
                    self._stop.wait(60)
                else:
                    self._set_state(f"http{code}", f"the hub answered {code}: {g.get('message') or g.get('error')}")
                    self._stop.wait(wait)
                    wait = min(wait * 2, 30.0)
            except ServerError as exc:
                registered = False
                self._set_state("rejected", f"{exc}")
                self._stop.wait(60)
            except (OSError, ValueError, http.client.HTTPException) as exc:     # the network is gone, or the address is wrong
                hint = connection_hint(exc, self.server_url)
                self._set_state("offline", f"no connection to the hub ({exc}); trying again"
                                           + (f"\n  -> {hint}" if hint else ""))
                self._stop.wait(wait)
                wait = min(wait * 2, 30.0)
        self._set_state("stopped", "stopped")

    def start(self) -> threading.Thread:
        self._thread = threading.Thread(target=self.run_loop, daemon=True, name="iyicam-agent")
        self._thread.start()
        return self._thread

    def stop(self) -> None:
        self._stop.set()
        with self._state_lock:
            self.state = ""
        self._set_state("stopped", "stopped")              # (a poll that waits is not waited for)
        if self._thread is not None:
            self._thread.join(timeout=2)


# ---------------------------------------------------------------------------------------------
# the command line (iyicam agent)
# ---------------------------------------------------------------------------------------------

def run(server_url: str | None = None, server_key: str | None = None, name: str | None = None,
        roots: list[str] | None = None, no_files: bool = False, show_key: bool = False, new_key: bool = False,
        setup: bool = False) -> int:
    settings = load_settings()
    if show_key:
        token = settings.get("token")
        if not (isinstance(token, str) and is_key(token)):
            print("error: this computer has no agent yet (run 'iyicam agent --server-url ... --server-key ...')",
                  file=sys.stderr)
            return 1
        print(token)
        return 0
    if server_url:
        settings["server_url"] = normalize_url(server_url)
    server_key = server_key or os.environ.get("IYICAM_SERVER_KEY")
    if server_key:
        settings["server_key"] = server_key.strip()
    if name:
        settings["name"] = name
    if not settings.get("server_url"):
        print("error: the address of the hub is needed:  iyicam agent --server-url https://...", file=sys.stderr)
        return 1
    if not settings.get("server_key"):
        if not sys.stdin or not sys.stdin.isatty():
            print("error: the server key of the hub is needed (--server-key, or IYICAM_SERVER_KEY)", file=sys.stderr)
            return 1
        settings["server_key"] = getpass.getpass("Server key of the hub ('iyicam serve --show-key' there): ").strip()
    settings.setdefault("agent_id", uuid.uuid4().hex)
    settings.setdefault("name", os.environ.get("COMPUTERNAME") or os.environ.get("HOSTNAME") or socket.gethostname())
    if not is_key(settings.get("token")):
        settings["token"] = make_key()
    if roots:
        settings["roots"], settings["files"] = [str(Path(r).expanduser()) for r in roots], True
    if no_files:
        settings["files"] = False
    use_files = settings.get("files", True) is not False

    tty = sys.stdout is not None and sys.stdout.isatty()
    agent = Agent(settings["server_url"], settings["server_key"], settings["agent_id"], settings["token"],
                  name=settings["name"], use_files=use_files, roots=settings.get("roots") or None,
                  settings_path=settings_file(), state_path=state_file(), log_path=log_file(), echo=tty)
    if setup or new_key:                 # (the settings are kept only when the hub took them: nothing half done)
        fresh = make_key() if new_key else settings["token"]
        try:
            agent.register(fresh)
        except (ServerError, OSError, ValueError, http.client.HTTPException) as exc:
            print(f"error: the hub did not take it, nothing was changed: {exc}", file=sys.stderr)
            return 1
        settings["token"] = fresh
        save_settings(settings)
        print(fresh)
        if new_key:
            print("(the old client key does not work any more; a running agent needs no restart)", file=sys.stderr)
        return 0
    save_settings(settings)

    try:                                                     # (the log is cut to its end so it never grows without end)
        lf = log_file()
        if lf.stat().st_size > LOG_LIMIT:
            tail = lf.read_bytes()[-LOG_LIMIT // 2:]
            lf.write_bytes(tail[tail.find(b"\n") + 1:])
    except OSError:
        pass
    agent.log(f"agent {settings['name']!r} starts; hub {settings['server_url']}; files: "
              + ("off" if not use_files else ("; ".join(settings["roots"]) if settings.get("roots") else "the folders of "
                                              "IYICAM_ROOTS, else the home folder")))
    if tty:
        print("=" * 64)
        print("iyiCAM AGENT (the engine and the files of THIS computer, for the hub)")
        print(f"  Hub        : {settings['server_url']}")
        print(f"  Client key : {settings['token']}")
        print("  Open the hub in a browser and sign in with this key.")
        print("  New key    : iyicam agent --new-key      Show it again: iyicam agent --show-key")
        print("  Ctrl+C stops it")
        print("=" * 64, flush=True)
    agent.start()
    try:
        while agent._thread is not None and agent._thread.is_alive():
            agent._thread.join(0.5)
    except KeyboardInterrupt:
        print("\nstopping ...", flush=True)
        agent.stop()
    return 0

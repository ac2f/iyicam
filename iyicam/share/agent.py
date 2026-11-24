"""``iyicam agent``: shares folders of this computer with the web app.  It does NO CAM work: the CAM runs in the browser.

The agent runs on the workshop PC (where the drawings and the folders the machine reads are).  It connects OUT to the
server (``iyicam serve``), so it works behind a router and a firewall: it asks "anything for me?" (a long poll), does what
a browser asked - list, stat, read or write a file - inside the folders it was given (:mod:`iyicam.share.rootfs` checks every
path: no ``..``, no link that leads out, no hidden names, only known kinds of file, a size limit), and sends the answer back.
The key is the server's (``iyicam serve --show-key``); it is sent as a bearer token, so the server must be reached over
HTTPS (or a network you trust) when it is not on this computer.

    iyicam agent --server https://iyicam.example.com --key ABCDEF-... --root ~/jobs --root /srv/drawings

Several browsers can use one agent; a second agent is refused while the first is connected.
"""
from __future__ import annotations

import json
import os
import secrets
import signal
import socket
import sys
import threading
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from ..core import paths
from .rootfs import MAX_FILE_BYTES, FsError, RootFS, default_roots

POLL_TIMEOUT = 45.0              # a little more than the server holds a poll (25 s)
WORKERS = 4                      # calls done at the same time
BACKOFF_MAX = 30.0


def agent_id() -> str:
    """The same id at every start (the server tells "the same agent again" from "another agent"), kept in the user folder."""
    f = paths.user_dir() / "agent.id"
    try:
        v = f.read_text(encoding="utf-8").strip()
        if len(v) >= 8:
            return v
    except OSError:
        pass
    v = secrets.token_hex(12)
    try:
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(v + "\n", encoding="utf-8")
    except OSError:
        pass                                      # (an id that is new at each start is no harm)
    return v


class ServerRefused(Exception):
    def __init__(self, status: int, code: str, message: str):
        super().__init__(message)
        self.status, self.code = status, code


class Agent:
    def __init__(self, server: str, key: str, roots: list[str], name: str = "", max_bytes: int = MAX_FILE_BYTES,
                 quiet: bool = False, hold_timeout: float = POLL_TIMEOUT):
        self.base = server.rstrip("/")
        self.key, self.name = key, name or socket.gethostname()
        self.fs = RootFS(roots, max_bytes)
        self.id = agent_id()
        self.quiet = quiet
        self.timeout = hold_timeout
        self.stop = threading.Event()
        self.connected = False

    def log(self, text: str, always: bool = False) -> None:
        if always or not self.quiet:
            print(time.strftime("%H:%M:%S"), text, file=sys.stderr, flush=True)

    # -- the wire ----------------------------------------------------------------------------------
    def _request(self, method: str, path: str, body: bytes | None = None, headers: dict | None = None, timeout: float = 60.0) -> bytes:
        h = {"Authorization": "Bearer " + self.key, "X-Agent-Id": self.id, "User-Agent": "iyicam-agent"}
        h.update(headers or {})
        req = urllib.request.Request(self.base + path, data=body, method=method, headers=h)
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return r.read()
        except urllib.error.HTTPError as exc:
            raw = exc.read()
            try:
                d = json.loads(raw.decode("utf-8"))
                raise ServerRefused(exc.code, str(d.get("error", "")), str(d.get("message", ""))) from None
            except (ValueError, UnicodeDecodeError):
                raise ServerRefused(exc.code, "", f"the server answered {exc.code}") from None

    def _poll(self) -> dict | None:
        body = json.dumps({"id": self.id, "name": self.name, "roots": [r["name"] for r in self.fs.roots()]}).encode()
        out = self._request("POST", "/agent/poll", body, {"Content-Type": "application/json"}, self.timeout)
        return json.loads(out.decode("utf-8")).get("job")

    def _reply(self, job_id: str, status: int, data: bytes) -> None:
        self._request("POST", f"/agent/reply/{job_id}", data, {"X-Status": str(status), "Content-Type": "application/octet-stream"})

    # -- the work ---------------------------------------------------------------------------------
    def do(self, job: dict) -> tuple[int, bytes]:
        """What one call answers: ``(status, body)``; never raises."""
        op, p = job.get("op"), job.get("params") or {}
        path = p.get("path", "")
        try:
            if not isinstance(path, str):
                raise FsError("bad_path", "the path is not valid")
            if op == "list":
                return 200, json.dumps(self.fs.list(path)).encode()
            if op == "stat":
                return 200, json.dumps(self.fs.stat(path)).encode()
            if op == "read":
                return 200, self.fs.read(path)
            if op == "write":
                size = int(job.get("body_size") or 0)
                if size > self.fs.max_bytes:
                    raise FsError("file_too_large", "the file is too big", path=path, limit=self.fs.max_bytes)
                data = self._request("GET", f"/agent/body/{job['id']}") if size else b""
                return 200, json.dumps(self.fs.write(path, data, bool(p.get("overwrite")))).encode()
            raise FsError("bad_request", f"there is no operation '{op}'")
        except FsError as exc:
            return exc.status, json.dumps(exc.to_dict()).encode()
        except OSError as exc:
            return 500, json.dumps({"error": "os_error", "message": str(exc)}).encode()
        except Exception as exc:                  # noqa: BLE001  (a bug: said as it is, the agent stays up)
            return 500, json.dumps({"error": "os_error", "message": f"{type(exc).__name__}: {exc}"}).encode()

    def _handle(self, job: dict) -> None:
        status, data = self.do(job)
        try:
            self._reply(job["id"], status, data)
        except (OSError, ServerRefused) as exc:
            self.log(f"could not answer a call: {exc}", always=True)

    def run(self) -> int:
        roots = self.fs.roots()
        self.log(f"agent {self.name!r} -> {self.base}; sharing: " + (", ".join(r["name"] for r in roots) or "nothing (no such folder)"), always=True)
        pool = ThreadPoolExecutor(WORKERS, thread_name_prefix="agent")
        wait = 1.0
        try:
            while not self.stop.is_set():
                try:
                    job = self._poll()
                    if not self.connected:
                        self.connected = True
                        self.log("connected")
                    wait = 1.0
                    if job:
                        pool.submit(self._handle, job)
                except ServerRefused as exc:
                    self.connected = False
                    if exc.status in (401, 403):
                        self.log(f"the server refused the key: {exc}", always=True)
                        return 2
                    self.log(f"the server refused: {exc}", always=True)
                    self.stop.wait(wait := min(wait * 2, BACKOFF_MAX))
                except (OSError, ValueError, urllib.error.URLError) as exc:
                    if self.connected:
                        self.log(f"lost the server ({exc}); trying again")
                    else:
                        self.log(f"cannot reach the server ({exc}); trying again", always=wait >= BACKOFF_MAX)
                    self.connected = False
                    self.stop.wait(wait := min(wait * 2, BACKOFF_MAX))
        except KeyboardInterrupt:
            self.log("stopped", always=True)
        finally:
            self.stop.set()
            try:
                self._request("POST", "/agent/bye", b"", timeout=3.0)      # (the server need not wait to find out)
            except (OSError, ServerRefused):
                pass
            pool.shutdown(wait=False, cancel_futures=True)
        return 0


def run(server: str, key: str, roots: list[str] | None = None, name: str | None = None, max_file_mb: int = 64,
        quiet: bool = False) -> int:
    if not server.startswith(("http://", "https://")):
        print("error: --server must start with http:// or https://", file=sys.stderr)
        return 1
    if not key:
        print("error: the key is needed (--key, or IYICAM_KEY); 'iyicam serve --show-key' on the server shows it", file=sys.stderr)
        return 1
    if not server.startswith("https://") and not server.split("//", 1)[1].startswith(("localhost", "127.", "[::1]", "192.168.", "10.")):
        print("warning: the key travels in the clear over plain HTTP; use https:// across the internet", file=sys.stderr)
    try:                                          # (a service manager stops a program with SIGTERM: leave like Ctrl+C does)
        signal.signal(signal.SIGTERM, lambda *_: (_ for _ in ()).throw(KeyboardInterrupt()))
    except (ValueError, OSError):                 # (not the main thread)
        pass
    return Agent(server, key, roots or default_roots(), name or "", max_file_mb * 1024 * 1024, quiet).run()

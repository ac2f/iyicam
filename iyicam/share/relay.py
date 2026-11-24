"""The relay: what lets browsers reach the files of a computer that only makes OUTGOING connections.

The agent (``iyicam agent``, :mod:`iyicam.share.agent`) sits in the workshop behind a router.  It asks the server "is there
anything for me?" (a long poll, held open for up to :data:`HOLD_SEC`, which a proxy in between survives), does what a browser
asked - list, stat, read or write a file inside its roots - and sends the answer back.  Nothing reaches into the workshop.

A call of a browser becomes a **job** in a queue; browsers take turns, any number of them can share the one agent.  The
body of a write travels the other way (the agent fetches it with the job's id) so that a file is bytes all the way, not
base64 in JSON.  Nothing of a file stays on the server after the call.

There is one agent per server: a second one with another id is refused while the first is connected (two computers
under one key would answer the same path differently).
"""
from __future__ import annotations

import json
import re
import secrets
import threading
import time
from collections import deque

from .rootfs import FsError

HOLD_SEC = 25.0                  # how long a poll waits for a job before it answers "nothing" (a proxy gives up near 100 s)
ONLINE_SEC = 35.0                # an agent not heard of for this long is not connected
JOB_TIMEOUT = 120.0              # how long a browser's call waits for the agent (a big file over a slow line)
MAX_PENDING = 32
AGENT_ID = re.compile(r"^[A-Za-z0-9_-]{8,64}$")


class RelayError(Exception):
    def __init__(self, status: int, code: str, message: str):
        super().__init__(message)
        self.status, self.code, self.message = status, code, message


class _Job:
    __slots__ = ("id", "op", "params", "body", "done", "status", "out", "taken")

    def __init__(self, op: str, params: dict, body: bytes | None):
        self.id = secrets.token_hex(8)
        self.op, self.params, self.body = op, params, body
        self.done = threading.Event()
        self.status, self.out, self.taken = 0, b"", False


class Relay:
    def __init__(self, hold: float = HOLD_SEC, online_sec: float = ONLINE_SEC, job_timeout: float = JOB_TIMEOUT):
        self.hold, self.online_sec, self.job_timeout = hold, online_sec, job_timeout
        self._cv = threading.Condition()
        self._agent: dict | None = None            # {id, name, roots, last_seen}
        self._pending: deque[_Job] = deque()
        self._inflight: dict[str, _Job] = {}
        self._closed = False

    # -- what a browser's side asks ---------------------------------------------------------------
    def _online(self) -> bool:
        return self._agent is not None and time.time() - self._agent["last_seen"] < self.online_sec

    def status(self) -> dict | None:
        """``{connected, name, roots}`` or ``None`` when no agent was ever seen / it is gone."""
        with self._cv:
            if not self._online():
                return None
            return {"connected": True, "name": self._agent["name"], "roots": list(self._agent["roots"])}

    def submit(self, op: str, params: dict, body: bytes | None = None) -> tuple[int, bytes]:
        """One call for the agent: ``(status, body)`` as the agent answered; waits for it."""
        with self._cv:
            if not self._online():
                raise FsError("agent_offline", "the agent (iyicam agent on the workshop computer) is not connected")
            if len(self._pending) + len(self._inflight) >= MAX_PENDING:
                raise FsError("too_busy", "too many calls are waiting for the agent")
            job = _Job(op, params, body)
            self._pending.append(job)
            self._cv.notify_all()
        deadline = time.time() + self.job_timeout
        gone = False
        while True:                                  # (an agent that goes away is noticed, not waited for)
            left = deadline - time.time()
            if left <= 0:
                break
            if job.done.wait(min(left, 2.0)):
                return job.status, job.out
            with self._cv:
                if not self._online() and not job.taken:
                    gone = True
                    break
        with self._cv:                               # nobody answered: forget the call
            try:
                self._pending.remove(job)
            except ValueError:
                pass
            self._inflight.pop(job.id, None)
        if job.done.is_set():
            return job.status, job.out
        if gone:
            raise FsError("agent_offline", "the agent (iyicam agent on the workshop computer) is not connected")
        raise FsError("agent_timeout", "the agent did not answer in time")

    # -- what the agent's side does -----------------------------------------------------------------
    def poll(self, agent_id: object, name: object, roots: object, hold: float | None = None) -> dict | None:
        """The agent says it is there (and what its roots are) and takes the next job, waiting up to ``hold`` seconds."""
        if not isinstance(agent_id, str) or not AGENT_ID.match(agent_id):
            raise RelayError(400, "invalid_agent", "the agent id is not valid")
        names = [str(r)[:200] for r in roots][:64] if isinstance(roots, list) else []
        end = time.time() + (self.hold if hold is None else hold)
        with self._cv:
            if self._online() and self._agent["id"] != agent_id:
                raise RelayError(409, "agent_taken", "another agent is connected to this server")
            self._agent = {"id": agent_id, "name": str(name or "")[:60], "roots": names, "last_seen": time.time()}
            while True:
                self._agent["last_seen"] = time.time()
                if self._pending:
                    job = self._pending.popleft()
                    job.taken = True
                    self._inflight[job.id] = job
                    return {"id": job.id, "op": job.op, "params": job.params, "body_size": len(job.body) if job.body is not None else 0}
                left = end - time.time()
                if left <= 0 or self._closed:
                    return None
                self._cv.wait(min(left, 5.0))

    def body(self, agent_id: str, job_id: str) -> bytes:
        with self._cv:
            self._check(agent_id)
            job = self._inflight.get(job_id)
        if job is None or job.body is None:
            raise RelayError(404, "no_such_job", "there is no such call")
        return job.body

    def reply(self, agent_id: str, job_id: str, status: int, out: bytes) -> bool:
        """The agent's answer; ``False`` when nobody waits for it any more."""
        with self._cv:
            self._check(agent_id)
            self._agent["last_seen"] = time.time()
            job = self._inflight.pop(job_id, None)
        if job is None:
            return False
        job.status, job.out = status, out
        job.done.set()
        return True

    def bye(self, agent_id: str) -> None:
        """The agent is stopping: it is gone now, not 35 seconds from now."""
        with self._cv:
            if self._agent is not None and self._agent["id"] == agent_id:
                self._agent = None
            self._cv.notify_all()

    def _check(self, agent_id: str) -> None:
        if self._agent is None or self._agent["id"] != agent_id:
            raise RelayError(409, "unknown_agent", "this agent is not the connected one; poll first")

    def close(self) -> None:
        with self._cv:
            self._closed = True
            self._cv.notify_all()


class RemoteFS:
    """The same face as :class:`~iyicam.share.rootfs.RootFS`, answered by the agent."""

    def __init__(self, relay: Relay, max_bytes: int):
        self.relay, self.max_bytes = relay, max_bytes

    def _ask(self, op: str, params: dict, body: bytes | None = None) -> bytes:
        status, out = self.relay.submit(op, params, body)
        if status >= 400:
            try:
                d = json.loads(out.decode("utf-8"))
                code = str(d.pop("error", "os_error"))
                msg = str(d.pop("message", "the agent could not do this"))
            except (ValueError, UnicodeDecodeError, AttributeError):
                raise FsError("os_error", f"the agent answered {status}") from None
            raise FsError(code, msg, **{k: v for k, v in d.items() if isinstance(k, str)})
        return out

    def roots(self) -> list[dict]:
        st = self.relay.status()
        return [{"name": n, "path": n} for n in (st["roots"] if st else [])]

    def list(self, path: str = "") -> dict:
        return json.loads(self._ask("list", {"path": path}))

    def stat(self, path: str) -> dict:
        return json.loads(self._ask("stat", {"path": path}))

    def read(self, path: str) -> bytes:
        return self._ask("read", {"path": path})

    def write(self, path: str, data: bytes, overwrite: bool = False) -> dict:
        return json.loads(self._ask("write", {"path": path, "overwrite": bool(overwrite)}, data))

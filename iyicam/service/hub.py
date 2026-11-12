"""The hub: a server with no engine of its own that passes what a browser asks to an agent.

``iyicam serve --hub`` is for a server that is not where the drawings and the machine's folders are, a small Linux
server on the internet, say.  The computer of the person runs ``iyicam agent`` (:mod:`iyicam.service.agent`), which
connects OUTWARD to the server (nothing has to reach into the workshop) and does the work there, with the files of
that computer.  The two keys:

* the **server key** (``server.key`` of the server) only lets an agent register; it never signs a browser in;
* the **client key** belongs to one agent and is made by it, on that computer.  A browser signs in with it and the
  session belongs to that agent; a new client key ends the sessions of the old one.

A browser's call becomes a *job*: it waits in the queue of the agent, the agent takes it with its next poll
(``/agent/poll``, held open for up to :data:`POLL_HOLD_SEC`), does it and answers (``/agent/reply``).  Nothing about the
drawing stays on the server except for as long as the call takes.  Registered agents (their name and the digest of
the client key, never the key) are kept in ``agents.json``, so after a restart of the server the same client key opens
the door again and the agents come back by themselves (the sessions of the browsers are in memory and are not kept).

This module has no sockets; :mod:`iyicam.service.server` puts it on the web.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
import secrets
import threading
import time
from collections import deque
from pathlib import Path

from ..core import paths
from .keys import KeyStore, Throttled, is_key, normalise_key

AGENT_ONLINE_SEC = 60.0          # an agent not heard of for this long counts as not connected (it polls every ~25 s)
POLL_HOLD_SEC = 25.0             # how long a poll waits for a job before it answers "nothing" (a proxy gives up at 100 s)
JOB_TIMEOUT_SEC = 300.0          # how long a browser's call waits for the agent's answer
MAX_PENDING = 32                 # calls waiting for one agent
SESSION_TTL = 12 * 3600.0
AGENT_ID = re.compile(r"^[0-9a-f]{32}$")


class HubError(Exception):
    """A refusal with its HTTP status and error code."""

    def __init__(self, status: int, code: str, message: str):
        super().__init__(message)
        self.status, self.code, self.message = status, code, message


def digest(key: object) -> str:
    return hashlib.sha256(normalise_key(key).encode()).hexdigest()


class _Job:
    __slots__ = ("id", "op", "payload", "done", "status", "out")

    def __init__(self, op: str, payload: dict):
        self.id = secrets.token_hex(8)
        self.op, self.payload = op, payload
        self.done = threading.Event()
        self.status, self.out = 0, {}                   # (``out``: the answer, a dict or, as it came from the agent, its JSON as bytes)


class _Agent:
    def __init__(self, agent_id: str, name: str = "", key_digest: str = ""):
        self.id, self.name, self.digest = agent_id, name, key_digest
        self.last_seen = 0.0
        self.pending: deque[_Job] = deque()          # calls the agent has not taken yet
        self.inflight: dict[str, _Job] = {}          # calls it has taken and not answered


class Hub:
    def __init__(self, keys: KeyStore, state: Path | None = None, *, online_sec: float = AGENT_ONLINE_SEC,
                 hold: float = POLL_HOLD_SEC, job_timeout: float = JOB_TIMEOUT_SEC, max_fail: int = 10,
                 window: float = 300.0):
        self.keys = keys
        self.state = Path(state) if state else paths.user_dir() / "agents.json"
        self.online_sec, self.hold, self.job_timeout = online_sec, hold, job_timeout
        self.max_fail, self.window = max_fail, window
        self._cv = threading.Condition()
        self._agents: dict[str, _Agent] = {}
        self._by_digest: dict[str, str] = {}
        self._fails: dict[str, list[float]] = {}
        self._closed = False
        self._load()

    # -- the registry on disk --------------------------------------------------------------
    def _load(self) -> None:
        try:
            data = json.loads(self.state.read_text(encoding="utf-8"))
            items = data.get("agents", {}) if isinstance(data, dict) else {}
        except (OSError, ValueError):
            return
        for agent_id, item in items.items():
            if AGENT_ID.match(str(agent_id)) and isinstance(item, dict) and isinstance(item.get("digest"), str):
                self._agents[agent_id] = _Agent(agent_id, str(item.get("name", ""))[:60], item["digest"])
                self._by_digest[item["digest"]] = agent_id

    def _save(self) -> None:
        data = {"version": 1, "agents": {a.id: {"name": a.name, "digest": a.digest}
                                         for a in self._agents.values() if a.digest}}
        try:
            self.state.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.state.with_name(self.state.name + ".tmp")
            fd = os.open(str(tmp), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(data, f)
            os.replace(tmp, self.state)
        except OSError:
            pass                      # (the agents register again by themselves; only a restart would show it)

    # -- the server key: what lets an agent in --------------------------------------------------
    def _recent(self, ip: str, now: float) -> list[float]:
        fails = [t for t in self._fails.get(ip, []) if now - t < self.window]
        if fails:
            self._fails[ip] = fails
        else:
            self._fails.pop(ip, None)
        return fails

    def check_server_key(self, ip: str, given: object) -> None:
        """Returns when the key is the server's; raises :class:`HubError` (403) when it is not and
        :class:`~iyicam.service.keys.Throttled` for an address that keeps trying."""
        now = time.time()
        with self._cv:
            fails = self._recent(ip, now)
            if len(fails) >= self.max_fail:
                raise Throttled(self.window - (now - fails[0]))
        ok = hmac.compare_digest(normalise_key(given).encode(), normalise_key(self.keys.get()).encode())
        with self._cv:
            if ok:
                self._fails.pop(ip, None)
                return
            self._fails.setdefault(ip, []).append(now)
            if len(self._fails) > 2000:
                for k in list(self._fails):
                    self._recent(k, now)
        raise HubError(403, "bad_server_key", "the server key is not right")

    # -- agents ------------------------------------------------------------------------------
    def register(self, agent_id: object, token: object, name: object = "") -> None:
        """An agent says who it is and what its client key is (again at every start: that is how a key is changed)."""
        if not isinstance(agent_id, str) or not AGENT_ID.match(agent_id):
            raise HubError(400, "invalid_agent", "the agent id is not valid")
        if not isinstance(token, str) or not is_key(token):
            raise HubError(400, "invalid_client_key", "the client key is not valid")
        key_digest = digest(token)
        with self._cv:
            other = self._by_digest.get(key_digest)
            if other and other != agent_id:
                raise HubError(409, "key_taken", "this client key belongs to another agent")
            agent = self._agents.get(agent_id)
            if agent is None:
                agent = self._agents[agent_id] = _Agent(agent_id)
            if agent.digest and agent.digest != key_digest:
                self._by_digest.pop(agent.digest, None)
            agent.digest = key_digest
            if isinstance(name, str) and name:
                agent.name = name[:60]
            self._by_digest[key_digest] = agent_id
            agent.last_seen = time.time()
            self._save()

    def agent_for_key(self, given: object) -> str | None:
        if not is_key(given):
            return None
        with self._cv:
            return self._by_digest.get(digest(given))

    def digest_of(self, agent_id: str) -> str | None:
        with self._cv:
            agent = self._agents.get(agent_id)
            return agent.digest if agent else None

    def _online(self, agent: _Agent) -> bool:
        return time.time() - agent.last_seen < self.online_sec

    def is_online(self, agent_id: str) -> bool:
        with self._cv:
            agent = self._agents.get(agent_id)
            return agent is not None and self._online(agent)

    def agents(self) -> list[dict]:
        with self._cv:
            return [{"id": a.id, "name": a.name, "online": self._online(a)} for a in self._agents.values()]

    def poll(self, agent_id: object, hold: float | None = None) -> dict | None:
        """The next call for this agent, waiting up to ``hold`` seconds for one; ``None`` when there is none."""
        end = time.time() + (self.hold if hold is None else hold)
        with self._cv:
            agent = self._agents.get(agent_id) if isinstance(agent_id, str) else None
            if agent is None:
                raise HubError(409, "unknown_agent", "the server does not know this agent; register first")
            while True:
                agent.last_seen = time.time()
                if agent.pending:
                    job = agent.pending.popleft()
                    agent.inflight[job.id] = job
                    return {"id": job.id, "op": job.op, "payload": job.payload}
                left = end - time.time()
                if left <= 0 or self._closed:
                    return None
                self._cv.wait(min(left, 5.0))

    def reply(self, agent_id: object, job_id: object, status: object, out: object, raw: bytes | None = None) -> bool:
        """The agent's answer to a call it took.  ``False`` when nobody waits for it any more (the call timed out)."""
        with self._cv:
            agent = self._agents.get(agent_id) if isinstance(agent_id, str) else None
            if agent is None:
                raise HubError(409, "unknown_agent", "the server does not know this agent; register first")
            agent.last_seen = time.time()
            job = agent.inflight.pop(job_id, None) if isinstance(job_id, str) else None
        if job is None:
            return False
        if raw is not None:                             # the answer as the agent wrote it: it goes on as it is, not parsed here
            out = raw if raw[:1] == b"{" and raw[-1:] == b"}" else None
        if not (isinstance(status, int) and not isinstance(status, bool) and 100 <= status <= 599) \
                or not isinstance(out, (dict, bytes)):
            status, out = 502, {"error": "bad_agent_answer", "message": "the agent's answer could not be read"}
        job.status, job.out = status, out
        job.done.set()
        return True

    def submit(self, agent_id: str, op: str, payload: dict, timeout: float | None = None) -> tuple[int, dict]:
        """A browser's call for the agent: ``(HTTP status, answer)``; waits for the agent's answer."""
        with self._cv:
            agent = self._agents.get(agent_id)
            if agent is None or not self._online(agent):
                return 503, {"error": "agent_offline",
                             "message": "the agent (iyicam agent on the computer) is not connected"}
            if len(agent.pending) + len(agent.inflight) >= MAX_PENDING:
                return 503, {"error": "too_busy", "message": "too many calls are waiting for the agent"}
            job = _Job(op, payload)
            agent.pending.append(job)
            self._cv.notify_all()
        deadline = time.time() + (self.job_timeout if timeout is None else timeout)
        gone = False
        while True:                                     # (an agent that goes away is noticed, not waited for)
            left = deadline - time.time()
            if left <= 0:
                break
            if job.done.wait(min(left, 2.0)):
                return job.status, job.out
            with self._cv:
                if not self._online(agent):
                    gone = True
                    break
        with self._cv:                                  # nobody answered: forget the call
            try:
                agent.pending.remove(job)
            except ValueError:
                pass
            agent.inflight.pop(job.id, None)
        if job.done.is_set():                           # (the answer came while we were taking the lock)
            return job.status, job.out
        if gone:
            return 503, {"error": "agent_offline",
                         "message": "the agent (iyicam agent on the computer) is not connected"}
        return 504, {"error": "agent_timeout", "message": "the agent did not answer in time"}

    def close(self) -> None:
        with self._cv:
            self._closed = True
            self._cv.notify_all()


class HubAuth:
    """Signing in a browser with the client key of an agent.  Same face as :class:`iyicam.service.server.Auth`; the
    session also says which agent it belongs to."""

    def __init__(self, hub: Hub, ttl: float = SESSION_TTL, max_fail: int = 10, window: float = 300.0,
                 max_sessions: int = 256):
        self.hub, self.keys = hub, hub.keys
        self.ttl, self.max_fail, self.window, self.max_sessions = ttl, max_fail, window, max_sessions
        self._lock = threading.Lock()
        self._sessions: dict[str, tuple[float, str, str]] = {}      # token -> (expires, agent id, digest of its key)
        self._fails: dict[str, list[float]] = {}

    def _recent(self, ip: str, now: float) -> list[float]:
        fails = [t for t in self._fails.get(ip, []) if now - t < self.window]
        if fails:
            self._fails[ip] = fails
        else:
            self._fails.pop(ip, None)
        return fails

    def login(self, ip: str, given: object) -> str | None:
        """A session token for the client key of an agent, ``None`` for any other; raises :class:`Throttled`."""
        now = time.time()
        with self._lock:
            fails = self._recent(ip, now)
            if len(fails) >= self.max_fail:
                raise Throttled(self.window - (now - fails[0]))
        agent_id = self.hub.agent_for_key(given)
        with self._lock:
            if agent_id is None:
                self._fails.setdefault(ip, []).append(now)
                if len(self._fails) > 2000:
                    for k in list(self._fails):
                        self._recent(k, now)
                return None
            self._fails.pop(ip, None)
            self._sessions = {t: v for t, v in self._sessions.items() if v[0] > now}
            while len(self._sessions) >= self.max_sessions:
                del self._sessions[min(self._sessions, key=lambda t: self._sessions[t][0])]
            token = secrets.token_urlsafe(32)
            self._sessions[token] = (now + self.ttl, agent_id, digest(given))
            return token

    def agent_of(self, token: str | None) -> str | None:
        """The agent a session belongs to; ``None`` for no session, an old one, or one of a key that was replaced."""
        if not token:
            return None
        with self._lock:
            entry = self._sessions.get(token)
        if entry is None or entry[0] <= time.time():
            return None
        current = self.hub.digest_of(entry[1])
        if current is None or not hmac.compare_digest(current, entry[2]):
            return None
        return entry[1]

    def valid(self, token: str | None) -> bool:
        return self.agent_of(token) is not None

    def logout(self, token: str | None) -> None:
        with self._lock:
            self._sessions.pop(token or "", None)

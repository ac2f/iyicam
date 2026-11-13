"""The sessions of browsers that signed in with a key: kept in memory and, when a file is given, on disk.

A browser that signs in with "keep me signed in" gets a session of 90 days (a browser that does not, 12 hours); every
time the page is opened the 90 days start again, so a phone that is used stays signed in for good, and one that is not
used for three months asks for the key again.  The sessions are in a file (``~/.iyicam/sessions.json``), so a server
that is restarted (an update, a reboot of the computer) does not sign everybody out.

Only a hash of a token is kept (in memory and in the file): the file is of no use to anyone who reads it.  A session
also holds a proof of the key it was made with (a digest), so **a new key ends every session**, the old ones in the file
too.  Shared by the server (:mod:`iyicam.service.server`) and the hub (:mod:`iyicam.service.hub`).
"""
from __future__ import annotations

import hashlib
import json
import os
import threading
import time
from pathlib import Path

SESSION_TTL = 12 * 3600.0                # a browser that is not kept signed in
REMEMBER_TTL = 90 * 86400.0              # one that is
RENEW_AFTER = 86400.0                    # a kept session is lengthened at most once a day (the file is written then)


def token_id(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


class SessionStore:
    """``token -> (expires, who, proof, ttl)``.  ``who`` is whatever the owner wants kept with a session (the hub: the
    agent), ``proof`` what must still be true for it to count (a digest of the key).  Thread safe."""

    def __init__(self, path: Path | None = None, max_sessions: int = 64):
        self.path = Path(path) if path else None
        self.max_sessions = max_sessions
        self._lock = threading.Lock()
        self._items: dict[str, tuple[float, str, str, float]] = {}
        self._load()

    # -- the file ------------------------------------------------------------------------------
    def _load(self) -> None:
        if self.path is None:
            return
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return                                   # (no file yet, or a damaged one: nobody is signed in, that is all)
        items = data.get("sessions") if isinstance(data, dict) else None
        if not isinstance(items, dict):
            return
        now = time.time()
        for sid, v in items.items():
            try:
                expires, who, proof, ttl = float(v[0]), str(v[1]), str(v[2]), float(v[3])
            except (TypeError, ValueError, IndexError, KeyError):
                continue
            if isinstance(sid, str) and len(sid) == 64 and expires > now:
                self._items[sid] = (expires, who, proof, ttl)

    def _save(self) -> None:
        if self.path is None:
            return
        data = {"version": 1, "sessions": {k: list(v) for k, v in self._items.items()}}
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_name(self.path.name + ".tmp")
            fd = os.open(str(tmp), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)     # (Windows ignores the mode)
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(data, f)
            os.replace(tmp, self.path)
        except OSError:
            pass                                     # (the sessions then live as long as the server: nothing is lost)

    # -- the sessions ----------------------------------------------------------------------------
    def add(self, token: str, ttl: float, who: str = "", proof: str = "", now: float | None = None) -> float:
        """Keep a session of ``ttl`` seconds; returns when it ends.  The oldest go when there are too many."""
        now = time.time() if now is None else now
        with self._lock:
            self._items = {k: v for k, v in self._items.items() if v[0] > now}
            while len(self._items) >= self.max_sessions:
                del self._items[min(self._items, key=lambda k: self._items[k][0])]
            expires = now + ttl
            self._items[token_id(token)] = (expires, who, proof, float(ttl))
            self._save()
            return expires

    def get(self, token: str | None, now: float | None = None) -> tuple[float, str, str, float] | None:
        """``(expires, who, proof, ttl)`` of a session that is there and not over, else ``None``."""
        if not token:
            return None
        now = time.time() if now is None else now
        with self._lock:
            entry = self._items.get(token_id(token))
        return entry if entry is not None and entry[0] > now else None

    def renew(self, token: str | None, now: float | None = None) -> float | None:
        """Lengthen a kept session (one of more than the 12 hours) to its whole time again; at most once a day.
        Returns the seconds it now has, or ``None`` when nothing changed."""
        now = time.time() if now is None else now
        entry = self.get(token, now)
        if entry is None or entry[3] <= SESSION_TTL or entry[0] - now > entry[3] - RENEW_AFTER:
            return None
        with self._lock:
            sid = token_id(token)
            cur = self._items.get(sid)
            if cur is None:
                return None
            self._items[sid] = (now + cur[3], cur[1], cur[2], cur[3])
            self._save()
            return cur[3]

    def remove(self, token: str | None) -> None:
        if not token:
            return
        with self._lock:
            if self._items.pop(token_id(token), None) is not None:
                self._save()

    def __len__(self) -> int:
        with self._lock:
            return len(self._items)

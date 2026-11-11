"""Keys: the key of a server or of an agent is 24 letters and digits, made once and kept in a file.

Shared by the web server (``iyicam serve``, :mod:`iyicam.service.server`), the hub in it (:mod:`iyicam.service.hub`)
and the agent (:mod:`iyicam.service.agent`).  Nothing here talks to a network.
"""
from __future__ import annotations

import os
import re
import secrets
import threading
from pathlib import Path

from ..core import paths

KEY_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"      # no 0 O 1 I: a key is read from a screen and typed on a phone


def key_file() -> Path:
    return paths.user_dir() / "server.key"


def make_key() -> str:
    """``ABCDEF-GHJKLM-NPQRST-UVWXYZ``: 24 characters of 32, about 120 bits."""
    chars = "".join(secrets.choice(KEY_ALPHABET) for _ in range(24))
    return "-".join(chars[i:i + 6] for i in range(0, 24, 6))


def normalise_key(text: object) -> str:
    """Dashes, spaces and case do not matter when a key is typed."""
    return re.sub(r"[^A-Za-z0-9]", "", str(text or "")).upper()


def is_key(text: object) -> bool:
    """Looks like a key (long enough, letters and digits); says nothing about whether it is the right one."""
    return len(normalise_key(text)) >= 16


class Throttled(Exception):
    def __init__(self, wait: float):
        super().__init__("too many failed attempts")
        self.wait = wait


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

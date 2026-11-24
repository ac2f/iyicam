"""The iyicam package as a zip for the browser: what the engine in the worker needs, nothing of the window or the web.

The worker unpacks it into the file system of Pyodide, so it holds the same files as the installed package except the
ones that are for other places: ``gui`` (Qt), ``web`` (the old page), ``webapp`` (the page itself: the browser fetches
it on its own), ``share`` (the server and the agent) and the command line.  The zip is built from the files that are
installed, so it can never be older than the server, and is kept (in memory) until one of them changes.  Compiled
files are left out: Pyodide's Python is not the one that made them.
"""
from __future__ import annotations

import hashlib
import io
import os
import threading
import zipfile
from pathlib import Path

PACKAGE = Path(__file__).resolve().parent.parent
SKIP_DIRS = {"gui", "web", "webapp", "share", "__pycache__"}
SKIP_FILES = {"cli.py", "__main__.py", "selftest.py"}
SKIP_SUFFIX = {".pyc", ".pyo"}


def _files(root: Path = PACKAGE):
    for base, dirs, names in os.walk(root):
        dirs[:] = sorted(d for d in dirs if d not in SKIP_DIRS)
        rel = Path(base).relative_to(root)
        for n in sorted(names):
            if (not rel.parts and n in SKIP_FILES) or Path(n).suffix in SKIP_SUFFIX or n.startswith("."):
                continue
            yield Path(base) / n, (rel / n)


class Bundle:
    """``data`` and ``etag`` of the zip; ``refresh()`` rebuilds it only when a file's size or time changed."""

    def __init__(self, root: Path = PACKAGE):
        self.root = Path(root)
        self._lock = threading.Lock()
        self._sig = None
        self.data = b""
        self.etag = ""

    def refresh(self) -> "Bundle":
        with self._lock:
            items, sig = [], hashlib.sha1()
            for p, rel in _files(self.root):
                st = p.stat()
                items.append((p, rel))
                sig.update(f"{rel.as_posix()}|{st.st_size}|{st.st_mtime_ns}\n".encode())
            digest = sig.hexdigest()
            if digest != self._sig:
                buf = io.BytesIO()
                with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as z:
                    for p, rel in items:
                        info = zipfile.ZipInfo(f"{self.root.name}/{rel.as_posix()}", (2020, 1, 1, 0, 0, 0))   # (a fixed time: the same bytes for the same files)
                        info.compress_type = zipfile.ZIP_DEFLATED
                        z.writestr(info, p.read_bytes())
                self.data, self.etag, self._sig = buf.getvalue(), '"' + digest[:24] + '"', digest
            return self

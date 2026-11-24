"""The bridge between the worker (JavaScript) and the operations of iyiCAM, inside Pyodide.

``worker.js`` copies the iyicam package into the file system of Pyodide, puts this file next to it and calls
:func:`init` once and :func:`call` for every operation.  Everything that crosses is a JSON string: the project can be
megabytes, and a string is copied once where a nested JS object would be converted node by node.

It is NOT part of the package that is sent to the browser (``iyicam.zip``): the page fetches it by itself, so that a
change here needs no new package.
"""
from __future__ import annotations

import inspect
import json
import os
import sys
import time
import traceback

HOME = "/home/pyodide/.iyicam"                 # IYICAM_HOME: a folder of the file system that JS keeps in IndexedDB
PROGRESS_EVERY = 0.05                          # seconds: progress is not posted more often (a message per polyline would drown the page)

_ops = None
_JobCancelled = None
_ServiceError = None
_jsonable = None


def init(home: str = HOME) -> str:
    """Make the engine ready; returns the version of iyiCAM."""
    global _ops, _JobCancelled, _ServiceError, _jsonable
    os.environ["IYICAM_HOME"] = home
    os.makedirs(home, exist_ok=True)
    from iyicam import __version__
    from iyicam.service import ops
    from iyicam.service.errors import JobCancelled, ServiceError
    from iyicam.service.wire import jsonable
    ops.ensure_ready()
    _ops, _JobCancelled, _ServiceError, _jsonable = ops, JobCancelled, ServiceError, jsonable
    return __version__


class Control:
    """What ``ops.py`` calls a ``JobControl``: it reports progress and says whether the caller cancelled.  The page cannot
    reach into a running Python call, so ``cancelled`` reads a flag that the page sets from the other thread (a
    SharedArrayBuffer); without one it is always false and the page ends the call by restarting the worker."""

    def __init__(self, post_progress, is_cancelled):
        self._post, self._flag = post_progress, is_cancelled
        self._last = 0.0

    def progress(self, k: float, n: float, label: str = "") -> None:
        now = time.monotonic()
        if now - self._last < PROGRESS_EVERY and k < n:
            return
        self._last = now
        if self._post is not None:
            self._post(float(k), float(n), str(label or ""))

    def cancelled(self) -> bool:
        return bool(self._flag()) if self._flag is not None else False

    def check(self) -> None:
        if self.cancelled():
            raise _JobCancelled()


def _snapshot() -> tuple:
    """What is in the user folder (names, sizes, times): when it differs after an operation, JS writes it to IndexedDB."""
    out = []
    for base, _dirs, files in os.walk(os.environ.get("IYICAM_HOME", HOME)):
        for f in files:
            p = os.path.join(base, f)
            try:
                st = os.stat(p)
            except OSError:
                continue
            out.append((p, st.st_size, st.st_mtime_ns))
    return tuple(sorted(out))


def _dump(obj) -> str:
    try:
        return json.dumps(obj, allow_nan=False, separators=(",", ":"))
    except (ValueError, TypeError):                 # (NaN, a tuple key, a set ...: the same clean-up the service uses elsewhere)
        return json.dumps(_jsonable(obj), allow_nan=False, separators=(",", ":"))


def call(op: str, payload_json: str, post_progress=None, is_cancelled=None) -> str:
    """Run ``ops.op_<op>`` and answer ``{"ok": true, "result": ..., "dirty": bool}`` or ``{"ok": false, "error": {code,
    message, details}}`` as a JSON string.  Never raises."""
    before = _snapshot()
    try:
        fn = getattr(_ops, "op_" + str(op), None) if isinstance(op, str) and op.replace("_", "").isalnum() else None
        if fn is None or not callable(fn):
            raise _ServiceError("unknown_operation", f"there is no operation '{op}'", operation=op)
        payload = json.loads(payload_json) if payload_json else {}
        kwargs = {}
        if "ctl" in inspect.signature(fn).parameters:
            kwargs["ctl"] = Control(post_progress, is_cancelled)
        result = fn(payload, **kwargs)
        return _dump({"ok": True, "result": result, "dirty": _snapshot() != before})
    except _JobCancelled:
        err = {"code": "cancelled", "message": "cancelled", "details": None}
    except _ServiceError as exc:
        err = {"code": exc.code, "message": exc.message, "details": _jsonable(exc.params) if exc.params else None}
    except Exception as exc:  # noqa: BLE001  (a bug: the page gets the traceback, the engine stays up)
        err = {"code": "internal", "message": f"{type(exc).__name__}: {exc}", "details": traceback.format_exc()}
    return _dump({"ok": False, "error": err, "dirty": _snapshot() != before})

"""One entry point for everything the service can do: ``handle("compute", payload)``.

Quick operations answer at once; the long ones (``compute``, ``gcode``, ``simulate``, ``nest``) can be started as
jobs with ``job.start`` and followed with ``job.status`` / ``job.result`` / ``job.cancel``, or run at once with
``run_sync=True`` (scripts and tests).  ``handle`` never raises: a failure is ``{"error": code, "message": ...}``.
"""
from __future__ import annotations

import sys
import traceback
from typing import Callable

from . import ops
from .errors import JobCancelled, ServiceError
from .jobs import JobControl, JobManager

QUICK: dict[str, Callable[[dict], dict]] = {
    "ping": ops.op_ping,
    "schema": ops.op_schema,
    "validate": ops.op_validate,
    "import": ops.op_import,
    "export": ops.op_export,
    "command": ops.op_command,
}

LONG: dict[str, Callable[..., dict]] = {
    "compute": ops.op_compute,
    "gcode": ops.op_gcode,
    "simulate": ops.op_simulate,
    "nest": ops.op_nest,
}


# quick operations that can also run as jobs: a big offset or a big file may outlast what a relay waits for
JOBBABLE = {"command", "import", "export"}


class ServiceHost:
    def __init__(self, workers: int = 2, ttl: float = 1800.0):
        self.jobs = JobManager(workers=workers, ttl=ttl)

    # -- the raising interface (for Python callers) -----------------------------------------
    def call(self, op: str, payload: dict | None = None) -> dict:
        payload = payload if payload is not None else {}
        if not isinstance(payload, dict):
            raise ServiceError("bad_request", "the request must be an object")
        if op in QUICK:
            return QUICK[op](payload)
        if op in LONG:      # run in this thread, no job
            fn = LONG[op]
            try:
                return self.jobs.run_sync(lambda ctl: fn(payload, ctl))
            except JobCancelled as exc:
                raise ServiceError("job_cancelled", "the job was cancelled") from exc
        if op == "job.start":
            kind = str(payload.get("op") or "")
            if kind not in LONG and kind not in JOBBABLE:
                raise ServiceError("unknown_operation", "this operation cannot be started as a job",
                                   operation=kind, known=sorted(set(LONG) | JOBBABLE))
            inner = payload.get("payload")
            if not isinstance(inner, dict):
                raise ServiceError("bad_request", "'payload' must be an object")
            if kind in LONG:
                fn = LONG[kind]
                return {"id": self.jobs.start(kind, lambda ctl: fn(inner, ctl)), "state": "queued"}
            quick = QUICK[kind]
            return {"id": self.jobs.start(kind, lambda ctl: quick(inner)), "state": "queued"}
        if op == "job.status":
            return self.jobs.status(str(payload.get("id")))
        if op == "job.cancel":
            return self.jobs.cancel(str(payload.get("id")))
        if op == "job.result":
            return self.jobs.result(str(payload.get("id")))
        raise ServiceError("unknown_operation", f"there is no operation '{op}'", operation=op)

    # -- the interface of a transport ----------------------------------------------------------
    def handle(self, op: str, payload: dict | None = None) -> dict:
        try:
            return self.call(op, payload)
        except ServiceError as exc:
            return exc.to_dict()
        except Exception as exc:  # noqa: BLE001  (a bug: said as it is, the service stays up)
            traceback.print_exc(file=sys.stderr)
            return {"error": "internal_error", "message": f"{type(exc).__name__}: {exc}"}

"""Long calculations as jobs: start, ask how far it is, cancel, fetch the result.

A request/response channel (a relay with a timeout of about a minute and a half) cannot hold a nesting that
takes minutes, and a phone whose browser tab is killed must be able to come back to the job it started.  A job
keeps no document, only its progress and result.  Finished jobs are forgotten after ``ttl`` seconds.
"""
from __future__ import annotations

import secrets
import sys
import threading
import time
import traceback
from dataclasses import dataclass, field
from typing import Any, Callable

from .errors import JobCancelled, ServiceError


class JobControl:
    """What a job function gets: report progress, look whether it was cancelled."""

    def __init__(self, job: "Job"):
        self._job = job

    def progress(self, k: float, n: float, label: str = "") -> None:
        self._job.k, self._job.n = k, n
        if label:
            self._job.label = label

    def cancelled(self) -> bool:
        return self._job.cancel.is_set()

    def check(self) -> None:
        if self._job.cancel.is_set():
            raise JobCancelled()


@dataclass
class Job:
    id: str
    kind: str
    state: str = "queued"           # queued | running | done | error | cancelled
    k: float = 0.0
    n: float = 0.0
    label: str = ""
    result: Any = None
    error: dict | None = None
    created: float = field(default_factory=time.time)
    started: float | None = None
    finished: float | None = None
    cancel: threading.Event = field(default_factory=threading.Event)

    def status(self) -> dict:
        end = self.finished or time.time()
        return {"id": self.id, "kind": self.kind, "state": self.state, "k": self.k, "n": self.n,
                "label": self.label, "elapsed": round(end - (self.started or self.created), 2),
                "error": self.error}


class JobManager:
    def __init__(self, workers: int = 2, ttl: float = 1800.0, keep: int = 40, max_active: int = 16):
        self._slots = threading.BoundedSemaphore(max(1, workers))
        self.max_active = max_active          # most jobs waiting or running at once: more is refused, not queued
        self._lock = threading.Lock()
        self._jobs: dict[str, Job] = {}
        self.ttl = ttl
        self.keep = keep

    # -- bookkeeping ------------------------------------------------------------------
    def _forget_old(self) -> None:
        now = time.time()
        with self._lock:
            done = [j for j in self._jobs.values() if j.finished is not None]
            for j in done:
                if now - j.finished > self.ttl:
                    del self._jobs[j.id]
            extra = len(self._jobs) - self.keep
            if extra > 0:       # too many: the oldest finished ones go first
                for j in sorted((j for j in self._jobs.values() if j.finished is not None),
                                key=lambda j: j.finished)[:extra]:
                    del self._jobs[j.id]

    def _get(self, jid: str) -> Job:
        with self._lock:
            job = self._jobs.get(str(jid))
        if job is None:
            raise ServiceError("job_not_found", "there is no such job (it may have been forgotten)", id=str(jid))
        return job

    # -- the interface ------------------------------------------------------------------
    def start(self, kind: str, fn: Callable[[JobControl], dict]) -> str:
        self._forget_old()
        job = Job(secrets.token_hex(6), kind)
        with self._lock:
            if sum(1 for j in self._jobs.values() if j.finished is None) >= self.max_active:
                raise ServiceError("too_busy", "too many jobs are running; wait for one to finish or cancel it",
                                   limit=self.max_active)
            self._jobs[job.id] = job
        threading.Thread(target=self._run, args=(job, fn), daemon=True, name=f"iyicam-job-{job.id}").start()
        return job.id

    def _run(self, job: Job, fn: Callable[[JobControl], dict]) -> None:
        with self._slots:
            if job.cancel.is_set():
                job.state, job.finished = "cancelled", time.time()
                return
            job.state, job.started = "running", time.time()
            try:
                job.result = fn(JobControl(job))
                job.state = "cancelled" if job.cancel.is_set() and job.result is None else "done"
            except JobCancelled:
                job.state = "cancelled"
            except ServiceError as exc:
                job.state, job.error = "error", exc.to_dict()
            except BaseException as exc:    # noqa: BLE001  (ComputeCancelled / NestCancelled are cancellations)
                if type(exc).__name__ in ("ComputeCancelled", "NestCancelled"):
                    job.state = "cancelled"
                elif isinstance(exc, Exception):
                    traceback.print_exc(file=sys.stderr)
                    job.state = "error"
                    job.error = {"error": "internal_error", "message": f"{type(exc).__name__}: {exc}"}
                else:
                    raise
            finally:
                job.finished = time.time()

    def status(self, jid: str) -> dict:
        return self._get(jid).status()

    def cancel(self, jid: str) -> dict:
        job = self._get(jid)
        job.cancel.set()
        if job.state == "queued":
            job.state, job.finished = "cancelled", time.time()
        return job.status()

    def result(self, jid: str) -> dict:
        job = self._get(jid)
        if job.state in ("queued", "running"):
            raise ServiceError("job_not_finished", "the job is still running", id=job.id, state=job.state)
        if job.state == "cancelled":
            raise ServiceError("job_cancelled", "the job was cancelled", id=job.id)
        if job.state == "error":
            raise ServiceError((job.error or {}).get("error", "internal_error"),
                               (job.error or {}).get("message", "the job failed"),
                               **((job.error or {}).get("params") or {}))
        return job.result

    def run_sync(self, fn: Callable[[JobControl], dict]) -> dict:
        """Run ``fn`` in the calling thread (small jobs, tests, scripts)."""
        job = Job("sync", "sync")
        job.state, job.started = "running", time.time()
        return fn(JobControl(job))

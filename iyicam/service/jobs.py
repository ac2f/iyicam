"""Long calculations as jobs: start, ask how far it is, cancel, fetch the result.

A request/response channel (a relay with a timeout of about a minute and a half) cannot hold a nesting that
takes minutes, and a phone whose browser tab is killed must be able to come back to the job it started.  A job
runs here, in a thread of the engine, whatever the phone does: the connection may break, the browser may be closed or
put to sleep, the job goes on.  A job keeps no document, only its progress and result.  Finished jobs are forgotten after
``ttl`` seconds.

**Kept jobs.**  A job started with ``keep`` (the page does it for nesting) is one of the **last three** jobs of the engine,
kept until three newer ones push it out (or for a week): its result stays where it can be asked for again, also by
another device, and it is written to ``store`` (a folder: ``index.json`` and one gzip file per result), so a restart of
the engine does not lose a finished job (one that was still running is then said to have been interrupted).  Which devices
were given the result is kept, so that a device that comes back can be asked whether it wants what it has not received.
"""
from __future__ import annotations

import gzip
import json
import os
import re
import secrets
import sys
import threading
import time
import traceback
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from .errors import JobCancelled, ServiceError

RECENT = 3                                  # kept jobs that are listed (the newest; a job that is not over is never dropped)
RECENT_TTL = 7 * 86400.0                    # a finished kept job is dropped after a week
MAX_STORED = 256 * 1024 * 1024              # a result bigger than this (as JSON) is not written to disk, it stays in memory
JOB_ID = re.compile(r"^[0-9a-f]{12}$")
DEVICE_ID = re.compile(r"^[A-Za-z0-9_-]{6,40}$")


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
    finished_event: threading.Event = field(default_factory=threading.Event)       # set when the job is over, however it ended
    kept: bool = False              # one of the last three jobs (see the module text)
    meta: dict = field(default_factory=dict)           # title, device (the one that started it), device_label
    delivered: dict = field(default_factory=dict)      # device -> when it was given the result
    size: int = 0                   # the result as JSON, in bytes (kept jobs)
    stored: bool = False            # the result is in a file of the store

    def status(self) -> dict:
        end = self.finished or time.time()
        return {"id": self.id, "kind": self.kind, "state": self.state, "k": self.k, "n": self.n,
                "label": self.label, "elapsed": round(end - (self.started or self.created), 2),
                "error": self.error}


def clean_meta(meta: Any) -> dict:
    """What a device says about the job it starts: a title to show, who it is (a random id its page made, and a name like
    ``Android · Chrome``).  Anything else is dropped, text is made one short line."""
    if not isinstance(meta, dict):
        return {}

    def line(v: Any, n: int) -> str:
        return re.sub(r"[\x00-\x1f\x7f]+", " ", str(v or "")).strip()[:n]
    device = str(meta.get("device") or "")
    return {"title": line(meta.get("title"), 120), "device": device if DEVICE_ID.match(device) else "",
            "device_label": line(meta.get("device_label"), 60)}


class JobManager:
    def __init__(self, workers: int = 2, ttl: float = 1800.0, keep: int = 40, max_active: int = 16,
                 store: Path | None = None, recent: int = RECENT, recent_ttl: float = RECENT_TTL):
        self._slots = threading.BoundedSemaphore(max(1, workers))
        self.max_active = max_active          # most jobs waiting or running at once: more is refused, not queued
        self._lock = threading.RLock()
        self._jobs: dict[str, Job] = {}
        self.ttl = ttl
        self.keep = keep
        self.store = Path(store) if store else None
        self.recent, self.recent_ttl = recent, recent_ttl
        self._load()

    # -- the store (kept jobs only) ----------------------------------------------------------
    def _result_file(self, jid: str) -> Path:
        return self.store / f"{jid}.result.json.gz"        # (``jid`` is checked against JOB_ID wherever it comes from)

    def _atomic(self, path: Path, data: bytes) -> bool:
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp = path.with_name(path.name + ".tmp")
            fd = os.open(str(tmp), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)     # (Windows ignores the mode)
            with os.fdopen(fd, "wb") as f:
                f.write(data)
            os.replace(tmp, path)
            return True
        except OSError:
            return False                         # (the job then lives as long as the engine: nothing is lost before that)

    def _save_index(self) -> None:
        if self.store is None:
            return
        with self._lock:
            rows = [{"id": j.id, "kind": j.kind, "state": j.state, "label": j.label, "created": j.created,
                     "started": j.started, "finished": j.finished, "error": j.error, "meta": j.meta,
                     "delivered": j.delivered, "size": j.size, "stored": j.stored}
                    for j in self._jobs.values() if j.kept]
        index = self.store / "index.json"
        if not rows and not index.exists():
            return                                   # (an engine that never kept a job leaves nothing on the disk)
        self._atomic(index, json.dumps({"version": 1, "jobs": rows}).encode("utf-8"))

    def _store_result(self, job: Job) -> None:
        """Count the result and write it to a file (a failure here is not the job's failure)."""
        try:
            raw = json.dumps(job.result, separators=(",", ":"), default=str).encode("utf-8")
        except (TypeError, ValueError):
            return
        job.size = len(raw)
        if self.store is not None and job.size <= MAX_STORED:
            job.stored = self._atomic(self._result_file(job.id), gzip.compress(raw, 1))

    def _load(self) -> None:
        if self.store is None:
            return
        try:
            data = json.loads((self.store / "index.json").read_text(encoding="utf-8"))
            rows = data.get("jobs") if isinstance(data, dict) else None
        except (OSError, ValueError):
            rows = None
        now = time.time()
        for row in rows if isinstance(rows, list) else []:
            try:
                jid = str(row["id"])
                if not JOB_ID.match(jid):
                    continue
                job = Job(jid, str(row["kind"]), str(row["state"]), label=str(row.get("label") or ""),
                          created=float(row["created"]), started=row.get("started"), finished=row.get("finished"),
                          error=row.get("error") if isinstance(row.get("error"), dict) else None)
                job.kept, job.meta = True, clean_meta(row.get("meta"))
                dev = row.get("delivered")
                job.delivered = {str(k): float(v) for k, v in dev.items()} if isinstance(dev, dict) else {}
                job.size, job.stored = int(row.get("size") or 0), bool(row.get("stored"))
            except (KeyError, TypeError, ValueError):
                continue
            if job.state in ("queued", "running"):           # the engine was stopped while it ran: it cannot go on
                job.state, job.finished = "error", now
                job.error = {"error": "interrupted", "message": "the engine was restarted while the job was running"}
            if job.finished is None or now - job.finished > self.recent_ttl:
                self._drop_files(job.id)
                continue
            job.finished_event.set()
            self._jobs[jid] = job
        self._enforce_recent()
        self._save_index()

    def _drop_files(self, jid: str) -> None:
        if self.store is not None and JOB_ID.match(jid):
            try:
                self._result_file(jid).unlink(missing_ok=True)
            except OSError:
                pass

    # -- bookkeeping ------------------------------------------------------------------
    def _forget_old(self) -> None:
        now = time.time()
        with self._lock:
            done = [j for j in self._jobs.values() if j.finished is not None and not j.kept]
            for j in done:
                if now - j.finished > self.ttl:
                    del self._jobs[j.id]
            extra = len(self._jobs) - self.keep
            if extra > 0:       # too many: the oldest finished ones go first
                for j in sorted((j for j in self._jobs.values() if j.finished is not None and not j.kept),
                                key=lambda j: j.finished)[:extra]:
                    del self._jobs[j.id]
            for j in [j for j in self._jobs.values() if j.kept and j.finished is not None
                      and now - j.finished > self.recent_ttl]:
                self._remove(j)

    def _remove(self, job: Job) -> None:
        with self._lock:
            self._jobs.pop(job.id, None)
        self._drop_files(job.id)

    def _enforce_recent(self) -> None:
        """Only the newest ``recent`` kept jobs stay (one that is not over is never dropped)."""
        with self._lock:
            kept = sorted((j for j in self._jobs.values() if j.kept), key=lambda j: j.created, reverse=True)
            for j in kept[self.recent:]:
                if j.finished is not None:
                    self._remove(j)

    def _get(self, jid: str) -> Job:
        with self._lock:
            job = self._jobs.get(str(jid))
        if job is None:
            raise ServiceError("job_not_found", "there is no such job (it may have been forgotten)", id=str(jid))
        return job

    # -- the interface ------------------------------------------------------------------
    def start(self, kind: str, fn: Callable[[JobControl], dict], keep: Any = None) -> str:
        """Run ``fn`` as a job.  ``keep`` (an object with ``title``, ``device``, ``device_label``) makes it one of the last
        three jobs: its result stays, on disk too, for any device to ask for."""
        self._forget_old()
        job = Job(secrets.token_hex(6), kind)
        if keep is not None and keep is not False:
            job.kept, job.meta = True, clean_meta(keep)
        with self._lock:
            if sum(1 for j in self._jobs.values() if j.finished is None) >= self.max_active:
                raise ServiceError("too_busy", "too many jobs are running; wait for one to finish or cancel it",
                                   limit=self.max_active)
            self._jobs[job.id] = job
        if job.kept:
            self._enforce_recent()
            self._save_index()
        threading.Thread(target=self._run, args=(job, fn), daemon=True, name=f"iyicam-job-{job.id}").start()
        return job.id

    def wait(self, jid: str, timeout: float) -> Job:
        """The job, after waiting up to ``timeout`` seconds for it to be over (a quick job answers in the same call)."""
        job = self._get(jid)
        job.finished_event.wait(max(0.0, timeout))
        return job

    def forget(self, jid: str) -> None:
        with self._lock:
            job = self._jobs.get(str(jid))
        if job is not None and not job.kept:         # (a kept job goes with ``dismiss``, or when newer ones push it out)
            with self._lock:
                self._jobs.pop(job.id, None)

    def _run(self, job: Job, fn: Callable[[JobControl], dict]) -> None:
        with self._slots:
            if job.cancel.is_set():
                job.state, job.finished = "cancelled", time.time()
                self._finish_kept(job)
                job.finished_event.set()
                return
            job.state, job.started = "running", time.time()
            if job.kept:
                self._save_index()
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
                if job.kept and job.state == "done":
                    self._store_result(job)               # (before the job counts as over: it is there when it is asked for)
                job.finished = time.time()
                self._finish_kept(job)                    # (written down before anyone is told it is over)
                job.finished_event.set()

    def _finish_kept(self, job: Job) -> None:
        if job.kept:
            self._enforce_recent()
            self._save_index()

    def status(self, jid: str) -> dict:
        return self._get(jid).status()

    def cancel(self, jid: str) -> dict:
        job = self._get(jid)
        job.cancel.set()
        if job.state == "queued":
            job.state, job.finished = "cancelled", time.time()
            self._finish_kept(job)
            job.finished_event.set()
        return job.status()

    def _load_result(self, job: Job) -> Any:
        if job.result is not None:
            return job.result
        if self.store is None or not job.stored:
            raise ServiceError("job_not_found", "the result of this job is gone (it was not kept)", id=job.id)
        try:
            job.result = json.loads(gzip.decompress(self._result_file(job.id).read_bytes()))
        except (OSError, ValueError, EOFError) as exc:
            raise ServiceError("job_not_found", "the result of this job could not be read any more", id=job.id) from exc
        return job.result

    def result(self, jid: str, device: str | None = None) -> dict:
        """The result of a job that is done.  ``device`` (the id a page made for itself) is noted as having received it."""
        job = self._get(jid)
        if job.state in ("queued", "running"):
            raise ServiceError("job_not_finished", "the job is still running", id=job.id, state=job.state)
        if job.state == "cancelled":
            raise ServiceError("job_cancelled", "the job was cancelled", id=job.id)
        if job.state == "error":
            raise ServiceError((job.error or {}).get("error", "internal_error"),
                               (job.error or {}).get("message", "the job failed"),
                               **((job.error or {}).get("params") or {}))
        out = self._load_result(job) if job.kept else job.result
        if job.kept and isinstance(device, str) and DEVICE_ID.match(device):
            job.delivered[device] = time.time()
            self._save_index()
        return out

    def mark_delivered(self, job: Job, device: str | None = None) -> None:
        """Note that ``device`` has the result (a job that was over within the call that started it gave it at once)."""
        device = device or job.meta.get("device")
        if job.kept and isinstance(device, str) and DEVICE_ID.match(device):
            job.delivered[device] = time.time()
            self._save_index()

    def listing(self, device: str | None = None) -> list[dict]:
        """The kept jobs, newest first: what each is, how far it is, whether ``device`` started it (``mine``) and has been
        given its result (``delivered``)."""
        self._forget_old()
        device = device if isinstance(device, str) and DEVICE_ID.match(device) else ""
        with self._lock:
            jobs = sorted((j for j in self._jobs.values() if j.kept), key=lambda j: j.created, reverse=True)
        out = []
        for j in jobs:
            err = j.error or {}
            out.append({"id": j.id, "kind": j.kind, "state": j.state, "k": j.k, "n": j.n, "label": j.label,
                        "created": j.created, "started": j.started, "finished": j.finished,
                        "title": j.meta.get("title", ""), "device_label": j.meta.get("device_label", ""),
                        "mine": bool(device) and j.meta.get("device") == device,
                        "delivered": bool(device) and device in j.delivered, "deliveries": len(j.delivered),
                        "size": j.size, "error": {"error": err.get("error"), "message": err.get("message")} if err else None,
                        "has_result": j.state == "done" and (j.result is not None or j.stored)})
        return out

    def dismiss(self, jid: str) -> dict:
        """Take a kept job off the list (one that is running is cancelled first)."""
        job = self._get(jid)
        if not job.kept:
            raise ServiceError("job_not_found", "this job is not one of the kept ones", id=job.id)
        if job.finished is None:
            job.cancel.set()
        self._remove(job)
        self._save_index()
        return {"ok": True, "id": job.id}

    def run_sync(self, fn: Callable[[JobControl], dict]) -> dict:
        """Run ``fn`` in the calling thread (small jobs, tests, scripts)."""
        job = Job("sync", "sync")
        job.state, job.started = "running", time.time()
        return fn(JobControl(job))

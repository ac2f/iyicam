"""Nesting on every processor of the computer: the same algorithm, its independent parts at once.

The work of a nesting is (1) the no-fit polygons of every pair of parts and (2) many layouts of the parts in different
orders, the best kept, then (3) polishing the best one. (1) is spread over the helpers in ``Nester.precompute``. Here:

* the layouts that do not depend on one another (the plain orders: biggest first in every gravity, ...) are made at
  once, one per helper, and compared in the same order as one after the other would: the best is the same layout;
* the random variations of the best order are made a round at a time (as many as there are helpers), each round from
  the best order so far;
* the polishing (ruin and recreate, then the order shaken up until the time is up) runs as one search per helper, each
  with its own random numbers (the first with the numbers one processor would use); the best search wins.

The helpers get the nester with all its no-fit polygons once (a file they load), so they do not work them out again.
They run at a lower priority: the computer stays usable while every processor works. A GPU would not help here: the
work is polygon clipping (Clipper, whole numbers, branches), not arithmetic on large arrays.
"""
from __future__ import annotations

import concurrent.futures as cf
import multiprocessing as mp
import os
import pickle
import random
import tempfile
import time

_state: dict = {}           # in a helper: the nester loaded, the cancel flag


def _lower_priority() -> None:
    try:
        if os.name == "nt":
            import ctypes
            BELOW_NORMAL = 0x00004000
            ctypes.windll.kernel32.SetPriorityClass(ctypes.windll.kernel32.GetCurrentProcess(), BELOW_NORMAL)
        else:
            os.nice(5)
    except Exception:  # noqa: BLE001  (a priority that cannot be lowered: the work is the same)
        pass


def _init(flag) -> None:
    _state["flag"] = flag
    _lower_priority()


def _nester(path: str):
    """The nester of this nesting in a helper (loaded once per helper)."""
    n = _state.get("nester")
    if n is None or _state.get("path") != path:
        with open(path, "rb") as f:
            n = pickle.load(f)
        flag = _state["flag"]
        n.cancel = flag.is_set
        _state["nester"], _state["path"] = n, path
    return n


def _run(path: str, order, gravity):
    n = _nester(path)
    return n.run_order(order, None, n.cancel, gravity=gravity)


def _search(path: str, best, order, grav, gravs, seed, seconds_left, tries, share=1):
    """One polishing search. ``seed``: a number, or the state of the random numbers of the main process (the first
    search goes on exactly as one processor would). ``seconds_left``: the time limit from now (the clocks of two
    processes do not agree), or None."""
    n = _nester(path)
    rng = random.Random()
    if isinstance(seed, tuple):
        rng.setstate(seed)
    else:
        rng.seed(seed)
    deadline = None if seconds_left is None else time.perf_counter() + seconds_left
    return n.search(best, order, grav, gravs, rng, deadline, tries, share)


class Helpers:
    """A pool of helper processes for one nesting; ``close()`` (or ``with``) ends them."""

    def __init__(self, workers: int):
        ctx = mp.get_context("spawn")
        self.flag = ctx.Event()
        self.workers = workers
        self.path: str | None = None
        self.pool = cf.ProcessPoolExecutor(max_workers=workers, mp_context=ctx, initializer=_init,
                                           initargs=(self.flag,))

    def publish(self, nester) -> None:
        """The nester as it is now (with its no-fit polygons) for the helpers: written once, each helper loads it once."""
        fd, path = tempfile.mkstemp(prefix="iyicam-nest-", suffix=".pkl")
        cancel, nester.cancel = nester.cancel, None          # (a function of this process does not go along)
        try:
            with os.fdopen(fd, "wb") as f:
                pickle.dump(nester, f, protocol=pickle.HIGHEST_PROTOCOL)
        finally:
            nester.cancel = cancel
        self._drop()
        self.path = path

    def _drop(self) -> None:
        if self.path:
            try:
                os.unlink(self.path)
            except OSError:
                pass
            self.path = None

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    def close(self) -> None:
        self.flag.set()                                      # (a helper still at work stops at its next check)
        self.pool.shutdown(wait=False, cancel_futures=True)
        self._drop()

    def map(self, fn, argss, check=None, done=None) -> list:
        """``fn(path, *args)`` for every ``args`` at once; the answers in the order of ``argss``. ``check()`` is called
        all the time (it raises to cancel: the helpers are told to stop); ``done(k)`` when the k-th answer is in."""
        futs = [self.pool.submit(fn, self.path, *a) for a in argss]
        pending = set(futs)
        try:
            while pending:
                if check is not None:
                    check()
                finished, pending = cf.wait(pending, timeout=0.1)
                for f in finished:
                    if done is not None:
                        done(futs.index(f))
        except BaseException:
            self.flag.set()
            for f in pending:
                f.cancel()
            raise
        return [f.result() for f in futs]


def worth_it(seconds_one_layout: float, layouts_left: int, workers: int) -> bool:
    """Starting the helpers (a second or so) pays when the layouts left would take a few seconds on one processor."""
    return workers >= 2 and layouts_left >= 1 and seconds_one_layout * layouts_left > 2.0


def now() -> float:
    return time.perf_counter()

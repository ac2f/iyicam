"""Nesting on every processor is the same algorithm: a layout made on a helper is the layout made here, the plain orders
made at once give the same best layout, and the polishing searches at once (the first with the random numbers one
processor uses) end no worse than one search. The helpers are always ended."""
import math

import pytest

from iyicam.nesting import nest as N
from iyicam.nesting import parallel as P


def gear(r0, teeth):
    return [((r0 + (12 if (k // 2) % 2 else 0)) * math.cos(2 * math.pi * k / (4 * teeth)),
             (r0 + (12 if (k // 2) % 2 else 0)) * math.sin(2 * math.pi * k / (4 * teeth))) for k in range(4 * teeth)]


def job():
    shapes = []
    for i in range(10):
        if i % 2:
            shapes.append(N.NestShape([gear(60 + 9 * i, 12 + i)], 2))
        else:
            a, b, t = 150 + 20 * i, 120 + 15 * i, 35
            shapes.append(N.NestShape([[(0, 0), (a, 0), (a, t), (t, t), (t, b), (0, b)]], 2))
    return shapes


SETTINGS = N.NestSettings(sheet_width=1600, sheet_height=900, iterations=6, rotations=[0, 90, 180, 270])


def same(r1, r2):
    key = lambda r: sorted((p.shape, p.copy, p.sheet, round(p.angle, 6), round(p.x, 6), round(p.y, 6)) for p in r.placements)
    return key(r1) == key(r2) and r1.sheets == r2.sheets and r1.unplaced == r2.unplaced


def test_a_layout_made_on_a_helper_is_the_one_made_here():
    n = N.Nester(job(), SETTINGS)
    plan = n.candidates(4)
    with P.Helpers(2) as h:
        h.publish(n)
        outs = h.map(P._run, plan)
    for (order, g), r in zip(plan, outs):
        assert same(r, n.run_order(order, gravity=g))


def test_the_same_best_layout_on_every_processor(monkeypatch):
    monkeypatch.setenv("IYICAM_NEST_PROCESSES", "0")
    one = N.nest(job(), SETTINGS)
    monkeypatch.setenv("IYICAM_NEST_PROCESSES", "3")
    monkeypatch.setattr(P, "worth_it", lambda *a: True)          # (a small job: the helpers anyway)
    started = []
    real = P.Helpers.__init__
    monkeypatch.setattr(P.Helpers, "__init__", lambda self, w, *a: (started.append(w), real(self, w, *a))[1])
    many = N.nest(job(), SETTINGS)
    assert started == [3]
    n = N.Nester(job(), SETTINGS)
    assert n.quality(many) <= n.quality(one)                       # (the first search is the one of one processor)
    assert len(many.placements) == len(one.placements)


def test_a_cancelled_nesting_ends_its_helpers(monkeypatch):
    monkeypatch.setenv("IYICAM_NEST_PROCESSES", "2")
    monkeypatch.setattr(P, "worth_it", lambda *a: True)
    made = []
    real = P.Helpers.__init__
    monkeypatch.setattr(P.Helpers, "__init__", lambda self, w, *a: (made.append(self), real(self, w, *a))[1])
    calls = [0]

    def cancel():                                                   # (once the helpers are at work)
        calls[0] += 1 if made else 0
        return calls[0] > 3
    with pytest.raises(N.NestCancelled):
        N.nest(job(), SETTINGS, cancel=cancel)
    assert made and all(h.flag.is_set() and h.path is None for h in made)


def test_the_helpers_run_at_the_priority_asked(monkeypatch):
    """low: the computer stays smooth (a lower priority); normal; high: as high as the system allows."""
    import os
    if os.name == "nt":
        pytest.skip("priorities of Windows: checked on Windows")
    seen = {}
    for level in ("low", "normal"):
        with P.Helpers(1, level) as h:
            seen[level] = h.pool.submit(os.getpriority, os.PRIO_PROCESS, 0).result()
    assert seen["low"] > seen["normal"] >= os.getpriority(os.PRIO_PROCESS, 0) - 0  # (low is nicer than normal)
    st = N.NestSettings(priority="high")
    assert st.priority == "high"

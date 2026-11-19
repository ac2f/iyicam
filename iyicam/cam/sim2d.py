"""The 2D simulation (as ArtCAM's): the moves of calculated toolpaths on a time line, so that a view can show where the tool
is at any moment and paint what it has cut so far.

Nothing is calculated again: the toolpaths are taken as they are. Arcs are cut into short lines; every piece has the time
it starts and ends (feed or rapid over its length, stretched so that the whole lasts as long as the time estimate of the
toolpaths, which knows the acceleration of the machine).
"""
from __future__ import annotations

import bisect
import math
from dataclasses import dataclass, field
from typing import Sequence

from .moves import ArcMove, DrillCycle, Feed, Rapid, Toolpath, arc_sweep

KIND_RAPID, KIND_FEED = 0, 1


@dataclass
class Piece:
    x0: float
    y0: float
    z0: float
    x1: float
    y1: float
    z1: float
    kind: int            # KIND_RAPID | KIND_FEED
    t0: float            # seconds from the start
    t1: float
    tool: int            # index of the toolpath in :attr:`Timeline.toolpaths`

    def at(self, t: float) -> tuple[float, float, float]:
        """Where the tool is at time ``t`` (inside the piece)."""
        if self.t1 <= self.t0:
            return self.x1, self.y1, self.z1
        u = min(1.0, max(0.0, (t - self.t0) / (self.t1 - self.t0)))
        return (self.x0 + (self.x1 - self.x0) * u, self.y0 + (self.y1 - self.y0) * u, self.z0 + (self.z1 - self.z0) * u)


@dataclass
class Timeline:
    toolpaths: list[Toolpath]
    pieces: list[Piece] = field(default_factory=list)
    total: float = 0.0                       # seconds
    _starts: list[float] = field(default_factory=list)

    def index_at(self, t: float) -> int:
        """The number of pieces that have started by time ``t``."""
        return bisect.bisect_right(self._starts, t)

    def position(self, t: float) -> tuple[float, float, float, int] | None:
        """``(x, y, z, toolpath index)`` of the tool at time ``t``, or ``None`` before anything."""
        i = self.index_at(t) - 1
        if i < 0:
            return None
        pc = self.pieces[i]
        x, y, z = pc.at(t)
        return x, y, z, pc.tool

    def tool_name(self, i: int) -> str:
        return self.toolpaths[i].name if 0 <= i < len(self.toolpaths) else ""


def _arc_points(sx: float, sy: float, m: ArcMove, tol: float = 0.05) -> list[tuple[float, float, float]]:
    r = math.hypot(sx - m.cx, sy - m.cy)
    sweep = arc_sweep(sx, sy, m)
    if r < 1e-9 or abs(sweep) < 1e-12:
        return [(m.x, m.y, m.z)]
    step = 2 * math.acos(max(-1.0, 1 - tol / r)) if r > tol else math.pi / 4
    n = max(1, math.ceil(abs(sweep) / step))
    a0 = math.atan2(sy - m.cy, sx - m.cx)
    out = []
    for i in range(1, n + 1):
        if i == n:
            out.append((m.x, m.y, m.z))
        else:
            a = a0 + sweep * i / n
            out.append((m.cx + r * math.cos(a), m.cy + r * math.sin(a), None))
    return out


def build(toolpaths: Sequence[Toolpath], rapid_rate: float = 8000.0, machine=None) -> Timeline:
    """The time line of ``toolpaths`` in the order they are cut. ``machine`` (a ``timing.Machine``): the time of every
    toolpath is stretched to its estimate (the estimate knows acceleration and corners)."""
    tl = Timeline(list(toolpaths))
    t = 0.0
    pos: tuple[float, float, float] | None = None
    rapid = max(1.0, float(getattr(machine, "rapid_rate", rapid_rate) or rapid_rate))
    for k, tp in enumerate(tl.toolpaths):
        first = len(tl.pieces)
        t_start = t

        def add(x1, y1, z1, kind, speed):
            nonlocal pos, t
            if pos is None:
                pos = (x1, y1, z1)
                return
            x0, y0, z0 = pos
            d = math.dist((x0, y0, z0), (x1, y1, z1))
            dt = d / max(speed, 1e-6) * 60.0
            tl.pieces.append(Piece(x0, y0, z0, x1, y1, z1, kind, t, t + dt, k))
            t += dt
            pos = (x1, y1, z1)

        for m in tp.moves:
            if isinstance(m, Rapid):
                add(m.x, m.y, m.z, KIND_RAPID, rapid)
            elif isinstance(m, Feed):
                add(m.x, m.y, m.z, KIND_FEED, m.f)
            elif isinstance(m, ArcMove):
                if pos is None:
                    add(m.x, m.y, m.z, KIND_FEED, m.f)
                    continue
                z0 = pos[2]
                pts = _arc_points(pos[0], pos[1], m)
                for j, (x, y, z) in enumerate(pts):
                    zz = z if z is not None else z0 + (m.z - z0) * (j + 1) / len(pts)
                    add(x, y, zz, KIND_FEED, m.f)
            elif isinstance(m, DrillCycle):
                add(m.x, m.y, m.r, KIND_RAPID, rapid)
                add(m.x, m.y, m.z, KIND_FEED, m.f)
                add(m.x, m.y, m.r, KIND_RAPID, rapid)
        if machine is not None and len(tl.pieces) > first:
            try:
                want = tp.stats(machine).time_s
            except Exception:  # noqa: BLE001  (no estimate: length over speed)
                want = 0.0
            have = t - t_start
            if want > 0 and have > 0:
                k_ = want / have
                for pc in tl.pieces[first:]:
                    pc.t0 = t_start + (pc.t0 - t_start) * k_
                    pc.t1 = t_start + (pc.t1 - t_start) * k_
                t = t_start + want
    tl.total = t
    tl._starts = [pc.t0 for pc in tl.pieces]
    return tl

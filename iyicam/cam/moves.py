"""Machine moves produced by toolpath strategies and consumed by post-processors.

Coordinates are millimetres in sheet coordinates with Z = 0 at the material top
(negative Z is inside the material).  Post-processors apply the final origin,
Z-zero and unit conversions.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

from ..core.tooling import Tool


@dataclass(slots=True)
class Rapid:
    x: float
    y: float
    z: float


@dataclass(slots=True)
class Feed:
    x: float
    y: float
    z: float
    f: float


@dataclass(slots=True)
class ArcMove:
    """Circular (helical when Z changes) move from the previous position."""

    x: float
    y: float
    z: float
    cx: float
    cy: float
    cw: bool
    f: float


@dataclass(slots=True)
class DrillCycle:
    """Drill one hole: posts output a canned cycle or expand it into moves."""

    x: float
    y: float
    z: float          # hole bottom
    r: float          # retract plane
    f: float          # plunge feed
    peck: float = 0.0
    dwell: float = 0.0  # seconds at the bottom


@dataclass(slots=True)
class Comment:
    text: str


@dataclass(slots=True)
class Dwell:
    seconds: float


Move = Rapid | Feed | ArcMove | DrillCycle | Comment | Dwell


def arc_sweep(sx: float, sy: float, m: ArcMove) -> float:
    """Signed sweep (radians, CCW positive) of an arc move starting at ``(sx, sy)``."""
    a0 = math.atan2(sy - m.cy, sx - m.cx)
    a1 = math.atan2(m.y - m.cy, m.x - m.cx)
    if m.cw:
        sw = -((a0 - a1) % (2 * math.pi))
    else:
        sw = (a1 - a0) % (2 * math.pi)
    if abs(sw) < 1e-12 and math.hypot(m.x - sx, m.y - sy) < 1e-9:
        sw = -2 * math.pi if m.cw else 2 * math.pi
    return sw


def drill_expand(prev_z: float, m: DrillCycle, clearance: float = 0.5) -> list[Move]:
    """Expand a drill cycle into explicit moves (peck drilling with full retract)."""
    out: list[Move] = [Rapid(m.x, m.y, max(prev_z, m.r)), Rapid(m.x, m.y, m.r)]
    if m.peck > 0:
        depth = m.r
        top = m.r
        while depth > m.z + 1e-9:
            depth = max(m.z, depth - m.peck)
            if top < m.r:
                out.append(Rapid(m.x, m.y, top))
            out.append(Feed(m.x, m.y, depth, m.f))
            out.append(Rapid(m.x, m.y, m.r))
            top = min(m.r, depth + clearance)
        if m.dwell > 0:
            out.insert(-1, Dwell(m.dwell))
    else:
        out.append(Feed(m.x, m.y, m.z, m.f))
        if m.dwell > 0:
            out.append(Dwell(m.dwell))
        out.append(Rapid(m.x, m.y, m.r))
    return out


@dataclass
class PathStats:
    feed_length: float = 0.0
    rapid_length: float = 0.0
    time_s: float = 0.0
    zmin: float = 0.0
    xmin: float = math.inf
    ymin: float = math.inf
    xmax: float = -math.inf
    ymax: float = -math.inf

    def merge(self, o: "PathStats") -> "PathStats":
        return PathStats(self.feed_length + o.feed_length, self.rapid_length + o.rapid_length,
                         self.time_s + o.time_s, min(self.zmin, o.zmin), min(self.xmin, o.xmin),
                         min(self.ymin, o.ymin), max(self.xmax, o.xmax), max(self.ymax, o.ymax))


@dataclass
class Toolpath:
    name: str
    tool: Tool
    moves: list = field(default_factory=list)
    op: str = ""
    def_id: str = ""
    sheet_id: str = ""
    safe_z: float = 10.0
    color: str = ""
    warnings: list[str] = field(default_factory=list)
    cuts: list[dict] = field(default_factory=list)  # one entry per cut: key, index, start, entry, moves, ...

    def start_point(self) -> tuple[float, float, float] | None:
        for m in self.moves:
            if isinstance(m, (Rapid, Feed, ArcMove)):
                return (m.x, m.y, m.z)
            if isinstance(m, DrillCycle):
                return (m.x, m.y, m.r)
        return None

    def cut_points(self, step: float = 2.0) -> list[tuple[float, float]]:
        """Points every ``step`` mm along the moves that cut (feed moves, drilled holes), as ``(x, y)``."""
        out: list[tuple[float, float]] = []
        pos = None
        for m in self.moves:
            if isinstance(m, (Comment, Dwell)):
                continue
            if isinstance(m, DrillCycle):
                out.append((m.x, m.y))
                pos = (m.x, m.y, m.r)
                continue
            if pos is not None and not isinstance(m, Rapid):
                if isinstance(m, ArcMove):
                    sw = arc_sweep(pos[0], pos[1], m)
                    r = math.hypot(pos[0] - m.cx, pos[1] - m.cy)
                    a0 = math.atan2(pos[1] - m.cy, pos[0] - m.cx)
                    n = max(1, int(abs(sw) * r / step) + 1)
                    out += [(m.cx + r * math.cos(a0 + sw * k / n), m.cy + r * math.sin(a0 + sw * k / n))
                            for k in range(1, n + 1)]
                else:
                    n = max(1, int(math.hypot(m.x - pos[0], m.y - pos[1]) / step) + 1)
                    out += [(pos[0] + (m.x - pos[0]) * k / n, pos[1] + (m.y - pos[1]) * k / n)
                            for k in range(1, n + 1)]
            pos = (m.x, m.y, m.z)
        return out

    def stats(self, rapid_rate: float = 8000.0, start: tuple[float, float, float] | None = None) -> PathStats:
        st = PathStats()
        pos = start
        for m in self.moves:
            if isinstance(m, Comment):
                continue
            if isinstance(m, Dwell):
                st.time_s += m.seconds
                continue
            if isinstance(m, DrillCycle):
                sub = drill_expand(pos[2] if pos else m.r, m)
                for s in sub:
                    pos = _acc(st, pos, s, rapid_rate)
                continue
            pos = _acc(st, pos, m, rapid_rate)
        return st


def _acc(st: PathStats, pos, m, rapid_rate: float):
    if isinstance(m, Dwell):
        st.time_s += m.seconds
        return pos
    x, y, z = m.x, m.y, m.z
    if pos is None:
        pos = (x, y, z)
    if isinstance(m, ArcMove):
        sw = arc_sweep(pos[0], pos[1], m)
        r = math.hypot(pos[0] - m.cx, pos[1] - m.cy)
        L = math.hypot(r * abs(sw), z - pos[2])
        # include the arc extremes in the bounding box
        for k in range(4):
            ang = k * math.pi / 2
            a0 = math.atan2(pos[1] - m.cy, pos[0] - m.cx)
            rel = (ang - a0) % (2 * math.pi) if sw >= 0 else (a0 - ang) % (2 * math.pi)
            if rel <= abs(sw):
                _bb(st, m.cx + r * math.cos(ang), m.cy + r * math.sin(ang))
    else:
        L = math.dist(pos, (x, y, z))
    if isinstance(m, Rapid):
        st.rapid_length += L
        st.time_s += L / max(1e-6, rapid_rate) * 60
    else:
        st.feed_length += L
        st.time_s += L / max(1e-6, m.f) * 60
        _bb(st, x, y)
        _bb(st, pos[0], pos[1])
        st.zmin = min(st.zmin, z)
    return (x, y, z)


def _bb(st: PathStats, x: float, y: float) -> None:
    st.xmin, st.ymin = min(st.xmin, x), min(st.ymin, y)
    st.xmax, st.ymax = max(st.xmax, x), max(st.ymax, y)


def format_time(seconds: float) -> str:
    s = int(round(seconds))
    h, rem = divmod(s, 3600)
    m, s = divmod(rem, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"

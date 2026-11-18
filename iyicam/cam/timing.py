"""How long a toolpath takes on a machine: moves with acceleration, corners and arcs, not only length over feed.

A machine does not run at the feed of a line from its first mm to its last: it speeds up and slows down (``accel``), has to slow
for a corner (the speed it may keep through a corner of a given angle follows from ``accel`` and ``corner_dev``, the way the
junction deviation of a motion planner does), and cannot keep the feed on a small arc (the sideways acceleration ``accel`` limits it
to sqrt(accel * radius)). A job of many small parts, of rounded corners and of many short rapids loses far more to this than a job
of a few long cuts, so an estimate that is a fixed amount more than length over feed is wrong in both directions.

``scale`` is the last word: what the machine really did over what the model says (see :func:`calibrate`).
"""
from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np

# Defaults: a big gantry router (cutting full sheets: 150-300 mm/s2 is what such machines do). With them a nested sheet of
# many small parts comes out some 17 % above length over feed, a job of a few long cuts a few % and a job of many small parts
# and arcs 30 % or more, which is the way the real time differs (docs/timing.md). The Machine setup can be changed, and
# "Calibrate from a real time" finds the acceleration of the machine that made a job in a time that was measured.
DEFAULT_ACCEL = 200.0        # mm/s2
DEFAULT_CORNER_DEV = 0.05    # mm
DEFAULT_RAPID = 8000.0       # mm/min


@dataclass(frozen=True)
class Machine:
    rapid_rate: float = DEFAULT_RAPID        # mm/min: the fastest the axes travel in G0
    z_rapid_rate: float = 3000.0             # mm/min: a move of Z alone (0: as the others)
    accel: float = DEFAULT_ACCEL             # mm/s2
    corner_dev: float = DEFAULT_CORNER_DEV   # mm: how far a corner may be cut off, which sets the speed through it
    scale: float = 1.0                       # what the machine really did over what the model says
    corners: str = "slow"                    # slow: as the planner of the machine does | blend: through every corner without slowing
                                             # (G64, "constant velocity", the way ArtCAM counts) | stop: a stop at every corner (G61)

    @classmethod
    def of(cls, v) -> "Machine":
        """A machine from a rapid rate (a number: the other settings are the defaults) or as it is."""
        return v if isinstance(v, Machine) else cls(rapid_rate=float(v))


def _corner_speed(dot: float, accel: float, dev: float) -> float:
    """The speed (mm/s) that can be kept through a corner whose two directions have this dot product (1: straight on)."""
    if dot > 0.999999:
        return math.inf
    cos_t = max(-1.0, min(1.0, -dot))
    s = math.sqrt(max(0.0, 0.5 * (1.0 - cos_t)))
    if s >= 0.999999:
        return math.inf
    return math.sqrt(accel * dev * s / (1.0 - s))


def segments(moves, start=None):
    """The motion of ``moves`` as segments: lists of length, top speed kind, start and end direction and the radius for arcs.
    Returns ``(segs, stops)``: ``segs`` of ``(length, kind, ux0, uy0, uz0, ux1, uy1, uz1, radius, feed, z_only)``; ``stops``:
    {segment index: seconds} for pauses before that segment (a pause is a stop)."""
    from .moves import ArcMove, Comment, DrillCycle, Dwell, Rapid, arc_sweep, drill_expand
    segs, stops = [], {}
    pos = start
    flat = []
    for m in moves:
        if isinstance(m, Comment):
            continue
        if isinstance(m, DrillCycle):
            flat += drill_expand(pos[2] if pos else m.r, m)
            pos = (m.x, m.y, m.r)
        else:
            flat.append(m)
            if not isinstance(m, Dwell):
                pos = (m.x, m.y, m.z)
    pos = start
    for m in flat:
        if isinstance(m, Dwell):
            stops[len(segs)] = stops.get(len(segs), 0.0) + m.seconds
            continue
        if pos is None:
            pos = (m.x, m.y, m.z)
            continue
        x, y, z = m.x, m.y, m.z
        if isinstance(m, ArcMove):
            sw = arc_sweep(pos[0], pos[1], m)
            r = math.hypot(pos[0] - m.cx, pos[1] - m.cy)
            L = math.hypot(r * abs(sw), z - pos[2])
            if L > 1e-9 and r > 1e-9:
                a0 = math.atan2(pos[1] - m.cy, pos[0] - m.cx)
                a1 = a0 + sw
                sgn = 1.0 if sw >= 0 else -1.0
                arc_l = r * abs(sw)
                kz = (z - pos[2]) / L
                kx = arc_l / L
                u0 = (-math.sin(a0) * sgn * kx, math.cos(a0) * sgn * kx, kz)
                u1 = (-math.sin(a1) * sgn * kx, math.cos(a1) * sgn * kx, kz)
                segs.append((L, "arc", *u0, *u1, r, m.f, False))
        else:
            dx, dy, dz = x - pos[0], y - pos[1], z - pos[2]
            L = math.sqrt(dx * dx + dy * dy + dz * dz)
            if L > 1e-9:
                u = (dx / L, dy / L, dz / L)
                segs.append((L, "rapid" if isinstance(m, Rapid) else "line", *u, *u, 0.0,
                             0.0 if isinstance(m, Rapid) else m.f, abs(dx) < 1e-9 and abs(dy) < 1e-9))
        pos = (x, y, z)
    return segs, stops


def duration(moves, machine=DEFAULT_RAPID, start=None) -> float:
    """Seconds the moves take on the machine (``Machine`` or a rapid rate)."""
    mc = Machine.of(machine)
    segs, stops = segments(moves, start)
    return _plan(segs, stops, mc) * mc.scale


def _plan(segs, stops, mc: Machine) -> float:
    n = len(segs)
    pause = sum(stops.values())
    if n == 0:
        return pause
    a = max(1e-3, mc.accel)
    L = np.fromiter((s[0] for s in segs), float, n)
    vmax = np.empty(n)
    for i, s in enumerate(segs):
        if s[1] == "rapid":
            rate = (mc.z_rapid_rate or mc.rapid_rate) if s[10] else mc.rapid_rate
            vmax[i] = max(1e-3, rate / 60.0)
        else:
            v = max(1e-3, s[9] / 60.0)
            if s[1] == "arc":
                v = min(v, math.sqrt(a * s[8]))          # (the sideways acceleration: a small arc cannot be run at the feed)
            vmax[i] = v
    # the speed at every junction: 0 at the ends and at a pause, else what the corner allows
    vj = np.zeros(n + 1)
    for i in range(1, n):
        s0, s1 = segs[i - 1], segs[i]
        if i in stops:
            vj[i] = 0.0
            continue
        dot = s0[5] * s1[2] + s0[6] * s1[3] + s0[7] * s1[4]
        if mc.corners == "stop":
            vj[i] = 0.0
        elif mc.corners == "blend":                     # (no slowing for a corner; a turn back on itself still stops)
            vj[i] = 0.0 if dot < -0.99 else min(vmax[i - 1], vmax[i])
        else:
            vj[i] = min(vmax[i - 1], vmax[i], _corner_speed(dot, a, mc.corner_dev))
    # the planner: the speed at a junction may not be more than what braking (backwards) or accelerating (forwards) over the
    # segment next to it allows
    for i in range(n - 1, 0, -1):
        vj[i] = min(vj[i], math.sqrt(vj[i + 1] ** 2 + 2.0 * a * L[i]))
    for i in range(1, n):
        vj[i] = min(vj[i], math.sqrt(vj[i - 1] ** 2 + 2.0 * a * L[i - 1]))
    v0, v1 = vj[:-1], vj[1:]
    peak = np.minimum(vmax, np.sqrt(np.maximum((2.0 * a * L + v0 ** 2 + v1 ** 2) / 2.0, 1e-12)))
    peak = np.maximum(peak, np.maximum(v0, v1))
    ta, td = (peak - v0) / a, (peak - v1) / a
    cruise = np.maximum(0.0, L - (peak ** 2 - v0 ** 2) / (2 * a) - (peak ** 2 - v1 ** 2) / (2 * a))
    return float(np.sum(ta + td + cruise / np.maximum(peak, 1e-9))) + pause


def calibrate(moves, real_seconds: float, machine=DEFAULT_RAPID, start=None) -> Machine:
    """:func:`calibrate_all` for one list of moves."""
    return calibrate_all([moves], real_seconds, machine, start)


def calibrate_all(jobs, real_seconds: float, machine=DEFAULT_RAPID, start=None) -> Machine:
    """The machine with the acceleration at which the moves take ``real_seconds`` (the speed through corners follows: it comes
    from the acceleration). When even a machine that accelerates without limit is slower than that, or one that crawls is quicker,
    ``scale`` makes up the rest. ``real_seconds`` is the time of cutting only: the pauses and tool changes of the program are in it
    only if they are in the moves."""
    mc = Machine.of(machine)
    planned = [segments(moves, start) for moves in jobs]
    planned = [(sg, st) for sg, st in planned if sg]
    if real_seconds <= 0 or not planned:
        return mc

    def t(acc: float) -> float:
        m1 = Machine(mc.rapid_rate, mc.z_rapid_rate, acc, mc.corner_dev, 1.0, mc.corners)
        return sum(_plan(sg, st, m1) for sg, st in planned)
    lo, hi = 20.0, 200000.0
    if real_seconds <= t(hi):                         # (quicker than the machine that never slows: the rest is the scale)
        return Machine(mc.rapid_rate, mc.z_rapid_rate, hi, mc.corner_dev, real_seconds / t(hi), mc.corners)
    if real_seconds >= t(lo):
        return Machine(mc.rapid_rate, mc.z_rapid_rate, lo, mc.corner_dev, real_seconds / t(lo), mc.corners)
    for _ in range(60):                               # (t falls as the acceleration grows: bisection on its logarithm)
        mid = math.sqrt(lo * hi)
        if t(mid) > real_seconds:
            lo = mid
        else:
            hi = mid
    return Machine(mc.rapid_rate, mc.z_rapid_rate, math.sqrt(lo * hi), mc.corner_dev, 1.0, mc.corners)


def parse_duration(text: str) -> float | None:
    """Seconds from ``h:mm:ss``, ``m:ss`` or a plain number of minutes (a comma is a decimal point); ``None`` if it is not a time."""
    t = text.strip().replace(",", ".")
    try:
        if ":" in t:
            parts = [float(x) for x in t.split(":")]
            if len(parts) > 3 or any(x < 0 for x in parts):
                return None
            sec = 0.0
            for x in parts:
                sec = sec * 60 + x
            return sec
        v = float(t) * 60.0
        return v if v >= 0 else None
    except ValueError:
        return None

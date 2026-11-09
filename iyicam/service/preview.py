"""Toolpaths as compact polylines for drawing on a small screen.

The machine moves of a toolpath (millions for a big job) are what a post-processor needs, not what a phone needs
to show where the tool goes.  This turns them into

* ``cuts``   - polylines of the moves that cut, each with the deepest Z along it (to colour by depth),
* ``rapids`` - flat list of ``x0, y0, x1, y1`` for the moves in the air,
* ``drills`` - ``[x, y, z]`` of every drilled hole,

with arcs flattened, points closer than ``step`` dropped, and ``step`` grown until the whole is small enough.
"""
from __future__ import annotations

import math
from typing import Sequence

from ..cam.moves import ArcMove, Comment, DrillCycle, Dwell, Feed, Rapid, Toolpath, arc_sweep


def _r(v: float) -> float:
    return round(v, 3)


def _decimate(flat: list[float], gap: float) -> list[float]:
    """Keep the first and last point and every point at least ``gap`` from the one kept before."""
    n = len(flat) // 2
    if n <= 2 or gap <= 0:
        return flat
    out = [flat[0], flat[1]]
    lx, ly = flat[0], flat[1]
    g2 = gap * gap
    for i in range(1, n - 1):
        x, y = flat[2 * i], flat[2 * i + 1]
        if (x - lx) ** 2 + (y - ly) ** 2 >= g2:
            out += [x, y]
            lx, ly = x, y
    out += [flat[-2], flat[-1]]
    return out


def _arc_points(pos, m: ArcMove, step: float) -> list[tuple[float, float]]:
    sw = arc_sweep(pos[0], pos[1], m)
    r = math.hypot(pos[0] - m.cx, pos[1] - m.cy)
    a0 = math.atan2(pos[1] - m.cy, pos[0] - m.cx)
    n = max(1, math.ceil(abs(sw) * r / max(step, 1e-6)), math.ceil(abs(sw) / 0.2))
    n = min(n, 720)
    pts = [(m.cx + r * math.cos(a0 + sw * k / n), m.cy + r * math.sin(a0 + sw * k / n)) for k in range(1, n)]
    pts.append((m.x, m.y))          # the end point exactly
    return pts


def _build(toolpaths: Sequence[Toolpath], step: float) -> dict:
    cuts: list[dict] = []
    rapids: list[float] = []
    drills: list[list[float]] = []
    gap = step * 0.5
    points = 0

    def flush(cur):
        nonlocal points
        if cur is None:
            return
        flat = _decimate(cur[1], gap)
        if len(flat) >= 4:
            cuts.append({"z": _r(cur[0]), "p": [_r(v) for v in flat]})
            points += len(flat) // 2

    for tp in toolpaths:
        pos = None
        cur = None            # [deepest z, flat list of x, y]
        for m in tp.moves:
            if isinstance(m, (Comment, Dwell)):
                continue
            if isinstance(m, DrillCycle):
                flush(cur)
                cur = None
                drills.append([_r(m.x), _r(m.y), _r(m.z)])
                pos = (m.x, m.y, m.r)
                continue
            if isinstance(m, Rapid):
                flush(cur)
                cur = None
                if pos is not None and math.hypot(m.x - pos[0], m.y - pos[1]) > 1e-6:
                    rapids += [_r(pos[0]), _r(pos[1]), _r(m.x), _r(m.y)]
                    points += 2
                pos = (m.x, m.y, m.z)
                continue
            if not isinstance(m, (Feed, ArcMove)):
                continue
            if pos is None:
                pos = (m.x, m.y, m.z)
            seg = _arc_points(pos, m, step) if isinstance(m, ArcMove) else [(m.x, m.y)]
            if cur is None:
                cur = [min(pos[2], m.z), [pos[0], pos[1]]]
            cur[0] = min(cur[0], m.z)
            for x, y in seg:
                if abs(x - cur[1][-2]) > 1e-9 or abs(y - cur[1][-1]) > 1e-9:   # a plunge adds no point
                    cur[1] += [x, y]
            pos = (m.x, m.y, m.z)
        flush(cur)
    return {"cuts": cuts, "rapids": rapids, "drills": drills, "points": points + len(drills)}


def _count(out: dict) -> int:
    return sum(len(c["p"]) for c in out["cuts"]) // 2 + len(out["rapids"]) // 2 + len(out["drills"])


def toolpath_preview(toolpaths: Sequence[Toolpath], step: float = 0.5, cap: int = 250_000) -> dict:
    """See the module docstring.  ``cap``: most points in the result.  The moves are read once; when the result is
    too big the polylines are thinned again with a doubled ``step`` (and every other rapid dropped) until it fits
    or nothing more can be taken out (``truncated``)."""
    step = max(step, 0.01)
    out = _build(toolpaths, step)
    for _ in range(24):
        out["points"] = _count(out)
        if out["points"] <= cap:
            break
        step *= 2
        gap = step * 0.5
        out["cuts"] = [{"z": c["z"], "p": _decimate(c["p"], gap)} for c in out["cuts"]]
        if len(out["rapids"]) // 4 > max(1, cap // 8):      # the moves in the air go before the cuts do
            out["rapids"] = [v for k in range(0, len(out["rapids"]), 8) for v in out["rapids"][k:k + 4]]
    out["points"] = _count(out)
    out["step"] = step
    out["truncated"] = out["points"] > cap
    return out

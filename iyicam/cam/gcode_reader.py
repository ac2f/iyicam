"""Read G-code back into toolpaths (backplot / simulation of existing files)."""
from __future__ import annotations

import math
import re
from dataclasses import dataclass, field

from ..core.tooling import Tool
from .moves import ArcMove, DrillCycle, Dwell, Feed, Rapid, Toolpath

_WORD = re.compile(r"([A-Z])\s*([-+]?(?:\d+\.?\d*|\.\d+))")


@dataclass
class ReadOptions:
    x_offset: float = 0.0   # added to X/Y after unit conversion (output origin -> sheet coordinates)
    y_offset: float = 0.0
    z_offset: float = 0.0   # subtracted from Z (e.g. the thickness when Z zero is at the bottom)
    tools: dict[int, Tool] = field(default_factory=dict)
    default_tool: Tool | None = None


def _strip(line: str) -> str:
    line = re.sub(r"\(.*?\)", " ", line)
    return line.split(";", 1)[0].strip().upper()


def read_gcode(text: str, opt: ReadOptions | None = None) -> list[Toolpath]:
    """Interpret G0/G1/G2/G3 (IJK or R), G17, G20/G21, G90/G91, G90.1/G91.1, G81-G83, G4, T/M6."""
    opt = opt or ReadOptions()
    k = 1.0
    absolute, ijk_inc = True, True
    motion = 0
    feed = 1000.0
    pos = [None, None, None]
    out: list[Toolpath] = []
    cycle = None  # (code, z, r, q, p)
    tool = opt.default_tool or Tool()

    def cur() -> Toolpath:
        if not out or out[-1].tool is not tool:
            out.append(Toolpath(f"T{tool.number} {tool.name}", tool, [], "gcode"))
        return out[-1]

    def sheet(x, y, z):
        return (x * k + opt.x_offset, y * k + opt.y_offset, z * k - opt.z_offset)

    for raw in text.splitlines():
        line = _strip(raw)
        if not line or line.startswith("%") or line.startswith("O"):
            continue
        words: list[tuple[str, float]] = [(a, float(b)) for a, b in _WORD.findall(line)]
        gs = [v for a, v in words if a == "G"]
        vals = {a: v for a, v in words if a != "G"}
        for g in gs:
            if g in (0, 1, 2, 3):
                motion = int(g)
                cycle = None
            elif g == 20:
                k = 25.4
            elif g == 21:
                k = 1.0
            elif g == 90:
                absolute = True
            elif g == 91:
                absolute = False
            elif abs(g - 90.1) < 1e-6:
                ijk_inc = False
            elif abs(g - 91.1) < 1e-6:
                ijk_inc = True
            elif g == 80:
                cycle = None
            elif g in (81, 82, 83):
                cycle = (int(g), None, None, 0.0, 0.0)
            elif g == 4:
                p = vals.get("P", vals.get("X", 0.0))
                cur().moves.append(Dwell(p / 1000.0 if p > 100 else p))
        if "T" in vals:
            n = int(vals["T"])
            src = opt.tools.get(n) or opt.default_tool
            tool = src.copy() if src is not None else Tool()
            tool.number = n
        if "F" in vals:
            feed = vals["F"] * k
        has_xyz = any(a in vals for a in "XYZ")
        if cycle is not None and (has_xyz or "R" in vals):
            code, cz, cr, cq, cp = cycle
            cz = vals.get("Z", cz)
            cr = vals.get("R", cr)
            cq = vals.get("Q", cq)
            cp = vals.get("P", cp)
            cycle = (code, cz, cr, cq, cp)
            x = vals.get("X", pos[0] / k if pos[0] is not None else 0.0)
            y = vals.get("Y", pos[1] / k if pos[1] is not None else 0.0)
            if cz is not None and cr is not None:
                sx, sy, sz = sheet(x, y, cz)
                rz = sheet(0, 0, cr)[2]
                cur().moves.append(DrillCycle(sx, sy, sz, rz, feed, cq * k if code == 83 else 0.0,
                                              cp / 1000.0 if cp > 100 else cp))
            pos[0], pos[1] = x * k, y * k
            continue
        if not has_xyz and not (motion in (2, 3) and any(a in vals for a in "IJR")):
            continue
        tgt = []
        for i, a in enumerate("XYZ"):
            base = pos[i] / k if pos[i] is not None else 0.0
            if a in vals:
                tgt.append(vals[a] if absolute else base + vals[a])
            else:
                tgt.append(base if pos[i] is not None else None)
        if None in tgt:  # unknown start position: take what we have
            tgt = [t if t is not None else 0.0 for t in tgt]
        x, y, z = tgt
        sx, sy, sz = sheet(x, y, z)
        tp = cur()
        if motion == 0:
            tp.moves.append(Rapid(sx, sy, sz))
        elif motion == 1:
            tp.moves.append(Feed(sx, sy, sz, feed))
        else:
            px, py = (pos[0] / k, pos[1] / k) if pos[0] is not None else (x, y)
            if "R" in vals:
                r = vals["R"]
                d = math.hypot(x - px, y - py)
                if d < 1e-12 or abs(r) < d / 2 - max(1e-3, abs(r) * 1e-4):
                    tp.moves.append(Feed(sx, sy, sz, feed))
                    pos = [x * k, y * k, z * k]
                    continue
                h = math.sqrt(max(0.0, r * r - d * d / 4))
                mx, my = (px + x) / 2, (py + y) / 2
                ux, uy = -(y - py) / d, (x - px) / d  # left normal of the chord
                # the centre is on the right of the chord for G2 with R > 0 (minor arc)
                side = -1 if motion == 2 else 1
                if r < 0:
                    side = -side
                cx, cy = mx + side * ux * h, my + side * uy * h
            else:
                i_, j_ = vals.get("I", 0.0), vals.get("J", 0.0)
                cx, cy = (px + i_, py + j_) if ijk_inc else (i_, j_)
            ccx, ccy, _ = sheet(cx, cy, 0)
            tp.moves.append(ArcMove(sx, sy, sz, ccx, ccy, motion == 2, feed))
        pos = [x * k, y * k, z * k]
    return [tp for tp in out if tp.moves]

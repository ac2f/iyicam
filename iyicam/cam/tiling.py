"""Toolpath tiling: machine a job longer than the machine in bands, one after another.

The sheet is cut into bands of ``length`` along X or Y. Cutting moves are split
where they cross a band edge and every band gets the moves inside it, shifted
so the band starts at zero: between tiles the operator slides the material on
by ``length`` and runs the next file from the same zero.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field, replace

from ..core.document import Sheet
from .builder import MoveBuilder
from .moves import ArcMove, Comment, DrillCycle, Dwell, Feed, Rapid, Toolpath, arc_sweep


@dataclass
class Tile:
    index: int        # band number along the axis
    offset: float     # where the band starts on the sheet
    sheet: Sheet      # the band as a sheet of its own (origin and size for the post)
    toolpaths: list[Toolpath] = field(default_factory=list)


def _edges(lo: float, hi: float, length: float) -> list[float]:
    """Band edges strictly between ``lo`` and ``hi``."""
    return [k * length for k in range(math.floor(lo / length) + 1, math.ceil(hi / length))]


def _split_feed(p: tuple, m: Feed, ax: int, length: float) -> list[Feed]:
    a, b = p[ax], (m.x, m.y)[ax]
    out = []
    if abs(b - a) > 1e-12:
        for t in sorted((e - a) / (b - a) for e in _edges(min(a, b), max(a, b), length)):
            if 1e-9 < t < 1 - 1e-9:
                out.append(Feed(p[0] + (m.x - p[0]) * t, p[1] + (m.y - p[1]) * t, p[2] + (m.z - p[2]) * t, m.f))
    return out + [m]


def _split_arc(p: tuple, m: ArcMove, ax: int, length: float) -> list[ArcMove]:
    r = math.hypot(p[0] - m.cx, p[1] - m.cy)
    sw = arc_sweep(p[0], p[1], m)
    a0 = math.atan2(p[1] - m.cy, p[0] - m.cx)
    c = (m.cx, m.cy)[ax]
    fracs = set()
    for e in _edges(c - r, c + r, length):
        q = max(-1.0, min(1.0, (e - c) / r))
        th = math.acos(q) if ax == 0 else math.asin(q)
        for t in ((th, -th) if ax == 0 else (th, math.pi - th)):
            rel = (t - a0) % (2 * math.pi) if sw > 0 else (a0 - t) % (2 * math.pi)
            f = rel / abs(sw)
            if 1e-9 < f < 1 - 1e-9:
                fracs.add(f)
    out = []
    for f in sorted(fracs):
        a = a0 + sw * f
        out.append(ArcMove(m.cx + r * math.cos(a), m.cy + r * math.sin(a), p[2] + (m.z - p[2]) * f,
                           m.cx, m.cy, m.cw, m.f))
    return out + [m]


def _band(p: tuple, m, ax: int, length: float) -> int:
    """Band of a piece that does not cross an edge (decided by its middle)."""
    if isinstance(m, ArcMove):
        a0 = math.atan2(p[1] - m.cy, p[0] - m.cx) + arc_sweep(p[0], p[1], m) / 2
        r = math.hypot(p[0] - m.cx, p[1] - m.cy)
        mid = (m.cx + r * math.cos(a0), m.cy + r * math.sin(a0))[ax]
    else:
        mid = (p[ax] + (m.x, m.y)[ax]) / 2
    return math.floor(mid / length)


def _runs(tp: Toolpath, ax: int, length: float) -> list[list]:
    """``[band, start or None (drilling), moves]`` pieces of a toolpath in machining order."""
    runs: list[list] = []
    cur = None
    pos = None
    for m in tp.moves:
        if isinstance(m, (Comment, Dwell)):
            if cur is not None:
                cur[2].append(m)
        elif isinstance(m, Rapid):
            cur, pos = None, (m.x, m.y, m.z)
        elif isinstance(m, DrillCycle):
            runs.append([math.floor((m.x, m.y)[ax] / length), None, [m]])
            cur, pos = None, (m.x, m.y, m.r)
        elif pos is None:
            pos = (m.x, m.y, m.z)
        else:
            split = _split_arc if isinstance(m, ArcMove) else _split_feed
            for piece in split(pos, m, ax, length):
                k = _band(pos, piece, ax, length)
                if cur is None or cur[0] != k:
                    cur = [k, pos, []]
                    runs.append(cur)
                cur[2].append(piece)
                pos = (piece.x, piece.y, piece.z)
    return runs


def tile_toolpaths(toolpaths: list[Toolpath], sheet: Sheet, axis: str = "y", length: float = 600.0,
                   clearance: float = 2.0) -> list[Tile]:
    """Split the toolpaths of ``sheet`` into bands of ``length`` along ``axis`` ('x' or 'y')."""
    if length <= 0:
        raise ValueError("The tile length must be positive")
    ax = 0 if axis == "x" else 1
    per_tp = [_runs(tp, ax, length) for tp in toolpaths]
    bands = sorted({r[0] for runs in per_tp for r in runs})
    extent = sheet.width if ax == 0 else sheet.height
    tiles = []
    for k in bands:
        off = k * length
        dx, dy = (off, 0.0) if ax == 0 else (0.0, off)
        size = max(1e-3, min(length, extent - off)) if off < extent else length
        w, h = (size, sheet.height) if ax == 0 else (sheet.width, size)
        tile = Tile(k, off, Sheet(f"{sheet.name} tile {len(tiles) + 1}", w, h, thickness=sheet.thickness))
        for tp, runs in zip(toolpaths, per_tp):
            mb = MoveBuilder(tp.safe_z, clearance, tp.tool.feed_rate, tp.tool.plunge_rate)
            for band, start, moves in runs:
                if band != k:
                    continue
                if start is None:  # a drilled hole: the cycle starts at safe Z
                    dc = moves[0]
                    mb.retract()
                    mb.rapid(dc.x - dx, dc.y - dy, tp.safe_z)
                    mb.moves.append(replace(dc, x=dc.x - dx, y=dc.y - dy))
                    continue
                x, y, z = start[0] - dx, start[1] - dy, start[2]
                if z > -1e-9:  # starts in the air: no slow plunge needed
                    mb.retract()
                    mb.rapid(x, y, tp.safe_z)
                    mb.rapid(x, y, z)
                else:  # continues a cut from the previous band
                    mb.travel(x, y, z)
                for m in moves:
                    if isinstance(m, ArcMove):
                        m = replace(m, x=m.x - dx, y=m.y - dy, cx=m.cx - dx, cy=m.cy - dy)
                    elif isinstance(m, Feed):
                        m = replace(m, x=m.x - dx, y=m.y - dy)
                    mb.moves.append(m)
                    if hasattr(m, "x"):
                        mb.pos = (m.x, m.y, m.z)
            if mb.moves:
                tile.toolpaths.append(replace(tp, moves=mb.finish(), name=f"{tp.name} (tile {len(tiles) + 1})"))
        tiles.append(tile)
    return tiles

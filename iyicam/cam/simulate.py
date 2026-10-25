"""Material removal simulation on a height map (numpy).

Every cutting move is swept as a "capsule": cells closer than the tool radius
to the move are lowered to the tool tip height plus the tool profile there.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Callable, Sequence

import numpy as np

from ..core import clip
from ..core.tooling import Tool
from .moves import ArcMove, DrillCycle, Feed, Rapid, Toolpath, arc_sweep


@dataclass
class HeightMap:
    z: np.ndarray        # (ny, nx), 0 = material top, negative = cut
    res: float
    x0: float
    y0: float
    thickness: float

    @property
    def shape(self) -> tuple[int, int]:
        return self.z.shape

    def at(self, x: float, y: float) -> float:
        j = int(round((x - self.x0) / self.res))
        i = int(round((y - self.y0) / self.res))
        return float(self.z[i, j])


def tool_profile(tool: Tool, d: np.ndarray) -> np.ndarray:
    """Height of the cutter surface above its tip at distance ``d`` from the axis."""
    R = tool.radius
    out = np.full(d.shape, np.inf)
    inside = d <= R
    dd = d[inside]
    if tool.type in ("v_bit", "engraving"):
        t = math.tan(tool.half_angle())
        out[inside] = np.maximum(0.0, dd - tool.tip_diameter / 2) / t
    elif tool.type == "ball_nose":
        out[inside] = R - np.sqrt(np.maximum(0.0, R * R - dd * dd))
    elif tool.type == "bull_nose" and tool.corner_radius > 0:
        rc = min(tool.corner_radius, R)
        flat = R - rc
        e = np.maximum(0.0, dd - flat)
        out[inside] = rc - np.sqrt(np.maximum(0.0, rc * rc - e * e))
    else:
        out[inside] = 0.0
    return out


class Simulator:
    def __init__(self, width: float, height: float, thickness: float, res: float, x0: float = 0.0, y0: float = 0.0):
        self.res = res
        self.nx = max(2, int(math.ceil(width / res)) + 1)
        self.ny = max(2, int(math.ceil(height / res)) + 1)
        self.hm = HeightMap(np.zeros((self.ny, self.nx)), res, x0, y0, thickness)

    def capsule(self, tool: Tool, a: tuple[float, float], b: tuple[float, float], z: float) -> None:
        """Sweep the tool tip at height ``z`` along the segment a-b."""
        R = tool.radius
        res = self.res
        hm = self.hm
        x0 = min(a[0], b[0]) - R
        x1 = max(a[0], b[0]) + R
        y0 = min(a[1], b[1]) - R
        y1 = max(a[1], b[1]) + R
        j0 = max(0, int(math.floor((x0 - hm.x0) / res)))
        j1 = min(self.nx - 1, int(math.ceil((x1 - hm.x0) / res)))
        i0 = max(0, int(math.floor((y0 - hm.y0) / res)))
        i1 = min(self.ny - 1, int(math.ceil((y1 - hm.y0) / res)))
        if j1 < j0 or i1 < i0:
            return
        xs = hm.x0 + np.arange(j0, j1 + 1) * res
        ys = hm.y0 + np.arange(i0, i1 + 1) * res
        X, Y = np.meshgrid(xs, ys)
        ax, ay = a
        dx, dy = b[0] - ax, b[1] - ay
        L2 = dx * dx + dy * dy
        if L2 < 1e-18:
            d = np.hypot(X - ax, Y - ay)
        else:
            t = np.clip(((X - ax) * dx + (Y - ay) * dy) / L2, 0.0, 1.0)
            d = np.hypot(X - (ax + t * dx), Y - (ay + t * dy))
        val = z + tool_profile(tool, d)
        view = hm.z[i0:i1 + 1, j0:j1 + 1]
        np.minimum(view, val, out=view)

    def segment(self, tool: Tool, p: tuple, q: tuple) -> None:
        """Cutting move p -> q (3D).  Sloped moves are split into short flat pieces."""
        if min(p[2], q[2]) >= 0.0:
            return
        L = math.hypot(q[0] - p[0], q[1] - p[1])
        dz = abs(q[2] - p[2])
        n = 1
        if dz > 1e-9:
            n = max(1, int(math.ceil(max(L, dz) / max(self.res, 0.05))))
        elif L > 60.0:
            n = int(math.ceil(L / 60.0))
        for k in range(n):
            t0, t1 = k / n, (k + 1) / n
            a = (p[0] + (q[0] - p[0]) * t0, p[1] + (q[1] - p[1]) * t0)
            b = (p[0] + (q[0] - p[0]) * t1, p[1] + (q[1] - p[1]) * t1)
            z = min(p[2] + (q[2] - p[2]) * t0, p[2] + (q[2] - p[2]) * t1)
            self.capsule(tool, a, b, z)

    def run(self, toolpaths: Sequence[Toolpath], progress: Callable[[int, int], None] | None = None,
            cancel: Callable[[], bool] | None = None) -> HeightMap:
        total = sum(len(tp.moves) for tp in toolpaths) or 1
        done = 0
        for tp in toolpaths:
            tool = tp.tool
            pos = None
            for m in tp.moves:
                done += 1
                if progress and done % 200 == 0:
                    progress(done, total)
                    if cancel and cancel():
                        return self.hm
                if isinstance(m, DrillCycle):
                    self.capsule(tool, (m.x, m.y), (m.x, m.y), m.z)
                    pos = (m.x, m.y, m.r)
                    continue
                if not isinstance(m, (Rapid, Feed, ArcMove)):
                    continue
                q = (m.x, m.y, m.z)
                if pos is not None:
                    if isinstance(m, ArcMove):
                        sw = arc_sweep(pos[0], pos[1], m)
                        r = math.hypot(pos[0] - m.cx, pos[1] - m.cy)
                        a0 = math.atan2(pos[1] - m.cy, pos[0] - m.cx)
                        tol = max(self.res * 0.25, 1e-3)
                        step = 2 * math.acos(max(-1.0, 1 - tol / r)) if r > tol else math.pi / 4
                        n = max(1, int(math.ceil(abs(sw) / step)))
                        prev = pos
                        for k in range(1, n + 1):
                            ang = a0 + sw * k / n
                            nxt = (m.cx + r * math.cos(ang), m.cy + r * math.sin(ang), pos[2] + (m.z - pos[2]) * k / n)
                            self.segment(tool, prev, nxt)
                            prev = nxt
                    else:
                        self.segment(tool, pos, q)
                pos = q
        if progress:
            progress(total, total)
        return self.hm


def auto_resolution(width: float, height: float, cells: int = 1_500_000) -> float:
    return max(0.05, math.sqrt(width * height / cells))


def simulate(toolpaths: Sequence[Toolpath], width: float, height: float, thickness: float,
             res: float = 0.0, progress=None, cancel=None, outline=None) -> HeightMap:
    """Material removal on a sheet; ``outline`` (outlines of the material) leaves no material beyond it."""
    res = res or auto_resolution(width, height)
    sim = Simulator(width, height, thickness, res)
    if outline:
        hm = sim.hm
        xs = hm.x0 + np.arange(hm.z.shape[1]) * res
        for i in range(hm.z.shape[0]):
            row = np.c_[xs, np.full(len(xs), hm.y0 + i * res)]
            hm.z[i, ~clip.region_mask(row, outline)] = -thickness  # nothing there to cut
    return sim.run(toolpaths, progress, cancel)


def shade(hm: HeightMap, light=(-0.5, 0.6, 0.62)) -> np.ndarray:
    """RGB image (ny, nx, 3) uint8 of the height map, row 0 = top (max Y)."""
    z = np.maximum(hm.z, -hm.thickness)
    gy, gx = np.gradient(z, hm.res)
    nz = 1.0 / np.sqrt(gx * gx + gy * gy + 1.0)
    lx, ly, lz = light
    ln = math.sqrt(lx * lx + ly * ly + lz * lz)
    lam = np.clip((-gx * lx - gy * ly + lz) * nz / ln, 0.0, 1.0)
    depth = np.clip(-z / max(hm.thickness, 1e-9), 0.0, 1.0)
    wood = np.array([226, 196, 150], float)
    deep = np.array([150, 110, 70], float)
    base = wood[None, None, :] * (1 - depth[..., None]) + deep[None, None, :] * depth[..., None]
    rgb = base * (0.45 + 0.55 * lam[..., None])
    through = hm.z <= -hm.thickness + 1e-6
    rgb[through] = (60, 64, 72)
    return np.clip(rgb[::-1], 0, 255).astype(np.uint8)

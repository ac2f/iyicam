"""V-carving: the tool depth follows the largest inscribed circle of the shape."""
from __future__ import annotations

import math

import numpy as np

from ..core import clip, vectorops
from ..core.geom import Contour, cross, unit
from ..core.params import Param
from ..core.document import ToolpathDef
from .builder import MoveBuilder
from .moves import Toolpath
from .operation import OpContext, Operation
from .ordering import nearest_order


def _simplify3(pts: list[tuple[float, float, float]], tol: float) -> list[tuple[float, float, float]]:
    """Douglas-Peucker in 3D."""
    if len(pts) < 3:
        return pts
    P = np.asarray(pts)
    keep = np.zeros(len(P), bool)
    keep[0] = keep[-1] = True
    stack = [(0, len(P) - 1)]
    while stack:
        i, j = stack.pop()
        if j <= i + 1:
            continue
        a, b = P[i], P[j]
        ab = b - a
        L2 = float(ab @ ab)
        seg = P[i + 1:j] - a
        if L2 < 1e-18:
            d = np.linalg.norm(seg, axis=1)
        else:
            t = np.clip(seg @ ab / L2, 0, 1)
            d = np.linalg.norm(seg - t[:, None] * ab, axis=1)
        k = int(np.argmax(d))
        if d[k] > tol:
            m = i + 1 + k
            keep[m] = True
            stack.append((i, m))
            stack.append((m, j))
    return [tuple(map(float, p)) for p in P[keep]]


def sample_boundary(poly: list[tuple[float, float]], step: float):
    """Points along a closed polygon with inward (left) normals, plus fans at reflex corners."""
    n = len(poly)
    pts, nrm = [], []
    for i in range(n):
        a = poly[i]
        b = poly[(i + 1) % n]
        d = (b[0] - a[0], b[1] - a[1])
        L = math.hypot(*d)
        if L < 1e-12:
            continue
        u = (d[0] / L, d[1] / L)
        nl = (-u[1], u[0])
        # fan at the start vertex when the boundary turns right (reflex for the region)
        p0 = poly[i - 1]
        dprev = unit((a[0] - p0[0], a[1] - p0[1]))
        turn = cross(dprev, u)
        if turn < -1e-9:
            a0 = math.atan2(dprev[0], -dprev[1])  # left normal of previous edge
            a1 = math.atan2(nl[1], nl[0])
            sweep = (a0 - a1) % (2 * math.pi)
            k = max(1, int(sweep / 0.15))
            for j in range(1, k):
                ang = a0 - sweep * j / k
                pts.append(a)
                nrm.append((math.cos(ang), math.sin(ang)))
        m = max(1, math.ceil(L / step))
        for j in range(m):
            t = j / m
            pts.append((a[0] + d[0] * t, a[1] + d[1] * t))
            nrm.append(nl)
    return np.asarray(pts, float), np.asarray(nrm, float)


def inscribed_radii(P: np.ndarray, N: np.ndarray, Q: np.ndarray, rmax: float, chunk: int = 64) -> np.ndarray:
    """Radius of the largest circle tangent at each P (normal N) containing no point of Q."""
    out = np.empty(len(P))
    for s in range(0, len(P), chunk):
        p = P[s:s + chunk]
        n = N[s:s + chunk]
        q = Q
        if math.isfinite(rmax):
            lo = p.min(axis=0) - 2 * rmax
            hi = p.max(axis=0) + 2 * rmax
            m = np.all((Q >= lo) & (Q <= hi), axis=1)
            q = Q[m]
        if len(q) == 0:
            out[s:s + chunk] = rmax
            continue
        d = q[None, :, :] - p[:, None, :]
        dn = np.einsum("ijk,ik->ij", d, n)
        d2 = np.einsum("ijk,ijk->ij", d, d)
        with np.errstate(divide="ignore", invalid="ignore"):
            r = np.where(dn > 1e-9, d2 / (2 * dn), np.inf)
        out[s:s + chunk] = np.minimum(r.min(axis=1), rmax)
    return out


class VCarveOp(Operation):
    id = "vcarve"
    label = "V-Carve"
    description = "Carve shapes with a V-bit; depth follows the shape width. Optional flat bottom."
    tool_types = ("v_bit", "engraving")
    needs_closed = True
    extra_tool_params = ("clear_tool",)

    @classmethod
    def params(cls) -> list[Param]:
        return [
            Param("start_depth", "Start depth", "length", 0.0, min=0.0, group="Depth"),
            Param("flat_depth", "Flat depth (0 = no limit)", "length", 0.0, min=0.0, group="Depth",
                  help="Maximum carving depth; deeper areas are machined flat"),
            Param("stepdown", "Stepdown (0 = single pass)", "length", 0.0, min=0.0, group="Depth"),
            Param("clear_flats", "Clear flat areas", "bool", True, group="Flat areas"),
            Param("clear_tool", "Flat area tool (empty = V-bit)", "tool", None, group="Flat areas"),
            Param("resolution", "Sampling step", "length", 0.1, min=0.01, max=2.0, group="Advanced"),
            Param("smoothing", "Path tolerance", "length", 0.01, min=0.001, max=0.5, group="Advanced"),
        ]

    def compute(self, ctx: OpContext) -> list[Toolpath]:
        v = self.values
        tool = ctx.tool
        if not tool.is_tapered:
            ctx.warn("V-carving needs a V-bit or engraving tool")
            return []
        closed = [c for c in ctx.contours if c.closed]
        if not closed:
            ctx.warn("Select closed vectors for V-carving")
            return []
        reg = vectorops.region(closed, ctx.tolerance)
        if not reg:
            return []
        start = v["start_depth"]
        tan = math.tan(tool.half_angle())
        r0 = tool.tip_diameter / 2
        flat = v["flat_depth"] or 0.0
        rmax = r0 + flat * tan if flat > 0 else math.inf
        rlim = min(rmax, tool.radius)
        step = v["resolution"]
        Q = np.concatenate([sample_boundary(p, step / 2)[0] for p in reg])
        loops = []
        too_wide = False
        for poly in reg:
            P, N = sample_boundary(poly, step)
            if len(P) == 0:
                continue
            R = inscribed_radii(P, N, Q, rmax)
            if np.any(R > tool.radius + 1e-6):
                too_wide = True
            R = np.minimum(R, rlim)
            C = P + N * R[:, None]
            Z = -(start + np.maximum(0.0, R - r0) / tan)
            pts = [(float(x), float(y), float(z)) for (x, y), z in zip(C, Z)]
            pts.append(pts[0])
            loops.append(_simplify3(pts, v["smoothing"]))
        if too_wide:
            ctx.warn("Parts of the design are wider than the V-bit; set a flat depth or use a larger tool")
        sd = v["stepdown"] or 0.0
        zmin = min(p[2] for lp in loops for p in lp)
        levels = []
        if sd > 0:
            k = 1
            while -start - k * sd > zmin + 1e-9:
                levels.append(-start - k * sd)
                k += 1
        levels.append(None)  # final pass without a limit
        order = nearest_order(loops, lambda lp, pos: lp[0][:2], lambda lp, e: lp[-1][:2], ctx.material.home[:2])
        mb = MoveBuilder(ctx.safe_z, ctx.clearance, tool.feed_rate, tool.plunge_rate, -start)
        for lim in levels:
            for lp in order:
                pts = lp if lim is None else [(x, y, max(z, lim)) for x, y, z in lp]
                x, y, z = pts[0]
                mb.travel(x, y, z)
                for x, y, z in pts[1:]:
                    mb.feed(x, y, z)
        out = [self.make_toolpath(ctx, mb.finish())]
        if flat > 0 and v["clear_flats"]:
            flats = clip.offset(reg, -(rmax - r0)) if rmax > r0 else reg
            if flats:
                out += self._clear(ctx, flats, start, flat)
        return out

    def _clear(self, ctx: OpContext, flats, start: float, flat: float) -> list[Toolpath]:
        from ..core.document import VectorObject
        from .pocket import PocketOp
        tool = self.values.get("clear_tool") or ctx.tool
        objs = [VectorObject([Contour.polyline(p, True) for p in flats], "")]
        tdef = ToolpathDef(ctx.tdef.name + " (flats)", "pocket", tool,
                           {"start_depth": start, "cut_depth": start + flat, "strategy": "offset"})
        sub = OpContext(ctx.project, ctx.sheet, tdef, objs, ctx.tolerance, ctx.warnings)
        op = PocketOp(tdef.params)
        res = op.compute(sub)
        for tp in res:
            tp.def_id = ctx.tdef.id
            tp.name = ctx.tdef.name + " (flats)"
        return res

"""V-carving: the tool depth follows the largest inscribed circle of the shape."""
from __future__ import annotations

import math

import numpy as np

from ..core import arcfit, clip, vectorops
from ..core.geom import Bezier, Contour, Line, cross, unit
from ..core.params import Param
from ..core.document import ToolpathDef
from .builder import MoveBuilder
from .moves import Toolpath
from .operation import OpContext, Operation
from .ordering import containment_levels, nearest_order


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


def prepare_contours(contours: list[Contour], tol: float = 0.02) -> tuple[list[Contour], bool]:
    """Closed contours oriented so the carved region is on their left.

    Dense polylines (e.g. flattened curves from DXF files) are refitted with arcs
    first: the inscribed circle test needs smooth tangents, not fake corners.
    Returns the contours and whether any of them had to be refitted.
    """
    out = []
    refitted = False
    for c in contours:
        if not c.closed or not c.segs:
            continue
        if len(c.segs) > 8 and all(isinstance(sg, Line) for sg in c.segs):
            c = arcfit.fit_contour(c.flatten(), True, tol, keep_start=True)
            refitted = True
        out.append(c)
    levels = containment_levels(out)
    return [c if c.is_ccw() == (lv % 2 == 0) else c.reversed() for c, lv in zip(out, levels)], refitted


class Samples:
    """Boundary samples with inward normals and their position along the boundary."""

    def __init__(self):
        self.p: list = []
        self.n: list = []
        self.loop: list[int] = []
        self.run: list[int] = []   # smooth stretch between real corners (-1 = on a corner)
        self.s: list[float] = []   # arc length position
        self.lengths: list[float] = []

    def arrays(self):
        return (np.asarray(self.p, float).reshape(-1, 2), np.asarray(self.n, float).reshape(-1, 2),
                np.asarray(self.loop, int), np.asarray(self.run, int), np.asarray(self.s, float))


def sample_boundary(c: Contour, step: float, out: Samples | None = None, loop: int = 0,
                    smooth_deg: float = 8.0) -> Samples:
    """Points on the exact curve with exact inward (left) normals.

    Joints whose tangents differ by less than ``smooth_deg`` count as smooth.
    Convex corners get a sample on the vertex (the carving runs up into the
    corner) and reflex corners a fan of normals sweeping round the vertex.
    """
    out = out or Samples()
    smooth = math.radians(smooth_deg)
    segs = c.segs
    run = 0
    acc = 0.0
    first = len(out.run)
    start_is_corner = False
    for i, sg in enumerate(segs):
        t_in = segs[i - 1].tangent_at(1.0)
        t_out = sg.tangent_at(0.0)
        turn = math.atan2(cross(t_in, t_out), t_in[0] * t_out[0] + t_in[1] * t_out[1])
        a = sg.a
        n_in, n_out = (-t_in[1], t_in[0]), (-t_out[1], t_out[0])
        if abs(turn) > smooth:
            run += 1
            if i == 0:
                start_is_corner = True
        if turn < -smooth:
            a0 = math.atan2(n_in[1], n_in[0])
            k = max(2, int(-turn / 0.12) + 1)
            for j in range(1, k):
                ang = a0 + turn * j / k
                out.p.append(a)
                out.n.append((math.cos(ang), math.sin(ang)))
                out.loop.append(loop)
                out.run.append(-1)
                out.s.append(acc)
        elif turn > smooth:
            bis = unit((n_in[0] + n_out[0], n_in[1] + n_out[1]))
            if bis != (0.0, 0.0):
                out.p.append(a)
                out.n.append(bis)
                out.loop.append(loop)
                out.run.append(-1)
                out.s.append(acc)
        L = sg.length()
        m = max(1, math.ceil(L / step))
        if isinstance(sg, Bezier):
            # arc length table: uniform spacing without an expensive inversion per sample
            ts = np.linspace(0.0, 1.0, 97)
            tp = np.array([sg.point_at(t) for t in ts])
            cum = np.concatenate([[0.0], np.cumsum(np.hypot(*np.diff(tp, axis=0).T))])
            L = float(cum[-1])
        for j in range(m):
            f = (j + 0.5) / m
            t = float(np.interp(L * f, cum, ts)) if isinstance(sg, Bezier) else f
            tg = sg.tangent_at(t)
            out.p.append(sg.point_at(t))
            out.n.append((-tg[1], tg[0]))
            out.loop.append(loop)
            out.run.append(run)
            out.s.append(acc + L * f)
        acc += L
    if not start_is_corner and run > 0:
        # the stretch before the first corner continues after the last one
        for k in range(first, len(out.run)):
            if out.run[k] == run:
                out.run[k] = 0
    out.lengths.append(acc)
    return out


def inscribed_radii(P: Samples, Q: Samples, rmax: float, window: float = 0.0, chunk: int = 64) -> np.ndarray:
    """Radius of the largest circle tangent at each P sample that contains no Q sample.

    Q samples closer than ``window`` along the same smooth stretch are ignored:
    tiny tangent jumps where fitted arcs meet would otherwise pinch the circle.
    """
    Pp, Pn, Pl, Pr, Ps = P.arrays()
    Qp, _, Ql, Qr, Qs = Q.arrays()
    lengths = np.asarray(Q.lengths, float)
    out = np.empty(len(Pp))
    for s0 in range(0, len(Pp), chunk):
        sl = slice(s0, s0 + chunk)
        p, n = Pp[sl], Pn[sl]
        if math.isfinite(rmax):
            lo = p.min(axis=0) - 2 * rmax
            hi = p.max(axis=0) + 2 * rmax
            m = np.all((Qp >= lo) & (Qp <= hi), axis=1)
        else:
            m = np.ones(len(Qp), bool)
        q = Qp[m]
        if len(q) == 0:
            out[sl] = rmax
            continue
        d = q[None, :, :] - p[:, None, :]
        dn = np.einsum("ijk,ik->ij", d, n)
        d2 = np.einsum("ijk,ijk->ij", d, d)
        valid = dn > 1e-9
        if window > 0:
            same = (Pl[sl][:, None] == Ql[m][None, :]) & (Pr[sl][:, None] == Qr[m][None, :]) & \
                   (Pr[sl][:, None] >= 0)
            ds = np.abs(Ps[sl][:, None] - Qs[m][None, :])
            Lp = lengths[Pl[sl]][:, None]
            ds = np.minimum(ds, Lp - ds)
            valid &= ~(same & (ds < window))
        with np.errstate(divide="ignore", invalid="ignore"):
            r = np.where(valid, d2 / (2 * np.where(valid, dn, 1.0)), np.inf)
        out[sl] = np.minimum(r.min(axis=1), rmax)
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
        loops_src, refitted = prepare_contours(closed)
        start = v["start_depth"]
        tan = math.tan(tool.half_angle())
        r0 = tool.tip_diameter / 2
        flat = v["flat_depth"] or 0.0
        rmax = r0 + flat * tan if flat > 0 else math.inf
        rlim = min(rmax, tool.radius)
        step = v["resolution"]
        Q = Samples()
        for k, c in enumerate(loops_src):
            sample_boundary(c, step / 2, Q, k)
        loops = []
        too_wide = False
        for k, src in enumerate(loops_src):
            S = sample_boundary(src, step, None, k)
            if not S.p:
                continue
            P, N = S.arrays()[:2]
            # circles larger than the cap are clamped anyway, which also bounds the search
            R = inscribed_radii(S, Q, rlim + 0.01, window=1.0 if refitted else max(4 * step, 0.3))
            if rmax > tool.radius and np.any(R > tool.radius + 1e-6):
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

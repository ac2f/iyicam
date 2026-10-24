"""Profile (contour) machining: outside / inside / on the vector."""
from __future__ import annotations

import math
from dataclasses import dataclass

from ..core import arcfit, clip, vectorops
from ..core.geom import Arc, Contour, Line, Pt, add, mul, perp, sub, unit
from ..core.params import Param
from .builder import MoveBuilder
from .operation import OpContext, Operation, depth_levels, depth_params, final_depth, stepdown
from .ordering import containment_levels, leveled_order
from .pathutil import PathWalker, emit_profile, machinable, nearest_on_polys, pl_max_breaks
from .moves import Toolpath

NEG_INF = -1e9


@dataclass
class CutItem:
    contour: Contour
    level: int = 0
    finish: bool = False
    wall_right: bool | None = None  # side of the finished wall (None: no leads)


def offset_loops(closed: list[Contour], delta: float, sharp: bool, tol: float) -> list[Contour]:
    """Offset a region and fit arcs; keeps the Clipper orientation (region on the left)."""
    reg = vectorops.region(closed, tol / 2)
    polys = clip.offset(reg, delta, "miter" if sharp else "round", arc_tol=tol / 2,
                        miter_limit=10.0 if sharp else 4.0)
    return [arcfit.fit_contour(p, True, tol) for p in polys if len(p) >= 3]


class TabPlan:
    """Tab positions along a closed loop and the resulting Z floor."""

    def __init__(self, length: float, centres: list[float], tab_len: float, top: float,
                 bottom: float, ramped: bool):
        self.L = length
        self.centres = centres
        self.h = tab_len / 2
        self.top = top
        self.ramp = (top - bottom) if ramped else 0.0

    def floor(self, s: float) -> float:
        best = NEG_INF
        for c in self.centres:
            d = abs((s - c + self.L / 2) % self.L - self.L / 2)
            if d <= self.h + 1e-9:
                return self.top
            if self.ramp > 0 and d <= self.h + self.ramp:
                best = max(best, self.top - (d - self.h))
        return best

    def breaks(self, s0: float, U: float) -> list[float]:
        out = []
        offs = [-self.h, self.h] + ([-self.h - self.ramp, self.h + self.ramp] if self.ramp > 0 else [])
        for c in self.centres:
            for o in offs:
                x = (c + o - s0) % self.L
                while x <= U + 1e-9:
                    out.append(x)
                    x += self.L
        return out


class ProfileOp(Operation):
    id = "profile"
    label = "Profile"
    description = "Cut along vectors on the outside, inside or on the line, with tabs, ramps and leads."
    tool_types = ("end_mill", "ball_nose", "bull_nose", "v_bit", "engraving")

    @classmethod
    def params(cls) -> list[Param]:
        ch = [("outside", "Outside / Right"), ("inside", "Inside / Left"), ("on", "On the vector")]
        return depth_params(6.0, through=True) + [
            Param("side", "Machine", "choice", "outside", choices=ch, group="Profile"),
            Param("direction", "Direction", "choice", "climb",
                  choices=[("climb", "Climb"), ("conventional", "Conventional")], group="Profile"),
            Param("allowance", "Allowance", "length", 0.0, group="Profile",
                  help="Material left on the wall; negative cuts into the part"),
            Param("sharp_corners", "Sharp corners", "bool", False, group="Profile",
                  visible={"side": ["outside", "inside"]},
                  help="Keep offset corners sharp instead of rolling around them"),
            Param("finish_pass", "Finish pass allowance", "length", 0.0, min=0.0, group="Profile",
                  visible={"side": ["outside", "inside"]},
                  help="Leave this much for a final full depth pass (0 = no finish pass)"),
            Param("start_mode", "Start point", "choice", "vector",
                  choices=[("vector", "Vector start point"), ("nearest", "Nearest to previous cut")],
                  group="Profile"),
            Param("tabs", "Add tabs / bridges", "bool", False, group="Tabs"),
            Param("tab_mode", "Tab placement", "choice", "count",
                  choices=[("count", "Number per vector"), ("spacing", "Distance between tabs"),
                           ("manual", "Positions picked on the drawing")],
                  group="Tabs", visible={"tabs": True},
                  help="Manual positions are edited with 'Edit Tabs' in the toolpath list"),
            Param("tab_count", "Tabs per vector", "int", 4, min=1, max=200, group="Tabs",
                  visible={"tabs": True, "tab_mode": "count"}),
            Param("tab_spacing", "Tab spacing", "length", 300.0, min=1.0, group="Tabs",
                  visible={"tabs": True, "tab_mode": "spacing"}),
            Param("tab_length", "Tab length", "length", 8.0, min=0.1, group="Tabs", visible={"tabs": True}),
            Param("tab_height", "Tab thickness", "length", 3.0, min=0.0, group="Tabs", visible={"tabs": True}),
            Param("tab_3d", "Ramped (3D) tabs", "bool", False, group="Tabs", visible={"tabs": True}),
            Param("tab_min_length", "Skip vectors shorter than", "length", 40.0, min=0.0, group="Tabs",
                  visible={"tabs": True}),
            Param("ramp", "Entry", "choice", "none",
                  choices=[("none", "Plunge"), ("linear", "Ramp over distance"), ("helix", "Smooth spiral")],
                  group="Ramp & Leads"),
            Param("ramp_length", "Ramp distance", "length", 20.0, min=0.1, group="Ramp & Leads",
                  visible={"ramp": "linear"}),
            Param("lead", "Lead in / out", "choice", "none",
                  choices=[("none", "None"), ("arc", "Arc"), ("line", "Line")], group="Ramp & Leads",
                  visible={"side": ["outside", "inside"]}),
            Param("lead_length", "Lead radius / length", "length", 5.0, min=0.1, group="Ramp & Leads",
                  visible={"lead": ["arc", "line"]}),
            Param("overlap", "Overlap at end", "length", 0.0, min=0.0, group="Ramp & Leads",
                  help="Continue past the start point to clean the seam"),
        ]

    # ---------------------------------------------------------------------------
    def compute(self, ctx: OpContext) -> list[Toolpath]:
        v = self.values
        tool = ctx.tool
        tol = ctx.tolerance
        start = v["start_depth"]
        final = final_depth(self, ctx)
        levels = depth_levels(start, final, stepdown(self, ctx))
        if not levels:
            ctx.warn("Cut depth must be deeper than the start depth")
            return []
        surface = -start
        side = v["side"]
        self._tool_r = (tool.radius_at_depth(final - start) if tool.is_tapered else tool.radius) + v["allowance"]
        closed = [c for c in ctx.contours if c.closed]
        opened = [machinable(c, tol) for c in ctx.contours if not c.closed]
        items: list[CutItem] = []
        if side == "on":
            items += [CutItem(machinable(c, tol)) for c in closed]
        else:
            r = tool.radius_at_depth(final - start) if tool.is_tapered else tool.radius
            base = r + v["allowance"]
            sign = 1.0 if side == "outside" else -1.0
            climb = v["direction"] == "climb"
            # Clipper keeps the solid on the left; for outside cuts that is the part.
            reverse = climb if side == "outside" else not climb
            fin = v["finish_pass"] or 0.0
            for delta, is_fin in ([(sign * (base + fin), False), (sign * base, True)] if fin > 0
                                  else [(sign * base, False)]):
                loops = offset_loops(closed, delta, v["sharp_corners"], tol)
                if not loops and not is_fin:
                    ctx.warn("Offset vanished: the tool is too large for some vectors")
                for lp in loops:
                    lp = lp.reversed() if reverse else lp
                    items.append(CutItem(lp, finish=is_fin, wall_right=climb))
            if opened:
                ctx.warn("Open vectors are machined on the line")
        items += [CutItem(c) for c in opened]
        if not items:
            ctx.warn("No vectors to machine")
            return []

        # preferred start points from the source vectors
        starts = [c.start for c in closed]
        if v["start_mode"] == "vector" and side != "on":
            items = [CutItem(self._rotate_to_hint(it.contour, starts), it.level, it.finish, it.wall_right)
                     if it.contour.closed else it for it in items]

        lv = containment_levels([it.contour for it in items])
        for it, l in zip(items, lv):
            it.level = l
        rough = [it for it in items if not it.finish]
        fin_items = [it for it in items if it.finish]
        home = ctx.material.home[:2]
        ordered = []
        pos = home
        for group in (rough, fin_items):
            if not group:
                continue
            g = leveled_order(group, [it.level for it in group], self._entry, self._exit, pos)
            ordered += g
            last = g[-1]
            pos = self._exit(last, self._entry(last, pos))

        mb = MoveBuilder(ctx.safe_z, ctx.clearance, tool.feed_rate, tool.plunge_rate, surface)
        for it in ordered:
            c = it.contour
            if c.closed and v["start_mode"] == "nearest" and mb.pos is not None:
                c = vectorops.set_start_point(c, mb.pos[:2])
            if c.closed:
                self._cut_closed(mb, c, levels if not it.finish else [levels[-1]], surface, final, it)
            else:
                self._cut_open(mb, c, levels, surface)
        return [self.make_toolpath(ctx, mb.finish())]

    # ---------------------------------------------------------------------------
    @staticmethod
    def _rotate_to_hint(c: Contour, starts: list[Pt]) -> Contour:
        if not starts:
            return c
        bb = c.bbox()
        reach = max(bb.width, bb.height) + 1.0
        cand = [p for p in starts if bb.inflated(reach).contains(p)] or starts
        best, bd = None, math.inf
        for p in cand:
            d = c.nearest(p)[3]
            if d < bd:
                best, bd = p, d
        return vectorops.set_start_point(c, best)

    def _entry(self, it: CutItem, pos: Pt) -> Pt:
        if it.contour.closed and self.values["start_mode"] == "nearest":
            return it.contour.nearest(pos)[2]
        return it.contour.start

    def _exit(self, it: CutItem, entry: Pt) -> Pt:
        return entry if it.contour.closed else it.contour.end

    # ---------------------------------------------------------------------------
    def _tabs(self, walker: PathWalker, final: float, tool_r: float) -> TabPlan | None:
        v = self.values
        L = walker.length
        if not v["tabs"]:
            return None
        top = -final + v["tab_height"]
        if v["tab_mode"] == "manual":
            reach = tool_r + v["tab_length"] + 1.0
            centres = []
            for pt in v.get("tab_points") or []:
                i, t, d = nearest_on_polys(tuple(pt), walker.segs)
                if d <= reach:
                    centres.append(walker.cum[i] + t * (walker.cum[i + 1] - walker.cum[i]))
            return TabPlan(L, centres, v["tab_length"], top, -final, v["tab_3d"]) if centres else None
        if L < max(v["tab_min_length"], v["tab_length"] * 2):
            return None
        if v["tab_mode"] == "spacing":
            n = max(1, round(L / max(1.0, v["tab_spacing"])))
        else:
            n = max(1, v["tab_count"])
        if n * v["tab_length"] >= L * 0.8:
            n = max(1, int(L * 0.8 / v["tab_length"]))
        centres = [(k + 0.5) * L / n for k in range(n)]
        return TabPlan(L, centres, v["tab_length"], top, -final, v["tab_3d"])

    def _cut_closed(self, mb: MoveBuilder, c: Contour, levels: list[float], surface: float,
                    final: float, it: CutItem) -> None:
        v = self.values
        walker = PathWalker(c.segs, True)
        L = walker.length
        if L < 1e-6:
            return
        tabs = self._tabs(walker, final, self._tool_r)
        lead = v["lead"] if it.wall_right is not None else "none"
        lead_len = v["lead_length"]
        overlap = v["overlap"]
        ramp = v["ramp"]
        s_cur = 0.0
        z_prev = surface
        floor_at_entry = surface
        passes = list(levels)
        helix = ramp == "helix" and lead == "none"
        if helix:
            passes.append(passes[-1])  # final clean-up lap at full depth
        for k, z in enumerate(passes):
            z_from = z_prev
            if lead != "none":
                U = L + overlap
                pz = lambda u, z=z: z
                breaks: list[float] = []
            elif helix and k < len(passes) - 1:
                U = L
                pz = lambda u, z=z, z0=z_from, U=U: z0 + (z - z0) * min(u, U) / U
                breaks = []
            elif ramp == "linear" and z < z_from - 1e-9:
                R = min(v["ramp_length"], L)
                U = L + R + overlap
                pz = lambda u, z=z, z0=z_from, R=R: z0 + (z - z0) * min(u, R) / R
                breaks = [R]
            else:
                U = L + overlap
                pz = lambda u, z=z: z
                breaks = []
            if tabs is not None and min(z, z_from) < tabs.top:
                s0 = s_cur
                zf = lambda u, pz=pz, s0=s0: max(pz(u), tabs.floor(s0 + u))
                br = pl_max_breaks(pz, lambda u, s0=s0: tabs.floor(s0 + u), breaks + tabs.breaks(s0, U) + [0.0, U])
            else:
                zf, br = pz, breaks
            p_start = walker.point_at(s_cur)
            z_entry_loop = zf(0.0)
            if lead != "none":
                T = walker.tangent_at(s_cur)
                S, lead_seg = self._lead_in(p_start, T, it.wall_right, lead, lead_len)
                z_s = z_from if ramp != "none" else z
                if k == 0:
                    mb.travel(S[0], S[1], z_s)
                else:
                    mb.hop(S[0], S[1], z_s, floor_at_entry)
                floor_at_entry = z_s
                mb.segment(lead_seg, z_entry_loop)
            else:
                if k == 0 or mb.pos is None:
                    mb.travel(p_start[0], p_start[1], z_entry_loop)
                else:
                    if abs(mb.pos[0] - p_start[0]) > 1e-6 or abs(mb.pos[1] - p_start[1]) > 1e-6:
                        mb.feed(p_start[0], p_start[1], mb.pos[2])
                    mb.plunge(z_entry_loop)
            mb.pos = emit_profile(mb.moves, walker, s_cur, U, zf, br, mb.feed_rate, mb.pos, mb.plunge_rate)
            if lead != "none":
                s_end = s_cur + U
                T2 = walker.tangent_at(s_end)
                seg = self._lead_out(walker.point_at(s_end), T2, it.wall_right, lead, lead_len)
                mb.segment(seg, mb.pos[2])
            else:
                s_cur = (s_cur + U) % L
            z_prev = z

    @staticmethod
    def _waste_normal(T: Pt, wall_right: bool) -> Pt:
        n = perp(T)
        return n if wall_right else mul(n, -1.0)

    def _lead_in(self, P: Pt, T: Pt, wall_right: bool, kind: str, R: float):
        N = self._waste_normal(T, wall_right)
        if kind == "arc":
            C = add(P, mul(N, R))
            S = sub(C, mul(T, R))
            ccw = wall_right  # centre on the left of the motion when the waste is on the left
            return S, Arc.from_center_points(S, P, C, ccw)
        S = add(P, mul(unit(sub(N, T)), R))
        return S, Line(S, P)

    def _lead_out(self, P: Pt, T: Pt, wall_right: bool, kind: str, R: float):
        N = self._waste_normal(T, wall_right)
        if kind == "arc":
            C = add(P, mul(N, R))
            E = add(C, mul(T, R))
            return Arc.from_center_points(P, E, C, wall_right)
        return Line(P, add(P, mul(unit(add(N, T)), R)))

    def _cut_open(self, mb: MoveBuilder, c: Contour, levels: list[float], surface: float) -> None:
        v = self.values
        cur = c
        z_prev = surface
        for k, z in enumerate(levels):
            walker = PathWalker(cur.segs, False)
            L = walker.length
            if L < 1e-9:
                return
            if v["ramp"] in ("linear", "helix") and z < z_prev:
                R = min(v["ramp_length"] if v["ramp"] == "linear" else L, L)
                zf = lambda u, z=z, z0=z_prev, R=R: z0 + (z - z0) * min(u, R) / R
                br = [R]
                z0 = z_prev
            else:
                zf = lambda u, z=z: z
                br = []
                z0 = z
            if k == 0:
                mb.travel(cur.start[0], cur.start[1], z0)
            else:
                mb.plunge(z0)
            mb.pos = emit_profile(mb.moves, walker, 0.0, L, zf, br, mb.feed_rate, mb.pos, mb.plunge_rate)
            z_prev = z
            cur = cur.reversed()


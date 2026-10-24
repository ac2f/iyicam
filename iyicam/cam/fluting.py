"""Fluting: follow vectors while ramping the depth in at the start and out at the end."""
from __future__ import annotations

import math

from ..core.params import Param
from .builder import MoveBuilder
from .moves import Toolpath
from .operation import OpContext, Operation, depth_levels
from .ordering import nearest_order
from .pathutil import PathWalker, emit_profile, machinable


class FlutingOp(Operation):
    id = "fluting"
    label = "Fluting"
    description = "Machine along vectors with the depth ramping in at the start and out at the end " \
                  "(decorative flutes with ball nose or V-bits)."

    @classmethod
    def params(cls) -> list[Param]:
        return [
            Param("start_depth", "Start depth", "length", 0.0, min=0.0, group="Depth"),
            Param("cut_depth", "Flute depth", "length", 4.0, min=0.01, group="Depth"),
            Param("stepdown", "Stepdown (0 = single pass)", "length", 0.0, min=0.0, group="Depth"),
            Param("ramp_units", "Ramp lengths in", "choice", "length",
                  choices=[("length", "Millimetres"), ("percent", "% of the vector length")], group="Ramps"),
            Param("ramp_start", "Ramp at start", "float", 20.0, min=0.0, group="Ramps"),
            Param("ramp_end", "Ramp at end", "float", 20.0, min=0.0, group="Ramps"),
            Param("profile", "Ramp shape", "choice", "linear",
                  choices=[("linear", "Linear"), ("smooth", "Smooth (S-curve)")], group="Ramps"),
        ]

    def compute(self, ctx: OpContext) -> list[Toolpath]:
        v = self.values
        tool = ctx.tool
        start, final = v["start_depth"], v["start_depth"] + v["cut_depth"]
        levels = depth_levels(start, final, v["stepdown"]) if v["stepdown"] > 0 else [-final]
        paths = [machinable(c, ctx.tolerance) for c in ctx.contours]
        if not paths:
            ctx.warn("No vectors to machine")
            return []
        ordered = nearest_order(paths, lambda c, pos: c.start, lambda c, e: c.end, ctx.material.home[:2])
        mb = MoveBuilder(ctx.safe_z, ctx.clearance, tool.feed_rate, tool.plunge_rate, -start)
        smooth = v["profile"] == "smooth"
        for c in ordered:
            walker = PathWalker(c.segs, False)
            L = walker.length
            if L < 1e-6:
                continue
            k = L / 100.0 if v["ramp_units"] == "percent" else 1.0
            rs, re = min(L, v["ramp_start"] * k), min(L, v["ramp_end"] * k)
            if rs + re > L:  # overlapping ramps share the vector
                rs, re = L * rs / (rs + re), L * re / (rs + re)

            def shape(u: float, rs=rs, re=re, L=L) -> float:
                f = 1.0
                if rs > 0 and u < rs:
                    f = u / rs
                elif re > 0 and u > L - re:
                    f = (L - u) / re
                f = max(0.0, min(1.0, f))
                return 0.5 - 0.5 * math.cos(math.pi * f) if smooth else f

            breaks = [rs, L - re]
            if smooth:
                n = 24
                breaks += [rs * i / n for i in range(1, n)] + [L - re * i / n for i in range(1, n)]
            for z in levels:
                depth = -start - z  # depth of this pass below the start depth
                zf = lambda u, depth=depth: -start - depth * shape(u)
                mb.travel(c.start[0], c.start[1], zf(0.0))
                mb.pos = emit_profile(mb.moves, walker, 0.0, L, zf, breaks, mb.feed_rate, mb.pos, mb.plunge_rate)
                mb.retract()
        return [self.make_toolpath(ctx, mb.finish())]

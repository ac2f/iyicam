"""Drilling at the centres of vectors (optionally only circles of a matching size)."""
from __future__ import annotations

import math

from ..core.geom import Arc, Contour, Pt, dist
from ..core.params import Param
from .builder import MoveBuilder
from .moves import DrillCycle, Toolpath
from .operation import OpContext, Operation, final_depth
from .ordering import nearest_order


def circle_of(c: Contour, tol: float = 1e-3) -> tuple[Pt, float] | None:
    """Centre and radius when ``c`` is a closed contour made only of concentric arcs."""
    if not c.closed or not c.segs or not all(isinstance(s, Arc) for s in c.segs):
        return None
    c0, r0 = c.segs[0].center, c.segs[0].radius
    for s in c.segs[1:]:
        if dist(s.center, c0) > tol or abs(s.radius - r0) > tol:
            return None
    return c0, r0


class DrillOp(Operation):
    id = "drill"
    label = "Drilling"
    description = "Drill holes at the centre of each selected vector."
    tool_types = ("drill", "end_mill", "ball_nose", "v_bit")

    @classmethod
    def params(cls) -> list[Param]:
        return [
            Param("start_depth", "Start depth", "length", 0.0, min=0.0, group="Depth"),
            Param("through", "Drill through material", "bool", False, group="Depth"),
            Param("overcut", "Drill below bottom", "length", 1.0, min=0.0, group="Depth",
                  visible={"through": True}),
            Param("cut_depth", "Hole depth", "length", 10.0, min=0.0, group="Depth", visible={"through": False}),
            Param("tip_compensation", "Add drill point length", "bool", False, group="Depth",
                  help="Deepen by the drill point so the full diameter reaches the depth"),
            Param("peck", "Peck depth (0 = none)", "length", 0.0, min=0.0, group="Drilling"),
            Param("dwell", "Dwell at bottom (s)", "float", 0.0, min=0.0, group="Drilling"),
            Param("retract", "Retract height", "length", 2.0, min=0.1, group="Drilling",
                  help="Height above the surface where drilling starts and returns to"),
            Param("targets", "Drill at", "choice", "centres",
                  choices=[("centres", "Centre of every selected vector"),
                           ("circles", "Circles only"),
                           ("matching", "Circles matching the tool diameter")], group="Drilling"),
            Param("match_tolerance", "Diameter tolerance", "length", 0.5, min=0.0, group="Drilling",
                  visible={"targets": "matching"}),
        ]

    def compute(self, ctx: OpContext) -> list[Toolpath]:
        v = self.values
        tool = ctx.tool
        pts: list[Pt] = []
        for c in ctx.contours:
            circ = circle_of(c)
            if v["targets"] == "centres":
                if circ:
                    pts.append(circ[0])
                elif c.closed:
                    pts.append(c.bbox().center)
                else:
                    continue
            elif circ is None:
                continue
            elif v["targets"] == "matching" and abs(2 * circ[1] - tool.diameter) > v["match_tolerance"]:
                continue
            else:
                pts.append(circ[0])
        if not pts:
            ctx.warn("No drill positions found in the selection")
            return []
        depth = final_depth(self, ctx)
        if v["tip_compensation"] and tool.type == "drill":
            depth += tool.radius / math.tan(tool.half_angle())
        start = v["start_depth"]
        surface = -start
        r = surface + v["retract"]
        ordered = nearest_order(pts, lambda p, pos: p, lambda p, e: p, ctx.material.home[:2])
        mb = MoveBuilder(ctx.safe_z, ctx.clearance, tool.feed_rate, tool.plunge_rate, surface)
        for p in ordered:
            mb.retract()
            mb.rapid(p[0], p[1], ctx.safe_z)
            mb.moves.append(DrillCycle(p[0], p[1], -depth, r, tool.plunge_rate, v["peck"], v["dwell"]))
            mb.pos = (p[0], p[1], r)
        return [self.make_toolpath(ctx, mb.finish())]

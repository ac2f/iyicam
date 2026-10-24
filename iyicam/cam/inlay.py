"""V-carve inlays: a pocket (female) and a mirrored plug (male) that fits into it.

The plug is the V-carve of the background around the mirrored design, started
``start`` below the surface. Turned over and pressed into the pocket, its walls
meet the pocket walls; flat areas keep a glue gap of ``flat_depth - start``.
"""
from __future__ import annotations

import math

from ..core import clip, vectorops
from ..core.geom import BBox, Contour, Transform
from ..core.params import Param
from .moves import Toolpath
from .operation import OpContext
from .vcarve import VCarveOp


class InlayOp(VCarveOp):
    id = "inlay"
    label = "Inlay"
    description = "V-carve inlay: the pocket, or the mirrored plug that fits into it."

    @classmethod
    def params(cls) -> list[Param]:
        male = {"part": "male"}
        own = [
            Param("part", "Part", "choice", "female", choices=[("female", "Pocket (female)"), ("male", "Plug (male)")],
                  group="Inlay"),
            Param("flat_depth", "Pocket depth", "length", 5.0, min=0.01, group="Inlay",
                  help="Depth of the pocket; wider areas are machined flat at this depth"),
            Param("glue_gap", "Glue gap", "length", 1.0, min=0.0, group="Inlay", visible=male,
                  help="Space left under the plug in flat areas (the plug starts this much higher)"),
            Param("margin", "Plug margin (0 = automatic)", "length", 0.0, min=0.0, group="Inlay", visible=male,
                  help="Width of the background carved around the plug"),
            Param("mirror", "Mirror the plug", "bool", True, group="Inlay", visible=male,
                  help="Mirror left/right in place so the plug can be turned over"),
        ]
        keep = ("stepdown", "clear_flats", "clear_tool", "resolution", "smoothing")
        return own + [p for p in super().params() if p.name in keep]

    def compute(self, ctx: OpContext) -> list[Toolpath]:
        v = self.values
        tool = ctx.tool
        if not tool.is_tapered:
            ctx.warn("Inlays need a V-bit")
            return []
        design = [c for c in ctx.contours if c.closed]
        if not design:
            ctx.warn("Select closed vectors for the inlay")
            return []
        flat = v["flat_depth"]
        if v["part"] == "female":
            return self.carve(ctx, design, 0.0, flat)
        gap = min(v["glue_gap"], flat)
        if v["mirror"]:
            bb = BBox()
            for c in design:
                bb = bb.union(c.bbox())
            T = Transform.mirror(True, ((bb.xmin + bb.xmax) / 2, 0.0))
            design = [c.transformed(T) for c in design]
        reach = tool.tip_diameter / 2 + flat * math.tan(tool.half_angle())
        margin = v["margin"] or 2 * reach + 1.0
        if margin < 2 * reach:
            ctx.warn(f"Plug margin is narrower than the V-bit at full depth ({2 * reach:.2f} mm)")
        outer = clip.offset(vectorops.region(design, ctx.tolerance), margin, arc_tol=0.02)
        boundary = [Contour.polyline(p, True) for p in outer]
        return self.carve(ctx, boundary + design, flat - gap, flat)

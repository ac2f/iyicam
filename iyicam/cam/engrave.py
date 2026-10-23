"""Engraving: follow vectors exactly at a depth (optionally derived from a V-bit line width)."""
from __future__ import annotations

from ..core.params import Param
from .moves import Toolpath
from .operation import OpContext
from .profile import ProfileOp

_FIXED = {"side": "on", "through": False, "overcut": 0.0, "allowance": 0.0, "sharp_corners": False,
          "finish_pass": 0.0, "tabs": False, "lead": "none", "lead_length": 1.0,
          "tab_mode": "count", "tab_count": 1, "tab_spacing": 100.0, "tab_length": 1.0,
          "tab_height": 0.0, "tab_3d": False, "tab_min_length": 0.0, "direction": "climb"}


class EngraveOp(ProfileOp):
    id = "engrave"
    label = "Engrave (Follow Vector)"
    description = "Machine exactly along the vectors; with a V-bit the depth can come from a line width."
    tool_types = None

    @classmethod
    def params(cls) -> list[Param]:
        keep = {"start_depth", "cut_depth", "stepdown", "ramp", "ramp_length", "start_mode", "overlap"}
        ps = [p for p in ProfileOp.params() if p.name in keep]
        for p in ps:
            if p.name == "cut_depth":
                p.default = 1.0
                p.visible = {"depth_mode": "depth"}
        ps.insert(1, Param("depth_mode", "Depth from", "choice", "depth",
                           choices=[("depth", "Cut depth"), ("width", "Line width (tapered tools)")],
                           group="Depth"))
        ps.insert(3, Param("line_width", "Line width", "length", 1.0, min=0.01, group="Depth",
                           visible={"depth_mode": "width"}))
        return ps

    def __init__(self, values: dict | None = None):
        super().__init__(values)
        self.values.update(_FIXED)

    def compute(self, ctx: OpContext) -> list[Toolpath]:
        if self.values["depth_mode"] == "width":
            if not ctx.tool.is_tapered:
                ctx.warn("Line width needs a V-bit or engraving tool; using the cut depth")
            else:
                self.values["cut_depth"] = self.values["start_depth"] + ctx.tool.depth_for_radius(
                    self.values["line_width"] / 2)
        return super().compute(ctx)

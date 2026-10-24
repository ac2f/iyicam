"""Base class for toolpath strategies (profile, pocket, drill, ...)."""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import ClassVar

from ..core.document import MaterialSetup, Project, Sheet, ToolpathDef, VectorObject
from ..core.geom import Contour
from ..core.params import Param, resolve
from ..core.tooling import Tool
from .moves import Toolpath


@dataclass
class OpContext:
    project: Project
    sheet: Sheet
    tdef: ToolpathDef
    objects: list[VectorObject]
    tolerance: float = 0.005
    warnings: list[str] = field(default_factory=list)
    previous: list = field(default_factory=list)  # toolpaths machined before this one on the sheet
    force_smart: bool = False                      # pick smart start points for this calculation
    order: list[str] | None = None                 # out: the cut order (vector contour keys)
    start_updates: dict | None = field(default_factory=dict)  # out: better vector start points

    @property
    def tool(self) -> Tool:
        return self.tdef.tool

    @property
    def material(self) -> MaterialSetup:
        return self.project.material

    @property
    def thickness(self) -> float:
        return self.project.sheet_thickness(self.sheet)

    @property
    def contours(self) -> list[Contour]:
        return [c for o in self.objects for c in o.contours if c.segs]

    @property
    def safe_z(self) -> float:
        return self.material.safe_z

    @property
    def clearance(self) -> float:
        return self.material.clearance

    def warn(self, text: str) -> None:
        if text not in self.warnings:
            self.warnings.append(text)


class Operation:
    """Subclass, set the class attributes and implement :meth:`compute`.

    Register the class through the plugin API (``api.add_operation``).
    """

    id: ClassVar[str] = ""
    label: ClassVar[str] = ""
    description: ClassVar[str] = ""
    tool_types: ClassVar[tuple[str, ...] | None] = None  # allowed tool types, None = any
    needs_closed: ClassVar[bool] = False
    extra_tool_params: ClassVar[tuple[str, ...]] = ()     # params of kind 'tool'

    @classmethod
    def params(cls) -> list[Param]:
        return []

    def __init__(self, values: dict | None = None):
        self.values = resolve(self.params(), values)

    def __getitem__(self, key: str):
        return self.values[key]

    def compute(self, ctx: OpContext) -> list[Toolpath]:
        raise NotImplementedError

    # -- helpers shared by strategies ----------------------------------------------
    def make_toolpath(self, ctx: OpContext, moves: list, tool: Tool | None = None,
                      suffix: str = "") -> Toolpath:
        return Toolpath(ctx.tdef.name + suffix, tool or ctx.tool, moves, self.id, ctx.tdef.id,
                        ctx.sheet.id, ctx.safe_z)


def depth_params(default_depth: float | None = 5.0, through: bool = False) -> list[Param]:
    """Common depth settings: start depth, cut depth (or through cut) and stepdown."""
    ps = [Param("start_depth", "Start depth", "length", 0.0, min=0.0, group="Depth",
                help="Depth below the material surface where cutting starts")]
    ps.append(Param("through", "Cut through material", "bool", through, group="Depth",
                    help="Cut to the full material thickness"))
    ps.append(Param("overcut", "Cut below bottom", "length", 0.2, min=0.0, group="Depth",
                    visible={"through": True}, help="Extra depth into the spoilboard"))
    ps.append(Param("cut_depth", "Cut depth", "length", default_depth, min=0.0, group="Depth",
                    visible={"through": False}))
    ps.append(Param("stepdown", "Stepdown (0 = tool)", "length", 0.0, min=0.0, group="Depth",
                    help="Maximum depth per pass; 0 uses the tool's stepdown"))
    return ps


def final_depth(op: Operation, ctx: OpContext) -> float:
    if op.values.get("through"):
        return ctx.thickness + (op.values.get("overcut") or 0.0)
    return op.values.get("cut_depth") or 0.0


def stepdown(op: Operation, ctx: OpContext) -> float:
    sd = op.values.get("stepdown") or 0.0
    return sd if sd > 0 else max(0.01, ctx.tool.stepdown)


def depth_levels(start: float, final: float, step: float) -> list[float]:
    """Pass depths as negative Z values with equal steps from ``-start`` to ``-final``."""
    if final <= start + 1e-9:
        return []
    n = max(1, math.ceil((final - start) / max(step, 1e-6) - 1e-9))
    return [-(start + (final - start) * (i + 1) / n) for i in range(n)]

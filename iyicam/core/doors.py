"""Parametric cabinet doors and panels (MDF doors): outline, panel lines and grooves."""
from __future__ import annotations

from dataclasses import dataclass, field

from . import shapes, vectorops
from .geom import Arc, Contour, Line

PANEL_STYLES = [("square", "Square"), ("round", "Rounded corners"), ("cove", "Cove (inverted) corners"),
                ("chamfer", "Chamfered corners"), ("arch", "Arched top")]


@dataclass
class DoorDesign:
    outline: Contour
    panels: list[Contour] = field(default_factory=list)
    grooves: list[Contour] = field(default_factory=list)


def panel(x: float, y: float, w: float, h: float, style: str = "square", radius: float = 10.0,
          arch: float = 0.0) -> Contour:
    """One panel line; ``arch`` is the rise of an arched top above its straight sides."""
    if style == "arch" and arch > 1e-9:
        rise = min(arch, h * 0.9)
        top = y + h - rise
        # DXF bulge = 2 * sagitta / chord, positive (CCW) bulges up when running right to left
        return Contour([Line((x, y), (x + w, y)), Line((x + w, y), (x + w, top)),
                        Arc((x + w, top), (x, top), 2 * rise / w), Line((x, top), (x, y))], True)
    corner = {"round": "round", "cove": "inverted", "chamfer": "chamfer"}.get(style)
    return shapes.rectangle(x, y, w, h, corner_radius=radius if corner else 0.0, corner=corner or "round")


def door(x: float, y: float, w: float, h: float, frame: float = 70.0, panels: int = 1,
         rail: float | None = None, style: str = "square", radius: float = 10.0, arch: float = 0.0,
         outer_radius: float = 0.0, grooves: int = 0, groove_spacing: float = 8.0,
         groove_side: str = "panel") -> DoorDesign:
    """Door of ``w`` x ``h`` at ``(x, y)`` with ``panels`` stacked panels inside a frame.

    Only the top panel gets the arch. Grooves are offsets of the panel lines, into the
    panels or out onto the frame.
    """
    rail = frame if rail is None else rail
    panels = max(1, panels)
    pw = w - 2 * frame
    ph = (h - 2 * frame - (panels - 1) * rail) / panels
    if pw <= 0 or ph <= 0:
        raise ValueError("The frame and rails leave no room for the panels")
    d = DoorDesign(shapes.rectangle(x, y, w, h, corner_radius=outer_radius))
    for i in range(panels):
        py = y + frame + i * (ph + rail)
        top = i == panels - 1
        d.panels.append(panel(x + frame, py, pw, ph, style if top or style != "arch" else "square",
                              radius, arch))
    sign = -1.0 if groove_side == "panel" else 1.0
    for p in d.panels:
        for k in range(1, grooves + 1):
            d.grooves += vectorops.offset([p], sign * k * groove_spacing)
    return d

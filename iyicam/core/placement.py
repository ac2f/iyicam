"""Where a selection is and how it is moved, turned, scaled, sized and slanted about a reference point.

The reference point is one of the nine points of the box around the selection (the corners, the middles of the
sides and the centre), as in the transform panel of a drawing program.
"""
from __future__ import annotations

import math

from .geom import BBox, Pt, Transform

# name -> (fraction of the width, fraction of the height) from the lower left corner of the box
ANCHORS: dict[str, tuple[float, float]] = {
    "nw": (0.0, 1.0), "n": (0.5, 1.0), "ne": (1.0, 1.0),
    "w": (0.0, 0.5), "c": (0.5, 0.5), "e": (1.0, 0.5),
    "sw": (0.0, 0.0), "s": (0.5, 0.0), "se": (1.0, 0.0),
}
ANCHOR_LABELS = {"nw": "Top left", "n": "Top", "ne": "Top right", "w": "Left", "c": "Centre", "e": "Right",
                 "sw": "Bottom left", "s": "Bottom", "se": "Bottom right"}


def anchor_point(bb: BBox, anchor: str) -> Pt:
    fx, fy = ANCHORS[anchor]
    return (bb.xmin + bb.width * fx, bb.ymin + bb.height * fy)


def move_to(bb: BBox, anchor: str, x: float, y: float) -> Transform:
    """Move the box so that its ``anchor`` point lands on (x, y)."""
    ax, ay = anchor_point(bb, anchor)
    return Transform.translate(x - ax, y - ay)


def size_to(bb: BBox, anchor: str, width: float, height: float) -> Transform:
    """Resize the box to ``width`` x ``height``; the ``anchor`` point stays where it is. A side of the box that
    has no length (a horizontal line has no height) cannot be stretched and stays as it is."""
    if width <= 0 or height <= 0:
        raise ValueError("the size must be more than nothing")
    sx = width / bb.width if bb.width > 1e-9 else 1.0
    sy = height / bb.height if bb.height > 1e-9 else 1.0
    return Transform.scale(sx, sy, anchor_point(bb, anchor))


def scale_by(bb: BBox, anchor: str, sx_percent: float, sy_percent: float) -> Transform:
    if sx_percent <= 0 or sy_percent <= 0:
        raise ValueError("the scale must be more than 0 %")
    return Transform.scale(sx_percent / 100.0, sy_percent / 100.0, anchor_point(bb, anchor))


def turn(angle_deg: float, pivot: Pt) -> Transform:
    """Counter-clockwise for a positive angle."""
    return Transform.rotate(math.radians(angle_deg), pivot)


def flip(bb: BBox, anchor: str, left_right: bool) -> Transform:
    """Mirror left / right (``left_right``) or top / bottom about the ``anchor`` point."""
    return Transform.mirror(left_right, anchor_point(bb, anchor))


def slant(bb: BBox, anchor: str, angle_x_deg: float, angle_y_deg: float) -> Transform:
    return Transform.skew(math.radians(angle_x_deg), math.radians(angle_y_deg), anchor_point(bb, anchor))


def is_identity(T: Transform, tol: float = 1e-12) -> bool:
    return all(abs(v - w) <= tol for v, w in zip(T.to_list(), (1.0, 0.0, 0.0, 1.0, 0.0, 0.0)))

"""Parametric primitive shapes.  All closed shapes are returned counter-clockwise."""
from __future__ import annotations

import math
from typing import Sequence

from .geom import Arc, Bezier, Contour, Line, Pt, Segment, Transform, polar

KAPPA = 0.5522847498307936  # bezier handle factor for a quarter circle


def line(p0: Pt, p1: Pt) -> Contour:
    return Contour([Line(p0, p1)], False)


def polyline(pts: Sequence[Pt], closed: bool = False) -> Contour:
    return Contour.polyline(pts, closed)


def rectangle(x: float, y: float, w: float, h: float, corner_radius: float = 0.0,
              corner: str = "round", rotation: float = 0.0) -> Contour:
    """Rectangle with lower-left corner ``(x, y)``.

    ``corner`` is ``round``, ``chamfer`` or ``inverted`` (concave round corners).
    ``rotation`` is in degrees about the rectangle centre.
    """
    w, h = abs(w), abs(h)
    r = max(0.0, min(corner_radius, w / 2, h / 2))
    if r <= 1e-9:
        c = Contour.polyline([(x, y), (x + w, y), (x + w, y + h), (x, y + h)], True)
    else:
        x0, y0, x1, y1 = x, y, x + w, y + h
        bulge = {"round": math.tan(math.pi / 8), "inverted": -math.tan(math.pi / 8)}.get(corner, 0.0)
        pts = [((x0 + r, y0), (x1 - r, y0)), ((x1, y0 + r), (x1, y1 - r)),
               ((x1 - r, y1), (x0 + r, y1)), ((x0, y1 - r), (x0, y0 + r))]
        segs: list[Segment] = []
        for i, (a, b) in enumerate(pts):
            if a != b:
                segs.append(Line(a, b))
            nxt = pts[(i + 1) % 4][0]
            segs.append(Arc(b, nxt, bulge) if bulge else Line(b, nxt))
        c = Contour(segs, True)
    if rotation:
        c = c.transformed(Transform.rotate(math.radians(rotation), (x + w / 2, y + h / 2)))
    return c


def rectangle_centered(cx: float, cy: float, w: float, h: float, **kw) -> Contour:
    return rectangle(cx - abs(w) / 2, cy - abs(h) / 2, w, h, **kw)


def circle(center: Pt, radius: float, start_angle: float = 0.0) -> Contour:
    """Full circle as two half arcs."""
    a = polar(center, radius, start_angle)
    b = polar(center, radius, start_angle + math.pi)
    return Contour([Arc(a, b, 1.0), Arc(b, a, 1.0)], True)


def arc(center: Pt, radius: float, start_deg: float, end_deg: float, ccw: bool = True) -> Contour:
    sa, ea = math.radians(start_deg), math.radians(end_deg)
    sweep = (ea - sa) % (2 * math.pi) if ccw else -((sa - ea) % (2 * math.pi))
    if abs(sweep) < 1e-12:
        return circle(center, radius, sa)
    n = 1 if abs(sweep) <= math.pi else 2
    segs = []
    for i in range(n):
        segs.append(Arc.from_center(center, radius, sa + sweep * i / n, sweep / n))
    return Contour(segs, False)


def arc_3pt(p0: Pt, pm: Pt, p1: Pt) -> Contour:
    return Contour([Arc.through(p0, pm, p1)], False)


def ellipse(center: Pt, rx: float, ry: float, rotation: float = 0.0) -> Contour:
    cx, cy = center
    k = KAPPA
    segs = [
        Bezier((cx + rx, cy), (cx + rx, cy + k * ry), (cx + k * rx, cy + ry), (cx, cy + ry)),
        Bezier((cx, cy + ry), (cx - k * rx, cy + ry), (cx - rx, cy + k * ry), (cx - rx, cy)),
        Bezier((cx - rx, cy), (cx - rx, cy - k * ry), (cx - k * rx, cy - ry), (cx, cy - ry)),
        Bezier((cx, cy - ry), (cx + k * rx, cy - ry), (cx + rx, cy - k * ry), (cx + rx, cy)),
    ]
    c = Contour(segs, True)
    if rotation:
        c = c.transformed(Transform.rotate(math.radians(rotation), center))
    return c


def polygon(center: Pt, radius: float, sides: int, rotation: float = 0.0,
            inscribed: bool = True, corner_radius: float = 0.0) -> Contour:
    """Regular polygon.  ``inscribed`` means vertices lie on ``radius``."""
    sides = max(3, int(sides))
    r = radius if inscribed else radius / math.cos(math.pi / sides)
    base = math.radians(rotation) + math.pi / 2
    pts = [polar(center, r, base + 2 * math.pi * i / sides) for i in range(sides)]
    c = Contour.polyline(pts, True)
    if corner_radius > 0:
        from .vectorops import fillet
        c = fillet(c, corner_radius)
    return c


def star(center: Pt, outer: float, inner: float, points: int, rotation: float = 0.0) -> Contour:
    points = max(3, int(points))
    base = math.radians(rotation) + math.pi / 2
    pts = []
    for i in range(points * 2):
        r = outer if i % 2 == 0 else inner
        pts.append(polar(center, r, base + math.pi * i / points))
    return Contour.polyline(pts, True)


def slot(p0: Pt, p1: Pt, width: float) -> Contour:
    """Obround slot between the centres ``p0`` and ``p1``."""
    r = width / 2
    dx, dy = p1[0] - p0[0], p1[1] - p0[1]
    L = math.hypot(dx, dy)
    if L < 1e-9:
        return circle(p0, r)
    nx, ny = -dy / L * r, dx / L * r
    a = (p0[0] - nx, p0[1] - ny)
    b = (p1[0] - nx, p1[1] - ny)
    c = (p1[0] + nx, p1[1] + ny)
    d = (p0[0] + nx, p0[1] + ny)
    return Contour([Line(a, b), Arc(b, c, 1.0), Line(c, d), Arc(d, a, 1.0)], True)


def spiral(center: Pt, start_radius: float, pitch: float, turns: float, segments_per_turn: int = 72) -> Contour:
    n = max(2, int(turns * segments_per_turn))
    pts = []
    for i in range(n + 1):
        t = i / segments_per_turn
        pts.append(polar(center, start_radius + pitch * t, 2 * math.pi * t))
    return Contour.polyline(pts, False)


def translated(c: Contour, dx: float, dy: float) -> Contour:
    return c.transformed(Transform.translate(dx, dy))


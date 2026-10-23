"""Conversions between iyiCAM geometry and Qt painter paths."""
from __future__ import annotations

import math
from typing import Iterable

from PySide6.QtGui import QFont, QFontMetricsF, QPainterPath

from ..cam.moves import ArcMove, DrillCycle, Feed, Rapid, Toolpath, arc_sweep
from ..core.geom import Arc, Bezier, Contour, Line, Segment, same


def add_contour(path: QPainterPath, c: Contour) -> None:
    if not c.segs:
        return
    path.moveTo(*c.start)
    for s in c.segs:
        if isinstance(s, Line):
            path.lineTo(*s.b)
        elif isinstance(s, Arc):
            for bz in s.to_beziers():
                path.cubicTo(bz.c1[0], bz.c1[1], bz.c2[0], bz.c2[1], bz.b[0], bz.b[1])
        elif isinstance(s, Bezier):
            path.cubicTo(s.c1[0], s.c1[1], s.c2[0], s.c2[1], s.b[0], s.b[1])
    if c.closed:
        path.closeSubpath()


def contours_path(contours: Iterable[Contour]) -> QPainterPath:
    p = QPainterPath()
    for c in contours:
        add_contour(p, c)
    return p


def toolpath_paths(tp: Toolpath) -> tuple[QPainterPath, QPainterPath, list[tuple[float, float]]]:
    """(cutting moves, rapid moves, drill points) in world coordinates."""
    feed, rapid = QPainterPath(), QPainterPath()
    drills: list[tuple[float, float]] = []
    pos = None
    for m in tp.moves:
        if isinstance(m, DrillCycle):
            drills.append((m.x, m.y))
            if pos is not None and (pos[0], pos[1]) != (m.x, m.y):
                rapid.moveTo(pos[0], pos[1])
                rapid.lineTo(m.x, m.y)
            pos = (m.x, m.y, m.r)
            continue
        if not isinstance(m, (Rapid, Feed, ArcMove)):
            continue
        if pos is None:
            pos = (m.x, m.y, m.z)
            continue
        if isinstance(m, Rapid):
            if (pos[0], pos[1]) != (m.x, m.y):
                rapid.moveTo(pos[0], pos[1])
                rapid.lineTo(m.x, m.y)
        else:
            if feed.currentPosition().x() != pos[0] or feed.currentPosition().y() != pos[1] or feed.elementCount() == 0:
                feed.moveTo(pos[0], pos[1])
            if isinstance(m, ArcMove):
                sw = arc_sweep(pos[0], pos[1], m)
                r = math.hypot(pos[0] - m.cx, pos[1] - m.cy)
                if r < 1e-9 or abs(sw) < 1e-12:
                    feed.lineTo(m.x, m.y)
                else:
                    a0 = math.atan2(pos[1] - m.cy, pos[0] - m.cx)
                    n = max(1, math.ceil(abs(sw) / (math.pi / 2)))
                    for i in range(n):
                        arc = Arc.from_center((m.cx, m.cy), r, a0 + sw * i / n, sw / n)
                        for bz in arc.to_beziers():
                            feed.cubicTo(bz.c1[0], bz.c1[1], bz.c2[0], bz.c2[1], bz.b[0], bz.b[1])
            else:
                feed.lineTo(m.x, m.y)
        pos = (m.x, m.y, m.z)
    return feed, rapid, drills


def path_to_contours(path: QPainterPath, flip_y: bool = True) -> list[Contour]:
    """Convert a (font) QPainterPath into closed contours; flips Y (Qt is Y-down)."""
    fy = -1.0 if flip_y else 1.0
    out: list[Contour] = []
    segs: list[Segment] = []
    cur = None
    i = 0
    n = path.elementCount()

    def flush():
        nonlocal segs
        if segs:
            if not same(segs[-1].b, segs[0].a, 1e-9):
                segs.append(Line(segs[-1].b, segs[0].a))
            out.append(Contour(segs, True))
        segs = []

    while i < n:
        e = path.elementAt(i)
        pt = (e.x, e.y * fy)
        if e.isMoveTo():
            flush()
            cur = pt
            i += 1
        elif e.isLineTo():
            if not same(cur, pt, 1e-9):
                segs.append(Line(cur, pt))
            cur = pt
            i += 1
        elif e.isCurveTo():
            c1 = pt
            e2, e3 = path.elementAt(i + 1), path.elementAt(i + 2)
            c2 = (e2.x, e2.y * fy)
            p = (e3.x, e3.y * fy)
            segs.append(Bezier(cur, c1, c2, p))
            cur = p
            i += 3
        else:
            i += 1
    flush()
    if flip_y:
        # flipping Y mirrors the winding; make outer contours counter-clockwise again
        out = [c.reversed() for c in out]
    return [c for c in out if len(c.segs) >= 2 and c.area() > 1e-9]


def text_contours(text: str, family: str, height: float, bold: bool = False, italic: bool = False,
                  spacing: float = 0.0, line_spacing: float = 1.4, align: str = "left") -> list[Contour]:
    """Outline contours of ``text`` with capital letters ``height`` mm tall, baseline at y = 0."""
    font = QFont(family)
    font.setPointSizeF(100.0)
    font.setBold(bold)
    font.setItalic(italic)
    if spacing:
        font.setLetterSpacing(QFont.AbsoluteSpacing, spacing / max(1e-9, height) * 70.0)
    fm = QFontMetricsF(font)
    cap = fm.capHeight() or fm.ascent() * 0.7
    k = height / cap
    path = QPainterPath()
    lines = text.split("\n")
    widths = [fm.horizontalAdvance(l) for l in lines]
    wmax = max(widths) if widths else 0.0
    for i, (ln, w) in enumerate(zip(lines, widths)):
        dx = {"left": 0.0, "center": (wmax - w) / 2, "right": wmax - w}.get(align, 0.0)
        path.addText(dx, i * fm.height() * line_spacing / 1.2, font, ln)
    contours = path_to_contours(path, True)
    from ..core.geom import Transform
    T = Transform.scale(k, k)
    return [c.transformed(T) for c in contours]

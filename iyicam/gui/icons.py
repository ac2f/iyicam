"""Small programmatically drawn toolbar icons (no external image files needed)."""
from __future__ import annotations

import math

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QIcon, QPainter, QPainterPath, QPen, QPixmap, QPolygonF

from . import theme

S = 24


def _ink() -> QColor:
    return QColor(theme.get().icon)


def _icon(draw) -> QIcon:
    """An icon in the colour of the theme, drawn twice as large for sharp lines on high resolution screens."""
    pm = QPixmap(S * 2, S * 2)
    pm.setDevicePixelRatio(2.0)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing)
    p.setPen(QPen(_ink(), 1.7, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
    draw(p)
    p.end()
    return QIcon(pm)


def _select(p):
    p.setBrush(_ink())
    p.drawPolygon(QPolygonF([QPointF(6, 3), QPointF(6, 19), QPointF(10, 15), QPointF(13, 21), QPointF(15, 20),
                             QPointF(12, 14), QPointF(18, 14)]))


def _nodes(p):
    path = QPainterPath(QPointF(3, 19))
    path.cubicTo(6, 4, 16, 22, 21, 5)
    p.drawPath(path)
    p.setBrush(QColor(theme.get().field))
    for x, y in ((3, 19), (12, 13), (21, 5)):
        p.drawRect(QRectF(x - 2.5, y - 2.5, 5, 5))


def _polyline(p):
    p.drawPolyline(QPolygonF([QPointF(3, 20), QPointF(8, 6), QPointF(14, 16), QPointF(21, 4)]))


def _line(p):
    p.drawLine(QPointF(4, 20), QPointF(20, 4))


def _rect(p):
    p.drawRect(QRectF(4, 6, 16, 12))


def _circle(p):
    p.drawEllipse(QPointF(12, 12), 8, 8)


def _ellipse(p):
    p.drawEllipse(QPointF(12, 12), 9, 5.5)


def _polygon(p):
    p.drawPolygon(QPolygonF([QPointF(12 + 8 * math.cos(a), 12 + 8 * math.sin(a))
                             for a in [math.pi / 2 + k * math.pi / 3 for k in range(6)]]))


def _star(p):
    pts = []
    for k in range(10):
        r = 9 if k % 2 == 0 else 4
        a = -math.pi / 2 + k * math.pi / 5
        pts.append(QPointF(12 + r * math.cos(a), 12 + r * math.sin(a)))
    p.drawPolygon(QPolygonF(pts))


def _arc(p):
    p.drawArc(QRectF(4, 6, 16, 16), 20 * 16, 140 * 16)


def _text(p):
    f = p.font()
    f.setPixelSize(17)
    f.setBold(True)
    p.setFont(f)
    p.drawText(QRectF(0, 0, S, S), Qt.AlignCenter, "A")


def _measure(p):
    p.drawLine(QPointF(3, 17), QPointF(21, 7))
    for t in (0.0, 0.33, 0.66, 1.0):
        x, y = 3 + 18 * t, 17 - 10 * t
        p.drawLine(QPointF(x, y), QPointF(x + 2, y + 3.5))


def _trim(p):
    p.drawLine(QPointF(4, 18), QPointF(20, 18))
    p.drawLine(QPointF(9, 4), QPointF(15, 21))
    p.setPen(QPen(QColor(theme.get().danger), 1.7, Qt.DashLine))
    p.drawLine(QPointF(15, 18), QPointF(20, 18))


def _start(p):
    p.drawRoundedRect(QRectF(4, 6, 16, 12), 3, 3)
    p.setBrush(QColor(theme.get().danger))
    p.setPen(QPen(QColor(theme.get().danger), 1.2))
    p.drawEllipse(QPointF(12, 6), 2.6, 2.6)
    p.drawPolygon(QPolygonF([QPointF(15, 3.5), QPointF(19, 6), QPointF(15, 8.5)]))


def _zoom(p):
    p.drawEllipse(QPointF(10, 10), 6, 6)
    p.drawLine(QPointF(14.5, 14.5), QPointF(20, 20))


def _new(p):
    p.drawPolyline(QPolygonF([QPointF(13, 3.5), QPointF(6, 3.5), QPointF(6, 20.5), QPointF(18, 20.5),
                              QPointF(18, 8.5), QPointF(13, 3.5), QPointF(13, 8.5), QPointF(18, 8.5)]))
    p.drawLine(QPointF(12, 12), QPointF(12, 17))
    p.drawLine(QPointF(9.5, 14.5), QPointF(14.5, 14.5))


def _open(p):
    p.drawPolyline(QPolygonF([QPointF(3.5, 18.5), QPointF(3.5, 6), QPointF(9, 6), QPointF(11, 8.5), QPointF(18, 8.5),
                              QPointF(18, 11)]))
    p.drawPolyline(QPolygonF([QPointF(3.5, 18.5), QPointF(7, 11), QPointF(21, 11), QPointF(17.5, 18.5),
                              QPointF(3.5, 18.5)]))


def _save(p):
    p.drawRoundedRect(QRectF(4, 4, 16, 16), 2.5, 2.5)
    p.drawRect(QRectF(8, 4, 8, 5))
    p.drawRect(QRectF(7.5, 13, 9, 7))


def _undo(p):
    path = QPainterPath(QPointF(19, 18.5))
    path.cubicTo(19, 11.5, 13.5, 9.5, 6.5, 10.5)
    p.drawPath(path)
    p.drawPolyline(QPolygonF([QPointF(10.5, 5.5), QPointF(5.5, 10.6), QPointF(10.5, 15.2)]))


def _redo(p):
    path = QPainterPath(QPointF(5, 18.5))
    path.cubicTo(5, 11.5, 10.5, 9.5, 17.5, 10.5)
    p.drawPath(path)
    p.drawPolyline(QPolygonF([QPointF(13.5, 5.5), QPointF(18.5, 10.6), QPointF(13.5, 15.2)]))


def _fit(p):
    for sx, sy in ((1, 1), (-1, 1), (1, -1), (-1, -1)):
        x, y = 12 - sx * 8, 12 - sy * 8
        p.drawPolyline(QPolygonF([QPointF(x + sx * 4.5, y), QPointF(x, y), QPointF(x, y + sy * 4.5)]))
    p.drawRect(QRectF(9, 9, 6, 6))


def _nest(p):
    for r in (QRectF(4, 4, 9, 7), QRectF(15, 4, 5, 7), QRectF(4, 13, 5, 7), QRectF(11, 13, 9, 7)):
        p.drawRoundedRect(r, 1.5, 1.5)


def _toolpath(p):
    p.drawRoundedRect(QRectF(3.5, 5.5, 17, 13), 3, 3)
    p.setPen(QPen(QColor(theme.get().accent), 1.7, Qt.DashLine, Qt.RoundCap, Qt.RoundJoin))
    p.drawRoundedRect(QRectF(7, 9, 10, 6), 1.5, 1.5)


def _recalc(p):
    p.drawArc(QRectF(4.5, 4.5, 15, 15), 40 * 16, 270 * 16)
    p.setBrush(_ink())
    p.drawPolygon(QPolygonF([QPointF(18.5, 3.5), QPointF(20.5, 9.5), QPointF(14.5, 8.5)]))


def _simulate(p):
    p.drawEllipse(QPointF(12, 12), 9, 9)
    p.setBrush(_ink())
    p.drawPolygon(QPolygonF([QPointF(10, 8), QPointF(16, 12), QPointF(10, 16)]))


def _gcode(p):
    p.drawPolyline(QPolygonF([QPointF(13, 3.5), QPointF(6, 3.5), QPointF(6, 20.5), QPointF(18, 20.5),
                              QPointF(18, 8.5), QPointF(13, 3.5), QPointF(13, 8.5), QPointF(18, 8.5)]))
    f = p.font()
    f.setPixelSize(8)
    f.setBold(True)
    p.setFont(f)
    p.drawText(QRectF(6, 10, 12, 10), Qt.AlignCenter, "G")


TOOL_ICONS = {"select": _select, "nodes": _nodes, "polyline": _polyline, "line": _line, "rectangle": _rect,
              "circle": _circle, "ellipse": _ellipse, "polygon": _polygon, "star": _star, "arc": _arc,
              "text": _text, "measure": _measure, "trim": _trim, "start": _start, "zoom": _zoom,
              "new": _new, "open": _open, "save": _save, "undo": _undo, "redo": _redo, "fit": _fit, "nest": _nest,
              "toolpath": _toolpath, "recalculate": _recalc, "simulate": _simulate, "gcode": _gcode}


def icon(name: str) -> QIcon:
    fn = TOOL_ICONS.get(name)
    return _icon(fn) if fn else QIcon()

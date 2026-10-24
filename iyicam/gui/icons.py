"""Small programmatically drawn toolbar icons (no external image files needed)."""
from __future__ import annotations

import math

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QIcon, QPainter, QPainterPath, QPen, QPixmap, QPolygonF

S = 24


def _icon(draw) -> QIcon:
    pm = QPixmap(S, S)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing)
    p.setPen(QPen(QColor("#2b3a55"), 1.7, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
    draw(p)
    p.end()
    return QIcon(pm)


def _select(p):
    p.setBrush(QColor("#2b3a55"))
    p.drawPolygon(QPolygonF([QPointF(6, 3), QPointF(6, 19), QPointF(10, 15), QPointF(13, 21), QPointF(15, 20),
                             QPointF(12, 14), QPointF(18, 14)]))


def _nodes(p):
    path = QPainterPath(QPointF(3, 19))
    path.cubicTo(6, 4, 16, 22, 21, 5)
    p.drawPath(path)
    p.setBrush(QColor("white"))
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
    p.setPen(QPen(QColor("#c0392b"), 1.7, Qt.DashLine))
    p.drawLine(QPointF(15, 18), QPointF(20, 18))


def _start(p):
    p.drawRoundedRect(QRectF(4, 6, 16, 12), 3, 3)
    p.setBrush(QColor("#c0392b"))
    p.setPen(QPen(QColor("#c0392b"), 1.2))
    p.drawEllipse(QPointF(12, 6), 2.6, 2.6)
    p.drawPolygon(QPolygonF([QPointF(15, 3.5), QPointF(19, 6), QPointF(15, 8.5)]))


def _zoom(p):
    p.drawEllipse(QPointF(10, 10), 6, 6)
    p.drawLine(QPointF(14.5, 14.5), QPointF(20, 20))


TOOL_ICONS = {"select": _select, "nodes": _nodes, "polyline": _polyline, "line": _line, "rectangle": _rect,
              "circle": _circle, "ellipse": _ellipse, "polygon": _polygon, "star": _star, "arc": _arc,
              "text": _text, "measure": _measure, "trim": _trim, "start": _start, "zoom": _zoom}


def icon(name: str) -> QIcon:
    fn = TOOL_ICONS.get(name)
    return _icon(fn) if fn else QIcon()

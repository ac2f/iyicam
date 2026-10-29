"""The cut order on the drawing: numbers where the tool goes down, the route between the cuts, arrows along the
cuts in the direction they are cut, the start points, the cut the cut order panel points at and the preview of
a cut order that is not applied yet."""
from __future__ import annotations

import math

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QFont, QPainter, QPen, QPicture, QPolygonF

from ..cam import cutinfo
from ..core.geom import Pt

CW_COLOR = "#e6550d"          # cut clockwise
CCW_COLOR = "#1b9e77"         # cut counter-clockwise
PREVIEW_COLOR = "#d6249f"
FOCUS_COLOR = "#ffcc00"
ARROW_SPACING_PX = 90.0
MAX_ARROWS_PER_CUT = 12
MANY_CUTS = 600               # beyond this the arrows are fewer (a big nest would be nothing but arrows)
MIN_ARROWS_PX = 28.0          # a cut smaller than this on the screen gets no arrows ...
MIN_NUMBER_PX = 16.0          # ... and no number (only the dot where the tool goes down)


def order_color(i: int, n: int) -> QColor:
    """Blue for the first cut, going through green and yellow to red for the last."""
    t = i / max(1, n - 1)
    return QColor.fromHsvF(0.62 * (1.0 - t), 0.75, 0.9)


def _arrow(p, tip: QPointF, ang: float, size: float, color: QColor) -> None:
    """A filled arrow head at ``tip`` pointing in the screen direction ``ang`` (radians, y down)."""
    left = tip + QPointF(-size * math.cos(ang - 0.45), -size * math.sin(ang - 0.45))
    right = tip + QPointF(-size * math.cos(ang + 0.45), -size * math.sin(ang + 0.45))
    p.setPen(Qt.NoPen)
    p.setBrush(color)
    p.drawPolygon(QPolygonF([tip, left, right]))


def _screen(canvas, pt: Pt) -> QPointF:
    return canvas.world_to_screen(*pt)


def paint_route(p, canvas, cuts: list[dict], home: Pt, color: QColor | None = None) -> None:
    """The rapid moves between the cuts as arrows (the colour goes from blue to red with the order)."""
    links = cutinfo.route(cuts, home)
    for i, (a, b) in enumerate(links):
        qa, qb = _screen(canvas, a), _screen(canvas, b)
        c = QColor(color) if color is not None else order_color(i, len(links))
        c.setAlpha(190)
        pen = QPen(c, 1.4, Qt.DashLine)
        p.setPen(pen)
        p.drawLine(qa, qb)
        dx, dy = qb.x() - qa.x(), qb.y() - qa.y()
        if math.hypot(dx, dy) > 14:
            mid = QPointF((qa.x() + qb.x()) / 2, (qa.y() + qb.y()) / 2)
            _arrow(p, mid + QPointF(dx, dy) / math.hypot(dx, dy) * 5, math.atan2(dy, dx), 8, c)


def paint_directions(p, canvas, cuts: list[dict], color: QColor | None = None) -> None:
    """Arrow heads along every cut, pointing the way the tool goes (orange: clockwise, green: counter-clockwise,
    open cuts: along the line)."""
    many = len(cuts) > MANY_CUTS
    for cut in cuts:
        path = cut.get("path")
        if path is None or not path.segs or cutinfo.cut_size(cut) * canvas.scale < MIN_ARROWS_PX:
            continue
        d = cutinfo.cut_direction(cut)
        c = QColor(color) if color is not None else QColor(CW_COLOR if d == "cw" else CCW_COLOR if d == "ccw" else "#3182bd")
        L = path.length()
        n = int(max(1, min(MAX_ARROWS_PER_CUT if not many else 2, round(L * canvas.scale / ARROW_SPACING_PX))))
        for k in range(n):
            s = L * (k + 0.5) / n
            q = _screen(canvas, path.point_at_length(s))
            t = path.tangent_at_length(s)
            _arrow(p, q, math.atan2(-t[1], t[0]), 9.0, c)


def paint_starts(p, canvas, cuts: list[dict], color: QColor | None = None) -> None:
    """Where every cut closes: green when the start point is a good one, orange when it is too close to a corner or
    to a cut area, violet (a square) when it was set by hand, grey when it is simply where it was drawn."""
    for cut in cuts:
        pt = cut.get("close") or cut.get("start")
        if pt is None or not cut.get("closed"):
            continue
        q = _screen(canvas, pt)
        quality = cutinfo.explain(cut)["quality"]
        fill, edge = {"hand": ("#9e9ac8", "#4a1486"), "good": ("#41ab5d", "#00441b"), "warn": ("#fd8d3c", "#7f2704"),
                      "plain": ("#bdbdbd", "#525252")}[quality]
        if color is not None:
            fill = color.name()
        p.setPen(QPen(QColor(edge), 1.3))
        p.setBrush(QColor(fill))
        if quality == "hand":
            p.drawRect(QRectF(q.x() - 4.5, q.y() - 4.5, 9, 9))
        elif quality == "warn":
            p.drawPolygon(QPolygonF([q + QPointF(0, -6), q + QPointF(6, 4), q + QPointF(-6, 4)]))
        else:
            p.drawEllipse(q, 4.0, 4.0)


def paint_numbers(p, canvas, cuts: list[dict], focus: str | None = None, color: QColor | None = None,
                  faded: bool = False) -> None:
    """The place in the order at the point where the tool goes down."""
    font = QFont(p.font())
    font.setPixelSize(10)
    font.setBold(True)
    p.setFont(font)
    n = len(cuts)
    for i, cut in enumerate(cuts):
        q = _screen(canvas, cut.get("entry") or cut["start"])
        text = str(cut["index"] + 1)
        w = 8 + 6 * len(text)
        big = cut["key"] == focus
        if not big and cutinfo.cut_size(cut) * canvas.scale < MIN_NUMBER_PX:
            p.setPen(QPen(QColor("#08306b"), 1))
            p.setBrush(order_color(i, n))
            p.drawEllipse(q, 2.5, 2.5)
            continue
        bad = cut.get("ok") is False
        fill = color if color is not None else order_color(i, n)
        fill = QColor(fill)
        if bad and color is None:
            fill = QColor("#fcbba1")
        if faded:
            fill.setAlpha(110)
        h = 17.0 if big else 13.0
        box = QRectF(q.x() + 4, q.y() - h - 2, w + (6 if big else 0), h)
        p.setPen(QPen(QColor("#000000" if big else "#08306b" if not bad else "#a50f15"), 2.0 if big else 1.0))
        p.setBrush(fill)
        p.drawRoundedRect(box, 3, 3)
        lum = 0.299 * fill.red() + 0.587 * fill.green() + 0.114 * fill.blue()
        p.setPen(QColor("#ffffff" if lum < 120 else "#111111"))
        p.drawText(box, Qt.AlignCenter, text)
        p.setPen(QPen(QColor("#08306b"), 1))
        p.setBrush(Qt.NoBrush)
        p.drawEllipse(q, 3, 3)


def paint_focus(p, canvas, cut: dict) -> None:
    """The cut the cut order panel points at: its whole path in a thick light line."""
    path = cut.get("path")
    if path is None or not path.segs:
        return
    pts = path.flatten(0.2 / max(canvas.scale, 1e-6) * 4)
    poly = QPolygonF([_screen(canvas, path.start)] + [_screen(canvas, q) for q in pts])
    c = QColor(FOCUS_COLOR)
    c.setAlpha(150)
    pen = QPen(c, 7)
    pen.setCapStyle(Qt.RoundCap)
    pen.setJoinStyle(Qt.RoundJoin)
    p.setPen(pen)
    p.setBrush(Qt.NoBrush)
    p.drawPolyline(poly)


def paint_ghosts(p, canvas, starts: dict) -> None:
    """From where a start point is now to where the preview would put it."""
    pen = QPen(QColor(PREVIEW_COLOR), 1.4, Qt.DotLine)
    for old, new in starts.values():
        a, b = _screen(canvas, old), _screen(canvas, new)
        p.setPen(pen)
        p.drawLine(a, b)
        p.setBrush(Qt.NoBrush)
        p.setPen(QPen(QColor("#525252"), 1.2))
        p.drawEllipse(a, 4.5, 4.5)


def _draw(p, canvas, tps: list) -> None:
    ctrl = canvas.ctrl
    s = ctrl.settings
    preview = ctrl.cut_preview
    home = ctrl.route_home()
    for tp in tps:
        cuts = cutinfo.real_cuts(tp.cuts)
        if not cuts:
            continue
        if preview is not None and preview.tid == tp.def_id:
            continue                                             # (the preview takes its place)
        if s.show_route:
            paint_route(p, canvas, cuts, home)
        if s.show_directions:
            paint_directions(p, canvas, cuts)
        if s.show_starts:
            paint_starts(p, canvas, cuts)
        focus = next((c for c in cuts if c["key"] == ctrl.focus_cut), None)
        if focus is not None:
            paint_focus(p, canvas, focus)
        if s.show_cut_order or focus is not None:
            paint_numbers(p, canvas, cuts, ctrl.focus_cut)
    if preview is not None:
        cuts = preview.cuts
        paint_route(p, canvas, cuts, home, QColor(PREVIEW_COLOR))
        paint_directions(p, canvas, cuts, QColor(PREVIEW_COLOR))
        paint_ghosts(p, canvas, preview.starts)
        paint_starts(p, canvas, cuts)
        paint_numbers(p, canvas, cuts, ctrl.focus_cut, QColor(PREVIEW_COLOR))


def paint_cut_view(p, canvas) -> None:
    """Everything of the cut order that the settings ask to show, in screen coordinates. It is drawn once per view of
    the drawing and calculation (a picture is kept): moving the mouse over a big job repaints at once."""
    ctrl = canvas.ctrl
    s = ctrl.settings
    preview = ctrl.cut_preview
    show = (s.show_cut_order, s.show_route, s.show_directions, s.show_starts)
    if not any(show) and preview is None and ctrl.focus_cut is None:
        return
    tps = [tp for tp in ctrl.sheet_toolpaths() if tp.cuts]
    key = (canvas.scale, canvas.cx, canvas.cy, canvas.width(), canvas.height(), show, ctrl.focus_cut, id(preview),
           tuple(id(tp) for tp in tps), tuple(ctrl.route_home()))
    cached = getattr(canvas, "_cut_cache", None)
    if cached is None or cached[0] != key:
        pic = QPicture()
        pp = QPainter(pic)
        pp.setRenderHints(p.renderHints())
        pp.setFont(p.font())
        _draw(pp, canvas, tps)
        pp.end()
        canvas._cut_cache = (key, pic, tps, preview)           # (the objects are kept: their ids stay theirs)
    p.save()
    p.resetTransform()
    p.drawPicture(0, 0, canvas._cut_cache[1])
    p.restore()

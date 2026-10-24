"""The 2D design view: sheet, grid, vectors, toolpaths and interactive tools."""
from __future__ import annotations

import math

from PySide6.QtCore import QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QBrush, QColor, QImage, QPainter, QPainterPath, QPen, QTransform
from PySide6.QtWidgets import QWidget

from ..core import clip
from ..core.document import VectorObject
from ..core.geom import Arc, BBox, Pt, dist
from .qtgeom import contours_path, toolpath_paths

TP_COLORS = ["#e6550d", "#3182bd", "#31a354", "#756bb1", "#de2d26", "#636363", "#fd8d3c", "#6baed6"]


class Canvas(QWidget):
    cursor_moved = Signal(float, float)
    prompt = Signal(str)
    tool_changed = Signal(str)

    PICK_PX = 6.0
    SNAP_PX = 9.0

    def __init__(self, ctrl, parent=None):
        super().__init__(parent)
        self.ctrl = ctrl
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.StrongFocus)
        self.setMinimumSize(300, 200)
        self.scale = 1.0
        self.cx, self.cy = 300.0, 200.0
        self._paths: dict[str, tuple[tuple, QPainterPath]] = {}
        self._tp_cache: list | None = None
        self._pan_from: QPointF | None = None
        self._space = False
        self.mouse_world: Pt = (0.0, 0.0)
        self.snap_marker: tuple[Pt, str] | None = None
        self.hover_id: str | None = None
        self.sim_image: QImage | None = None
        self.sim_rect: QRectF | None = None
        self.show_sim = False
        self.tool = None
        self.context_menu_hook = None
        ctrl.changed.connect(self._doc_changed)
        ctrl.selection_changed.connect(self.update)
        ctrl.toolpaths_changed.connect(self._tp_changed)
        ctrl.sheet_changed.connect(self._sheet_changed)
        ctrl.project_replaced.connect(lambda: self.zoom_extents())
        from .tools import SelectTool
        self.set_tool(SelectTool(self))

    # -- view transform ---------------------------------------------------------------------
    def world_to_screen(self, x: float, y: float) -> QPointF:
        return QPointF(self.width() / 2 + (x - self.cx) * self.scale, self.height() / 2 - (y - self.cy) * self.scale)

    def screen_to_world(self, p: QPointF) -> Pt:
        return ((p.x() - self.width() / 2) / self.scale + self.cx, -(p.y() - self.height() / 2) / self.scale + self.cy)

    def view_transform(self) -> QTransform:
        s = self.scale
        return QTransform(s, 0, 0, -s, self.width() / 2 - self.cx * s, self.height() / 2 + self.cy * s)

    def px(self, n: float) -> float:
        """``n`` screen pixels in world units."""
        return n / self.scale

    def zoom_extents(self) -> None:
        sh = self.ctrl.sheet
        bb = BBox(0, 0, sh.width, sh.height).union(sh.bbox()) if sh.vectors else BBox(0, 0, sh.width, sh.height)
        self.zoom_to(bb)

    def zoom_to(self, bb: BBox, margin: float = 0.06) -> None:
        w, h = max(bb.width, 1e-3), max(bb.height, 1e-3)
        W, H = max(1, self.width()), max(1, self.height())
        self.scale = min(W / (w * (1 + 2 * margin)), H / (h * (1 + 2 * margin)))
        self.cx, self.cy = bb.center
        self.update()

    def zoom_selection(self) -> None:
        bb = BBox()
        for o in self.ctrl.selected_objects():
            bb = bb.union(o.bbox())
        if not bb.empty:
            self.zoom_to(bb.inflated(max(bb.width, bb.height) * 0.1 + 1))

    def zoom_by(self, factor: float, anchor: QPointF | None = None) -> None:
        anchor = anchor or QPointF(self.width() / 2, self.height() / 2)
        wx, wy = self.screen_to_world(anchor)
        self.scale = max(1e-4, min(1e5, self.scale * factor))
        nx, ny = self.screen_to_world(anchor)
        self.cx += wx - nx
        self.cy += wy - ny
        self.update()

    # -- caches ----------------------------------------------------------------------------------
    def _doc_changed(self) -> None:
        self._paths.clear()
        self.update()

    def _tp_changed(self) -> None:
        self._tp_cache = None
        self.update()

    def _sheet_changed(self) -> None:
        self._paths.clear()
        self._tp_cache = None
        self.sim_image = None
        self.update()

    def invalidate(self, obj: VectorObject | None = None) -> None:
        if obj is None:
            self._paths.clear()
        else:
            self._paths.pop(obj.id, None)
        self.update()

    def obj_path(self, o: VectorObject) -> QPainterPath:
        key = tuple(id(c) for c in o.contours)
        hit = self._paths.get(o.id)
        if hit is None or hit[0] != key:
            hit = (key, contours_path(o.contours))
            self._paths[o.id] = hit
        return hit[1]

    # -- tools --------------------------------------------------------------------------------------
    def set_tool(self, tool) -> None:
        if self.tool is not None:
            self.tool.deactivate()
        self.tool = tool
        tool.activate()
        self.setCursor(tool.cursor)
        self.prompt.emit(tool.prompt)
        self.tool_changed.emit(tool.name)
        self.update()

    def cancel_tool(self) -> None:
        from .tools import SelectTool
        if not isinstance(self.tool, SelectTool):
            self.set_tool(SelectTool(self))
        else:
            self.tool.cancel()

    # -- hit testing / snapping --------------------------------------------------------------------
    def visible_objects(self) -> list[VectorObject]:
        proj = self.ctrl.project
        out = []
        for o in self.ctrl.sheet.vectors:
            l = proj.layer(o.layer_id)
            if l is None or l.visible:
                out.append(o)
        return out

    def hit(self, p: Pt, tol_px: float | None = None, editable_only: bool = True) -> VectorObject | None:
        tol = self.px(tol_px or self.PICK_PX)
        best, bd = None, math.inf
        for o in reversed(self.visible_objects()):
            if editable_only and not self.ctrl.editable(o):
                continue
            if not o.bbox().inflated(tol).contains(p):
                continue
            for c in o.contours:
                d = c.nearest(p)[3]
                if d < bd:
                    best, bd = o, d
        return best if bd <= tol else None

    def objects_in_rect(self, r: BBox, crossing: bool) -> list[VectorObject]:
        out = []
        rect = r.corners()
        for o in self.visible_objects():
            if not self.ctrl.editable(o):
                continue
            bb = o.bbox()
            if r.contains_box(bb):
                out.append(o)
            elif crossing and r.intersects(bb):
                lines = [c.flatten(self.px(1.0)) + ([c.start] if c.closed else []) for c in o.contours]
                if clip.clip_lines(lines, [rect]):
                    out.append(o)
        return out

    def snap(self, p: Pt, exclude: set[str] | frozenset = frozenset(), raw: bool = False) -> Pt:
        """Snap to nodes / midpoints / centres / grid; updates the snap marker."""
        self.snap_marker = None
        if raw:
            return p
        s = self.ctrl.settings
        tol = self.px(self.SNAP_PX)
        best, bd, kind = None, tol, ""
        if s.snap_objects:
            for o in self.visible_objects():
                if o.id in exclude or not o.bbox().inflated(tol).contains(p):
                    continue
                for c in o.contours:
                    for seg in c.segs:
                        for q, k in ((seg.a, "node"), (seg.b, "node")):
                            d = dist(p, q)
                            if d < bd:
                                best, bd, kind = q, d, k
                        m = seg.point_at(0.5)
                        d = dist(p, m)
                        if d < bd * 0.8:
                            best, bd, kind = m, d, "mid"
                        if isinstance(seg, Arc):
                            cc = seg.center
                            d = dist(p, cc)
                            if d < bd:
                                best, bd, kind = cc, d, "centre"
            sh = self.ctrl.sheet
            for q in BBox(0, 0, sh.width, sh.height).corners() + [(sh.width / 2, sh.height / 2)]:
                d = dist(p, q)
                if d < bd:
                    best, bd, kind = q, d, "sheet"
        if best is not None:
            self.snap_marker = (best, kind)
            return best
        if s.snap_grid and s.grid > 0:
            g = s.grid
            q = (round(p[0] / g) * g, round(p[1] / g) * g)
            self.snap_marker = (q, "grid")
            return q
        return p

    # -- painting -------------------------------------------------------------------------------------
    def grid_step(self) -> float:
        g = self.ctrl.settings.grid if self.ctrl.settings.grid > 0 else 10.0
        while g * self.scale < 8:
            g *= 5 if str(g)[0] in "12" else 2
        while g * self.scale > 120:
            g /= 5 if str(g)[0] in "15" else 2
        return g

    def paintEvent(self, ev) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.fillRect(self.rect(), QColor("#d8dce2"))
        sh = self.ctrl.sheet
        T = self.view_transform()
        # sheet
        p.setTransform(T)
        p.fillRect(QRectF(0, 0, sh.width, sh.height), QColor("#fbf7ee"))
        if self.show_sim and self.sim_image is not None and self.sim_rect is not None:
            p.save()
            p.translate(self.sim_rect.x(), self.sim_rect.y() + self.sim_rect.height())
            p.scale(self.sim_rect.width() / self.sim_image.width(), -self.sim_rect.height() / self.sim_image.height())
            p.drawImage(0, 0, self.sim_image)
            p.restore()
        self._paint_grid(p, sh)
        pen = QPen(QColor("#8a7f6b"), 1.2)
        pen.setCosmetic(True)
        p.setPen(pen)
        p.setBrush(Qt.NoBrush)
        p.drawRect(QRectF(0, 0, sh.width, sh.height))
        self._paint_origin(p, sh)
        # vectors
        sel = set(self.ctrl.selection)
        proj = self.ctrl.project
        for o in self.ctrl.sheet.vectors:
            layer = proj.layer(o.layer_id)
            if layer is not None and not layer.visible:
                continue
            path = self.obj_path(o)
            color = QColor(layer.color if layer else "#000000")
            if layer is not None and layer.locked:
                color.setAlpha(110)
            if o.id in sel:
                pen = QPen(QColor("#e31a1c"), 2.0)
                pen.setStyle(Qt.DashLine)
            elif o.id == self.hover_id:
                pen = QPen(color.lighter(130), 2.4)
            else:
                pen = QPen(color, 1.3)
            pen.setCosmetic(True)
            p.setPen(pen)
            p.drawPath(path)
        # toolpaths
        if self.ctrl.settings.show_toolpaths:
            self._paint_toolpaths(p)
        # start points / directions of selected vectors
        p.resetTransform()
        for o in self.ctrl.selected_objects():
            for c in o.contours:
                if c.segs:
                    self._paint_start(p, c, self.ctrl.settings.show_directions or len(sel) < 50)
        # tool overlay
        if self.tool is not None:
            self.tool.paint(p)
        if self.snap_marker is not None:
            (x, y), kind = self.snap_marker
            q = self.world_to_screen(x, y)
            p.setPen(QPen(QColor("#ff00aa"), 1.5))
            p.setBrush(Qt.NoBrush)
            if kind == "grid":
                p.drawLine(q + QPointF(-5, 0), q + QPointF(5, 0))
                p.drawLine(q + QPointF(0, -5), q + QPointF(0, 5))
            elif kind == "centre":
                p.drawEllipse(q, 5, 5)
            elif kind == "mid":
                p.drawPolygon([q + QPointF(0, -6), q + QPointF(6, 4), q + QPointF(-6, 4)])
            else:
                p.drawRect(QRectF(q.x() - 5, q.y() - 5, 10, 10))
        p.end()

    def _paint_grid(self, p: QPainter, sh) -> None:
        g = self.grid_step()
        if g * self.scale < 6:
            return
        x0, y0 = self.screen_to_world(QPointF(0, self.height()))
        x1, y1 = self.screen_to_world(QPointF(self.width(), 0))
        x0, y0 = max(0.0, x0), max(0.0, y0)
        x1, y1 = min(sh.width, x1), min(sh.height, y1)
        if x1 <= x0 or y1 <= y0:
            return
        minor = QPen(QColor(0, 0, 0, 22), 1)
        major = QPen(QColor(0, 0, 0, 45), 1)
        minor.setCosmetic(True)
        major.setCosmetic(True)
        i0, i1 = math.ceil(x0 / g), math.floor(x1 / g)
        for i in range(i0, i1 + 1):
            p.setPen(major if i % 10 == 0 else minor)
            p.drawLine(QPointF(i * g, y0), QPointF(i * g, y1))
        j0, j1 = math.ceil(y0 / g), math.floor(y1 / g)
        for j in range(j0, j1 + 1):
            p.setPen(major if j % 10 == 0 else minor)
            p.drawLine(QPointF(x0, j * g), QPointF(x1, j * g))

    def _paint_origin(self, p: QPainter, sh) -> None:
        from ..post.engine import ORIGINS
        fx, fy = ORIGINS.get(self.ctrl.project.material.xy_origin, (0, 0))
        o = (fx * sh.width, fy * sh.height)
        p.save()
        p.resetTransform()
        q = self.world_to_screen(*o)
        p.setPen(QPen(QColor("#c00000"), 2))
        p.drawLine(q, q + QPointF(28, 0))
        p.setPen(QPen(QColor("#008000"), 2))
        p.drawLine(q, q + QPointF(0, -28))
        p.setPen(QPen(QColor("#303030"), 1))
        p.drawEllipse(q, 3, 3)
        p.restore()

    def _paint_start(self, p: QPainter, c, arrow: bool) -> None:
        q = self.world_to_screen(*c.start)
        p.setPen(QPen(QColor("#1a9641"), 1))
        p.setBrush(QBrush(QColor("#1a9641")))
        p.drawEllipse(q, 3.5, 3.5)
        if not arrow:
            return
        seg = c.segs[0]
        m = seg.point_at(0.5)
        t = seg.tangent_at(0.5)
        qm = self.world_to_screen(*m)
        ang = math.atan2(-t[1], t[0])
        a1 = qm + QPointF(-9 * math.cos(ang - 0.45), -9 * math.sin(ang - 0.45))
        a2 = qm + QPointF(-9 * math.cos(ang + 0.45), -9 * math.sin(ang + 0.45))
        p.drawPolygon([qm, a1, a2])

    def _paint_toolpaths(self, p: QPainter) -> None:
        if self._tp_cache is None:
            self._tp_cache = []
            ctrl = self.ctrl
            idx = {t.id: i for i, t in enumerate(ctrl.project.toolpaths)}
            for tp in ctrl.sheet_toolpaths() + list(ctrl.backplot):
                feed, rapid, drills = toolpath_paths(tp)
                color = "#8e44ad" if tp.op == "gcode" else TP_COLORS[idx.get(tp.def_id, 0) % len(TP_COLORS)]
                st = tp.stats()
                width = tp.tool.radius_at_depth(-st.zmin) * 2 if tp.tool.is_tapered else tp.tool.diameter
                self._tp_cache.append((feed, rapid, drills, color, width))
        for feed, rapid, drills, color, width in self._tp_cache:
            c = QColor(color)
            if self.ctrl.settings.show_tool_width:
                wc = QColor(c)
                wc.setAlpha(60)
                pen = QPen(wc, width)
                pen.setCapStyle(Qt.RoundCap)
                pen.setJoinStyle(Qt.RoundJoin)
                p.setPen(pen)
                p.drawPath(feed)
            pen = QPen(c, 1.0)
            pen.setCosmetic(True)
            p.setPen(pen)
            p.drawPath(feed)
            rp = QPen(QColor(220, 30, 30, 140), 0.8, Qt.DashLine)
            rp.setCosmetic(True)
            p.setPen(rp)
            p.drawPath(rapid)
            if drills:
                p.setPen(pen)
                r = self.px(5)
                for x, y in drills:
                    p.drawEllipse(QPointF(x, y), r, r)
                    p.drawLine(QPointF(x - r, y), QPointF(x + r, y))
                    p.drawLine(QPointF(x, y - r), QPointF(x, y + r))

    # -- events ----------------------------------------------------------------------------------------
    def resizeEvent(self, ev) -> None:
        if ev.oldSize().width() <= 0:
            self.zoom_extents()
        super().resizeEvent(ev)

    def wheelEvent(self, ev) -> None:
        d = ev.angleDelta().y()
        if d:
            self.zoom_by(1.2 ** (d / 120.0), ev.position())

    def _world(self, ev) -> Pt:
        return self.screen_to_world(ev.position())

    def mousePressEvent(self, ev) -> None:
        self.setFocus()
        if ev.button() == Qt.MiddleButton or (ev.button() == Qt.LeftButton and self._space):
            self._pan_from = ev.position()
            self.setCursor(Qt.ClosedHandCursor)
            return
        self.tool.press(ev, self._world(ev))
        self.update()

    def mouseMoveEvent(self, ev) -> None:
        w = self._world(ev)
        self.mouse_world = w
        if self._pan_from is not None:
            d = ev.position() - self._pan_from
            self._pan_from = ev.position()
            self.cx -= d.x() / self.scale
            self.cy += d.y() / self.scale
            self.update()
            return
        self.cursor_moved.emit(*w)
        self.tool.move(ev, w)
        self.update()

    def mouseReleaseEvent(self, ev) -> None:
        if self._pan_from is not None and ev.button() in (Qt.MiddleButton, Qt.LeftButton):
            self._pan_from = None
            self.setCursor(self.tool.cursor)
            return
        self.tool.release(ev, self._world(ev))
        self.update()

    def mouseDoubleClickEvent(self, ev) -> None:
        self.tool.double_click(ev, self._world(ev))
        self.update()

    def keyPressEvent(self, ev) -> None:
        if ev.key() == Qt.Key_Space and not ev.isAutoRepeat():
            self._space = True
            self.setCursor(Qt.OpenHandCursor)
            return
        if self.tool.key(ev):
            self.update()
            return
        k = ev.key()
        if k == Qt.Key_Escape:
            self.cancel_tool()
        elif k in (Qt.Key_F, Qt.Key_Home):
            self.zoom_extents()
        elif k in (Qt.Key_Plus, Qt.Key_Equal):
            self.zoom_by(1.25)
        elif k == Qt.Key_Minus:
            self.zoom_by(0.8)
        else:
            super().keyPressEvent(ev)
            return
        self.update()

    def keyReleaseEvent(self, ev) -> None:
        if ev.key() == Qt.Key_Space and not ev.isAutoRepeat():
            self._space = False
            self.setCursor(self.tool.cursor)
            return
        super().keyReleaseEvent(ev)

    def contextMenuEvent(self, ev) -> None:
        pos = QPointF(ev.pos())
        if self.tool.context_menu(pos, self.screen_to_world(pos)):
            return
        if self.context_menu_hook is not None:
            self.context_menu_hook(ev.globalPos())

    def leaveEvent(self, ev) -> None:
        self.hover_id = None
        self.snap_marker = None
        self.update()

"""Interactive canvas tools: selection/transform, node editing, drawing and measuring."""
from __future__ import annotations

import math

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QBrush, QColor, QPainter, QPen, QTransform
from PySide6.QtWidgets import QMenu

from ..core import shapes, vectorops
from ..core.document import VectorObject
from ..core.geom import Arc, BBox, Bezier, Contour, Line, Pt, Transform, add, dist, sub
from . import menus
from .qtgeom import contours_path


def qtransform(T: Transform) -> QTransform:
    return QTransform(T.a, T.b, T.c, T.d, T.e, T.f)


class CanvasTool:
    name = "tool"
    prompt = ""
    cursor = Qt.CrossCursor

    def __init__(self, canvas):
        self.canvas = canvas
        self.ctrl = canvas.ctrl

    def activate(self) -> None:
        pass

    def deactivate(self) -> None:
        pass

    def cancel(self) -> None:
        pass

    def press(self, ev, w: Pt) -> None:
        pass

    def move(self, ev, w: Pt) -> None:
        pass

    def release(self, ev, w: Pt) -> None:
        pass

    def double_click(self, ev, w: Pt) -> None:
        pass

    def key(self, ev) -> bool:
        return False

    def paint(self, p: QPainter) -> None:
        pass

    def context_menu(self, pos, w: Pt) -> bool:
        return False

    # helpers
    def s(self, pt: Pt) -> QPointF:
        return self.canvas.world_to_screen(*pt)

    def say(self, text: str) -> None:
        self.canvas.prompt.emit(text)


def _sel_bbox(objs) -> BBox:
    bb = BBox()
    for o in objs:
        bb = bb.union(o.bbox())
    return bb


# ---------------------------------------------------------------------------
# selection / transform
# ---------------------------------------------------------------------------

class SelectTool(CanvasTool):
    name = "select"
    prompt = ("Click to select (Shift: add, Ctrl: toggle). Drag to move (Ctrl+drag: copy), handles to "
              "scale/rotate, double-click to edit nodes. Arrows nudge. Transform panel: Alt+F7..F11.")
    cursor = Qt.ArrowCursor
    HANDLE = 5.0

    def __init__(self, canvas):
        super().__init__(canvas)
        self.mode: str | None = None
        self.start: Pt | None = None
        self.cur: Pt | None = None
        self.anchor: Pt | None = None
        self.preview: Transform | None = None
        self.press_screen: QPointF | None = None
        self.copying = False                  # Ctrl was down when the drag began: it makes a copy
        self.unselect_on_click: str | None = None   # Ctrl+click on a selected vector takes it out when it is only a click

    def cancel(self) -> None:
        self.mode = None
        self.preview = None
        self.copying = False
        self.unselect_on_click = None

    def _handles(self) -> dict[str, QPointF]:
        objs = [o for o in self.ctrl.selected_objects() if self.ctrl.editable(o)]
        if not objs:
            return {}
        bb = _sel_bbox(objs)
        x0, y0, x1, y1 = bb.xmin, bb.ymin, bb.xmax, bb.ymax
        xm, ym = (x0 + x1) / 2, (y0 + y1) / 2
        h = {"sw": (x0, y0), "se": (x1, y0), "ne": (x1, y1), "nw": (x0, y1),
             "s": (xm, y0), "e": (x1, ym), "n": (xm, y1), "w": (x0, ym)}
        out = {k: self.s(v) for k, v in h.items()}
        out["rot"] = self.s((xm, y1)) + QPointF(0, -26)
        return out

    def _handle_at(self, pos: QPointF) -> str | None:
        for k, q in self._handles().items():
            if abs(q.x() - pos.x()) <= self.HANDLE + 2 and abs(q.y() - pos.y()) <= self.HANDLE + 2:
                return k
        return None

    def press(self, ev, w: Pt) -> None:
        if ev.button() != Qt.LeftButton:
            return
        self.press_screen = ev.position()
        self.start = w
        self.cur = w
        self.copying = False
        self.unselect_on_click = None
        h = self._handle_at(ev.position())
        if h is not None:
            self.mode = "rotate" if h == "rot" else "scale:" + h
            self.bb = _sel_bbox([o for o in self.ctrl.selected_objects() if self.ctrl.editable(o)])
            return
        obj = self.canvas.hit(w)
        mods = ev.modifiers()
        if obj is not None:
            if mods & Qt.ShiftModifier:
                self.ctrl.select([obj.id], "add")
            elif mods & Qt.ControlModifier:
                # Ctrl+click toggles, Ctrl+drag copies: which of the two it is shows when the mouse moves
                self.copying = True
                if obj.id in self.ctrl.selection:
                    self.unselect_on_click = obj.id
                else:
                    self.ctrl.select([obj.id], "add")
            elif obj.id not in self.ctrl.selection:
                self.ctrl.select([obj.id])
            self.mode = "move"
            self.anchor = self.canvas.snap(w)
            return
        self.mode = "band"

    def move(self, ev, w: Pt) -> None:
        cv = self.canvas
        if self.mode is None:
            o = cv.hit(w)
            cv.hover_id = o.id if o else None
            h = self._handle_at(ev.position())
            if h == "rot":
                cv.setCursor(Qt.CrossCursor)
            elif h in ("ne", "sw"):
                cv.setCursor(Qt.SizeBDiagCursor)
            elif h in ("nw", "se"):
                cv.setCursor(Qt.SizeFDiagCursor)
            elif h in ("n", "s"):
                cv.setCursor(Qt.SizeVerCursor)
            elif h in ("e", "w"):
                cv.setCursor(Qt.SizeHorCursor)
            elif o is not None and o.id in self.ctrl.selection:
                cv.setCursor(Qt.SizeAllCursor)
            else:
                cv.setCursor(Qt.ArrowCursor)
            cv.snap_marker = None
            return
        self.cur = w
        moved = self.press_screen is not None and (ev.position() - self.press_screen).manhattanLength() > 3
        if not moved:
            return
        if self.mode == "move":
            t = cv.snap(w, exclude=set(self.ctrl.selection))
            if ev.modifiers() & Qt.ShiftModifier:  # constrain to horizontal / vertical
                dx, dy = t[0] - self.anchor[0], t[1] - self.anchor[1]
                t = (t[0], self.anchor[1]) if abs(dx) >= abs(dy) else (self.anchor[0], t[1])
            self.preview = Transform.translate(t[0] - self.anchor[0], t[1] - self.anchor[1])
            copy = self.copying or bool(ev.modifiers() & Qt.ControlModifier)
            cv.setCursor(Qt.DragCopyCursor if copy else Qt.SizeAllCursor)
            self.say(f"{'Copy' if copy else 'Move'} dX {self.ctrl.units.fmt(t[0] - self.anchor[0])}  "
                     f"dY {self.ctrl.units.fmt(t[1] - self.anchor[1])}")
        elif self.mode == "rotate":
            c = self.bb.center
            a = math.atan2(w[1] - c[1], w[0] - c[0]) - math.atan2(self.start[1] - c[1], self.start[0] - c[0])
            deg = math.degrees(a)
            if ev.modifiers() & Qt.ShiftModifier:
                deg = round(deg / 15) * 15
            self.preview = Transform.rotate(math.radians(deg), c)
            self.say(f"Rotate {deg:.1f}° (Shift: 15° steps)")
        elif self.mode and self.mode.startswith("scale:"):
            self.preview = self._scale_transform(self.mode[6:], cv.snap(w), ev.modifiers())
        elif self.mode == "band":
            cv.snap_marker = None

    def _scale_transform(self, h: str, w: Pt, mods) -> Transform:
        bb = self.bb
        about_centre = bool(mods & Qt.ControlModifier)
        # fixed point: the opposite side (or the centre with Ctrl)
        ax = bb.xmax if "w" in h else bb.xmin if "e" in h else None
        ay = bb.ymax if "s" in h else bb.ymin if "n" in h else None
        if about_centre:
            ax = bb.center[0] if ax is not None else None
            ay = bb.center[1] if ay is not None else None
        hx = bb.xmin if "w" in h else bb.xmax
        hy = bb.ymin if "s" in h else bb.ymax
        sx = (w[0] - ax) / (hx - ax) if ax is not None and abs(hx - ax) > 1e-9 else 1.0
        sy = (w[1] - ay) / (hy - ay) if ay is not None and abs(hy - ay) > 1e-9 else 1.0
        if len(h) == 2 and not (mods & Qt.ShiftModifier):
            s = sx if abs(sx) >= abs(sy) else sy
            sx = sy = s
        sx = sx if abs(sx) > 1e-3 else 1e-3
        sy = sy if abs(sy) > 1e-3 else 1e-3
        piv = (ax if ax is not None else bb.center[0], ay if ay is not None else bb.center[1])
        self.say(f"Scale {sx * 100:.1f}% x {sy * 100:.1f}%  (Shift: free, Ctrl: about centre)")
        return Transform.scale(sx, sy, piv)

    def release(self, ev, w: Pt) -> None:
        mode, T = self.mode, self.preview
        copy = mode == "move" and (self.copying or bool(ev.modifiers() & Qt.ControlModifier))
        unselect = self.unselect_on_click
        self.mode = None
        self.preview = None
        self.copying = False
        self.unselect_on_click = None
        self.canvas.setCursor(self.cursor)
        if ev.button() != Qt.LeftButton:
            return
        if mode == "move" and T is None and unselect is not None:       # (only a Ctrl+click)
            self.ctrl.select([unselect], "remove")
            self.say(self.prompt)
            return
        if mode == "band":
            a, b = self.start, w
            far = self.press_screen is not None and (ev.position() - self.press_screen).manhattanLength() > 3
            if far:
                r = BBox.of_points([a, b])
                crossing = b[0] < a[0]
                ids = [o.id for o in self.canvas.objects_in_rect(r, crossing)]
                m = ev.modifiers()
                self.ctrl.select(ids, "add" if m & Qt.ShiftModifier else "toggle" if m & Qt.ControlModifier else "replace")
            elif not (ev.modifiers() & (Qt.ShiftModifier | Qt.ControlModifier)):
                self.ctrl.select([])
            return
        if T is not None and copy:
            self.ctrl.transform_selection(T, 1, "Copy")      # (the copies are selected, the originals stay)
        elif T is not None:
            label = {"move": "Move"}.get(mode, "Rotate" if mode == "rotate" else "Scale")
            objs = [o for o in self.ctrl.selected_objects() if self.ctrl.editable(o)]
            with self.ctrl.edit(label):
                for o in objs:
                    o.transform(T)
        self.say(self.prompt)

    def double_click(self, ev, w: Pt) -> None:
        obj = self.canvas.hit(w)
        if obj is not None:
            self.ctrl.select([obj.id])
            self.canvas.set_tool(NodeTool(self.canvas))

    def key(self, ev) -> bool:
        k = ev.key()
        if k in (Qt.Key_Delete, Qt.Key_Backspace):
            self.ctrl.delete_selection()
            return True
        steps = {Qt.Key_Left: (-1, 0), Qt.Key_Right: (1, 0), Qt.Key_Up: (0, 1), Qt.Key_Down: (0, -1)}
        if k in steps and self.ctrl.selection:
            g = self.ctrl.settings.grid if self.ctrl.settings.grid > 0 else 1.0
            if ev.modifiers() & Qt.ShiftModifier:
                g *= 10
            elif ev.modifiers() & Qt.ControlModifier:
                g /= 10
            dx, dy = steps[k]
            with self.ctrl.edit("Nudge"):
                for o in self.ctrl.selected_objects():
                    if self.ctrl.editable(o):
                        o.transform(Transform.translate(dx * g, dy * g))
            return True
        return False

    def paint(self, p: QPainter) -> None:
        cv = self.canvas
        if self.mode == "band" and self.start and self.cur:
            a, b = self.s(self.start), self.s(self.cur)
            crossing = self.cur[0] < self.start[0]
            col = QColor("#2ca02c") if crossing else QColor("#1f78b4")
            pen = QPen(col, 1, Qt.DashLine if crossing else Qt.SolidLine)
            p.setPen(pen)
            fill = QColor(col)
            fill.setAlpha(30)
            p.setBrush(fill)
            p.drawRect(QRectF(a, b).normalized())
        if self.preview is not None:
            p.save()
            p.setTransform(qtransform(self.preview) * cv.view_transform())
            pen = QPen(QColor("#1f78b4"), 1.5)
            pen.setCosmetic(True)
            p.setPen(pen)
            p.setBrush(Qt.NoBrush)
            for o in self.ctrl.selected_objects():
                p.drawPath(cv.obj_path(o))
            p.restore()
            return
        hs = self._handles()
        if hs:
            p.setPen(QPen(QColor("#1f78b4"), 1, Qt.DotLine))
            p.setBrush(Qt.NoBrush)
            p.drawRect(QRectF(hs["nw"], hs["se"]).normalized())
            p.drawLine(hs["n"], hs["rot"])
            p.setPen(QPen(QColor("#1f78b4"), 1))
            p.setBrush(QBrush(QColor("white")))
            for k, q in hs.items():
                if k == "rot":
                    p.drawEllipse(q, self.HANDLE, self.HANDLE)
                else:
                    p.drawRect(QRectF(q.x() - self.HANDLE, q.y() - self.HANDLE, 2 * self.HANDLE, 2 * self.HANDLE))


# ---------------------------------------------------------------------------
# node editing
# ---------------------------------------------------------------------------

class NodeTool(CanvasTool):
    name = "nodes"
    prompt = ("Node editing: drag nodes and bezier handles, double-click a span to insert a node, Del deletes "
              "selected nodes, right-click for span/node options, Esc to finish.")
    cursor = Qt.ArrowCursor
    R = 4.0

    def __init__(self, canvas):
        super().__init__(canvas)
        self.sel: set[tuple[str, int, int]] = set()
        self.drag = None
        self.span = None
        self.orig: dict = {}
        self.moved = False

    def targets(self) -> list[VectorObject]:
        return [o for o in self.ctrl.selected_objects() if self.ctrl.editable(o)]

    def _pick(self, pos: QPointF):
        best = None
        tol = 7.0
        for o in self.targets():
            for ci, c in enumerate(o.contours):
                for si, sg in enumerate(c.segs):
                    if isinstance(sg, Bezier):
                        for which, cp in ((1, sg.c1), (2, sg.c2)):
                            q = self.s(cp)
                            d = math.hypot(q.x() - pos.x(), q.y() - pos.y())
                            if d <= tol and (best is None or d < best[0]):
                                best = (d, ("ctrl", o.id, ci, si, which))
                for ni, pt in enumerate(c.nodes()):
                    q = self.s(pt)
                    d = math.hypot(q.x() - pos.x(), q.y() - pos.y())
                    if d <= tol and (best is None or d < best[0] or best[1][0] == "ctrl" and d < best[0] + 2):
                        best = (d, ("node", o.id, ci, ni))
        if best:
            return best[1]
        w = self.canvas.screen_to_world(pos)
        tolw = self.canvas.px(6)
        for o in self.targets():
            for ci, c in enumerate(o.contours):
                si, t, q, d = c.nearest(w)
                if d <= tolw:
                    return ("span", o.id, ci, si, t)
        return None

    def _obj(self, oid: str) -> VectorObject | None:
        return self.ctrl.sheet.vector(oid)

    def press(self, ev, w: Pt) -> None:
        if ev.button() != Qt.LeftButton:
            return
        hit = self._pick(ev.position())
        self.moved = False
        if hit is None:
            obj = self.canvas.hit(w)
            self.sel.clear()
            self.span = None
            if obj is not None:
                self.ctrl.select([obj.id], "add" if ev.modifiers() & Qt.ShiftModifier else "replace")
            return
        if hit[0] == "span":
            self.span = hit
            self.sel.clear()
            return
        self.span = None
        if hit[0] == "node":
            key = hit[1:4]
            if ev.modifiers() & Qt.ShiftModifier:
                self.sel ^= {key}
            elif key not in self.sel:
                self.sel = {key}
        self.drag = hit
        self.start = w
        self.ctrl.checkpoint("Edit nodes")
        self.orig = {}
        for (oid, ci, ni) in (self.sel if hit[0] == "node" else {hit[1:4]}):
            o = self._obj(oid)
            if o is not None:
                self.orig[(oid, ci)] = o.contours[ci]

    def move(self, ev, w: Pt) -> None:
        if self.drag is None:
            h = self._pick(ev.position())
            self.canvas.setCursor(Qt.SizeAllCursor if h and h[0] != "span" else
                                  Qt.PointingHandCursor if h else Qt.ArrowCursor)
            return
        kind = self.drag[0]
        oid, ci = self.drag[1], self.drag[2]
        o = self._obj(oid)
        if o is None:
            return
        target = self.canvas.snap(w, exclude={oid})
        self.moved = True
        if kind == "ctrl":
            si, which = self.drag[3], self.drag[4]
            o.contours[ci] = vectorops.move_control(self.orig[(oid, ci)], si, which, target)
        else:
            ni = self.drag[3]
            base = self.orig[(oid, ci)].nodes()[ni]
            dx, dy = target[0] - base[0], target[1] - base[1]
            for (soid, sci) in self.orig:
                c = self.orig[(soid, sci)]
                nodes = c.nodes()
                for (koid, kci, kni) in self.sel:
                    if koid == soid and kci == sci:
                        c = vectorops.move_node(c, kni, (nodes[kni][0] + dx, nodes[kni][1] + dy))
                so = self._obj(soid)
                so.contours[sci] = c
                self.canvas.invalidate(so)
        self.canvas.invalidate(o)

    def release(self, ev, w: Pt) -> None:
        if self.drag is not None:
            self.drag = None
            if self.moved:
                self.ctrl.notify()
            else:
                self.ctrl.undo_stack.discard_last()

    def double_click(self, ev, w: Pt) -> None:
        hit = self._pick(ev.position())
        if hit and hit[0] == "span":
            self._apply(hit[1], hit[2], lambda c: vectorops.insert_node(c, hit[3], hit[4]), "Insert node")

    def _apply(self, oid: str, ci: int, fn, label: str) -> None:
        o = self._obj(oid)
        if o is None:
            return
        with self.ctrl.edit(label):
            res = fn(o.contours[ci])
            if isinstance(res, list):
                o.contours[ci:ci + 1] = res
            else:
                o.contours[ci] = res
            o.contours = list(o.contours)
        self.sel.clear()
        self.span = None

    def delete_nodes(self) -> None:
        if not self.sel:
            return
        with self.ctrl.edit("Delete nodes"):
            by: dict[tuple[str, int], list[int]] = {}
            for oid, ci, ni in self.sel:
                by.setdefault((oid, ci), []).append(ni)
            for (oid, ci), nodes in by.items():
                o = self._obj(oid)
                c = o.contours[ci]
                for ni in sorted(nodes, reverse=True):
                    c = vectorops.delete_node(c, ni)
                o.contours[ci] = c
                o.contours = list(o.contours)
        self.sel.clear()

    def key(self, ev) -> bool:
        if ev.key() in (Qt.Key_Delete, Qt.Key_Backspace):
            self.delete_nodes()
            return True
        return False

    def context_menu(self, pos, w: Pt) -> bool:
        hit = self._pick(pos)
        if hit is None:
            return False
        m = QMenu(self.canvas)
        oid, ci = hit[1], hit[2]
        o = self._obj(oid)
        c = o.contours[ci]
        if hit[0] == "node":
            ni = hit[3]
            m.addAction("Set as start point", lambda: self._apply(oid, ci, lambda c: vectorops.set_start_node(c, ni),
                                                                  "Set start point"))
            m.addAction("Delete node", lambda: (self.sel.clear(), self.sel.add((oid, ci, ni)), self.delete_nodes()))
            seg = ni if ni < len(c.segs) else len(c.segs) - 1
            t = 0.0 if ni < len(c.segs) else 1.0
            m.addAction("Break at node", lambda: self._apply(oid, ci, lambda c: vectorops.break_at(c, seg, t),
                                                             "Break vector"))
        elif hit[0] == "span":
            si, t = hit[3], hit[4]
            m.addAction("Insert node here", lambda: self._apply(oid, ci, lambda c: vectorops.insert_node(c, si, t),
                                                                "Insert node"))
            m.addAction("Set start point here", lambda: self._apply(
                oid, ci, lambda c: vectorops.set_start_point(c, c.segs[si].point_at(t)), "Set start point"))
            m.addAction("Break here", lambda: self._apply(oid, ci, lambda c: vectorops.break_at(c, si, t), "Break vector"))
            m.addSeparator()
            for kind, label in (("line", "Convert span to line"), ("arc", "Convert span to arc"),
                                ("bezier", "Convert span to bezier")):
                m.addAction(label, lambda k=kind: self._apply(oid, ci, lambda c: vectorops.convert_segment(c, si, k),
                                                              "Convert span"))
        else:
            return False
        m.addSeparator()
        m.addAction("Reverse direction", lambda: self._apply(oid, ci, lambda c: c.reversed(), "Reverse"))
        if c.closed:
            m.addAction("Open (break at start)", lambda: self._apply(oid, ci, lambda c: vectorops.break_at(c, 0, 0.0),
                                                                     "Open vector"))
        else:
            m.addAction("Close", lambda: self._apply(oid, ci, lambda c: vectorops.close(c), "Close vector"))
        menus.show_menu(m, self.canvas.mapToGlobal(pos.toPoint()))
        return True

    def paint(self, p: QPainter) -> None:
        if self.span is not None:
            o = self._obj(self.span[1])
            if o is not None and self.span[2] < len(o.contours) and self.span[3] < len(o.contours[self.span[2]].segs):
                seg = o.contours[self.span[2]].segs[self.span[3]]
                p.save()
                p.setTransform(self.canvas.view_transform())
                pen = QPen(QColor("#ff7f00"), 3.5)
                pen.setCosmetic(True)
                p.setPen(pen)
                p.drawPath(contours_path([Contour([seg], False)]))
                p.restore()
        R = self.R
        for o in self.targets():
            for ci, c in enumerate(o.contours):
                p.setPen(QPen(QColor("#888888"), 1))
                for sg in c.segs:
                    if isinstance(sg, Bezier):
                        a, c1, c2, b = self.s(sg.a), self.s(sg.c1), self.s(sg.c2), self.s(sg.b)
                        p.drawLine(a, c1)
                        p.drawLine(b, c2)
                        p.setBrush(QBrush(QColor("#ffffff")))
                        p.drawEllipse(c1, R - 1, R - 1)
                        p.drawEllipse(c2, R - 1, R - 1)
                for ni, pt in enumerate(c.nodes()):
                    q = self.s(pt)
                    selected = (o.id, ci, ni) in self.sel
                    p.setPen(QPen(QColor("#1f3b73"), 1))
                    if selected:
                        p.setBrush(QBrush(QColor("#e31a1c")))
                    elif ni == 0:
                        p.setBrush(QBrush(QColor("#1a9641")))
                    else:
                        p.setBrush(QBrush(QColor("#ffffff")))
                    sz = R + (1.5 if ni == 0 else 0)
                    p.drawRect(QRectF(q.x() - sz, q.y() - sz, 2 * sz, 2 * sz))


# ---------------------------------------------------------------------------
# drawing tools
# ---------------------------------------------------------------------------

class DrawTool(CanvasTool):
    shape_name = "Vector"

    def commit(self, contours: list[Contour] | Contour, name: str | None = None) -> None:
        if isinstance(contours, Contour):
            contours = [contours]
        contours = [c for c in contours if c.segs]
        if not contours:
            return
        obj = VectorObject(contours, self.ctrl.layer_id, name or self.shape_name)
        self.ctrl.add_vectors([obj], f"Draw {name or self.shape_name}")

    def preview(self, p: QPainter, contours: list[Contour]) -> None:
        p.save()
        p.setTransform(self.canvas.view_transform())
        pen = QPen(QColor("#1f78b4"), 1.5, Qt.DashLine)
        pen.setCosmetic(True)
        p.setPen(pen)
        p.setBrush(Qt.NoBrush)
        p.drawPath(contours_path([c for c in contours if c.segs]))
        p.restore()


class PolylineTool(DrawTool):
    name = "polyline"
    shape_name = "Polyline"
    prompt = ("Click to add points, drag for smooth (bezier) points. Click the first point to close, "
              "Enter / double-click / right-click to finish, Backspace removes the last point, Shift = 15°.")

    def __init__(self, canvas):
        super().__init__(canvas)
        self.nodes: list[tuple[Pt, Pt | None]] = []
        self.pending: tuple[Pt, Pt | None] | None = None
        self.cur: Pt | None = None
        self.skip_release = False
        self.swallow_menu = False

    def cancel(self) -> None:
        self.nodes.clear()
        self.pending = None

    def context_menu(self, pos, w: Pt) -> bool:
        swallow, self.swallow_menu = self.swallow_menu, False
        return swallow

    def _constrain(self, w: Pt, mods) -> Pt:
        if self.nodes and mods & Qt.ShiftModifier:
            a = self.nodes[-1][0]
            ang = math.atan2(w[1] - a[1], w[0] - a[0])
            ang = round(ang / (math.pi / 12)) * (math.pi / 12)
            r = dist(a, w)
            return (a[0] + r * math.cos(ang), a[1] + r * math.sin(ang))
        return w

    def press(self, ev, w: Pt) -> None:
        if ev.button() == Qt.RightButton:
            self.swallow_menu = bool(self.nodes)
            self.finish(False)
            return
        if ev.button() != Qt.LeftButton:
            return
        p = self._constrain(self.canvas.snap(w), ev.modifiers())
        if len(self.nodes) >= 2:
            q = self.s(self.nodes[0][0])
            if math.hypot(q.x() - ev.position().x(), q.y() - ev.position().y()) < 8:
                self.finish(True)
                self.skip_release = True
                return
        self.pending = (p, None)
        self.press_pos = ev.position()

    def move(self, ev, w: Pt) -> None:
        self.cur = self._constrain(self.canvas.snap(w), ev.modifiers())
        if self.pending is not None and (ev.buttons() & Qt.LeftButton):
            if (ev.position() - self.press_pos).manhattanLength() > 4:
                p = self.pending[0]
                self.pending = (p, sub(w, p))

    def release(self, ev, w: Pt) -> None:
        if self.skip_release:
            self.skip_release = False
            return
        if self.pending is not None:
            if not self.nodes or dist(self.nodes[-1][0], self.pending[0]) > 1e-9:
                self.nodes.append(self.pending)
            self.pending = None
            self.say(f"{len(self.nodes)} point(s). " + self.prompt)

    def double_click(self, ev, w: Pt) -> None:
        self.finish(False)
        self.skip_release = True

    def key(self, ev) -> bool:
        k = ev.key()
        if k in (Qt.Key_Return, Qt.Key_Enter):
            self.finish(False)
            return True
        if k == Qt.Key_Backspace and self.nodes:
            self.nodes.pop()
            return True
        if k == Qt.Key_C and len(self.nodes) >= 3:
            self.finish(True)
            return True
        return False

    def contour(self, nodes, closed: bool) -> Contour:
        segs = []
        pairs = list(zip(nodes, nodes[1:]))
        if closed:
            pairs.append((nodes[-1], nodes[0]))
        for (a, ha), (b, hb) in pairs:
            if dist(a, b) < 1e-9:
                continue
            if ha is None and hb is None:
                segs.append(Line(a, b))
            else:
                c1 = add(a, ha) if ha else a
                c2 = sub(b, hb) if hb else b
                segs.append(Bezier(a, c1, c2, b))
        return Contour(segs, closed and len(segs) >= 2)

    def finish(self, closed: bool) -> None:
        if len(self.nodes) >= 2:
            self.commit(self.contour(self.nodes, closed))
        self.nodes.clear()
        self.pending = None

    def paint(self, p: QPainter) -> None:
        nodes = list(self.nodes)
        if self.pending is not None:
            nodes.append(self.pending)
        elif self.cur is not None and nodes:
            nodes.append((self.cur, None))
        if len(nodes) >= 2:
            self.preview(p, [self.contour(nodes, False)])
        p.setPen(QPen(QColor("#1f78b4"), 1))
        p.setBrush(QBrush(QColor("white")))
        for pt, h in self.nodes:
            q = self.s(pt)
            p.drawRect(QRectF(q.x() - 3, q.y() - 3, 6, 6))


class TwoPointTool(DrawTool):
    """Press-drag-release or click-click shapes defined by two points."""

    def __init__(self, canvas):
        super().__init__(canvas)
        self.a: Pt | None = None
        self.b: Pt | None = None
        self.press_pos = None
        self.mods = Qt.NoModifier

    def cancel(self) -> None:
        self.a = self.b = None

    def make(self, a: Pt, b: Pt, mods) -> Contour | None:
        raise NotImplementedError

    def press(self, ev, w: Pt) -> None:
        if ev.button() != Qt.LeftButton:
            return
        p = self.canvas.snap(w)
        if self.a is None:
            self.a = p
            self.press_pos = ev.position()
        else:
            self._finish(p, ev.modifiers())

    def move(self, ev, w: Pt) -> None:
        self.mods = ev.modifiers()
        if self.a is not None:
            self.b = self.canvas.snap(w)
            c = self.make(self.a, self.b, self.mods)
            if c is not None:
                bb = c.bbox()
                u = self.ctrl.units
                self.say(f"{self.shape_name}: {u.fmt(bb.width)} x {u.fmt(bb.height)}")

    def release(self, ev, w: Pt) -> None:
        if self.a is not None and self.press_pos is not None and \
                (ev.position() - self.press_pos).manhattanLength() > 4:
            self._finish(self.canvas.snap(w), ev.modifiers())
        self.press_pos = None

    def _finish(self, b: Pt, mods) -> None:
        c = self.make(self.a, b, mods) if dist(self.a, b) > 1e-9 else None
        self.a = self.b = None
        if c is not None:
            self.commit(c)
        self.say(self.prompt)

    def paint(self, p: QPainter) -> None:
        if self.a is not None and self.b is not None:
            c = self.make(self.a, self.b, self.mods)
            if c is not None:
                self.preview(p, [c])


class LineTool(TwoPointTool):
    name = "line"
    shape_name = "Line"
    prompt = "Line: click or drag from the start point to the end point (Shift = 15°)."

    def make(self, a, b, mods):
        if mods & Qt.ShiftModifier:
            ang = round(math.atan2(b[1] - a[1], b[0] - a[0]) / (math.pi / 12)) * (math.pi / 12)
            r = dist(a, b)
            b = (a[0] + r * math.cos(ang), a[1] + r * math.sin(ang))
        return shapes.line(a, b)


class RectTool(TwoPointTool):
    name = "rectangle"
    shape_name = "Rectangle"
    prompt = "Rectangle: drag between two corners (Ctrl: from the centre, Shift: square)."
    corner_radius = 0.0

    def make(self, a, b, mods):
        dx, dy = b[0] - a[0], b[1] - a[1]
        if mods & Qt.ShiftModifier:
            m = max(abs(dx), abs(dy))
            dx, dy = math.copysign(m, dx or 1), math.copysign(m, dy or 1)
        if mods & Qt.ControlModifier:
            x0, y0, w, h = a[0] - abs(dx), a[1] - abs(dy), 2 * abs(dx), 2 * abs(dy)
        else:
            x0, y0, w, h = min(a[0], a[0] + dx), min(a[1], a[1] + dy), abs(dx), abs(dy)
        if w < 1e-9 or h < 1e-9:
            return None
        return shapes.rectangle(x0, y0, w, h, self.corner_radius)


class CircleTool(TwoPointTool):
    name = "circle"
    shape_name = "Circle"
    prompt = "Circle: drag from the centre to the radius (Shift: drag a diameter)."

    def make(self, a, b, mods):
        if mods & Qt.ShiftModifier:
            return shapes.circle(((a[0] + b[0]) / 2, (a[1] + b[1]) / 2), dist(a, b) / 2)
        r = dist(a, b)
        return shapes.circle(a, r) if r > 1e-9 else None


class EllipseTool(TwoPointTool):
    name = "ellipse"
    shape_name = "Ellipse"
    prompt = "Ellipse: drag from the centre to a corner of its bounding box (Shift: circle)."

    def make(self, a, b, mods):
        rx, ry = abs(b[0] - a[0]), abs(b[1] - a[1])
        if mods & Qt.ShiftModifier:
            rx = ry = max(rx, ry)
        if rx < 1e-9 or ry < 1e-9:
            return None
        return shapes.ellipse(a, rx, ry)


class PolygonTool(TwoPointTool):
    name = "polygon"
    shape_name = "Polygon"
    sides = 6

    @property
    def prompt(self):
        return f"Polygon ({self.sides} sides, +/- to change): drag from the centre to a corner."

    def make(self, a, b, mods):
        r = dist(a, b)
        if r < 1e-9:
            return None
        rot = math.degrees(math.atan2(b[1] - a[1], b[0] - a[0])) - 90
        return shapes.polygon(a, r, self.sides, rot)

    def key(self, ev) -> bool:
        if ev.key() in (Qt.Key_Plus, Qt.Key_Equal):
            self.sides = min(100, self.sides + 1)
        elif ev.key() == Qt.Key_Minus:
            self.sides = max(3, self.sides - 1)
        else:
            return False
        self.say(self.prompt)
        return True


class StarTool(PolygonTool):
    name = "star"
    shape_name = "Star"
    sides = 5
    ratio = 0.45

    @property
    def prompt(self):
        return f"Star ({self.sides} points, +/- to change): drag from the centre to a point."

    def make(self, a, b, mods):
        r = dist(a, b)
        if r < 1e-9:
            return None
        rot = math.degrees(math.atan2(b[1] - a[1], b[0] - a[0])) - 90
        return shapes.star(a, r, r * self.ratio, self.sides, rot)


class ArcTool(DrawTool):
    name = "arc"
    shape_name = "Arc"
    prompt = "Arc: click the start point, the end point, then a point on the arc."

    def __init__(self, canvas):
        super().__init__(canvas)
        self.pts: list[Pt] = []
        self.cur: Pt | None = None

    def cancel(self) -> None:
        self.pts = []

    def press(self, ev, w):
        if ev.button() != Qt.LeftButton:
            return
        self.pts.append(self.canvas.snap(w))
        if len(self.pts) == 3:
            a, b, m = self.pts
            self.pts = []
            seg = Arc.through(a, m, b)
            if isinstance(seg, Arc) and abs(seg.sweep) > math.pi:
                s1, s2 = seg.split(0.5)
                self.commit(Contour([s1, s2], False))
            else:
                self.commit(Contour([seg], False))

    def move(self, ev, w):
        self.cur = self.canvas.snap(w)

    def paint(self, p):
        if len(self.pts) == 1 and self.cur:
            self.preview(p, [shapes.line(self.pts[0], self.cur)])
        elif len(self.pts) == 2 and self.cur and dist(self.cur, self.pts[0]) > 1e-9:
            self.preview(p, [Contour([Arc.through(self.pts[0], self.cur, self.pts[1])], False)])


class TextTool(DrawTool):
    name = "text"
    shape_name = "Text"
    prompt = "Text: click where the text baseline should start."

    def press(self, ev, w):
        if ev.button() != Qt.LeftButton:
            return
        from .dialogs import TextDialog
        p = self.canvas.snap(w)
        dlg = TextDialog(self.ctrl, self.canvas)
        if dlg.exec():
            cs = dlg.contours()
            T = Transform.translate(*p)
            self.commit([c.transformed(T) for c in cs], dlg.text()[:24] or "Text")


class MeasureTool(CanvasTool):
    name = "measure"
    prompt = "Measure: drag between two points."

    def __init__(self, canvas):
        super().__init__(canvas)
        self.a = self.b = None
        self.dragging = False

    def press(self, ev, w):
        if ev.button() == Qt.LeftButton:
            self.a = self.b = self.canvas.snap(w)
            self.dragging = True

    def move(self, ev, w):
        if self.dragging:
            self.b = self.canvas.snap(w)
            u = self.ctrl.units
            dx, dy = self.b[0] - self.a[0], self.b[1] - self.a[1]
            self.say(f"Distance {u.fmt(math.hypot(dx, dy))}   dX {u.fmt(dx)}   dY {u.fmt(dy)}   "
                     f"angle {math.degrees(math.atan2(dy, dx)):.2f}°")

    def release(self, ev, w):
        self.dragging = False

    def paint(self, p):
        if self.a and self.b:
            qa, qb = self.s(self.a), self.s(self.b)
            p.setPen(QPen(QColor("#6a3d9a"), 1.5))
            p.drawLine(qa, qb)
            u = self.ctrl.units
            p.drawText(qb + QPointF(8, -8), u.fmt(dist(self.a, self.b)))


class TrimTool(CanvasTool):
    name = "trim"
    prompt = "Trim: click the part of a vector to remove; it is cut where it crosses other vectors."
    cursor = Qt.CrossCursor

    def move(self, ev, w):
        o = self.canvas.hit(w)
        self.canvas.hover_id = o.id if o else None

    def press(self, ev, w):
        from ..core.intersect import trim
        if ev.button() != Qt.LeftButton:
            return
        obj = self.canvas.hit(w)
        if obj is None:
            return
        ci = min(range(len(obj.contours)), key=lambda k: obj.contours[k].nearest(w)[3])
        target = obj.contours[ci]
        cutters = [c for o in self.canvas.visible_objects() for c in o.contours if c is not target]
        pieces = trim(target, w, cutters)
        if pieces is None:
            self.say("Nothing to trim here: the vector does not cross other vectors around this point.")
            return
        with self.ctrl.edit("Trim"):
            obj.contours = obj.contours[:ci] + pieces + obj.contours[ci + 1:]
            if not obj.contours:
                self.ctrl.project.remove_vectors([obj.id])
        self.say(self.prompt)


class TabTool(CanvasTool):
    """Add / remove manual tab positions of one profile toolpath."""

    name = "tabs"
    prompt = "Edit tabs: click on a vector to add a tab, click a tab to remove it. Esc when done."
    cursor = Qt.CrossCursor

    def __init__(self, canvas, tdef_id: str):
        super().__init__(canvas)
        self.tdef_id = tdef_id

    def tdef(self):
        return self.ctrl.project.toolpath(self.tdef_id)

    def activate(self) -> None:
        t = self.tdef()
        if t is not None and t.params.get("tab_mode") != "manual":
            self._store(self._seed(t))

    def _seed(self, t) -> list[list[float]]:
        """Start from the automatic positions so switching to manual changes nothing."""
        p = t.params
        pts = []
        if not p.get("tabs"):
            return pts
        for o in self.ctrl.project.vectors_for(t, self.ctrl.sheet):
            for c in o.contours:
                if not c.closed:
                    continue
                L = c.length()
                if L < max(p.get("tab_min_length", 40.0), 2 * p.get("tab_length", 8.0)):
                    continue
                n = max(1, round(L / max(1.0, p.get("tab_spacing", 300.0)))) if p.get("tab_mode") == "spacing" \
                    else max(1, int(p.get("tab_count", 4)))
                pts += [list(c.point_at_length((k + 0.5) * L / n)) for k in range(n)]
        return pts

    def _store(self, pts: list) -> None:
        import copy
        t = self.tdef()
        if t is None:
            return
        new = copy.deepcopy(t)
        new.params.update({"tab_points": pts, "tabs": True, "tab_mode": "manual"})
        self.ctrl.replace_toolpath(new)

    def press(self, ev, w):
        t = self.tdef()
        if ev.button() != Qt.LeftButton or t is None:
            return
        pts = [list(p) for p in t.params.get("tab_points") or []]
        pos = ev.position()
        for k, p in enumerate(pts):
            q = self.s(tuple(p))
            if math.hypot(q.x() - pos.x(), q.y() - pos.y()) <= 8:
                del pts[k]
                self._store(pts)
                return
        best, bd = None, self.canvas.px(15)
        for o in self.ctrl.project.vectors_for(t, self.ctrl.sheet):
            for c in o.contours:
                _, _, q, d = c.nearest(w)
                if d < bd:
                    best, bd = q, d
        if best is not None:
            pts.append(list(best))
            self._store(pts)

    def paint(self, p):
        t = self.tdef()
        if t is None:
            return
        p.setPen(QPen(QColor("#7f2704"), 1))
        p.setBrush(QBrush(QColor("#fd8d3c")))
        for pt in t.params.get("tab_points") or []:
            q = self.s(tuple(pt))
            p.drawRect(QRectF(q.x() - 5, q.y() - 5, 10, 10))


class StartPointTool(CanvasTool):
    """Click on a vector to start its cut there (a new node is made); the start is then kept
    as it is by smart start points. Right click releases a start point again."""

    name = "start"
    prompt = ("Start point: click on a vector where its cut should start (a node is added there). "
              "Right click a start point to release it to smart start points. Esc when done.")
    cursor = Qt.CrossCursor

    def _nearest(self, w: Pt):
        best, bd = None, self.canvas.px(12)
        for o in self.canvas.visible_objects():
            if not o.bbox().inflated(bd).contains(w):
                continue
            for ci, c in enumerate(o.contours):
                if not c.closed:
                    continue
                _, _, q, d = c.nearest(w)
                if d < bd:
                    best, bd = (o, ci, q), d
        return best

    def move(self, ev, w):
        hit = self._nearest(w)
        self.canvas.hover_id = hit[0].id if hit else None

    def press(self, ev, w):
        hit = self._nearest(w)
        if hit is None:
            return
        o, ci, q = hit
        if ev.button() == Qt.RightButton:
            if ci in o.locked_starts:
                with self.ctrl.edit("Release start point", geometry=False):
                    o.locked_starts.remove(ci)
                self.ctrl.stale = True
                self.ctrl.recalculate()
            return
        if ev.button() == Qt.LeftButton:
            self.ctrl.set_start_point(o.id, ci, q)

    def paint(self, p):
        for o in self.canvas.visible_objects():
            for ci, c in enumerate(o.contours):
                if not c.closed or not c.segs:
                    continue
                q = self.s(c.start)
                locked = ci in o.locked_starts
                p.setPen(QPen(QColor("#4a1486" if locked else "#08519c"), 1.2))
                p.setBrush(QBrush(QColor("#9e9ac8" if locked else "#6baed6")))
                if locked:
                    p.drawRect(QRectF(q.x() - 4, q.y() - 4, 8, 8))
                else:
                    p.drawEllipse(q, 3.5, 3.5)


class OrderTool(CanvasTool):
    """Pick cuts one after the other: they are cut in this order from position ``index`` on."""

    name = "order"
    cursor = Qt.PointingHandCursor

    def __init__(self, canvas, tdef_id: str, index: int = 0):
        super().__init__(canvas)
        self.tdef_id = tdef_id
        self.index = max(0, index)
        self.picked: list[str] = []
        self.prompt = (f"Cut order from #{self.index + 1}: click the vectors in the order they should be cut. "
                       "Enter (or double click) applies, Backspace takes the last one back, Esc cancels.")

    def _keys(self) -> dict[str, tuple]:
        t = self.ctrl.project.toolpath(self.tdef_id)
        if t is None:
            return {}
        return {f"{o.id}:{i}": (o, c) for o in self.ctrl.project.vectors_for(t, self.ctrl.sheet)
                for i, c in enumerate(o.contours) if c.segs}

    def _at(self, w: Pt) -> str | None:
        """The vector contour at ``w``: the nearest line, else the smallest closed one around it."""
        from ..core.geom import point_in_polygon
        keys = self._keys()
        best, bd = None, self.canvas.px(12)
        for k, (o, c) in keys.items():
            d = c.nearest(w)[3]
            if d < bd:
                best, bd = k, d
        if best is not None:
            return best
        around = [(abs(c.area()), k) for k, (o, c) in keys.items()
                  if c.closed and c.bbox().contains(w) and point_in_polygon(w, c.flatten(0.1))]
        return min(around)[1] if around else None

    def press(self, ev, w):
        if ev.button() != Qt.LeftButton:
            return
        k = self._at(w)
        if k is not None and k not in self.picked:
            self.picked.append(k)
            self.say(f"{len(self.picked)} picked: next is #{self.index + len(self.picked) + 1}. Enter applies.")
            self.canvas.update()

    def double_click(self, ev, w):
        self.apply()

    def key(self, ev) -> bool:
        if ev.key() in (Qt.Key_Return, Qt.Key_Enter):
            self.apply()
            return True
        if ev.key() == Qt.Key_Backspace and self.picked:
            self.picked.pop()
            self.canvas.update()
            return True
        return False

    def apply(self) -> None:
        if self.picked:
            self.ctrl.set_manual_order(self.tdef_id, self.index, self.picked)
            self.picked = []
        self.canvas.cancel_tool()

    def paint(self, p):
        keys = self._keys()
        font = p.font()
        font.setPixelSize(12)
        font.setBold(True)
        p.setFont(font)
        for n, k in enumerate(self.picked):
            if k not in keys:
                continue
            c = keys[k][1]
            q = self.s(c.start)
            p.setPen(QPen(QColor("#a50f15"), 1.5))
            p.setBrush(QBrush(QColor("#fcbba1")))
            p.drawEllipse(q, 11, 11)
            p.drawText(QRectF(q.x() - 11, q.y() - 11, 22, 22), Qt.AlignCenter, str(self.index + n + 1))


TOOLS = {cls.name: cls for cls in (SelectTool, NodeTool, PolylineTool, LineTool, RectTool, CircleTool,
                                    EllipseTool, PolygonTool, StarTool, ArcTool, TextTool, MeasureTool, TrimTool,
                                    StartPointTool)}

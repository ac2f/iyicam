"""The 2D simulation of the desktop program (as ArtCAM's "2D simulation"): the tool runs over the sheet along the calculated
toolpaths and paints, live, where it has cut: lighter where it went into the material, dark where it went through.

Nothing is calculated again (the toolpaths as they were calculated are played), and only the toolpaths chosen: the ones
selected on the Toolpaths panel, or every one on the sheet. Play, pause, the speed (1x ... 1000x) and the place in time
(drag the slider) can be changed while it runs.
"""
from __future__ import annotations

from PySide6.QtCore import QPointF, QRectF, Qt, QTimer
from PySide6.QtGui import QColor, QImage, QPainter, QPen
from PySide6.QtWidgets import (QCheckBox, QComboBox, QHBoxLayout, QLabel, QPushButton, QSlider, QVBoxLayout, QWidget)

from ..cam import sim2d
from ..cam.moves import format_time

SPEEDS = (1, 2, 5, 10, 25, 50, 100, 250, 1000)
THROUGH = QColor(52, 56, 64)            # cut through: the table shows
CUT = QColor(176, 120, 64)              # into the material
TOOL = QColor("#e11d48")


class SimView:
    """What the drawing shows of a 2D simulation: a picture of the sheet with the cut so far, and the tool."""

    def __init__(self, width: float, height: float, thickness: float, timeline: sim2d.Timeline):
        self.timeline = timeline
        self.thickness = max(thickness, 1e-6)
        self.rect = QRectF(0, 0, width, height)
        self.ppm = max(0.25, min(4.0, 4000.0 / max(width, height, 1.0)))       # pixels per millimetre
        self.image = QImage(max(1, int(width * self.ppm)), max(1, int(height * self.ppm)),
                            QImage.Format_ARGB32_Premultiplied)
        self.t = 0.0
        self._done = 0                  # pieces painted in full
        self.reset()

    def reset(self) -> None:
        self.image.fill(Qt.transparent)
        self._done = 0
        self.t = 0.0

    def _pen(self, piece: sim2d.Piece) -> QPen | None:
        z = max(piece.z0, piece.z1)
        if piece.kind != sim2d.KIND_FEED or z >= -1e-6:
            return None                                                   # (a rapid, or above the material)
        tp = self.timeline.toolpaths[piece.tool]
        depth = -min(piece.z0, piece.z1)
        tool = tp.tool
        d = tool.radius_at_depth(depth) * 2 if tool.is_tapered else tool.diameter
        through = depth >= self.thickness - 1e-3
        c = QColor(THROUGH) if through else QColor(CUT).darker(100 + int(60 * min(1.0, depth / self.thickness)))
        pen = QPen(c, max(d, 1.0 / self.ppm))
        pen.setCapStyle(Qt.RoundCap)
        pen.setJoinStyle(Qt.RoundJoin)
        return pen

    def advance(self, t: float) -> None:
        """Paint what is cut up to time ``t`` (back in time: painted again from the start)."""
        t = max(0.0, min(t, self.timeline.total))
        if t < self.t:
            self.reset()
        upto = self.timeline.index_at(t)
        pieces = self.timeline.pieces
        p = QPainter(self.image)
        p.setRenderHint(QPainter.Antialiasing)
        p.scale(self.ppm, -self.ppm)
        p.translate(0, -self.rect.height())
        last = None
        for i in range(self._done, upto):
            pc = pieces[i]
            if pc.t1 > t:                                   # (the piece the tool is on: up to where it is)
                x, y, z = pc.at(t)
                part = sim2d.Piece(pc.x0, pc.y0, pc.z0, x, y, z, pc.kind, pc.t0, t, pc.tool)
                pen = self._pen(part)
                if pen is not None:
                    p.setPen(pen)
                    p.drawLine(QPointF(pc.x0, pc.y0), QPointF(x, y))
                break
            pen = self._pen(pc)
            if pen is not None:
                if last is None or pen.color() != last.color() or pen.widthF() != last.widthF():
                    p.setPen(pen)
                    last = pen
                p.drawLine(QPointF(pc.x0, pc.y0), QPointF(pc.x1, pc.y1))
            self._done = i + 1
        p.end()
        self.t = t

    def paint(self, p: QPainter) -> None:
        """The picture, in the coordinates of the sheet (the painter has the view transform)."""
        p.save()
        p.translate(0, self.rect.height())
        p.scale(1.0 / self.ppm, -1.0 / self.ppm)
        p.drawImage(0, 0, self.image)
        p.restore()

    def paint_tool(self, p: QPainter, canvas) -> None:
        """The tool where it is now (screen coordinates)."""
        pos = self.timeline.position(self.t)
        if pos is None:
            return
        x, y, z, k = pos
        tool = self.timeline.toolpaths[k].tool
        q = canvas.world_to_screen(x, y)
        r = max(4.0, tool.diameter / 2 * canvas.scale)
        p.setPen(QPen(TOOL, 2.0))
        c = QColor(TOOL)
        c.setAlpha(70 if z < 0 else 25)
        p.setBrush(c)
        p.drawEllipse(q, r, r)
        p.drawLine(q + QPointF(-r - 4, 0), q + QPointF(r + 4, 0))
        p.drawLine(q + QPointF(0, -r - 4), q + QPointF(0, r + 4))


class SimPanel(QWidget):
    """Play / pause, speed, the place in time and what is played."""

    def __init__(self, window, parent=None):
        super().__init__(parent)
        self.window_ = window
        self.view: SimView | None = None
        self.playing = False
        lay = QVBoxLayout(self)
        lay.setContentsMargins(6, 6, 6, 6)
        r = QHBoxLayout()
        r.addWidget(QLabel("Toolpaths:"))
        self.which = QComboBox()
        self.which.addItem("The selected ones", "selected")
        self.which.addItem("All on this sheet", "all")
        self.which.setToolTip("The selected ones: what is selected on the Toolpaths panel (all when none is)")
        r.addWidget(self.which, 1)
        lay.addLayout(r)
        r = QHBoxLayout()
        self.restart = QPushButton("⏮")
        self.restart.setToolTip("From the start")
        self.play = QPushButton("▶ Play")
        self.end = QPushButton("⏭")
        self.end.setToolTip("Show everything cut at once")
        self.close_btn = QPushButton("✕")
        self.close_btn.setToolTip("End the simulation (the drawing as before)")
        for w in (self.restart, self.play, self.end, self.close_btn):
            r.addWidget(w)
        lay.addLayout(r)
        r = QHBoxLayout()
        r.addWidget(QLabel("Speed:"))
        self.speed = QComboBox()
        for s in SPEEDS:
            self.speed.addItem(f"{s}x", s)
        self.speed.setCurrentIndex(SPEEDS.index(10))
        self.speed.setToolTip("How much faster than the machine (1x: as long as the job takes)")
        r.addWidget(self.speed, 1)
        self.paths = QCheckBox("Toolpath lines")
        self.paths.setToolTip("Show the lines of the toolpaths over the simulation")
        r.addWidget(self.paths)
        lay.addLayout(r)
        self.slider = QSlider(Qt.Horizontal)
        self.slider.setRange(0, 1000)
        lay.addWidget(self.slider)
        self.info = QLabel("Press Simulate (F6) to play the calculated toolpaths.")
        self.info.setWordWrap(True)
        lay.addWidget(self.info)
        self.shaded = QPushButton("Shaded result (3D)...")
        self.shaded.setToolTip("The material as it is left by the whole job, shaded (takes a moment)")
        lay.addWidget(self.shaded)
        lay.addStretch(1)
        self.timer = QTimer(self)
        self.timer.setInterval(33)
        self.timer.timeout.connect(self._tick)
        self.play.clicked.connect(self.toggle)
        self.restart.clicked.connect(lambda: self.seek(0.0))
        self.end.clicked.connect(lambda: self.seek(self.view.timeline.total if self.view else 0.0))
        self.close_btn.clicked.connect(self.stop)
        self.slider.sliderMoved.connect(self._slid)
        self.paths.toggled.connect(lambda *_: window.canvas.update())
        self.which.currentIndexChanged.connect(lambda *_: self.view is not None and self.start(play=self.playing))
        self.shaded.clicked.connect(window.shaded_simulation)
        self._enable()

    # -- what is played -------------------------------------------------------------------------------
    def chosen(self) -> list:
        """The calculated toolpaths of the sheet that are played, in their order (nothing is calculated)."""
        win = self.window_
        c = win.ctrl
        ids = win.toolpaths_panel.selected_ids() if self.which.currentData() == "selected" else []
        tps = c.sheet_toolpaths(enabled_only=not ids)
        return [tp for tp in tps if not ids or tp.def_id in ids]

    def start(self, play: bool = True) -> bool:
        win = self.window_
        c = win.ctrl
        tps = self.chosen()
        if not tps:
            self.info.setText("There is no calculated toolpath to play on this sheet: calculate first (F5).")
            self.stop()
            return False
        sh = c.sheet
        tl = sim2d.build(tps, machine=c.project.material.machine())
        self.view = SimView(sh.width, sh.height, c.project.sheet_thickness(sh), tl)
        win.canvas.sim2d = self
        self.slider.setValue(0)
        self._enable()
        if play:
            self.playing = True
            self.timer.start()
        else:
            self.playing = False
            self.timer.stop()
        self._show()
        return True

    def stop(self) -> None:
        self.timer.stop()
        self.playing = False
        self.view = None
        if getattr(self.window_.canvas, "sim2d", None) is self:
            self.window_.canvas.sim2d = None
        self.window_.canvas.update()
        self._enable()
        self._show()

    # -- playing --------------------------------------------------------------------------------------
    def toggle(self) -> None:
        if self.view is None:
            self.start()
            return
        if self.view.t >= self.view.timeline.total - 1e-9:
            self.view.reset()
        self.playing = not self.playing
        (self.timer.start if self.playing else self.timer.stop)()
        self._show()

    def seek(self, t: float) -> None:
        if self.view is None:
            return
        self.view.advance(t)
        self._show()

    def _slid(self, v: int) -> None:
        if self.view is not None:
            self.seek(self.view.timeline.total * v / 1000.0)

    def _tick(self) -> None:
        if self.view is None:
            self.timer.stop()
            return
        speed = self.speed.currentData() or 1
        t = self.view.t + self.timer.interval() / 1000.0 * speed
        if t >= self.view.timeline.total:
            t = self.view.timeline.total
            self.playing = False
            self.timer.stop()
        self.view.advance(t)
        self._show()

    def _enable(self) -> None:
        on = self.view is not None
        for w in (self.restart, self.end, self.close_btn, self.slider):
            w.setEnabled(on)

    def _show(self) -> None:
        self.play.setText("⏸ Pause" if self.playing else "▶ Play")
        v = self.view
        if v is None:
            self.slider.setValue(0)
            self.window_.canvas.update()
            return
        total = v.timeline.total
        if not self.slider.isSliderDown():
            self.slider.blockSignals(True)
            self.slider.setValue(int(1000 * v.t / total) if total > 0 else 1000)
            self.slider.blockSignals(False)
        pos = v.timeline.position(v.t)
        where = ""
        if pos is not None:
            x, y, z, k = pos
            u = self.window_.ctrl.units
            where = f"  ·  {v.timeline.tool_name(k)}  ·  X {u.fmt(x)}  Y {u.fmt(y)}  Z {u.fmt(z)}"
        self.info.setText(f"{format_time(v.t)} / {format_time(total)}{where}")
        self.window_.canvas.update()

    # -- the drawing ----------------------------------------------------------------------------------------
    def paints_paths(self) -> bool:
        return self.paths.isChecked()

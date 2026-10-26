"""Dialogs for the cut order (G-code sequence) and the start points of the vectors.

The two are independent: each has its own switch for working automatically and
its own button to run it once.
"""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QCheckBox, QComboBox, QDialog, QDialogButtonBox, QDoubleSpinBox, QFormLayout,
                               QHBoxLayout, QLabel, QListWidget, QListWidgetItem, QPushButton, QSpinBox,
                               QVBoxLayout)

from ..cam import sequence as seq


def sequenced_toolpaths(ctrl) -> list:
    """Toolpaths that cut vector by vector (profile, engrave, ...)."""
    from ..cam.profile import ProfileOp
    out = []
    for t in ctrl.project.toolpaths:
        op = ctrl.registry.operations.get(t.op)
        if op is not None and issubclass(op, ProfileOp):
            out.append(t)
    return out


def default_toolpath(ctrl, window=None) -> str | None:
    """The toolpath selected in the toolpath list, else one machining the selected vectors."""
    tps = sequenced_toolpaths(ctrl)
    ids = {t.id for t in tps}
    panel = getattr(window, "toolpaths_panel", None)
    if panel is not None:
        for i in panel.selected_ids():
            if i in ids:
                return i
    sel = set(ctrl.selection)
    for t in tps:
        if sel & {o.id for o in ctrl.project.vectors_for(t, ctrl.sheet)}:
            return t.id
    return tps[0].id if tps else None


def _length_spin(ctrl, value: float, special: str | None = None) -> QDoubleSpinBox:
    u = ctrl.units
    w = QDoubleSpinBox()
    w.setRange(0.0, 1e5)
    w.setDecimals(u.decimals)
    w.setSuffix(u.suffix)
    w.setValue(u.to_display(value))
    if special:
        w.setSpecialValueText(special)
    return w


class _ToolpathDialog(QDialog):
    def __init__(self, ctrl, tid: str | None, parent=None):
        super().__init__(parent)
        self.ctrl = ctrl
        self.window_ = parent
        self.tp = QComboBox()
        for t in sequenced_toolpaths(ctrl):
            self.tp.addItem(t.name, t.id)
        i = self.tp.findData(tid or default_toolpath(ctrl, parent))
        self.tp.setCurrentIndex(max(0, i))
        self.tp.currentIndexChanged.connect(self.load)

    @property
    def tid(self) -> str | None:
        return self.tp.currentData()

    def tdef(self):
        return self.ctrl.project.toolpath(self.tid) if self.tid else None

    def cfg(self) -> dict:
        t = self.tdef()
        return seq.settings(t.sequence if t else None)

    def results(self):
        t = self.tdef()
        return [tp for r in (self.ctrl.results_for(t) if t else []) for tp in r.toolpaths]

    def load(self) -> None:
        pass


class CutOrderDialog(_ToolpathDialog):
    """Strategy, automatic / frozen order, re-order once and manual ordering."""

    def __init__(self, ctrl, tid: str | None = None, parent=None):
        super().__init__(ctrl, tid, parent)
        self.setWindowTitle("Cut Order")
        lay = QVBoxLayout(self)
        form = QFormLayout()
        form.addRow("Toolpath", self.tp)
        self.auto = QCheckBox("Re-order automatically on every calculation")
        self.auto.setToolTip("Off: the order is frozen; new vectors are added at the end")
        form.addRow("", self.auto)
        self.strategy = QComboBox()
        for k, label in seq.STRATEGIES:
            self.strategy.addItem(label, k)
        form.addRow("Strategy", self.strategy)
        self.grouping = QComboBox()
        for k, label in seq.GROUPINGS:
            self.grouping.addItem(label, k)
        self.grouping.setToolTip("Göbek: the inner pieces of a part (holes, the hub of a ring, parts nested in holes). "
                                 "First option: all of them are cut first, in the order of the strategy, and "
                                 "the outlines follow in the order of the strategy.")
        form.addRow("Inner pieces (göbek)", self.grouping)
        self.band = _length_spin(ctrl, 0.0, "Automatic")
        self.band.setToolTip("Height of a row (width of a column) for the row and column strategies")
        form.addRow("Row / column size", self.band)
        self.direction = QComboBox()
        for k, label in seq.DIRECTIONS:
            self.direction.addItem(label, k)
        self.direction.setToolTip("Cutting direction used with this strategy (every strategy keeps its own); "
                                  "holes mill like their outline. Start points take the direction into account.")
        self.all_dirs = QPushButton("All strategies...")
        self.all_dirs.setToolTip("Set the direction of every strategy at once, and the defaults of new toolpaths")
        row = QHBoxLayout()
        row.addWidget(self.direction, 1)
        row.addWidget(self.all_dirs)
        form.addRow("Direction (this strategy)", row)
        lay.addLayout(form)
        note = QLabel("Whatever the strategy, vectors inside others (holes, parts in holes, engravings) are "
                      "always cut before the vector around them, so nothing is cut once it is loose.")
        note.setWordWrap(True)
        lay.addWidget(note)
        row = QHBoxLayout()
        self.reorder = QPushButton("Re-order now")
        self.reorder.setToolTip("Order the cuts with the strategy once (a frozen order stays frozen)")
        row.addWidget(self.reorder)
        row.addStretch(1)
        lay.addLayout(row)
        lay.addWidget(QLabel("Order on this sheet (select a row and move it, or pick on the drawing):"))
        self.list = QListWidget()
        lay.addWidget(self.list, 1)
        row = QHBoxLayout()
        self.up, self.down = QPushButton("Up"), QPushButton("Down")
        self.start_at = QSpinBox()
        self.start_at.setRange(1, 1)
        self.start_at.setPrefix("from #")
        self.pick = QPushButton("Pick order on the drawing")
        self.pick.setToolTip("Click the vectors in the order they should be cut, starting at this position")
        for w in (self.up, self.down, self.start_at, self.pick):
            row.addWidget(w)
        lay.addLayout(row)
        self.info = QLabel()
        self.info.setWordWrap(True)
        lay.addWidget(self.info)
        bb = QDialogButtonBox(QDialogButtonBox.Close)
        bb.rejected.connect(self.reject)
        lay.addWidget(bb)
        self.resize(460, 620)
        self._loading = True
        self.auto.toggled.connect(lambda v: self._set(auto_order=v))
        self.strategy.currentIndexChanged.connect(lambda: self._set(strategy=self.strategy.currentData()))
        self.grouping.currentIndexChanged.connect(lambda: self._set(grouping=self.grouping.currentData()))
        self.band.editingFinished.connect(lambda: self._set(band=ctrl.units.from_display(self.band.value())))
        self.direction.currentIndexChanged.connect(self._set_direction)
        self.all_dirs.clicked.connect(self._all_directions)
        self.reorder.clicked.connect(self._reorder)
        self.up.clicked.connect(lambda: self._move(-1))
        self.down.clicked.connect(lambda: self._move(1))
        self.pick.clicked.connect(self._pick)
        self.list.currentRowChanged.connect(lambda r: self.start_at.setValue(r + 1) if r >= 0 else None)
        self.load()

    def load(self) -> None:
        c = self.cfg()
        self._loading = True
        for w, v in ((self.auto, None), (self.strategy, c["strategy"]), (self.grouping, c["grouping"]),
                     (self.direction, seq.direction_for(c))):
            w.blockSignals(True)
            if v is None:
                w.setChecked(bool(c["auto_order"]))
            else:
                w.setCurrentIndex(max(0, w.findData(v)))
            w.blockSignals(False)
        self.band.setValue(self.ctrl.units.to_display(c["band"]))
        self._fill()
        self._loading = False

    def _fill(self) -> None:
        row = self.list.currentRow()
        self.list.clear()
        keys = self.ctrl.cut_order(self.tid) if self.tid else []
        for n, k in enumerate(keys):
            vid, ci = k.rsplit(":", 1)
            o = self.ctrl.find_vector(vid)
            name = (o.name or "Vector") if o else "?"
            if o is not None and len(o.contours) > 1:
                name += f"  · contour {int(ci) + 1}"
            it = QListWidgetItem(f"{n + 1}.  {name}")
            it.setData(Qt.UserRole, k)
            self.list.addItem(it)
        self.start_at.setRange(1, max(1, len(keys)))
        if 0 <= row < len(keys):
            self.list.setCurrentRow(row)
        notes = [w for tp in self.results() for w in tp.warnings if "order" in w.lower() or "parts" in w.lower()]
        self.info.setText("\n".join(dict.fromkeys(notes)))

    def _set(self, **changes) -> None:
        if not self._loading and self.tid:
            self.ctrl.set_sequence(self.tid, **changes)
            self.ctrl.remember_cut_order(**changes)
            if "strategy" in changes:  # every strategy has its own direction
                self.direction.blockSignals(True)
                self.direction.setCurrentIndex(max(0, self.direction.findData(seq.direction_for(self.cfg()))))
                self.direction.blockSignals(False)
            self._fill()

    def _set_direction(self) -> None:
        dirs = dict(self.cfg()["directions"])
        dirs[self.strategy.currentData()] = self.direction.currentData()
        self._set(directions=dirs)

    def _all_directions(self) -> None:
        if not self.tid:
            return
        dlg = DirectionsDialog(self.ctrl, self.cfg(), self)
        if dlg.exec():
            self._set(directions=dlg.directions())
            if dlg.as_default.isChecked():
                self.ctrl.settings.strategy_directions = dlg.directions()
                self.ctrl.settings.save()
            self.load()

    def _reorder(self) -> None:
        if self.tid:
            self.ctrl.reorder_now(self.tid)
            self._fill()

    def _move(self, delta: int) -> None:
        r = self.list.currentRow()
        if r < 0 or not 0 <= r + delta < self.list.count():
            return
        key = self.list.item(r).data(Qt.UserRole)
        self.ctrl.set_manual_order(self.tid, r + delta, [key])
        self.load()
        self.list.setCurrentRow(r + delta)

    def _pick(self) -> None:
        from .tools import OrderTool
        canvas = getattr(self.window_, "canvas", None)
        if canvas is None or not self.tid:
            return
        canvas.set_tool(OrderTool(canvas, self.tid, self.start_at.value() - 1))
        self.accept()


class DirectionsDialog(QDialog):
    """Cutting direction of every strategy."""

    def __init__(self, ctrl, cfg: dict, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Cutting direction per strategy")
        lay = QVBoxLayout(self)
        note = QLabel("Each strategy cuts in its own direction. Climb milling runs clockwise around the outside "
                      "of a part; holes always mill like their outline.")
        note.setWordWrap(True)
        lay.addWidget(note)
        form = QFormLayout()
        self.combos: dict[str, QComboBox] = {}
        current = seq.settings(cfg)
        for k, label in seq.STRATEGIES:
            box = QComboBox()
            for d, dl in seq.DIRECTIONS:
                box.addItem(dl, d)
            box.setCurrentIndex(max(0, box.findData(current["directions"].get(k, current["direction"]))))
            form.addRow(label, box)
            self.combos[k] = box
        lay.addLayout(form)
        self.as_default = QCheckBox("Use these for new toolpaths too")
        lay.addWidget(self.as_default)
        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)
        lay.addWidget(bb)

    def directions(self) -> dict[str, str]:
        return {k: box.currentData() for k, box in self.combos.items()}


class StartPointsDialog(_ToolpathDialog):
    """Smart start points on/off, run once, start point tool and releasing hand-set starts."""

    def __init__(self, ctrl, tid: str | None = None, parent=None):
        super().__init__(ctrl, tid, parent)
        self.setWindowTitle("Start Points")
        lay = QVBoxLayout(self)
        form = QFormLayout()
        form.addRow("Toolpath", self.tp)
        self.smart = QCheckBox("Move the vectors' start points automatically")
        self.smart.setToolTip("Every calculation puts each start point at the best place of its vector "
                              "(a new node is made there); start points set by hand are kept")
        form.addRow("", self.smart)
        self.margin = _length_spin(ctrl, 1.0)
        self.margin.setToolTip("Free material kept between the plunging tool and areas cut before")
        form.addRow("Clear of cut areas by", self.margin)
        self.corner = _length_spin(ctrl, 0.0, "Automatic")
        self.corner.setToolTip("Keep start points this far from corners")
        form.addRow("Away from corners by", self.corner)
        lay.addLayout(form)
        note = QLabel("A cut closes where it starts, so the start point is where the part is held last. It is "
                      "put where the sheet still holds best (ahead of the sweep, away from neighbours already "
                      "cut), with the well held sides cut last, a corner distance off the corner and clear of "
                      "machined areas. Long narrow parts and letters close just above their bottom right corner "
                      "(mirrored for the other sweeps). Changing one start point only recalculates that cut and "
                      "the ones after it; the cut order stays as it is.")
        note.setWordWrap(True)
        lay.addWidget(note)
        row = QHBoxLayout()
        self.now = QPushButton("Optimise now")
        self.now.setToolTip("Move the start points once (undoable), even with the automatic mode off")
        self.tool = QPushButton("Set on the drawing (B)")
        self.release = QPushButton("Release hand-set starts")
        self.release.setToolTip("Start points of the selected vectors (or of the whole toolpath) "
                                "may be moved by smart start points again")
        for w in (self.now, self.tool, self.release):
            row.addWidget(w)
        lay.addLayout(row)
        self.info = QLabel()
        self.info.setWordWrap(True)
        lay.addWidget(self.info)
        bb = QDialogButtonBox(QDialogButtonBox.Close)
        bb.rejected.connect(self.reject)
        lay.addWidget(bb)
        self.resize(460, 330)
        self._loading = False
        self.smart.toggled.connect(lambda v: self._set(smart_starts=v))
        self.margin.editingFinished.connect(lambda: self._set(start_margin=ctrl.units.from_display(self.margin.value())))
        self.corner.editingFinished.connect(
            lambda: self._set(corner_distance=ctrl.units.from_display(self.corner.value())))
        self.now.clicked.connect(self._now)
        self.tool.clicked.connect(self._tool)
        self.release.clicked.connect(self._release)
        self.load()

    def load(self) -> None:
        c = self.cfg()
        self._loading = True
        self.smart.setChecked(bool(c["smart_starts"]))
        self.margin.setValue(self.ctrl.units.to_display(c["start_margin"]))
        self.corner.setValue(self.ctrl.units.to_display(c["corner_distance"]))
        self._loading = False
        self._info()

    def _info(self) -> None:
        t = self.tdef()
        if t is None:
            self.info.setText("No profile or engraving toolpath in the project.")
            return
        objs = self.ctrl.project.vectors_for(t, self.ctrl.sheet)
        locked = sum(len(o.locked_starts) for o in objs)
        cuts = [c for tp in self.results() for c in tp.cuts if c.get("closed") and not c.get("finish")]
        bad = sum(1 for c in cuts if c.get("ok") is False)
        text = f"{len(cuts)} closed cut(s) on this sheet, {locked} start point(s) set by hand."
        if bad:
            text += f" {bad} start point(s) could not be kept clear of cut areas and corners."
        self.info.setText(text)

    def _set(self, **changes) -> None:
        if not self._loading and self.tid:
            self.ctrl.set_sequence(self.tid, **changes)
            self.ctrl.remember_cut_order(**changes)
            self._info()

    def _now(self) -> None:
        if self.tid:
            self.ctrl.optimise_starts({self.tid})
            self._info()

    def _tool(self) -> None:
        from .tools import StartPointTool
        canvas = getattr(self.window_, "canvas", None)
        if canvas is not None:
            canvas.set_tool(StartPointTool(canvas))
            self.accept()

    def _release(self) -> None:
        t = self.tdef()
        if t is None:
            return
        objs = self.ctrl.project.vectors_for(t, self.ctrl.sheet)
        sel = set(self.ctrl.selection)
        ids = [o.id for o in objs if o.id in sel] or [o.id for o in objs]
        self.ctrl.unlock_starts(ids)
        self.ctrl.recalculate({t.id})
        self._info()

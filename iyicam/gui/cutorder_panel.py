"""The cut order panel: the order the cuts are made in, the direction they are cut in and where each one starts,
shown on the drawing as it is changed.

Everything that can be changed can be *previewed* first (a toolpath is calculated with the new settings and drawn in
another colour, with what it changes in numbers) and then applied or dropped. The list of cuts is the cut order:
select a row to see the cut, move it with the buttons or by dragging, and change where that cut starts without
changing its place in the order.
"""
from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (QAbstractItemView, QCheckBox, QDialog, QDialogButtonBox, QFormLayout, QGridLayout,
                                QGroupBox, QHBoxLayout, QHeaderView, QLabel, QPushButton, QScrollArea, QToolButton,
                                QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget)
from .wheelguard import QComboBox, QDoubleSpinBox, QSpinBox

from ..cam import cutinfo
from ..cam import sequence as seq
from .panels import DocPanel

MEMORY_KEY = "cut_order_panel"
QUALITY_MARK = {"hand": ("■ by hand", "#6a51a3"), "good": ("● good", "#238b45"),
                "warn": ("▲ check", "#d94801"), "plain": ("○ as drawn", "#737373")}
PLACES = (("nw", "↖", "Top left corner"), ("n", "↑", "Middle of the top side"),
          ("ne", "↗", "Top right corner"), ("w", "←", "Middle of the left side"),
          ("e", "→", "Middle of the right side"), ("sw", "↙", "Bottom left corner"),
          ("s", "↓", "Middle of the bottom side"), ("se", "↘", "Bottom right corner"))
VIEW_OPTIONS = (("show_cut_order", "Numbers"), ("show_route", "Route"), ("show_directions", "Direction arrows"),
                ("show_starts", "Start points"))


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


def fill_strategies(box: QComboBox) -> None:
    """The order strategies under the headings of their groups (a heading cannot be chosen)."""
    from PySide6.QtGui import QFont, QStandardItem
    box.clear()
    model = box.model()
    last = None
    for k, label, group in seq.grouped_strategies():
        if group != last:
            head = QStandardItem(group)
            head.setFlags(Qt.NoItemFlags)                       # (not selectable, not enabled)
            f = QFont()
            f.setBold(True)
            head.setFont(f)
            model.appendRow(head)
            last = group
        box.addItem("    " + label, k)
    if box.currentData() is None:                                # (the first heading is not a choice)
        box.setCurrentIndex(1)


def _combo() -> QComboBox:
    c = QComboBox()
    c.setSizeAdjustPolicy(QComboBox.AdjustToMinimumContentsLengthWithIcon)
    c.setMinimumContentsLength(4)
    return c


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
        last = None
        for k, label, group in seq.grouped_strategies():
            if group != last:
                head = QLabel(f"<b>{group}</b>")
                form.addRow(head)
                last = group
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


class CutList(QTreeWidget):
    """The cuts in the order they are made; a row can be dragged to another place (``moved(key, position)``)."""
    moved = Signal(str, int)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setHeaderLabels(["#", "Vector", "Cut", "Start point"])
        self.setRootIsDecorated(False)
        self.setUniformRowHeights(True)
        self.setSelectionMode(QAbstractItemView.SingleSelection)
        self.setDragDropMode(QAbstractItemView.InternalMove)
        self.setDefaultDropAction(Qt.MoveAction)
        h = self.header()
        h.setStretchLastSection(True)
        h.setSectionResizeMode(0, QHeaderView.ResizeToContents)
        h.setSectionResizeMode(1, QHeaderView.Stretch)
        h.setSectionResizeMode(2, QHeaderView.ResizeToContents)

    def dropEvent(self, ev) -> None:
        item = self.currentItem()
        target = self.itemAt(ev.position().toPoint())
        ev.ignore()                                      # (the panel rebuilds the list from the new order)
        if item is None:
            return
        src = self.indexOfTopLevelItem(item)
        if target is None:
            dst = self.topLevelItemCount()
        else:
            dst = self.indexOfTopLevelItem(target)
            if self.dropIndicatorPosition() == QAbstractItemView.BelowItem:
                dst += 1
        if dst > src:
            dst -= 1                                     # (the dragged row is taken out first)
        if dst != src:
            self.moved.emit(item.data(0, Qt.UserRole), dst)


class CutOrderPanel(DocPanel):
    signals = ("toolpaths_changed", "changed")

    def __init__(self, ctrl, window=None, parent=None):
        super().__init__(parent)
        self.ctrl = ctrl
        self.window_ = window
        self._loading = True
        self._syncing = False
        self._pending: dict[str, dict] = {}   # settings chosen and not applied yet, by toolpath
        self._advanced: list = []             # (form, row) of the settings that are rarely used: behind "More options"
        self._dirty = True                    # (what is shown is not what the document says: see showEvent)
        self._shown_preview = None
        self._cuts: list[dict] = []
        body = QWidget()
        lay = QVBoxLayout(body)
        lay.setContentsMargins(6, 6, 6, 6)

        self.tp = _combo()
        row = QFormLayout()
        row.setFieldGrowthPolicy(QFormLayout.AllNonFixedFieldsGrow)
        row.addRow("Toolpath", self.tp)
        lay.addLayout(row)

        lay.addWidget(self._order_group())
        lay.addWidget(self._starts_group())
        lay.addWidget(self._preview_bar())
        self.more = QPushButton()
        self.more.setCheckable(True)
        self.more.setFlat(True)
        self.more.setToolTip("Row size, inner pieces, direction, clear-of-cuts, what is shown on the drawing and the "
                             "start point of one cut: the settings that are rarely changed")
        lay.addWidget(self.more)
        self._more_boxes = [self._view_group(), self._start_edit_group()]
        lay.addWidget(self._more_boxes[0])
        lay.addWidget(self._cuts_group(), 1)
        lay.addWidget(self._more_boxes[1])
        self.info = QLabel()
        self.info.setWordWrap(True)
        lay.addWidget(self.info)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        scroll.setWidget(body)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(scroll)

        self.more.toggled.connect(self._show_more)
        self._show_more(False)
        self._wire()
        self._connect(ctrl)
        self._refresh()

    def _show_more(self, on: bool) -> None:
        self.more.setText("▾ Fewer options" if on else "▸ More options")
        for form, row in self._advanced:
            form.setRowVisible(row, on)
        for box in self._more_boxes:
            box.setVisible(on)

    def _connect(self, ctrl) -> None:
        super()._connect(ctrl)
        ctrl.selection_changed.connect(self.refresh_selection)
        ctrl.cut_view_changed.connect(self._view_changed)

    def bind(self, ctrl) -> None:
        if ctrl is not self.ctrl:
            for sig, slot in (("selection_changed", self.refresh_selection), ("cut_view_changed", self._view_changed)):
                try:
                    getattr(self.ctrl, sig).disconnect(slot)
                except (RuntimeError, TypeError):
                    pass
        super().bind(ctrl)

    # -- building ---------------------------------------------------------------------------------------
    def _order_group(self) -> QGroupBox:
        box = QGroupBox("Order")
        form = QFormLayout(box)
        form.setFieldGrowthPolicy(QFormLayout.AllNonFixedFieldsGrow)
        self.auto = QCheckBox("Smart order (re-order automatically)")
        self.auto.setToolTip("On: the cuts are put in the order of the strategy again whenever the toolpath is "
                             "calculated. Off: the order is frozen; new vectors are added at the end.")
        form.addRow(self.auto)
        self._advanced.append((form, self.auto))
        self.strategy = _combo()
        fill_strategies(self.strategy)                         # (in groups: columns, rows, routes, ...)
        self.strategy.setToolTip("Smart: the shortest route between the cuts (nearest next, then improved), "
                                 "inner pieces first. The others sweep the sheet from a corner, by rows, ...")
        form.addRow("Strategy", self.strategy)
        self.grouping = _combo()
        for k, label in seq.GROUPINGS:
            self.grouping.addItem(label, k)
        self.grouping.setToolTip("Göbek: the inner pieces of a part (holes, the hub of a ring, parts nested in "
                                 "holes). First option: all of them are cut first, then the outlines.")
        form.addRow("Inner pieces", self.grouping)
        self._advanced.append((form, self.grouping))
        self.band = _length_spin(self.ctrl, 0.0, "Automatic")
        self.band.setToolTip("Row / column size: the height of a row (width of a column) for the row and column strategies")
        form.addRow("Row size", self.band)
        self._advanced.append((form, self.band))
        self.direction = _combo()
        for k, label in seq.DIRECTIONS:
            self.direction.addItem(label, k)
        self.direction.setToolTip("Cutting direction used with this strategy (every strategy keeps its own); holes "
                                  "mill like their outline. Start points take the direction into account.")
        self.all_dirs = QPushButton("All...")
        self.all_dirs.setToolTip("Set the direction of every strategy at once, and the defaults of new toolpaths")
        r = QHBoxLayout()
        r.addWidget(self.direction, 1)
        r.addWidget(self.all_dirs)
        form.addRow("Direction", r)
        self._advanced.append((form, r))
        r = QHBoxLayout()
        self.preview_order = QPushButton("Preview")
        self.preview_order.setToolTip("Show the order the strategy would give now, and what it saves")
        self.reorder = QPushButton("Re-order now")
        self.reorder.setToolTip("Order the cuts with the strategy once (a frozen order stays frozen)")
        r.addWidget(self.preview_order)
        r.addWidget(self.reorder)
        form.addRow(r)
        self._advanced.append((form, r))
        return box

    def _starts_group(self) -> QGroupBox:
        box = QGroupBox("Start points")
        form = QFormLayout(box)
        form.setFieldGrowthPolicy(QFormLayout.AllNonFixedFieldsGrow)
        self.smart = QCheckBox("Smart start points (automatic)")
        self.smart.setToolTip("Every calculation puts each start point at the best place of its vector (a node is "
                              "made there); start points set by hand are kept")
        form.addRow(self.smart)
        self.margin = _length_spin(self.ctrl, 1.0)
        self.margin.setToolTip("Clear of cuts: free material kept between the plunging tool and areas cut before")
        form.addRow("Clear of cuts", self.margin)
        self._advanced.append((form, self.margin))
        self.corner = _length_spin(self.ctrl, 0.0, "Automatic")
        self.corner.setToolTip("Off corners: keep start points this far from corners")
        form.addRow("Off corners", self.corner)
        self._advanced.append((form, self.corner))
        r = QGridLayout()
        self.preview_starts = QPushButton("Preview")
        self.preview_starts.setToolTip("Show where smart start points would put the start points (nothing moves yet)")
        self.now = QPushButton("Optimise now")
        self.now.setToolTip("Move the start points once (undoable), even with the automatic mode off")
        self.release = QPushButton("Release")
        self.release.setToolTip("Start points of the selected vectors (or of the whole toolpath) may be moved by "
                                "smart start points again")
        self.tool = QPushButton("Draw (B)")
        self.tool.setToolTip("Click on a vector, or drag a start point along it, on the drawing")
        r.addWidget(self.preview_starts, 0, 0)
        r.addWidget(self.now, 0, 1)
        r.addWidget(self.release, 1, 0)
        r.addWidget(self.tool, 1, 1)
        form.addRow(r)
        self._advanced.append((form, r))
        return box

    def _preview_bar(self) -> QGroupBox:
        self.bar = QGroupBox("Preview (pink on the drawing)")
        lay = QVBoxLayout(self.bar)
        self.summary = QLabel()
        self.summary.setWordWrap(True)
        lay.addWidget(self.summary)
        r = QHBoxLayout()
        self.apply_btn = QPushButton("Apply")
        self.apply_btn.setToolTip("Calculate this toolpath with the settings above (the only thing that does)")
        self.cancel_btn = QPushButton("Cancel")
        self.cancel_btn.setToolTip("Forget the changes (and the preview)")
        self.reset_btn = QPushButton("Reset order")
        self.reset_btn.setToolTip("Apply the settings and order the cuts again from nothing: a hand-made or frozen "
                                  "order is forgotten")
        r.addWidget(self.apply_btn)
        r.addWidget(self.reset_btn)
        r.addWidget(self.cancel_btn)
        lay.addLayout(r)
        self.bar.hide()
        return self.bar

    def _view_group(self) -> QGroupBox:
        box = QGroupBox("Show on the drawing")
        grid = QGridLayout(box)
        self.view: dict[str, QCheckBox] = {}
        for i, (key, label) in enumerate(VIEW_OPTIONS):
            c = QCheckBox(label)
            c.setChecked(bool(getattr(self.ctrl.settings, key)))
            self.view[key] = c
            grid.addWidget(c, i // 2, i % 2)
        legend = QLabel(
            '<span style="color:#e6550d">\u25b6 clockwise</span> &nbsp; <span style="color:#1b9e77">\u25b6 '
            'counter-clockwise</span><br><span style="color:#41ab5d">\u25cf good start</span> &nbsp; '
            '<span style="color:#7a6ab8">\u25a0 by hand</span> &nbsp; <span style="color:#d94801">\u25b2 check</span>'
            ' &nbsp; <span style="color:#737373">\u25cb as drawn</span><br>numbers: <span style="color:#3a6fd8">'
            'first</span> \u2192 <span style="color:#d9541e">last</span>')
        legend.setWordWrap(True)
        legend.setTextFormat(Qt.RichText)
        self.live = QCheckBox("Calculate while I change settings")
        self.live.setToolTip("Off (the default): choosing a setting calculates nothing; Preview shows the "
                                      "result in pink and Apply makes it so. On: every choice is applied at once.")
        self.live.setChecked(bool(self.ctrl.settings.recall(MEMORY_KEY).get("live", False)))
        grid.addWidget(self.live, 2, 0, 1, 2)
        grid.addWidget(legend, 3, 0, 1, 2)
        return box

    def _cuts_group(self) -> QGroupBox:
        box = QGroupBox("Cuts in the order they are made")
        lay = QVBoxLayout(box)
        self.list = CutList()
        self.list.setMinimumHeight(120)
        lay.addWidget(self.list, 1)
        r = QHBoxLayout()
        self.up, self.down = QPushButton("Up"), QPushButton("Down")
        self.start_at = QSpinBox()
        self.start_at.setRange(1, 1)
        self.start_at.setPrefix("#")
        self.start_at.setMaximumWidth(64)
        self.start_at.setToolTip("Where the numbering starts: from # on (from the end: # places from the end, #1 is the last)")
        self.pick_mode = QComboBox()
        for k, label, tip in (("from", "Number from #", "The cuts you click take the places from # on"),
                              ("end", "Number from the end", "The first cut you click is cut last (# places from the "
                                                             "end: #1 is the last), the next one before it, ..."),
                              ("swap", "Swap two", "Click two cuts: they exchange their places (as many pairs as you like)")):
            self.pick_mode.addItem(label, k)
            self.pick_mode.setItemData(self.pick_mode.count() - 1, tip, Qt.ToolTipRole)
        self.pick_mode.setToolTip("How the cuts you click on the drawing are numbered")
        self.pick = QPushButton("Click to number...")
        self.pick.setToolTip("Click the vectors on the drawing in the order they should be cut (the numbers they "
                             "will have are shown as you click). Ctrl+click a cut: numbering goes on after it.")
        for w in (self.up, self.down):
            r.addWidget(w)
        lay.addLayout(r)
        r = QHBoxLayout()
        for w in (self.pick_mode, self.start_at, self.pick):
            r.addWidget(w)
        lay.addLayout(r)
        self.pick_mode.currentIndexChanged.connect(
            lambda: self.start_at.setEnabled(self.pick_mode.currentData() != "swap"))
        return box

    def _start_edit_group(self) -> QGroupBox:
        self.edit_box = QGroupBox("Start point of the selected cut")
        lay = QVBoxLayout(self.edit_box)
        self.explain = QLabel("Select a cut in the list.")
        self.explain.setWordWrap(True)
        lay.addWidget(self.explain)
        r = QHBoxLayout()
        self.back = QToolButton()
        self.back.setText("◀")
        self.back.setToolTip("Move the start point backwards along the cut")
        self.forward = QToolButton()
        self.forward.setText("▶")
        self.forward.setToolTip("Move the start point forwards along the cut")
        self.step = _length_spin(self.ctrl, 5.0)
        self.step.setRange(0.01, 1e5)
        self.step.setToolTip("How far the arrows move the start point")
        r.addWidget(self.back)
        r.addWidget(self.step, 1)
        r.addWidget(self.forward)
        lay.addLayout(r)
        grid = QGridLayout()
        self.places: dict[str, QToolButton] = {}
        cells = {"nw": (0, 0), "n": (0, 1), "ne": (0, 2), "w": (1, 0), "e": (1, 2), "sw": (2, 0), "s": (2, 1),
                 "se": (2, 2)}
        for key, arrow, tip in PLACES:
            b = QToolButton()
            b.setText(arrow)
            b.setToolTip(f"Start at the {tip.lower()}")
            self.places[key] = b
            grid.addWidget(b, *cells[key])
        centre = QLabel("start")
        centre.setAlignment(Qt.AlignCenter)
        grid.addWidget(centre, 1, 1)
        lay.addLayout(grid)
        r = QHBoxLayout()
        self.pick_start = QPushButton("Pick...")
        self.pick_start.setToolTip("Click on the vector where this cut should start")
        self.smart_one = QPushButton("Smart")
        self.smart_one.setToolTip("Let smart start points choose the start of this cut")
        self.release_one = QPushButton("Release")
        self.release_one.setToolTip("Give a start point set by hand back to smart start points")
        for w in (self.pick_start, self.smart_one, self.release_one):
            r.addWidget(w)
        lay.addLayout(r)
        note = QLabel("The place of the cut in the order does not change.")
        note.setWordWrap(True)
        lay.addWidget(note)
        return self.edit_box

    def _wire(self) -> None:
        self.tp.currentIndexChanged.connect(self._toolpath_changed)
        self.auto.toggled.connect(lambda v: self._set_now(auto_order=v))
        self.strategy.currentIndexChanged.connect(lambda: self._change(strategy=self.strategy.currentData()))
        self.grouping.currentIndexChanged.connect(lambda: self._change(grouping=self.grouping.currentData()))
        self.band.editingFinished.connect(lambda: self._change(band=self.ctrl.units.from_display(self.band.value())))
        self.direction.currentIndexChanged.connect(self._direction_changed)
        self.all_dirs.clicked.connect(self._all_directions)
        self.preview_order.clicked.connect(self._preview_order)
        self.reorder.clicked.connect(self._reorder)
        self.smart.toggled.connect(lambda v: self._set_now(smart_starts=v))
        self.margin.editingFinished.connect(
            lambda: self._set_now(start_margin=self.ctrl.units.from_display(self.margin.value())))
        self.corner.editingFinished.connect(
            lambda: self._set_now(corner_distance=self.ctrl.units.from_display(self.corner.value())))
        self.preview_starts.clicked.connect(self._preview_starts)
        self.now.clicked.connect(self._optimise_now)
        self.release.clicked.connect(self._release)
        self.tool.clicked.connect(self._start_tool)
        self.apply_btn.clicked.connect(self._apply)
        self.cancel_btn.clicked.connect(self._cancel)
        self.reset_btn.clicked.connect(self._reset_order)
        for key, c in self.view.items():
            c.toggled.connect(lambda v, k=key: self._set_view(k, v))
        self.live.toggled.connect(self._remember)
        self.list.currentItemChanged.connect(self._row_changed)
        self.list.moved.connect(self._dragged)
        self.up.clicked.connect(lambda: self._move(-1))
        self.down.clicked.connect(lambda: self._move(1))
        self.pick.clicked.connect(self._pick)
        self.back.clicked.connect(lambda: self._step(-1))
        self.forward.clicked.connect(lambda: self._step(1))
        for key, b in self.places.items():
            b.clicked.connect(lambda checked=False, k=key: self._place(k))
        self.pick_start.clicked.connect(self._pick_start)
        self.smart_one.clicked.connect(self._smart_one)
        self.release_one.clicked.connect(self._release_one)

    # -- the toolpath and what it says ------------------------------------------------------------------
    @property
    def tid(self) -> str | None:
        return self.tp.currentData()

    def tdef(self):
        return self.ctrl.project.toolpath(self.tid) if self.tid else None

    def cfg(self) -> dict:
        t = self.tdef()
        return seq.settings(t.sequence if t else None)

    def show_toolpath(self, tid: str | None = None) -> None:
        i = self.tp.findData(tid or default_toolpath(self.ctrl, self.window_))
        if i >= 0:
            self.tp.setCurrentIndex(i)

    def _toolpath_changed(self, *_) -> None:
        if not self._loading:
            self.ctrl.clear_cut_preview()
            self.ctrl.set_focus_cut(None)
            self.refresh()

    def _preview(self):
        p = self.ctrl.cut_preview
        return p if p is not None and p.tid == self.tid else None

    def refresh(self) -> None:
        """The document changed: show it again (when the panel is on show; otherwise when it is)."""
        if self.isVisible():
            self._refresh()
        else:
            self._dirty = True

    def showEvent(self, ev) -> None:
        super().showEvent(ev)
        if self._dirty:
            self._refresh()

    def sync_view(self) -> None:
        """The "show on the drawing" options were changed elsewhere (the View menu)."""
        self._loading = True
        try:
            for key, c in self.view.items():
                c.setChecked(bool(getattr(self.ctrl.settings, key)))
        finally:
            self._loading = False

    def _view_changed(self) -> None:
        """A preview came or went (the cut that is looked at changing is nothing to show again)."""
        if self.ctrl.cut_preview is not self._shown_preview:
            self.refresh()

    def _refresh(self) -> None:
        """Show the toolpath (or the preview of it): settings, the list of cuts and what is said about them."""
        ctrl = self.ctrl
        self._dirty = False
        self._shown_preview = ctrl.cut_preview
        self._loading = True
        try:
            keep = self.tid
            self.tp.clear()
            for t in sequenced_toolpaths(ctrl):
                self.tp.addItem(t.name, t.id)
            i = self.tp.findData(keep or default_toolpath(ctrl, self.window_))
            self.tp.setCurrentIndex(max(0, i))
            for key, c in self.view.items():
                c.setChecked(bool(getattr(ctrl.settings, key)))
            c = self.cfg()
            p = self._preview()
            wait = self._pending.get(self.tid) or {}
            shown = {**c, **wait, **(p.changes if p else {})}
            for w, v in ((self.strategy, shown["strategy"]), (self.grouping, shown["grouping"]),
                         (self.direction, seq.direction_for(shown))):
                w.blockSignals(True)
                i = w.findData(v)
                w.setCurrentIndex(i if i >= 0 else w.findData(seq.DEFAULTS["strategy"]) if w is self.strategy else 0)
                w.blockSignals(False)
            for w, v in ((self.auto, bool(shown["auto_order"])), (self.smart, bool(shown["smart_starts"]))):
                w.blockSignals(True)
                w.setChecked(v)
                w.blockSignals(False)
            u = ctrl.units
            self.band.setValue(u.to_display(shown["band"]))
            self.margin.setValue(u.to_display(shown["start_margin"]))
            self.corner.setValue(u.to_display(shown["corner_distance"]))
            for w in (self.band, self.margin, self.corner, self.step):
                w.setDecimals(u.decimals)
                w.setSuffix(u.suffix)
            enabled = self.tid is not None
            for w in (self.tp, self.auto, self.strategy, self.grouping, self.band, self.direction, self.all_dirs,
                      self.preview_order, self.reorder, self.smart, self.margin, self.corner, self.preview_starts,
                      self.now, self.release, self.tool, self.list, self.up, self.down, self.start_at, self.pick, self.pick_mode):
                w.setEnabled(enabled)
            self.start_at.setEnabled(enabled and self.pick_mode.currentData() != "swap")
            self._fill_list()
            self._summary(p)
        finally:
            self._loading = False
        self._row_state()
        ctrl.set_focus_cut(self.focus_key())

    def _fill_list(self) -> None:
        ctrl = self.ctrl
        row = self.focus_key()
        self.list.blockSignals(True)
        self.list.clear()
        self._cuts = ctrl.cuts_of(self.tid, preview=True) if self.tid else []
        for n, cut in enumerate(self._cuts):
            vid, ci = cut["key"].rsplit(":", 1)
            o = ctrl.find_vector(vid)
            name = (o.name or "Vector") if o else "?"
            if o is not None and len(o.contours) > 1:
                name += f" ({int(ci) + 1})"
            e = cutinfo.explain(cut)
            d = cutinfo.DIRECTION_ARROWS[e["direction"]] + " " + (
                "cw" if e["direction"] == "cw" else "ccw" if e["direction"] == "ccw" else "line")
            mark, color = QUALITY_MARK[e["quality"]] if cut.get("closed") else ("", "#737373")
            it = QTreeWidgetItem([str(n + 1), name, d, f"{mark}  {e['place']}".strip() if mark else "open: on the line"])
            it.setData(0, Qt.UserRole, cut["key"])
            it.setForeground(3, QColor(color))
            it.setToolTip(3, "\n".join(e["lines"]))
            it.setToolTip(2, f"Cut {cutinfo.DIRECTION_NAMES[e['direction']]}")
            self.list.addTopLevelItem(it)
            if cut["key"] == row:
                self.list.setCurrentItem(it)
        self.start_at.setRange(1, max(1, len(self._cuts)))
        self.list.blockSignals(False)

    def _summary(self, p) -> None:
        ctrl = self.ctrl
        u = ctrl.units
        wait = self._pending.get(self.tid) if self.tid else None
        if p is not None:
            self.summary.setText(p.summary(lambda v: f"{u.to_display(v):.{0 if u.name == 'mm' else 1}f}{u.suffix}"))
            self.bar.setTitle("Preview (pink on the drawing)")
            self.bar.show()
        elif wait:
            self.summary.setText("Chosen, not applied yet: " + ", ".join(sorted(wait)) +
                                 ".\nNothing is calculated until Apply.")
            self.bar.setTitle("Settings waiting")
            self.bar.show()
        else:
            self.bar.hide()
        notes = []
        if self.tid:
            tdef = self.tdef()
            notes = [w for r in (ctrl.results_for(tdef) if tdef else []) for tp in r.toolpaths for w in tp.warnings
                     if "order" in w.lower() or "parts" in w.lower() or "start point" in w.lower()]
        cuts = self._cuts
        text = f"{len(cuts)} cut(s)"
        marks = [cutinfo.explain(c)["quality"] for c in cuts if c.get("closed")]
        if marks:
            words = (("good", "good"), ("hand", "set by hand"), ("warn", "to check"), ("plain", "as drawn"))
            text += " \u00b7 start points: " + ", ".join(f"{marks.count(k)} {w}" for k, w in words if marks.count(k))
        if len(cuts) > 1:
            text += f", {u.to_display(cutinfo.route_length(cuts, ctrl.route_home())):.{0 if u.name == 'mm' else 1}f}" \
                    f"{u.suffix} between the cuts"
        if ctrl.stale and not ctrl.settings.auto_recalculate:
            text += " (the toolpaths are not calculated: F5)"
        self.info.setText("\n".join([text] + list(dict.fromkeys(notes))))

    # -- what the settings do ----------------------------------------------------------------------------
    def _remember(self, *_) -> None:
        if not self._loading:
            self.ctrl.settings.remember(MEMORY_KEY, {"live": self.live.isChecked()})

    def _set_view(self, key: str, value: bool) -> None:
        if self._loading:
            return
        if self.window_ is not None and hasattr(self.window_, "set_view"):
            self.window_.set_view(key, value)
        else:
            setattr(self.ctrl.settings, key, value)
            self.ctrl.cut_view_changed.emit()

    def pending(self) -> dict:
        """The settings that were chosen on the panel and are not applied yet (nothing is calculated for them)."""
        return self._pending.setdefault(self.tid, {}) if self.tid else {}

    def _stage(self, **changes) -> None:
        """A setting was chosen: it waits for Apply (or a Preview); no calculation is made for it."""
        if self._loading or not self.tid:
            return
        shown = {**self.cfg(), **(self._pending.get(self.tid) or {})}
        # a box that was left without a change (or one that rounds the number: inches) stages nothing
        changes = {k: v for k, v in changes.items()
                   if not (isinstance(v, (int, float)) and isinstance(shown.get(k), (int, float)) and abs(v - shown[k]) < 1e-3)
                   and shown.get(k) != v}
        if not changes:
            return
        self.ctrl.clear_cut_preview()
        if self.live.isChecked():                   # (the person asked for every choice to be applied at once)
            self.ctrl.set_sequence(self.tid, **changes)
            self.ctrl.remember_cut_order(**changes)
            return
        self.pending().update(changes)
        self._summary(self._preview())

    _set_now = _change = _stage

    def _discard(self) -> None:
        self._pending.pop(self.tid, None)
        self.ctrl.clear_cut_preview()
        self.refresh()
        self._summary(None)

    def _direction_changed(self) -> None:
        if self._loading or not self.tid:
            return
        dirs = dict({**self.cfg(), **(self._pending.get(self.tid) or {})}["directions"])
        p = self._preview()
        if p is not None and "directions" in p.changes:
            dirs = dict(p.changes["directions"])
        dirs[self.strategy.currentData()] = self.direction.currentData()
        self._change(directions=dirs)

    def _all_directions(self) -> None:
        if not self.tid:
            return
        dlg = DirectionsDialog(self.ctrl, {**self.cfg(), **(self._pending.get(self.tid) or {})}, self)
        if dlg.exec():
            self._set_now(directions=dlg.directions())
            if dlg.as_default.isChecked():
                self.ctrl.settings.strategy_directions = dlg.directions()
                self.ctrl.settings.save()

    def _preview_order(self) -> None:
        if self.tid:
            p = self._preview()
            self.ctrl.preview_cut_order(self.tid, {**(self._pending.get(self.tid) or {}), **(p.changes if p else {}),
                                                   "order": []}, p.smart_starts if p else False)

    def _take_pending(self) -> dict:
        """The settings that wait: they leave the waiting list because they are about to be applied."""
        changes = dict(self._pending.pop(self.tid, None) or {})
        if changes:
            self.ctrl.remember_cut_order(**changes)
        return changes

    def _reorder(self) -> None:
        if self.tid:
            self.ctrl.clear_cut_preview()
            self.ctrl.set_sequence(self.tid, order=[], **self._take_pending())

    def _reset_order(self) -> None:
        """Order the cuts again from nothing (the frozen or hand-made order is forgotten), with the settings chosen."""
        if self.tid:
            self.ctrl.clear_cut_preview()
            self.ctrl.reset_order(self.tid, **self._take_pending())

    def _preview_starts(self) -> None:
        if self.tid:
            p = self._preview()
            self.ctrl.preview_cut_order(self.tid, {**(self._pending.get(self.tid) or {}), **(p.changes if p else {})}, True)

    def _optimise_now(self) -> None:
        if self.tid:
            self.ctrl.clear_cut_preview()
            changes = self._take_pending()
            if changes:
                self.ctrl.set_sequence(self.tid, **changes)
            self.ctrl.optimise_starts({self.tid})

    def _release(self) -> None:
        t = self.tdef()
        if t is None:
            return
        objs = self.ctrl.project.vectors_for(t, self.ctrl.sheet)
        sel = set(self.ctrl.selection)
        ids = [o.id for o in objs if o.id in sel] or [o.id for o in objs]
        self.ctrl.unlock_starts(ids)
        self.ctrl.calc_if_auto({t.id})

    def _start_tool(self) -> None:
        from .tools import StartPointTool
        canvas = getattr(self.window_, "canvas", None)
        if canvas is not None:
            canvas.set_tool(StartPointTool(canvas))

    def _apply(self) -> None:
        """The one command that calculates: the preview becomes real, or the settings that wait are applied."""
        if self.ctrl.cut_preview is not None:
            self._pending.pop(self.tid, None)
            self.ctrl.apply_cut_preview()
        elif self.tid and self._pending.get(self.tid):
            self.ctrl.set_sequence(self.tid, **self._take_pending())
        self._summary(None)

    def _cancel(self) -> None:
        self._discard()

    # -- the list ---------------------------------------------------------------------------------------------
    def focus_key(self) -> str | None:
        it = self.list.currentItem()
        return it.data(0, Qt.UserRole) if it is not None else None

    def _cut(self, key: str | None = None) -> dict | None:
        key = key or self.focus_key()
        return next((c for c in self._cuts if c["key"] == key), None)

    def _row_state(self) -> None:
        """The start point box is about the cut of the selected row (and is for closed cuts only)."""
        cut = self._cut()
        closed = bool(cut and cut.get("closed"))
        for w in (self.back, self.forward, self.step, self.pick_start, self.smart_one, self.release_one,
                  *self.places.values()):
            w.setEnabled(closed and self._preview() is None)
        if cut is None:
            self.explain.setText("Select a cut in the list.")
        elif closed:
            self.explain.setText("\n".join(cutinfo.explain(cut)["lines"]))
        else:
            self.explain.setText("An open vector is cut on the line, from one end to the other: it has no start "
                                 "point to move.")

    def _row_changed(self, *_) -> None:
        """A row was selected: the drawing points at that cut and its vector is the selection."""
        if self._loading or self._syncing:
            return
        self._row_state()
        key = self.focus_key()
        self._syncing = True
        try:
            self.ctrl.set_focus_cut(key)
            if key is not None:
                vid = key.rsplit(":", 1)[0]
                if self.ctrl.selection != [vid]:
                    self.ctrl.select([vid])
        finally:
            self._syncing = False

    def select_cut(self, key: str | None) -> None:
        """Point the list at a cut (from the drawing)."""
        for i in range(self.list.topLevelItemCount()):
            it = self.list.topLevelItem(i)
            if it.data(0, Qt.UserRole) == key:
                self.list.setCurrentItem(it)
                return

    def refresh_selection(self) -> None:
        """The selection of the drawing changed: when it is one vector with a cut, the list points at that cut."""
        if self._syncing or self._loading:
            return
        sel = self.ctrl.selection
        if len(sel) == 1:
            for cut in self._cuts:
                if cut["key"].rsplit(":", 1)[0] == sel[0]:
                    if cut["key"] != self.focus_key():
                        self._syncing = True
                        try:
                            self.select_cut(cut["key"])
                            self.ctrl.set_focus_cut(cut["key"])
                        finally:
                            self._syncing = False
                        self._row_state()
                    return
        # nothing, several vectors or a vector this toolpath does not cut: no cut is looked at any more (the yellow line
        # of the cut that was goes)
        if self.focus_key() is not None or self.ctrl.focus_cut is not None:
            self._syncing = True
            try:
                self.list.setCurrentItem(None)
                self.list.clearSelection()
                self.ctrl.set_focus_cut(None)
            finally:
                self._syncing = False
            self._row_state()

    def _move(self, delta: int) -> None:
        r = self.list.indexOfTopLevelItem(self.list.currentItem())
        if r < 0 or not 0 <= r + delta < self.list.topLevelItemCount() or not self.tid:
            return
        key = self.focus_key()
        self.ctrl.clear_cut_preview()
        self.ctrl.set_manual_order(self.tid, r + delta, [key])
        self.select_cut(key)

    def _dragged(self, key: str, position: int) -> None:
        if self.tid:
            self.ctrl.clear_cut_preview()
            self.ctrl.set_manual_order(self.tid, position, [key])
            self.select_cut(key)

    def _pick(self) -> None:
        from .tools import OrderTool
        canvas = getattr(self.window_, "canvas", None)
        if canvas is not None and self.tid:
            self.ctrl.clear_cut_preview()
            canvas.set_tool(OrderTool(canvas, self.tid, self.start_at.value() - 1, self.pick_mode.currentData()))

    # -- the start point of the selected cut ------------------------------------------------------------------
    def _step(self, sign: int) -> None:
        """Move the start along the cut: forwards is the way the tool goes."""
        cut, key = self._cut(), self.focus_key()
        if cut is None or not cut.get("closed") or key is None:
            return
        got = self.ctrl.contour_of(key)
        if got is None:
            return
        same = (cutinfo.cut_direction(cut) == "ccw") == got[2].is_ccw()
        delta = self.ctrl.units.from_display(self.step.value()) * sign * (1 if same else -1)
        self.ctrl.move_start(key, delta)
        self.select_cut(key)

    def _place(self, place: str) -> None:
        key = self.focus_key()
        if key is not None:
            self.ctrl.start_at_place(key, place)
            self.select_cut(key)

    def _pick_start(self) -> None:
        from .tools import StartPointTool
        canvas = getattr(self.window_, "canvas", None)
        key = self.focus_key()
        if canvas is not None and key is not None:
            canvas.set_tool(StartPointTool(canvas, only=key))

    def _smart_one(self) -> None:
        key = self.focus_key()
        if key is not None and self.tid:
            self.ctrl.smart_start_one(self.tid, key)
            self.select_cut(key)

    def _release_one(self) -> None:
        key = self.focus_key()
        if key is not None:
            self.ctrl.unlock_start(key)
            self.select_cut(key)

    # -- panel lifecycle --------------------------------------------------------------------------------------
    def hideEvent(self, ev) -> None:
        try:
            self.ctrl.clear_cut_preview()
            self.ctrl.set_focus_cut(None)
        except RuntimeError:                    # (the document is gone: the program is closing)
            pass
        super().hideEvent(ev)

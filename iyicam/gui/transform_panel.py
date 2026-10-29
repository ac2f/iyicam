"""The transform panel: numbers for where the selection is and how it is moved, turned, scaled, sized and slanted.

Pick a reference point (one of the nine points of the box around the selection), type the values and press Apply
(or Enter). "Make copies" leaves the vectors as they are and makes copies instead: the first one moved by the
transformation, the next one by it twice, and so on - a row of five copies is "Move X 100, 5 copies".
"""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QButtonGroup, QCheckBox, QComboBox, QDoubleSpinBox, QFormLayout, QGridLayout,
                               QHBoxLayout, QLabel, QPushButton, QScrollArea, QSpinBox, QStackedWidget, QToolButton,
                               QVBoxLayout, QWidget)

from ..core import placement
from ..core.geom import BBox, Transform
from .panels import DocPanel
from .settings import Units

PAGES = [("position", "Position"), ("rotate", "Rotate"), ("scale", "Scale and mirror"), ("size", "Size"),
         ("slant", "Slant")]
MEMORY_KEY = "transform_panel"


class TransformPanel(DocPanel):
    signals = ("selection_changed", "changed")

    def __init__(self, ctrl, parent=None):
        super().__init__(parent)
        self.ctrl = ctrl
        self._bb: BBox | None = None
        self._busy = False
        self._units_name = ctrl.units.name
        self.length_spins: list[QDoubleSpinBox] = []
        self.f: dict[str, QDoubleSpinBox | QCheckBox | QComboBox] = {}

        body = QWidget()
        lay = QVBoxLayout(body)
        lay.setContentsMargins(6, 6, 6, 6)
        self.info = QLabel("Nothing selected")
        self.info.setWordWrap(True)
        lay.addWidget(self.info)
        self.page = self._combo()
        self.page.setToolTip("What to do with the selection (Alt+F7 position, Alt+F8 rotate, Alt+F9 scale and "
                             "mirror, Alt+F10 size, Alt+F11 slant)")
        for key, title in PAGES:
            self.page.addItem(title, key)
        lay.addWidget(self.page)
        self.stack = QStackedWidget()
        for build in (self._position_page, self._rotate_page, self._scale_page, self._size_page, self._slant_page):
            self.stack.addWidget(build())
        lay.addWidget(self.stack)
        lay.addLayout(self._bottom())
        lay.addStretch(1)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        scroll.setWidget(body)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(scroll)

        self.page.currentIndexChanged.connect(self._page_changed)
        self._restore()
        self._about_changed()
        self._connect(ctrl)
        self.refresh()

    # -- building ---------------------------------------------------------------------------------
    def _spin(self, name: str, lo: float, hi: float, value: float, step: float = 1.0, decimals: int = 3,
              suffix: str = "", length: bool = False) -> QDoubleSpinBox:
        s = QDoubleSpinBox()
        s.setRange(lo, hi)
        s.setDecimals(decimals)
        s.setSingleStep(step)
        s.setSuffix(suffix)
        s.setValue(value)
        s.setKeyboardTracking(False)
        s.lineEdit().returnPressed.connect(self.apply)
        self.f[name] = s
        if length:
            self.length_spins.append(s)
        return s

    @staticmethod
    def _combo() -> QComboBox:
        """A combo box that may be as narrow as the panel (it elides what does not fit)."""
        c = QComboBox()
        c.setSizeAdjustPolicy(QComboBox.AdjustToMinimumContentsLengthWithIcon)
        c.setMinimumContentsLength(8)
        return c

    def _check(self, name: str, text: str, checked: bool) -> QCheckBox:
        c = QCheckBox(text)
        c.setChecked(checked)
        self.f[name] = c
        return c

    def _button(self, text: str, tip: str, slot) -> QPushButton:
        b = QPushButton(text)
        b.setMinimumWidth(0)
        b.setToolTip(tip)
        b.clicked.connect(lambda checked=False: slot())
        return b

    def _position_page(self) -> QWidget:
        w = QWidget()
        form = QFormLayout(w)
        form.setContentsMargins(0, 4, 0, 4)
        form.addRow("X", self._spin("px", -1e6, 1e6, 0.0, length=True))
        form.addRow("Y", self._spin("py", -1e6, 1e6, 0.0, length=True))
        rel = self._check("rel", "Relative: move by these amounts", False)
        rel.setToolTip("Off: the reference point goes to X, Y. On: the selection moves by X, Y.")
        rel.toggled.connect(self._relative_changed)
        form.addRow(rel)
        return w

    def _rotate_page(self) -> QWidget:
        w = QWidget()
        form = QFormLayout(w)
        form.setContentsMargins(0, 4, 0, 4)
        form.addRow("Angle", self._spin("angle", -3600.0, 3600.0, 90.0, 1.0, 3, " °"))
        about = self._combo()
        for key, label in (("ref", "Reference point"), ("origin", "Sheet corner (0, 0)"), ("point", "A point")):
            about.addItem(label, key)
        about.currentIndexChanged.connect(self._about_changed)
        self.f["about"] = about
        form.addRow("About", about)
        self._pivot_rows = (self._spin("cx", -1e6, 1e6, 0.0, length=True), self._spin("cy", -1e6, 1e6, 0.0, length=True))
        form.addRow("Point X", self._pivot_rows[0])
        form.addRow("Point Y", self._pivot_rows[1])
        self._pivot_form = form
        quick = QGridLayout()
        for i, (text, deg, tip) in enumerate((("↺ 90°", 90.0, "Quarter turn to the left"),
                                              ("↻ 90°", -90.0, "Quarter turn to the right"),
                                              ("180°", 180.0, "Half turn"),
                                              ("↺ 45°", 45.0, "Eighth of a turn to the left"),
                                              ("↻ 45°", -45.0, "Eighth of a turn to the right"))):
            quick.addWidget(self._button(text, tip, lambda d=deg: self.rotate_by(d)), i // 3, i % 3)
        form.addRow(quick)
        return w

    def _scale_page(self) -> QWidget:
        w = QWidget()
        form = QFormLayout(w)
        form.setContentsMargins(0, 4, 0, 4)
        sx = self._spin("sx", 0.001, 1e6, 100.0, 1.0, 3, " %")
        sy = self._spin("sy", 0.001, 1e6, 100.0, 1.0, 3, " %")
        sx.valueChanged.connect(lambda v: self._linked("scale", "sx", "sy", v))
        sy.valueChanged.connect(lambda v: self._linked("scale", "sy", "sx", v))
        form.addRow("Horizontal", sx)
        form.addRow("Vertical", sy)
        form.addRow(self._check("prop_scale", "Keep the proportions", True))
        flips = QGridLayout()
        flips.addWidget(self._button("↔ Left/right", "Mirror about the reference point, left to right",
                                     lambda: self.flip(True)), 0, 0)
        flips.addWidget(self._button("↕ Top/bottom", "Mirror about the reference point, top to bottom",
                                     lambda: self.flip(False)), 0, 1)
        form.addRow(flips)
        return w

    def _size_page(self) -> QWidget:
        w = QWidget()
        form = QFormLayout(w)
        form.setContentsMargins(0, 4, 0, 4)
        sw = self._spin("w", 0.001, 1e6, 100.0, length=True)
        sh = self._spin("h", 0.001, 1e6, 100.0, length=True)
        sw.valueChanged.connect(lambda v: self._linked("size", "w", "h", v))
        sh.valueChanged.connect(lambda v: self._linked("size", "h", "w", v))
        form.addRow("Width", sw)
        form.addRow("Height", sh)
        form.addRow(self._check("prop_size", "Keep the proportions", True))
        return w

    def _slant_page(self) -> QWidget:
        w = QWidget()
        form = QFormLayout(w)
        form.setContentsMargins(0, 4, 0, 4)
        form.addRow("Horizontal", self._spin("ax", -80.0, 80.0, 15.0, 1.0, 3, " °"))
        form.addRow("Vertical", self._spin("ay", -80.0, 80.0, 0.0, 1.0, 3, " °"))
        return w

    def _bottom(self) -> QHBoxLayout:
        row = QHBoxLayout()
        grid = QGridLayout()
        grid.setSpacing(2)
        self.anchor_group = QButtonGroup(self)
        self.anchor_group.setExclusive(True)
        self.anchors: dict[str, QToolButton] = {}
        for i, key in enumerate(("nw", "n", "ne", "w", "c", "e", "sw", "s", "se")):
            b = QToolButton()
            b.setCheckable(True)
            b.setFixedSize(22, 22)
            b.setText("○")
            b.setToolTip(f"Reference point: {placement.ANCHOR_LABELS[key].lower()}")
            b.toggled.connect(lambda on, b=b: b.setText("●" if on else "○"))
            self.anchor_group.addButton(b)
            self.anchors[key] = b
            grid.addWidget(b, i // 3, i % 3)
        self.anchors["sw"].setChecked(True)
        self.anchor_group.buttonToggled.connect(self._anchor_changed)
        box = QVBoxLayout()
        box.addWidget(QLabel("Reference point"))
        box.addLayout(grid)
        row.addLayout(box)
        right = QVBoxLayout()
        self.copy = self._check("copy", "Make copies", False)
        self.copy.setToolTip("Leave the vectors where they are and make copies instead")
        self.count = QSpinBox()
        self.count.setRange(1, 1000)
        self.count.setPrefix("Copies: ")
        self.count.setToolTip("How many copies: the first moved by the transformation, the next by it twice, ...")
        self.count.lineEdit().returnPressed.connect(self.apply)
        self.copy.toggled.connect(self.count.setEnabled)
        self.count.setEnabled(False)
        self.apply_button = QPushButton("Apply")
        self.apply_button.setToolTip("Apply to the selection (Enter in a field does the same)")
        self.apply_button.clicked.connect(lambda checked=False: self.apply())
        right.addWidget(self.copy)
        right.addWidget(self.count)
        right.addWidget(self.apply_button)
        row.addLayout(right, 1)
        return row

    # -- state ---------------------------------------------------------------------------------------
    @property
    def anchor(self) -> str:
        return next((k for k, b in self.anchors.items() if b.isChecked()), "sw")

    def show_page(self, key: str) -> None:
        i = self.page.findData(key)
        if i >= 0:
            self.page.setCurrentIndex(i)
        first = self.stack.currentWidget().findChild(QDoubleSpinBox)
        if first is not None and first.isEnabled():
            first.setFocus()
            first.selectAll()

    def _page_changed(self, i: int) -> None:
        self.stack.setCurrentIndex(i)
        self._save()

    def _anchor_changed(self, *_) -> None:
        self._fill_from_selection(size=False)         # (what was typed as a size stays)
        self._save()

    def _relative_changed(self, on: bool) -> None:
        if on:
            self._set("px", 0.0)
            self._set("py", 0.0)
        else:
            self._fill_from_selection(size=False)
        self._save()

    def _about_changed(self, *_) -> None:
        point = self.f["about"].currentData() == "point"
        for s in self._pivot_rows:
            self._pivot_form.setRowVisible(s, point)

    def _linked(self, which: str, this: str, other: str, value: float) -> None:
        """Keep the proportions: the other number follows the one that was typed."""
        if self._busy or not self.f["prop_" + which].isChecked():
            return
        if which == "size":
            if self._bb is None:
                return
            bw, bh = self._bb.width, self._bb.height
            if bw <= 1e-9 or bh <= 1e-9:
                return
            ratio = bh / bw if this == "w" else bw / bh
            v = value * ratio
        else:
            v = value
        self._set(other, v)

    def _set(self, name: str, value: float) -> None:
        s = self.f[name]
        self._busy = True
        try:
            s.setValue(value)
        finally:
            self._busy = False

    def _mm(self, name: str) -> float:
        return self.ctrl.units.from_display(self.f[name].value())

    def _typed(self, name: str, current: float) -> float:
        """The millimetres in a field; the exact ``current`` value when the field still shows it (rounded to the
        decimals of the field), so that applying what was not changed changes nothing."""
        s, u = self.f[name], self.ctrl.units
        typed = u.from_display(s.value())
        return current if abs(typed - current) <= 0.5 * 10 ** -s.decimals() * u.factor + 1e-12 else typed

    def _fill_from_selection(self, size: bool = True) -> None:
        """The fields show where the selection is (X, Y of the reference point) and how big it is. The amounts of
        a relative move are left as they were typed, to be applied again."""
        bb = self._bb
        if bb is None:
            return
        u = self.ctrl.units
        if not self.f["rel"].isChecked():
            x, y = placement.anchor_point(bb, self.anchor)
            self._set("px", u.to_display(x))
            self._set("py", u.to_display(y))
        if size:
            self._set("w", u.to_display(max(bb.width, 0.001)))
            self._set("h", u.to_display(max(bb.height, 0.001)))

    def _selection_box(self) -> tuple[list, BBox | None]:
        objs = [o for o in self.ctrl.selected_objects() if self.ctrl.editable(o)]
        bb = BBox()
        for o in objs:
            bb = bb.union(o.bbox())
        return objs, (None if bb.empty else bb)

    def _units_changed(self) -> None:
        """The numbers typed in the length fields are re-written in the units that are on now."""
        u, old = self.ctrl.units, Units(self._units_name)
        for s in self.length_spins:
            mm = old.from_display(s.value())
            s.setDecimals(u.decimals)
            s.setSuffix(u.suffix)
            s.setSingleStep(0.1 if u.name == "inch" else 1.0)
            self._busy = True
            s.setValue(u.to_display(mm))
            self._busy = False
        self._units_name = u.name

    def refresh(self) -> None:
        u = self.ctrl.units
        if u.name != self._units_name or self.length_spins[0].suffix() != u.suffix:
            self._units_changed()
        objs, bb = self._selection_box()
        self._bb = bb
        for w in (self.stack, self.apply_button, self.copy, self.page):
            w.setEnabled(bb is not None)
        self.count.setEnabled(bb is not None and self.copy.isChecked())
        if bb is None:
            self.info.setText("Nothing selected: select vectors to move, turn, scale or slant them.")
            return
        self.info.setText(f"<b>{len(objs)}</b> vector(s), {u.fmt(bb.width)} \u00d7 {u.fmt(bb.height)}")
        self._fill_from_selection()

    # -- applying -----------------------------------------------------------------------------------
    def _transform(self) -> Transform:
        """The transformation the open page describes; ValueError when its numbers make no sense."""
        bb, a, key = self._bb, self.anchor, self.page.currentData()
        if key == "position":
            if self.f["rel"].isChecked():
                return Transform.translate(self._mm("px"), self._mm("py"))
            ax, ay = placement.anchor_point(bb, a)
            return placement.move_to(bb, a, self._typed("px", ax), self._typed("py", ay))
        if key == "rotate":
            return placement.turn(self.f["angle"].value(), self._pivot())
        if key == "scale":
            return placement.scale_by(bb, a, self.f["sx"].value(), self.f["sy"].value())
        if key == "size":
            return placement.size_to(bb, a, self._typed("w", bb.width), self._typed("h", bb.height))
        return placement.slant(bb, a, self.f["ax"].value(), self.f["ay"].value())

    def _pivot(self) -> tuple[float, float]:
        about = self.f["about"].currentData()
        if about == "origin":
            return (0.0, 0.0)
        if about == "point":
            return (self._mm("cx"), self._mm("cy"))
        return placement.anchor_point(self._bb, self.anchor)

    def _run(self, T: Transform, label: str) -> bool:
        copies = self.count.value() if self.copy.isChecked() else 0
        ok = self.ctrl.transform_selection(T, copies, label + (" (copies)" if copies else ""))
        self._save()
        return ok

    def apply(self) -> bool:
        """Do what the open page says to the selection."""
        if self._bb is None:
            return False
        key = self.page.currentData()
        try:
            T = self._transform()
        except ValueError as exc:
            self.ctrl.message.emit(str(exc))
            return False
        return self._run(T, dict(PAGES)[key])

    def rotate_by(self, degrees: float) -> bool:
        if self._bb is None:
            return False
        return self._run(placement.turn(degrees, self._pivot()), "Rotate")

    def flip(self, left_right: bool) -> bool:
        if self._bb is None:
            return False
        return self._run(placement.flip(self._bb, self.anchor, left_right), "Mirror")

    # -- what is remembered ----------------------------------------------------------------------------
    NUMBERS = ("angle", "sx", "sy", "ax", "ay")
    FLAGS = ("rel", "prop_scale", "prop_size", "copy")

    def _save(self) -> None:
        if self._busy:
            return
        v = {"page": self.page.currentData(), "anchor": self.anchor, "about": self.f["about"].currentData(),
             "count": self.count.value()}
        v.update({k: self.f[k].value() for k in self.NUMBERS})
        v.update({k: self.f[k].isChecked() for k in self.FLAGS})
        self.ctrl.settings.remember(MEMORY_KEY, v)

    def _restore(self) -> None:
        v = self.ctrl.settings.recall(MEMORY_KEY)
        self._busy = True
        try:
            for k in self.NUMBERS:
                if isinstance(v.get(k), (int, float)) and not isinstance(v.get(k), bool):
                    self.f[k].setValue(float(v[k]))
            for k in self.FLAGS:
                if isinstance(v.get(k), bool):
                    self.f[k].setChecked(v[k])
            if isinstance(v.get("count"), int) and not isinstance(v.get("count"), bool):
                self.count.setValue(v["count"])
            i = self.f["about"].findData(v.get("about"))
            if i >= 0:
                self.f["about"].setCurrentIndex(i)
            if v.get("anchor") in self.anchors:
                self.anchors[v["anchor"]].setChecked(True)
            i = self.page.findData(v.get("page"))
            if i >= 0:
                self.page.setCurrentIndex(i)
                self.stack.setCurrentIndex(i)
        finally:
            self._busy = False
        self.count.setEnabled(self.copy.isChecked())

"""Widgets generated from :class:`~iyicam.core.params.Param` schemas."""
from __future__ import annotations

from typing import Any

from PySide6.QtCore import Signal
from PySide6.QtGui import QDoubleValidator, QFont
from PySide6.QtWidgets import (QCheckBox, QComboBox, QDoubleSpinBox, QFontComboBox, QFormLayout, QGroupBox,
                               QHBoxLayout, QLineEdit, QPlainTextEdit, QSpinBox, QVBoxLayout, QWidget)

from ..core.params import Param, is_visible
from ..core.tooling import Tool, ToolLibrary
from .settings import Units

LENGTH_KINDS = ("length",)


class OptionalNumber(QLineEdit):
    """Number entry that may be left empty (``None``)."""

    def __init__(self, value: float | None, suffix: str = "", parent=None):
        super().__init__(parent)
        self.setValidator(QDoubleValidator())
        self.setPlaceholderText("auto")
        self.suffix = suffix
        self.set_value(value)

    def set_value(self, v: float | None) -> None:
        self.setText("" if v is None else f"{v:g}")

    def value(self) -> float | None:
        t = self.text().strip().replace(",", ".")
        if not t:
            return None
        try:
            return float(t)
        except ValueError:
            return None


class ParamForm(QWidget):
    """Edits a dict of values described by a list of params.  Emits ``changed``."""

    changed = Signal()

    def __init__(self, params: list[Param], values: dict | None = None, units: Units | None = None,
                 tools: ToolLibrary | None = None, parent=None):
        super().__init__(parent)
        self.params = list(params)
        self.units = units or Units()
        self.tools = tools
        self._values = dict(values or {})
        self.widgets: dict[str, QWidget] = {}
        self._rows: dict[str, tuple[QFormLayout, QWidget]] = {}
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        groups: dict[str, QFormLayout] = {}
        for p in self.params:
            g = p.group or "Settings"
            if g not in groups:
                box = QGroupBox(g)
                lay = QFormLayout(box)
                lay.setFieldGrowthPolicy(QFormLayout.AllNonFixedFieldsGrow)
                outer.addWidget(box)
                groups[g] = lay
            w = self._make(p, self._values.get(p.name, p.default))
            w.setToolTip(p.help)
            groups[g].addRow(p.label, w)
            self.widgets[p.name] = w
            self._rows[p.name] = (groups[g], w)
        self._groups = groups
        outer.addStretch(1)
        self._update_visibility()

    # -- widget factory --------------------------------------------------------------------
    def _make(self, p: Param, v: Any) -> QWidget:
        k = p.kind
        u = self.units
        if k in ("float", "length", "angle", "feed", "percent"):
            conv = k in ("length", "feed")
            suffix = {"angle": " °", "percent": " %",
                      "feed": (" in/min" if u.name == "inch" else " mm/min")}.get(k, u.suffix if conv else "")
            if v is None:
                w = OptionalNumber(None if v is None else (u.to_display(v) if conv else v), suffix.strip())
                w.textChanged.connect(self._emit)
                return w
            w = QDoubleSpinBox()
            w.setDecimals(u.decimals if conv else (1 if k == "feed" else max(0, min(4, p.decimals))))
            lo = -1e6 if p.min is None else p.min
            hi = 1e6 if p.max is None else p.max
            if conv:
                lo, hi = lo / u.factor, hi / u.factor
            w.setRange(lo, hi)
            w.setSuffix(suffix)
            w.setSingleStep(p.step or (1.0 if k != "length" else (0.1 if u.name == "inch" else 1.0)))
            w.setValue(u.to_display(v) if conv else float(v))
            w.setKeyboardTracking(False)
            w.valueChanged.connect(self._emit)
            return w
        if k == "int":
            w = QSpinBox()
            w.setRange(int(p.min if p.min is not None else -10 ** 9), int(p.max if p.max is not None else 10 ** 9))
            w.setValue(int(v or 0))
            w.valueChanged.connect(self._emit)
            return w
        if k == "bool":
            w = QCheckBox()
            w.setChecked(bool(v))
            w.toggled.connect(self._emit)
            return w
        if k == "choice":
            w = QComboBox()
            for val, label in p.choices:
                w.addItem(label, val)
            i = w.findData(v)
            w.setCurrentIndex(max(0, i))
            w.currentIndexChanged.connect(self._emit)
            return w
        if k == "text":
            w = QPlainTextEdit(str(v or ""))
            w.setMaximumHeight(90)
            w.textChanged.connect(self._emit)
            return w
        if k == "font":
            w = QFontComboBox()
            if v:
                w.setCurrentFont(QFont(str(v)))
            w.currentFontChanged.connect(self._emit)
            return w
        if k == "tool":
            w = QComboBox()
            w.addItem("(none)", None)
            for t in (self.tools.tools if self.tools else []):
                w.addItem(t.describe(), t.id)
            if isinstance(v, Tool):
                i = w.findData(v.id)
                if i < 0:
                    w.addItem(v.describe() + " (project)", v.id)
                    self._project_tool = v
                    i = w.count() - 1
                w.setCurrentIndex(i)
            w.currentIndexChanged.connect(self._emit)
            return w
        if k == "point":
            box = QWidget()
            lay = QHBoxLayout(box)
            lay.setContentsMargins(0, 0, 0, 0)
            xs, ys = QDoubleSpinBox(), QDoubleSpinBox()
            for s, val in zip((xs, ys), v or (0.0, 0.0)):
                s.setRange(-1e6, 1e6)
                s.setDecimals(u.decimals)
                s.setValue(u.to_display(val))
                s.valueChanged.connect(self._emit)
                lay.addWidget(s)
            box._spins = (xs, ys)
            return box
        w = QLineEdit(str(v or ""))
        w.textChanged.connect(self._emit)
        return w

    # -- values ---------------------------------------------------------------------------------
    def _read(self, p: Param) -> Any:
        w = self.widgets[p.name]
        u = self.units
        conv = p.kind in ("length", "feed")
        if isinstance(w, OptionalNumber):
            v = w.value()
            return None if v is None else (u.from_display(v) if conv else v)
        if isinstance(w, QDoubleSpinBox):
            return u.from_display(w.value()) if conv else w.value()
        if isinstance(w, QSpinBox):
            return w.value()
        if isinstance(w, QCheckBox):
            return w.isChecked()
        if isinstance(w, QFontComboBox):
            return w.currentFont().family()
        if isinstance(w, QComboBox):
            if p.kind == "tool":
                tid = w.currentData()
                if tid is None:
                    return None
                t = self.tools.get(tid) if self.tools else None
                if t is None:
                    t = getattr(self, "_project_tool", None)
                return t.copy() if t is not None else None
            return w.currentData()
        if isinstance(w, QPlainTextEdit):
            return w.toPlainText()
        if hasattr(w, "_spins"):
            return tuple(u.from_display(s.value()) for s in w._spins)
        return w.text()

    def values(self) -> dict:
        out = dict(self._values)
        for p in self.params:
            out[p.name] = self._read(p)
        return out

    def _emit(self, *args) -> None:
        self._update_visibility()
        self.changed.emit()

    def _update_visibility(self) -> None:
        vals = self.values()
        for p in self.params:
            lay, w = self._rows[p.name]
            lay.setRowVisible(w, is_visible(p, vals))
        for name, lay in self._groups.items():
            box = lay.parentWidget()
            box.setVisible(any(is_visible(p, vals) for p in self.params if (p.group or "Settings") == name))

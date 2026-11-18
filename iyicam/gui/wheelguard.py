"""The mouse wheel only changes a number or a choice that has the keyboard focus.

Without this a panel that is scrolled with the wheel changes every number box it passes under the pointer.  These are
the spin boxes, combo boxes and sliders of the program: one that is not focused does not take the wheel (it goes on to
the scroll area it is in), and the wheel does not give it the focus.  (It is done in the widgets themselves and not
with a filter on the whole application: a Python filter sees every event of the program, and while a calculation
runs in a thread that makes the window crawl.)"""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QValidator
from PySide6.QtWidgets import QComboBox as _ComboBox
from PySide6.QtWidgets import QDoubleSpinBox as _DoubleSpinBox
from PySide6.QtWidgets import QFontComboBox as _FontComboBox
from PySide6.QtWidgets import QSlider as _Slider
from PySide6.QtWidgets import QSpinBox as _SpinBox


_SUMCHARS = set("0123456789+-*/^().,eE× ÷:x−·")


def _calc_methods(base):
    """A spin box takes sums: ``12+3.5`` or ``(100-8)/2`` is worked out when the box is left or Enter is pressed (a comma is a
    decimal point like the dot)."""
    from ..core import calc

    def strip(self, text: str) -> str:
        t = text
        for fix in (self.prefix(), self.suffix()):
            if fix and fix.strip() and fix.strip() in t:
                t = t.replace(fix.strip(), "")
        return t.strip()

    def validate(self, text, pos):
        t = strip(self, text)
        if all(c in _SUMCHARS for c in t):
            if t == "" or calc.evaluate(t) is not None:
                return QValidator.State.Acceptable, text, pos
            return QValidator.State.Intermediate, text, pos              # (half written: 12+ ...)
        return QValidator.State.Invalid, text, pos

    def valueFromText(self, text):
        v = calc.evaluate(strip(self, text))
        if v is None:
            return base.valueFromText(self, text)
        return v if base is _DoubleSpinBox else int(round(v))

    return validate, valueFromText


def _make(base):
    class Guarded(base):
        def __init__(self, *args, **kw):
            super().__init__(*args, **kw)
            self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)     # (clicking or tabbing gives the focus, the wheel does not)

        if base in (_DoubleSpinBox, _SpinBox):
            validate, valueFromText = _calc_methods(base)

        def wheelEvent(self, ev):
            if self.hasFocus():
                super().wheelEvent(ev)
            else:
                ev.ignore()                                     # (the panel under it scrolls)

    Guarded.__name__ = Guarded.__qualname__ = base.__name__
    return Guarded


QDoubleSpinBox = _make(_DoubleSpinBox)
QSpinBox = _make(_SpinBox)
QComboBox = _make(_ComboBox)
QFontComboBox = _make(_FontComboBox)
QSlider = _make(_Slider)
GUARDED = (QDoubleSpinBox, QSpinBox, QComboBox, QFontComboBox, QSlider)

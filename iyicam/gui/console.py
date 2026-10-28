"""Interactive Python console for scripting the open project."""
from __future__ import annotations

import code
import contextlib
import io
import traceback

from PySide6.QtCore import Qt
from PySide6.QtGui import QFontDatabase
from PySide6.QtWidgets import QLineEdit, QPlainTextEdit, QVBoxLayout, QWidget

BANNER = """iyiCAM Python console.  Available names:
  ctrl, project, sheet(), selected(), window, REGISTRY
  shapes, vectorops, geom, add(contour_or_list), run(command_id, **values)
Example:  add(shapes.circle((100, 100), 25))"""


class Console(QWidget):
    def __init__(self, ctrl, window, parent=None):
        super().__init__(parent)
        from ..core import geom, shapes, vectorops
        from ..core.document import VectorObject
        from ..core.registry import REGISTRY
        self.ctrl = ctrl
        lay = QVBoxLayout(self)
        lay.setContentsMargins(2, 2, 2, 2)
        mono = QFontDatabase.systemFont(QFontDatabase.FixedFont)
        self.out = QPlainTextEdit()
        self.out.setReadOnly(True)
        self.out.setFont(mono)
        self.inp = QLineEdit()
        self.inp.setFont(mono)
        self.inp.setPlaceholderText(">>> ")
        self.inp.returnPressed.connect(self.execute)
        self.inp.installEventFilter(self)
        lay.addWidget(self.out)
        lay.addWidget(self.inp)
        self.history: list[str] = []
        self.hpos = 0
        self.buffer: list[str] = []

        def add(contours, name: str = "script"):
            cs = contours if isinstance(contours, list) else [contours]
            obj = VectorObject(cs, self.ctrl.layer_id, name)
            self.ctrl.add_vectors([obj], "Script")
            return obj

        def run(cmd_id: str, **values):
            return self.ctrl.run_command(REGISTRY.commands[cmd_id], values)

        # (``ctrl``, ``project`` and the functions follow the document of the tab that is open)
        self.ns = {"ctrl": ctrl, "window": window, "REGISTRY": REGISTRY, "shapes": shapes,
                   "vectorops": vectorops, "geom": geom, "add": add, "run": run,
                   "sheet": lambda: self.ctrl.sheet, "selected": lambda: self.ctrl.selected_objects()}
        self.interp = code.InteractiveInterpreter(self.ns)
        self.out.setPlainText(BANNER + "\n")

    @property
    def project(self):
        return self.ctrl.project

    def bind(self, ctrl) -> None:
        """Work on the document of another tab."""
        self.ctrl = ctrl
        self.ns["ctrl"] = ctrl
        self.ns["project"] = ctrl.project

    def eventFilter(self, obj, ev) -> bool:
        if obj is self.inp and ev.type() == ev.Type.KeyPress and self.history:
            if ev.key() == Qt.Key_Up:
                self.hpos = max(0, self.hpos - 1)
                self.inp.setText(self.history[self.hpos])
                return True
            if ev.key() == Qt.Key_Down:
                self.hpos = min(len(self.history), self.hpos + 1)
                self.inp.setText(self.history[self.hpos] if self.hpos < len(self.history) else "")
                return True
        return super().eventFilter(obj, ev)

    def execute(self) -> None:
        line = self.inp.text()
        self.inp.clear()
        if line.strip():
            self.history.append(line)
        self.hpos = len(self.history)
        self.write(("... " if self.buffer else ">>> ") + line + "\n")
        self.buffer.append(line)
        self.ns["project"] = self.ctrl.project
        src = "\n".join(self.buffer)
        buf = io.StringIO()
        try:
            with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(buf):
                more = self.interp.runsource(src, "<console>", "single")
        except SystemExit:
            more = False
        except Exception:
            buf.write(traceback.format_exc())
            more = False
        if not more:
            self.buffer = []
        self.write(buf.getvalue())
        self.inp.setPlaceholderText("... " if more else ">>> ")

    def write(self, text: str) -> None:
        if text:
            self.out.moveCursor(self.out.textCursor().MoveOperation.End)
            self.out.insertPlainText(text)
            self.out.ensureCursorVisible()

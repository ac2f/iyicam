"""A robot that drives the GUI in tests.

Dialogs close themselves (accepted, after an optional hook has set values), message boxes answer,
file dialogs get the paths queued for them, and every error is collected: exceptions in slots, the
"critical" box the main window shows for them, and warnings. Nothing ever waits for a person.
"""
from __future__ import annotations

import sys
import traceback

from PySide6.QtWidgets import QApplication, QDialog, QFileDialog, QInputDialog, QMenu, QMessageBox

BAD_WORDS = ("Traceback", "has no attribute", "NoneType", "object is not", "Error", "error:", "Exception")


class Robot:
    def __init__(self):
        self.errors: list[str] = []      # things that should never happen
        self.messages: list[tuple[str, str]] = []  # (kind, text) of every message box
        self.dialogs: list[str] = []     # titles of the dialogs that were shown
        self.files: list[str] = []       # paths for the next file dialogs
        self.hook = None                 # called with every dialog before it is accepted
        self.refuse = False              # reject dialogs instead of accepting them
        self.squeeze = False             # answer of the squeeze question
        self._saved = {}

    # -- state ---------------------------------------------------------------------------------
    def reset(self) -> None:
        self.errors.clear()
        self.messages.clear()
        self.dialogs.clear()
        self.files.clear()
        self.hook = None
        self.refuse = False

    def problems(self) -> list[str]:
        """Errors, plus the warnings that look like a program fault."""
        out = list(self.errors)
        for kind, text in self.messages:
            if kind == "critical" or (kind == "warning" and any(w in text for w in BAD_WORDS)):
                out.append(f"{kind}: {text}")
        return out

    # -- patches -------------------------------------------------------------------------------
    def install(self, mp) -> "Robot":
        robot = self

        def exec_(dlg):
            robot.dialogs.append(dlg.windowTitle() or type(dlg).__name__)
            if robot.hook is not None:
                robot.hook(dlg)
            if robot.refuse:
                dlg.reject()
                return QDialog.Rejected
            dlg.accept()
            return QDialog.result(dlg)

        mp.setattr(QDialog, "exec", exec_)
        mp.setattr(QDialog, "exec_", exec_, raising=False)

        def box(kind, answer):
            def f(parent=None, title="", text="", *a, **k):
                robot.messages.append((kind, f"{title}: {text}"))
                return answer(a, k) if callable(answer) else answer
            return staticmethod(f)

        def question(a, k):
            buttons = a[0] if a else k.get("buttons", QMessageBox.Yes | QMessageBox.No)
            if buttons & QMessageBox.Discard:
                return QMessageBox.Discard
            return QMessageBox.Yes if buttons & QMessageBox.Yes else QMessageBox.Ok

        mp.setattr(QMessageBox, "information", box("information", QMessageBox.Ok))
        mp.setattr(QMessageBox, "warning", box("warning", QMessageBox.Ok))
        mp.setattr(QMessageBox, "critical", box("critical", QMessageBox.Ok))
        mp.setattr(QMessageBox, "question", box("question", question))
        mp.setattr(QMessageBox, "about", box("about", QMessageBox.Ok))

        def msg_exec(self):
            robot.messages.append(("box", self.text()))
            for b in self.buttons():
                if robot.squeeze and "Squeeze" in b.text():
                    self._chosen = b
            return QMessageBox.Ok

        mp.setattr(QMessageBox, "exec", msg_exec)
        mp.setattr(QMessageBox, "clickedButton", lambda self: getattr(self, "_chosen", None))

        def file_open(*a, **k):
            return (robot.files.pop(0), "") if robot.files else ("", "")

        mp.setattr(QFileDialog, "getOpenFileName", staticmethod(file_open))
        mp.setattr(QFileDialog, "getSaveFileName", staticmethod(file_open))
        mp.setattr(QFileDialog, "getExistingDirectory", staticmethod(lambda *a, **k: robot.files.pop(0)
                                                                    if robot.files else ""))
        mp.setattr(QFileDialog, "getOpenFileNames", staticmethod(lambda *a, **k: (
            ([robot.files.pop(0)] if robot.files else []), "")))
        mp.setattr(QInputDialog, "getText", staticmethod(lambda p=None, t="", l="", *a, text="", **k: (text or "x", True)))
        mp.setattr(QInputDialog, "getInt", staticmethod(lambda p=None, t="", l="", value=0, *a, **k: (value, True)))
        mp.setattr(QInputDialog, "getDouble", staticmethod(lambda p=None, t="", l="", value=0.0, *a, **k: (value, True)))
        mp.setattr(QInputDialog, "getItem", staticmethod(lambda p, t, l, items, current=0, *a, **k: (items[current], True)))
        mp.setattr(QMenu, "exec", lambda self, *a, **k: None)

        def hook(etype, value, tb):
            robot.errors.append("".join(traceback.format_exception(etype, value, tb)))

        self._saved["excepthook"] = sys.excepthook
        sys.excepthook = hook
        mp.setattr(sys, "excepthook", hook)
        return self


def process() -> None:
    QApplication.processEvents()

"""A robot that drives the GUI in tests.

Dialogs close themselves (accepted, after an optional hook has set values), message boxes answer,
file dialogs get the paths queued for them, and every error is collected: exceptions in slots, the
"critical" box the main window shows for them, and warnings. Nothing ever waits for a person.
"""
from __future__ import annotations

import sys
import traceback

from PySide6.QtGui import QColor
from PySide6.QtWidgets import (QApplication, QColorDialog, QDialog, QFileDialog, QInputDialog,
                               QMessageBox)

BAD_WORDS = ("Traceback", "has no attribute", "NoneType", "object is not", "Error", "error:", "Exception")


class Robot:
    def __init__(self):
        self.errors: list[str] = []      # things that should never happen
        self.messages: list[tuple[str, str]] = []  # (kind, text) of every message box
        self.dialogs: list[str] = []     # titles of the dialogs that were shown
        self.files: list[str] = []       # paths for the next file dialogs
        self.file_calls: list[tuple[str, str]] = []  # (title, where the dialog started) of every file dialog
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
        self.file_calls.clear()
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
            robot.file_calls.append((a[1] if len(a) > 1 else "", a[2] if len(a) > 2 else ""))
            return (robot.files.pop(0), "") if robot.files else ("", "")

        mp.setattr(QFileDialog, "getOpenFileName", staticmethod(file_open))
        mp.setattr(QFileDialog, "getSaveFileName", staticmethod(file_open))
        def file_dir(*a, **k):
            robot.file_calls.append((a[1] if len(a) > 1 else "", a[2] if len(a) > 2 else ""))
            return robot.files.pop(0) if robot.files else ""

        def file_many(*a, **k):
            robot.file_calls.append((a[1] if len(a) > 1 else "", a[2] if len(a) > 2 else ""))
            return ([robot.files.pop(0)] if robot.files else []), ""

        mp.setattr(QFileDialog, "getExistingDirectory", staticmethod(file_dir))
        mp.setattr(QFileDialog, "getOpenFileNames", staticmethod(file_many))
        mp.setattr(QInputDialog, "getText", staticmethod(lambda p=None, t="", l="", *a, text="", **k: (text or "x", True)))
        mp.setattr(QInputDialog, "getInt", staticmethod(lambda p=None, t="", l="", value=0, *a, **k: (value, True)))
        mp.setattr(QInputDialog, "getDouble", staticmethod(lambda p=None, t="", l="", value=0.0, *a, **k: (value, True)))
        mp.setattr(QInputDialog, "getItem", staticmethod(lambda p, t, l, items, current=0, *a, **k: (items[current], True)))
        from iyicam.gui import menus
        mp.setattr(menus, "show_menu", lambda menu, pos: None)   # (PySide's QMenu.exec cannot be patched)
        mp.setattr(QColorDialog, "getColor", staticmethod(lambda *a, **k: QColor("#2a7fff")))

        def hook(etype, value, tb):
            robot.errors.append("".join(traceback.format_exception(etype, value, tb)))

        mp.setattr(sys, "excepthook", hook)     # (monkeypatch puts the old hook back)
        return self


def process() -> None:
    QApplication.processEvents()


def rwin_gen(tmp_path, monkeypatch):
    """A main window on a fresh settings folder with the robot installed: ``yield from`` it in a fixture."""
    monkeypatch.setenv("IYICAM_HOME", str(tmp_path / "home"))
    robot = Robot().install(monkeypatch)
    from iyicam.gui.app import create_window
    app, w = create_window(["test"], load_plugins=False)
    w.resize(1200, 800)
    w.show()
    process()
    yield w, robot
    w.ctrl.dirty = False
    w.close()
    process()


def populate(win, select=True):
    """Parts of every kind on two layers: closed, open, with a hole, text."""
    from iyicam.core import shapes
    from iyicam.core.document import VectorObject
    from iyicam.core.geom import Transform
    from iyicam.gui.qtgeom import text_contours
    c = win.ctrl
    lay2 = c.project.layer_by_name("Parts") or c.project.add_layer("Parts")
    base = c.layer_id
    objs = [
        VectorObject([shapes.rectangle(20, 20, 120, 80, corner_radius=8)], base, "Panel"),
        VectorObject([shapes.circle((200, 60), 30)], base, "Disc"),
        VectorObject([shapes.rectangle(260, 20, 110, 90), shapes.circle((315, 65), 18)], lay2.id, "Ring part"),
        VectorObject([shapes.polyline([(20, 150), (120, 160), (180, 150), (240, 190)])], base, "Open line"),
        VectorObject([shapes.polyline([(300, 150), (380, 150), (380, 220), (340, 250), (300, 220)], True)],
                     lay2.id, "Pentagon"),
        VectorObject(text_contours("Ab", "DejaVu Sans", 30.0), base, "Text"),
    ]
    objs[-1].transform(Transform.translate(40, 260))
    c.add_vectors(objs, select=False)
    if select:
        c.select([o.id for o in objs])
    return objs

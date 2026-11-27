"""Dialogs: commands, sheets, material, tools, toolpaths, nesting, export, plugins, posts, text."""
from __future__ import annotations

import html
import copy
import json
import math
import shutil
import threading
import tomllib
from pathlib import Path

from PySide6.QtCore import Qt, QTimer, QUrl
from PySide6.QtGui import QDesktopServices, QFont, QFontDatabase, QGuiApplication
from PySide6.QtWidgets import (QAbstractItemView, QApplication, QButtonGroup, QCheckBox, QDialog, QDialogButtonBox,
                                QFormLayout, QGroupBox, QHBoxLayout, QHeaderView, QInputDialog, QLabel, QLineEdit,
                                QListWidget, QListWidgetItem, QMenu, QMessageBox, QPlainTextEdit, QProgressDialog,
                                QPushButton, QRadioButton, QScrollArea, QSplitter, QTableWidget, QTableWidgetItem,
                                QTabWidget, QToolButton, QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget)
from .wheelguard import QComboBox, QDoubleSpinBox, QFontComboBox, QSpinBox

from ..core import clip, paths
from ..core.document import XY_ORIGINS, Selection, ToolpathDef
from ..core.params import Param
from ..core.stock import StockItem, load_stock, save_stock
from ..core.tooling import TOOL_TYPES, Tool
from ..nesting.nest import DEFAULT_DIRECTION, DIRECTIONS, NestCancelled, NestSettings, direction_of
from ..nesting.parts import (ROTATION_MODES, RULE_USES_VALUE, apply_nesting, parse_angles, prepare_nesting,
                             run_nesting, squeeze_lines)
from ..nesting.stock import leftover_of_sheet, stock_from_vectors, use_up
from ..post import editing
from .files import open_file, save_file
from .material_info import material_summary
from .forms import ParamForm
from .posts import BlocksEditor, ExplainedGcode, OptionsForm


class RemembersSize:
    """A dialog that opens at the size it had when it was closed the last time."""

    def keep_size(self, ctrl, key: str, width: int, height: int) -> None:
        self._size_ctrl, self._size_key = ctrl, "size:" + key
        saved = ctrl.settings.recall(self._size_key)
        try:
            width, height = int(saved["width"]), int(saved["height"])
        except (KeyError, TypeError, ValueError, OverflowError):
            pass
        screen = QGuiApplication.primaryScreen()
        if screen is not None:                      # (a dialog from a bigger screen must still fit this one)
            area = screen.availableGeometry()
            width, height = min(width, area.width()), min(height, area.height())
        self.resize(max(width, 200), max(height, 150))

    def done(self, result: int) -> None:
        key = getattr(self, "_size_key", None)
        if key:
            self._size_ctrl.settings.remember(key, {"width": self.width(), "height": self.height()})
        super().done(result)


def buttons(dlg: QDialog, ok_text: str = "OK") -> QDialogButtonBox:
    bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
    bb.button(QDialogButtonBox.Ok).setText(ok_text)
    bb.accepted.connect(dlg.accept)
    bb.rejected.connect(dlg.reject)
    return bb


class _Worker(threading.Thread):
    def __init__(self, fn):
        super().__init__(daemon=True)
        self.fn, self.result, self.error = fn, None, None

    def run(self) -> None:
        try:
            self.result = self.fn()
        except BaseException as e:  # noqa: BLE001  (handed to the caller)
            self.error = e


def run_in_thread(fn, prog: QProgressDialog, state: dict):
    """Run ``fn()`` in a thread while the interface stays alive.

    ``state`` has ``k`` / ``n`` (progress, written by the thread) and ``cancel`` (a threading.Event set
    here when the progress dialog is cancelled, for the thread to look at)."""
    worker = _Worker(fn)
    worker.start()
    while worker.is_alive():
        QApplication.processEvents()
        if prog.wasCanceled():
            state["cancel"].set()
        else:
            prog.setMaximum(max(1, state["n"]))
            prog.setValue(min(state["k"], max(1, state["n"])))
        worker.join(0.02)
    QApplication.processEvents()
    if worker.error is not None:
        raise worker.error
    return worker.result


def nest_signature(objs, sheets) -> int:
    """What a nesting depends on, as one number: the vectors (their outlines and layers) and the sheets (their size and
    outline). Compared when a nesting in the background is over: was anything changed meanwhile?"""
    def contour(c):
        return (c.closed, tuple((type(s).__name__, s.a, s.b, getattr(s, "bulge", None), getattr(s, "c1", None),
                                 getattr(s, "c2", None)) for s in c.segs))
    vec = tuple((o.id, o.layer_id, tuple(contour(c) for c in o.contours)) for o in objs)
    sh = tuple((s.id, s.width, s.height, len(s.vectors), repr(s.outline)) for s in sheets)
    return hash((vec, sh))


class NestJob:
    """A nesting in the background (``NestingDialog.start``): a thread does the work (with the helper processes on every
    processor), a timer here looks after it; when it is over the layout is put into its project, in the interface."""

    def __init__(self, dialog, prep, done, show_progress=None):
        self.dialog, self.prep, self.done, self.show_progress = dialog, prep, done, show_progress
        self.state = prep["state"]
        self.ctrl = dialog.ctrl
        self.result = None
        self.worker = _Worker(prep["compute"])
        self.worker.start()
        self.timer = QTimer()
        self.timer.timeout.connect(self._tick)
        self.timer.start(100)

    @property
    def running(self) -> bool:
        return self.timer.isActive()

    def cancel(self) -> None:
        self.state["cancel"].set()

    def progress(self) -> float:
        return min(1.0, self.state["k"] / max(1, self.state["n"]))

    def wait(self, seconds: float | None = None) -> None:
        """(tests) The job to its end, the interface going on meanwhile."""
        import time
        end = None if seconds is None else time.monotonic() + seconds
        while self.running and (end is None or time.monotonic() < end):
            QApplication.processEvents()
            self.worker.join(0.02)
        QApplication.processEvents()

    def _tick(self) -> None:
        if self.show_progress is not None:
            self.show_progress(self.state["k"], self.state["n"])
        if self.worker.is_alive():
            return
        self.timer.stop()
        out = None
        try:
            err = self.worker.error
            if isinstance(err, NestCancelled) or self.state["cancel"].is_set():
                out = None
            elif isinstance(err, clip.TooComplex):
                QMessageBox.warning(self.dialog.parent(), "Nesting", f"This cannot be nested:\n{err}")
            elif err is not None:
                QMessageBox.critical(self.dialog.parent(), "Nesting", f"{type(err).__name__}: {err}")
            elif not self.dialog.unchanged(self.prep):
                QMessageBox.warning(self.dialog.parent(), "Nesting",
                                    "The vectors (or the sheets) of this nesting were changed while it ran: the layout "
                                    "is not put in, nothing is changed. Nest again.")
            else:
                # (the view stays where you are working now: it goes to the nested sheet only when you are still there)
                win = self.dialog.parent()
                here = getattr(win, "ctrl", None) is self.ctrl and self.ctrl.sheet is self.prep["sheet"]
                self.prep["show"] = here
                out = self.dialog.finish(self.prep, self.worker.result)
        finally:
            self.result = out
            self.done(out)


def scroll(widget: QWidget) -> QScrollArea:
    sa = QScrollArea()
    sa.setWidgetResizable(True)
    sa.setWidget(widget)
    sa.setFrameShape(QScrollArea.NoFrame)
    return sa


# ---------------------------------------------------------------------------
# generic command dialog
# ---------------------------------------------------------------------------

class CommandDialog(RemembersSize, QDialog):
    def __init__(self, ctrl, cmd, parent=None):
        super().__init__(parent)
        self.ctrl = ctrl
        self.cmd = cmd
        self.setWindowTitle(cmd.label.rstrip("."))
        lay = QVBoxLayout(self)
        if cmd.tooltip:
            lab = QLabel(cmd.tooltip)
            lab.setWordWrap(True)
            lay.addWidget(lab)
        self.form = ParamForm(cmd.params, ctrl.settings.recall(f"command:{cmd.id}", cmd.params), ctrl.units,
                              ctrl.tools)
        lay.addWidget(scroll(self.form))
        lay.addWidget(buttons(self))
        self.keep_size(ctrl, f"command:{cmd.id}", 380, min(640, 120 + 34 * len(cmd.params)))

    def values(self) -> dict:
        v = self.form.values()
        self.ctrl.settings.remember(f"command:{self.cmd.id}", v, self.cmd.params)
        return v


class CommandReportDialog(RemembersSize, QDialog):
    """What a command handed back to be read (a measurement, a count, a list): its report as text that can be selected and
    copied, with the messages and warnings it gave over the top."""

    def __init__(self, ctrl, cmd, result: dict, parent=None):
        super().__init__(parent)
        from ..core.commands import report_text
        self.result = result
        self.setWindowTitle(str(result.get("title") or cmd.label).rstrip("."))
        lay = QVBoxLayout(self)
        say = [m for m in result.get("messages") or []]
        if say:
            lab = QLabel("\n".join(say))
            lab.setWordWrap(True)
            lab.setTextInteractionFlags(Qt.TextSelectableByMouse)
            lay.addWidget(lab)
        self.text = QPlainTextEdit()
        self.text.setReadOnly(True)
        self.text.setFont(QFontDatabase.systemFont(QFontDatabase.FixedFont))
        self.text.setLineWrapMode(QPlainTextEdit.NoWrap)
        self.text.setPlainText(report_text({**result, "title": ""}))
        lay.addWidget(self.text, 1)
        row = QHBoxLayout()
        copy = QPushButton("Copy")
        copy.clicked.connect(lambda: QGuiApplication.clipboard().setText(self.text.toPlainText()))
        close = QPushButton("Close")
        close.setDefault(True)
        close.clicked.connect(self.accept)
        row.addWidget(copy)
        row.addStretch(1)
        row.addWidget(close)
        lay.addLayout(row)
        self.keep_size(ctrl, f"report:{cmd.id}", 460, 420)


# ---------------------------------------------------------------------------
# sheets / material
# ---------------------------------------------------------------------------

SHEET_PRESETS = [("2800 x 2100", 2800, 2100), ("3660 x 1830", 3660, 1830), ("2440 x 1220", 2440, 1220),
                 ("2500 x 1250", 2500, 1250), ("3050 x 1220", 3050, 1220), ("1220 x 610", 1220, 610)]   # (the built-in ones: see core/presets.py)


class PresetsDialog(QDialog):
    """The sheet sizes that are offered ready: add, change, remove, back to the built-in ones. They are kept for next time."""

    def __init__(self, ctrl, parent=None):
        super().__init__(parent)
        from ..core import presets
        self.ctrl = ctrl
        self.setWindowTitle("Sheet sizes")
        lay = QVBoxLayout(self)
        lay.addWidget(QLabel("Name, width and height (millimetres). Plugins may add sizes of their own: they are not in this list."))
        self.table = QTableWidget(0, 3)
        self.table.setHorizontalHeaderLabels(["Name", "Width", "Height"])
        self.table.horizontalHeader().setStretchLastSection(True)
        lay.addWidget(self.table)
        for name, w, h in (presets.clean(ctrl.settings.sheet_presets) or presets.BUILTIN):
            self._row(name, w, h)
        row = QHBoxLayout()
        add = QPushButton("Add")
        add.clicked.connect(lambda: self._row("New size", 1000.0, 1000.0))
        rem = QPushButton("Remove")
        rem.clicked.connect(lambda: self.table.removeRow(self.table.currentRow()) if self.table.currentRow() >= 0 else None)
        back = QPushButton("Built-in sizes")
        back.clicked.connect(self._builtin)
        for b in (add, rem, back):
            row.addWidget(b)
        row.addStretch(1)
        lay.addLayout(row)
        lay.addWidget(buttons(self))

    def _row(self, name: str, w: float, h: float) -> None:
        r = self.table.rowCount()
        self.table.insertRow(r)
        self.table.setItem(r, 0, QTableWidgetItem(name))
        self.table.setItem(r, 1, QTableWidgetItem(f"{w:g}"))
        self.table.setItem(r, 2, QTableWidgetItem(f"{h:g}"))

    def _builtin(self) -> None:
        from ..core import presets
        self.table.setRowCount(0)
        for name, w, h in presets.BUILTIN:
            self._row(name, w, h)

    def sizes(self) -> list[list]:
        from ..core import calc, presets
        raw = []
        for r in range(self.table.rowCount()):
            cells = [(self.table.item(r, c).text() if self.table.item(r, c) else "") for c in range(3)]
            raw.append([cells[0], calc.evaluate(cells[1]), calc.evaluate(cells[2])])    # (a sum is taken)
        return [list(x) for x in presets.clean(raw)]

    def accept(self) -> None:
        self.ctrl.settings.sheet_presets = self.sizes()
        self.ctrl.settings.save()
        super().accept()


class LayerScopeDialog(QDialog):
    """Where a layer is: on every sheet, on the sheets ticked, and / or on the sheets of a group. Sheets that are not in the list
    do not show the layer (nor its vectors)."""

    def __init__(self, ctrl, layer, parent=None):
        super().__init__(parent)
        self.setWindowTitle(f"Layer: {layer.name}")
        lay = QVBoxLayout(self)
        self.every = QRadioButton("On every sheet")
        self.some = QRadioButton("Only on the sheets ticked, and the sheets of the group:")
        lay.addWidget(self.every)
        lay.addWidget(self.some)
        self.list = QListWidget()
        want = set(layer.sheet_ids or [])
        for s in ctrl.project.sheets:
            it = QListWidgetItem(s.name + (f"   ▣ {s.group}" if s.group else ""))
            it.setData(Qt.UserRole, s.id)
            it.setFlags(it.flags() | Qt.ItemIsUserCheckable)
            it.setCheckState(Qt.Checked if s.id in want else Qt.Unchecked)
            self.list.addItem(it)
        lay.addWidget(self.list)
        self.group = QComboBox()
        self.group.addItem("(no group)", "")
        for g in ctrl.sheet_groups():
            self.group.addItem(g, g)
        if layer.sheet_group and self.group.findData(layer.sheet_group) < 0:
            self.group.addItem(layer.sheet_group, layer.sheet_group)
        self.group.setCurrentIndex(max(0, self.group.findData(layer.sheet_group)))
        lay.addWidget(self.group)
        (self.every if layer.sheet_ids is None and not layer.sheet_group else self.some).setChecked(True)
        for w in (self.list, self.group):
            self.some.toggled.connect(w.setEnabled)
        self.list.setEnabled(self.some.isChecked())
        self.group.setEnabled(self.some.isChecked())
        lay.addWidget(buttons(self))

    def chosen(self) -> tuple[list[str] | None, str]:
        if self.every.isChecked():
            return None, ""
        ids = [self.list.item(i).data(Qt.UserRole) for i in range(self.list.count())
               if self.list.item(i).checkState() == Qt.Checked]
        return ids, self.group.currentData() or ""


class PresetSaveDialog(QDialog):
    """What a preset keeps: every field of the toolpath with a tick (kept or not) and, for a number, a sum instead of the value
    (``material_thickness + 0.2``): it is worked out when the preset is put on a toolpath, so it follows the job."""

    def __init__(self, fields: list[dict], name: str = "", parent=None):
        super().__init__(parent)
        from ..cam import tp_presets
        self.setWindowTitle("Save as preset")
        self.fields = fields
        lay = QVBoxLayout(self)
        form = QFormLayout()
        self.name = QLineEdit(name)
        form.addRow("Name of the preset", self.name)
        lay.addLayout(form)
        lay.addWidget(QLabel("Tick what the preset keeps. A number can be a sum of the job instead: "
                             + ", ".join(v[0] for v in tp_presets.VARIABLES) + "  (e.g. material_thickness + 0.2). "
                             "The name can have them in braces: Cut {material_thickness} mm."))
        self.table = QTableWidget(len(fields), 3)
        self.table.setHorizontalHeaderLabels(["Keep", "Value", "Or a sum"])
        self.table.setVerticalHeaderLabels([f["label"] for f in fields])
        self.table.horizontalHeader().setStretchLastSection(True)
        default_on = {"name", "tool"}
        for r, f in enumerate(fields):
            it = QTableWidgetItem()
            it.setFlags(it.flags() | Qt.ItemIsUserCheckable)
            it.setCheckState(Qt.Checked if f["key"] in default_on or f["key"].startswith("params.") else Qt.Unchecked)
            self.table.setItem(r, 0, it)
            shown = f["value"].get("name", "tool") if isinstance(f["value"], dict) else ("" if f["value"] is None else str(f["value"]))
            v = QTableWidgetItem(shown)
            v.setFlags(v.flags() & ~Qt.ItemIsEditable)
            self.table.setItem(r, 1, v)
            e = QTableWidgetItem("")
            if not f["number"]:
                e.setFlags(e.flags() & ~Qt.ItemIsEditable)
            self.table.setItem(r, 2, e)
        lay.addWidget(self.table, 1)
        lay.addWidget(buttons(self))
        self.resize(560, 560)

    def chosen(self) -> dict:
        out = {}
        for r, f in enumerate(self.fields):
            if self.table.item(r, 0).checkState() != Qt.Checked:
                continue
            expr = self.table.item(r, 2).text().strip()
            out[f["key"]] = {"expr": expr} if expr and f["number"] else {"value": f["value"]}
        return out


class SheetDialog(QDialog):
    def __init__(self, ctrl, sheet, parent=None, first: bool = False):
        super().__init__(parent)
        self.ctrl, self.sheet, self.first = ctrl, sheet, first
        self.setWindowTitle("Sheet")
        u = ctrl.units
        m = ctrl.project.material
        if first:                       # (the first sheet of a blank project: its thickness is the project's, and where Z is 0)
            params = [Param("name", "Name", "str", sheet.name),
                      Param("width", "Width (X)", "length", sheet.width, min=1.0),
                      Param("height", "Height (Y)", "length", sheet.height, min=1.0),
                      Param("thickness", "Thickness", "length", ctrl.project.sheet_thickness(sheet), min=0.01),
                      Param("z_zero", "Z zero", "choice", m.z_zero,
                            choices=[("top", "Top of the material"), ("bottom", "The table (the board under the material)")],
                            help="The table: Z of the G-code is the height over the board the material lies on; the thickness "
                                 "is then only needed for the passes (Save G-code says which thickness it used)")]
            lay = QVBoxLayout(self)
            presets = QComboBox()
            presets.addItem("Preset sizes...")
            for name, w, h in ctrl.sheet_sizes():
                presets.addItem(name, (w, h))
            lay.addWidget(presets)
            self.form = ParamForm(params, None, u)
            lay.addWidget(self.form)
            presets.activated.connect(lambda i: self._preset(presets.itemData(i)))
            self.plain = QCheckBox()
            self.plain.setVisible(False)
            lay.addWidget(buttons(self))
            return
        params = [Param("name", "Name", "str", sheet.name),
                  Param("group", "Group", "str", sheet.group, help="Sheets of one group are nested onto together and can "
                                                                    "have toolpaths of their own"),
                  Param("width", "Width (X)", "length", sheet.width, min=1.0),
                  Param("height", "Height (Y)", "length", sheet.height, min=1.0),
                  Param("thickness", "Thickness", "length", ctrl.project.sheet_thickness(sheet), min=0.01,
                        help="Every sheet has its own thickness: its toolpaths cut through this")]
        lay = QVBoxLayout(self)
        presets = QComboBox()
        presets.addItem("Preset sizes...")
        for name, w, h in ctrl.sheet_sizes():
            presets.addItem(name, (w, h))
        lay.addWidget(presets)
        self.form = ParamForm(params, None, u)
        lay.addWidget(self.form)
        presets.activated.connect(lambda i: self._preset(presets.itemData(i)))
        self.plain = QCheckBox("Plain rectangle (remove the outline of this leftover)")
        self.plain.setVisible(bool(sheet.outline))
        lay.addWidget(self.plain)
        lay.addWidget(buttons(self))

    def _preset(self, wh) -> None:
        if not wh:
            return
        u = self.ctrl.units
        self.form.widgets["width"].setValue(u.to_display(wh[0]))
        self.form.widgets["height"].setValue(u.to_display(wh[1]))

    def accept(self) -> None:
        v = self.form.values()
        if self.first:
            with self.ctrl.edit("Sheet properties"):
                s = self.sheet
                s.name, s.width, s.height = v["name"], v["width"], v["height"]
                s.thickness = v["thickness"]
                self.ctrl.project.material.thickness = v["thickness"]    # (what a sheet of a project kept without one is)
                self.ctrl.project.material.z_zero = v["z_zero"]
            self.ctrl.sheet_changed.emit()
            super().accept()
            return
        with self.ctrl.edit("Sheet properties"):
            s = self.sheet
            s.name, s.width, s.height = v["name"], v["width"], v["height"]
            if (v["group"] or "").strip() != s.group:
                s.group = (v["group"] or "").strip()
                self.ctrl.stale = bool(self.ctrl.project.toolpaths)
            s.thickness = v["thickness"]
            if self.plain.isChecked():
                s.outline = None
        if self.ctrl.project.toolpaths:
            self.ctrl.refresh_stale()
        self.ctrl.sheet_changed.emit()
        super().accept()


class MaterialDialog(RemembersSize, QDialog):
    def __init__(self, ctrl, parent=None):
        super().__init__(parent)
        self.ctrl = ctrl
        m = ctrl.project.material
        s = ctrl.sheet
        self.setWindowTitle("Material Setup")
        origin_labels = {o: o.replace("_", " ").title() for o in XY_ORIGINS}
        params = [
            Param("width", "Sheet width (X)", "length", s.width, min=1.0, group="Sheet"),
            Param("height", "Sheet height (Y)", "length", s.height, min=1.0, group="Sheet",
                  visible={"rotary": False}),
            Param("thickness", "Thickness of this sheet", "length", ctrl.project.sheet_thickness(s), min=0.01, group="Sheet",
                  visible={"rotary": False}, help="Every sheet has its own thickness (Sheets panel: Properties for the others)"),
            Param("z_zero", "Z zero", "choice", m.z_zero,
                  choices=[("top", "Top of the material"), ("bottom", "Bottom (machine bed / rotary axis)")],
                  group="Material"),
            Param("xy_origin", "XY origin", "choice", m.xy_origin, choices=list(origin_labels.items()),
                  group="Material"),
            Param("safe_z", "Safe Z (above surface)", "length", m.safe_z, min=0.1, group="Heights",
                  help="The height above the material surface the tool retracts to and travels at between cuts. "
                       "A toolpath can have a safe Z of its own (on its Settings tab)."),
            Param("clearance", "Rapid-down clearance", "length", m.clearance, min=0.0, group="Heights",
                  help="The tool rapids down to this height above the material and then plunges"),
            Param("home_x", "Home X", "length", m.home[0], group="Home position"),
            Param("home_y", "Home Y", "length", m.home[1], group="Home position"),
            Param("home_z", "Home Z (above surface)", "length", m.home[2], group="Home position"),
            Param("rapid_rate", "Machine rapid rate", "feed", m.rapid_rate, min=1.0, group="Machine",
                  help="Only used for machining time estimates"),
            Param("corner_mode", "Corners", "choice", m.corner_mode,
                  choices=[("", "As the machine is set (the estimate slows down for corners)"),
                           ("blend", "Straight through without slowing (G64, like ArtCAM)"),
                           ("exact", "A stop at every corner (G61)")], group="Machine",
                  help="Written into the G-code after the start of the file (G64 / G61), and the time estimate follows it"),
            Param("z_rapid_rate", "Rapid rate of Z alone", "feed", m.z_rapid_rate, min=0.0, group="Machine",
                  help="Time estimates: Z is often slower than X and Y (0: the same)"),
            Param("accel", "Acceleration (mm/s²)", "float", m.accel, min=1.0, max=100000.0, decimals=0, group="Machine",
                  help="Time estimates: how fast the machine speeds up and slows down. Calibrate from a real time finds it."),
            Param("corner_dev", "Corner deviation", "length", m.corner_dev, min=0.0001, group="Machine",
                  help="Time estimates: how far the machine may cut off a corner (sets the speed it keeps through corners)"),
            Param("time_scale", "Time estimate correction", "float", m.time_scale, min=0.05, max=20.0, decimals=3, group="Machine",
                  help="What the machine really took over what the estimate says (1: none). Calibrate from a real time sets it when "
                       "the acceleration alone cannot."),
            Param("rotary", "Wrap around a rotary axis", "bool", m.rotary, group="Rotary",
                  help="The sheet is the unrolled cylinder: X along the axis, Y around it (written as A)"),
            Param("rotary_diameter", "Cylinder diameter", "length", m.rotary_diameter, min=1.0, group="Rotary",
                  visible={"rotary": True}, help="Sets the sheet height to the circumference"),
        ]
        lay = QVBoxLayout(self)
        self.form = ParamForm(params, None, ctrl.units)
        lay.addWidget(scroll(self.form))
        cal = QPushButton("Calibrate the time estimate from a real time...")
        cal.setToolTip("Type how long the calculated toolpaths of this sheet really took: the acceleration of the machine is "
                       "found from it, and the estimates after that come out near what the machine does")
        cal.clicked.connect(self.calibrate)
        lay.addWidget(cal)
        lay.addWidget(buttons(self))
        self.keep_size(ctrl, "material", 420, 600)

    def calibrate(self) -> None:
        """Find the acceleration at which the toolpaths of this sheet take the time that was measured."""
        from ..cam import timing
        c = self.ctrl
        tps = [tp for tp in c.sheet_toolpaths() if tp.moves]
        if not tps:
            QMessageBox.information(self, "Calibrate", "Calculate the toolpaths of this sheet first: the calibration is made on them.")
            return
        text, ok = QInputDialog.getText(self, "Calibrate", "How long did the toolpaths of this sheet really take?\n"
                                        "(h:mm:ss or m:ss; a plain number is minutes)")
        if not ok or not text.strip():
            return
        seconds = timing.parse_duration(text)
        if seconds is None or seconds <= 0:
            QMessageBox.warning(self, "Calibrate", f"'{text}' is not a time.")
            return
        v = self.form.values()
        base = timing.Machine(v["rapid_rate"], v["z_rapid_rate"], v["accel"], v["corner_dev"], 1.0,
                              {"blend": "blend", "exact": "stop"}.get(v["corner_mode"] or "", "slow"))
        mc = timing.calibrate_all([tp.moves for tp in tps], seconds, base)
        self.form.widgets["accel"].setValue(mc.accel)
        self.form.widgets["time_scale"].setValue(mc.scale)
        QMessageBox.information(self, "Calibrate", f"Acceleration {mc.accel:.0f} mm/s²" + (
            f", correction x{mc.scale:.3f}" if abs(mc.scale - 1) > 1e-3 else "") + ".\nPress OK to keep it.")

    def accept(self) -> None:
        v = self.form.values()
        if v["clearance"] > v["safe_z"] + 1e-9:
            QMessageBox.warning(self, "Material Setup", "The rapid-down clearance cannot be higher than the safe Z: "
                                "the tool would rapid down from a height it never travels at.")
            return
        with self.ctrl.edit("Material setup"):
            m = self.ctrl.project.material
            m.z_zero, m.xy_origin = v["z_zero"], v["xy_origin"]
            self.ctrl.sheet.thickness = v["thickness"]          # (this sheet's: the others keep theirs)
            m.safe_z, m.clearance, m.rapid_rate = v["safe_z"], v["clearance"], v["rapid_rate"]
            m.z_rapid_rate, m.accel, m.corner_dev, m.time_scale = v["z_rapid_rate"], v["accel"], v["corner_dev"], v["time_scale"]
            m.corner_mode = v["corner_mode"] or ""
            m.home = (v["home_x"], v["home_y"], v["home_z"])
            self.ctrl.sheet.width, self.ctrl.sheet.height = v["width"], v["height"]
            m.rotary, m.rotary_diameter = v["rotary"], v["rotary_diameter"]
            if m.rotary:  # depths are measured from the surface, the axis is one radius below
                m.thickness = m.rotary_diameter / 2
                for s in self.ctrl.project.sheets:
                    s.height, s.thickness = math.pi * m.rotary_diameter, m.thickness
        self.ctrl.settings.remember("material", {k: v[k] for k in self.ctrl.MATERIAL_MEMORY})  # for new projects
        self.ctrl.sheet_changed.emit()
        super().accept()


# ---------------------------------------------------------------------------
# tools
# ---------------------------------------------------------------------------

def tool_params(t: Tool, groups: list[str] | None = None) -> list[Param]:
    tapered = ["v_bit", "engraving"]
    if groups is not None:                       # (the groups of the tool database: one of them is chosen)
        names = list(dict.fromkeys([g for g in groups] + [t.group]))
        group = Param("group", "Group", "choice", t.group, choices=[(g, g or "(no group)") for g in names], group="Tool",
                      help="New groups: 'New group...' under the list of tools")
    else:
        group = Param("group", "Group", "str", t.group, group="Tool")
    return [
        Param("name", "Name", "str", t.name, group="Tool",
              help="It may have {variables} of the tool: {type} Ø{diameter} -> End Mill Ø6 (the {…} button puts them in)"),
        Param("type", "Type", "choice", t.type, choices=list(TOOL_TYPES.items()), group="Tool"),
        Param("number", "Tool number", "int", t.number, min=0, max=999, group="Tool"),
        group,
        Param("diameter", "Diameter", "length", t.diameter, min=0.01, group="Geometry"),
        Param("angle", "Included angle", "angle", t.angle, min=1.0, max=179.0, group="Geometry",
              visible={"type": tapered + ["drill"]}),
        Param("tip_diameter", "Tip (flat) diameter", "length", t.tip_diameter, min=0.0, group="Geometry",
              visible={"type": tapered}),
        Param("corner_radius", "Corner radius", "length", t.corner_radius, min=0.0, group="Geometry",
              visible={"type": "bull_nose"}),
        Param("flute_length", "Flute length", "length", t.flute_length, min=0.0, group="Geometry"),
        Param("flutes", "Flutes", "int", t.flutes, min=1, max=12, group="Geometry"),
        Param("stepdown", "Stepdown", "length", t.stepdown, min=0.01, group="Cutting"),
        Param("stepover", "Stepover", "length", t.stepover, min=0.001, group="Cutting"),
        Param("feed_rate", "Feed rate", "feed", t.feed_rate, min=1.0, group="Cutting"),
        Param("plunge_rate", "Plunge rate", "feed", t.plunge_rate, min=1.0, group="Cutting"),
        Param("spindle_speed", "Spindle speed (rpm)", "int", t.spindle_speed, min=0, max=100000, group="Cutting"),
        Param("spindle_dir", "Spindle direction", "choice", t.spindle_dir,
              choices=[("cw", "Clockwise (M3)"), ("ccw", "Counter-clockwise (M4)")], group="Cutting"),
        Param("coolant", "Coolant", "choice", t.coolant,
              choices=[("off", "Off"), ("mist", "Mist (M7)"), ("flood", "Flood (M8)")], group="Cutting"),
        Param("notes", "Notes", "text", t.notes, group="Notes"),
    ]


class ToolEditor(QWidget):
    #: the size of the cutter: once a tool is chosen for a toolpath these cannot be changed there (the toolpath is
    #: calculated for that size; another size is another tool: the tool database)
    SIZE_FIELDS = ("diameter", "tip_diameter", "angle", "corner_radius")

    def __init__(self, ctrl, tool: Tool | None = None, parent=None, lock_size: bool = False,
                 groups: list[str] | None = None):
        super().__init__(parent)
        self.ctrl = ctrl
        self.lock_size = lock_size
        self.groups = groups
        self.lay = QVBoxLayout(self)
        self.lay.setContentsMargins(0, 0, 0, 0)
        # the name: {variables} of the tool, and what the name becomes
        row = QHBoxLayout()
        self.name_shown = QLabel()
        self.name_shown.setStyleSheet("color: gray")
        row.addWidget(self.name_shown, 1)
        var_b = QToolButton()
        var_b.setText("{…}")
        var_b.setToolTip("Put a variable of the tool in its name (the diameter, the type, the number ...)")
        var_b.setPopupMode(QToolButton.InstantPopup)
        vmenu = QMenu(var_b)
        from ..core.tooling import TOOL_NAME_VARIABLES
        for name, en, _tr in TOOL_NAME_VARIABLES:
            vmenu.addAction(f"{{{name}}}  {en}", lambda n=name: self._insert_variable(n))
        var_b.setMenu(vmenu)
        row.addWidget(var_b)
        self.lay.addLayout(row)
        self.form = None
        self._tool = None
        self.set_tool(tool or Tool())

    def _insert_variable(self, name: str) -> None:
        w = self.form.widgets.get("name")
        if isinstance(w, QLineEdit):
            w.insert("{" + name + "}")
            w.setFocus()

    def _show_name(self, *_) -> None:
        try:
            t = self.tool()
        except Exception:  # noqa: BLE001  (a form half filled in)
            return
        self.name_shown.setText(f"Name: <b>{t.label}</b>" if t.label != t.name else "")

    def set_groups(self, groups: list[str]) -> None:
        """The groups of the tool database changed: the Group field offers them."""
        self.groups = groups
        self.set_tool(self.tool())

    def set_tool(self, t: Tool) -> None:
        self._tool = t
        if self.form is not None:
            self.lay.removeWidget(self.form)
            self.form.deleteLater()
        self.form = ParamForm(tool_params(t, self.groups), None, self.ctrl.units)
        self.form.changed.connect(self._show_name)
        if self.lock_size:
            for name in self.SIZE_FIELDS:
                w = self.form.widgets.get(name)
                if w is not None:
                    w.setEnabled(False)
                    w.setToolTip("The size of the tool cannot be changed here: choose another tool (Select from "
                                 "database...) or change it in the tool database")
        self.lay.addWidget(self.form)
        self._show_name()

    def tool(self) -> Tool:
        t = self._tool.copy()
        for k, v in self.form.values().items():
            setattr(t, k, v)
        return t


class FeedsDialog(QDialog):
    """Chip load based feeds and speeds for a tool."""

    def __init__(self, ctrl, tool: Tool, parent=None):
        super().__init__(parent)
        from ..core.feeds import MATERIALS
        self.ctrl = ctrl
        self.setWindowTitle(f"Feeds & Speeds — {tool.name}")
        params = [Param("material", "Material", "choice", "mdf", choices=[(k, v[0]) for k, v in MATERIALS.items()]),
                  Param("diameter", "Diameter", "length", tool.diameter, min=0.1),
                  Param("flutes", "Flutes", "int", tool.flutes, min=1, max=12),
                  Param("rpm", "Spindle speed (rpm)", "int", tool.spindle_speed or 18000, min=1000, max=100000)]
        lay = QVBoxLayout(self)
        self.params = params
        self.form = ParamForm(params, ctrl.settings.recall("feeds", params[:1]), ctrl.units)   # the material
        lay.addWidget(self.form)
        self.result_label = QLabel()
        self.result_label.setTextFormat(Qt.RichText)
        lay.addWidget(self.result_label)
        note = QLabel("<i>Starting values only — check the tool maker's recommendations.</i>")
        lay.addWidget(note)
        lay.addWidget(buttons(self, "Apply to tool"))
        self.form.changed.connect(self.update_result)
        self.update_result()

    def suggestion(self):
        from ..core.feeds import suggest
        v = self.form.values()
        return suggest(v["material"], v["diameter"], v["flutes"], v["rpm"]), v

    def update_result(self) -> None:
        c, v = self.suggestion()
        u = self.ctrl.units
        self.result_label.setText(
            f"Chip load <b>{c.chip_load:.3f} mm</b><br>Feed rate <b>{c.feed_rate:.0f} mm/min</b>, "
            f"plunge {c.plunge_rate:.0f} mm/min<br>Stepdown {u.fmt(c.stepdown)}, stepover {u.fmt(c.stepover)}"
            f"<br>Surface speed {c.surface_speed:.0f} m/min")

    def apply_to(self, t: Tool) -> Tool:
        c, v = self.suggestion()
        self.ctrl.settings.remember("feeds", v, self.params[:1])
        t = t.copy()
        t.feed_rate, t.plunge_rate = round(c.feed_rate), round(c.plunge_rate)
        t.stepdown, t.stepover = round(c.stepdown, 2), round(c.stepover, 2)
        t.spindle_speed, t.flutes = int(v["rpm"]), int(v["flutes"])
        return t


class ToolDatabaseDialog(RemembersSize, QDialog):
    """Edit the tool library; in pick mode the selected tool is returned."""

    def __init__(self, ctrl, parent=None, pick: bool = False, select_id: str | None = None, title: str = ""):
        super().__init__(parent)
        self.ctrl = ctrl
        self.setWindowTitle(title or ("Select Tool" if pick else "Tool Database"))
        self.lib_tools = [t.copy() for t in ctrl.tools.tools]
        self.groups: list[str] = list(ctrl.tools.groups())        # (empty groups too, in their order)
        self.current_group: str | None = None
        lay = QVBoxLayout(self)
        split = QSplitter()
        left = QWidget()
        ll = QVBoxLayout(left)
        ll.setContentsMargins(0, 0, 0, 0)
        self.tree = QTreeWidget()
        self.tree.setHeaderHidden(True)
        self.tree.currentItemChanged.connect(self._changed)
        if pick:
            self.tree.itemDoubleClicked.connect(lambda *_: self.accept())
        ll.addWidget(self.tree)
        row = QHBoxLayout()
        for text, slot in (("Add", self.add), ("Copy", self.copy), ("Delete", self.delete),
                           ("Feeds...", self.feeds), ("Import...", self.import_), ("Export...", self.export)):
            b = QPushButton(text)
            b.clicked.connect(slot)
            row.addWidget(b)
        ll.addLayout(row)
        grow = QHBoxLayout()
        for text, slot, tip in (("New group...", self.new_group, "Make a group of tools (an empty one: put tools in it with "
                                                                  "their Group field)"),
                                ("Rename group...", self.rename_group, "Rename the group that is selected (its tools move with it)"),
                                ("Delete group", self.delete_group, "Remove the group that is selected; its tools stay, "
                                                                    "out of every group")):
            b = QPushButton(text)
            b.setToolTip(tip)
            b.clicked.connect(slot)
            grow.addWidget(b)
        grow.addStretch(1)
        ll.addLayout(grow)
        split.addWidget(left)
        self.editor = ToolEditor(ctrl, groups=self.groups)
        split.addWidget(scroll(self.editor))
        split.setSizes([260, 380])
        lay.addWidget(split)
        lay.addWidget(buttons(self, "Select" if pick else "Save"))
        self.current: Tool | None = None
        self.refresh(select_id)
        self.keep_size(ctrl, "tools", 720, 620)

    GROUP_ROLE = Qt.UserRole + 1

    def _commit_editor(self) -> None:
        if self.current is not None:
            new = self.editor.tool()
            for i, t in enumerate(self.lib_tools):
                if t.id == self.current.id:
                    self.lib_tools[i] = new
                    self.current = new
            if new.group not in self.groups:
                self.groups.append(new.group)

    def refresh(self, select_id: str | None = None, select_group: str | None = None) -> None:
        self.tree.blockSignals(True)
        self.tree.clear()
        groups: dict[str, QTreeWidgetItem] = {}
        target = None
        order = list(dict.fromkeys(self.groups + [t.group for t in self.lib_tools]))
        for name in order:
            g = QTreeWidgetItem([name or "Ungrouped"])
            g.setData(0, self.GROUP_ROLE, name)
            f = g.font(0)
            f.setBold(True)
            g.setFont(0, f)
            self.tree.addTopLevelItem(g)
            g.setExpanded(True)
            groups[name] = g
            if select_group is not None and name == select_group:
                target = g
        for t in self.lib_tools:
            it = QTreeWidgetItem([f"T{t.number}  {t.label}"])
            it.setData(0, Qt.UserRole, t.id)
            groups[t.group].addChild(it)
            if select_group is None and t.id == (select_id or (self.current.id if self.current else None)):
                target = it
        for name, g in groups.items():
            if not g.childCount():
                g.setText(0, f"{name or 'Ungrouped'}  (empty)")
        self.tree.blockSignals(False)
        if target is None and self.lib_tools:
            target = groups[self.lib_tools[0].group].child(0)
        if target is not None:
            self.tree.setCurrentItem(target)

    def _changed(self, cur, prev) -> None:
        self._commit_editor()
        if cur is not None and cur.data(0, Qt.UserRole) is None:      # (a group: what Rename / Delete group act on)
            self.current_group = cur.data(0, self.GROUP_ROLE)
            return
        tid = cur.data(0, Qt.UserRole) if cur else None
        t = next((t for t in self.lib_tools if t.id == tid), None)
        if t is not None:
            self.current = t
            self.current_group = t.group
            self.editor.set_tool(t)

    # -- groups --------------------------------------------------------------------------------------------------------
    def _group_names(self) -> list[str]:
        return list(dict.fromkeys(self.groups + [t.group for t in self.lib_tools]))

    def new_group(self) -> None:
        name, ok = QInputDialog.getText(self, "New group", "Name of the group:")
        name = (name or "").strip()
        if not ok or not name:
            return
        self._commit_editor()
        if name not in self._group_names():
            self.groups.append(name)
        self.editor.set_groups(self._group_names())
        self.refresh(select_group=name)

    def rename_group(self) -> None:
        old = self.current_group
        if old is None:
            QMessageBox.information(self, "Rename group", "Select a group (or a tool of it) first.")
            return
        name, ok = QInputDialog.getText(self, "Rename group", "New name of the group:", text=old)
        name = (name or "").strip()
        if not ok or not name or name == old:
            return
        self._commit_editor()
        for t in self.lib_tools:
            if t.group == old:
                t.group = name
        self.groups = list(dict.fromkeys(name if g == old else g for g in self._group_names()))
        self.current_group = name
        if self.current is not None:
            self.editor.groups = self._group_names()
            self.editor.set_tool(next(t for t in self.lib_tools if t.id == self.current.id))
        self.refresh(select_group=name)

    def delete_group(self) -> None:
        name = self.current_group
        if name is None:
            QMessageBox.information(self, "Delete group", "Select a group (or a tool of it) first.")
            return
        n = sum(1 for t in self.lib_tools if t.group == name)
        if n and QMessageBox.question(self, "Delete group", f"The group '{name}' has {n} tool(s): they stay, out of every "
                                                           "group. Delete the group?") != QMessageBox.Yes:
            return
        self._commit_editor()
        for t in self.lib_tools:
            if t.group == name:
                t.group = ""
        self.groups = [g for g in self._group_names() if g != name]
        self.current_group = None
        self.editor.groups = self._group_names()
        if self.current is not None:
            self.current = next(t for t in self.lib_tools if t.id == self.current.id)
            self.editor.set_tool(self.current)
        self.refresh()

    def add(self) -> None:
        self._commit_editor()
        t = Tool(name="New tool", number=max((t.number for t in self.lib_tools), default=0) + 1,
                 group=self.current_group if self.current_group is not None else "End Mills")
        self.lib_tools.append(t)
        self.current = t
        self.editor.set_tool(t)          # (before the list moves to it: or the tool shown before is written over the new one)
        self.refresh(t.id)

    def copy(self) -> None:
        self._commit_editor()
        if self.current:
            t = self.current.copy(new_id=True)
            t.name += " copy"
            self.lib_tools.append(t)
            self.refresh(t.id)

    def delete(self) -> None:
        if self.current:
            self.lib_tools = [t for t in self.lib_tools if t.id != self.current.id]
            self.current = None
            self.refresh()

    def feeds(self) -> None:
        if self.current is None:
            return
        dlg = FeedsDialog(self.ctrl, self.editor.tool(), self)
        if dlg.exec():
            t = dlg.apply_to(self.editor.tool())
            self.editor.set_tool(t)

    def import_(self) -> None:
        fn = open_file(self, self.ctrl.settings, "tools", "Import tools", "Tool library (*.json)")
        if fn:
            from ..core.tooling import ToolLibrary
            try:
                lib = ToolLibrary.load(Path(fn))
            except (OSError, ValueError, TypeError, KeyError, AttributeError) as exc:
                QMessageBox.warning(self, "Import tools", f"This is not a tool library: {exc}")
                return
            self._commit_editor()
            self.lib_tools += [t.copy(new_id=True) for t in lib.tools]
            self.refresh()

    def export(self) -> None:
        fn, _ = save_file(self, self.ctrl.settings, "tools", "Export tools", "tools.json", "Tool library (*.json)")
        if fn:
            from ..core.tooling import ToolLibrary
            self._commit_editor()
            ToolLibrary(self.lib_tools).save(Path(fn))

    def selected_tool(self) -> Tool | None:
        self._commit_editor()
        return self.current.copy() if self.current else None

    def accept(self) -> None:
        self._commit_editor()
        self.ctrl.tools.tools = self.lib_tools
        self.ctrl.tools.group_names = [g for g in self._group_names() if g]
        try:
            self.ctrl.save_tools()
        except OSError as exc:
            QMessageBox.warning(self, "Tool database", f"Could not save the tool library:\n{exc}")
        super().accept()


# ---------------------------------------------------------------------------
# toolpaths
# ---------------------------------------------------------------------------

class ToolpathDialog(RemembersSize, QDialog):
    def __init__(self, ctrl, op_id: str, tdef: ToolpathDef | None = None, parent=None, sheet_group: str = "",
                 tool: Tool | None = None):
        super().__init__(parent)
        self.ctrl = ctrl
        self.op = ctrl.registry.operations[op_id]
        self.tdef = copy.deepcopy(tdef) if tdef else None
        self.setWindowTitle(f"{self.op.label} Toolpath")
        lay = QVBoxLayout(self)
        if self.op.description:
            d = QLabel(self.op.description)
            d.setWordWrap(True)
            lay.addWidget(d)
        top = QFormLayout()
        n = sum(1 for t in ctrl.project.toolpaths if t.op == op_id) + 1
        self.name = QLineEdit(self.tdef.name if self.tdef else f"{self.op.label.split(' (')[0]} {n}")
        self.name.setToolTip("The name may have {variables} of the toolpath and sums, as the file name prefixes: "
                             "Profile Ø{tool_diameter} F{feed_rate/60:.1} -> Profile Ø6 F80.0")
        nrow = QHBoxLayout()
        nrow.addWidget(self.name, 1)
        var_b = QToolButton()
        var_b.setText("{…}")
        var_b.setToolTip("Put a variable of the toolpath in the name (the speed, the tool, the depth ...)")
        var_b.setPopupMode(QToolButton.InstantPopup)
        vmenu = QMenu(var_b)
        from ..post.naming import TOOLPATH_VARIABLES
        for v in TOOLPATH_VARIABLES:
            vmenu.addAction(f"{{{v['name']}}}  {v['text']['en']}",
                            lambda n=v["name"]: (self.name.insert("{" + n + "}"), self.name.setFocus()))
        vmenu.addSeparator()
        vmenu.addAction("{feed_rate/60:.1}  Feed rate in mm/s, one decimal",
                        lambda: (self.name.insert("{feed_rate/60:.1}"), self.name.setFocus()))
        var_b.setMenu(vmenu)
        nrow.addWidget(var_b)
        top.addRow("Name", nrow)
        self.name_shown = QLabel()
        self.name_shown.setStyleSheet("color: gray")
        top.addRow("", self.name_shown)
        prow = QHBoxLayout()
        self.preset_box = QComboBox()
        self.preset_box.setToolTip("Presets of toolpaths: what a toolpath was set to, kept under a name")
        self._fill_presets()
        apply_b = QPushButton("Apply")
        apply_b.setToolTip("Put the preset on this toolpath (its sums are worked out with this material and sheet)")
        apply_b.clicked.connect(self.apply_preset)
        save_b = QPushButton("Save as preset...")
        save_b.setToolTip("Keep what this toolpath is set to under a name: you choose what is kept, and which numbers follow the job")
        save_b.clicked.connect(self.save_preset)
        del_b = QPushButton("Delete")
        del_b.clicked.connect(self.delete_preset)
        prow.addWidget(self.preset_box, 1)
        for b in (apply_b, save_b, del_b):
            prow.addWidget(b)
        top.addRow("Preset", prow)
        self._sequence_extra: dict = {}
        self._group_extra: str | None = None
        self.keep_old = QCheckBox("Keep the original: save this as a new toolpath")
        self.auto_new = QCheckBox("Changing the name does this by itself")
        self.auto_new.setChecked(bool(ctrl.settings.rename_makes_new))
        self._first_name = self.name.text()
        self.name.textChanged.connect(self._name_edited)
        self.name.textChanged.connect(self._show_name)
        if self.tdef is not None:                          # (a toolpath that is edited: the name can make a new one)
            top.addRow(self.keep_old)
            top.addRow(self.auto_new)
        lay.addLayout(top)
        tabs = QTabWidget()
        lay.addWidget(tabs)
        # -- tool tab
        tool_tab = QWidget()
        tl = QVBoxLayout(tool_tab)
        row = QHBoxLayout()
        self.tool_label = QLabel()
        row.addWidget(self.tool_label, 1)
        b = QPushButton("Select from database...")
        b.clicked.connect(self.pick_tool)
        row.addWidget(b)
        tl.addLayout(row)
        self.memory_key = f"toolpath:{op_id}"
        mem = {} if self.tdef else ctrl.settings.recall(self.memory_key)
        # a new toolpath takes the tool as it is in the tool database (the one chosen for it, else the one chosen last time):
        # what was changed on the tool of another toolpath (its feed rate ...) is that toolpath's only
        tool = self.tdef.tool if self.tdef else tool or self.library_tool(ctrl, self.op, mem.get("tool_id")) \
            or self._default_tool()
        self.tool_editor = ToolEditor(ctrl, tool, lock_size=True)
        tl.addWidget(scroll(self.tool_editor))
        # -- vectors tab
        vec_tab = QWidget()
        vl = QVBoxLayout(vec_tab)
        self.rb_sel = QRadioButton()
        self.rb_layers = QRadioButton("Vectors on these layers (on every sheet it is calculated on):")
        grp = QButtonGroup(self)
        grp.addButton(self.rb_sel)
        grp.addButton(self.rb_layers)
        vl.addWidget(self.rb_sel)
        vl.addWidget(self.rb_layers)
        self.layer_list = QListWidget()
        sel = self.tdef.selection if self.tdef else None
        from ..core.scope import layer_in_scope, new_item_scope as _scope
        side = self.tdef.sheet_group if self.tdef is not None else (sheet_group or _scope(ctrl.sheet))
        if side and not any(x.group == side and x.isolated for x in ctrl.project.sheets):
            side = ""                                      # (a group that is not isolated: the layers of the rest)
        for l in ctrl.project.layers:
            if not layer_in_scope(ctrl.project, l, side) and not (sel is not None and l.id in sel.layer_ids):
                continue                                   # (the layers of another isolated group are not offered)
            it = QListWidgetItem(l.name)
            it.setData(Qt.UserRole, l.id)
            it.setFlags(it.flags() | Qt.ItemIsUserCheckable)
            checked = (sel is not None and l.id in sel.layer_ids)
            it.setCheckState(Qt.Checked if checked else Qt.Unchecked)
            self.layer_list.addItem(it)
        vl.addWidget(self.layer_list)
        # where it is calculated: on the side of the project it is made on (core.scope): the sheets of the isolated group
        # open, else every sheet that is not isolated; a toolpath kept to some sheets (made before) stays on them
        from ..core.scope import new_item_scope
        self.all_sheets = QCheckBox("Calculate on every sheet")
        self.all_sheets.setChecked(self.tdef is None or self.tdef.sheet_ids is None)
        self.all_sheets.hide()
        scope = self.tdef.sheet_group if self.tdef is not None else (sheet_group or new_item_scope(ctrl.sheet))
        self.group_box = QComboBox()
        self.group_box.addItem("(not by group)", "")
        if scope:
            self.group_box.addItem(f"The isolated group: {scope}", scope)
        self.group_box.setCurrentIndex(max(0, self.group_box.findData(scope)))
        self.group_box.hide()
        if scope:
            where = (f"▣ Isolated group <b>{scope}</b>: this toolpath is the group's, calculated on its sheets only"
                     if self.tdef is None or ctrl.project.sheets and any(x.group == scope and x.isolated for x in ctrl.project.sheets)
                     else f"Calculated on the sheets of the group <b>{scope}</b>")
        elif self.tdef is not None and self.tdef.sheet_ids is not None:
            n = len(self.tdef.sheet_ids)
            where = f"Calculated on {n} sheet{'s' if n != 1 else ''} it was kept to"
        else:
            where = "Calculated on every sheet that is not isolated (isolate a sheet or a group in the Sheets panel to " \
                    "give it toolpaths of its own)"
        self.scope_label = QLabel(where)
        self.scope_label.setWordWrap(True)
        vl.addWidget(self.scope_label)
        cur_ids = [o.id for o in ctrl.selected_objects()]
        if self.tdef and self.tdef.selection.mode == "vectors" and not cur_ids:
            cur_ids = list(self.tdef.selection.vector_ids)
        self.vector_ids = cur_ids
        self._open_sheet_id = ctrl.sheet_id               # (the sheet a new toolpath on "this sheet" means: where it was opened)
        self._last_selection = list(ctrl.selection)
        self._first_all_sheets = None
        self.rb_sel.setText(f"Selected vectors ({len(cur_ids)})")
        self.follow = QCheckBox("Follow the selection on the drawing while this window is open")
        self.follow.setChecked(self.tdef is None)
        self.follow.setToolTip("Select vectors on the drawing with this window open: they are the vectors of the toolpath")
        vl.addWidget(self.follow)
        ctrl.selection_changed.connect(self._follow_selection)
        ctrl.project_replaced.connect(self._project_gone)
        self.finished.connect(self._let_go)
        (self.rb_layers if sel is not None and sel.mode == "layers" else self.rb_sel).setChecked(True)
        if not cur_ids and (sel is None or sel.mode != "layers"):
            self.rb_layers.setChecked(True)
            for i in range(self.layer_list.count()):
                it = self.layer_list.item(i)
                if it.data(Qt.UserRole) == ctrl.layer_id:
                    it.setCheckState(Qt.Checked)
        # -- parameters tab
        from ..cam import tp_defaults
        params = tp_defaults.apply(self.op.id, self.op.params())     # (the person's own: how far a through cut goes below)
        grp = sheet_group or (self.tdef.sheet_group if self.tdef else "")
        target = next((x for x in ctrl.project.sheets if grp and x.group == grp), None) or ctrl.sheet
        for p in params:
            if p.name == "cut_depth" and not self.tdef and self.op.id == "profile" and target is not None:
                p.default = ctrl.project.sheet_thickness(target)     # (the sheet it is made for: of its group, else the one open)
        # a new toolpath starts from the default settings every time (allowance, depths, passes ...: nothing is carried
        # over from the last one, which could cut a part too big or too small unnoticed); the tool chosen last is kept
        self.params = [p for p in params if not (p.name == "cut_depth" and self.op.id == "profile")]
        values = dict(self.tdef.params) if self.tdef else {}
        self.form = ParamForm(params, values, ctrl.units, ctrl.tools, collapse=True)
        settings_tab = QWidget()
        sl = QVBoxLayout(settings_tab)
        sl.setContentsMargins(0, 0, 0, 0)
        sl.addWidget(self._heights_box())
        sl.addWidget(self.form, 1)
        tabs.addTab(scroll(settings_tab), "Settings")
        tabs.addTab(tool_tab, "Tool")
        tabs.addTab(vec_tab, "Vectors")
        lay.addWidget(buttons(self, "Calculate"))
        self._update_tool_label()
        self.tool_editor.form.changed.connect(self._update_tool_label)
        self.keep_size(ctrl, f"toolpath:{op_id}", 460, 680)

    def _heights_box(self) -> QGroupBox:
        """Safe Z and the rapid-down clearance: the project's (set in Material Setup) or this toolpath's own."""
        u = self.ctrl.units
        m = self.ctrl.project.material
        t = self.tdef
        own = t is not None and (t.safe_z is not None or t.clearance is not None)
        box = QGroupBox("Heights above the material surface")
        form = QFormLayout(box)
        self.heights_note = QLabel()
        self.heights_note.setWordWrap(True)
        row = QHBoxLayout()
        row.addWidget(self.heights_note, 1)
        setup = QPushButton("Material Setup...")
        setup.setToolTip("Safe Z, rapid-down clearance, Z zero, thickness and the home position of the project")
        setup.clicked.connect(self._material_setup)
        row.addWidget(setup)
        form.addRow(row)
        self.own_heights = QCheckBox("This toolpath has heights of its own")
        self.own_heights.setToolTip("Off: the toolpath travels at the safe Z of the project. On: it travels at the "
                                    "safe Z below (to clear clamps, say) and rapids down to its own clearance.")
        self.own_heights.setChecked(own)
        form.addRow(self.own_heights)
        self.safe_z = QDoubleSpinBox()
        self.clearance = QDoubleSpinBox()
        for w, v, lo in ((self.safe_z, m.safe_z if t is None or t.safe_z is None else t.safe_z, 0.1),
                         (self.clearance, m.clearance if t is None or t.clearance is None else t.clearance, 0.0)):
            w.setRange(u.to_display(lo), u.to_display(1e5))
            w.setDecimals(u.decimals)
            w.setSuffix(u.suffix)
            w.setValue(u.to_display(v))
        self.safe_z.setToolTip("The tool retracts to this height and travels at it between cuts")
        self.clearance.setToolTip("The tool rapids down to this height above the material and then plunges")
        form.addRow("Safe Z", self.safe_z)
        form.addRow("Rapid-down clearance", self.clearance)
        self.own_heights.toggled.connect(self._heights_changed)
        self._heights_changed()
        return box

    def _heights_changed(self, *_) -> None:
        u = self.ctrl.units
        m = self.ctrl.project.material
        own = self.own_heights.isChecked()
        self.safe_z.setEnabled(own)
        self.clearance.setEnabled(own)
        if not own:     # (shows what the toolpath will use)
            self.safe_z.setValue(u.to_display(m.safe_z))
            self.clearance.setValue(u.to_display(m.clearance))
        self.heights_note.setText(f"Project: safe Z <b>{u.fmt(m.safe_z)}</b>, rapid-down clearance "
                                  f"<b>{u.fmt(m.clearance)}</b>")

    def _material_setup(self) -> None:
        MaterialDialog(self.ctrl, self).exec()
        self._heights_changed()

    def heights(self) -> tuple[float | None, float | None]:
        """``(safe Z, clearance)`` of the toolpath in millimetres; ``None`` for the project's."""
        if not self.own_heights.isChecked():
            return None, None
        u = self.ctrl.units
        return u.from_display(self.safe_z.value()), u.from_display(self.clearance.value())

    def _show_name(self, *_) -> None:
        """What a name with {variables} becomes (nothing is shown for a plain name)."""
        if not hasattr(self, "name_shown"):
            return
        text = self.name.text()
        if "{" not in text:
            self.name_shown.setText("")
            return
        from ..post.naming import toolpath_name
        try:
            t = self.toolpath()
            self.name_shown.setText("→ " + toolpath_name(self.ctrl.project, t, self.ctrl.sheet))
        except Exception:  # noqa: BLE001  (a form half filled in: nothing to show yet)
            self.name_shown.setText("")

    @staticmethod
    def library_tool(ctrl, op, tool_id) -> Tool | None:
        """A copy of the tool ``tool_id`` of the tool database, as it is there (when it fits the toolpath)."""
        t = ctrl.tools.get(tool_id) if isinstance(tool_id, str) else None
        if t is None or (op.tool_types is not None and t.type not in op.tool_types):
            return None
        return t.copy()

    def _remember(self) -> None:
        s = self.ctrl.settings
        s.memory.pop(self.memory_key + ":params", None)      # (settings are not carried over to new toolpaths)
        # which tool of the database was used (not the tool as it was changed for this toolpath: that is not carried over)
        s.remember(self.memory_key, {"tool_id": self.tool_editor.tool().id, "all_sheets": self.all_sheets.isChecked()})

    def _default_tool(self) -> Tool:
        lib = self.ctrl.tools.tools
        types = self.op.tool_types
        for t in lib:
            if types is None or t.type in types:
                return t.copy()
        return Tool()

    def _update_tool_label(self) -> None:
        t = self.tool_editor.tool()
        warn = ""
        if self.op.tool_types and t.type not in self.op.tool_types:
            warn = "  ⚠ not recommended for this toolpath"
        self.tool_label.setText(f"<b>{t.describe()}</b>{warn}")

    def pick_tool(self) -> None:
        dlg = ToolDatabaseDialog(self.ctrl, self, pick=True)
        if dlg.exec():
            t = dlg.selected_tool()
            if t is not None:
                self.tool_editor.set_tool(t)
                self.tool_editor.form.changed.connect(self._update_tool_label)
                self._update_tool_label()

    def toolpath(self) -> ToolpathDef:
        """The toolpath as set in the dialog (a new definition each time it is called for a new toolpath)."""
        layers = [self.layer_list.item(i).data(Qt.UserRole) for i in range(self.layer_list.count())
                  if self.layer_list.item(i).checkState() == Qt.Checked]
        if self.rb_layers.isChecked():
            sel = Selection("layers", [], layers)
        else:
            sel = Selection("vectors", list(self.vector_ids), [])
        group = self.group_box.currentData() or ""
        if group or self.all_sheets.isChecked():
            sheets = None
        elif self.tdef is not None and self.tdef.sheet_ids is not None and self._sheets_box_untouched():
            sheets = list(self.tdef.sheet_ids)            # (a toolpath of some sheets stays on them: not moved to the sheet that is open)
        else:
            sheets = [self._open_sheet_id]
            if sel.mode == "vectors" and sel.vector_ids:       # (the sheet of the vectors chosen, not the one the window opened on)
                ids = set(sel.vector_ids)
                holding = [s.id for s in self.ctrl.project.sheets if any(v.id in ids for v in s.vectors)]
                if holding and self._open_sheet_id not in holding:
                    sheets = holding
        params = {k: v for k, v in self.form.values().items()}
        safe_z, clearance = self.heights()
        if self.tdef:
            t = self.tdef
            t.name, t.tool, t.params, t.selection, t.sheet_ids = self.name.text(), self.tool_editor.tool(), \
                params, sel, sheets
            t.sheet_group = group
            t.safe_z, t.clearance = safe_z, clearance
        else:
            t = ToolpathDef(self.name.text(), self.op.id, self.tool_editor.tool(), params, sel, True, sheets,
                            safe_z=safe_z, clearance=clearance, sheet_group=group)
        t.sequence.update(self._sequence_extra)             # (what a preset set of the cut order)
        if self._group_extra is not None:
            t.group = self._group_extra
        return t

    # -- presets -------------------------------------------------------------------------------------------------------
    def _fill_presets(self) -> None:
        from ..cam import tp_presets
        self.preset_box.clear()
        self.preset_box.addItem("(no preset)", None)
        for p in tp_presets.load():
            mark = "" if not p.get("op") or p["op"] == self.op.id else f"   [{p['op']}]"
            self.preset_box.addItem(p["name"] + mark, p["id"])

    def apply_preset(self) -> None:
        """Put the chosen preset on the window: the fields it keeps change, the others stay as they are."""
        from ..cam import tp_presets
        pid = self.preset_box.currentData()
        pre = next((p for p in tp_presets.load() if p.get("id") == pid), None)
        if pre is None:
            return
        t = copy.deepcopy(self.toolpath())
        notes = tp_presets.apply(pre, t, self.ctrl.project, self.ctrl.sheet, self.op.params())
        if pre.get("op") and pre["op"] != self.op.id:
            notes.insert(0, f"the preset is of a {pre['op']} toolpath: only what this one has was put")
        self.name.setText(t.name)
        if "tool" in pre.get("fields", {}):
            self.tool_editor.set_tool(t.tool)
            self.tool_editor.form.changed.connect(self._update_tool_label)
            self._update_tool_label()
        self.form.set_values(t.params)
        flds = pre.get("fields", {})
        if "safe_z" in flds or "clearance" in flds:
            own = t.safe_z is not None or t.clearance is not None
            self.own_heights.setChecked(own)
            u = self.ctrl.units
            if t.safe_z is not None:
                self.safe_z.setValue(u.to_display(t.safe_z))
            if t.clearance is not None:
                self.clearance.setValue(u.to_display(t.clearance))
        # (the group of sheets of a preset is not taken: a toolpath stays on the side of the project it is made on)
        self._sequence_extra = {k[9:]: v["value"] for k, v in flds.items() if k.startswith("sequence.") and "value" in v}
        self._sequence_extra.update({k: v for k, v in t.sequence.items() if f"sequence.{k}" in flds})
        if "group" in flds:
            self._group_extra = t.group
        if notes:
            QMessageBox.information(self, "Preset", "\n".join(notes))

    def save_preset(self) -> None:
        from ..cam import tp_presets
        t = copy.deepcopy(self.toolpath())
        dlg = PresetSaveDialog(tp_presets.fields(t, self.op.params()), self.preset_box.currentText()
                               if self.preset_box.currentData() else t.name, self)
        if not dlg.exec():
            return
        try:
            tp_presets.save({"name": dlg.name.text(), "op": self.op.id, "fields": dlg.chosen()})
        except tp_presets.PresetError as exc:
            QMessageBox.warning(self, "Preset", str(exc))
            return
        self._fill_presets()
        self.preset_box.setCurrentIndex(max(0, self.preset_box.findText(dlg.name.text().strip(), Qt.MatchStartsWith)))

    def delete_preset(self) -> None:
        from ..cam import tp_presets
        pid = self.preset_box.currentData()
        if pid and QMessageBox.question(self, "Preset", f"Delete the preset '{self.preset_box.currentText()}'?",
                                        QMessageBox.Yes | QMessageBox.No) == QMessageBox.Yes:
            tp_presets.delete(pid)
            self._fill_presets()

    def _sheets_box_untouched(self) -> bool:
        return self.all_sheets.isChecked() == (self.tdef is None or self.tdef.sheet_ids is None)

    def _name_edited(self, text: str) -> None:
        if self.tdef is not None and self.auto_new.isChecked() and text != self._first_name:
            self.keep_old.setChecked(True)

    def _follow_selection(self) -> None:
        """The selection on the drawing changed while the window is open: the vectors of the toolpath follow it."""
        if not self.follow.isChecked() or not hasattr(self, "rb_sel"):
            return
        if list(self.ctrl.selection) == self._last_selection:           # (an edit that changes nothing in the selection: nothing to follow)
            return
        self._last_selection = list(self.ctrl.selection)
        ids = [o.id for o in self.ctrl.selected_objects()]
        if ids:
            self.vector_ids = ids
            self.rb_sel.setText(f"Selected vectors ({len(ids)})")
            self.rb_sel.setChecked(True)

    def _let_go(self, *_) -> None:
        for sig, slot in ((self.ctrl.selection_changed, self._follow_selection), (self.ctrl.project_replaced, self._project_gone)):
            try:
                sig.disconnect(slot)
            except (RuntimeError, TypeError):
                pass

    def _project_gone(self) -> None:
        """The project of this window was replaced (a file opened in its place, its tab emptied): the window has nothing to work on."""
        self.reject()

    def open_modeless(self, on_accept) -> None:
        """Show the window beside the drawing, not as a pop-up in front of it: the vectors can be selected, the drawing zoomed, ...
        while it is open; ``on_accept()`` is called when the person presses Calculate."""
        self.setWindowModality(Qt.NonModal)
        self.setWindowFlag(Qt.Tool, True)
        self.accepted.connect(on_accept)
        win = self.parent()
        if win is not None and hasattr(win, "frameGeometry"):
            g = win.frameGeometry()
            self.resize(min(self.width(), 480), min(self.height(), max(400, g.height() - 80)))
            self.move(max(0, g.right() - self.width() - 8), g.top() + 40)       # (the right edge of the program)
        self.show()
        self.raise_()
        self.activateWindow()

    def accept(self) -> None:
        if self.rb_sel.isChecked() and not self.vector_ids:
            QMessageBox.information(self, "Toolpath", "Select vectors first, or choose layers on the Vectors tab.")
            return
        self.ctrl.settings.rename_makes_new = self.auto_new.isChecked()
        self._remember()
        super().accept()


# ---------------------------------------------------------------------------
# nesting
# ---------------------------------------------------------------------------

class StockTable(QTableWidget):
    """The plates of the stock: name, size, quantity (0 = unlimited), leftover, own thickness."""

    COLUMNS = ["Use", "Name", "Size", "Quantity", "Leftover", "Thickness"]

    def __init__(self, ctrl, items: list[StockItem], checkable: bool = False, parent=None):
        super().__init__(len(items), len(self.COLUMNS), parent)
        self.ctrl, self.items = ctrl, items
        u = ctrl.units
        self.setHorizontalHeaderLabels(self.COLUMNS)
        self.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
        self.setColumnHidden(0, not checkable)
        self.use, self.qty, self.left, self.thick = [], [], [], []
        for r, it in enumerate(items):
            use = QCheckBox()
            use.setChecked(True)
            self.setCellWidget(r, 0, use)
            self.setItem(r, 1, QTableWidgetItem(it.name))
            size = QTableWidgetItem(f"{u.fmt(it.width)} x {u.fmt(it.height)}" + ("" if it.is_rect else "  (shape)"))
            size.setFlags(size.flags() & ~Qt.ItemIsEditable)
            self.setItem(r, 2, size)
            qty = QSpinBox()
            qty.setRange(0, 9999)
            qty.setSpecialValueText("unlimited")
            qty.setValue(it.quantity)
            self.setCellWidget(r, 3, qty)
            left = QCheckBox()
            left.setChecked(it.leftover)
            left.setToolTip("Leftovers are used before new sheets")
            self.setCellWidget(r, 4, left)
            thick = QDoubleSpinBox()
            thick.setDecimals(u.decimals)
            thick.setRange(0.0, 1e4)
            thick.setSuffix(u.suffix)
            thick.setSpecialValueText("material")
            thick.setValue(u.to_display(it.thickness) if it.thickness else 0.0)
            self.setCellWidget(r, 5, thick)
            self.use.append(use)
            self.qty.append(qty)
            self.left.append(left)
            self.thick.append(thick)

    def used(self, r: int) -> bool:
        return self.use[r].isChecked()

    def set_used(self, flags: list[bool]) -> None:
        for box, on in zip(self.use, flags):
            box.setChecked(on)

    def item(self, r: int) -> StockItem:  # type: ignore[override]
        """The plate of row ``r`` with the edits (a copy that keeps the id)."""
        it = self.items[r].copy()
        it.id = self.items[r].id
        name = QTableWidget.item(self, r, 1)
        it.name = name.text() if name is not None else it.name
        it.quantity = self.qty[r].value()
        it.leftover = self.left[r].isChecked()
        it.thickness = self.ctrl.units.from_display(self.thick[r].value()) or None
        return it

    def edited_items(self) -> list[StockItem]:
        return [self.item(r) for r in range(len(self.items))]


class PlateDialog(QDialog):
    """A plate for the stock: a rectangle of a size, or a plate of any of the shapes (circle, ellipse, polygon, L...)."""

    SHAPES = [("rect", "Rectangle"), ("rounded", "Rounded rectangle"), ("circle", "Circle (with an optional hole)"),
              ("ellipse", "Ellipse"), ("polygon", "Regular polygon"), ("l", "L shape")]

    def __init__(self, ctrl, parent=None):
        super().__init__(parent)
        self.ctrl = ctrl
        self.setWindowTitle("Add Plate")
        s = ctrl.sheet
        params = [Param("shape", "Shape", "choice", "rect", choices=self.SHAPES),
                  Param("name", "Name (empty: from the size)", "str", ""),
                  Param("width", "Width (X)", "length", s.width, min=1.0,
                        visible={"shape": ["rect", "rounded", "ellipse", "l"]}),
                  Param("height", "Height (Y)", "length", s.height, min=1.0,
                        visible={"shape": ["rect", "rounded", "ellipse", "l"]}),
                  Param("radius", "Corner radius", "length", 50.0, min=0.0, visible={"shape": "rounded"}),
                  Param("diameter", "Diameter", "length", min(s.width, s.height), min=1.0,
                        visible={"shape": ["circle", "polygon"]}, help="Across the corners for a polygon"),
                  Param("hole", "Hole in the middle (diameter, 0 = none)", "length", 0.0, min=0.0,
                        visible={"shape": "circle"}),
                  Param("sides", "Sides", "int", 6, min=3, max=64, visible={"shape": "polygon"}),
                  Param("notch_width", "Notch width (top right)", "length", s.width / 3, min=1.0, visible={"shape": "l"}),
                  Param("notch_height", "Notch height (top right)", "length", s.height / 3, min=1.0, visible={"shape": "l"}),
                  Param("quantity", "Quantity (0 = unlimited)", "int", 1, min=0, max=9999),
                  Param("leftover", "Leftover (use before new sheets)", "bool", False)]
        lay = QVBoxLayout(self)
        presets = QComboBox()
        presets.addItem("Preset sheet sizes...")
        for name, w, h in ctrl.sheet_sizes():
            presets.addItem(name, (w, h))
        lay.addWidget(presets)
        self.params = [p for p in params if p.name in ("quantity", "leftover", "shape")]   # the size follows the sheet
        self.form = ParamForm(params, ctrl.settings.recall("plate", self.params), ctrl.units)
        presets.activated.connect(lambda i: self._preset(presets.itemData(i)))
        lay.addWidget(self.form)
        lay.addWidget(buttons(self))

    def _preset(self, wh) -> None:
        if wh:
            u = self.ctrl.units
            self.form.widgets["width"].setValue(u.to_display(wh[0]))
            self.form.widgets["height"].setValue(u.to_display(wh[1]))

    def item(self) -> StockItem:
        from ..core.stock import shaped_plate
        v = self.form.values()
        self.ctrl.settings.remember("plate", v, self.params)
        sizes = {k: v[k] for k in ("width", "height", "radius", "diameter", "hole", "sides", "notch_width", "notch_height")
                 if k in v and v[k]}
        return shaped_plate(v["shape"], v["name"], v["quantity"], v["leftover"], None, **sizes)


class LeftoverDialog(QDialog):
    """What of a cut sheet is worth keeping."""

    def __init__(self, ctrl, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Leftover of the Sheet")
        tool_d = max((t.tool.diameter for t in ctrl.project.toolpaths if t.op == "profile"), default=6.0)
        params = [Param("clearance", "Cut width round the parts", "length", tool_d / 2 + 1.0, min=0.0,
                        help="How far the cuts reach beyond the parts: the tool radius and a little more"),
                  Param("min_width", "Narrowest strip worth keeping", "length", 30.0, min=0.0),
                  Param("min_area", "Smallest piece worth keeping (cm²)", "float", 25.0, min=0.0)]
        lay = QVBoxLayout(self)
        lay.addWidget(QLabel(f"The material that is left on <b>{ctrl.sheet.name}</b> after its parts are cut out "
                             "becomes plates of the stock."))
        self.ctrl, self.params = ctrl, params[1:]   # the cut width follows the tools; the rest is remembered
        self.form = ParamForm(params, ctrl.settings.recall("leftover", self.params), ctrl.units)
        lay.addWidget(self.form)
        lay.addWidget(buttons(self))

    def values(self) -> tuple[float, float, float]:
        v = self.form.values()
        self.ctrl.settings.remember("leftover", v, self.params)
        return v["clearance"], v["min_width"], v["min_area"] * 100.0


class StockDialog(RemembersSize, QDialog):
    """The stock of plates that jobs are nested onto."""

    def __init__(self, ctrl, parent=None):
        super().__init__(parent)
        self.ctrl = ctrl
        self.setWindowTitle("Stock of Plates")
        self.items: list[StockItem] = [it.copy() for it in ctrl.project.stock]
        for new, old in zip(self.items, ctrl.project.stock):
            new.id = old.id
        lay = QVBoxLayout(self)
        note = QLabel("Plates that jobs are nested onto: leftovers of any shape and the sheet sizes you buy. "
                      "Leftovers are used first, the smallest first.")
        note.setWordWrap(True)
        lay.addWidget(note)
        self.holder = QVBoxLayout()
        lay.addLayout(self.holder)
        self.table: StockTable | None = None
        self._build()
        row = QHBoxLayout()
        for text, tip, slot in (("Add plate...", "A rectangle of a size", self._add),
                                ("From selected vectors", "Closed vectors drawn as the outline of a plate; closed "
                                 "vectors inside it are holes", self._from_vectors),
                                ("Leftover of sheet...", "The material left on the current sheet", self._leftover),
                                ("Remove", "Remove the selected plates", self._remove)):
            b = QPushButton(text)
            b.setToolTip(tip)
            b.clicked.connect(slot)
            row.addWidget(b)
        lay.addLayout(row)
        row = QHBoxLayout()
        for text, slot in (("Load...", self._load), ("Save...", self._save)):
            b = QPushButton(text)
            b.clicked.connect(slot)
            row.addWidget(b)
        row.addStretch(1)
        lay.addLayout(row)
        lay.addWidget(buttons(self))
        self.keep_size(ctrl, "stock", 640, 420)

    def _build(self) -> None:
        if self.table is not None:
            self.holder.removeWidget(self.table)
            self.table.deleteLater()
        self.table = StockTable(self.ctrl, self.items)
        self.holder.addWidget(self.table)

    def _commit(self) -> None:
        """Keep the edits in the table before the list changes."""
        if self.table is not None:
            self.items = self.table.edited_items()

    def _add(self) -> None:
        dlg = PlateDialog(self.ctrl, self)
        if dlg.exec():
            self._commit()
            self.items.append(dlg.item())
            self._build()

    def _from_vectors(self) -> None:
        items = stock_from_vectors(self.ctrl.selected_objects())
        if not items:
            QMessageBox.information(self, "Stock", "Select closed vectors that outline the plates first.")
            return
        self._commit()
        self.items += items
        self._build()

    def _leftover(self) -> None:
        dlg = LeftoverDialog(self.ctrl, self)
        if not dlg.exec():
            return
        items = leftover_of_sheet(self.ctrl.sheet, *dlg.values())
        if not items:
            QMessageBox.information(self, "Stock", "Nothing worth keeping is left on this sheet.")
            return
        self._commit()
        self.items += items
        self._build()

    def _remove(self) -> None:
        rows = {i.row() for i in self.table.selectedIndexes()}
        if rows:
            self._commit()
            self.items = [it for r, it in enumerate(self.items) if r not in rows]
            self._build()

    def _load(self) -> None:
        fn = open_file(self, self.ctrl.settings, "stock", "Load stock", "Stock (*.json)")
        if not fn:
            return
        try:
            loaded = load_stock(fn)
        except Exception as e:  # noqa: BLE001
            QMessageBox.warning(self, "Stock", f"Could not read the stock file:\n{e}")
            return
        self._commit()
        self.items += loaded
        self._build()

    def _save(self) -> None:
        fn, _ = save_file(self, self.ctrl.settings, "stock", "Save stock", "stock.json", "Stock (*.json)")
        if fn:
            self._commit()
            save_stock(fn, self.items)

    def accept(self) -> None:
        self._commit()
        self.ctrl.set_stock(self.items)
        super().accept()


class RotationRuleEdit(QWidget):
    """How far a part may turn: a rule and, for some rules, an angle."""

    def __init__(self, rule: dict | None = None, inherit: bool = False, parent=None):
        super().__init__(parent)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        self.mode = QComboBox()
        if inherit:
            self.mode.addItem("Like its layer", "inherit")
        for k, label in ROTATION_MODES:
            self.mode.addItem(label, k)
        self.value = QDoubleSpinBox()
        self.value.setRange(0.0, 360.0)
        self.value.setDecimals(1)
        self.value.setSuffix("°")
        self.value.setValue(10.0)
        lay.addWidget(self.mode, 1)
        lay.addWidget(self.value)
        self.mode.currentIndexChanged.connect(self._enable)
        self.set_rule(rule or {"mode": "inherit" if inherit else "default"})

    def _enable(self) -> None:
        self.value.setEnabled(self.mode.currentData() in RULE_USES_VALUE)

    def set_rule(self, rule: dict) -> None:
        i = self.mode.findData(rule.get("mode", "default"))
        self.mode.setCurrentIndex(max(0, i))
        if rule.get("value"):
            self.value.setValue(float(rule["value"]))
        self._enable()

    def rule(self) -> dict:
        return {"mode": self.mode.currentData(), "value": self.value.value()}


class NewSheetsGroupDialog(QDialog):
    """After a nesting: the group the new sheets go into (the group of the sheet it started on first), a new group, or none."""

    def __init__(self, n: int, current: str, groups: list[str], parent=None, used: int = 0):
        super().__init__(parent)
        self.setWindowTitle("New sheets")
        form = QFormLayout(self)
        also = (f" A group chosen also takes the sheet{'s' if used != 1 else ''} the parts were nested on (the whole "
                "layout is in it)." if used else "")
        form.addRow(QLabel(f"The nesting made {n} new sheet{'s' if n != 1 else ''}. Put {'them' if n != 1 else 'it'} in a "
                           f"group?{also}"))
        self.box = QComboBox()
        if current:
            self.box.addItem(f"The group of this sheet: {current}", ("group", current))
        for g in groups:
            if g != current:
                self.box.addItem(f"The group {g}", ("group", g))
        self.box.addItem("A new group...", ("new", ""))
        self.box.addItem("No group", ("none", ""))
        self.name = QLineEdit()
        self.name.setPlaceholderText("Name of the new group")
        self.box.currentIndexChanged.connect(lambda *_: self.name.setEnabled(self.box.currentData()[0] == "new"))
        self.name.setEnabled(self.box.currentData()[0] == "new")
        form.addRow("Group", self.box)
        form.addRow("New group", self.name)
        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)
        form.addRow(bb)

    def choice(self) -> str:
        kind, name = self.box.currentData()
        if kind == "new":
            return self.name.text().strip() or "Nested"
        return name if kind == "group" else ""


class NestingDialog(RemembersSize, QDialog):
    def __init__(self, ctrl, parent=None):
        super().__init__(parent)
        from ..core.scope import groups_of_scope, scope_of
        from ..nesting.parts import group_parts
        self.ctrl = ctrl
        self.setWindowTitle("Nesting")
        objs = [o for o in (ctrl.selected_objects() or list(ctrl.sheet.vectors)) if ctrl.editable(o)]
        self.objs = objs
        self.parts = group_parts(objs)
        s = ctrl.sheet
        tool_d = max((t.tool.diameter for t in ctrl.project.toolpaths if t.op == "profile"), default=6.0)
        saved = ctrl.project.metadata.get("nesting", {})
        params = [
            Param("width", "Reserve / new sheet width", "length", s.width, min=1.0, group="Sheets",
                  help="The size of the sheets started when the plates of the Plates tab are full (or when there are none)"),
            Param("height", "Reserve / new sheet height", "length", s.height, min=1.0, group="Sheets"),
            Param("reserve_quantity", "Reserve sheets at most (0 = as many as needed)", "int", 0, min=0, max=10000,
                  group="Sheets", help="What does not fit when they are used up is reported"),
            Param("plate_order", "Order of the plates", "choice", "leftovers",
                  choices=[("leftovers", "Leftovers first, then the plates small to large"),
                           ("listed", "As listed in the Plates tab"),
                           ("largest", "The plates large to small"),
                           ("best", "Best order: the orders of the sizes are tried, the one using the least material is kept")],
                  group="Sheets", help="The reserve sheets are always used last"),
            Param("margin", "Border gap", "length", 10.0, min=0.0, group="Sheets"),
            Param("spacing", "Gap between parts", "length", tool_d + 2.0, min=0.0, group="Sheets",
                  help="Usually the cutter diameter plus a little clearance"),
            Param("min_leftover", "Leftover narrower than this is not used", "length", 150.0, min=0.0, group="Sheets",
                  help="On a sheet with parts on it and on leftover plates: the strips between the cuts already made and "
                       "the pockets the parts close in, narrower than this, are left alone (no part is put there). "
                       "0 = every free spot is used"),
            Param("target", "Place on", "choice", "current",
                  choices=[("current", "Current sheet first (around what stays on it), then the stock"),
                           ("all", "All sheets first (around what stays on them), then the stock")]
                  + [(f"group:{g}", f"The sheets of the group '{g}' first (around what stays on them), then the stock")
                     for g in groups_of_scope(ctrl.project, scope_of(ctrl.sheet))] + [("new", "The stock and new sheets only")],
                  group="Sheets"),
            Param("new_sheets", "Use reserve (new) sheets when the plates are full", "bool", True, group="Sheets"),
            Param("use_up", "Take the plates used out of the stock", "bool", True, group="Sheets",
                  help="Leftovers with a quantity are counted down, used up ones are removed"),
            Param("rotation", "Rotation", "choice", "90",
                  choices=[("0", "No rotation"), ("180", "0 / 180°"), ("90", "90° steps"),
                           ("45", "45° steps"), ("15", "15° steps"), ("custom", "Steps of my own..."),
                           ("list", "List of angles...")], group="Strategy"),
            Param("rotation_step", "Rotation step", "angle", 30.0, min=0.1, max=360.0, group="Strategy",
                  visible={"rotation": "custom"}, help="Every angle of this step around the circle is tried"),
            Param("angles", "Angles (degrees)", "str", "0, 45, 90, 135", group="Strategy",
                  visible={"rotation": "list"}, help="Any angles, separated by commas, e.g. 0, 22.5, 90"),
            Param("tilt_step", "Step of limited rotation", "angle", 5.0, min=0.1, max=90.0, group="Strategy",
                  help="The step of 'Tilt up to ±X°' set for parts or layers below"),
            Param("arrange", "Arrangement", "choice", "free",
                  choices=[("free", "Free (the parts interlock, least material)"),
                           ("rows", "Rows along X (lined up, a row at a time)"),
                           ("columns", "Columns along Y (lined up, a column at a time)")],
                  group="Strategy", help="Rows and columns put the parts in tidy strips of the sheet, from the corner "
                  "chosen below, each strip as thick as its thickest part; free nesting uses the least material"),
            Param("corner", "Start in the corner", "choice", "bl",
                  choices=[("bl", "Bottom left"), ("br", "Bottom right"), ("tl", "Top left"), ("tr", "Top right")],
                  group="Strategy", visible={"arrange": ["rows", "columns"]},
                  help="The first row (or column) starts here"),
            Param("direction", "Direction", "choice", DEFAULT_DIRECTION,
                  choices=[("along_x", "Along X (fill the width, the leftover is a strip on top)"),
                           ("along_y", "Along Y (fill the height, the leftover is on the right)"),
                           ("auto", "Automatic (try every direction, keep the least area)"),
                           ("compact", "Smallest area"), ("diag", "Lower left corner (diagonal)")],
                  group="Strategy", visible={"arrange": "free"},
                  help="Along X: the parts are put across the sheet first and the layout grows upwards, so what is "
                  "left is one strip across the whole width. A direction chosen is kept; Automatic tries them all"),
            Param("iterations", "Optimisation attempts", "int", 30, min=1, max=200, group="Strategy",
                  help="1: one quick pass. More: other orders of the parts and other directions are tried, "
                       "the best layout (fewest sheets, least area used on the last) is kept and polished"),
            Param("priority", "Processor use", "choice", "low", group="Strategy",
                  choices=[("low", "Every processor, low priority (the computer stays smooth)"),
                           ("normal", "Every processor, normal priority"),
                           ("high", "Every processor, high priority (the fastest; other programs slow down)")],
                  help="The nesting uses every processor of the computer. Low priority: what you do meanwhile (other "
                       "sheets, other programs) comes first. High: the nesting comes first and ends soonest."),
            Param("time_limit", "Time limit of the search (seconds)", "int", 120, min=0, max=3600, group="Strategy",
                  help="No further layout is tried after this; the best so far is kept (a small job is done long "
                       "before: the search stops when nothing better comes). Hundreds of curved parts use less "
                       "material with more time. 0 = no limit"),
            Param("group_inner", "Inner pieces (göbek) stay with their part", "bool", False, group="Parts",
                  help="On: whatever lies inside a part (the pieces in its holes too) moves with it. "
                       "Off: pieces in holes are parts of their own and are nested separately"),
            Param("fill_holes", "Place smaller parts in the holes of parts", "bool", False, group="Parts",
                  help="Holes that hold nothing that stays can take other parts. Not for holes that "
                       "are only drawn (engraved), as their material is not cut away"),
            Param("tolerance", "Outline tolerance", "length", 0.5, min=0.01, group="Strategy",
                  help="Outlines are simplified this much; parts may end up this much further apart"),
            Param("max_points", "Outline points per part", "int", 300, min=0, max=5000, group="Strategy",
                  help="A part with more points is simplified further, which makes the nesting much faster "
                       "(complex outlines: seconds instead of minutes) and leaves it a little further from its "
                       "neighbours. 0 = no limit"),
            Param("squeeze", "Squeeze parts in", "choice", "off",
                  choices=[("off", "Off"), ("ask", "Suggest it, I decide"), ("auto", "Squeeze in automatically")],
                  group="Squeezing in",
                  help="When parts do not fit, find out whether tilting some a little or letting them come "
                       "closer to the edge than the border gap places them (or saves a sheet)"),
            Param("squeeze_angle", "Tilt by up to", "angle", 5.0, min=0.0, max=90.0, group="Squeezing in",
                  visible={"squeeze": ["ask", "auto"]}, help="Off their angles; 0 = never tilt"),
            Param("squeeze_step", "Tilt step", "angle", 1.0, min=0.1, max=45.0, group="Squeezing in",
                  visible={"squeeze": ["ask", "auto"]}),
            Param("squeeze_margin", "Closer to the edge by up to", "length", 3.0, min=0.0, group="Squeezing in",
                  visible={"squeeze": ["ask", "auto"]}, help="What parts may take from the border gap; 0 = never"),
        ]
        lay = QVBoxLayout(self)
        tabs = QTabWidget()
        lay.addWidget(tabs, 1)
        page1, page2, page3 = QWidget(), QWidget(), QWidget()
        l1, l2, l3 = QVBoxLayout(page1), QVBoxLayout(page2), QVBoxLayout(page3)
        self.l3 = l3
        presets = QComboBox()
        presets.addItem("Preset sheet sizes...")
        for name, w, h in ctrl.sheet_sizes():
            presets.addItem(name, (w, h))
        l1.addWidget(presets)
        # everything is remembered but the sheet size, which is the current sheet's unless it is changed here
        self.memo = [p for p in params if p.name not in ("width", "height")]
        mem = ctrl.settings.recall("nesting")
        if "direction" not in mem and ("gravity" in mem or "gravity_only" in mem):   # (stored before Direction)
            mem = dict(mem, direction=direction_of(mem.get("gravity"), mem.get("gravity_only")))
            if mem.get("iterations") == 8:          # (the old default: the new one finds tighter layouts)
                mem["iterations"] = 30
            ctrl.settings.remember("nesting", mem, save=False)
        old = {k: v for k, v in (("max_points", 300), ("time_limit", 120)) if mem.get(k) == {"max_points": 40, "time_limit": 30}[k]}
        if old:                                      # (the old defaults: 40 points made curved parts up to 6 mm fatter on
            ctrl.settings.remember("nesting", dict(mem, **old), save=False)   # every side; 30 s is short for big jobs)
        self.form = ParamForm(params, ctrl.settings.recall("nesting", self.memo), ctrl.units)
        self._grouped = False
        self._memory: dict = {"qty": {}, "rules": {}, "layer_rules": {}}   # what was set for parts that left the table
        self.form.changed.connect(self._form_changed)
        presets.activated.connect(lambda i: self._preset(presets.itemData(i)))
        form_scroll = scroll(self.form)
        form_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        l1.addWidget(form_scroll, 1)
        self.stock_holder = QVBoxLayout()
        l2.addLayout(self.stock_holder)
        self.stock_table: StockTable | None = None
        self._build_stock()
        row = QHBoxLayout()
        for text, tip, slot in (("Add plate...", "A sheet size with its quantity, or a circle, ellipse, polygon, L shape",
                                 self._add_plate),
                                ("Up", "Use the selected plate earlier", lambda: self._move_plate(-1)),
                                ("Down", "Use the selected plate later", lambda: self._move_plate(1)),
                                ("Manage stock...", "Leftovers drawn as vectors, the leftover of a cut sheet, "
                                 "stock files", self._manage_stock)):
            b = QPushButton(text)
            b.setToolTip(tip)
            b.clicked.connect(slot)
            row.addWidget(b)
        l2.addLayout(row)
        l2.addStretch(1)
        self._build_parts_page(saved)
        for page, title in ((page1, "Settings"), (page2, "Plates"), (page3, "Parts and rotation")):
            tabs.addTab(page, title)
        self.tabs = tabs
        lay.addWidget(buttons(self, "Nest"))
        self.keep_size(ctrl, "nesting", 700, 760)
        self._form_changed()   # remembered: the inner pieces stay with their part

    def _build_parts_page(self, saved: dict, prev: dict | None = None) -> None:
        """The table of the parts (quantities, rotation rules); ``prev`` carries over what was set for the
        parts that are still there when the grouping of the parts changes."""
        l3 = self.l3
        prev = prev or {}
        # how far parts may turn: by layer and by part
        layer_ids = []
        for p in self.parts:
            if p.root.layer_id not in layer_ids:
                layer_ids.append(p.root.layer_id)
        l3.addWidget(QLabel("<b>Rotation by layer</b> (parts follow their layer unless set below):"))
        self.layer_table = QTableWidget(len(layer_ids), 2)
        self.layer_table.setHorizontalHeaderLabels(["Layer", "Rotation"])
        self.layer_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        self.layer_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeToContents)
        self.layer_table.setMaximumHeight(130)
        self.layer_ids = layer_ids
        self.layer_rules: list[RotationRuleEdit] = []
        for i, lid in enumerate(layer_ids):
            layer = self.ctrl.project.layer(lid)
            self.layer_table.setItem(i, 0, QTableWidgetItem(layer.name if layer else "?"))
            ed = RotationRuleEdit(prev.get("layer_rules", {}).get(lid) or saved.get("layer_rules", {}).get(lid))
            self.layer_table.setCellWidget(i, 1, ed)
            self.layer_rules.append(ed)
        l3.addWidget(self.layer_table)
        l3.addWidget(QLabel(f"<b>{len(self.parts)}</b> part(s) found. Set the quantities and how far each may "
                            "turn (select several rows to set them together):"))
        self.table = QTableWidget(len(self.parts), 4)
        self.table.setHorizontalHeaderLabels(["Part", "Size", "Quantity", "Rotation"])
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        self.table.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeToContents)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.ExtendedSelection)
        u = self.ctrl.units
        self.qty: list[QSpinBox] = []
        self.rules: list[RotationRuleEdit] = []
        for i, p in enumerate(self.parts):
            bb = p.root.bbox()
            name = p.root.name or f"Part {i + 1}"
            if p.members:
                name += f" (+{len(p.members)})"
            item = QTableWidgetItem(name)
            item.setFlags(item.flags() & ~Qt.ItemIsEditable)
            self.table.setItem(i, 0, item)
            self.table.setItem(i, 1, QTableWidgetItem(f"{u.fmt(bb.width)} x {u.fmt(bb.height)}"))
            sp = QSpinBox()
            sp.setRange(0, 10000)
            sp.setValue(prev.get("qty", {}).get(p.root.id, saved.get("quantities", {}).get(p.root.id, 1)))
            self.table.setCellWidget(i, 2, sp)
            self.qty.append(sp)
            ed = RotationRuleEdit(prev.get("rules", {}).get(p.root.id) or saved.get("part_rules", {}).get(p.root.id),
                                  inherit=True)
            self.table.setCellWidget(i, 3, ed)
            self.rules.append(ed)
        l3.addWidget(self.table, 1)
        bulk = QHBoxLayout()
        bulk.addWidget(QLabel("Selected parts:"))
        self.bulk = RotationRuleEdit(None, inherit=True)
        bulk.addWidget(self.bulk, 1)
        apply = QPushButton("Apply")
        apply.setToolTip("Set the rotation of the selected rows")
        apply.clicked.connect(self._apply_bulk)
        bulk.addWidget(apply)
        l3.addLayout(bulk)

    def _form_changed(self) -> None:
        grouped = bool(self.form.values().get("group_inner"))
        if grouped != self._grouped:
            self._grouped = grouped
            self._regroup()

    def _regroup(self) -> None:
        """The grouping of inner pieces changed: the parts are found again, what was set for them is kept."""
        from ..nesting.parts import group_parts
        mem = self._memory
        mem["qty"].update({p.root.id: sp.value() for p, sp in zip(self.parts, self.qty)})
        mem["rules"].update({p.root.id: ed.rule() for p, ed in zip(self.parts, self.rules)
                             if ed.rule()["mode"] != "inherit"})
        mem["layer_rules"].update({lid: ed.rule() for lid, ed in zip(self.layer_ids, self.layer_rules)})
        prev = mem
        self.parts = group_parts(self.objs, group_inner=bool(self.form.values().get("group_inner")))
        while self.l3.count():
            item = self.l3.takeAt(0)
            if item.widget() is not None:
                item.widget().deleteLater()
            elif item.layout() is not None:
                while item.layout().count():
                    sub = item.layout().takeAt(0)
                    if sub.widget() is not None:
                        sub.widget().deleteLater()
        self._build_parts_page(self.ctrl.project.metadata.get("nesting", {}), prev)

    # -- stock ---------------------------------------------------------------------------
    def _build_stock(self, items: list[StockItem] | None = None, used: list[bool] | None = None) -> None:
        if self.stock_table is not None:
            self.stock_holder.removeWidget(self.stock_table)
            self.stock_table.deleteLater()
        stock = list(self.ctrl.project.stock) if items is None else items
        self.stock_table = StockTable(self.ctrl, stock, checkable=True)
        if used:
            self.stock_table.set_used(used)
        label = "Plates (used in this order with 'As listed'; leftovers first by default; the reserve sheets last):" if stock else \
            "No plates yet: add sheet sizes with their quantities, circles or leftovers of any shape below."
        if getattr(self, "stock_label", None) is None:
            self.stock_label = QLabel()
            self.stock_holder.addWidget(self.stock_label)
        self.stock_label.setText(label)
        self.stock_holder.addWidget(self.stock_table)

    def _manage_stock(self) -> None:
        if StockDialog(self.ctrl, self).exec():
            self._build_stock()

    def _add_plate(self) -> None:
        """A plate size (any shape) with its quantity: it goes into the stock of the project, at the end of the list."""
        dlg = PlateDialog(self.ctrl, self)
        if dlg.exec():
            table = self.stock_table
            items = table.edited_items() + [dlg.item()]
            used = [table.used(r) for r in range(len(table.items))] + [True]
            self.ctrl.set_stock(items)
            self._build_stock(items, used)

    def _move_plate(self, delta: int) -> None:
        """Move the selected plate up or down the list: the order they are used in."""
        table = self.stock_table
        rows = sorted({i.row() for i in table.selectedIndexes()})
        if len(rows) != 1:
            return
        r, to = rows[0], rows[0] + delta
        if not 0 <= to < len(table.items):
            return
        items = table.edited_items()
        used = [table.used(k) for k in range(len(items))]
        items[r], items[to] = items[to], items[r]
        used[r], used[to] = used[to], used[r]
        self.ctrl.set_stock(items)
        self._build_stock(items, used)
        self.stock_table.selectRow(to)

    def _preset(self, wh) -> None:
        if wh:
            u = self.ctrl.units
            self.form.widgets["width"].setValue(u.to_display(wh[0]))
            self.form.widgets["height"].setValue(u.to_display(wh[1]))

    def _apply_bulk(self) -> None:
        rule = self.bulk.rule()
        for idx in self.table.selectionModel().selectedRows():
            self.rules[idx.row()].set_rule(rule)

    # -- running -------------------------------------------------------------------------
    def accept(self) -> None:
        v = self.form.values()
        if v["rotation"] == "list":
            try:
                parse_angles(v["angles"])
            except ValueError:
                QMessageBox.warning(self, "Rotation", "The angles must be numbers separated by commas, "
                                    "for example: 0, 22.5, 90")
                return
        super().accept()

    def rotations(self, v: dict) -> list[float]:
        """The angles every part may take unless a rule says otherwise."""
        rot = v["rotation"]
        if rot == "list":
            return parse_angles(v["angles"])
        step = v["rotation_step"] if rot == "custom" else float(rot)
        return [0.0] if step <= 0 else [round(k * step, 6) for k in range(max(1, int(round(360 / step))))]

    def _suggestion_text(self, strict, better) -> str:
        gain = []
        placed = len(strict.unplaced) - len(better.unplaced)
        if placed > 0:
            gain.append(f"place {placed} part(s) that do not fit")
        if better.sheets < strict.sheets:
            gain.append(f"save {strict.sheets - better.sheets} plate(s)")
        head = "Squeezing parts in would " + " and ".join(gain or ["improve the layout"]) + ":"
        return "\n".join([head] + squeeze_lines(self.parts, better.squeezed)[:14])

    def _ask_squeeze(self, text: str) -> bool:
        box = QMessageBox(self)
        box.setWindowTitle("Squeeze parts in?")
        box.setText(text)
        yes = box.addButton("Squeeze them in", QMessageBox.AcceptRole)
        box.addButton("Keep it as it is", QMessageBox.RejectRole)
        box.exec()
        return box.clickedButton() is yes

    def run(self):
        """Nest and wait (a progress window); the outcome, or ``None`` when cancelled (nothing is changed then)."""
        try:
            prep = self.prepare()
        except clip.TooComplex as exc:    # (copies piled on one spot, on the plate or among the parts)
            QMessageBox.warning(self, "Nesting", f"This cannot be nested:\n{exc}")
            return None
        prog = QProgressDialog("Nesting parts...", "Cancel", 0, 100, self)
        prog.setWindowModality(Qt.WindowModal)
        prog.setMinimumDuration(300)
        prog.setAutoClose(False)
        prog.setAutoReset(False)
        state = prep["state"]
        try:
            # the long part runs in a thread: the program stays alive and Cancel works at once
            res = run_in_thread(prep["compute"], prog, state)
        except NestCancelled:
            return None
        except clip.TooComplex as exc:    # (copies piled on one spot, on the plate or among the parts)
            QMessageBox.warning(self, "Nesting", f"This cannot be nested:\n{exc}")
            return None
        finally:
            prog.close()
        return self.finish(prep, res)

    def start(self, done, show_progress=None):
        """Nest in the background: the program stays usable (other tabs, other sheets) while every processor works.
        ``done(out)`` is called here (in the interface) when it is over: the outcome, or ``None`` (cancelled, failed,
        or the vectors were changed meanwhile: nothing is changed then). ``show_progress(k, n)`` is called as it goes.
        Returns the job: ``job.cancel()`` stops it."""
        prep = self.prepare()
        return NestJob(self, prep, done, show_progress)

    def prepare(self) -> dict:
        """Everything a nesting needs, read from the window and the project now (in the interface)."""
        from ..core.scope import sheets_with
        v = self.form.values()
        squeezing = v["squeeze"] != "off"
        gravity, only = DIRECTIONS.get(v["direction"], DIRECTIONS[DEFAULT_DIRECTION])
        st = NestSettings(v["width"], v["height"], v["margin"], v["spacing"], self.rotations(v), gravity,
                          iterations=v["iterations"], time_limit=float(v["time_limit"]), tolerance=v["tolerance"],
                          max_points=v["max_points"], gravity_only=only, arrange=v["arrange"],
                          corner=v["corner"], stock_order=v["plate_order"], reserve_quantity=int(v["reserve_quantity"]),
                          squeeze_angle=v["squeeze_angle"] if squeezing else 0.0,
                          squeeze_margin=v["squeeze_margin"] if squeezing else 0.0,
                          squeeze_step=v["squeeze_step"], min_leftover=v["min_leftover"],
                          priority=v.get("priority", "low"))
        quantities = {p.root.id: sp.value() for p, sp in zip(self.parts, self.qty)}
        part_rules = {p.root.id: ed.rule() for p, ed in zip(self.parts, self.rules)
                      if ed.rule()["mode"] != "inherit"}
        layer_rules = {lid: ed.rule() for lid, ed in zip(self.layer_ids, self.layer_rules)
                       if ed.rule()["mode"] != "default"}
        state = {"k": 0, "n": 1, "cancel": threading.Event()}

        def progress(k, n):
            state["k"], state["n"] = k, n

        ctrl = self.ctrl
        table = self.stock_table
        stock = [table.item(r) for r in range(len(table.items)) if table.used(r)]
        target = v["target"]
        use_sheets = ([ctrl.sheet] if target == "current" else sheets_with(ctrl.project, ctrl.sheet) if target == "all"
                      else ctrl.sheets_in(target[6:]) if target.startswith("group:") else [])
        # the rules are kept with the project, and stay when the nesting is undone
        ctrl.project.metadata["nesting"] = {"part_rules": part_rules, "layer_rules": layer_rules,
                                            "quantities": {k: n for k, n in quantities.items() if n != 1}}
        ctrl.settings.remember("nesting", v, self.memo)
        moving = {o.id for p in self.parts for o in p.objects}
        sheet = ctrl.sheet
        blank = target == "current" and not sheet.outline and all(o.id in moving for o in sheet.vectors)
        job = prepare_nesting(ctrl.project, self.objs, st, quantities, [], stock, use_sheets, v["new_sheets"],
                              part_rules, layer_rules, v["tilt_step"], v["group_inner"], v["fill_holes"])
        return {"v": v, "job": job, "state": state, "sheet": sheet, "blank": blank, "squeezing": squeezing,
                "use_sheets": list(use_sheets), "signature": nest_signature(self.objs, use_sheets),
                "compute": lambda: run_nesting(job, progress, state["cancel"].is_set)}

    def unchanged(self, prep) -> bool:
        """Whether the vectors nested and the sheets nested onto are as they were when the nesting started (they may
        have been changed meanwhile: the nesting ran in the background)."""
        live = {o.id: o for s in self.ctrl.project.sheets for o in s.vectors}
        if any(o.id not in live or live[o.id] is not o for o in self.objs):
            return False
        sheets = {s.id for s in self.ctrl.project.sheets}
        if any(s.id not in sheets for s in prep["use_sheets"]):
            return False
        return nest_signature(self.objs, prep["use_sheets"]) == prep["signature"]

    def finish(self, prep, res):
        """The layout put into the project (one undo step); the outcome."""
        v, job, ctrl, sheet = prep["v"], prep["job"], self.ctrl, prep["sheet"]

        def choose(strict, better) -> bool:
            return v["squeeze"] == "auto" or self._ask_squeeze(self._suggestion_text(strict, better))
        ctrl.checkpoint("Nesting")
        try:
            if prep["blank"]:
                sheet.width, sheet.height = v["width"], v["height"]  # a blank sheet takes the size asked for
                job.settings.sheet_width, job.settings.sheet_height = v["width"], v["height"]
            out = apply_nesting(job, res, choose if prep["squeezing"] else None)
            if v["use_up"]:
                self._use_up(out)
        except Exception:
            ctrl.undo_stack.rollback(ctrl.project)
            raise
        ctrl.notify()
        if out.sheets and prep.get("show", True):
            ctrl.set_sheet(out.sheets[0].id)
        return out

    def _use_up(self, out) -> None:
        """Count the plates used down in the project's stock; used up ones leave it."""
        self.ctrl.project.stock = use_up(self.ctrl.project.stock, out.plates)


# ---------------------------------------------------------------------------
# G-code export
# ---------------------------------------------------------------------------

def zero_warning(project) -> str:
    """What Save G-code says when Z zero is the table: the Z of the file is measured from the board under the material, and
    the passes (and the heights over the material) come from the thickness that is set, which nobody may have measured."""
    m = project.material
    if m.z_zero != "bottom":
        return ""
    th = sorted({round(project.sheet_thickness(s), 3) for s in project.sheets} or {round(m.thickness, 3)})
    used = ", ".join(f"{t:g}" for t in th)
    return ("⚠ Z zero is the table (the board under the material): Z0 of the G-code is the top of that board, a through cut "
            f"ends just under it. The thickness used for the passes and the heights: {used} mm. Check it is the thickness of "
            "the material on the machine (Material Setup).")


class ExportDialog(RemembersSize, QDialog):
    def __init__(self, ctrl, parent=None, only: set[str] | None = None, group: str = ""):
        """``only``: the toolpaths that are ticked (otherwise the ones that are switched on); ``group``: the name
        that is offered for the file."""
        super().__init__(parent)
        self.ctrl = ctrl
        self.group = group
        self.setWindowTitle("Save G-code")
        lay = QVBoxLayout(self)
        form = QFormLayout()
        self.post = QComboBox()
        self._fill_posts(ctrl.settings.last_post)
        self.post.currentIndexChanged.connect(self.preview)
        post_row = QHBoxLayout()
        post_row.addWidget(self.post, 1)
        edit_post = QPushButton("Edit post...")
        edit_post.setToolTip("Change this post-processor or make your own from it: a field for every option, the "
                             "blocks of text, and a preview")
        edit_post.clicked.connect(self._edit_post)
        post_row.addWidget(edit_post)
        form.addRow("Post-processor", post_row)
        from ..core.scope import scope_of
        side = scope_of(ctrl.sheet)
        self.sheets = QComboBox()
        self.sheets.addItem("Current sheet", "current")
        self.sheets.addItem(f"All sheets of the isolated group {side} (one file per sheet)" if side else
                            "All sheets (one file per sheet; isolated groups are saved from their own sheets)", "all")
        self.sheets.currentIndexChanged.connect(lambda *_: self._name_changed())
        form.addRow("Sheets", self.sheets)
        self.per_toolpath = QCheckBox("One file per toolpath (all in the folder)")
        self.per_toolpath.setToolTip("Every ticked toolpath in a G-code file of its own, named after the toolpath (unless the "
                                     "name already has {toolpath_name} or {segments})")
        form.addRow("", self.per_toolpath)
        tiling = QHBoxLayout()
        self.tile = QCheckBox("Tile the job")
        self.tile.setToolTip("Machine a sheet longer than the machine in bands, one file per band")
        self.tile_axis = QComboBox()
        self.tile_axis.addItem("along Y", "y")
        self.tile_axis.addItem("along X", "x")
        self.tile_len = QDoubleSpinBox()
        self.tile_len.setRange(1.0, 1e6)
        self.tile_len.setDecimals(ctrl.units.decimals)
        self.tile_len.setSuffix(ctrl.units.suffix)
        self.tile_len.setValue(ctrl.units.to_display(600.0))
        for w in (self.tile, self.tile_axis, self.tile_len):
            tiling.addWidget(w)
        for sig in (self.tile.toggled, self.tile_axis.currentIndexChanged, self.tile_len.valueChanged):
            sig.connect(self.preview)
        form.addRow("Tiling", tiling)
        self.heights = QLabel()
        self.heights.setWordWrap(True)
        row = QHBoxLayout()
        row.addWidget(self.heights, 1)
        setup = QPushButton("Material Setup...")
        setup.setToolTip("Change the safe Z, the home position and the other heights, then see the G-code again")
        setup.clicked.connect(self._material_setup)
        row.addWidget(setup)
        form.addRow("Material", row)
        self.zero_warning = QLabel()
        self.zero_warning.setWordWrap(True)
        self.zero_warning.setStyleSheet("background:#fff4ce; color:#7a4d00; border:1px solid #f0c36d; border-radius:4px; "
                                        "padding:6px")
        form.addRow(self.zero_warning)
        lay.addLayout(form)
        mem = ctrl.settings.recall("export_gcode")   # the options of the last export
        for w in (self.sheets, self.tile, self.tile_axis, self.tile_len):
            w.blockSignals(True)   # the preview is made once, below
        for combo, key in ((self.sheets, "sheets"), (self.tile_axis, "tile_axis")):
            i = combo.findData(mem.get(key))
            if i >= 0:
                combo.setCurrentIndex(i)
        if isinstance(mem.get("tile"), bool):
            self.tile.setChecked(mem["tile"])
        if isinstance(mem.get("per_toolpath"), bool):
            self.per_toolpath.setChecked(mem["per_toolpath"])
        if isinstance(mem.get("tile_len"), (int, float)) and not isinstance(mem.get("tile_len"), bool) \
                and mem["tile_len"] >= 1.0:
            self.tile_len.setValue(ctrl.units.to_display(float(mem["tile_len"])))
        for w in (self.sheets, self.tile, self.tile_axis, self.tile_len):
            w.blockSignals(False)
        self.desc = QLabel()
        self.desc.setWordWrap(True)
        lay.addWidget(self.desc)
        lay.addWidget(self._name_box(mem))
        lay.addWidget(self._where_box(mem))
        lay.addWidget(QLabel("Toolpaths in this file, in machining order (▲ ▼ or drag to change it: the project's order "
                             "changes too, and the name follows):"))
        pick = QHBoxLayout()
        self.list = QListWidget()
        self.list.setDragDropMode(QListWidget.InternalMove)          # (drag a toolpath to where it is to be cut)
        self.list.setDefaultDropAction(Qt.MoveAction)
        from ..core.scope import toolpath_in_scope
        for t in ctrl.project.toolpaths:
            if not toolpath_in_scope(ctrl.project, t, side):
                continue                                     # (another side of the project: not saved from here)
            it = QListWidgetItem(f"{t.name}  —  T{t.tool.number} {t.tool.label}" + (f"   [{t.group}]" if t.group else ""))
            it.setData(Qt.UserRole, t.id)
            it.setFlags(it.flags() | Qt.ItemIsUserCheckable)
            on = only is not None and t.id in only          # (from the menu nothing is ticked: tick what is to be saved)
            it.setCheckState(Qt.Checked if on else Qt.Unchecked)
            self.list.addItem(it)
        self.list.itemChanged.connect(self.preview)
        self.list.model().rowsMoved.connect(lambda *_: QTimer.singleShot(0, self._order_changed))
        self.list.setMaximumHeight(160)
        for text, fn, tip in (("All", lambda: self._tick(lambda t: True), "Tick every toolpath"),
                              ("This sheet", self.tick_sheet, "Tick the toolpaths of the sheet shown (those calculated on it)"),
                              ("None", lambda: self._tick(lambda t: False), "Tick none")):
            b = QPushButton(text)
            b.setToolTip(tip)
            b.clicked.connect(fn)
            pick.addWidget(b)
        for text, delta, tip in (("▲", -1, "Cut the selected toolpath earlier"), ("▼", 1, "Cut the selected toolpath later")):
            b = QPushButton(text)
            b.setToolTip(tip)
            b.setFixedWidth(34)
            b.clicked.connect(lambda _c=False, d=delta: self.move_selected(d))
            pick.addWidget(b)
        self.by_group = QComboBox()
        self.by_group.addItem("Only the group...", None)
        for g in ctrl.groups():
            self.by_group.addItem(g, g)
        self.by_group.setVisible(self.by_group.count() > 1)
        self.by_group.activated.connect(lambda *_: self._tick(lambda t: t.group == self.by_group.currentData())
                                        if self.by_group.currentData() else None)
        pick.addWidget(self.by_group)
        pick.addStretch(1)
        lay.addLayout(pick)
        lay.addWidget(self.list)
        self.view = ExplainedGcode()          # (click a line to read what it says: G2 X20 Y0 I5 J0 ...)
        self.text = self.view.text
        lay.addWidget(self.view, 1)
        bb = QDialogButtonBox()
        save = bb.addButton("Save", QDialogButtonBox.AcceptRole)       # (nothing is written before this is pressed)
        save.clicked.connect(self.save)
        self.save_button = save
        cp = bb.addButton("Copy", QDialogButtonBox.ActionRole)
        cp.clicked.connect(lambda: QApplication.clipboard().setText(self.text.toPlainText()))
        bb.addButton(QDialogButtonBox.Close).clicked.connect(self.reject)
        lay.addWidget(bb)
        self.keep_size(ctrl, "export", 640, 780)
        self.list.itemChanged.connect(self._name_changed)
        self.post.currentIndexChanged.connect(self._name_changed)
        self._name_changed()
        self.preview()

    # -- the name of the files: prefixes with variables, like the blocks of a post ----------------------------
    def _name_box(self, mem: dict) -> QGroupBox:
        from ..post import naming
        box = QGroupBox("File name")
        lay = QVBoxLayout(box)
        self._name_rows = QVBoxLayout()
        lay.addLayout(self._name_rows)
        self._active = None
        parts = mem.get("name_parts") if isinstance(mem.get("name_parts"), list) else None
        try:
            parts, sep = naming.check(parts, mem.get("name_sep", naming.DEFAULT_SEP))
        except naming.NamingError:
            parts, sep = list(naming.DEFAULT_PARTS), naming.DEFAULT_SEP
        for text in parts:
            self._add_prefix(text)
        row = QHBoxLayout()
        add = QPushButton("＋ Add a prefix")
        add.clicked.connect(lambda: self._add_prefix("", focus=True))
        self.name_var = QComboBox()
        self.name_var.addItem("Insert a variable...", None)
        for v in naming.VARIABLES:
            self.name_var.addItem(f"{{{v['name']}}}  {v['text']['en']}", v["name"])
        self.name_var.activated.connect(self._insert_var)
        self.name_sep = QLineEdit(sep)
        self.name_sep.setMaxLength(4)
        self.name_sep.setMaximumWidth(48)
        self.name_sep.setToolTip("Put between the prefixes")
        self.name_sep.textChanged.connect(self._name_changed)
        formats = QPushButton("Formats...")
        formats.setToolTip("What can be written in a {variable}: 3 digits with zeros in front ({revision:03}), decimals, sums")
        formats.clicked.connect(self._show_formats)
        for w in (add, self.name_var, formats):
            row.addWidget(w)
        row.addWidget(QLabel("between:"))
        row.addWidget(self.name_sep)
        row.addStretch(1)
        lay.addLayout(row)
        # {segments}: a part of the name for every toolpath of the file, a group stacked into one
        row = QHBoxLayout()
        self.segment = QLineEdit(mem.get("segment") if isinstance(mem.get("segment"), str) and mem.get("segment")
                                 else naming.DEFAULT_SEGMENT)
        self.segment.setToolTip("The part of every toolpath in {segments}. A piece in [brackets] is left out when a variable "
                                "in it has no value: [s{step}] only with more than one pass, [m{count}] only for a stack.")
        self.segment.textChanged.connect(self._name_changed)
        self.segment_var = QComboBox()
        self.segment_var.addItem("Insert...", None)
        for v in naming.SEGMENT_VARIABLES:
            self.segment_var.addItem(f"{{{v['name']}}}  {v['text']['en']}", v["name"])
        self.segment_var.activated.connect(self._insert_segment_var)
        self.segment_sep = QLineEdit(mem.get("segment_sep") if isinstance(mem.get("segment_sep"), str)
                                     else naming.DEFAULT_SEGMENT_SEP)
        self.segment_sep.setMaxLength(4)
        self.segment_sep.setMaximumWidth(48)
        self.segment_sep.setToolTip("Put between the parts of the toolpaths")
        self.segment_sep.textChanged.connect(self._name_changed)
        row.addWidget(QLabel("Part of a toolpath ({segments}):"))
        row.addWidget(self.segment, 1)
        row.addWidget(self.segment_var)
        row.addWidget(QLabel("between:"))
        row.addWidget(self.segment_sep)
        lay.addLayout(row)
        row = QHBoxLayout()
        self.together = QCheckBox("Cut the toolpaths of a group one after the other")
        self.together.setToolTip("The toolpaths of a group are cut together, where the first of them is (the order of the "
                                 "file); off: in the order of the Toolpaths panel")
        self.together.setChecked(mem.get("together", True) is not False)
        self.stack = QCheckBox("Group names by order (m = how many)")
        self.stack.setToolTip("Toolpaths one after the other with the same settings (name without digits, side, depths, "
                              "allowance, safe Z, tool) are one part of {segments}, with their number: i_oyma2z10m3")
        self.stack.setChecked(mem.get("stack", True) is not False)
        for w in (self.together, self.stack):
            w.toggled.connect(self._name_changed)
            row.addWidget(w)
        row.addStretch(1)
        lay.addLayout(row)
        self.together.toggled.connect(self.preview)
        self.name_preview = QLabel()
        self.name_preview.setWordWrap(True)
        self.name_preview.setTextInteractionFlags(Qt.TextSelectableByMouse)
        lay.addWidget(self.name_preview)
        return box

    # -- where the files go, and the revision --------------------------------------------------------------------
    def _where_box(self, mem: dict) -> QGroupBox:
        box = QGroupBox("Save to")
        lay = QVBoxLayout(box)
        row = QHBoxLayout()
        last = self.ctrl.settings.last_dir("gcode")
        self.folder = QLineEdit(mem.get("folder") if isinstance(mem.get("folder"), str) and mem.get("folder") else last)
        self.folder.setPlaceholderText("Choose the folder first, then press Save")
        browse = QPushButton("Folder...")
        browse.clicked.connect(self._pick_folder)
        row.addWidget(self.folder, 1)
        row.addWidget(browse)
        lay.addLayout(row)
        row = QHBoxLayout()
        self.subfolder = QLineEdit(mem.get("subfolder") if isinstance(mem.get("subfolder"), str) else "")
        self.subfolder.setPlaceholderText("a folder inside it, with {variables}, like Rev{revision} (empty: none)")
        self.subfolder.textChanged.connect(self._name_changed)
        row.addWidget(QLabel("Folder inside:"))
        row.addWidget(self.subfolder, 1)
        lay.addLayout(row)
        row = QHBoxLayout()
        self.revision = QSpinBox()
        self.revision.setRange(0, 99999)
        self.revision.setToolTip("The revision number: it counts up by itself after every save, and can be typed")
        self.rev_scope = QComboBox()
        self.rev_scope.addItem("of the project", "project")
        self.rev_scope.addItem("of the sheet group (each group counts for itself)", "group")
        self.rev_scope.addItem("of the sheet (each sheet counts for itself)", "sheet")
        i = self.rev_scope.findData(mem.get("rev_scope"))
        self.rev_scope.setCurrentIndex(max(0, i))
        self.rev_auto = QCheckBox("count up after saving")
        self.rev_auto.setChecked(mem.get("rev_auto") is not False)
        self.revision.setValue(self._next_revision())
        self.revision.valueChanged.connect(self._name_changed)
        self.rev_scope.currentIndexChanged.connect(lambda *_: (self.revision.setValue(self._next_revision()), self._name_changed()))
        self.rev_sheets = QPushButton("Sheets...")
        self.rev_sheets.setToolTip("Set the revision number of every sheet")
        self.rev_sheets.clicked.connect(self._sheet_revisions)
        self.rev_sheets.setVisible(self.rev_scope.currentData() == "sheet")
        self.rev_scope.currentIndexChanged.connect(lambda *_: self.rev_sheets.setVisible(self.rev_scope.currentData() == "sheet"))
        for w in (QLabel("Revision"), self.revision, self.rev_scope, self.rev_sheets, self.rev_auto):
            row.addWidget(w)
        row.addStretch(1)
        lay.addLayout(row)
        self.last_saved = QLabel()
        self.last_saved.setWordWrap(True)
        self.last_saved.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self._show_last(mem.get("last"))
        lay.addWidget(self.last_saved)
        return box

    def _revisions(self) -> dict:
        r = self.ctrl.project.metadata.setdefault("revisions", {"project": 1, "groups": {}})
        r.setdefault("groups", {})
        r.setdefault("sheets", {})
        return r

    def _next_revision(self) -> int:
        r = self._revisions()
        sh = self.ctrl.sheet
        if self.rev_scope.currentData() == "sheet":
            return int(r["sheets"].get(sh.id, r.get("project", 1)))
        if self.rev_scope.currentData() == "group" and sh.group:
            return int(r["groups"].get(sh.group, r.get("project", 1)))
        return int(r.get("project", 1))

    def sheet_revisions(self) -> dict[str, int]:
        """The revision of every sheet (the open sheet: the number in the box)."""
        return {sh.id: (self.revision.value() if sh.id == self.ctrl.sheet_id else
                        int(self._revisions()["sheets"].get(sh.id, self._revisions().get("project", 1))))
                for sh in self.ctrl.project.sheets}

    def set_sheet_revisions(self, revs: dict[str, int]) -> None:
        r = self._revisions()
        for sid, v in revs.items():
            r["sheets"][sid] = int(v)
        self.ctrl.dirty = True
        if self.ctrl.sheet_id in revs:
            self.revision.setValue(int(revs[self.ctrl.sheet_id]))
        self._name_changed()

    def _show_formats(self) -> None:
        """The formats a {variable} can have, with examples (a list a person can read and copy from)."""
        from ..post import naming
        rows = "".join(f"<tr><td><code>{html.escape(ex)}</code></td><td>&nbsp;&nbsp;{html.escape(text)}</td></tr>"
                       for ex, text in naming.FORMATS)
        box = QMessageBox(self)
        box.setWindowTitle("Formats of the variables")
        box.setTextFormat(Qt.RichText)
        box.setTextInteractionFlags(Qt.TextSelectableByMouse)
        box.setText("Write a format after a colon inside the braces:<br><br><table>" + rows + "</table><br>"
                    "They work in the file name, the folder inside, the toolpath part ({segments}) and toolpath names.")
        box.exec()

    def _sheet_revisions(self) -> None:
        """A number for every sheet (each sheet counts up for itself)."""
        d = QDialog(self)
        d.setWindowTitle("Revision of every sheet")
        form = QFormLayout(d)
        boxes = {}
        for sh in self.ctrl.project.sheets:
            b = QSpinBox()
            b.setRange(0, 99999)
            b.setValue(self.sheet_revisions()[sh.id])
            form.addRow(sh.name + ("  (open)" if sh.id == self.ctrl.sheet_id else ""), b)
            boxes[sh.id] = b
        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        bb.accepted.connect(d.accept)
        bb.rejected.connect(d.reject)
        form.addRow(bb)
        if d.exec():
            self.set_sheet_revisions({sid: b.value() for sid, b in boxes.items()})

    def _pick_folder(self) -> None:
        from .files import open_dir
        d = open_dir(self, self.ctrl.settings, "gcode", "Folder for the G-code")
        if d:
            self.folder.setText(d)

    def _show_last(self, last) -> None:
        if isinstance(last, dict) and last.get("files"):
            self.last_saved.setText(f"Last saved ({last.get('when', '')}, revision {last.get('revision', '')}): "
                                    + ", ".join(last["files"][:4]) + (" ..." if len(last["files"]) > 4 else "")
                                    + (f"\nin {last.get('folder', '')}" if last.get("folder") else ""))
        else:
            self.last_saved.setText("Nothing saved yet.")

    def subfolder_name(self, vals: dict) -> str:
        from ..post import naming
        text = self.subfolder.text().strip()
        if not text:
            return ""
        parts = [naming.render([p], vals, "_") for p in text.replace("\\", "/").split("/") if p.strip()]
        return "/".join(p for p in parts if p and p != "job")

    def _add_prefix(self, text: str = "", focus: bool = False) -> None:
        row = QHBoxLayout()
        edit = QLineEdit(text)
        edit.setPlaceholderText("a prefix, with {variables}")
        edit.textChanged.connect(self._name_changed)
        edit.cursorPositionChanged.connect(lambda *_: setattr(self, "_active", edit))
        from .panels import small_button
        gone = small_button("✕", "Remove this prefix", lambda: self._remove_prefix(row))
        row.addWidget(edit, 1)
        row.addWidget(gone)
        self._name_rows.addLayout(row)
        self._active = edit
        if focus:
            edit.setFocus()
        self._name_changed()

    def _remove_prefix(self, row) -> None:
        if self._name_rows.count() <= 1:
            return
        while row.count():
            w = row.takeAt(0).widget()
            if w is not None:
                if w is self._active:
                    self._active = None
                w.deleteLater()
        self._name_rows.removeItem(row)
        self._name_changed()

    def name_parts(self) -> list[str]:
        out = []
        for i in range(self._name_rows.count()):
            item = self._name_rows.itemAt(i).layout()
            if item is not None and item.count():
                out.append(item.itemAt(0).widget().text())
        return out

    def _insert_segment_var(self, i: int) -> None:
        name = self.segment_var.itemData(i)
        self.segment_var.setCurrentIndex(0)
        if name is not None:
            self.segment.insert("{" + name + "}")
            self.segment.setFocus()

    def _insert_var(self, i: int) -> None:
        name = self.name_var.itemData(i)
        self.name_var.setCurrentIndex(0)
        if name is None:
            return
        edit = self._active
        if edit is None:
            self._add_prefix("")
            edit = self._active
        edit.insert("{" + name + "}")
        edit.setFocus()

    def _revision_for(self, sheet) -> int:
        """The revision of the files of this sheet: with the revision of the sheet group, a sheet of a group has its group's number
        (the box is the number of the sheet that is open, and what is typed in it is the one for that group)."""
        r = self._revisions()
        if self.rev_scope.currentData() == "sheet" and sheet.id != self.ctrl.sheet_id:
            return int(r["sheets"].get(sheet.id, r.get("project", 1)))
        if self.rev_scope.currentData() == "group" and sheet.group and sheet.id != self.ctrl.sheet_id:
            return int(r["groups"].get(sheet.group, r.get("project", 1)))
        return self.revision.value()

    def side_sheets(self) -> list:
        """"All sheets": those of the side of the project the open sheet is on (core.scope)."""
        from ..core.scope import sheets_with
        return sheets_with(self.ctrl.project, self.ctrl.sheet)

    def name_values(self, index: int, sheet, tps) -> dict:
        """The value of every variable of the file name (and of the folder inside) for a file holding ``tps`` (every one of
        them counts: names, tools and depths joined with +)."""
        from ..post import naming
        project = self.ctrl.project
        sheets = self.side_sheets() if self.sheets.currentData() != "current" else [sheet]
        group = self.group or next(iter({t.group for t in project.toolpaths if t.id in self.chosen() and t.group}), "")
        return naming.values_for(project, sheet, index, len(sheets), self.current_post().name, group, tps,
                                 revision=self._revision_for(sheet), segment=self.segment.text() or naming.DEFAULT_SEGMENT,
                                 segment_sep=self.segment_sep.text(), stack=self.stack.isChecked())

    def file_stem(self, index: int, sheet, tps) -> str | None:
        """The name of a file of one sheet holding ``tps`` by the prefixes."""
        from ..post import naming
        sheets = self.side_sheets() if self.sheets.currentData() != "current" else [sheet]
        parts, sep = naming.check(self.name_parts(), self.name_sep.text() or naming.DEFAULT_SEP)
        name = naming.render(parts, self.name_values(index, sheet, tps), sep)
        many = len(sheets) > 1
        return f"{name}_{index}" if many and not naming.uses(parts, "sheet_number", "sheet_name") else name

    def _name_changed(self, *_) -> None:
        from ..post import naming
        if not hasattr(self, "name_preview") or not hasattr(self, "list"):
            return
        try:
            naming.check(self.name_parts(), self.name_sep.text() or naming.DEFAULT_SEP)
        except naming.NamingError as exc:
            self.name_preview.setText(f"<span style='color:#b91c1c'>{exc}</span>")
            return
        post = self.current_post()
        names = []
        sheets = [self.ctrl.sheet] if self.sheets.currentData() == "current" else self.side_sheets()
        from ..post.export import tool_groups
        for i, sh in enumerate(sheets, 1):
            tps = self.toolpaths(sh)
            groups = tool_groups(post, tps) if tps else []
            stems = [self.file_stem(i, sh, g) for g in groups]
            if len(set(x.lower() for x in stems)) < len(stems):        # (the same name for two files: the post's _1_T1 ... after it)
                stems = [f"{x}_{k + 1}_T{g[0].tool.number}" for k, (x, g) in enumerate(zip(stems, groups))]
            names += [f"{x}.{post.extension}" for x in stems]
        self.name_preview.setText("Files: " + (", ".join(names[:4]) + (" ..." if len(names) > 4 else "") if names else "(none)"))

    def tick_sheet(self) -> None:
        """Tick the toolpaths that belong on the sheet shown (``cam.job.shown_on``: calculated there), and no others."""
        from ..cam.job import shown_on
        sheet = self.ctrl.sheet
        i = self.sheets.findData("current")              # (this sheet's toolpaths are saved for this sheet)
        if i >= 0:
            self.sheets.setCurrentIndex(i)
        self._tick(lambda t: sheet is not None and shown_on(self.ctrl.project, t, sheet))

    def _tick(self, wanted) -> None:
        """Tick the toolpaths that ``wanted(toolpath)`` says yes to, and no others."""
        self.list.blockSignals(True)
        for i in range(self.list.count()):
            it = self.list.item(i)
            t = self.ctrl.project.toolpath(it.data(Qt.UserRole))
            it.setCheckState(Qt.Checked if t is not None and wanted(t) else Qt.Unchecked)
        self.list.blockSignals(False)
        self._name_changed()                  # (the names of the files follow what is ticked)
        self.preview()

    def _material_setup(self) -> None:
        MaterialDialog(self.ctrl, self).exec()
        self.preview()

    def _fill_posts(self, select: str | None) -> None:
        self.post.blockSignals(True)
        self.post.clear()
        for p in sorted(self.ctrl.registry.posts.values(), key=lambda p: p.name.lower()):
            self.post.addItem(p.name, p.id)
        self.post.setCurrentIndex(max(0, self.post.findData(select)))
        self.post.blockSignals(False)

    def _edit_post(self) -> None:
        """The post editor on the post chosen; what was made or changed there is in the list when it closes."""
        dlg = PostEditorDialog(self.ctrl, self, select=self.post.currentData())
        dlg.exec()
        self._fill_posts(dlg.selected_id() or self.post.currentData())
        self.preview()

    def current_post(self):
        return self.ctrl.registry.posts[self.post.currentData()]

    # -- the order of the toolpaths ----------------------------------------------------------------------------
    def order(self) -> list[str]:
        return [self.list.item(i).data(Qt.UserRole) for i in range(self.list.count())]

    def move_selected(self, delta: int) -> None:
        """▲ / ▼: the selected toolpath one place earlier / later."""
        row = self.list.currentRow()
        to = row + delta
        if row < 0 or not 0 <= to < self.list.count():
            return
        self.list.blockSignals(True)
        it = self.list.takeItem(row)
        self.list.insertItem(to, it)
        self.list.setCurrentRow(to)
        self.list.blockSignals(False)
        self._order_changed()

    def _order_changed(self) -> None:
        """The list was put in another order: the project's toolpaths follow (one undo step), then the G-code and the name."""
        self.ctrl.set_toolpath_order(self.order())
        self._name_changed()
        self.preview()

    def chosen(self) -> set[str]:
        return {self.list.item(i).data(Qt.UserRole) for i in range(self.list.count())
                if self.list.item(i).checkState() == Qt.Checked}

    def toolpaths(self, sheet):
        """The calculated toolpaths of the sheet that are saved, in the order they are cut (a group together, when asked)."""
        from ..post import naming
        ids = self.chosen()
        tps = [tp for tp in self.ctrl.sheet_toolpaths(sheet, enabled_only=False) if tp.def_id in ids]
        if getattr(self, "together", None) is not None and self.together.isChecked():
            tps = naming.group_order(self.ctrl.project, tps)
        return tps

    def jobs(self, sheet) -> list[tuple[str, object, list]]:
        """``[(file suffix, sheet, toolpaths)]``: the sheet, or one entry per tile."""
        tps = self.toolpaths(sheet)
        if not tps or not self.tile.isChecked():
            return [("", sheet, tps)] if tps else []
        from ..cam.tiling import tile_toolpaths
        tiles = tile_toolpaths(tps, sheet, self.tile_axis.currentData(),
                               self.ctrl.units.from_display(self.tile_len.value()),
                               self.ctrl.project.material.clearance)
        return [(f"_tile{i + 1}", t.sheet, t.toolpaths) for i, t in enumerate(tiles)]

    def preview(self, *args) -> None:
        from ..post.export import post_toolpaths
        post = self.current_post()
        self.desc.setText(f"{post.description}  (*.{post.extension})")
        m = self.ctrl.project.material
        u = self.ctrl.units
        self.heights.setText(f"{material_summary(self.ctrl)} \u00b7 home {u.fmt(m.home[0])}, {u.fmt(m.home[1])}, "
                             f"{u.fmt(m.home[2])}")
        warn = zero_warning(self.ctrl.project)
        self.zero_warning.setText(warn)
        self.zero_warning.setVisible(bool(warn))
        none = not self.chosen()
        self.save_button.setEnabled(not none)
        self.save_button.setToolTip("Tick the toolpaths to save first" if none else "Write the files")
        if none:
            self.view.show_message("Tick the toolpaths to save (the list above, or All)")
            return
        jobs = self.jobs(self.ctrl.sheet)
        if not jobs:
            self.view.show_message("(no calculated toolpaths on this sheet)")
            return
        files = []
        try:
            for tile, sheet, tps in jobs:
                files += [(tile + sfx, text) for sfx, text in post_toolpaths(self.ctrl.project, sheet, tps, post,
                                                                            "preview" + tile)]
        except Exception as exc:  # broken user posts must not take the app down
            self.view.show_message(f"Post-processor error:\n{type(exc).__name__}: {exc}")
            return
        out = []
        for suffix, text in files:
            if len(files) > 1:
                out.append(f";---------- file *{suffix}.{post.extension} ----------")
            out.append(text.replace("\r\n", "\n"))
        self.view.set_gcode("\n".join(out), post.options["units"] == "inch", post.options["ijk"] == "absolute")

    def save(self) -> None:
        """Write the files into the folder that was chosen, named by the prefixes (and in a folder of the revision when that is
        asked for). Nothing is written before this."""
        import datetime
        from ..post import naming
        post = self.current_post()
        ctrl = self.ctrl
        sheets = [ctrl.sheet] if self.sheets.currentData() == "current" else self.side_sheets()
        try:
            naming.check(self.name_parts(), self.name_sep.text() or naming.DEFAULT_SEP)
        except naming.NamingError as exc:
            QMessageBox.warning(self, "Save G-code", f"The file name: {exc}")
            return
        folder = self.folder.text().strip()
        if not folder:
            QMessageBox.information(self, "Save G-code", "Choose the folder first (Folder...), then press Save.")
            return
        base = Path(folder)
        from ..post.export import by_toolpath, named_files, per_toolpath_stem
        per = self.per_toolpath.isChecked()
        parts, sep = naming.check(self.name_parts(), self.name_sep.text() or naming.DEFAULT_SEP)
        written = []
        plan = []                       # (sheet number, sheet, [(path, text)]): every file made before anything is written
        try:
            for i, s in enumerate(sheets, 1):
                tps_here = self.toolpaths(s)
                if not tps_here:
                    continue
                vals = self.name_values(i, s, tps_here)
                sub = self.subfolder_name(vals)
                folder_here = base / sub if sub else base
                out = []
                for tile, sheet, tps in self.jobs(s):
                    for chunk in (by_toolpath(tps) if per else [tps]):      # (one file per toolpath, when asked)
                        def stem_of(g, i=i, s=s, tile=tile):
                            st = self.file_stem(i, s, g) + tile
                            return per_toolpath_stem(st, g, parts, sep) if per else st
                        for stem, text in named_files(ctrl.project, sheet, chunk, post, stem_of):
                            out.append((folder_here / f"{stem}.{post.extension}", text))
                seen: dict[str, int] = {}               # (two toolpaths of one name: the second file gets _2)
                for k, (path, text) in enumerate(out):
                    key = str(path).lower()
                    seen[key] = seen.get(key, 0) + 1
                    if seen[key] > 1:
                        out[k] = (path.with_name(f"{path.stem}_{seen[key]}{path.suffix}"), text)
                plan.append((i, s, out))
        except Exception as exc:
            QMessageBox.warning(self, "Save G-code", f"{type(exc).__name__}: {exc}")
            return
        if not plan:
            QMessageBox.information(self, "Save G-code", "There is nothing to save: no calculated toolpath on these sheets.")
            return
        taken = [p for _i, _s, out in plan for p, _t in out if p.exists()]
        if taken and QMessageBox.question(self, "Save G-code", f"{len(taken)} file(s) are there already ({taken[0].name} ...). "
                                          "Replace them?", QMessageBox.Yes | QMessageBox.No) != QMessageBox.Yes:
            return
        for _i, _s, out in plan:
            for path, text in out:
                try:
                    path.parent.mkdir(parents=True, exist_ok=True)
                    with open(path, "w", encoding="ascii", errors="replace", newline="") as fh:
                        fh.write(text)
                except Exception as exc:
                    QMessageBox.warning(self, "Save G-code", f"{type(exc).__name__}: {exc}")
                    return
                written.append(path)
        ctrl.settings.last_post = post.id
        ctrl.settings.set_last_dir("gcode", folder)
        used = self.revision.value()
        last = {"files": [p.name for p in written], "folder": str(written[0].parent) if written else folder,
                "revision": used, "when": datetime.datetime.now().strftime("%Y-%m-%d %H:%M")}
        r = self._revisions()
        up = 1 if self.rev_auto.isChecked() else 0                    # (counting up: the next save is the next revision)
        for _i, s, _o in plan:
            rev = self._revision_for(s)
            if self.rev_scope.currentData() == "sheet":
                r["sheets"][s.id] = rev + up
            elif self.rev_scope.currentData() == "group" and s.group:
                r["groups"][s.group] = rev + up
            else:
                r["project"] = rev + up
        ctrl.dirty = True
        ctrl.changed.emit()                                           # (the title and the Undo menu know)
        self._remember(last)
        self._show_last(last)
        self.revision.setValue(self._next_revision())
        ctrl.message.emit("Saved " + ", ".join(p.name for p in written) if written else "Nothing to save")
        # the window stays open: the next revision can be saved from it

    def _remember(self, last=None) -> None:
        s, u = self.ctrl.settings, self.ctrl.units
        prev = s.recall("export_gcode").get("last") if last is None else last
        s.remember("export_gcode", {"sheets": self.sheets.currentData(), "tile": self.tile.isChecked(),
                                    "per_toolpath": self.per_toolpath.isChecked(),
                                    "name_parts": self.name_parts(), "name_sep": self.name_sep.text(),
                                    "segment": self.segment.text(), "segment_sep": self.segment_sep.text(),
                                    "together": self.together.isChecked(), "stack": self.stack.isChecked(),
                                    "folder": self.folder.text().strip(), "subfolder": self.subfolder.text(),
                                    "rev_scope": self.rev_scope.currentData(), "rev_auto": self.rev_auto.isChecked(),
                                    "last": prev,
                                    "tile_axis": self.tile_axis.currentData(),
                                    "tile_len": u.from_display(self.tile_len.value())})


# ---------------------------------------------------------------------------
# plugins / posts
# ---------------------------------------------------------------------------

class PluginsDialog(RemembersSize, QDialog):
    def __init__(self, ctrl, manager, parent=None):
        super().__init__(parent)
        self.ctrl, self.pm = ctrl, manager
        self.setWindowTitle("Plugins")
        lay = QVBoxLayout(self)
        lab = QLabel(f"Plugins are Python files or packages in <code>{paths.plugins_dir()}</code> "
                     "or installed packages exposing the <code>iyicam.plugins</code> entry point.")
        lab.setWordWrap(True)
        lab.setTextInteractionFlags(Qt.TextSelectableByMouse)
        lay.addWidget(lab)
        self.table = QTableWidget(0, 4)
        self.table.setHorizontalHeaderLabels(["Enabled", "Plugin", "Version", "Status"])
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
        self.table.horizontalHeader().setSectionResizeMode(3, QHeaderView.Stretch)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.itemChanged.connect(self._changed)
        self.table.currentCellChanged.connect(lambda *a: self._details())
        lay.addWidget(self.table)
        self.details = QPlainTextEdit()
        self.details.setReadOnly(True)
        self.details.setMaximumHeight(140)
        lay.addWidget(self.details)
        row = QHBoxLayout()
        for text, slot in (("Install...", self.install), ("Open folder", self.open_folder),
                           ("Reload", self.reload)):
            b = QPushButton(text)
            b.clicked.connect(slot)
            row.addWidget(b)
        row.addStretch(1)
        close = QPushButton("Close")
        close.clicked.connect(self.accept)
        row.addWidget(close)
        lay.addLayout(row)
        self._updating = False
        self.refresh()
        self.keep_size(ctrl, "plugins", 640, 460)

    def refresh(self) -> None:
        self._updating = True
        infos = sorted(self.pm.plugins.values(), key=lambda i: i.name.lower())
        self.infos = infos
        self.table.setRowCount(len(infos))
        for r, info in enumerate(infos):
            en = QTableWidgetItem()
            en.setFlags(en.flags() | Qt.ItemIsUserCheckable)
            en.setCheckState(Qt.Checked if info.enabled else Qt.Unchecked)
            self.table.setItem(r, 0, en)
            self.table.setItem(r, 1, QTableWidgetItem(info.name))
            self.table.setItem(r, 2, QTableWidgetItem(info.version))
            status = "loaded" if info.loaded else ("error" if info.error else "disabled")
            self.table.setItem(r, 3, QTableWidgetItem(status))
        self._updating = False

    def _details(self) -> None:
        r = self.table.currentRow()
        if 0 <= r < len(self.infos):
            i = self.infos[r]
            src = i.path or i.entry_point
            self.details.setPlainText(f"{i.description}\nAuthor: {i.author}\nSource: {src}\n\n{i.error}")

    def _changed(self, it) -> None:
        if self._updating or it.column() != 0:
            return
        info = self.infos[it.row()]
        self.pm.set_enabled(info.key, it.checkState() == Qt.Checked)
        self.ctrl.settings.disabled_plugins = sorted(self.pm.disabled)
        self.ctrl.settings.save()
        # the rows are rebuilt when this change is over: replacing the item that is being changed would delete it
        QTimer.singleShot(0, self._rebuilt)

    def _rebuilt(self) -> None:
        self.refresh()
        if hasattr(self.parent(), "rebuild_plugin_ui"):
            self.parent().rebuild_plugin_ui()

    def install(self) -> None:
        fn = open_file(self, self.ctrl.settings, "plugin", "Install plugin", "Python plugin (*.py)")
        if fn:
            dst = paths.ensure(paths.plugins_dir()) / Path(fn).name
            try:
                if not (dst.exists() and dst.samefile(fn)):   # a file picked in the plugins folder is there already
                    shutil.copy(fn, dst)
            except OSError as exc:
                QMessageBox.warning(self, "Install plugin", f"The plugin could not be copied:\n{exc}")
                return
            self.reload()

    def open_folder(self) -> None:
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(paths.ensure(paths.plugins_dir()))))

    def reload(self) -> None:
        for info in list(self.pm.plugins.values()):
            self.pm.unload(info)
        self.pm.load_all()
        self.refresh()
        if hasattr(self.parent(), "rebuild_plugin_ui"):
            self.parent().rebuild_plugin_ui()


class PostEditorDialog(RemembersSize, QDialog):
    """Create and change user post-processors: a form for every option, a text for every block, the file itself and
    a live preview (on a sample part, or on the current sheet) that can be clicked to read what each line says.

    The form and the file (TOML) are one thing seen two ways: a change in either shows in the other.  Only what
    differs from the post a post is based on is written."""

    def __init__(self, ctrl, parent=None, select: str | None = None):
        super().__init__(parent)
        self.ctrl = ctrl
        self.current = None            # the PostProcessor shown (None: nothing, or a file that cannot be loaded)
        self.broken_path: Path | None = None
        self.draft: dict | None = None
        self.orig: dict | None = None
        self._syncing = False
        self._from = "form"            # which of the two was changed last: "form" or "text"
        self.setWindowTitle("Post-processors")
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(300)
        self._timer.timeout.connect(self._settle)
        lay = QHBoxLayout(self)
        left = QVBoxLayout()
        self.list = QListWidget()
        self.list.currentRowChanged.connect(self._load)
        left.addWidget(self.list)
        for text, slot, tip in (
                ("New from selected...", self.new, "Make your own post-processor from the one selected, then change it"),
                ("Delete", self.delete, "Delete the selected post-processor (only the ones you made)"),
                ("Open folder", self.open_folder, "The folder of your post-processor files"),
                ("Reload posts", self.reload, "Read the files again")):
            b = QPushButton(text)
            b.setToolTip(tip)
            b.clicked.connect(slot)
            left.addWidget(b)
            if text == "Delete":
                self.delete_btn = b
        lay.addLayout(left, 1)
        right = QVBoxLayout()
        self.path_label = QLabel()
        self.path_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        right.addWidget(self.path_label)
        self.tabs = QTabWidget()
        settings = QWidget()
        sl = QVBoxLayout(settings)
        sl.setContentsMargins(0, 6, 0, 0)
        top = QFormLayout()
        self.name_edit, self.desc_edit, self.ext_edit = QLineEdit(), QLineEdit(), QLineEdit()
        self.ext_edit.setMaxLength(8)
        self.ext_edit.setToolTip("The ending of the files, like nc or tap")
        top.addRow("Name", self.name_edit)
        top.addRow("Description", self.desc_edit)
        top.addRow("File extension", self.ext_edit)
        sl.addLayout(top)
        self.options = OptionsForm()
        sl.addWidget(self.options, 1)
        self.tabs.addTab(settings, "Settings")
        self.blocks = BlocksEditor()
        self.tabs.addTab(self.blocks, "Blocks of text")
        self.edit = QPlainTextEdit()
        self.edit.setFont(QFontDatabase.systemFont(QFontDatabase.FixedFont))
        self.edit.setLineWrapMode(QPlainTextEdit.NoWrap)
        self.tabs.addTab(self.edit, "File (TOML)")
        right.addWidget(self.tabs, 3)
        self.status = QLabel()
        self.status.setWordWrap(True)
        right.addWidget(self.status)
        row = QHBoxLayout()
        self.save_btn = QPushButton("Save")
        self.save_btn.clicked.connect(self.save)
        row.addWidget(self.save_btn)
        row.addStretch(1)
        row.addWidget(QLabel("Preview on"))
        self.source = QComboBox()
        self.source.addItem("a sample part", "sample")
        self.source.addItem("the current sheet", "sheet")
        self.source.setToolTip("The sample part has a rounded plate, a hole to drill and a round pocket: arcs, "
                               "drilling and two tools. The sheet needs calculated toolpaths.")
        self.source.currentIndexChanged.connect(lambda _i: self._settle())
        row.addWidget(self.source)
        close = QPushButton("Close")
        close.clicked.connect(self.accept)
        row.addWidget(close)
        right.addLayout(row)
        self.view = ExplainedGcode(note_height=100)
        self.out = self.view.text
        right.addWidget(self.view, 2)
        lay.addLayout(right, 3)
        for w in (self.name_edit, self.desc_edit, self.ext_edit):
            w.textEdited.connect(self._form_changed)
        self.options.changed.connect(self._form_changed)
        self.blocks.changed.connect(self._form_changed)
        self.edit.textChanged.connect(self._text_changed)
        self.refresh(select)
        self.keep_size(ctrl, "posts", 1100, 780)

    # -- the list ------------------------------------------------------------------------------
    def refresh(self, select: str | None = None) -> None:
        from ..builtins import USER_POST_ERRORS
        self.list.blockSignals(True)
        self.list.clear()
        self.posts = sorted(self.ctrl.registry.posts.values(), key=lambda p: p.name.lower())
        self.broken = sorted(USER_POST_ERRORS.items())   # files that could not be loaded, still to be mended here
        row = 0
        for i, p in enumerate(self.posts):
            it = QListWidgetItem(("✎ " if editing.is_user_post(p) else "") + p.name)
            self.list.addItem(it)
            if p.id == select:
                row = i
        for i, (path, _) in enumerate(self.broken):
            self.list.addItem(QListWidgetItem(f"⚠ {Path(path).name} (cannot be loaded)"))
            if select is not None and Path(path).stem == select:
                row = len(self.posts) + i
        self.list.blockSignals(False)
        self.list.setCurrentRow(row)

    def selected_id(self) -> str | None:
        """The id of the post that is shown (``None`` for a file that cannot be loaded)."""
        return self.current.id if self.current is not None else None

    # -- showing a post --------------------------------------------------------------------------
    def _say(self, text: str, kind: str = "") -> None:
        color = {"bad": "#b91c1c", "warn": "#b45309"}.get(kind)
        self.status.setStyleSheet(f"color:{color}" if color else "")
        self.status.setText(text)

    def _set_text(self, text: str) -> None:
        self._syncing = True
        try:
            self.edit.setPlainText(text)
        finally:
            self._syncing = False

    def _fill(self, draft: dict, original: dict, editable: bool) -> None:
        """The form shows a draft (the marks show what differs from ``original``)."""
        self._syncing = True
        try:
            self.name_edit.setText(draft.get("name", ""))
            self.desc_edit.setText(draft.get("description", ""))
            self.ext_edit.setText(draft.get("extension", ""))
            for w in (self.name_edit, self.desc_edit, self.ext_edit):
                w.setEnabled(editable)
            self.options.load(draft["options"], original["options"], editable)
            self.blocks.load(draft["blocks"], original["blocks"], editable)
        finally:
            self._syncing = False

    def _load_broken(self, path: str, error: str) -> None:
        """A post file that could not be loaded: its text can be edited, and the mistake is shown."""
        self._timer.stop()
        self.current = None
        self.draft = self.orig = None
        self.broken_path = Path(path)
        self.path_label.setText(f"{path}   [cannot be loaded]")
        try:
            text = Path(path).read_text(encoding="utf-8")
        except OSError as exc:
            text = f"# {exc}"
        self._set_text(text)
        self.edit.setReadOnly(False)
        self.tabs.setCurrentWidget(self.edit)
        self.tabs.setTabEnabled(0, False)
        self.tabs.setTabEnabled(1, False)
        self.save_btn.setEnabled(True)
        self.delete_btn.setEnabled(True)
        self._say("")
        self.view.show_message(f"This post-processor cannot be loaded:\n{error}\n\nCorrect it and save.")

    def _load(self, row: int) -> None:
        if len(self.posts) <= row < len(self.posts) + len(self.broken):
            self._load_broken(*self.broken[row - len(self.posts)])
            return
        if not 0 <= row < len(self.posts):
            return
        self._timer.stop()
        self.broken_path = None
        p = self.posts[row]
        self.current = p
        self.draft = editing.draft_of(p)
        self.orig = copy.deepcopy(self.draft)
        user = editing.is_user_post(p)
        self.path_label.setText(str(p.path or "(defined by a plugin)") + ("" if user else "   [read only]"))
        text = Path(p.path).read_text(encoding="utf-8") if p.path and Path(p.path).exists() else \
            json.dumps(p.definition, indent=2)
        self._set_text(text)
        self.edit.setReadOnly(not user)
        self.tabs.setTabEnabled(0, True)
        self.tabs.setTabEnabled(1, True)
        self.save_btn.setEnabled(user)
        self.delete_btn.setEnabled(user)
        self._fill(self.draft, self.orig, user)
        self._say("" if user else "This post-processor comes with the program and cannot be changed. "
                                  "Use \u201cNew from selected...\u201d to make your own from it.")
        self._test(p)

    # -- the form and the file are one thing --------------------------------------------------------
    def _form_changed(self, *_args) -> None:
        """A field of the form was changed by a person: the file is written again from the form."""
        if self._syncing or self.draft is None or self.current is None:
            return
        self.draft["name"], self.draft["description"] = self.name_edit.text(), self.desc_edit.text()
        self.draft["extension"] = self.ext_edit.text().strip().lstrip(".").lower() or "nc"
        self.draft["options"] = dict(self.options.options)
        self.draft["blocks"] = dict(self.blocks.blocks)
        self._from = "form"
        if self._write_toml():
            self._timer.start()

    def _write_toml(self) -> bool:
        try:
            clean, warnings = editing.clean_draft(self.draft)
        except editing.PostEditError as exc:
            self._say(str(exc), "bad")
            return False
        clean["id"] = self.current.id
        parent = editing.parent_of(clean["extends"], self.ctrl.registry.posts, self.current.id)
        if parent is None:
            clean["extends"] = ""
        self._set_text(editing.dump_toml(editing.own_definition(clean, parent)))
        self._say("\n".join(warnings), "warn" if warnings else "")
        return True

    def _text_changed(self) -> None:
        """The file was typed in: the form follows it (after a short pause, when what was typed makes sense)."""
        if self._syncing:
            return
        self._from = "text"
        self._timer.start()

    def _post_of_text(self, text: str):
        """``(PostProcessor, parsed TOML)`` of the text of the file; raises what is wrong with it."""
        from ..post.engine import PostProcessor
        from ..post.loader import merge
        d = tomllib.loads(text)
        pid = str(d.get("id") or (self.current.id if self.current is not None else "draft"))
        known = {p.id: p.definition for p in self.ctrl.registry.posts.values() if p.id != pid}
        parent = known.get(d.get("extends")) if d.get("extends") else None
        full = merge(parent, d) if parent else dict(d)
        full["id"] = pid
        return PostProcessor(full, self.current.path if self.current is not None else None), d

    def _settle(self) -> None:
        """Whatever was changed, the file is the truth: it is read, the form follows (when the file was typed in) and
        the preview is made again."""
        if self.current is not None and not editing.is_user_post(self.current):
            self._test(self.current)               # (a post that is read only: there is no file text to read again)
            return
        text = self.edit.toPlainText()
        try:
            post, parsed = self._post_of_text(text)
        except Exception as exc:  # noqa: BLE001  (a file that is wrong while it is being typed is not an error)
            self._say(f"{type(exc).__name__}: {exc}", "bad")
            self.view.show_message(f"This file cannot be used yet:\n{type(exc).__name__}: {exc}")
            return
        if self.current is not None and self.draft is not None:
            if self._from == "text":
                self.draft = editing.draft_of(post, str(parsed.get("extends") or ""))
                self._fill(self.draft, self.orig, editing.is_user_post(self.current))
            try:
                warnings = editing.clean_draft(self.draft)[1]
            except editing.PostEditError as exc:
                self._say(str(exc), "bad")
            else:
                self._say("\n".join(warnings), "warn" if warnings else "")
        self._test(post)

    def _test(self, post) -> None:
        """The preview: the G-code of a sample part (or of the current sheet) written with ``post``."""
        from ..post.export import post_toolpaths
        try:
            tps = self.ctrl.sheet_toolpaths() if self.source.currentData() == "sheet" else []
            if self.source.currentData() == "sheet" and not tps:
                self.view.show_message("(calculate a toolpath to see a preview of the sheet)")
                return
            files = post_toolpaths(self.ctrl.project, self.ctrl.sheet, tps, post, "preview") if tps \
                else editing.sample_files(post)
        except Exception as exc:  # noqa: BLE001  (a post that fails while it is being made is told, not raised)
            self.view.show_message(f"Error: {type(exc).__name__}: {exc}")
            return
        self.view.set_gcode("\n".join(t for _, t in files)[:300000], post.options["units"] == "inch",
                            post.options["ijk"] == "absolute")

    # -- making, saving, deleting ----------------------------------------------------------------------
    def new(self) -> None:
        base = self.current
        if base is None:
            return
        name, ok = QInputDialog.getText(self, "New post-processor", "Name:", text=base.name + " (custom)")
        if not ok or not name:
            return
        d = editing.draft_of(base, extends=base.id)
        d.update(id="", name=name.strip(), description=f"Based on {base.name}")
        try:
            target, _warnings = editing.save_user_post(d, self.ctrl.registry.posts)
        except (editing.PostEditError, OSError) as exc:
            QMessageBox.warning(self, "Post-processors", str(exc))
            return
        from ..post.loader import read_definition
        self.reload(select=str(read_definition(target).get("id") or target.stem))

    def save(self) -> None:
        self._timer.stop()
        broken = self.broken_path
        if broken is not None:
            broken.write_text(self.edit.toPlainText(), encoding="utf-8")
            self.reload(select=broken.stem)
            return
        p = self.current
        if p is None or not editing.is_user_post(p):
            return
        Path(p.path).write_text(self.edit.toPlainText(), encoding="utf-8")
        self.reload(select=p.id)

    def delete(self) -> None:
        p, broken = self.current, self.broken_path
        target = Path(p.path) if p is not None and editing.is_user_post(p) else broken
        if target is None:
            return
        name = p.name if p is not None else target.name
        if QMessageBox.question(self, "Post-processors", f"Delete \u201c{name}\u201d?") != QMessageBox.Yes:
            return
        try:
            target.unlink(missing_ok=True)
        except OSError as exc:
            QMessageBox.warning(self, "Post-processors", str(exc))
            return
        self.reload()

    def reload(self, select: str | None = None) -> None:
        from ..builtins import load_user_posts
        self._timer.stop()
        reg = self.ctrl.registry
        reg.remove_source("user-posts")
        try:
            load_user_posts(reg)
        except Exception as exc:  # noqa: BLE001
            QMessageBox.warning(self, "Post-processors", f"{type(exc).__name__}: {exc}")
        self.refresh(select if isinstance(select, str) else None)

    def open_folder(self) -> None:
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(paths.ensure(paths.posts_dir()))))

    def done(self, result: int) -> None:
        self._timer.stop()
        super().done(result)


# ---------------------------------------------------------------------------
# text / import / preferences
# ---------------------------------------------------------------------------

class TextDialog(QDialog):
    DEFAULTS = {"text": "iyiCAM", "font": "", "height": 30.0, "bold": False, "italic": False, "align": "left",
                "spacing": 0.0}

    def __init__(self, ctrl, parent=None):
        super().__init__(parent)
        self.ctrl = ctrl
        self.setWindowTitle("Text")
        lay = QVBoxLayout(self)
        form = QFormLayout()
        L = dict(self.DEFAULTS)   # what the last text was set to, if it still fits
        for k, v in ctrl.settings.recall("text").items():
            if k in L and type(v) is type(L[k]) or (k in ("height", "spacing") and isinstance(v, (int, float))
                                                    and not isinstance(v, bool)):
                L[k] = v
        if L["align"] not in ("left", "center", "right"):
            L["align"] = "left"
        L["height"] = max(0.1, float(L["height"]))
        self.edit = QPlainTextEdit(L["text"])
        self.edit.setMaximumHeight(90)
        form.addRow("Text", self.edit)
        self.font = QFontComboBox()
        if L["font"]:
            self.font.setCurrentFont(QFont(L["font"]))
        form.addRow("Font", self.font)
        u = ctrl.units
        self.height = QDoubleSpinBox()
        self.height.setRange(0.1, 10000)
        self.height.setSuffix(u.suffix)
        self.height.setValue(u.to_display(L["height"]))
        form.addRow("Capital height", self.height)
        self.spacing = QDoubleSpinBox()
        self.spacing.setRange(-1000, 1000)
        self.spacing.setSuffix(u.suffix)
        self.spacing.setValue(u.to_display(L["spacing"]))
        form.addRow("Extra letter spacing", self.spacing)
        self.bold, self.italic = QCheckBox("Bold"), QCheckBox("Italic")
        self.bold.setChecked(L["bold"])
        self.italic.setChecked(L["italic"])
        row = QHBoxLayout()
        row.addWidget(self.bold)
        row.addWidget(self.italic)
        form.addRow("Style", row)
        self.align = QComboBox()
        for a in ("left", "center", "right"):
            self.align.addItem(a.title(), a)
        self.align.setCurrentIndex(max(0, self.align.findData(L["align"])))
        form.addRow("Alignment", self.align)
        lay.addLayout(form)
        lay.addWidget(buttons(self))

    def text(self) -> str:
        return self.edit.toPlainText()

    def contours(self):
        from .qtgeom import text_contours
        u = self.ctrl.units
        v = {"text": self.text(), "font": self.font.currentFont().family(),
             "height": u.from_display(self.height.value()), "bold": self.bold.isChecked(),
             "italic": self.italic.isChecked(), "align": self.align.currentData(),
             "spacing": u.from_display(self.spacing.value())}
        self.ctrl.settings.remember("text", v)
        return text_contours(v["text"], v["font"], v["height"], v["bold"], v["italic"], v["spacing"],
                             align=v["align"])


class ImportDialog(QDialog):
    UNITS = [("auto", "Automatic (from file)"), ("1", "Millimetres"), ("25.4", "Inches"), ("10", "Centimetres"),
             ("1000", "Metres")]

    def __init__(self, ctrl, path: str, parent=None):
        super().__init__(parent)
        self.setWindowTitle(f"Import {Path(path).name}")
        dxf = path.lower().endswith(".dxf")
        params = [Param("keep_layers", "Use the file's layers", "bool", True)]
        if path.lower().endswith(".cdr"):
            params = [Param("scale", "Scale", "percent", 100.0, min=0.001)]
        elif dxf:
            from ..formats.dxf import detect_units
            code, name = detect_units(path)
            params = [Param("units", f"Units (file says: {name})", "choice", "auto", choices=self.UNITS),
                      Param("join", "Join gaps up to", "length", 0.05, min=0.0)] + params
        else:
            if Path(path).suffix.lower() in (".plt", ".hpgl", ".hpg"):
                params.append(Param("join", "Join gaps up to", "length", 0.05, min=0.0))
            params.append(Param("scale", "Scale", "percent", 100.0, min=0.001))
        params.append(Param("center", "Centre on the sheet", "bool", False))
        self.ctrl = ctrl
        self.params = params
        self.key = f"import:{Path(path).suffix.lower().lstrip('.')}"
        lay = QVBoxLayout(self)
        self.form = ParamForm(params, ctrl.settings.recall(self.key, params), ctrl.units)
        lay.addWidget(self.form)
        lay.addWidget(buttons(self, "Import"))

    def values(self) -> dict:
        v = self.form.values()
        self.ctrl.settings.remember(self.key, v, self.params)
        return v


class ExportOptionsDialog(QDialog):
    """The options of a file format (``Exporter.params``), remembered for the next export."""

    def __init__(self, ctrl, exporter, parent=None):
        super().__init__(parent)
        self.setWindowTitle(f"Export {exporter.label}")
        self.ctrl, self.params = ctrl, exporter.params
        self.key = f"export:{exporter.extension}"
        lay = QVBoxLayout(self)
        self.form = ParamForm(self.params, ctrl.settings.recall(self.key, self.params), ctrl.units)
        lay.addWidget(self.form)
        lay.addWidget(buttons(self, "Export"))

    def values(self) -> dict:
        v = self.form.values()
        self.ctrl.settings.remember(self.key, v, self.params)
        return v


class PreferencesDialog(QDialog):
    def __init__(self, ctrl, parent=None):
        super().__init__(parent)
        self.ctrl = ctrl
        s = ctrl.settings
        self.setWindowTitle("Preferences")
        params = [
            Param("units", "Display units", "choice", s.units, choices=[("mm", "Millimetres"), ("inch", "Inches")]),
            Param("feed_units", "Feed rates", "choice", s.feed_units,
                  choices=[("min", "Per minute (mm/min)"), ("s", "Per second (mm/s)")],
                  help="How feed and plunge rates are shown and typed; the G-code is always per minute (F)"),
            Param("theme", "Appearance", "choice", s.theme,
                  choices=[("system", "Follow the system"), ("light", "Light"), ("dark", "Dark")]),
            Param("grid", "Grid spacing (mm)", "float", s.grid, min=0.0),
            Param("snap_grid", "Snap to grid", "bool", s.snap_grid),
            Param("snap_objects", "Snap to vectors (nodes, middles, centres, crossings, sheet)", "bool",
                  s.snap_objects),
            Param("snap_align", "Snap level with a node (same X or Y)", "bool", s.snap_align),
            Param("snap_edges", "Snap to any point on a vector", "bool", s.snap_edges),
            Param("snap_radius", "Snap reach (pixels)", "float", s.snap_radius, min=2.0, max=60.0,
                  help="How near the pointer has to be for something to catch it; the same at every zoom"),
            Param("resize_handles", "Resize handles round a selection (mouse)", "bool", s.resize_handles,
                  help="Off: a selection is only dragged, from anywhere inside it (also Vectors > Edit)"),
            Param("nudge_mode", "Arrow key step", "choice", s.nudge_mode,
                  choices=[("auto", "Automatic (follows the zoom: far moves more, close finer)"), ("fixed", "Fixed")]),
            Param("nudge_step", "Fixed arrow key step", "length", s.nudge_step, min=0.001, visible={"nudge_mode": "fixed"},
                  help="Shift: 10 times, Ctrl: a tenth"),
            Param("auto_recalculate", "Recalculate toolpaths automatically after every edit", "bool", s.auto_recalculate),
            Param("calculate_on_open", "Calculate the toolpaths of a project when it is opened", "bool", s.calculate_on_open),
            Param("ask_tool", "A new toolpath asks for its tool from the tool database first", "bool", s.ask_tool),
            Param("show_directions", "Show direction arrows", "bool", s.show_directions),
            Param("sim_resolution", "Simulation resolution (mm, 0 = auto)", "float", s.sim_resolution, min=0.0),
        ]
        from ..cam import tp_defaults
        mine = tp_defaults.load()             # (kept in the user folder: the web app has the same)
        params += [Param(k, label, "length", mine[k], min=0.0, max=100.0,
                         help="For new toolpaths; 0: the cut stops at the bottom of the material") for k, label, _ in tp_defaults.KEYS]
        lay = QVBoxLayout(self)
        self.form = ParamForm(params, None, ctrl.units)
        lay.addWidget(self.form)
        lay.addWidget(buttons(self))

    def accept(self) -> None:
        v = self.form.values()
        s = self.ctrl.settings
        window = self.parent()
        from ..cam import tp_defaults
        tp_defaults.save({k: v.pop(k) for k, _, _ in tp_defaults.KEYS if k in v})
        for k, val in v.items():
            if k in ("show_directions", "show_cut_order", "show_route", "show_starts") and hasattr(window, "set_view"):
                window.set_view(k, bool(val))              # (the View menu and the drawing follow)
            elif k not in ("units", "theme"):
                setattr(s, k, val)
        self.ctrl.set_units(v["units"])
        if hasattr(window, "sync_snap"):
            window.sync_snap()
        if hasattr(window, "sync_edit_options"):
            window.sync_edit_options()
        if hasattr(window, "set_theme"):
            window.set_theme(v["theme"])
        else:
            s.theme = v["theme"]
        s.save()
        super().accept()

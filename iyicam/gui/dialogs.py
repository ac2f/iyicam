"""Dialogs: commands, sheets, material, tools, toolpaths, nesting, export, plugins, posts, text."""
from __future__ import annotations

import copy
import json
import math
import shutil
import threading
from pathlib import Path

from PySide6.QtCore import Qt, QTimer, QUrl
from PySide6.QtGui import QDesktopServices, QFont, QFontDatabase, QGuiApplication
from PySide6.QtWidgets import (QAbstractItemView, QApplication, QButtonGroup, QCheckBox, QComboBox, QDialog,
                               QDialogButtonBox, QDoubleSpinBox, QFontComboBox, QFormLayout,
                               QHBoxLayout, QHeaderView, QInputDialog, QLabel, QLineEdit, QListWidget,
                               QListWidgetItem, QMessageBox, QPlainTextEdit, QProgressDialog, QPushButton,
                               QRadioButton, QScrollArea, QSpinBox, QSplitter, QTableWidget, QTableWidgetItem,
                               QTabWidget, QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget)

from ..core import clip, paths
from ..core.document import XY_ORIGINS, Selection, ToolpathDef
from ..core.params import Param
from ..core.stock import StockItem, load_stock, save_stock
from ..core.tooling import TOOL_TYPES, Tool
from ..nesting.nest import NestCancelled, NestSettings
from ..nesting.parts import (ROTATION_MODES, RULE_USES_VALUE, apply_nesting, parse_angles, prepare_nesting,
                             run_nesting, squeeze_lines)
from ..nesting.stock import leftover_of_sheet, stock_from_vectors, use_up
from .files import open_file, save_file
from .forms import ParamForm


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


# ---------------------------------------------------------------------------
# sheets / material
# ---------------------------------------------------------------------------

SHEET_PRESETS = [("2800 x 2100", 2800, 2100), ("3660 x 1830", 3660, 1830), ("2440 x 1220", 2440, 1220),
                 ("2500 x 1250", 2500, 1250), ("3050 x 1220", 3050, 1220), ("1220 x 610", 1220, 610)]


class SheetDialog(QDialog):
    def __init__(self, ctrl, sheet, parent=None):
        super().__init__(parent)
        self.ctrl, self.sheet = ctrl, sheet
        self.setWindowTitle("Sheet")
        u = ctrl.units
        params = [Param("name", "Name", "str", sheet.name),
                  Param("width", "Width (X)", "length", sheet.width, min=1.0),
                  Param("height", "Height (Y)", "length", sheet.height, min=1.0),
                  Param("own_thickness", "Own thickness", "bool", sheet.thickness is not None),
                  Param("thickness", "Thickness", "length", sheet.thickness or ctrl.project.material.thickness,
                        min=0.01, visible={"own_thickness": True})]
        lay = QVBoxLayout(self)
        presets = QComboBox()
        presets.addItem("Preset sizes...")
        for name, w, h in SHEET_PRESETS:
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
        with self.ctrl.edit("Sheet properties"):
            s = self.sheet
            s.name, s.width, s.height = v["name"], v["width"], v["height"]
            s.thickness = v["thickness"] if v["own_thickness"] else None
            if self.plain.isChecked():
                s.outline = None
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
            Param("thickness", "Material thickness", "length", m.thickness, min=0.01, group="Material",
                  visible={"rotary": False}),
            Param("z_zero", "Z zero", "choice", m.z_zero,
                  choices=[("top", "Top of the material"), ("bottom", "Bottom (machine bed / rotary axis)")],
                  group="Material"),
            Param("xy_origin", "XY origin", "choice", m.xy_origin, choices=list(origin_labels.items()),
                  group="Material"),
            Param("safe_z", "Safe Z (above surface)", "length", m.safe_z, min=0.1, group="Heights"),
            Param("clearance", "Rapid-down clearance", "length", m.clearance, min=0.0, group="Heights",
                  help="The tool rapids down to this height above the material and then plunges"),
            Param("home_x", "Home X", "length", m.home[0], group="Home position"),
            Param("home_y", "Home Y", "length", m.home[1], group="Home position"),
            Param("home_z", "Home Z (above surface)", "length", m.home[2], group="Home position"),
            Param("rapid_rate", "Machine rapid rate", "feed", m.rapid_rate, min=1.0, group="Machine",
                  help="Only used for machining time estimates"),
            Param("rotary", "Wrap around a rotary axis", "bool", m.rotary, group="Rotary",
                  help="The sheet is the unrolled cylinder: X along the axis, Y around it (written as A)"),
            Param("rotary_diameter", "Cylinder diameter", "length", m.rotary_diameter, min=1.0, group="Rotary",
                  visible={"rotary": True}, help="Sets the sheet height to the circumference"),
        ]
        lay = QVBoxLayout(self)
        self.form = ParamForm(params, None, ctrl.units)
        lay.addWidget(scroll(self.form))
        lay.addWidget(buttons(self))
        self.keep_size(ctrl, "material", 420, 560)

    def accept(self) -> None:
        v = self.form.values()
        with self.ctrl.edit("Material setup"):
            m = self.ctrl.project.material
            m.thickness, m.z_zero, m.xy_origin = v["thickness"], v["z_zero"], v["xy_origin"]
            m.safe_z, m.clearance, m.rapid_rate = v["safe_z"], v["clearance"], v["rapid_rate"]
            m.home = (v["home_x"], v["home_y"], v["home_z"])
            self.ctrl.sheet.width, self.ctrl.sheet.height = v["width"], v["height"]
            m.rotary, m.rotary_diameter = v["rotary"], v["rotary_diameter"]
            if m.rotary:  # depths are measured from the surface, the axis is one radius below
                m.thickness = m.rotary_diameter / 2
                for s in self.ctrl.project.sheets:
                    s.height, s.thickness = math.pi * m.rotary_diameter, None
        self.ctrl.settings.remember("material", {k: v[k] for k in self.ctrl.MATERIAL_MEMORY})  # for new projects
        self.ctrl.sheet_changed.emit()
        super().accept()


# ---------------------------------------------------------------------------
# tools
# ---------------------------------------------------------------------------

def tool_params(t: Tool) -> list[Param]:
    tapered = ["v_bit", "engraving"]
    return [
        Param("name", "Name", "str", t.name, group="Tool"),
        Param("type", "Type", "choice", t.type, choices=list(TOOL_TYPES.items()), group="Tool"),
        Param("number", "Tool number", "int", t.number, min=0, max=999, group="Tool"),
        Param("group", "Group", "str", t.group, group="Tool"),
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
    def __init__(self, ctrl, tool: Tool | None = None, parent=None):
        super().__init__(parent)
        self.ctrl = ctrl
        self.lay = QVBoxLayout(self)
        self.lay.setContentsMargins(0, 0, 0, 0)
        self.form = None
        self._tool = None
        self.set_tool(tool or Tool())

    def set_tool(self, t: Tool) -> None:
        self._tool = t
        if self.form is not None:
            self.lay.removeWidget(self.form)
            self.form.deleteLater()
        self.form = ParamForm(tool_params(t), None, self.ctrl.units)
        self.lay.addWidget(self.form)

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

    def __init__(self, ctrl, parent=None, pick: bool = False):
        super().__init__(parent)
        self.ctrl = ctrl
        self.setWindowTitle("Select Tool" if pick else "Tool Database")
        self.lib_tools = [t.copy() for t in ctrl.tools.tools]
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
        split.addWidget(left)
        self.editor = ToolEditor(ctrl)
        split.addWidget(scroll(self.editor))
        split.setSizes([260, 380])
        lay.addWidget(split)
        lay.addWidget(buttons(self, "Select" if pick else "Save"))
        self.current: Tool | None = None
        self.refresh()
        self.keep_size(ctrl, "tools", 720, 620)

    def _commit_editor(self) -> None:
        if self.current is not None:
            new = self.editor.tool()
            for i, t in enumerate(self.lib_tools):
                if t.id == self.current.id:
                    self.lib_tools[i] = new
                    self.current = new

    def refresh(self, select_id: str | None = None) -> None:
        self.tree.blockSignals(True)
        self.tree.clear()
        groups: dict[str, QTreeWidgetItem] = {}
        target = None
        for t in self.lib_tools:
            g = groups.get(t.group)
            if g is None:
                g = QTreeWidgetItem([t.group or "Ungrouped"])
                g.setFlags(g.flags() & ~Qt.ItemIsSelectable)
                self.tree.addTopLevelItem(g)
                g.setExpanded(True)
                groups[t.group] = g
            it = QTreeWidgetItem([f"T{t.number}  {t.name}"])
            it.setData(0, Qt.UserRole, t.id)
            g.addChild(it)
            if t.id == (select_id or (self.current.id if self.current else None)):
                target = it
        self.tree.blockSignals(False)
        if target is None and self.lib_tools:
            target = groups[self.lib_tools[0].group].child(0)
        if target is not None:
            self.tree.setCurrentItem(target)

    def _changed(self, cur, prev) -> None:
        self._commit_editor()
        tid = cur.data(0, Qt.UserRole) if cur else None
        t = next((t for t in self.lib_tools if t.id == tid), None)
        if t is not None:
            self.current = t
            self.editor.set_tool(t)

    def add(self) -> None:
        self._commit_editor()
        t = Tool(name="New tool", number=max((t.number for t in self.lib_tools), default=0) + 1)
        self.lib_tools.append(t)
        self.current = t
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
        try:
            self.ctrl.save_tools()
        except OSError as exc:
            QMessageBox.warning(self, "Tool database", f"Could not save the tool library:\n{exc}")
        super().accept()


# ---------------------------------------------------------------------------
# toolpaths
# ---------------------------------------------------------------------------

class ToolpathDialog(RemembersSize, QDialog):
    def __init__(self, ctrl, op_id: str, tdef: ToolpathDef | None = None, parent=None):
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
        top.addRow("Name", self.name)
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
        tool = self.tdef.tool if self.tdef else self._remembered_tool(mem.get("tool")) or self._default_tool()
        self.tool_editor = ToolEditor(ctrl, tool)
        tl.addWidget(scroll(self.tool_editor))
        # -- vectors tab
        vec_tab = QWidget()
        vl = QVBoxLayout(vec_tab)
        self.rb_sel = QRadioButton()
        self.rb_layers = QRadioButton("Vectors on these layers (on every sheet):")
        grp = QButtonGroup(self)
        grp.addButton(self.rb_sel)
        grp.addButton(self.rb_layers)
        vl.addWidget(self.rb_sel)
        vl.addWidget(self.rb_layers)
        self.layer_list = QListWidget()
        sel = self.tdef.selection if self.tdef else None
        for l in ctrl.project.layers:
            it = QListWidgetItem(l.name)
            it.setData(Qt.UserRole, l.id)
            it.setFlags(it.flags() | Qt.ItemIsUserCheckable)
            checked = (sel is not None and l.id in sel.layer_ids)
            it.setCheckState(Qt.Checked if checked else Qt.Unchecked)
            self.layer_list.addItem(it)
        vl.addWidget(self.layer_list)
        self.all_sheets = QCheckBox("Calculate on every sheet")
        self.all_sheets.setChecked(self.tdef is None and mem.get("all_sheets", True) is not False
                                   or self.tdef is not None and self.tdef.sheet_ids is None)
        vl.addWidget(self.all_sheets)
        cur_ids = [o.id for o in ctrl.selected_objects()]
        if self.tdef and self.tdef.selection.mode == "vectors" and not cur_ids:
            cur_ids = list(self.tdef.selection.vector_ids)
        self.vector_ids = cur_ids
        self.rb_sel.setText(f"Selected vectors ({len(cur_ids)})")
        (self.rb_layers if sel is not None and sel.mode == "layers" else self.rb_sel).setChecked(True)
        if not cur_ids and (sel is None or sel.mode != "layers"):
            self.rb_layers.setChecked(True)
            for i in range(self.layer_list.count()):
                it = self.layer_list.item(i)
                if it.data(Qt.UserRole) == ctrl.layer_id:
                    it.setCheckState(Qt.Checked)
        # -- parameters tab
        params = self.op.params()
        for p in params:
            if p.name == "cut_depth" and not self.tdef and self.op.id == "profile":
                p.default = ctrl.project.material.thickness
        # a new toolpath starts with the settings of the last one of its kind, but its depth is the material's
        self.params = [p for p in params if not (p.name == "cut_depth" and self.op.id == "profile")]
        values = dict(self.tdef.params) if self.tdef else ctrl.settings.recall(self.memory_key + ":params", self.params)
        self.form = ParamForm(params, values, ctrl.units, ctrl.tools)
        tabs.addTab(scroll(self.form), "Settings")
        tabs.addTab(tool_tab, "Tool")
        tabs.addTab(vec_tab, "Vectors")
        lay.addWidget(buttons(self, "Calculate"))
        self._update_tool_label()
        self.tool_editor.form.changed.connect(self._update_tool_label)
        self.keep_size(ctrl, f"toolpath:{op_id}", 460, 680)

    def _remembered_tool(self, d) -> Tool | None:
        """The tool of the last toolpath of this kind (when it still fits the toolpath)."""
        if not isinstance(d, dict):
            return None
        try:
            t = Tool.from_dict(d)
        except (TypeError, ValueError, KeyError):
            return None
        return t if self.op.tool_types is None or t.type in self.op.tool_types else None

    def _remember(self) -> None:
        s = self.ctrl.settings
        s.remember(self.memory_key + ":params", self.form.values(), self.params, save=False)
        s.remember(self.memory_key, {"tool": self.tool_editor.tool().to_dict(),
                                     "all_sheets": self.all_sheets.isChecked()})

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
        sheets = None if self.all_sheets.isChecked() else [self.ctrl.sheet_id]
        params = {k: v for k, v in self.form.values().items()}
        if self.tdef:
            t = self.tdef
            t.name, t.tool, t.params, t.selection, t.sheet_ids = self.name.text(), self.tool_editor.tool(), \
                params, sel, sheets
            return t
        return ToolpathDef(self.name.text(), self.op.id, self.tool_editor.tool(), params, sel, True, sheets)

    def accept(self) -> None:
        if self.rb_sel.isChecked() and not self.vector_ids:
            QMessageBox.information(self, "Toolpath", "Select vectors first, or choose layers on the Vectors tab.")
            return
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
    """A plate for the stock: a rectangle of a size."""

    def __init__(self, ctrl, parent=None):
        super().__init__(parent)
        self.ctrl = ctrl
        self.setWindowTitle("Add Plate")
        s = ctrl.sheet
        params = [Param("name", "Name", "str", "Leftover"),
                  Param("width", "Width (X)", "length", s.width, min=1.0),
                  Param("height", "Height (Y)", "length", s.height, min=1.0),
                  Param("quantity", "Quantity (0 = unlimited)", "int", 1, min=0, max=9999),
                  Param("leftover", "Leftover (use before new sheets)", "bool", True)]
        lay = QVBoxLayout(self)
        presets = QComboBox()
        presets.addItem("Preset sheet sizes...")
        for name, w, h in SHEET_PRESETS:
            presets.addItem(name, (w, h))
        lay.addWidget(presets)
        self.params = [p for p in params if p.name in ("quantity", "leftover")]   # the size follows the sheet
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
        v = self.form.values()
        self.ctrl.settings.remember("plate", v, self.params)
        return StockItem(v["name"], v["width"], v["height"], None, v["quantity"], v["leftover"])


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


class NestingDialog(RemembersSize, QDialog):
    def __init__(self, ctrl, parent=None):
        super().__init__(parent)
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
            Param("width", "New sheet width", "length", s.width, min=1.0, group="Sheets"),
            Param("height", "New sheet height", "length", s.height, min=1.0, group="Sheets"),
            Param("margin", "Border gap", "length", 10.0, min=0.0, group="Sheets"),
            Param("spacing", "Gap between parts", "length", tool_d + 2.0, min=0.0, group="Sheets",
                  help="Usually the cutter diameter plus a little clearance"),
            Param("target", "Place on", "choice", "current",
                  choices=[("current", "Current sheet first (around what stays on it), then the stock"),
                           ("all", "All sheets first (around what stays on them), then the stock"),
                           ("new", "The stock and new sheets only")],
                  group="Sheets"),
            Param("new_sheets", "Start new sheets when needed", "bool", True, group="Sheets"),
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
            Param("gravity", "Pack towards", "choice", "left",
                  choices=[("left", "Left (X min)"), ("bottom", "Bottom (Y min)"), ("compact", "Smallest area"),
                           ("reach_x", "Shortest layout along X"), ("reach_y", "Shortest layout along Y")],
                  group="Strategy", help="The first layout is made this way; with more attempts the other "
                  "ways are tried too and the one that uses the least of the sheet is kept"),
            Param("iterations", "Optimisation attempts", "int", 8, min=1, max=200, group="Strategy",
                  help="1: one quick pass. More: other orders of the parts and other directions are tried, "
                       "the best layout (fewest sheets, least area used on the last) is kept and polished"),
            Param("time_limit", "Time limit of the search (seconds)", "int", 30, min=0, max=3600, group="Strategy",
                  help="No further layout is tried after this; the best so far is kept. 0 = no limit"),
            Param("group_inner", "Inner pieces (göbek) stay with their part", "bool", False, group="Parts",
                  help="On: whatever lies inside a part (the pieces in its holes too) moves with it. "
                       "Off: pieces in holes are parts of their own and are nested separately"),
            Param("fill_holes", "Place smaller parts in the holes of parts", "bool", False, group="Parts",
                  help="Holes that hold nothing that stays can take other parts. Not for holes that "
                       "are only drawn (engraved), as their material is not cut away"),
            Param("tolerance", "Outline tolerance", "length", 0.5, min=0.01, group="Strategy",
                  help="Outlines are simplified this much; parts may end up this much further apart"),
            Param("max_points", "Outline points per part", "int", 40, min=0, max=5000, group="Strategy",
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
        for name, w, h in SHEET_PRESETS:
            presets.addItem(name, (w, h))
        l1.addWidget(presets)
        # everything is remembered but the sheet size, which is the current sheet's unless it is changed here
        self.memo = [p for p in params if p.name not in ("width", "height")]
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
        manage = QPushButton("Manage stock...")
        manage.setToolTip("Add leftovers of any shape, sheet sizes and quantities")
        manage.clicked.connect(self._manage_stock)
        l2.addWidget(manage)
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
    def _build_stock(self) -> None:
        if self.stock_table is not None:
            self.stock_holder.removeWidget(self.stock_table)
            self.stock_table.deleteLater()
        stock = self.ctrl.project.stock
        self.stock_table = StockTable(self.ctrl, list(stock), checkable=True)
        label = "Plates of the stock (leftovers are used first):" if stock else \
            "The stock is empty: add leftovers of any shape below to nest onto them."
        if getattr(self, "stock_label", None) is None:
            self.stock_label = QLabel()
            self.stock_holder.addWidget(self.stock_label)
        self.stock_label.setText(label)
        self.stock_holder.addWidget(self.stock_table)

    def _manage_stock(self) -> None:
        if StockDialog(self.ctrl, self).exec():
            self._build_stock()

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
        """Nest; the outcome, or ``None`` when cancelled (nothing is changed then)."""
        v = self.form.values()
        squeezing = v["squeeze"] != "off"
        st = NestSettings(v["width"], v["height"], v["margin"], v["spacing"], self.rotations(v), v["gravity"],
                          iterations=v["iterations"], time_limit=float(v["time_limit"]), tolerance=v["tolerance"],
                          max_points=v["max_points"],
                          squeeze_angle=v["squeeze_angle"] if squeezing else 0.0,
                          squeeze_margin=v["squeeze_margin"] if squeezing else 0.0,
                          squeeze_step=v["squeeze_step"])
        quantities = {p.root.id: sp.value() for p, sp in zip(self.parts, self.qty)}
        part_rules = {p.root.id: ed.rule() for p, ed in zip(self.parts, self.rules)
                      if ed.rule()["mode"] != "inherit"}
        layer_rules = {lid: ed.rule() for lid, ed in zip(self.layer_ids, self.layer_rules)
                       if ed.rule()["mode"] != "default"}
        prog = QProgressDialog("Nesting parts...", "Cancel", 0, 100, self)
        prog.setWindowModality(Qt.WindowModal)
        prog.setMinimumDuration(300)
        prog.setAutoClose(False)
        prog.setAutoReset(False)
        state = {"k": 0, "n": 1, "cancel": threading.Event()}

        def progress(k, n):
            state["k"], state["n"] = k, n

        def choose(strict, better) -> bool:
            return v["squeeze"] == "auto" or self._ask_squeeze(self._suggestion_text(strict, better))

        ctrl = self.ctrl
        table = self.stock_table
        stock = [table.item(r) for r in range(len(table.items)) if table.used(r)]
        target = v["target"]
        use_sheets = [ctrl.sheet] if target == "current" else list(ctrl.project.sheets) if target == "all" else []
        # the rules are kept with the project, and stay when the nesting is undone
        ctrl.project.metadata["nesting"] = {"part_rules": part_rules, "layer_rules": layer_rules,
                                            "quantities": {k: n for k, n in quantities.items() if n != 1}}
        ctrl.settings.remember("nesting", v, self.memo)
        moving = {o.id for p in self.parts for o in p.objects}
        sheet = ctrl.sheet
        blank = target == "current" and not sheet.outline and all(o.id in moving for o in sheet.vectors)
        try:
            job = prepare_nesting(ctrl.project, self.objs, st, quantities, [], stock, use_sheets, v["new_sheets"],
                                  part_rules, layer_rules, v["tilt_step"], v["group_inner"], v["fill_holes"])
            # the long part runs in a thread: the program stays alive and Cancel works at once
            res = run_in_thread(lambda: run_nesting(job, progress, state["cancel"].is_set), prog, state)
        except NestCancelled:
            return None
        except clip.TooComplex as exc:    # (copies piled on one spot, on the plate or among the parts)
            QMessageBox.warning(self, "Nesting", f"This cannot be nested:\n{exc}")
            return None
        finally:
            prog.close()
        ctrl.checkpoint("Nesting")
        try:
            if blank:
                sheet.width, sheet.height = v["width"], v["height"]  # a blank sheet takes the size asked for
                job.settings.sheet_width, job.settings.sheet_height = v["width"], v["height"]
            out = apply_nesting(job, res, choose if squeezing else None)
            if v["use_up"]:
                self._use_up(out)
        except Exception:
            ctrl.undo_stack.rollback(ctrl.project)
            raise
        ctrl.notify()
        if out.sheets:
            ctrl.set_sheet(out.sheets[0].id)
        return out

    def _use_up(self, out) -> None:
        """Count the plates used down in the project's stock; used up ones leave it."""
        self.ctrl.project.stock = use_up(self.ctrl.project.stock, out.plates)


# ---------------------------------------------------------------------------
# G-code export
# ---------------------------------------------------------------------------

class ExportDialog(RemembersSize, QDialog):
    def __init__(self, ctrl, parent=None):
        super().__init__(parent)
        self.ctrl = ctrl
        self.setWindowTitle("Save G-code")
        lay = QVBoxLayout(self)
        form = QFormLayout()
        self.post = QComboBox()
        for p in sorted(ctrl.registry.posts.values(), key=lambda p: p.name.lower()):
            self.post.addItem(p.name, p.id)
        i = self.post.findData(ctrl.settings.last_post)
        self.post.setCurrentIndex(max(0, i))
        self.post.currentIndexChanged.connect(self.preview)
        form.addRow("Post-processor", self.post)
        self.sheets = QComboBox()
        self.sheets.addItem("Current sheet", "current")
        self.sheets.addItem("All sheets (one file per sheet)", "all")
        form.addRow("Sheets", self.sheets)
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
        if isinstance(mem.get("tile_len"), (int, float)) and not isinstance(mem.get("tile_len"), bool) \
                and mem["tile_len"] >= 1.0:
            self.tile_len.setValue(ctrl.units.to_display(float(mem["tile_len"])))
        for w in (self.sheets, self.tile, self.tile_axis, self.tile_len):
            w.blockSignals(False)
        self.desc = QLabel()
        self.desc.setWordWrap(True)
        lay.addWidget(self.desc)
        lay.addWidget(QLabel("Toolpaths (in machining order):"))
        self.list = QListWidget()
        for t in ctrl.project.toolpaths:
            it = QListWidgetItem(f"{t.name}  —  T{t.tool.number} {t.tool.name}")
            it.setData(Qt.UserRole, t.id)
            it.setFlags(it.flags() | Qt.ItemIsUserCheckable)
            it.setCheckState(Qt.Checked if t.enabled else Qt.Unchecked)
            self.list.addItem(it)
        self.list.itemChanged.connect(self.preview)
        self.list.setMaximumHeight(140)
        lay.addWidget(self.list)
        self.text = QPlainTextEdit()
        self.text.setReadOnly(True)
        self.text.setFont(QFontDatabase.systemFont(QFontDatabase.FixedFont))
        lay.addWidget(self.text, 1)
        bb = QDialogButtonBox()
        save = bb.addButton("Save...", QDialogButtonBox.AcceptRole)
        save.clicked.connect(self.save)
        cp = bb.addButton("Copy", QDialogButtonBox.ActionRole)
        cp.clicked.connect(lambda: QApplication.clipboard().setText(self.text.toPlainText()))
        bb.addButton(QDialogButtonBox.Close).clicked.connect(self.reject)
        lay.addWidget(bb)
        self.keep_size(ctrl, "export", 640, 720)
        self.preview()

    def current_post(self):
        return self.ctrl.registry.posts[self.post.currentData()]

    def chosen(self) -> set[str]:
        return {self.list.item(i).data(Qt.UserRole) for i in range(self.list.count())
                if self.list.item(i).checkState() == Qt.Checked}

    def toolpaths(self, sheet):
        ids = self.chosen()
        return [tp for tp in self.ctrl.sheet_toolpaths(sheet, enabled_only=False) if tp.def_id in ids]

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
        jobs = self.jobs(self.ctrl.sheet)
        if not jobs:
            self.text.setPlainText("(no calculated toolpaths on this sheet)")
            return
        files = []
        try:
            for tile, sheet, tps in jobs:
                files += [(tile + sfx, text) for sfx, text in post_toolpaths(self.ctrl.project, sheet, tps, post,
                                                                            "preview" + tile)]
        except Exception as exc:  # broken user posts must not take the app down
            self.text.setPlainText(f"Post-processor error:\n{type(exc).__name__}: {exc}")
            return
        out = []
        for suffix, text in files:
            if len(files) > 1:
                out.append(f";---------- file *{suffix}.{post.extension} ----------")
            out.append(text.replace("\r\n", "\n"))
        self.text.setPlainText("\n".join(out))

    def save(self) -> None:
        from ..post.export import write_gcode
        post = self.current_post()
        ctrl = self.ctrl
        proj = ctrl.project
        name = proj.path.stem if proj.path else proj.name
        fn, _ = save_file(self, ctrl.settings, "gcode", "Save G-code", f"{name}.{post.extension}",
                          f"{post.name} (*.{post.extension});;All files (*)",
                          str(proj.path.parent) if proj.path else str(Path.home()))
        if not fn:
            return
        sheets = [ctrl.sheet] if self.sheets.currentData() == "current" else ctrl.project.sheets
        written = []
        for i, s in enumerate(sheets):
            path = Path(fn)
            if len(sheets) > 1:
                path = path.with_name(f"{path.stem}_{i + 1}{path.suffix}")
            try:
                for tile, sheet, tps in self.jobs(s):
                    written += write_gcode(path.with_name(path.stem + tile + path.suffix), ctrl.project, sheet,
                                           tps, post)
            except Exception as exc:
                QMessageBox.warning(self, "Save G-code", f"{type(exc).__name__}: {exc}")
                return
        ctrl.settings.last_post = post.id
        self._remember()
        ctrl.message.emit("Saved " + ", ".join(p.name for p in written) if written else "Nothing to save")
        self.accept()

    def _remember(self) -> None:
        s, u = self.ctrl.settings, self.ctrl.units
        s.remember("export_gcode", {"sheets": self.sheets.currentData(), "tile": self.tile.isChecked(),
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


POST_TEMPLATE = """# Custom post-processor.  Every option / block of the parent post can be overridden.
# Documentation: docs/postprocessors.md
id = "{id}"
extends = "{base}"
name = "{name}"
description = "Based on {base_name}"

[options]
# decimals = 3
# line_numbers = true
# arcs = "ijk"          # ijk | r | none

[blocks]
# header = \"\"\"
# %
# ({{file_name}})
# G90 G17 {{unit_code}}
# \"\"\"
"""


class PostEditorDialog(RemembersSize, QDialog):
    """Create and edit user post-processors (TOML) with a live preview."""

    def __init__(self, ctrl, parent=None):
        super().__init__(parent)
        self.ctrl = ctrl
        self.setWindowTitle("Post-processors")
        lay = QHBoxLayout(self)
        left = QVBoxLayout()
        self.list = QListWidget()
        self.list.currentRowChanged.connect(self._load)
        left.addWidget(self.list)
        for text, slot in (("New from selected...", self.new), ("Open folder", self.open_folder),
                           ("Reload posts", self.reload)):
            b = QPushButton(text)
            b.clicked.connect(slot)
            left.addWidget(b)
        lay.addLayout(left, 1)
        right = QVBoxLayout()
        self.path_label = QLabel()
        right.addWidget(self.path_label)
        self.edit = QPlainTextEdit()
        self.edit.setFont(QFontDatabase.systemFont(QFontDatabase.FixedFont))
        right.addWidget(self.edit, 2)
        row = QHBoxLayout()
        self.save_btn = QPushButton("Save && test")
        self.save_btn.clicked.connect(self.save)
        row.addWidget(self.save_btn)
        row.addStretch(1)
        close = QPushButton("Close")
        close.clicked.connect(self.accept)
        row.addWidget(close)
        right.addLayout(row)
        self.out = QPlainTextEdit()
        self.out.setReadOnly(True)
        self.out.setFont(QFontDatabase.systemFont(QFontDatabase.FixedFont))
        right.addWidget(self.out, 1)
        lay.addLayout(right, 3)
        self.refresh()
        self.keep_size(ctrl, "posts", 980, 680)

    def refresh(self, select: str | None = None) -> None:
        from ..builtins import USER_POST_ERRORS
        self.list.blockSignals(True)
        self.list.clear()
        self.posts = sorted(self.ctrl.registry.posts.values(), key=lambda p: p.name.lower())
        self.broken = sorted(USER_POST_ERRORS.items())   # files that could not be loaded, still to be mended here
        row = 0
        for i, p in enumerate(self.posts):
            user = p.path is not None and paths.posts_dir() in Path(p.path).parents
            it = QListWidgetItem(("✎ " if user else "") + p.name)
            self.list.addItem(it)
            if p.id == select:
                row = i
        for i, (path, _) in enumerate(self.broken):
            self.list.addItem(QListWidgetItem(f"⚠ {Path(path).name} (cannot be loaded)"))
            if select is not None and Path(path).stem == select:
                row = len(self.posts) + i
        self.list.blockSignals(False)
        self.list.setCurrentRow(row)

    def _load_broken(self, path: str, error: str) -> None:
        """A post file that could not be loaded: its text can be edited, and the mistake is shown."""
        self.current = None
        self.broken_path = Path(path)
        self.path_label.setText(f"{path}   [cannot be loaded]")
        try:
            self.edit.setPlainText(Path(path).read_text(encoding="utf-8"))
        except OSError as exc:
            self.edit.setPlainText(f"# {exc}")
        self.edit.setReadOnly(False)
        self.save_btn.setEnabled(True)
        self.out.setPlainText(f"This post-processor cannot be loaded:\n{error}\n\nCorrect it and save.")

    def _load(self, row: int) -> None:
        if len(self.posts) <= row < len(self.posts) + len(self.broken):
            self._load_broken(*self.broken[row - len(self.posts)])
            return
        if not 0 <= row < len(self.posts):
            return
        self.broken_path = None
        p = self.posts[row]
        self.current = p
        user = p.path is not None and paths.posts_dir() in Path(p.path).parents
        self.path_label.setText(str(p.path or "(defined by a plugin)") + ("" if user else "   [read only]"))
        text = Path(p.path).read_text(encoding="utf-8") if p.path and Path(p.path).exists() else \
            json.dumps(p.definition, indent=2)
        self.edit.setPlainText(text)
        self.edit.setReadOnly(not user)
        self.save_btn.setEnabled(user)
        self._test(p)

    def _test(self, post) -> None:
        from ..post.export import post_toolpaths
        tps = self.ctrl.sheet_toolpaths()
        if not tps:
            self.out.setPlainText("(calculate a toolpath to see a preview)")
            return
        try:
            files = post_toolpaths(self.ctrl.project, self.ctrl.sheet, tps, post, "preview")
            self.out.setPlainText("\n".join(t for _, t in files).replace("\r\n", "\n")[:20000])
        except Exception as exc:
            self.out.setPlainText(f"Error: {type(exc).__name__}: {exc}")

    def new(self) -> None:
        base = getattr(self, "current", None)
        if base is None:
            return
        name, ok = QInputDialog.getText(self, "New post-processor", "Name:", text=base.name + " (custom)")
        if not ok or not name:
            return
        pid = "".join(ch if ch.isalnum() else "_" for ch in name.lower()).strip("_") or "custom"
        f = paths.ensure(paths.posts_dir()) / f"{pid}.toml"
        f.write_text(POST_TEMPLATE.format(id=pid, base=base.id, name=name.replace('"', "'"),
                                          base_name=base.name), encoding="utf-8")
        self.reload(select=pid)

    def save(self) -> None:
        broken = getattr(self, "broken_path", None)
        if broken is not None:
            broken.write_text(self.edit.toPlainText(), encoding="utf-8")
            self.reload(select=broken.stem)
            return
        p = self.current
        if p is None:
            return
        Path(p.path).write_text(self.edit.toPlainText(), encoding="utf-8")
        self.reload(select=p.id)

    def reload(self, select: str | None = None) -> None:
        from ..builtins import load_user_posts
        reg = self.ctrl.registry
        reg.remove_source("user-posts")
        try:
            load_user_posts(reg)
        except Exception as exc:  # noqa: BLE001
            QMessageBox.warning(self, "Post-processors", f"{type(exc).__name__}: {exc}")
        self.refresh(select if isinstance(select, str) else None)

    def open_folder(self) -> None:
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(paths.ensure(paths.posts_dir()))))


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
        if dxf:
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
            Param("theme", "Appearance", "choice", s.theme,
                  choices=[("system", "Follow the system"), ("light", "Light"), ("dark", "Dark")]),
            Param("grid", "Grid spacing (mm)", "float", s.grid, min=0.0),
            Param("snap_grid", "Snap to grid", "bool", s.snap_grid),
            Param("snap_objects", "Snap to vectors", "bool", s.snap_objects),
            Param("auto_recalculate", "Recalculate toolpaths automatically", "bool", s.auto_recalculate),
            Param("show_directions", "Show direction arrows", "bool", s.show_directions),
            Param("sim_resolution", "Simulation resolution (mm, 0 = auto)", "float", s.sim_resolution, min=0.0),
        ]
        lay = QVBoxLayout(self)
        self.form = ParamForm(params, None, ctrl.units)
        lay.addWidget(self.form)
        lay.addWidget(buttons(self))

    def accept(self) -> None:
        v = self.form.values()
        s = self.ctrl.settings
        for k, val in v.items():
            if k not in ("units", "theme"):
                setattr(s, k, val)
        self.ctrl.set_units(v["units"])
        window = self.parent()
        if hasattr(window, "set_theme"):
            window.set_theme(v["theme"])
        else:
            s.theme = v["theme"]
        s.save()
        super().accept()

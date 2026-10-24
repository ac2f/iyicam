"""Dialogs: commands, sheets, material, tools, toolpaths, nesting, export, plugins, posts, text."""
from __future__ import annotations

import copy
import json
import shutil
from pathlib import Path

from PySide6.QtCore import Qt, QUrl
from PySide6.QtGui import QDesktopServices, QFont, QFontDatabase
from PySide6.QtWidgets import (QAbstractItemView, QApplication, QButtonGroup, QCheckBox, QComboBox, QDialog,
                               QDialogButtonBox, QDoubleSpinBox, QFileDialog, QFontComboBox, QFormLayout,
                               QHBoxLayout, QHeaderView, QInputDialog, QLabel, QLineEdit, QListWidget,
                               QListWidgetItem, QMessageBox, QPlainTextEdit, QProgressDialog, QPushButton,
                               QRadioButton, QScrollArea, QSpinBox, QSplitter, QTableWidget, QTableWidgetItem,
                               QTabWidget, QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget)

from ..core import paths
from ..core.document import XY_ORIGINS, Selection, ToolpathDef
from ..core.params import Param
from ..core.tooling import TOOL_TYPES, Tool
from .forms import ParamForm


def buttons(dlg: QDialog, ok_text: str = "OK") -> QDialogButtonBox:
    bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
    bb.button(QDialogButtonBox.Ok).setText(ok_text)
    bb.accepted.connect(dlg.accept)
    bb.rejected.connect(dlg.reject)
    return bb


def scroll(widget: QWidget) -> QScrollArea:
    sa = QScrollArea()
    sa.setWidgetResizable(True)
    sa.setWidget(widget)
    sa.setFrameShape(QScrollArea.NoFrame)
    return sa


# ---------------------------------------------------------------------------
# generic command dialog
# ---------------------------------------------------------------------------

class CommandDialog(QDialog):
    last_values: dict[str, dict] = {}

    def __init__(self, ctrl, cmd, parent=None):
        super().__init__(parent)
        self.cmd = cmd
        self.setWindowTitle(cmd.label.rstrip("."))
        lay = QVBoxLayout(self)
        if cmd.tooltip:
            lab = QLabel(cmd.tooltip)
            lab.setWordWrap(True)
            lay.addWidget(lab)
        self.form = ParamForm(cmd.params, self.last_values.get(cmd.id), ctrl.units, ctrl.tools)
        lay.addWidget(scroll(self.form))
        lay.addWidget(buttons(self))
        self.resize(380, min(640, 120 + 34 * len(cmd.params)))

    def values(self) -> dict:
        v = self.form.values()
        self.last_values[self.cmd.id] = v
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
        self.ctrl.sheet_changed.emit()
        super().accept()


class MaterialDialog(QDialog):
    def __init__(self, ctrl, parent=None):
        super().__init__(parent)
        self.ctrl = ctrl
        m = ctrl.project.material
        s = ctrl.sheet
        self.setWindowTitle("Material Setup")
        origin_labels = {o: o.replace("_", " ").title() for o in XY_ORIGINS}
        params = [
            Param("width", "Sheet width (X)", "length", s.width, min=1.0, group="Sheet"),
            Param("height", "Sheet height (Y)", "length", s.height, min=1.0, group="Sheet"),
            Param("thickness", "Material thickness", "length", m.thickness, min=0.01, group="Material"),
            Param("z_zero", "Z zero", "choice", m.z_zero,
                  choices=[("top", "Top of the material"), ("bottom", "Bottom (machine bed)")], group="Material"),
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
        ]
        lay = QVBoxLayout(self)
        self.form = ParamForm(params, None, ctrl.units)
        lay.addWidget(scroll(self.form))
        lay.addWidget(buttons(self))
        self.resize(420, 560)

    def accept(self) -> None:
        v = self.form.values()
        with self.ctrl.edit("Material setup"):
            m = self.ctrl.project.material
            m.thickness, m.z_zero, m.xy_origin = v["thickness"], v["z_zero"], v["xy_origin"]
            m.safe_z, m.clearance, m.rapid_rate = v["safe_z"], v["clearance"], v["rapid_rate"]
            m.home = (v["home_x"], v["home_y"], v["home_z"])
            self.ctrl.sheet.width, self.ctrl.sheet.height = v["width"], v["height"]
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
        self.form = ParamForm(params, None, ctrl.units)
        lay.addWidget(self.form)
        self.result_label = QLabel()
        self.result_label.setTextFormat(Qt.RichText)
        lay.addWidget(self.result_label)
        note = QLabel("<i>Starting values only — check the tool maker's recommendations.</i>")
        lay.addWidget(note)
        lay.addWidget(buttons(self, "Apply to tool"))
        self.form.changed.connect(self.update_result)
        self.update_result()

    def result(self):
        from ..core.feeds import suggest
        v = self.form.values()
        return suggest(v["material"], v["diameter"], v["flutes"], v["rpm"]), v

    def update_result(self) -> None:
        c, v = self.result()
        u = self.ctrl.units
        self.result_label.setText(
            f"Chip load <b>{c.chip_load:.3f} mm</b><br>Feed rate <b>{c.feed_rate:.0f} mm/min</b>, "
            f"plunge {c.plunge_rate:.0f} mm/min<br>Stepdown {u.fmt(c.stepdown)}, stepover {u.fmt(c.stepover)}"
            f"<br>Surface speed {c.surface_speed:.0f} m/min")

    def apply_to(self, t: Tool) -> Tool:
        c, v = self.result()
        t = t.copy()
        t.feed_rate, t.plunge_rate = round(c.feed_rate), round(c.plunge_rate)
        t.stepdown, t.stepover = round(c.stepdown, 2), round(c.stepover, 2)
        t.spindle_speed, t.flutes = int(v["rpm"]), int(v["flutes"])
        return t


class ToolDatabaseDialog(QDialog):
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
        self.resize(720, 620)

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
        fn, _ = QFileDialog.getOpenFileName(self, "Import tools", "", "Tool library (*.json)")
        if fn:
            from ..core.tooling import ToolLibrary
            try:
                lib = ToolLibrary.load(Path(fn))
            except (OSError, ValueError, TypeError) as exc:
                QMessageBox.warning(self, "Import tools", str(exc))
                return
            self._commit_editor()
            self.lib_tools += [t.copy(new_id=True) for t in lib.tools]
            self.refresh()

    def export(self) -> None:
        fn, _ = QFileDialog.getSaveFileName(self, "Export tools", "tools.json", "Tool library (*.json)")
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

class ToolpathDialog(QDialog):
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
        tool = self.tdef.tool if self.tdef else self._default_tool()
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
        self.all_sheets.setChecked(self.tdef is None or self.tdef.sheet_ids is None)
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
        values = dict(self.tdef.params) if self.tdef else {}
        params = self.op.params()
        for p in params:
            if p.name == "cut_depth" and not self.tdef and self.op.id == "profile":
                p.default = ctrl.project.material.thickness
        self.form = ParamForm(params, values, ctrl.units, ctrl.tools)
        tabs.addTab(scroll(self.form), "Settings")
        tabs.addTab(tool_tab, "Tool")
        tabs.addTab(vec_tab, "Vectors")
        lay.addWidget(buttons(self, "Calculate"))
        self._update_tool_label()
        self.tool_editor.form.changed.connect(self._update_tool_label)
        self.resize(460, 680)

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

    def result(self) -> ToolpathDef:
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
        super().accept()


# ---------------------------------------------------------------------------
# nesting
# ---------------------------------------------------------------------------

class NestingDialog(QDialog):
    def __init__(self, ctrl, parent=None):
        super().__init__(parent)
        from ..nesting.parts import group_parts
        self.ctrl = ctrl
        self.setWindowTitle("Nesting")
        objs = ctrl.selected_objects() or list(ctrl.sheet.vectors)
        self.objs = objs
        self.parts = group_parts(objs)
        s = ctrl.sheet
        tool_d = max((t.tool.diameter for t in ctrl.project.toolpaths if t.op == "profile"), default=6.0)
        params = [
            Param("width", "Sheet width", "length", s.width, min=1.0, group="Sheets"),
            Param("height", "Sheet height", "length", s.height, min=1.0, group="Sheets"),
            Param("margin", "Border gap", "length", 10.0, min=0.0, group="Sheets"),
            Param("spacing", "Gap between parts", "length", tool_d + 2.0, min=0.0, group="Sheets",
                  help="Usually the cutter diameter plus a little clearance"),
            Param("target", "Place on", "choice", "current",
                  choices=[("current", "Current sheet first, then new sheets"), ("new", "New sheets only")],
                  group="Sheets"),
            Param("rotation", "Rotation", "choice", "90",
                  choices=[("0", "No rotation"), ("180", "0 / 180°"), ("90", "90° steps"),
                           ("45", "45° steps"), ("15", "15° steps")], group="Strategy"),
            Param("gravity", "Pack towards", "choice", "left",
                  choices=[("left", "Left (X min)"), ("bottom", "Bottom (Y min)"), ("compact", "Smallest area")],
                  group="Strategy"),
            Param("iterations", "Optimisation attempts", "int", 1, min=1, max=200, group="Strategy"),
            Param("tolerance", "Outline tolerance", "length", 0.5, min=0.01, group="Strategy",
                  help="Outlines are simplified this much; parts may end up this much further apart"),
        ]
        lay = QVBoxLayout(self)
        presets = QComboBox()
        presets.addItem("Preset sheet sizes...")
        for name, w, h in SHEET_PRESETS:
            presets.addItem(name, (w, h))
        lay.addWidget(presets)
        self.form = ParamForm(params, None, ctrl.units)
        presets.activated.connect(lambda i: self._preset(presets.itemData(i)))
        lay.addWidget(self.form)
        lay.addWidget(QLabel(f"<b>{len(self.parts)}</b> part(s) found. Set the quantities:"))
        self.table = QTableWidget(len(self.parts), 3)
        self.table.setHorizontalHeaderLabels(["Part", "Size", "Quantity"])
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        u = ctrl.units
        self.qty: list[QSpinBox] = []
        for i, p in enumerate(self.parts):
            bb = p.root.bbox()
            name = p.root.name or f"Part {i + 1}"
            if p.members:
                name += f" (+{len(p.members)})"
            self.table.setItem(i, 0, QTableWidgetItem(name))
            self.table.setItem(i, 1, QTableWidgetItem(f"{u.fmt(bb.width)} x {u.fmt(bb.height)}"))
            sp = QSpinBox()
            sp.setRange(0, 10000)
            sp.setValue(1)
            self.table.setCellWidget(i, 2, sp)
            self.qty.append(sp)
        lay.addWidget(self.table)
        lay.addWidget(buttons(self, "Nest"))
        self.resize(480, 700)

    def _preset(self, wh) -> None:
        if wh:
            u = self.ctrl.units
            self.form.widgets["width"].setValue(u.to_display(wh[0]))
            self.form.widgets["height"].setValue(u.to_display(wh[1]))

    def run(self):
        from ..nesting.nest import NestSettings
        from ..nesting.parts import nest_objects
        v = self.form.values()
        step = float(v["rotation"])
        rots = [0.0] if step == 0 else [k * step for k in range(int(round(360 / step)))]
        st = NestSettings(v["width"], v["height"], v["margin"], v["spacing"], rots, v["gravity"],
                          iterations=v["iterations"], tolerance=v["tolerance"])
        quantities = {p.root.id: sp.value() for p, sp in zip(self.parts, self.qty)}
        total = sum(quantities.values())
        prog = QProgressDialog("Nesting parts...", "Cancel", 0, max(1, total), self)
        prog.setWindowModality(Qt.WindowModal)
        prog.setMinimumDuration(300)

        def progress(k, n):
            prog.setMaximum(max(1, n))
            prog.setValue(k)
            QApplication.processEvents()

        ctrl = self.ctrl
        targets = [ctrl.sheet] if v["target"] == "current" else []
        ctrl.checkpoint("Nesting")
        try:
            out = nest_objects(ctrl.project, self.objs, st, quantities, targets, progress, prog.wasCanceled)
        except Exception:
            ctrl.undo_stack.rollback(ctrl.project)
            raise
        finally:
            prog.close()
        ctrl.notify()
        if out.sheets:
            ctrl.set_sheet(out.sheets[0].id)
        return out


# ---------------------------------------------------------------------------
# G-code export
# ---------------------------------------------------------------------------

class ExportDialog(QDialog):
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
        lay.addLayout(form)
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
        self.resize(640, 720)
        self.preview()

    def current_post(self):
        return self.ctrl.registry.posts[self.post.currentData()]

    def chosen(self) -> set[str]:
        return {self.list.item(i).data(Qt.UserRole) for i in range(self.list.count())
                if self.list.item(i).checkState() == Qt.Checked}

    def toolpaths(self, sheet):
        ids = self.chosen()
        return [tp for tp in self.ctrl.sheet_toolpaths(sheet, enabled_only=False) if tp.def_id in ids]

    def preview(self, *args) -> None:
        from ..post.export import post_toolpaths
        post = self.current_post()
        self.desc.setText(f"{post.description}  (*.{post.extension})")
        tps = self.toolpaths(self.ctrl.sheet)
        if not tps:
            self.text.setPlainText("(no calculated toolpaths on this sheet)")
            return
        try:
            files = post_toolpaths(self.ctrl.project, self.ctrl.sheet, tps, post, "preview")
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
        base = ctrl.project.path.with_suffix("") if ctrl.project.path else Path.home() / ctrl.project.name
        fn, _ = QFileDialog.getSaveFileName(self, "Save G-code", f"{base}.{post.extension}",
                                            f"{post.name} (*.{post.extension});;All files (*)")
        if not fn:
            return
        sheets = [ctrl.sheet] if self.sheets.currentData() == "current" else ctrl.project.sheets
        written = []
        for i, s in enumerate(sheets):
            tps = self.toolpaths(s)
            if not tps:
                continue
            path = Path(fn)
            if len(sheets) > 1:
                path = path.with_name(f"{path.stem}_{i + 1}{path.suffix}")
            try:
                written += write_gcode(path, ctrl.project, s, tps, post)
            except Exception as exc:
                QMessageBox.warning(self, "Save G-code", f"{type(exc).__name__}: {exc}")
                return
        ctrl.settings.last_post = post.id
        ctrl.message.emit("Saved " + ", ".join(p.name for p in written) if written else "Nothing to save")
        self.accept()


# ---------------------------------------------------------------------------
# plugins / posts
# ---------------------------------------------------------------------------

class PluginsDialog(QDialog):
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
        self.resize(640, 460)

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
        self.refresh()
        if hasattr(self.parent(), "rebuild_plugin_ui"):
            self.parent().rebuild_plugin_ui()

    def install(self) -> None:
        fn, _ = QFileDialog.getOpenFileName(self, "Install plugin", "", "Python plugin (*.py)")
        if fn:
            dst = paths.ensure(paths.plugins_dir()) / Path(fn).name
            shutil.copy(fn, dst)
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


class PostEditorDialog(QDialog):
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
        self.resize(980, 680)

    def refresh(self, select: str | None = None) -> None:
        self.list.blockSignals(True)
        self.list.clear()
        self.posts = sorted(self.ctrl.registry.posts.values(), key=lambda p: p.name.lower())
        row = 0
        for i, p in enumerate(self.posts):
            user = p.path is not None and paths.posts_dir() in Path(p.path).parents
            it = QListWidgetItem(("✎ " if user else "") + p.name)
            self.list.addItem(it)
            if p.id == select:
                row = i
        self.list.blockSignals(False)
        self.list.setCurrentRow(row)

    def _load(self, row: int) -> None:
        if not 0 <= row < len(self.posts):
            return
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
        p = self.current
        Path(p.path).write_text(self.edit.toPlainText(), encoding="utf-8")
        self.reload(select=p.id)

    def reload(self, select: str | None = None) -> None:
        from ..builtins import load_user_posts
        reg = self.ctrl.registry
        reg.remove_source("user-posts")
        try:
            load_user_posts(reg)
        except Exception as exc:
            QMessageBox.warning(self, "Post-processors", f"{type(exc).__name__}: {exc}")
        self.refresh(select)

    def open_folder(self) -> None:
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(paths.ensure(paths.posts_dir()))))


# ---------------------------------------------------------------------------
# text / import / preferences
# ---------------------------------------------------------------------------

class TextDialog(QDialog):
    last = {"text": "iyiCAM", "font": "", "height": 30.0, "bold": False, "italic": False, "align": "left",
            "spacing": 0.0}

    def __init__(self, ctrl, parent=None):
        super().__init__(parent)
        self.ctrl = ctrl
        self.setWindowTitle("Text")
        lay = QVBoxLayout(self)
        form = QFormLayout()
        L = self.last
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
        TextDialog.last = v
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
        lay = QVBoxLayout(self)
        self.form = ParamForm(params, None, ctrl.units)
        lay.addWidget(self.form)
        lay.addWidget(buttons(self, "Import"))

    def values(self) -> dict:
        return self.form.values()


class PreferencesDialog(QDialog):
    def __init__(self, ctrl, parent=None):
        super().__init__(parent)
        self.ctrl = ctrl
        s = ctrl.settings
        self.setWindowTitle("Preferences")
        params = [
            Param("units", "Display units", "choice", s.units, choices=[("mm", "Millimetres"), ("inch", "Inches")]),
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
            if k != "units":
                setattr(s, k, val)
        self.ctrl.set_units(v["units"])
        s.save()
        super().accept()

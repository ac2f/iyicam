"""The windows of the packages of toolpaths (:mod:`iyicam.cam.tp_packages`): make and change packages, and put one on the
vectors that are selected."""
from __future__ import annotations

import copy
import json
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QCheckBox, QDialog, QDialogButtonBox, QFormLayout, QGroupBox, QHBoxLayout, QInputDialog,
                               QLabel, QLineEdit, QListWidget, QListWidgetItem, QMenu, QMessageBox, QPushButton, QSplitter,
                               QToolButton, QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget)

from ..cam import tp_packages as P
from ..core.document import Selection, Tool, _jsonable
from .dialogs import RemembersSize, ToolDatabaseDialog, scroll
from .files import open_file, save_file
from .forms import ParamForm


def variables_button(edit: QLineEdit, toolpath_vars: bool = True) -> QToolButton:
    """A {…} button that puts a variable of the package (and of the toolpath) in a name."""
    b = QToolButton()
    b.setText("{…}")
    b.setToolTip("Put a variable in the name: the package's are put in when the toolpaths are made, the toolpath's follow it")
    b.setPopupMode(QToolButton.InstantPopup)
    m = QMenu(b)

    def put(text: str) -> None:
        edit.insert(text)
        edit.setFocus()

    for name, en, _ in P.VARIABLES:
        m.addAction(f"{{{name}}}  {en}", lambda n=name: put("{" + n + "}"))
    m.addAction("{n:02}  The next number with two digits (01, 02 ...)", lambda: put("{n:02}"))
    if toolpath_vars:
        m.addSeparator()
        from ..post.naming import TOOLPATH_VARIABLES
        for v in TOOLPATH_VARIABLES:
            m.addAction(f"{{{v['name']}}}  {v['text']['en']}", lambda n=v["name"]: put("{" + n + "}"))
    b.setMenu(m)
    return b


def pick_tool(ctrl, parent, current: dict | None, title: str) -> Tool | None:
    dlg = ToolDatabaseDialog(ctrl, parent, pick=True, select_id=(current or {}).get("id"), title=title)
    return dlg.selected_tool() if dlg.exec() else None


def tool_text(d: dict | None) -> str:
    return Tool.from_dict(d).describe() if d else "(none)"


# ------------------------------------------------------------------------------------------------ one toolpath of a package
class PackageItemDialog(QDialog):
    """A toolpath of a package: its name, its tool (the package's or its own), its settings, what is asked when it is used."""

    def __init__(self, ctrl, item: dict, package_tool: dict | None, parent=None):
        super().__init__(parent)
        self.ctrl = ctrl
        self.item = copy.deepcopy(item)
        self.op = ctrl.registry.operations[item["op"]]
        self.setWindowTitle(f"{self.op.label} in the package")
        self.resize(480, 680)
        lay = QVBoxLayout(self)
        top = QFormLayout()
        self.name = QLineEdit(item["name"])
        row = QHBoxLayout()
        row.addWidget(self.name, 1)
        row.addWidget(variables_button(self.name))
        top.addRow("Name", row)
        self.own = QCheckBox("A tool of its own (not the package's)")
        self.own.setChecked(item["tool"] is not None)
        self.tool_label = QLabel()
        tb = QPushButton("Choose tool...")
        tb.clicked.connect(self._choose)
        trow = QHBoxLayout()
        trow.addWidget(self.tool_label, 1)
        trow.addWidget(tb)
        top.addRow(self.own)
        top.addRow("Tool", trow)
        self.layers = QLineEdit(", ".join(item.get("layers") or []))
        self.layers.setToolTip("When the package is put on layers: the layers this toolpath machines (names, with commas). "
                               "Empty: the layers chosen when it is used")
        lb = QToolButton()
        lb.setText("▾")
        lb.setPopupMode(QToolButton.InstantPopup)
        lm = QMenu(lb)
        for l in ctrl.project.layers:
            lm.addAction(l.name, lambda n=l.name: self.layers.setText(
                ", ".join(dict.fromkeys([x.strip() for x in self.layers.text().split(",") if x.strip()] + [n]))))
        lb.setMenu(lm)
        lrow = QHBoxLayout()
        lrow.addWidget(self.layers, 1)
        lrow.addWidget(lb)
        top.addRow("Layers (on layers)", lrow)
        self.package_tool = package_tool
        self.own.toggled.connect(self._show_tool)
        lay.addLayout(top)
        params = self.op.params()
        values = {}
        from ..core.document import _unjson
        for p in params:
            if p.name in item["params"]:
                values[p.name] = _unjson(item["params"][p.name])
        self.form = ParamForm(params, values, ctrl.units, ctrl.tools, collapse=True)
        lay.addWidget(QLabel("<b>Settings</b>"))
        lay.addWidget(scroll(self.form), 3)
        lay.addWidget(QLabel("<b>Asked when the package is used</b> (changed for that time):"))
        self.ask = QListWidget()
        for p in params:
            if p.kind in ("tool", "points") or p.name == "tab_points":
                continue
            it = QListWidgetItem(f"{p.label}  ({p.name})")
            it.setData(Qt.UserRole, p.name)
            it.setFlags(it.flags() | Qt.ItemIsUserCheckable)
            it.setCheckState(Qt.Checked if p.name in item["ask"] else Qt.Unchecked)
            self.ask.addItem(it)
        lay.addWidget(self.ask, 2)
        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)
        lay.addWidget(bb)
        self._show_tool()

    def _show_tool(self, *_) -> None:
        d = self.item["tool"] if self.own.isChecked() else self.package_tool
        self.tool_label.setText(tool_text(d) + ("" if self.own.isChecked() else "  (the package's)"))

    def _choose(self) -> None:
        t = pick_tool(self.ctrl, self, self.item["tool"] or self.package_tool, "Tool of this toolpath")
        if t is not None:
            self.item["tool"] = t.to_dict()
            self.own.setChecked(True)
            self._show_tool()

    def result_item(self) -> dict:
        it = self.item
        it["name"] = self.name.text().strip() or self.op.label
        if not self.own.isChecked():
            it["tool"] = None
        elif it["tool"] is None:
            it["tool"] = copy.deepcopy(self.package_tool)
        it["params"] = {k: _jsonable(v) for k, v in self.form.values().items()}
        it["ask"] = [self.ask.item(i).data(Qt.UserRole) for i in range(self.ask.count())
                     if self.ask.item(i).checkState() == Qt.Checked]
        it["layers"] = list(dict.fromkeys(x.strip() for x in self.layers.text().split(",") if x.strip()))
        return it


# ------------------------------------------------------------------------------------------------ the packages
class PackagesDialog(RemembersSize, QDialog):
    """Make, change and remove packages of toolpaths; put one on the selection."""

    def __init__(self, ctrl, parent=None, select_id: str | None = None):
        super().__init__(parent)
        self.ctrl = ctrl
        self.setWindowTitle("Toolpath Packages")
        self.pkgs: list[dict] = P.load()
        self.cur: int | None = None
        self.to_apply: str | None = None
        lay = QVBoxLayout(self)
        split = QSplitter()
        left = QWidget()
        ll = QVBoxLayout(left)
        ll.setContentsMargins(0, 0, 0, 0)
        self.list = QListWidget()
        self.list.currentRowChanged.connect(self._picked)
        ll.addWidget(self.list, 1)
        for text, slot in (("New...", self.new), ("Duplicate", self.duplicate), ("Delete", self.delete),
                           ("Import...", self.import_), ("Export...", self.export)):
            b = QPushButton(text)
            b.clicked.connect(slot)
            ll.addWidget(b)
        split.addWidget(left)
        right = QWidget()
        rl = QVBoxLayout(right)
        rl.setContentsMargins(0, 0, 0, 0)
        form = QFormLayout()
        self.name = QLineEdit()
        form.addRow("Name", self.name)
        self.tool_label = QLabel()
        tb = QPushButton("Change tool...")
        tb.setToolTip("The tool the toolpaths of the package use (each can have its own); when the package is used another "
                      "tool can be chosen for that time")
        tb.clicked.connect(self.change_tool)
        trow = QHBoxLayout()
        trow.addWidget(self.tool_label, 1)
        trow.addWidget(tb)
        form.addRow("Tool", trow)
        self.make_group = QCheckBox("Put the toolpaths in a new toolpath group")
        form.addRow(self.make_group)
        self.group_name = QLineEdit()
        self.group_name.setToolTip("{package}, {sheet_number}, {sheet_name}, {n}: the next number ({n:02}: 01, 02 ...)")
        grow = QHBoxLayout()
        grow.addWidget(self.group_name, 1)
        grow.addWidget(variables_button(self.group_name, toolpath_vars=False))
        form.addRow("Group name", grow)
        self.group_shown = QLabel()
        self.group_shown.setStyleSheet("color: gray")
        form.addRow("", self.group_shown)
        self.this_sheet = QCheckBox("Only on the sheet that is open (else on every sheet)")
        form.addRow(self.this_sheet)
        rl.addLayout(form)
        rl.addWidget(QLabel("<b>Toolpaths of the package</b>"))
        self.items = QTreeWidget()
        self.items.setHeaderLabels(["Name", "Type", "Tool", "Asked"])
        self.items.setColumnWidth(0, 180)
        self.items.itemDoubleClicked.connect(lambda *_: self.edit_item())
        rl.addWidget(self.items, 1)
        irow = QHBoxLayout()
        add = QPushButton("Add toolpath")
        menu = QMenu(add)
        for op in ctrl.registry.operations.values():
            menu.addAction(op.label, lambda o=op.id: self.add_item(o))
        add.setMenu(menu)
        irow.addWidget(add)
        for text, slot in (("From selected toolpaths", self.add_selected), ("Edit...", self.edit_item),
                           ("Remove", self.remove_item), ("Up", lambda: self.move_item(-1)), ("Down", lambda: self.move_item(1))):
            b = QPushButton(text)
            b.clicked.connect(slot)
            irow.addWidget(b)
        rl.addLayout(irow)
        split.addWidget(right)
        split.setSizes([200, 560])
        lay.addWidget(split, 1)
        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        bb.button(QDialogButtonBox.Ok).setText("Save")
        self.apply_b = bb.addButton("Save and use on the selection...", QDialogButtonBox.AcceptRole)
        self.apply_b.clicked.connect(lambda: setattr(self, "to_apply", self.current()["id"] if self.current() else None))
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)
        lay.addWidget(bb)
        for w in (self.name, self.group_name):
            w.textChanged.connect(self._show_group)
        self.make_group.toggled.connect(self._show_group)
        self._fill(select_id)
        self.keep_size(ctrl, "packages", 820, 560)

    # -- the list -------------------------------------------------------------------------------------------------------
    def _fill(self, select_id: str | None = None) -> None:
        self.list.blockSignals(True)
        self.list.clear()
        for p in self.pkgs:
            self.list.addItem(p["name"])
        self.list.blockSignals(False)
        row = next((i for i, p in enumerate(self.pkgs) if p["id"] == select_id), 0 if self.pkgs else -1)
        self.cur = None
        self.list.setCurrentRow(row)
        if row < 0:
            self._show(None)

    def current(self) -> dict | None:
        return self.pkgs[self.cur] if self.cur is not None and 0 <= self.cur < len(self.pkgs) else None

    def _commit(self) -> None:
        p = self.current()
        if p is None:
            return
        p["name"] = self.name.text().strip() or p["name"]
        p["make_group"] = self.make_group.isChecked()
        p["group_name"] = self.group_name.text() or P.DEFAULT_GROUP
        p["this_sheet"] = self.this_sheet.isChecked()
        item = self.list.item(self.cur)
        if item is not None:
            item.setText(p["name"])

    def _picked(self, row: int) -> None:
        self._commit()
        self.cur = row if row >= 0 else None
        self._show(self.current())

    def _show(self, p: dict | None) -> None:
        on = p is not None
        for w in (self.name, self.make_group, self.group_name, self.this_sheet, self.items, self.apply_b):
            w.setEnabled(on)
        if not on:
            self.name.clear()
            self.items.clear()
            self.tool_label.setText("")
            return
        self.name.setText(p["name"])
        self.make_group.setChecked(p["make_group"])
        self.group_name.setText(p["group_name"])
        self.this_sheet.setChecked(p["this_sheet"])
        self.tool_label.setText(tool_text(p["tool"]))
        self._fill_items()
        self._show_group()

    def _show_group(self, *_) -> None:
        p = self.current()
        if p is None:
            return
        self.group_name.setEnabled(self.make_group.isChecked())
        if not self.make_group.isChecked():
            self.group_shown.setText("(no group)")
            return
        probe = {**p, "name": self.name.text().strip() or p["name"]}
        try:
            name, _ = P.group_name(probe, self.ctrl.project, self.ctrl.sheet, self.group_name.text() or P.DEFAULT_GROUP)
            self.group_shown.setText(f"→ {name}  (on this sheet, now)")
        except Exception:  # noqa: BLE001  (half typed)
            self.group_shown.setText("")

    def _fill_items(self) -> None:
        p = self.current()
        self.items.clear()
        for it in p["items"]:
            op = self.ctrl.registry.operations.get(it["op"])
            row = QTreeWidgetItem([it["name"], op.label if op else it["op"],
                                   Tool.from_dict(it["tool"]).label if it["tool"] else "(package's)", ", ".join(it["ask"])])
            if it.get("layers"):
                row.setToolTip(0, "On layers: " + ", ".join(it["layers"]))
            self.items.addTopLevelItem(row)

    # -- packages ---------------------------------------------------------------------------------------------------------
    def new(self) -> None:
        name, ok = QInputDialog.getText(self, "New package", "Name of the package:")
        if not ok or not name.strip():
            return
        tool = pick_tool(self.ctrl, self, None, "Tool of the package")
        if tool is None:
            return
        self._commit()
        p = P.new(name.strip(), tool)
        self.pkgs.append(p)
        self._fill(p["id"])

    def duplicate(self) -> None:
        p = self.current()
        if p is None:
            return
        self._commit()
        q = copy.deepcopy(p)
        q["id"] = P.check({**q, "id": ""})["id"]
        q["name"] = p["name"] + " copy"
        self.pkgs.append(q)
        self._fill(q["id"])

    def delete(self) -> None:
        p = self.current()
        if p is None or QMessageBox.question(self, "Delete package", f"Delete the package '{p['name']}'?") != QMessageBox.Yes:
            return
        del self.pkgs[self.cur]
        self.cur = None
        self._fill()

    def import_(self) -> None:
        fn = open_file(self, self.ctrl.settings, "packages", "Import packages", "Toolpath packages (*.icampkg *.json)")
        if not fn:
            return
        try:
            data = json.loads(Path(fn).read_text(encoding="utf-8"))
            got = [P.check({**d, "id": ""}) for d in (data.get("packages") if isinstance(data, dict) else data)]
        except (OSError, ValueError, TypeError, AttributeError, P.PackageError) as exc:
            QMessageBox.warning(self, "Import packages", f"These are not packages of toolpaths: {exc}")
            return
        self._commit()
        self.pkgs += got
        self._fill(got[0]["id"] if got else None)

    def export(self) -> None:
        p = self.current()
        if p is None:
            return
        self._commit()
        fn, _ = save_file(self, self.ctrl.settings, "packages", "Export package", P.export_name(p),
                          "Toolpath packages (*.icampkg)")
        if fn:
            Path(fn).write_text(json.dumps({"format": "iyicam-toolpath-packages", "packages": [p]}, indent=1,
                                           ensure_ascii=False), encoding="utf-8")

    def change_tool(self) -> None:
        p = self.current()
        if p is None:
            return
        t = pick_tool(self.ctrl, self, p["tool"], "Tool of the package")
        if t is not None:
            p["tool"] = t.to_dict()
            self.tool_label.setText(tool_text(p["tool"]))
            self._fill_items()

    # -- its toolpaths ----------------------------------------------------------------------------------------------------
    def _row(self) -> int:
        it = self.items.currentItem()
        return self.items.indexOfTopLevelItem(it) if it is not None else -1

    def add_item(self, op_id: str) -> None:
        p = self.current()
        if p is None:
            return
        op = self.ctrl.registry.operations[op_id]
        names = {q.name for q in op.params()}
        item = {"name": f"{op.label.split(' (')[0]} {len(p['items']) + 1}", "op": op_id, "params": {}, "tool": None,
                "ask": [a for a in P.DEFAULT_ASK if a in names], "safe_z": None, "clearance": None, "sequence": {},
                "layers": []}
        if p["tool"] is None:
            item["tool"] = Tool().to_dict()
        dlg = PackageItemDialog(self.ctrl, item, p["tool"], self)
        if dlg.exec():
            p["items"].append(dlg.result_item())
            self._fill_items()

    def add_selected(self) -> None:
        """The toolpaths selected in the toolpath list (or all of the project) become toolpaths of the package."""
        p = self.current()
        if p is None:
            return
        win = self.parent()
        ids = win.toolpaths_panel.selected_ids() if hasattr(win, "toolpaths_panel") else []
        tps = [t for t in self.ctrl.project.toolpaths if t.id in ids] or list(self.ctrl.project.toolpaths)
        if not tps:
            QMessageBox.information(self, "Package", "The project has no toolpaths yet.")
            return
        for t in tps:
            op = self.ctrl.registry.operations.get(t.op)
            p["items"].append(P.item_from_toolpath(t, p["tool"], op.params() if op else None, self.ctrl.project))
        self._fill_items()

    def edit_item(self) -> None:
        p, r = self.current(), self._row()
        if p is None or r < 0:
            return
        if p["items"][r]["op"] not in self.ctrl.registry.operations:
            QMessageBox.warning(self, "Package", f"The toolpath type '{p['items'][r]['op']}' is not available.")
            return
        dlg = PackageItemDialog(self.ctrl, p["items"][r], p["tool"], self)
        if dlg.exec():
            p["items"][r] = dlg.result_item()
            self._fill_items()
            self.items.setCurrentItem(self.items.topLevelItem(r))

    def remove_item(self) -> None:
        p, r = self.current(), self._row()
        if p is not None and r >= 0:
            del p["items"][r]
            self._fill_items()

    def move_item(self, step: int) -> None:
        p, r = self.current(), self._row()
        if p is None or r < 0 or not 0 <= r + step < len(p["items"]):
            return
        items = p["items"]
        items[r], items[r + step] = items[r + step], items[r]
        self._fill_items()
        self.items.setCurrentItem(self.items.topLevelItem(r + step))

    # -- kept -------------------------------------------------------------------------------------------------------------
    def accept(self) -> None:
        self._commit()
        try:
            kept = [P.check(p) for p in self.pkgs]
        except P.PackageError as exc:
            QMessageBox.warning(self, "Toolpath Packages", str(exc))
            return
        P._write(kept)
        super().accept()


# ------------------------------------------------------------------------------------------------ using a package
class PackageApplyDialog(RemembersSize, QDialog):
    """A package on the selected vectors: the tool for this time, the asked settings and the names of each toolpath, the group."""

    def __init__(self, ctrl, pkg: dict, vector_ids: list[str], parent=None):
        super().__init__(parent)
        from PySide6.QtWidgets import QButtonGroup, QComboBox, QRadioButton
        self.ctrl = ctrl
        self.pkg = pkg
        self.vector_ids = list(vector_ids)
        self.tool: dict | None = copy.deepcopy(pkg["tool"])
        self.item_tools: dict[int, dict] = {}
        self.setWindowTitle(f"Package: {pkg['name']}")
        lay = QVBoxLayout(self)
        head = QFormLayout()
        # what it machines: the selected vectors, or every vector of layers
        self.rb_sel = QRadioButton(f"The {len(self.vector_ids)} selected vector(s)")
        self.rb_layers = QRadioButton("Vectors on layers (each toolpath its own layers, or these):")
        grp = QButtonGroup(self)
        grp.addButton(self.rb_sel)
        grp.addButton(self.rb_layers)
        head.addRow("Machine", self.rb_sel)
        head.addRow("", self.rb_layers)
        self.layer_list = QListWidget()
        self.layer_list.setMaximumHeight(110)
        for l in ctrl.project.layers:
            it = QListWidgetItem(l.name)
            it.setData(Qt.UserRole, l.id)
            it.setFlags(it.flags() | Qt.ItemIsUserCheckable)
            it.setCheckState(Qt.Checked if l.id == ctrl.layer_id else Qt.Unchecked)
            self.layer_list.addItem(it)
        head.addRow("", self.layer_list)
        (self.rb_sel if self.vector_ids else self.rb_layers).setChecked(True)
        self.rb_sel.setEnabled(bool(self.vector_ids))
        self._combo = QComboBox
        self.tool_label = QLabel(tool_text(self.tool))
        tb = QPushButton("Change tool...")
        tb.setToolTip("Another tool for this time (the package keeps its own)")
        tb.clicked.connect(self.change_tool)
        trow = QHBoxLayout()
        trow.addWidget(self.tool_label, 1)
        trow.addWidget(tb)
        if pkg["tool"] is not None:
            head.addRow("Tool", trow)
        self.make_group = QCheckBox("Put them in a new toolpath group")
        self.make_group.setChecked(pkg["make_group"])
        head.addRow(self.make_group)
        self.group_name = QLineEdit(pkg["group_name"])
        grow = QHBoxLayout()
        grow.addWidget(self.group_name, 1)
        grow.addWidget(variables_button(self.group_name, toolpath_vars=False))
        head.addRow("Group name", grow)
        self.group_shown = QLabel()
        self.group_shown.setStyleSheet("color: gray")
        head.addRow("", self.group_shown)
        self.this_sheet = QCheckBox("Only on the sheet that is open")
        self.this_sheet.setChecked(pkg["this_sheet"])
        head.addRow(self.this_sheet)
        lay.addLayout(head)
        body = QWidget()
        bl = QVBoxLayout(body)
        self.layer_boxes: list = []
        self.boxes: list[tuple[QGroupBox, QLineEdit, QLabel, ParamForm | None]] = []
        for i, it in enumerate(pkg["items"]):
            op = ctrl.registry.operations.get(it["op"])
            box = QGroupBox(op.label if op else f"{it['op']} (not available)")
            box.setCheckable(True)
            box.setChecked(op is not None)
            box.setEnabled(op is not None)
            fl = QVBoxLayout(box)
            name = QLineEdit(it["name"])
            nrow = QHBoxLayout()
            nrow.addWidget(QLabel("Name"))
            nrow.addWidget(name, 1)
            nrow.addWidget(variables_button(name))
            fl.addLayout(nrow)
            shown = QLabel()
            shown.setStyleSheet("color: gray")
            fl.addWidget(shown)
            lcombo = self._combo()
            lcombo.addItem("(the layers checked above)", None)
            for l in ctrl.project.layers:
                lcombo.addItem(l.name, l.id)
            own = [l.id for n in it.get("layers") or [] for l in ctrl.project.layers if l.name == n]
            if own:
                lcombo.setCurrentIndex(max(0, lcombo.findData(own[0])))
            lcombo.setToolTip("On layers: the layer this toolpath machines" +
                              (f" (the package says: {', '.join(it['layers'])})" if it.get("layers") else ""))
            lcombo.currentIndexChanged.connect(self._show_names)
            lrow = QHBoxLayout()
            lrow.addWidget(QLabel("Layer"))
            lrow.addWidget(lcombo, 1)
            lwrap = QWidget()
            lwrap.setLayout(lrow)
            lrow.setContentsMargins(0, 0, 0, 0)
            fl.addWidget(lwrap)
            self.layer_boxes.append((lcombo, lwrap, own))
            if it["tool"] is not None:
                lab = QLabel(f"Tool: {tool_text(it['tool'])}")
                b = QPushButton("Change...")
                b.clicked.connect(lambda checked=False, k=i, l=lab: self.change_item_tool(k, l))
                r = QHBoxLayout()
                r.addWidget(lab, 1)
                r.addWidget(b)
                fl.addLayout(r)
            asked = P.asked_params(it, ctrl.registry) if op else []
            form = ParamForm(asked, None, ctrl.units, ctrl.tools) if asked else None
            if form is not None:
                fl.addWidget(form)
                form.changed.connect(self._show_names)
            name.textChanged.connect(self._show_names)
            bl.addWidget(box)
            self.boxes.append((box, name, shown, form))
        bl.addStretch(1)
        lay.addWidget(scroll(body), 1)
        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        bb.button(QDialogButtonBox.Ok).setText("Create toolpaths")
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)
        lay.addWidget(bb)
        for w in (self.group_name,):
            w.textChanged.connect(self._show_names)
        self.make_group.toggled.connect(self._show_names)
        self.rb_layers.toggled.connect(self._show_names)
        self.layer_list.itemChanged.connect(self._show_names)
        self._show_names()
        self.keep_size(ctrl, "package_apply", 520, 680)

    def change_tool(self) -> None:
        t = pick_tool(self.ctrl, self, self.tool, f"Tool of the package {self.pkg['name']} (this time)")
        if t is not None:
            self.tool = t.to_dict()
            self.tool_label.setText(tool_text(self.tool))
            self._show_names()

    def change_item_tool(self, k: int, label: QLabel) -> None:
        t = pick_tool(self.ctrl, self, self.item_tools.get(k) or self.pkg["items"][k]["tool"], "Tool of this toolpath (this time)")
        if t is not None:
            self.item_tools[k] = t.to_dict()
            label.setText(f"Tool: {tool_text(self.item_tools[k])}")
            self._show_names()

    def checked_layers(self) -> list[str]:
        return [self.layer_list.item(i).data(Qt.UserRole) for i in range(self.layer_list.count())
                if self.layer_list.item(i).checkState() == Qt.Checked]

    def _show_names(self, *_) -> None:
        on_layers = self.rb_layers.isChecked()
        self.layer_list.setEnabled(on_layers)
        for _, wrap, _ in self.layer_boxes:
            wrap.setVisible(on_layers)
        self.group_name.setEnabled(self.make_group.isChecked())
        try:
            tps, group = self.toolpaths()
        except Exception:  # noqa: BLE001  (half typed)
            return
        self.group_shown.setText(f"→ {group}" if group else "(no group)")
        from ..post.naming import toolpath_name
        made = iter(tps)
        for box, _, shown, _ in self.boxes:
            if box.isChecked():
                t = next(made, None)
                shown.setText("→ " + toolpath_name(self.ctrl.project, t, self.ctrl.sheet) if t is not None else "")
            else:
                shown.setText("(left out)")

    def toolpaths(self):
        """The toolpaths as the window is set (new definitions) and the name of their group."""
        values, names, skip = {}, {}, set()
        for i, (box, name, _, form) in enumerate(self.boxes):
            if not box.isChecked():
                skip.add(i)
                continue
            names[i] = name.text()
            if form is not None:
                values[i] = form.values()
        item_layers = {}
        if self.rb_layers.isChecked():
            sel = Selection("layers", [], self.checked_layers())
            for i, (combo, _, _) in enumerate(self.layer_boxes):
                if combo.currentData():
                    item_layers[i] = [combo.currentData()]
        else:
            sel = Selection("vectors", list(self.vector_ids), [])
        return P.build(self.pkg, self.ctrl.project, self.ctrl.sheet, sel, self.ctrl.registry,
                       tool=Tool.from_dict(self.tool) if self.tool else None, values=values, names=names, skip=skip,
                       make_group=self.make_group.isChecked(), group_template=self.group_name.text() or P.DEFAULT_GROUP,
                       this_sheet=self.this_sheet.isChecked(),
                       item_tools={k: Tool.from_dict(v) for k, v in self.item_tools.items()}, item_layers=item_layers)

    def accept(self) -> None:
        if all(not box.isChecked() for box, *_ in self.boxes):
            QMessageBox.information(self, "Package", "Every toolpath of the package is left out.")
            return
        try:
            self.toolpaths()
        except P.PackageError as exc:
            QMessageBox.information(self, "Package", str(exc))
            return
        super().accept()

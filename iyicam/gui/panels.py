"""Dockable panels: layers, sheets, toolpaths and selection properties."""
from __future__ import annotations

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QColor, QFont, QIcon, QPixmap
from PySide6.QtWidgets import (QAbstractItemView, QInputDialog, QColorDialog, QFormLayout, QHBoxLayout,
                                QHeaderView, QInputDialog, QLabel, QLineEdit, QListWidget, QListWidgetItem, QMenu,
                                QMessageBox, QPushButton, QToolButton, QTreeWidget, QTreeWidgetItem, QVBoxLayout,
                                QWidget)
from .wheelguard import QComboBox, QDoubleSpinBox

from ..cam.moves import PathStats, format_time
from ..core.geom import BBox, Transform
from . import menus
from .material_info import material_summary


def swatch(color: str, size: int = 12) -> QIcon:
    pm = QPixmap(size, size)
    pm.fill(QColor(color))
    return QIcon(pm)


def small_button(text: str, tip: str, slot) -> QToolButton:
    b = QToolButton()
    b.setText(text)
    b.setToolTip(tip)
    b.setAutoRaise(True)
    b.clicked.connect(slot)
    return b


class DocPanel(QWidget):
    """A panel that shows one document: ``bind`` moves it to the document of another tab."""
    signals: tuple[str, ...] = ()       # the signals of the controller that make the panel show it again

    def _connect(self, ctrl) -> None:
        for name in self.signals:
            getattr(ctrl, name).connect(self.refresh)

    def bind(self, ctrl) -> None:
        if ctrl is not self.ctrl:
            for name in self.signals:
                try:
                    getattr(self.ctrl, name).disconnect(self.refresh)
                except (RuntimeError, TypeError):    # (a document that is gone)
                    pass
            self.ctrl = ctrl
            self._connect(ctrl)
        self.refresh()


class LayersPanel(DocPanel):
    signals = ("changed", "sheet_changed")

    def __init__(self, ctrl, parent=None):
        super().__init__(parent)
        self.ctrl = ctrl
        lay = QVBoxLayout(self)
        lay.setContentsMargins(2, 2, 2, 2)
        self.tree = QTreeWidget()
        self.tree.setHeaderLabels(["Layer", "Show", "Lock"])
        self.tree.setRootIsDecorated(False)
        self.tree.header().setSectionResizeMode(0, QHeaderView.Stretch)
        self.tree.header().setSectionResizeMode(1, QHeaderView.ResizeToContents)
        self.tree.header().setSectionResizeMode(2, QHeaderView.ResizeToContents)
        self.tree.itemChanged.connect(self._item_changed)
        self.tree.currentItemChanged.connect(self._current_changed)
        self.tree.itemDoubleClicked.connect(self._double_clicked)
        self.tree.setContextMenuPolicy(Qt.CustomContextMenu)
        self.tree.customContextMenuRequested.connect(self._menu)
        lay.addWidget(self.tree)
        row = QHBoxLayout()
        for text, tip, slot in (("+", "New layer", self.add), ("−", "Delete layer", self.delete),
                                ("▲", "Move up", lambda: self.move(-1)), ("▼", "Move down", lambda: self.move(1)),
                                ("■", "Layer colour", self.color),
                                ("↳", "Move selected vectors to this layer", self.move_selection),
                                ("⬚", "Select all vectors on this layer", self.select_layer)):
            row.addWidget(small_button(text, tip, slot))
        row.addStretch(1)
        lay.addLayout(row)
        self._updating = False
        self._changing = False
        self._connect(ctrl)
        self.refresh()

    def refresh(self) -> None:
        if self._changing:   # called from inside the change of one of the rows: rebuild when that is over
            QTimer.singleShot(0, self.refresh)
            return
        self._updating = True
        self.tree.clear()
        counts: dict[str, int] = {}
        for v in self.ctrl.sheet.vectors:
            counts[v.layer_id] = counts.get(v.layer_id, 0) + 1
        for l in self.ctrl.project.layers_for(self.ctrl.sheet):          # (a layer of another sheet or group is not shown)
            it = QTreeWidgetItem([f"{l.name} ({counts.get(l.id, 0)})" + (" ▣" if l.sheet_ids is not None or l.sheet_group else ""), "", ""])
            it.setData(0, Qt.UserRole, l.id)
            it.setIcon(0, swatch(l.color))
            it.setCheckState(1, Qt.Checked if l.visible else Qt.Unchecked)
            it.setCheckState(2, Qt.Checked if l.locked else Qt.Unchecked)
            if l.id == self.ctrl.layer_id:
                f = QFont()
                f.setBold(True)
                it.setFont(0, f)
            self.tree.addTopLevelItem(it)
            if l.id == self.ctrl.layer_id:
                self.tree.setCurrentItem(it)
        self._updating = False

    def _lid(self, it=None) -> str | None:
        it = it or self.tree.currentItem()
        return it.data(0, Qt.UserRole) if it else None

    def _item_changed(self, it, col) -> None:
        if self._updating:
            return
        layer = self.ctrl.project.layer(self._lid(it))
        if layer is None:
            return
        self._changing = True   # rebuilding the tree now would delete the row that is being changed
        try:
            with self.ctrl.edit("Layer state", geometry=False):
                layer.visible = it.checkState(1) == Qt.Checked
                layer.locked = it.checkState(2) == Qt.Checked
            if not layer.visible or layer.locked:
                self.ctrl.select([o.id for o in self.ctrl.selected_objects() if o.layer_id != layer.id])
        finally:
            self._changing = False
        QTimer.singleShot(0, self.refresh)

    def _current_changed(self, cur, prev) -> None:
        if not self._updating and cur is not None:
            self.ctrl.layer_id = self._lid(cur)
            self.refresh()

    def _double_clicked(self, it, col) -> None:
        if col == 0:
            self.rename()

    def _menu(self, pos) -> None:
        m = QMenu(self)
        m.addAction("Rename...", self.rename)
        m.addAction("Colour...", self.color)
        m.addAction("Appears on...", self.scope)
        m.addAction("Select vectors", self.select_layer)
        m.addAction("Move selection here", self.move_selection)
        m.addSeparator()
        m.addAction("Delete", self.delete)
        menus.show_menu(m, self.tree.mapToGlobal(pos))

    def add(self) -> None:
        name, ok = QInputDialog.getText(self, "New layer", "Name:", text=f"Layer {len(self.ctrl.project.layers) + 1}")
        if ok and name:
            with self.ctrl.edit("Add layer", geometry=False):
                l = self.ctrl.project.add_layer(name)
            self.ctrl.set_layer(l.id)

    def scope(self) -> None:
        """Which sheets the layer is on: every sheet, some sheets, or the sheets of a group."""
        layer = self.ctrl.project.layer(self._lid())
        if layer is None:
            return
        from .dialogs import LayerScopeDialog
        dlg = LayerScopeDialog(self.ctrl, layer, self)
        if dlg.exec():
            ids, group = dlg.chosen()
            with self.ctrl.edit("Layer sheets", geometry=False):
                layer.sheet_ids, layer.sheet_group = ids, group

    def rename(self) -> None:
        layer = self.ctrl.project.layer(self._lid())
        if layer is None:
            return
        name, ok = QInputDialog.getText(self, "Rename layer", "Name:", text=layer.name)
        if ok and name:
            with self.ctrl.edit("Rename layer", geometry=False):
                layer.name = name

    def delete(self) -> None:
        lid = self._lid()
        p = self.ctrl.project
        layer = p.layer(lid)
        if layer is None:
            return
        n = sum(1 for _, v in p.iter_vectors() if v.layer_id == lid)
        move_to = None
        if n:
            others = [l for l in p.layers if l.id != lid]
            choice = QMessageBox.question(
                self, "Delete layer", f"Layer '{layer.name}' contains {n} vector(s).\n"
                f"Move them to '{others[0].name if others else 'Default'}' instead of deleting them?",
                QMessageBox.Yes | QMessageBox.No | QMessageBox.Cancel)
            if choice == QMessageBox.Cancel:
                return
            if choice == QMessageBox.Yes and others:
                move_to = others[0].id
        with self.ctrl.edit("Delete layer"):
            p.remove_layer(lid, move_to)
        self.ctrl.layer_id = p.layers[0].id

    def move(self, d: int) -> None:
        ls = self.ctrl.project.layers
        lid = self._lid()
        i = next((k for k, l in enumerate(ls) if l.id == lid), None)
        if i is None or not 0 <= i + d < len(ls):
            return
        with self.ctrl.edit("Reorder layers", geometry=False):
            ls[i], ls[i + d] = ls[i + d], ls[i]

    def color(self) -> None:
        layer = self.ctrl.project.layer(self._lid())
        if layer is None:
            return
        c = QColorDialog.getColor(QColor(layer.color), self, "Layer colour")
        if c.isValid():
            with self.ctrl.edit("Layer colour", geometry=False):
                layer.color = c.name()

    def move_selection(self) -> None:
        lid = self._lid()
        objs = self.ctrl.selected_objects()
        if lid and objs:
            with self.ctrl.edit("Move to layer"):
                for o in objs:
                    o.layer_id = lid

    def select_layer(self) -> None:
        lid = self._lid()
        self.ctrl.select([o.id for o in self.ctrl.sheet.vectors if o.layer_id == lid])


class SheetsPanel(DocPanel):
    signals = ("changed", "sheet_changed")

    def __init__(self, ctrl, parent=None):
        super().__init__(parent)
        self.ctrl = ctrl
        lay = QVBoxLayout(self)
        lay.setContentsMargins(2, 2, 2, 2)
        self.list = QListWidget()
        self.list.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.list.currentRowChanged.connect(self._row_changed)
        self.list.itemDoubleClicked.connect(lambda *_: self.properties())
        self.list.setContextMenuPolicy(Qt.CustomContextMenu)
        self.list.customContextMenuRequested.connect(self._menu)
        lay.addWidget(self.list)
        row = QHBoxLayout()
        for text, tip, slot in (("+", "Add sheet", self.add), ("⧉", "Duplicate sheet", self.duplicate),
                                ("−", "Delete sheet", self.delete), ("▲", "Move the sheet up", lambda: self.move(-1)),
                                ("▼", "Move the sheet down", lambda: self.move(1)), ("⚙", "Sheet properties", self.properties)):
            row.addWidget(small_button(text, tip, slot))
        row.addStretch(1)
        lay.addLayout(row)
        self._updating = False
        self._connect(ctrl)
        self.refresh()

    def refresh(self) -> None:
        self._updating = True
        self.list.clear()
        u = self.ctrl.units
        for s in self.ctrl.project.sheets:
            t = self.ctrl.project.sheet_thickness(s)
            shape = "  (shape)" if s.outline else ""
            it = QListWidgetItem(f"{s.name}  —  {u.fmt(s.width)} x {u.fmt(s.height)} x {u.fmt(t)}{shape}  "
                                 f"[{len(s.vectors)}]" + (f"   ▣ {s.group}" if s.group else ""))
            it.setData(Qt.UserRole, s.id)
            self.list.addItem(it)
            if s.id == self.ctrl.sheet_id:
                self.list.setCurrentItem(it)
        self._updating = False

    def _row_changed(self, row: int) -> None:
        if not self._updating and row >= 0:
            self.ctrl.set_sheet(self.list.item(row).data(Qt.UserRole))

    def selected_ids(self) -> list[str]:
        return [it.data(Qt.UserRole) for it in self.list.selectedItems()] or [self.ctrl.sheet_id]

    def move(self, delta: int) -> None:
        self.ctrl.move_sheet(self.ctrl.sheet_id, delta)

    def _menu(self, pos) -> None:
        m = QMenu(self)
        g = m.addMenu("Group")
        g.addAction("New group from the selected...", self.new_group)
        for name in self.ctrl.sheet_groups():
            sub = g.addMenu(name)
            sub.addAction("Put the selected in it", lambda n=name: self.ctrl.set_sheet_group(self.selected_ids(), n))
            sub.addAction("Select its sheets", lambda n=name: self._select_group(n))
            sub.addAction("Rename...", lambda n=name: self.rename_group(n))
            sub.addAction("Nest onto this group...", lambda n=name: self.nest_on(n))
            sub.addAction("New toolpath for this group", lambda n=name: self.toolpath_for(n))
        g.addSeparator()
        g.addAction("Take the selected out of their group", lambda: self.ctrl.set_sheet_group(self.selected_ids(), ""))
        m.addAction("Properties...", self.properties)
        menus.show_menu(m, self.list.mapToGlobal(pos))

    def new_group(self) -> None:
        name, ok = QInputDialog.getText(self, "Group of sheets", "Name of the group:")
        if ok and name.strip():
            self.ctrl.set_sheet_group(self.selected_ids(), name)

    def rename_group(self, old: str) -> None:
        name, ok = QInputDialog.getText(self, "Group of sheets", "New name:", text=old)
        if ok and name.strip():
            ids = [s.id for s in self.ctrl.sheets_in(old)]
            self.ctrl.set_sheet_group(ids, name)
            for t in self.ctrl.project.toolpaths:          # (the toolpaths of the group follow its name)
                if t.sheet_group == old:
                    t.sheet_group = name.strip()
            self.ctrl.toolpaths_changed.emit()

    def _select_group(self, name: str) -> None:
        ids = {s.id for s in self.ctrl.sheets_in(name)}
        for i in range(self.list.count()):
            self.list.item(i).setSelected(self.list.item(i).data(Qt.UserRole) in ids)

    def nest_on(self, name: str) -> None:
        win = self.window()
        if hasattr(win, "nest"):
            win.nest(group=name)

    def toolpath_for(self, name: str) -> None:
        win = self.window()
        if hasattr(win, "new_toolpath"):
            win.new_toolpath("profile", sheet_group=name)

    def add(self) -> None:
        with self.ctrl.edit("Add sheet", geometry=False):
            s = self.ctrl.project.add_sheet()
        self.ctrl.set_sheet(s.id)

    def duplicate(self) -> None:
        src = self.ctrl.sheet
        with self.ctrl.edit("Duplicate sheet", geometry=False):
            s = self.ctrl.project.add_sheet(src.name + " copy", src.width, src.height)
            s.thickness = src.thickness
            s.outline = [list(poly) for poly in src.outline] if src.outline else None
            s.vectors = [v.copy() for v in src.vectors]
        self.ctrl.set_sheet(s.id)

    def delete(self) -> None:
        if len(self.ctrl.project.sheets) <= 1:
            return
        sid = self.ctrl.sheet_id
        with self.ctrl.edit("Delete sheet"):
            self.ctrl.project.remove_sheet(sid)
        self.ctrl.set_sheet(self.ctrl.project.sheets[0].id)

    def properties(self) -> None:
        from .dialogs import SheetDialog
        SheetDialog(self.ctrl, self.ctrl.sheet, self).exec()


class ToolpathsPanel(DocPanel):
    signals = ("toolpaths_changed", "sheet_changed")

    def __init__(self, ctrl, window, parent=None):
        super().__init__(parent)
        self.ctrl = ctrl
        self.window = window
        lay = QVBoxLayout(self)
        lay.setContentsMargins(2, 2, 2, 2)
        self.tree = QTreeWidget()
        self.tree.setHeaderLabels(["Toolpath", "Group", "Tool", "Time"])
        self.tree.setRootIsDecorated(True)                 # (the toolpaths of a group are stacked under it)
        self.tree.setSelectionMode(QAbstractItemView.ExtendedSelection)
        h = self.tree.header()
        h.setStretchLastSection(False)
        for i in range(4):
            h.setSectionResizeMode(i, QHeaderView.Interactive)   # (every column can be made wider or narrower)
        mem = ctrl.settings.recall("toolpaths_panel")
        widths = mem.get("widths")
        for i, w in enumerate(widths if isinstance(widths, list) else [190, 80, 90, 60]):
            if isinstance(w, int) and 20 <= w <= 2000 and i < 4:
                self.tree.setColumnWidth(i, w)
        h.sectionResized.connect(self._widths_changed)
        self._folded: set[str] = {g for g in (mem.get("folded") or []) if isinstance(g, str)}
        self.tree.itemCollapsed.connect(lambda it: self._fold(it, True))
        self.tree.itemExpanded.connect(lambda it: self._fold(it, False))
        self.tree.itemChanged.connect(self._item_changed)
        self.tree.itemDoubleClicked.connect(lambda *_: self.edit())
        self.tree.setContextMenuPolicy(Qt.CustomContextMenu)
        self.tree.customContextMenuRequested.connect(self._menu)
        lay.addWidget(self.tree)
        self.status = QLabel()
        self.status.setWordWrap(True)
        lay.addWidget(self.status)
        row = QHBoxLayout()
        self.material = QLabel()
        self.material.setWordWrap(True)
        self.material.setToolTip("The material and the heights of the project: the tool travels at the safe Z and "
                                 "rapids down to the clearance before it plunges")
        row.addWidget(self.material, 1)
        b = QPushButton("Material Setup...")
        b.setToolTip("Thickness, Z zero, safe Z, rapid-down clearance, home position (Ctrl+Shift+M)")
        b.clicked.connect(window.material_setup)
        row.addWidget(b)
        lay.addLayout(row)
        row = QHBoxLayout()
        self.new_btn = QPushButton("New ▾")
        self.new_btn.setToolTip("Create a toolpath")
        self.new_btn.clicked.connect(self._new_menu)
        row.addWidget(self.new_btn)
        for text, tip, slot in (("✎", "Edit toolpath", self.edit), ("⧉", "Duplicate", self.duplicate),
                                ("−", "Delete", self.delete), ("▲", "Move up", lambda: self.move(-1)),
                                ("▼", "Move down", lambda: self.move(1)),
                                ("⟳", "Recalculate all", self.recalculate)):
            row.addWidget(small_button(text, tip, slot))
        row.addStretch(1)
        lay.addLayout(row)
        row2 = QHBoxLayout()
        b = QPushButton("Save G-code...")
        b.clicked.connect(window.export_gcode)
        row2.addWidget(b)
        b = QPushButton("Simulate")
        b.clicked.connect(window.simulate)
        row2.addWidget(b)
        lay.addLayout(row2)
        self._updating = False
        self._changing = False
        self._connect(ctrl)
        self.refresh()

    def _connect(self, ctrl) -> None:
        super()._connect(ctrl)
        ctrl.changed.connect(self._update_material)

    def bind(self, ctrl) -> None:
        if ctrl is not self.ctrl:
            try:
                self.ctrl.changed.disconnect(self._update_material)
            except (RuntimeError, TypeError):
                pass
        super().bind(ctrl)

    def _update_material(self) -> None:
        self.material.setText(material_summary(self.ctrl))

    GROUP_ROLE = Qt.UserRole + 1

    def selected_ids(self) -> list[str]:
        """The selected toolpaths (a selected group heading stands for every toolpath in it)."""
        out = []
        for it in self.tree.selectedItems():
            if it.data(0, self.GROUP_ROLE):
                out += [it.child(k).data(0, Qt.UserRole) for k in range(it.childCount())]
            elif it.data(0, Qt.UserRole):
                out.append(it.data(0, Qt.UserRole))
        return list(dict.fromkeys(out))

    def _rows(self):
        """Every toolpath row, in groups or not."""
        for i in range(self.tree.topLevelItemCount()):
            it = self.tree.topLevelItem(i)
            if it.data(0, self.GROUP_ROLE):
                for k in range(it.childCount()):
                    yield it.child(k)
            else:
                yield it

    def _widths_changed(self, *_) -> None:
        if getattr(self, "_updating", False):
            return
        mem = dict(self.ctrl.settings.recall("toolpaths_panel"))
        mem["widths"] = [self.tree.columnWidth(i) for i in range(4)]
        self.ctrl.settings.remember("toolpaths_panel", mem, save=False)

    def _fold(self, it, folded: bool) -> None:
        g = it.data(0, self.GROUP_ROLE)
        if not g or getattr(self, "_updating", False):
            return
        (self._folded.add if folded else self._folded.discard)(g)
        mem = dict(self.ctrl.settings.recall("toolpaths_panel"))
        mem["folded"] = sorted(self._folded)
        self.ctrl.settings.remember("toolpaths_panel", mem, save=False)

    def refresh(self) -> None:
        if self._changing:   # called from inside the change of one of the rows: rebuild when that is over
            QTimer.singleShot(0, self.refresh)
            return
        self._updating = True
        sel = set(self.selected_ids())
        self.tree.clear()
        total = PathStats()
        problems = 0
        rate = self.ctrl.project.material.machine()
        old_ids = set(getattr(self.ctrl, "stale_ids", set())) if self.ctrl.stale else set()
        heads: dict[str, QTreeWidgetItem] = {}
        times: dict[str, float] = {}
        for t in self.ctrl.project.toolpaths:
            res = [r for r in self.ctrl.results_for(t) if r.toolpaths or r.error or r.warnings]
            here = self.ctrl.results.get((t.id, self.ctrl.sheet_id))
            st = PathStats()
            if here:
                for tp in here.toolpaths:
                    st = st.merge(tp.stats(rate))
            if t.enabled:
                total = total.merge(st)
            it = QTreeWidgetItem([t.name, t.group, t.tool.name, format_time(st.time_s) if here and here.toolpaths else ""])
            it.setData(0, Qt.UserRole, t.id)
            it.setFlags(it.flags() | Qt.ItemIsUserCheckable)
            it.setCheckState(0, Qt.Checked if t.enabled else Qt.Unchecked)
            op = self.ctrl.registry.operations.get(t.op)
            msgs = [r.error for r in res if r.error] + [w for r in res for w in r.warnings]
            tip = f"{op.label if op else t.op}\n{t.tool.describe()}"
            if t.id in old_ids:
                tip += "\n\nIts vectors or settings changed since it was calculated: F5 calculates it again."
                it.setForeground(0, QColor("#c00000"))
            if msgs:
                tip += "\n\n" + "\n".join(dict.fromkeys(msgs))
                if t.id not in old_ids:
                    it.setForeground(0, QColor("#b35806" if not any(r.error for r in res) else "#c00000"))
                problems += 1
            it.setToolTip(0, tip)
            if t.group:
                head = heads.get(t.group)
                if head is None:                      # (a heading for the group: its toolpaths are stacked under it)
                    head = QTreeWidgetItem([t.group, "", "", ""])
                    head.setData(0, self.GROUP_ROLE, t.group)
                    f = QFont(head.font(0))
                    f.setBold(True)
                    head.setFont(0, f)
                    head.setFlags(head.flags() | Qt.ItemIsUserCheckable | Qt.ItemIsAutoTristate)
                    self.tree.addTopLevelItem(head)
                    heads[t.group] = head
                head.addChild(it)
                if t.enabled and here and here.toolpaths:
                    times[t.group] = times.get(t.group, 0.0) + st.time_s
            else:
                self.tree.addTopLevelItem(it)
            if t.id in sel:
                it.setSelected(True)
        for g, head in heads.items():
            n = head.childCount()
            head.setText(0, f"{g}  ({n})")
            head.setText(3, format_time(times[g]) if times.get(g) else "")
            head.setToolTip(0, f"Group {g}: {n} toolpath(s). Tick to switch them all on or off; right click for the group.")
            head.setExpanded(g not in self._folded)
            if any(head.child(k).data(0, Qt.UserRole) in old_ids for k in range(n)):
                head.setForeground(0, QColor("#c00000"))
        txt = f"Sheet total: {format_time(total.time_s)}"
        if self.ctrl.stale:
            txt += (f"  —  <b>{len(old_ids)} toolpath(s) need recalculating</b> (red)" if old_ids
                    else "  —  <b>toolpaths need recalculating</b>")
        if problems:
            txt += f"  —  {problems} with warnings (hover for details)"
        self.status.setText(txt)
        self._update_material()
        self._updating = False

    def _item_changed(self, it, col) -> None:
        if self._updating:
            return
        g = it.data(0, self.GROUP_ROLE)
        if g:                                   # (the tick of a group heading: every toolpath of the group on or off)
            state = it.checkState(0)
            if state != Qt.PartiallyChecked:
                self._changing = True
                try:
                    self._switch_group(g, state == Qt.Checked)
                finally:
                    self._changing = False
                QTimer.singleShot(0, self.refresh)
            return
        t = self.ctrl.project.toolpath(it.data(0, Qt.UserRole))
        if t is not None:
            self._changing = True   # rebuilding the tree now would delete the row that is being changed
            try:
                with self.ctrl.edit("Toggle toolpath", geometry=False):
                    t.enabled = it.checkState(0) == Qt.Checked
                self.ctrl.toolpaths_changed.emit()
            finally:
                self._changing = False
            QTimer.singleShot(0, self.refresh)

    def _new_menu(self) -> None:
        m = QMenu(self)
        for op in self.ctrl.registry.operations.values():
            m.addAction(op.label, lambda o=op: self.window.new_toolpath(o.id))
        menus.show_menu(m, self.new_btn.mapToGlobal(self.new_btn.rect().bottomLeft()))

    def _menu(self, pos) -> None:
        m = QMenu(self)
        m.addAction("Edit...", self.edit)
        m.addAction("Duplicate", self.duplicate)
        m.addAction("Delete", self.delete)
        m.addSeparator()
        m.addAction("Select its vectors", self.select_vectors)
        self._group_menu(m.addMenu("Group"))
        m.addAction("Save G-code of the selected...", self.save_selected)
        ids = self.selected_ids()
        t = self.ctrl.project.toolpath(ids[0]) if ids else None
        op = self.ctrl.registry.operations.get(t.op) if t else None
        if op is not None and any(p.name == "tab_mode" for p in op.params()):
            m.addAction("Edit tabs on the drawing", lambda: self.window.edit_tabs(t.id))
        if op is not None and any(p.name == "start_mode" for p in op.params()):
            m.addAction("Cut order...", lambda: self.window.cut_order(t.id))
            m.addAction("Start points...", lambda: self.window.start_points(t.id))
        m.addAction("Save as template...", self.window.save_toolpath_template)
        m.addAction("Load template...", self.window.load_toolpath_template)
        menus.show_menu(m, self.tree.mapToGlobal(pos))

    def _group_menu(self, menu: QMenu) -> None:
        ids = self.selected_ids()
        menu.setEnabled(bool(ids))
        menu.addAction("New group from the selected...", self.new_group)
        for g in self.ctrl.groups():
            sub = menu.addMenu(g)
            sub.addAction("Put the selected in it", lambda g=g: self.ctrl.set_group(self.selected_ids(), g))
            sub.addAction("Select its toolpaths", lambda g=g: self._select_group(g))
            sub.addAction("Switch only this group on", lambda g=g: self.ctrl.enable_only(self.ctrl.group_ids(g)))
            sub.addAction("Switch it off", lambda g=g: self._switch_group(g, False))
            sub.addAction("Switch it on", lambda g=g: self._switch_group(g, True))
            sub.addAction("Rename...", lambda g=g: self.rename_group(g))
            sub.addAction("Nest the vectors of this group...", lambda g=g: self.nest_group(g))
            sub.addAction("Save G-code of this group...", lambda g=g: self.window.export_gcode(self.ctrl.group_ids(g), g))
        menu.addSeparator()
        menu.addAction("Take the selected out of their group", lambda: self.ctrl.set_group(self.selected_ids(), ""))

    def nest_group(self, g: str) -> None:
        """Nest the vectors that the toolpaths of a group machine: they are selected, then the nesting window opens."""
        ctrl = self.ctrl
        ids: list[str] = []
        for tid in ctrl.group_ids(g):
            t = ctrl.project.toolpath(tid)
            for sheet in ctrl.project.sheets:
                ids += [o.id for o in ctrl.project.vectors_for(t, sheet)]
        ids = list(dict.fromkeys(ids))
        if not ids:
            return
        ctrl.select(ids)
        self.window.nest()

    def new_group(self) -> None:
        ids = self.selected_ids()
        if not ids:
            return
        name, ok = QInputDialog.getText(self, "Group", "Name of the group:")
        if ok and name.strip():
            self.ctrl.set_group(ids, name)

    def rename_group(self, old: str) -> None:
        name, ok = QInputDialog.getText(self, "Group", "New name:", text=old)
        if ok and name.strip():
            self.ctrl.rename_group(old, name)

    def _select_group(self, g: str) -> None:
        ids = set(self.ctrl.group_ids(g))
        self.tree.clearSelection()
        for it in self._rows():
            it.setSelected(it.data(0, Qt.UserRole) in ids)

    def _switch_group(self, g: str, on: bool) -> None:
        ids = set(self.ctrl.group_ids(g))
        with self.ctrl.edit("Switch toolpaths", geometry=False):
            for t in self.ctrl.project.toolpaths:
                if t.id in ids:
                    t.enabled = on
        self.ctrl.toolpaths_changed.emit()

    def save_selected(self) -> None:
        ids = self.selected_ids()
        if ids:
            self.window.export_gcode(ids)

    def edit(self) -> None:
        ids = self.selected_ids()
        if ids:
            self.window.edit_toolpath(ids[0])

    def duplicate(self) -> None:
        import copy
        from ..core.document import new_id
        for tid in self.selected_ids():
            t = self.ctrl.project.toolpath(tid)
            if t is not None:
                n = copy.deepcopy(t)
                n.id = new_id()
                n.name = t.name + " copy"
                self.ctrl.add_toolpath(n)

    def delete(self) -> None:
        ids = set(self.selected_ids())
        if ids:
            self.ctrl.remove_toolpaths(ids)

    def move(self, d: int) -> None:
        ids = self.selected_ids()
        if ids:
            self.ctrl.move_toolpath(ids[0], d)

    def recalculate(self) -> None:
        self.window.recalculate()

    def select_vectors(self) -> None:
        ids = self.selected_ids()
        if not ids:
            return
        t = self.ctrl.project.toolpath(ids[0])
        self.ctrl.select([o.id for o in self.ctrl.project.vectors_for(t, self.ctrl.sheet)])


class PropertiesPanel(DocPanel):
    """Numeric position / size of the selection, its layer and geometry info."""
    signals = ("selection_changed", "changed")

    def __init__(self, ctrl, parent=None):
        super().__init__(parent)
        self.ctrl = ctrl
        lay = QVBoxLayout(self)
        lay.setContentsMargins(4, 4, 4, 4)
        self.info = QLabel("Nothing selected")
        self.info.setWordWrap(True)
        lay.addWidget(self.info)
        form = QFormLayout()
        self.spins = {}
        for key, label in (("x", "X (left)"), ("y", "Y (bottom)"), ("w", "Width"), ("h", "Height")):
            s = QDoubleSpinBox()
            s.setRange(-1e6, 1e6)
            s.setKeyboardTracking(False)
            s.editingFinished.connect(self.apply)
            form.addRow(label, s)
            self.spins[key] = s
        self.keep = QPushButton("Keep proportions")
        self.keep.setCheckable(True)
        self.keep.setChecked(True)
        form.addRow("", self.keep)
        self.name = QLineEdit()
        self.name.editingFinished.connect(self.rename)
        form.addRow("Name", self.name)
        self.layer = QComboBox()
        self.layer.activated.connect(self.set_layer)
        form.addRow("Layer", self.layer)
        lay.addLayout(form)
        lay.addStretch(1)
        self._bb: BBox | None = None
        self._connect(ctrl)
        self.refresh()

    def refresh(self) -> None:
        objs = self.ctrl.selected_objects()
        u = self.ctrl.units
        enabled = bool(objs)
        for s in self.spins.values():
            s.setEnabled(enabled)
            s.setDecimals(u.decimals)
            s.setSuffix(u.suffix)
        self.name.setEnabled(len(objs) == 1)
        self.layer.setEnabled(enabled)
        self.layer.blockSignals(True)
        self.layer.clear()
        for l in self.ctrl.project.layers:
            self.layer.addItem(swatch(l.color), l.name, l.id)
        self.layer.blockSignals(False)
        if not objs:
            self.info.setText("Nothing selected")
            self._bb = None
            self.name.clear()
            return
        bb = BBox()
        for o in objs:
            bb = bb.union(o.bbox())
        self._bb = bb
        for key, v in (("x", bb.xmin), ("y", bb.ymin), ("w", bb.width), ("h", bb.height)):
            self.spins[key].blockSignals(True)
            self.spins[key].setValue(u.to_display(v))
            self.spins[key].blockSignals(False)
        ncont = sum(len(o.contours) for o in objs)
        closed = sum(1 for o in objs for c in o.contours if c.closed)
        length = sum(c.length() for o in objs for c in o.contours)
        lines = [f"<b>{len(objs)}</b> vector(s), {ncont} contour(s), {closed} closed",
                 f"Length: {u.fmt(length)}"]
        if len(objs) == 1:
            o = objs[0]
            area = sum(c.signed_area() for c in o.contours if c.closed)
            if closed:
                lines.append(f"Area: {u.to_display(u.to_display(abs(area))):.3f} {u.suffix.strip()}²")
            self.name.setText(o.name)
        else:
            self.name.clear()
        lids = {o.layer_id for o in objs}
        if len(lids) == 1:
            self.layer.setCurrentIndex(max(0, self.layer.findData(next(iter(lids)))))
        self.info.setText("<br>".join(lines))

    def apply(self) -> None:
        if self._bb is None:
            return
        u = self.ctrl.units
        x, y = u.from_display(self.spins["x"].value()), u.from_display(self.spins["y"].value())
        w, h = u.from_display(self.spins["w"].value()), u.from_display(self.spins["h"].value())
        bb = self._bb
        if (bb.width > 1e-9 and w < 1e-3) or (bb.height > 1e-9 and h < 1e-3):
            self.refresh()   # a size of nothing (or less) would destroy the vectors: back to what they are
            return
        sx = w / bb.width if bb.width > 1e-9 else 1.0
        sy = h / bb.height if bb.height > 1e-9 else 1.0
        if self.keep.isChecked():
            if abs(sx - 1) > 1e-9:
                sy = sx
            else:
                sx = sy
        T = Transform.translate(x - bb.xmin, y - bb.ymin) @ Transform.scale(sx, sy, (bb.xmin, bb.ymin))
        if all(abs(v) < 1e-9 for v in (x - bb.xmin, y - bb.ymin, sx - 1, sy - 1)):
            return
        with self.ctrl.edit("Position / size"):
            for o in self.ctrl.selected_objects():
                if self.ctrl.editable(o):
                    o.transform(T)

    def rename(self) -> None:
        objs = self.ctrl.selected_objects()
        if len(objs) == 1 and objs[0].name != self.name.text():
            with self.ctrl.edit("Rename vector", geometry=False):
                objs[0].name = self.name.text()

    def set_layer(self, idx: int) -> None:
        lid = self.layer.itemData(idx)
        objs = self.ctrl.selected_objects()
        if lid and objs:
            with self.ctrl.edit("Move to layer"):
                for o in objs:
                    o.layer_id = lid

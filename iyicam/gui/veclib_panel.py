"""The panel of the vector library (:mod:`iyicam.core.veclib`): groups in groups, drawings kept and put in again."""
from __future__ import annotations

import json
from pathlib import Path

from PySide6.QtCore import QPointF, Qt, Signal
from PySide6.QtGui import QColor, QPainter, QPainterPath, QPen, QPixmap
from PySide6.QtWidgets import (QAbstractItemView, QCheckBox, QInputDialog, QLabel, QLineEdit, QMenu,
                               QMessageBox, QToolBar, QToolButton, QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget)

from ..core.veclib import ROOT, LibraryError, VectorLibrary
from .files import open_file, save_file

ID_ROLE = Qt.UserRole
GROUP_ROLE = Qt.UserRole + 1


class LibraryTree(QTreeWidget):
    """A tree whose groups and drawings are moved by dragging (a drawing into a group, a group into another)."""
    dropped = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setHeaderHidden(True)
        self.setDragDropMode(QAbstractItemView.InternalMove)
        self.setSelectionMode(QAbstractItemView.SingleSelection)
        self.setDefaultDropAction(Qt.MoveAction)

    def dropEvent(self, ev) -> None:
        super().dropEvent(ev)
        self.dropped.emit()


def thumbnail(item: dict, size: int = 160, color: str = "#2a6fdb") -> QPixmap:
    """A picture of a drawing of the library."""
    from ..core.document import contour_from_dict
    pm = QPixmap(size, size)
    pm.fill(Qt.transparent)
    w, h = (item.get("size") or [0, 0])[:2]
    if not w and not h:
        return pm
    k = (size - 12) / max(w, h, 1e-6)
    ox, oy = (size - w * k) / 2, (size + h * k) / 2
    path = QPainterPath()
    for d in item["vectors"]:
        for cd in d.get("contours", []):
            try:
                pts = contour_from_dict(cd).flatten(max(w, h) / 300 or 0.1)
            except (KeyError, ValueError, TypeError):
                continue
            if not pts:
                continue
            path.moveTo(QPointF(ox + pts[0][0] * k, oy - pts[0][1] * k))
            for x, y in pts[1:]:
                path.lineTo(QPointF(ox + x * k, oy - y * k))
            if cd.get("closed"):
                path.closeSubpath()
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing)
    p.setPen(QPen(QColor(color), 1.2))
    p.drawPath(path)
    p.end()
    return pm


class VectorLibraryPanel(QWidget):
    """Groups (in groups) of drawings: keep the selection in it, put a drawing in the open sheet (double click), drag to reorder."""

    def __init__(self, window, library: VectorLibrary | None = None, parent=None):
        super().__init__(parent)
        self.window = window
        self.lib = library or VectorLibrary.load()
        lay = QVBoxLayout(self)
        lay.setContentsMargins(2, 2, 2, 2)
        # (a tool bar: what does not fit goes behind its » button, so the panel never makes the docks wider)
        row = QToolBar()
        row.setIconSize(row.iconSize() * 0.8)

        def tb(text, tip, slot):
            a = row.addAction(text)
            a.setToolTip(tip)
            a.triggered.connect(lambda checked=False: self._safe(slot))
            return a

        tb("+📁", "New group (in the group that is selected: a subgroup)", self.new_group)
        tb("+▣", "Keep the selected vectors in the library (in the group that is selected)", self.add_selection)
        tb("⤓", "Put the drawing in the open sheet (double click does it too)", self.insert)
        tb("🗑", "Delete", self.delete)
        more = QToolButton()
        more.setText("⋯")
        more.setToolTip("Rename, move up / down, import, export ...")
        more.setPopupMode(QToolButton.InstantPopup)
        m = QMenu(more)
        m.addAction("Rename...", lambda: self._safe(self.rename))
        m.addAction("Move up", lambda: self._safe(self.shift, -1))
        m.addAction("Move down", lambda: self._safe(self.shift, 1))
        m.addSeparator()
        m.addAction("Import a library or a group...", lambda: self._safe(self.import_))
        m.addAction("Export the group that is selected...", lambda: self._safe(self.export))
        m.addAction("Move to the top level", lambda: self._safe(self.to_top))
        more.setMenu(m)
        row.addWidget(more)
        lay.addWidget(row)
        self.search = QLineEdit()
        self.search.setPlaceholderText("Search the library...")
        self.search.textChanged.connect(self._filter)
        lay.addWidget(self.search)
        self.tree = LibraryTree()
        self.tree.dropped.connect(lambda: self._safe(self._sync_from_tree))
        self.tree.itemDoubleClicked.connect(lambda it, col: self._safe(self.insert) if not it.data(0, GROUP_ROLE) else None)
        self.tree.currentItemChanged.connect(self._preview)
        self.tree.setContextMenuPolicy(Qt.CustomContextMenu)
        self.tree.customContextMenuRequested.connect(self._menu)
        self.tree.itemExpanded.connect(lambda *_: None)
        lay.addWidget(self.tree, 3)
        self.picture = QLabel()
        self.picture.setAlignment(Qt.AlignCenter)
        self.picture.setFixedHeight(110)                 # (the tree has the room: the picture is a glance)
        lay.addWidget(self.picture)
        self.info = QLabel()
        self.info.setWordWrap(True)
        self.info.setStyleSheet("color: gray")
        lay.addWidget(self.info)
        self.own_layers = QCheckBox("On the layers it came from")
        self.own_layers.setToolTip("Put a drawing on the layers it came from (made when missing); off: on the current layer")
        lay.addWidget(self.own_layers)
        self.refresh()

    # -- the tree -------------------------------------------------------------------------------------------------------
    def refresh(self, select: str | None = None) -> None:
        cur = select or self.current_id()
        open_ids = self._expanded()
        self.tree.blockSignals(True)
        self.tree.clear()
        target = None

        def fill(parent_item, g):
            nonlocal target
            for sub in g["groups"]:
                it = QTreeWidgetItem([f"📁 {sub['name']}  ({len(self.lib.items(sub))})"])
                it.setData(0, ID_ROLE, sub["id"])
                it.setData(0, GROUP_ROLE, True)
                it.setFlags(it.flags() | Qt.ItemIsDragEnabled | Qt.ItemIsDropEnabled)
                f = it.font(0)
                f.setBold(True)
                it.setFont(0, f)
                (parent_item.addChild(it) if parent_item else self.tree.addTopLevelItem(it))
                fill(it, sub)
                it.setExpanded(sub["id"] in open_ids or not open_ids)
                if sub["id"] == cur:
                    target = it
            for d in g["items"]:
                w, h = (d.get("size") or [0, 0])[:2]
                it = QTreeWidgetItem([f"{d['name']}   {w:g} × {h:g}"])
                it.setData(0, ID_ROLE, d["id"])
                it.setData(0, GROUP_ROLE, False)
                it.setFlags((it.flags() | Qt.ItemIsDragEnabled) & ~Qt.ItemIsDropEnabled)
                it.setToolTip(0, f"{d['name']}: {len(d['vectors'])} vector(s), {w:g} × {h:g} mm. Double click: put it in the sheet")
                (parent_item.addChild(it) if parent_item else self.tree.addTopLevelItem(it))
                if d["id"] == cur:
                    target = it

        fill(None, self.lib.root)
        self.tree.blockSignals(False)
        if target is not None:
            self.tree.setCurrentItem(target)
        self._filter(self.search.text())
        self._preview()

    def _expanded(self) -> set:
        out = set()

        def walk(it):
            if it.isExpanded():
                out.add(it.data(0, ID_ROLE))
            for k in range(it.childCount()):
                walk(it.child(k))
        for i in range(self.tree.topLevelItemCount()):
            walk(self.tree.topLevelItem(i))
        return out

    def _sync_from_tree(self) -> None:
        """A drag moved something: the library takes the order and the nesting of the tree."""
        nodes = {}

        def collect(g):
            for sub in g["groups"]:
                nodes[sub["id"]] = sub
                collect(sub)
            for it in g["items"]:
                nodes[it["id"]] = it
        collect(self.lib.root)

        def build(children) -> dict:
            g = {"groups": [], "items": []}
            for it in children:
                node = nodes.get(it.data(0, ID_ROLE))
                if node is None:
                    continue
                if it.data(0, GROUP_ROLE):
                    inner = build([it.child(k) for k in range(it.childCount())])
                    node["groups"], node["items"] = inner["groups"], inner["items"]
                    g["groups"].append(node)
                else:
                    g["items"].append(node)
                    for k in range(it.childCount()):                  # (a drawing dropped onto a drawing: it stays beside it)
                        sub = nodes.get(it.child(k).data(0, ID_ROLE))
                        if sub is not None:
                            g["groups" if it.child(k).data(0, GROUP_ROLE) else "items"].append(sub)
            return g
        top = build([self.tree.topLevelItem(i) for i in range(self.tree.topLevelItemCount())])
        self.lib.root["groups"], self.lib.root["items"] = top["groups"], top["items"]
        self._save()
        self.refresh()

    def _filter(self, text: str) -> None:
        t = text.strip().lower()

        def walk(it) -> bool:
            shown = False
            for k in range(it.childCount()):
                shown |= walk(it.child(k))
            hit = not t or t in it.text(0).lower() or shown
            it.setHidden(not hit)
            if t and shown:
                it.setExpanded(True)
            return hit
        for i in range(self.tree.topLevelItemCount()):
            walk(self.tree.topLevelItem(i))

    def current_id(self) -> str | None:
        it = self.tree.currentItem()
        return it.data(0, ID_ROLE) if it is not None else None

    def _group_for_new(self) -> str:
        """Where a new group or drawing goes: the group selected, or the group of the drawing selected."""
        nid = self.current_id()
        if nid is None:
            return ROOT
        node, parent = self.lib.find(nid)
        if node is None:
            return ROOT
        return node["id"] if self.lib.is_group(node) else (parent["id"] if parent else ROOT)

    def _preview(self, *_) -> None:
        nid = self.current_id()
        node, _ = self.lib.find(nid) if nid else (None, None)
        if node is None or self.lib.is_group(node):
            self.picture.clear()
            self.info.setText(f"{len(self.lib.items(node))} drawing(s) in this group" if node else
                              f"{len(self.lib.items())} drawing(s) in the library")
            return
        self.picture.setPixmap(thumbnail(node, 104))
        where = " ▸ ".join(self.lib.path_of(nid)) or "(top level)"
        self.info.setText(f"{where}  ·  {len(node['vectors'])} vector(s)")

    def _menu(self, pos) -> None:
        m = QMenu(self)
        it = self.tree.itemAt(pos)
        if it is not None:
            self.tree.setCurrentItem(it)
        m.addAction("New group here...", lambda: self._safe(self.new_group))
        m.addAction("Keep the selected vectors here...", lambda: self._safe(self.add_selection))
        if it is not None:
            if not it.data(0, GROUP_ROLE):
                m.addAction("Put in the sheet", lambda: self._safe(self.insert))
            m.addAction("Rename...", lambda: self._safe(self.rename))
            m.addAction("Delete", lambda: self._safe(self.delete))
            m.addAction("Move up", lambda: self._safe(self.shift, -1))
            m.addAction("Move down", lambda: self._safe(self.shift, 1))
            m.addAction("Move to the top level", lambda: self._safe(self.to_top))
        m.exec(self.tree.viewport().mapToGlobal(pos))

    # -- what is done ------------------------------------------------------------------------------------------------------
    def _safe(self, fn, *args):
        try:
            return fn(*args)
        except LibraryError as exc:
            QMessageBox.information(self, "Vector Library", str(exc))
        except OSError as exc:
            QMessageBox.warning(self, "Vector Library", f"The library could not be saved: {exc}")
        return None

    def _save(self) -> None:
        self.lib.save()

    def new_group(self) -> None:
        parent = self._group_for_new()
        where = " ▸ ".join(self.lib.path_of(parent) + ([self.lib.find(parent)[0]["name"]] if parent != ROOT else []))
        name, ok = QInputDialog.getText(self, "New group", f"Name of the group{' in ' + where if where else ''}:")
        if not ok or not name.strip():
            return
        g = self.lib.add_group(name, parent)
        self._save()
        self.refresh(g["id"])

    def add_selection(self) -> None:
        ctrl = self.window.ctrl
        objs = ctrl.selected_objects()
        if not objs:
            raise LibraryError("Select the vectors to keep in the library first.")
        name, ok = QInputDialog.getText(self, "Keep in the library", f"Name of the drawing ({len(objs)} vector(s)):",
                                        text=objs[0].name or "")
        if not ok:
            return
        it = self.lib.add_item(name, objs, ctrl.project, self._group_for_new())
        self._save()
        self.refresh(it["id"])

    def insert(self) -> list:
        """The drawing selected, in the middle of what the open sheet shows (its vectors selected: one undo step)."""
        nid = self.current_id()
        node, _ = self.lib.find(nid) if nid else (None, None)
        if node is None or self.lib.is_group(node):
            raise LibraryError("Select a drawing of the library (not a group).")
        ctrl = self.window.ctrl
        w, h = (node.get("size") or [0, 0])[:2]
        cv = getattr(self.window, "canvas", None)
        if cv is not None and cv.width() > 0:
            cx, cy = cv.screen_to_world(QPointF(cv.width() / 2, cv.height() / 2))
        else:
            cx, cy = ctrl.sheet.width / 2, ctrl.sheet.height / 2
        objs = self.lib.vectors_of(node, ctrl.layer_id, (cx - w / 2, cy - h / 2), ctrl.project,
                                   own_layers=self.own_layers.isChecked())
        ctrl.add_vectors(objs, f"Library: {node['name']}")
        return objs

    def rename(self) -> None:
        nid = self.current_id()
        if nid is None:
            return
        node, _ = self.lib.find(nid)
        name, ok = QInputDialog.getText(self, "Rename", "New name:", text=node["name"])
        if ok and name.strip():
            self.lib.rename(nid, name)
            self._save()
            self.refresh(nid)

    def delete(self) -> None:
        nid = self.current_id()
        if nid is None:
            return
        node, _ = self.lib.find(nid)
        what = (f"the group '{node['name']}' and the {len(self.lib.items(node))} drawing(s) in it"
                if self.lib.is_group(node) else f"the drawing '{node['name']}'")
        if QMessageBox.question(self, "Vector Library", f"Delete {what}?") != QMessageBox.Yes:
            return
        self.lib.remove(nid)
        self._save()
        self.refresh()

    def shift(self, step: int) -> None:
        nid = self.current_id()
        if nid is not None:
            self.lib.shift(nid, step)
            self._save()
            self.refresh(nid)

    def to_top(self) -> None:
        nid = self.current_id()
        if nid is not None:
            self.lib.move(nid, ROOT)
            self._save()
            self.refresh(nid)

    def import_(self) -> None:
        ctrl = self.window.ctrl
        fn = open_file(self, ctrl.settings, "veclib", "Import a vector library", "Vector library (*.icamlib *.json)")
        if not fn:
            return
        try:
            data = json.loads(Path(fn).read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise LibraryError(f"This is not a vector library: {exc}") from exc
        g = self.lib.import_data(data, self._group_for_new())
        if not g["name"] or g["name"] == "Imported":
            g["name"] = Path(fn).stem
        self._save()
        self.refresh(g["id"])

    def export(self) -> None:
        ctrl = self.window.ctrl
        nid = self.current_id() or ROOT
        node, _ = self.lib.find(nid)
        fn, _ = save_file(self, ctrl.settings, "veclib", "Export from the vector library",
                          f"{node['name'] or 'library'}.icamlib", "Vector library (*.icamlib)")
        if fn:
            Path(fn).write_text(json.dumps(self.lib.export_group(nid), ensure_ascii=False), encoding="utf-8")

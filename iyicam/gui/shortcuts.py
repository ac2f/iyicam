"""Keyboard shortcuts of every menu item, changed by the person and kept in the settings; and the window that changes them.

Every item of the menus (the drawing tools of the Draw menu, the pages of the transform panel and the commands of plugins
too) has an id made of its menu path: ``Edit/Duplicate``, ``Vectors/Transform/Rotate``.  The settings keep only what differs
from the program's own keys (``Settings.shortcuts``: id -> list of key strings, ``[]`` for none), so a new version of the
program can bring new keys without overwriting the person's.
"""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QAction, QKeySequence
from PySide6.QtWidgets import (QDialog, QDialogButtonBox, QHBoxLayout, QKeySequenceEdit, QLabel, QLineEdit, QMenu,
                               QMessageBox, QPushButton, QTreeWidget, QTreeWidgetItem, QVBoxLayout)


def clean(text: str) -> str:
    """The text of a menu item without its accelerator (&) and its "..."."""
    return text.replace("&&", "\0").replace("&", "").replace("\0", "&").rstrip(".").strip()


def keys_of(action: QAction) -> list[str]:
    return [k.toString(QKeySequence.PortableText) for k in action.shortcuts() if not k.isEmpty()]


def collect(menubar, skip: tuple[str, ...] = ("Window", "Open Recent")) -> dict[str, QAction]:
    """Every item of the menus by its id (the menu path), in the order of the menus."""
    out: dict[str, QAction] = {}

    def walk(menu: QMenu, path: list[str]) -> None:
        for a in menu.actions():
            if a.isSeparator():
                continue
            name = clean(a.text())
            if not name:
                continue
            sub = a.menu()
            if sub is not None:
                if name not in skip:
                    walk(sub, path + [name])
                continue
            aid = "/".join(path + [name])
            n = 2
            while aid in out:                          # (two items of the same name in one menu)
                aid = "/".join(path + [f"{name} ({n})"])
                n += 1
            out[aid] = a

    for a in menubar.actions():
        if a.menu() is not None and clean(a.text()) not in skip:
            walk(a.menu(), [clean(a.text())])
    return out


def apply(actions: dict[str, QAction], defaults: dict[str, list[str]], overrides: dict) -> None:
    """Put the program's keys back and the person's over them."""
    for aid, a in actions.items():
        keys = overrides.get(aid) if isinstance(overrides, dict) else None
        if not isinstance(keys, list):
            keys = defaults.get(aid, [])
        a.setShortcuts([QKeySequence(k) for k in keys if k])
        tip = a.toolTip()
        if keys and a.text() and "(" not in tip:
            a.setToolTip(f"{clean(a.text())} ({', '.join(keys)})")


class ShortcutsDialog(QDialog):
    """Every function of the menus with its keyboard shortcut: choose one, press the keys, OK."""

    def __init__(self, actions: dict[str, QAction], defaults: dict[str, list[str]], overrides: dict, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Keyboard Shortcuts")
        self.resize(620, 640)
        self.actions = actions
        self.defaults = defaults
        self.keys: dict[str, list[str]] = {}
        for aid in actions:
            o = overrides.get(aid) if isinstance(overrides, dict) else None
            self.keys[aid] = list(o) if isinstance(o, list) else list(defaults.get(aid, []))
        lay = QVBoxLayout(self)
        self.filter = QLineEdit()
        self.filter.setPlaceholderText("Search a function or a key (Ctrl+D) ...")
        self.filter.textChanged.connect(self._filter)
        lay.addWidget(self.filter)
        self.tree = QTreeWidget()
        self.tree.setHeaderLabels(["Function", "Shortcut"])
        self.tree.setColumnWidth(0, 400)
        self.tree.currentItemChanged.connect(self._picked)
        lay.addWidget(self.tree, 1)
        row = QHBoxLayout()
        row.addWidget(QLabel("Shortcut:"))
        self.edit = QKeySequenceEdit()
        self.edit.setToolTip("Click here and press the keys")
        self.edit.editingFinished.connect(self._edited)
        row.addWidget(self.edit, 1)
        clear = QPushButton("None")
        clear.setToolTip("No shortcut for this function")
        clear.clicked.connect(lambda: self._set_current([]))
        default = QPushButton("Default")
        default.setToolTip("The program's own shortcut for this function")
        default.clicked.connect(lambda: self._set_current(list(self.defaults.get(self._current_id() or "", []))))
        row.addWidget(clear)
        row.addWidget(default)
        lay.addLayout(row)
        self.note = QLabel()
        self.note.setWordWrap(True)
        self.note.setStyleSheet("color: gray")
        lay.addWidget(self.note)
        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel | QDialogButtonBox.RestoreDefaults)
        bb.button(QDialogButtonBox.RestoreDefaults).setText("Reset all")
        bb.button(QDialogButtonBox.RestoreDefaults).clicked.connect(self.reset_all)
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)
        lay.addWidget(bb)
        self.items: dict[str, QTreeWidgetItem] = {}
        self._fill()

    # -- the list --------------------------------------------------------------------------------------------------------
    def _fill(self) -> None:
        self.tree.clear()
        self.items.clear()
        heads: dict[str, QTreeWidgetItem] = {}
        for aid in self.actions:
            path, _, name = aid.rpartition("/")
            head = heads.get(path)
            if head is None:
                head = QTreeWidgetItem([path.replace("/", " ▸ "), ""])
                head.setFlags(head.flags() & ~Qt.ItemIsSelectable)
                f = head.font(0)
                f.setBold(True)
                head.setFont(0, f)
                self.tree.addTopLevelItem(head)
                head.setExpanded(True)
                heads[path] = head
            it = QTreeWidgetItem([name, ""])
            it.setData(0, Qt.UserRole, aid)
            head.addChild(it)
            self.items[aid] = it
            self._show(aid)

    def _show(self, aid: str) -> None:
        it = self.items[aid]
        it.setText(1, ", ".join(self.keys[aid]))
        changed = self.keys[aid] != list(self.defaults.get(aid, []))
        f = it.font(1)
        f.setBold(changed)
        it.setFont(1, f)

    def _filter(self, text: str) -> None:
        t = text.strip().lower()
        for i in range(self.tree.topLevelItemCount()):
            head = self.tree.topLevelItem(i)
            any_shown = False
            for k in range(head.childCount()):
                it = head.child(k)
                aid = it.data(0, Qt.UserRole)
                hit = not t or t in aid.lower() or any(t in key.lower() for key in self.keys[aid])
                it.setHidden(not hit)
                any_shown |= hit
            head.setHidden(not any_shown)

    def _current_id(self) -> str | None:
        it = self.tree.currentItem()
        return it.data(0, Qt.UserRole) if it is not None else None

    def _picked(self, cur, prev) -> None:
        aid = self._current_id()
        self.edit.blockSignals(True)
        self.edit.setKeySequence(QKeySequence(self.keys[aid][0]) if aid and self.keys[aid] else QKeySequence())
        self.edit.blockSignals(False)
        self.edit.setEnabled(aid is not None)
        if aid:
            d = ", ".join(self.defaults.get(aid, [])) or "none"
            self.note.setText(f"{aid.replace('/', ' ▸ ')} — the program's own: {d}")

    # -- changing a key --------------------------------------------------------------------------------------------------
    def _edited(self) -> None:
        seq = self.edit.keySequence()
        key = seq[0].toCombined() if seq.count() else 0
        text = QKeySequence(key).toString(QKeySequence.PortableText) if key else ""
        self._set_current([text] if text else [])

    def users_of(self, key: str, but: str | None = None) -> list[str]:
        want = QKeySequence(key)
        return [aid for aid, ks in self.keys.items() if aid != but and any(QKeySequence(k) == want for k in ks)]

    def _set_current(self, keys: list[str]) -> None:
        aid = self._current_id()
        if aid is None:
            return
        for key in keys:
            for other in self.users_of(key, but=aid):
                ans = QMessageBox.question(self, "Keyboard Shortcuts",
                                           f"{key} is the shortcut of\n{other.replace('/', ' ▸ ')}.\n\nTake it from there?")
                if ans != QMessageBox.Yes:
                    self._picked(None, None)
                    return
                self.keys[other] = [k for k in self.keys[other] if QKeySequence(k) != QKeySequence(key)]
                self._show(other)
        self.keys[aid] = keys
        self._show(aid)
        self._picked(None, None)

    def reset_all(self) -> None:
        for aid in self.keys:
            self.keys[aid] = list(self.defaults.get(aid, []))
            self._show(aid)
        self._picked(None, None)

    def overrides(self) -> dict[str, list[str]]:
        """What differs from the program's own keys (what the settings keep)."""
        return {aid: ks for aid, ks in self.keys.items() if ks != list(self.defaults.get(aid, []))}

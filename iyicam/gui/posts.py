"""The parts of the post-processor windows: G-code you can click to read what a line says, the glossary of G-code
words (what I and J are), a form for the options of a post and an editor for its blocks of text.

The words of every option and block come from the engine's own description (``iyicam.post.editing.specs``), the
same one the page is built from, so a new option of the engine is in both without a change here."""
from __future__ import annotations

import html
import math

from PySide6.QtCore import QPointF, Qt, Signal
from PySide6.QtGui import QColor, QFontDatabase, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import (QCheckBox, QComboBox, QDialog, QDoubleSpinBox, QGroupBox, QHBoxLayout, QLabel, QLineEdit,
                               QListWidget, QListWidgetItem, QPlainTextEdit, QPushButton, QScrollArea, QSpinBox,
                               QTextBrowser, QVBoxLayout, QWidget)

from ..post import editing, guide


def _mono():
    return QFontDatabase.systemFont(QFontDatabase.FixedFont)


# ---------------------------------------------------------------------------
# reading G-code
# ---------------------------------------------------------------------------

def explanation_html(r: dict) -> str:
    """What :func:`iyicam.post.guide.explain` says about a line, as a few lines of rich text."""
    esc = html.escape
    rows = "".join(f"<tr><td style='white-space:nowrap'><b>{esc(w['word'])}</b>&nbsp;&nbsp;</td><td>{esc(w['text'])}</td></tr>"
                   for w in r.get("words", []))
    return f"<p style='margin:0 0 4px 0'><b>{esc(r.get('summary') or '')}</b></p><table cellspacing='0'>{rows}</table>"


class ExplainedGcode(QWidget):
    """G-code to read: click a line and what it says appears under it, word by word (``G2 X20 Y0 I5 J0``: an arc,
    its centre as a place on the sheet, its radius ...).  ``text`` is the read-only text itself."""

    HINT = "Click a line to read what it says."

    def __init__(self, parent=None, note_height: int = 120):
        super().__init__(parent)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(4)
        self.text = QPlainTextEdit()
        self.text.setReadOnly(True)
        self.text.setFont(_mono())
        self.text.setLineWrapMode(QPlainTextEdit.NoWrap)
        lay.addWidget(self.text, 1)
        self.note = QTextBrowser()
        self.note.setMaximumHeight(note_height)
        self.note.setOpenLinks(False)
        lay.addWidget(self.note)
        row = QHBoxLayout()
        self.hint = QLabel(self.HINT)
        self.hint.setEnabled(False)
        row.addWidget(self.hint, 1)
        words = QPushButton("What are I J K?")
        words.setToolTip("What the letters and the codes of G-code mean, and how an arc is written")
        words.clicked.connect(lambda: GlossaryDialog(self).exec())
        row.addWidget(words)
        lay.addLayout(row)
        self._ex: guide.Explainer | None = None
        self._setting = False
        self.text.cursorPositionChanged.connect(self._explain)

    def set_gcode(self, text: str, inch: bool | None = None, arc_absolute: bool = False) -> None:
        text = text.replace("\r\n", "\n")
        self._ex = guide.Explainer(text.split("\n"), "en", inch=inch, arc_absolute=arc_absolute)
        self._put(text)

    def show_message(self, text: str) -> None:
        """Something that is not G-code (an error, a note): shown as it is, and clicking it explains nothing."""
        self._ex = None
        self._put(text)

    def _put(self, text: str) -> None:
        self._setting = True
        try:
            self.text.setPlainText(text)
        finally:
            self._setting = False
        self.note.clear()

    def _explain(self) -> None:
        if self._ex is None or self._setting:
            return
        i = self.text.textCursor().blockNumber()
        try:
            r = self._ex.at(i)
        except IndexError:
            return
        self.note.setHtml(explanation_html(r))


class ArcFigure(QWidget):
    """An arc with its start S, its end E and its centre C, and I and J drawn from the start: what they measure."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumSize(260, 170)

    def paintEvent(self, _event) -> None:      # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        pal = self.palette()
        ink, accent, warn = pal.windowText().color(), QColor("#15803d"), QColor("#b45309")
        w, h = self.width(), self.height()
        k = min(w / 230.0, h / 150.0)
        ox, oy = (w - 230 * k) / 2, (h - 150 * k) / 2

        def pt(x, y):
            return QPointF(ox + x * k, oy + y * k)

        s, e, c = pt(50, 120), pt(70, 20), pt(110, 80)
        r = 72.1 * k
        p.setPen(QPen(accent, 3))
        # the arc from S to E turning clockwise on the screen about C
        path = QPainterPath(s)
        a0 = math.degrees(math.atan2(-(s.y() - c.y()), s.x() - c.x()))
        a1 = math.degrees(math.atan2(-(e.y() - c.y()), e.x() - c.x()))
        sweep = -((a0 - a1) % 360.0)                  # (clockwise is negative in Qt's angles)
        path.arcTo(c.x() - r, c.y() - r, 2 * r, 2 * r, a0, sweep)
        p.drawPath(path)
        pen = QPen(warn, 2, Qt.DashLine)
        p.setPen(pen)
        p.drawLine(s, pt(110, 120))
        p.drawLine(pt(110, 120), c)
        p.setPen(Qt.NoPen)
        p.setBrush(warn)
        p.drawEllipse(c, 4, 4)
        p.setBrush(accent)
        p.drawEllipse(s, 4, 4)
        p.drawEllipse(e, 4, 4)
        f = p.font()
        f.setBold(True)
        p.setFont(f)
        p.setPen(ink)
        p.drawText(pt(30, 128), "S")
        p.drawText(pt(76, 16), "E")
        p.drawText(pt(118, 78), "C")
        p.setPen(warn)
        p.drawText(pt(76, 138), "I")
        p.drawText(pt(116, 104), "J")
        p.end()


class GlossaryDialog(QDialog):
    """What the letters and the codes of G-code mean, and how an arc is read (the words I and J)."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("G-code words")
        lay = QVBoxLayout(self)
        lay.addWidget(QLabel("<b>How to read an arc line</b>"))
        lay.addWidget(ArcFigure(), 0, Qt.AlignHCenter)
        esc = html.escape
        body = "".join(f"<p>{esc(t)}</p>" for t in guide.ARC_HELP["en"])
        for title, key in (("Letters", "letter"), ("G codes (moves and modes)", "g"), ("M codes (machine)", "m")):
            body += f"<h4>{title}</h4><table cellspacing='0' cellpadding='2'>"
            body += "".join(f"<tr><td style='white-space:nowrap'><b>{esc(g['word'])}</b>&nbsp;&nbsp;</td><td>{esc(g['text'])}</td></tr>"
                            for g in guide.glossary("en") if g["group"] == key)
            body += "</table>"
        self.view = QTextBrowser()
        self.view.setHtml(body)
        lay.addWidget(self.view, 1)
        close = QPushButton("Close")
        close.clicked.connect(self.accept)
        row = QHBoxLayout()
        row.addStretch(1)
        row.addWidget(close)
        lay.addLayout(row)
        self.resize(640, 640)


# ---------------------------------------------------------------------------
# the options of a post
# ---------------------------------------------------------------------------

# which options matter, given the others (the rest are hidden, not removed)
SHOWN = {
    "line_number_start": lambda o: o.get("line_numbers"), "line_number_step": lambda o: o.get("line_numbers"),
    "line_number_max": lambda o: o.get("line_numbers"), "line_number_space": lambda o: o.get("line_numbers"),
    "ijk": lambda o: o.get("arcs") == "ijk", "arc_max_sweep": lambda o: o.get("arcs") != "none",
    "arc_min_radius": lambda o: o.get("arcs") != "none", "arc_max_radius": lambda o: o.get("arcs") != "none",
    "helical": lambda o: o.get("arcs") != "none", "arc_xy_always": lambda o: o.get("arcs") != "none",
}


class OptionsForm(QScrollArea):
    """A field for every option of a post, in groups, with the engine's sentence of help under it.  ``draft`` (the
    options of a post) is changed in place; ``changed`` is emitted after each change made by a person."""

    changed = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWidgetResizable(True)
        self.setFrameShape(QScrollArea.NoFrame)
        inner = QWidget()
        self.setWidget(inner)
        lay = QVBoxLayout(inner)
        self.options: dict = {}
        self._orig: dict = {}
        self._building = False
        self.fields: dict[str, tuple[QWidget, QWidget, QLabel]] = {}      # key -> (row, input, mark)
        spec = editing.specs()
        for g in spec["groups"]:
            box = QGroupBox(g["label"]["en"])
            gl = QVBoxLayout(box)
            for o in (o for o in spec["options"] if o["group"] == g["id"]):
                gl.addWidget(self._field(o))
            lay.addWidget(box)
        lay.addStretch(1)

    def _field(self, o: dict) -> QWidget:
        key, kind = o["key"], o["kind"]
        row = QWidget()
        rl = QVBoxLayout(row)
        rl.setContentsMargins(0, 2, 0, 2)
        rl.setSpacing(1)
        top = QHBoxLayout()
        mark = QLabel("●")
        mark.setStyleSheet("color:#15803d")
        mark.setToolTip("Changed from the post this one is based on")
        mark.setVisible(False)
        if kind == "bool":
            w = QCheckBox(o["label"]["en"])
            w.toggled.connect(lambda v, k=key: self._set(k, bool(v)))
            top.addWidget(w, 1)
        else:
            top.addWidget(QLabel(o["label"]["en"]))
            if kind == "choice":
                w = QComboBox()
                for v, label in o["choices"]:
                    w.addItem(label["en"], v)
                w.currentIndexChanged.connect(lambda _i, k=key, w=w: self._set(k, w.currentData()))
            elif kind == "int":
                w = QSpinBox()
                w.setRange(int(o.get("min", -10 ** 9)), int(o.get("max", 10 ** 9)))
                w.valueChanged.connect(lambda v, k=key: self._set(k, int(v)))
            elif kind == "float":
                w = QDoubleSpinBox()
                w.setDecimals(4)
                w.setRange(float(o.get("min", -1e9)), float(o.get("max", 1e9)))
                w.valueChanged.connect(lambda v, k=key: self._set(k, float(v)))
            else:
                w = QLineEdit()
                w.setMaxLength(8)
                w.textEdited.connect(lambda v, k=key: self._set(k, v))
            top.addStretch(1)
            top.addWidget(w)
        top.addWidget(mark)
        rl.addLayout(top)
        help_ = o["help"]["en"]
        if help_:
            lab = QLabel(help_)
            lab.setWordWrap(True)
            lab.setEnabled(False)
            rl.addWidget(lab)
            w.setToolTip(help_)
        self.fields[key] = (row, w, mark)
        return row

    # -- the model -------------------------------------------------------------------------
    def _set(self, key: str, value) -> None:
        if self._building:
            return
        self.options[key] = value
        self._refresh()
        self.changed.emit()

    def _refresh(self) -> None:
        for key, (row, _w, mark) in self.fields.items():
            need = SHOWN.get(key)
            row.setVisible(True if need is None else bool(need(self.options)))
            mark.setVisible(self.options.get(key) != self._orig.get(key))

    def load(self, options: dict, original: dict | None = None, editable: bool = True) -> None:
        """Show ``options`` (a copy is kept), compare them with ``original`` for the marks of what was changed."""
        self.options = dict(options)
        self._orig = dict(original if original is not None else options)
        self._building = True
        try:
            for key, (_row, w, _mark) in self.fields.items():
                v = self.options.get(key)
                if isinstance(w, QCheckBox):
                    w.setChecked(bool(v))
                elif isinstance(w, QComboBox):
                    w.setCurrentIndex(max(0, w.findData(v)))
                elif isinstance(w, (QSpinBox, QDoubleSpinBox)):
                    w.setValue(v if isinstance(v, (int, float)) and not isinstance(v, bool) else 0)
                elif isinstance(w, QLineEdit):
                    w.setText("" if v is None else str(v))
                w.setEnabled(editable)
        finally:
            self._building = False
        self._refresh()


# ---------------------------------------------------------------------------
# the blocks of a post
# ---------------------------------------------------------------------------

class BlocksEditor(QWidget):
    """The blocks of text of a post (start of the file, tool change, a move ...): pick one, edit it, and put a
    variable or a word of a move where the cursor is.  ``blocks`` is changed in place."""

    changed = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        spec = editing.specs()
        self.specs = {b["name"]: b for b in spec["blocks"]}
        self.vars = spec["variables"]
        self.blocks: dict = {}
        self._orig: dict = {}
        self._building = False
        self._editable = True
        lay = QHBoxLayout(self)
        self.list = QListWidget()
        groups = {g["id"]: g["label"]["en"] for g in spec["block_groups"]}
        for b in spec["blocks"]:
            it = QListWidgetItem(f"{groups[b['group']]} ▸ {b['label']['en']}")
            it.setData(Qt.UserRole, b["name"])
            self.list.addItem(it)
        self.list.currentRowChanged.connect(self._show)
        self.list.setMinimumWidth(210)
        lay.addWidget(self.list, 1)
        right = QVBoxLayout()
        self.title = QLabel()
        self.title.setWordWrap(True)
        right.addWidget(self.title)
        self.edit = QPlainTextEdit()
        self.edit.setFont(_mono())
        self.edit.setLineWrapMode(QPlainTextEdit.NoWrap)
        self.edit.textChanged.connect(self._typed)
        right.addWidget(self.edit, 1)
        row = QHBoxLayout()
        self.words = QComboBox()
        self.words.activated.connect(lambda i: self._insert(self.words, i))
        self.vars_box = QComboBox()
        self.vars_box.activated.connect(lambda i: self._insert(self.vars_box, i))
        self.reset_btn = QPushButton("Back to the first")
        self.reset_btn.setToolTip("Put the block back as it was when this post was opened")
        self.reset_btn.clicked.connect(self._reset)
        for w in (self.words, self.vars_box):
            row.addWidget(w, 1)
        row.addWidget(self.reset_btn)
        right.addLayout(row)
        hint = QLabel("Where {name} is written the program puts its value, for example {safe_z}.")
        hint.setEnabled(False)
        right.addWidget(hint)
        lay.addLayout(right, 3)

    def _name(self) -> str | None:
        it = self.list.currentItem()
        return it.data(Qt.UserRole) if it is not None else None

    def _label_of(self, name: str) -> str:
        changed = self.blocks.get(name, "") != self._orig.get(name, "")
        b = self.specs[name]
        return f"{b['label']['en']}" + ("  ●" if changed else "")

    def _marks(self) -> None:
        for i in range(self.list.count()):
            it = self.list.item(i)
            head = it.text().split(" ▸ ")[0]
            it.setText(f"{head} ▸ {self._label_of(it.data(Qt.UserRole))}")

    def load(self, blocks: dict, original: dict | None = None, editable: bool = True) -> None:
        self.blocks = dict(blocks)
        self._orig = dict(original if original is not None else blocks)
        self._editable = editable
        self.edit.setReadOnly(not editable)
        self.words.setEnabled(editable)
        self.vars_box.setEnabled(editable)
        self._marks()
        row = self.list.currentRow()
        self.list.setCurrentRow(row if row >= 0 else 0)
        self._show(self.list.currentRow())

    def _show(self, row: int) -> None:
        name = self._name()
        if name is None:
            return
        b = self.specs[name]
        self.title.setText(f"<b>{html.escape(b['label']['en'])}</b> · {html.escape(b['when']['en'])}")
        self._building = True
        try:
            self.edit.setPlainText(self.blocks.get(name, ""))
        finally:
            self._building = False
        self.words.clear()
        self.words.addItem("Insert a word…")
        for w in b["words"] + b["extra"]:
            self.words.addItem(w, "{" + w + "}")
        self.vars_box.clear()
        self.vars_box.addItem("Insert a variable…")
        for v in self.vars:
            if v["group"] == "job" or (b["vars"] == "tool" and v["group"] == "tool"):
                self.vars_box.addItem(f"{v['name']}  —  {v['text']['en']}", "{" + v["name"] + "}")
        self.reset_btn.setEnabled(self._editable and self.blocks.get(name, "") != self._orig.get(name, ""))

    def _typed(self) -> None:
        name = self._name()
        if self._building or name is None or not self._editable:
            return
        self.blocks[name] = self.edit.toPlainText()
        self.reset_btn.setEnabled(self.blocks[name] != self._orig.get(name, ""))
        self._marks()
        self.changed.emit()

    def _insert(self, combo: QComboBox, i: int) -> None:
        text = combo.itemData(i)
        combo.setCurrentIndex(0)
        if text and self._editable:
            self.edit.insertPlainText(text)
            self.edit.setFocus()

    def _reset(self) -> None:
        name = self._name()
        if name is not None and self._editable:
            self.edit.setPlainText(self._orig.get(name, ""))

"""The warning about open vectors before nesting and before toolpaths that need closed vectors: it appears only when
something is open, its buttons do what they say (close with one undo step, select, go on, cancel), a single straight line
is never "closed", an on-the-line profile does not ask, and "do not warn me again" is kept."""
import pytest

pytest.importorskip("PySide6.QtWidgets")

from PySide6.QtWidgets import QMessageBox  # noqa: E402

from gui_robot import rwin_gen  # noqa: E402
from iyicam.core import shapes  # noqa: E402
from iyicam.core.geom import Contour, Line  # noqa: E402
from iyicam.gui import openwarn  # noqa: E402


@pytest.fixture
def rwin(tmp_path, monkeypatch):
    yield from rwin_gen(tmp_path, monkeypatch)


def answer(monkeypatch, label, never_again=False):
    """Make the warning click the button ``label`` (and tick "do not warn me again"); the texts shown are kept."""
    shown, chosen = [], {}

    def exec_(box):
        shown.append(box.text())
        if never_again:
            box.checkBox().setChecked(True)
        chosen["b"] = box.button(QMessageBox.Cancel) if label == "Cancel" else \
            next(b for b in box.buttons() if b.text() == label)
        return 0
    monkeypatch.setattr(QMessageBox, "exec", exec_)
    monkeypatch.setattr(QMessageBox, "clickedButton", lambda box: chosen.get("b"))
    return shown


def drawing(win):
    c = win.ctrl
    gap = Contour([Line((0, 0), (100, 0)), Line((100, 0), (100, 80)), Line((100, 80), (0, 80)), Line((0, 80), (0, 0.3))])
    line = Contour([Line((200, 0), (300, 0))])
    from iyicam.core.document import VectorObject
    lid = c.project.layers[0].id
    with c.edit("draw"):
        c.sheet.vectors[:] = [VectorObject([gap], lid), VectorObject([line], lid),
                              VectorObject([shapes.rectangle(400, 0, 50, 50)], lid)]
    return c, c.sheet.vectors


def test_close_and_continue_closes_the_open_outline_in_one_undo_step(rwin, monkeypatch):
    win, _ = rwin
    c, (frame, line, box) = drawing(win)
    shown = answer(monkeypatch, "Close and continue")
    assert openwarn.ask(win, c, [frame, line, box], "nesting") is True
    assert "2 contour(s) of 2 vector(s) are not closed" in shown[0] and "1 nearly closed" in shown[0]
    assert "0.3 mm apart" in shown[0] and "1 open line(s)" in shown[0] and "thin lines" in shown[0]
    assert frame.contours[0].closed and frame.contours[0].segs[-1].b == frame.contours[0].start
    assert not line.contours[0].closed                          # (a single straight line is left as it is)
    c.undo()
    assert not c.sheet.vectors[0].contours[0].closed


def test_select_them_selects_the_open_vectors_and_stops(rwin, monkeypatch):
    win, _ = rwin
    c, (frame, line, box) = drawing(win)
    answer(monkeypatch, "Select them")
    assert openwarn.ask(win, c, [frame, line, box], "nesting") is False
    assert set(c.selection) == {frame.id, line.id}


def test_continue_cancel_and_never_again(rwin, monkeypatch):
    win, _ = rwin
    c, (frame, line, box) = drawing(win)
    answer(monkeypatch, "Continue anyway")
    assert openwarn.ask(win, c, [frame], "this Pocket toolpath") is True and not frame.contours[0].closed
    answer(monkeypatch, "Cancel")
    assert openwarn.ask(win, c, [frame], "nesting") is False
    shown = answer(monkeypatch, "Continue anyway", never_again=True)
    assert openwarn.ask(win, c, [frame], "nesting") is True and shown
    shown = answer(monkeypatch, "Cancel")
    assert openwarn.ask(win, c, [frame], "nesting") is True and not shown      # (not asked any more)


def test_nothing_open_nothing_asked_and_which_toolpaths_ask(rwin, monkeypatch):
    win, _ = rwin
    c, (frame, line, box) = drawing(win)
    shown = answer(monkeypatch, "Cancel")
    assert openwarn.ask(win, c, [box], "nesting") is True and not shown
    ops = c.registry.operations
    assert openwarn.needs_closed(ops["pocket"], {})
    assert openwarn.needs_closed(ops["profile"], {"side": "inside"})
    assert not openwarn.needs_closed(ops["profile"], {"side": "on"})


def test_the_nesting_command_asks_first(rwin, monkeypatch):
    win, _ = rwin
    c, (frame, line, box) = drawing(win)
    shown = answer(monkeypatch, "Cancel")
    opened = []
    monkeypatch.setattr("iyicam.gui.main_window.NestingDialog", lambda *a, **k: opened.append(1))
    win.nest()
    assert shown and not opened                                   # (cancelled at the warning: no nesting window)

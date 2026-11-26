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


# ---------------------------------------------------------------------------------------------- what cannot be cut
from iyicam.cam import cutcheck  # noqa: E402
from iyicam.cam.job import compute_toolpath  # noqa: E402
from iyicam.core.document import Selection, ToolpathDef, VectorObject  # noqa: E402

from conftest import end_mill  # noqa: E402


def vecs(*cs):
    return [VectorObject([c] if not isinstance(c, list) else c, "L") for c in cs]


R = shapes.rectangle


@pytest.mark.parametrize("objs, kw, bad", [
    (vecs(R(0, 0, 100, 100), R(110, 0, 100, 100)), dict(tool_diameter=6, side="outside"), None),
    (vecs(R(0, 0, 100, 100), R(50, 50, 100, 100)), {}, "overlaps"),                    # (outlines cross)
    (vecs(R(0, 0, 100, 100), R(0, 0, 100, 100)), {}, "overlaps"),                      # (a copy on top)
    (vecs([R(0, 0, 100, 100), R(20, 20, 60, 60)]), dict(tool_diameter=6, side="outside"), None),   # (a hole)
    (vecs(R(0, 0, 100, 100), R(105, 0, 100, 100)), dict(tool_diameter=6, side="outside"), "tight"),
    (vecs(R(0, 0, 100, 100), R(106, 0, 100, 100)), dict(tool_diameter=6, side="outside"), None),   # (just enough)
    (vecs(R(0, 0, 100, 100), R(107, 0, 100, 100)), dict(tool_diameter=6, allowance=1, side="outside"), "tight"),
    (vecs([R(0, 0, 100, 100), R(20, 20, 60, 60)], R(23, 23, 54, 54)), dict(tool_diameter=6, side="outside"), "tight"),
    (vecs([R(0, 0, 100, 100), R(20, 20, 4, 60)]), dict(tool_diameter=6, side="outside"), "narrow"),
    (vecs(R(0, 0, 5, 60)), dict(tool_diameter=6, side="inside"), "narrow"),
    (vecs(R(0, 0, 100, 100), R(101, 0, 100, 100)), dict(tool_diameter=6, side="inside"), None),    # (inside: no gap)
])
def test_what_cannot_be_cut(objs, kw, bad):
    pr = cutcheck.check(objs, **kw)
    assert bool(pr) == (bad is not None), pr
    if bad:
        assert getattr(pr, bad)
        assert set(pr.vector_ids()) <= {o.id for o in objs}


def test_the_engine_does_not_cut_it():
    from conftest import make_project
    p, s, made = make_project([[R(0, 0, 100, 100)], [R(103, 0, 100, 100)]], size=(600, 400))
    t = ToolpathDef("p", "profile", end_mill(6), {"through": True, "side": "outside"}, Selection("vectors", [o.id for o in made], []))
    r = compute_toolpath(p, t, s)
    assert r.error and "closer together than the tool needs" in r.error and not r.toolpaths
    assert set(r.problem_vectors) == {o.id for o in made}
    t.params["side"] = "on"                                             # (on the line: cut)
    assert not compute_toolpath(p, t, s).error
    t.tool = end_mill(2)
    t.params["side"] = "outside"                                        # (a 2 mm tool fits in the 3 mm gap)
    assert not compute_toolpath(p, t, s).error


def test_the_window_refuses_and_selects(rwin, monkeypatch):
    win, _ = rwin
    c = win.ctrl
    lid = c.project.layers[0].id
    with c.edit("draw"):
        c.sheet.vectors[:] = [VectorObject([R(0, 0, 100, 100)], lid), VectorObject([R(50, 50, 100, 100)], lid)]
    t = ToolpathDef("p", "pocket", end_mill(6), {}, Selection("vectors", [o.id for o in c.sheet.vectors], []))
    shown = answer(monkeypatch, "Select them")
    assert openwarn.cuttable(win, c, t) is False
    assert "lie on one another" in shown[0]
    assert set(c.selection) == {o.id for o in c.sheet.vectors}

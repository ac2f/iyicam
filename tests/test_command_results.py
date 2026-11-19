"""A command gives its answer back: what ``run(ctx, values)`` returns, the report it hands over, and how every place that runs a
command shows it (the page, the desktop window and its status bar, the console).

Before, ``run_command`` threw the return value away and the only way back was ``ctx.message``: the desktop's status bar kept
the last of several messages (a measurement's total was overwritten by the line after it) and the page showed three of them for
a few seconds, so a plugin that measured something seemed to give nothing back.
"""
import pytest

from conftest import make_project
from iyicam.core import shapes
from iyicam.core.commands import Command, CommandContext, report_text, run_command
from iyicam.core.registry import REGISTRY
from iyicam.service import ops


def ctx_of(selection=True):
    p, s, objs = make_project([shapes.rectangle(0, 0, 100, 50)])
    return CommandContext(p, s, [o.id for o in objs] if selection else []), p, s, objs


def run_returning(value):
    ctx, *_ = ctx_of()
    run_command(Command("t.cmd", "T", lambda c, v: value), ctx)
    return ctx


# ------------------------------------------------------------------------------------------------ what run() returns
def test_nothing_returned_is_nothing_said():
    ctx = run_returning(None)
    assert ctx.result() == {"title": "", "messages": [], "report": [], "warnings": [], "data": {}}


def test_a_returned_text_or_list_of_texts_is_said():
    assert run_returning("Total: 300 mm").messages == ["Total: 300 mm"]
    assert run_returning(["a", "b"]).messages == ["a", "b"]
    assert run_returning(("a", "b")).messages == ["a", "b"]


def test_a_returned_dict_is_the_whole_answer():
    ctx = run_returning({"title": "Measure", "messages": ["total"], "headline": ["same, the kit's word"],
                         "report": [{"title": "TOTAL", "rows": [["Length", 300.0]]}],
                         "sections": [{"title": "MORE", "rows": [("a", "b")]}],
                         "warnings": ["careful", "careful"], "data": {"total": 300.0}, "extra": 7})
    r = ctx.result()
    assert r["title"] == "Measure"
    assert r["messages"] == ["total", "same, the kit's word"]
    assert r["report"] == [{"title": "TOTAL", "rows": [["Length", "300.0"]]}, {"title": "MORE", "rows": [["a", "b"]]}]
    assert r["warnings"] == ["careful"]                                   # (said once)
    assert r["data"] == {"total": 300.0, "extra": 7}                      # (a key that is none of the above is data too)


def test_a_dict_of_numbers_alone_is_data_and_anything_else_is_a_value():
    assert run_returning({"total": 300.0}).result()["data"] == {"total": 300.0}
    assert run_returning(42).result()["data"] == {"value": 42}
    assert run_returning([1, 2]).result()["data"] == {"value": [1, 2]}


def test_what_is_said_while_running_and_what_is_returned_add_up():
    def run(ctx, values):
        ctx.message("first")
        ctx.section("S", [("k", 1)])
        ctx.warn("w")
        ctx.data["n"] = 3
        return {"messages": ["second"], "data": {"m": 4}}
    ctx, *_ = ctx_of()
    run_command(Command("t.cmd", "T", run), ctx)
    assert ctx.result() == {"title": "", "messages": ["first", "second"], "report": [{"title": "S", "rows": [["k", "1"]]}],
                            "warnings": ["w"], "data": {"n": 3, "m": 4}}


def test_a_report_reads_as_text():
    text = report_text({"title": "Measure", "report": [{"title": "TOTAL", "rows": [["Length", "300 mm"], ["Outlines", "1"]]}],
                        "warnings": ["open outline"]})
    assert text == "Measure\n\nTOTAL\n   Length   : 300 mm\n   Outlines : 1\n\n! open outline\n"
    assert report_text({}) == "\n"


# ------------------------------------------------------------------------------------------------ the page's engine
@pytest.fixture
def measuring_command(monkeypatch):
    def run(ctx, values):
        total = sum(c.length() for o in ctx.selected for c in o.contours)
        ctx.message(f"Total {total:.0f} mm")
        return {"title": "Length", "report": [{"title": "TOTAL", "rows": [["Length", f"{total:.0f} mm"]]}],
                "data": {"total": total}}
    monkeypatch.setitem(REGISTRY.commands, "t.measure", Command("t.measure", "Measure...", run, "Test", [], 1))


def call(selection):
    p, s, objs = make_project([shapes.rectangle(0, 0, 100, 50)])
    return ops.op_command({"project": p.to_dict(), "sheet": s.id, "selection": [objs[0].id] if selection else [],
                           "command": "t.measure"})


def test_the_engine_hands_the_result_to_the_page(measuring_command):
    got = call(True)
    assert got["messages"] == ["Total 300 mm"] and got["changed"] is False
    assert got["result"] == {"title": "Length", "messages": ["Total 300 mm"], "warnings": [], "data": {"total": 300.0},
                             "report": [{"title": "TOTAL", "rows": [["Length", "300 mm"]]}]}


def test_the_result_of_the_engine_is_plain_json(measuring_command):
    import json
    json.dumps(call(True))                                              # (a tuple or a float 'inf' in it would not survive the wire)


# ------------------------------------------------------------------------------------------------ the desktop
@pytest.fixture
def win():
    pytest.importorskip("PySide6.QtWidgets")
    from PySide6.QtWidgets import QApplication
    from iyicam.gui.app import create_window
    app, w = create_window(["test"], load_plugins=False)
    w.show()
    QApplication.processEvents()
    w.ctrl.settings.auto_recalculate = False
    yield w
    w.ctrl.dirty = False
    w.close()
    QApplication.processEvents()


def select_a_rectangle(ctrl):
    from iyicam.core.document import VectorObject
    o = VectorObject([shapes.rectangle(0, 0, 100, 50)], ctrl.project.layers[0].id)
    ctrl.sheet.vectors.append(o)
    ctrl.select([o.id])
    return o


def test_the_status_bar_keeps_every_message_not_only_the_last(win, measuring_command):
    win.ctrl.message.disconnect()                                       # (only the window's own listener below)
    seen = []
    win.ctrl.message.connect(seen.append)
    select_a_rectangle(win.ctrl)

    def three(ctx, values):
        ctx.message("total 300 mm")
        ctx.message("1 outline")
        ctx.message("preview is on the layers")
        ctx.warn("an open outline")
    cmd = Command("t.three", "Three", three, "Test")
    assert win.ctrl.run_command(cmd, {})
    assert seen == ["total 300 mm · 1 outline · preview is on the layers · ⚠ an open outline"]   # (one text: nothing overwrites another)


def test_a_command_that_reports_opens_a_window_with_the_report(win, measuring_command, monkeypatch):
    from PySide6.QtWidgets import QApplication
    from iyicam.gui import dialogs
    shown = []
    monkeypatch.setattr(dialogs.CommandReportDialog, "exec",
                        lambda self: shown.append((self.windowTitle(), self.text.toPlainText())) or 0)
    select_a_rectangle(win.ctrl)
    assert win.ctrl.run_command(REGISTRY.commands["t.measure"], {})
    QApplication.processEvents()
    assert shown == [("Length", "TOTAL\n   Length : 300 mm\n")]
    assert win.statusBar().currentMessage() == "Total 300 mm"
    assert win.ctrl.last_result["data"] == {"total": 300.0}


def test_a_script_can_ask_for_no_window(win, measuring_command, monkeypatch):
    from PySide6.QtWidgets import QApplication
    from iyicam.gui import dialogs
    shown = []
    monkeypatch.setattr(dialogs.CommandReportDialog, "exec", lambda self: shown.append(1) or 0)
    select_a_rectangle(win.ctrl)
    assert win.ctrl.run_command(REGISTRY.commands["t.measure"], {}, show_report=False)
    QApplication.processEvents()
    assert shown == [] and win.ctrl.last_result["report"][0]["title"] == "TOTAL"


def test_no_report_no_window(win, monkeypatch):
    from PySide6.QtWidgets import QApplication
    from iyicam.gui import dialogs
    shown = []
    monkeypatch.setattr(dialogs.CommandReportDialog, "exec", lambda self: shown.append(1) or 0)
    select_a_rectangle(win.ctrl)
    assert win.ctrl.run_command(Command("t.say", "Say", lambda c, v: "just a line"), {})
    QApplication.processEvents()
    assert shown == [] and win.statusBar().currentMessage() == "just a line"


def test_the_report_window_text_can_be_copied(win, measuring_command):
    from PySide6.QtWidgets import QApplication, QPushButton
    from iyicam.gui.dialogs import CommandReportDialog
    dlg = CommandReportDialog(win.ctrl, REGISTRY.commands["t.measure"],
                              {"title": "Length", "messages": ["Total 300 mm"], "warnings": ["w"],
                               "report": [{"title": "TOTAL", "rows": [["Length", "300 mm"]]}], "data": {}}, win)
    assert "Length : 300 mm" in dlg.text.toPlainText() and "! w" in dlg.text.toPlainText()
    next(b for b in dlg.findChildren(QPushButton) if b.text() == "Copy").click()
    assert QApplication.clipboard().text() == dlg.text.toPlainText()
    dlg.close()


def test_the_console_returns_what_the_command_handed_back(win, measuring_command):
    from iyicam.gui.console import Console
    select_a_rectangle(win.ctrl)
    console = Console(win.ctrl, win)
    r = console.ns["run"]("t.measure")
    assert r["data"] == {"total": 300.0} and "Length : 300 mm" in console.ns["report_text"](r)
    assert console.ns["run"]("edit.weld") is False                      # (a command that failed: False, as before)

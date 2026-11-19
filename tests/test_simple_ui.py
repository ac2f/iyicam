"""Rarely used settings are behind "More options": toolpath forms, the cut order panel, the post editor, the order strategies."""
import pytest

pytest.importorskip("PySide6.QtWidgets")

from gui_robot import process, rwin_gen  # noqa: E402
from test_cutedit import parts  # noqa: E402

from iyicam.builtins import ensure_registered  # noqa: E402
from iyicam.cam import sequence as seq  # noqa: E402
from iyicam.core.params import Param, is_advanced  # noqa: E402
from iyicam.core.registry import REGISTRY  # noqa: E402
from iyicam.gui.forms import ParamForm  # noqa: E402
from iyicam.post import editing  # noqa: E402


@pytest.fixture
def rwin(tmp_path, monkeypatch):
    yield from rwin_gen(tmp_path, monkeypatch)


def test_what_is_rarely_changed_is_advanced_and_what_is_needed_is_not():
    ensure_registered(REGISTRY)
    ps = {p.name: p for p in REGISTRY.operations["profile"].params()}
    assert not any(is_advanced(ps[n]) for n in ("through", "side", "tabs", "cut_depth"))
    assert all(is_advanced(ps[n]) for n in ("overcut", "ramp", "lead", "sharp_corners", "finish_pass"))
    assert is_advanced(Param("x", "X", "bool", False, advanced=True)) and not is_advanced(Param("x", "X", "bool", False))


def test_a_collapsed_form_shows_the_basics_and_the_rest_with_the_button(rwin):
    ensure_registered(REGISTRY)
    f = ParamForm(REGISTRY.operations["profile"].params(), None, None, None, collapse=True)
    f.show()
    shown = lambda: {n for n, (lay, w) in f._rows.items() if lay.isRowVisible(w)}
    assert shown() == {"through", "side", "tabs"} and "More options" in f.more.text()
    f.more.setChecked(True)
    assert {"overcut", "ramp", "direction"} <= shown() and "Fewer" in f.more.text()
    plain = ParamForm(REGISTRY.operations["profile"].params(), None, None, None)       # (a command's form shows all)
    plain.show()
    assert {"overcut", "ramp"} <= {n for n, (lay, w) in plain._rows.items() if lay.isRowVisible(w)}


def test_the_cut_order_panel_keeps_the_rarely_used_settings_behind_more_options(rwin):
    win, _ = rwin
    c, t, objs = parts(win)
    win.cut_order(t.id)
    process()
    p = win.cutorder_panel
    assert not p.band.isVisibleTo(p) and not p.margin.isVisibleTo(p) and not p._more_boxes[0].isVisibleTo(p)
    assert p.strategy.itemData(0) is None and p.strategy.itemData(1) == "cols_bl" and p.strategy.isVisibleTo(p) and p.smart.isVisibleTo(p)
    p.more.setChecked(True)
    assert p.band.isVisibleTo(p) and p.margin.isVisibleTo(p) and p._more_boxes[1].isVisibleTo(p)


def test_the_common_strategies_come_first_and_columns_are_the_default():
    names = [k for k, _ in seq.strategy_list()]                     # (group by group: columns, rows, routes, ...)
    assert names[:1] == ["cols_bl"] and names.index("rows_bl") < names.index("smart") < names.index("zigzag_up")
    assert sorted(names) == sorted(k for k, _ in seq.STRATEGIES)
    assert seq.settings(None)["strategy"] == "cols_bl"


def test_the_post_specs_say_which_options_and_blocks_are_common():
    sp = editing.specs()
    assert {o["key"] for o in sp["options"] if o["common"]} >= {"units", "decimals", "arcs"}
    assert {b["name"] for b in sp["blocks"] if b["common"]} == {"header", "footer", "tool_change"}
    assert any(not o["common"] for o in sp["options"])

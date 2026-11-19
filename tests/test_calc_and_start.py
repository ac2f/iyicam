"""Numbers typed as sums (desktop and page), and the first move of a file starting at the home Z."""
import re
import subprocess
from pathlib import Path

import pytest
from conftest import add_tp, end_mill, make_project

from iyicam.builtins import ensure_registered
from iyicam.cam.job import compute_all, sheet_toolpaths
from iyicam.core import shapes
from iyicam.core.calc import evaluate, is_sum
from iyicam.core.registry import REGISTRY
from iyicam.post.export import post_toolpaths

WEB = Path(__file__).resolve().parents[1] / "iyicam" / "web"


@pytest.mark.parametrize("text,want", [("12+3.5", 15.5), ("(100-8)/2", 46.0), ("2*7,5", 15.0), ("1,5+2,5", 4.0), ("-3+5", 2.0),
                                       ("2^3", 8.0), ("3x4", 12.0), ("5 - 2 ", 3.0), ("1e3+1", 1001.0), ("7", 7.0), (".5", 0.5)])
def test_sums_are_worked_out(text, want):
    assert evaluate(text) == pytest.approx(want)


@pytest.mark.parametrize("text", ["", "abc", "1.2.3", "10/0", "12+", "()", "2**3**", "__import__('os')", "1;2", "9^999"])
def test_what_is_not_a_sum_is_none_and_nothing_is_run(text):
    assert evaluate(text) is None


def test_is_sum_tells_a_sum_from_a_number():
    assert is_sum("1+2") and not is_sum("12,5") and not is_sum("-3") and is_sum("3*4")


def test_spin_boxes_take_sums():
    pytest.importorskip("PySide6.QtWidgets")
    from PySide6.QtWidgets import QApplication
    QApplication.instance() or QApplication([])
    from iyicam.gui.forms import OptionalNumber
    from iyicam.gui.wheelguard import QDoubleSpinBox, QSpinBox
    d = QDoubleSpinBox()
    d.setRange(-1e6, 1e6)
    d.setSuffix(" mm")
    d.setValue(5)
    for text, want in (("12+3,5 mm", 15.5), ("(100-8)/2", 46.0), ("abc", 46.0), ("12+", 46.0), ("2*7.5", 15.0)):
        d.lineEdit().setText(text)
        d.interpretText()
        assert d.value() == pytest.approx(want), text
    i = QSpinBox()
    i.setRange(0, 1000)
    i.lineEdit().setText("7*3+1")
    i.interpretText()
    assert i.value() == 22
    o = OptionalNumber(None)
    o.setText("10/4")
    assert o.value() == 2.5
    o.setText("")
    assert o.value() is None


def test_the_page_works_out_sums_the_same_way():
    node = subprocess.run(["node", "--version"], capture_output=True)
    if node.returncode:
        pytest.skip("no node")
    src = (WEB / "app.js").read_text(encoding="utf-8")
    m = re.search(r"function calc\(text\) \{.*?\n\}\nconst isSum", src, re.S)
    assert m
    body = m.group(0)[: -len("const isSum")]
    cases = [("12+3.5", 15.5), ("(100-8)/2", 46), ("2*7,5", 15), ("-3+5", 2), ("2^3", 8), ("1,5+2,5", 4), ("3x4", 12)]
    bad = ["", "abc", "12+", "1.2.3", "10/0", "()", "alert(1)"]
    js = body + "\nconst out=[" + ",".join(f"calc({t!r})" for t, _ in cases) + "];\nconst bad=[" + ",".join(
        f"calc({t!r})" for t in bad) + "];\nconsole.log(JSON.stringify([out, bad.map(Number.isNaN)]));"
    r = subprocess.run(["node", "-e", js], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    import json
    out, isnan = json.loads(r.stdout)
    assert out == [w for _, w in cases] and all(isnan)


def first_lines(home_z=None, header=None, n=2, **options):
    ensure_registered(REGISTRY)
    p, s, objs = make_project([[shapes.rectangle(10, 10, 100, 60)]], size=(500, 300))
    add_tp(p, "profile", end_mill(6), {"through": True}, objs)
    if home_z is not None:
        p.material.home = (0.0, 0.0, home_z)
    res = compute_all(p, REGISTRY)
    base = REGISTRY.posts["mach3"]
    post = type(base)(base.definition)
    post.options.update(options)
    # (a post of one's own: its start of the file and its tool change do not move Z)
    post.blocks["header"] = "%\nG90 G21" if header is None else header
    post.blocks["tool_change"] = "M5\nT{tool_number} M6\nS{spindle_speed} M3"
    text = post_toolpaths(p, s, sheet_toolpaths(p, res, s), post, "x")[0][1]
    lines = text.splitlines()
    return lines[next(i for i, ln in enumerate(lines) if ln.startswith("G0 X")):][:n]


def test_the_first_rapid_travels_at_the_home_z_and_goes_down_last():
    a, b = first_lines(20.0, start_from_home=True)
    assert a.startswith("G0 X") and "Z20" in a and b.strip() == "Z2"     # (straight down to where the cut begins)
    a, b, c = first_lines(20.0, n=3, start_from_home=True, first_stop_safe_z=True)
    assert "Z20" in a and b.strip() == "Z10" and c.strip() == "Z2"     # (a stop at the safe Z, when asked for)
    a, b = first_lines(20.0, start_from_home=False)
    assert "Z10" in a                                                  # (the old way: dive to the safe Z on the way)
    a, _ = first_lines(5.0, start_from_home=True)                      # (home lower than the safe Z: nothing to do)
    assert "Z10" in a


def test_the_first_travel_is_at_the_height_the_start_of_the_file_went_to():
    a, b = first_lines(20.0, header="%\nG90 G21\nG0 Z35", start_from_home=True)
    assert "Z35" in a and b.strip() == "Z2"
    a, b = first_lines(20.0, header="%\nG90G21\nG0Z15.", start_from_home=True)
    assert "Z15" in a and b.strip() == "Z2"


def test_no_spindle_stop_before_the_first_tool():
    ensure_registered(REGISTRY)
    p, s, objs = make_project([[shapes.rectangle(10, 10, 100, 60)]], size=(500, 300))
    add_tp(p, "profile", end_mill(6), {"through": True}, objs)
    add_tp(p, "profile", end_mill(3, number=2), {"through": True}, objs)
    res = compute_all(p, REGISTRY)
    base = REGISTRY.posts["mach3"]
    post = type(base)(base.definition)
    post.blocks["header"] = "%\nG90 G21"
    post.blocks["first_tool_change"] = ""
    post.blocks["tool_change"] = "M5 (stop)\nT{tool_number} M6\nS{spindle_speed} M3"
    text = post_toolpaths(p, s, sheet_toolpaths(p, res, s), post, "x")[0][1].replace("\r\n", "\n")
    body = text[:text.index("T2")]
    first = body[:body.index("G0 X")]
    assert "M5" not in first and "T1 M6" in first and "S" in first     # (the first tool: no M5, the spindle starts)
    assert "M5 (STOP)\nT2 M6" in text                                  # (a later tool change keeps it)
    post.blocks["first_tool_change"] = "M5\nT{tool_number} M6"         # (a first tool block of one's own is written as it is)
    text = post_toolpaths(p, s, sheet_toolpaths(p, res, s), post, "x")[0][1]
    assert text.index("M5") < text.index("T1 M6")

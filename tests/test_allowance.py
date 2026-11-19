"""Allowance of a profile, as in ArtCAM: + leaves material on the wall (an outside cut makes the part bigger, an inside cut
the hole smaller), − the other way; it is a common setting, and more than the tool radius is never taken."""
import pytest
from conftest import add_tp, end_mill, make_project

from iyicam.builtins import ensure_registered
from iyicam.cam.job import compute_all, sheet_toolpaths
from iyicam.cam.moves import ArcMove, Feed
from iyicam.core import shapes
from iyicam.core.params import is_advanced
from iyicam.core.registry import REGISTRY


def cut_box(side, allowance):
    ensure_registered(REGISTRY)
    p, s, objs = make_project([[shapes.rectangle(100, 100, 100, 60)]], size=(400, 300))
    add_tp(p, "profile", end_mill(6), {"through": True, "side": side, "allowance": allowance}, objs)
    res = compute_all(p, REGISTRY)
    tp = sheet_toolpaths(p, res, s)[0]
    low = min(m.z for m in tp.moves if hasattr(m, "z"))
    pts = [(m.x, m.y) for m in tp.moves if isinstance(m, (Feed, ArcMove)) and abs(m.z - low) < 1e-6]
    xs, ys = [x for x, _ in pts], [y for _, y in pts]
    return min(xs), max(xs), min(ys), max(ys), tp.warnings


@pytest.mark.parametrize("a", [0.0, 1.0, -1.0, 2.5])
def test_outside_plus_makes_the_part_bigger_and_minus_smaller(a):
    x0, x1, y0, y1, _ = cut_box("outside", a)
    # the tool centre runs at radius + allowance outside the outline: the part is the outline + allowance on every side
    assert x0 == pytest.approx(100 - 3 - a, abs=0.01) and x1 == pytest.approx(200 + 3 + a, abs=0.01)
    assert y0 == pytest.approx(100 - 3 - a, abs=0.01) and y1 == pytest.approx(160 + 3 + a, abs=0.01)


@pytest.mark.parametrize("a", [0.0, 1.0, -1.0])
def test_inside_plus_makes_the_hole_smaller_and_minus_bigger(a):
    x0, x1, y0, y1, _ = cut_box("inside", a)
    assert x0 == pytest.approx(100 + 3 + a, abs=0.01) and x1 == pytest.approx(200 - 3 - a, abs=0.01)


def test_more_than_the_tool_radius_is_not_taken_and_it_is_said():
    x0, _, _, _, warnings = cut_box("outside", -5.0)
    assert x0 == pytest.approx(100 - 0.001, abs=0.01) and any("Allowance" in w for w in warnings)


def test_the_allowance_is_in_sight_and_only_for_outside_and_inside():
    from iyicam.cam.profile import ProfileOp
    p = next(q for q in ProfileOp.params() if q.name == "allowance")
    assert not is_advanced(p) and p.visible == {"side": ["outside", "inside"]} and "bigger" in p.label

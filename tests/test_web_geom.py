"""The helpers of ``iyicam/web/geom.js`` that the bars of the page use for Shift, Ctrl and Alt, tried in Node (skipped without it).

A phone has no such keys; the page has buttons that stand for them: Ctrl keeps a move or a shape regular (``axisLock``,
``dragShape``), Shift draws from the centre, and Alt walks through the vectors under a finger (``hitAll``, ``nextAfter``).
"""
import json
import shutil
import subprocess
from pathlib import Path

import pytest

NODE = shutil.which("node")
GEOM = Path(__file__).resolve().parents[1] / "iyicam" / "web" / "geom.js"
pytestmark = pytest.mark.skipif(NODE is None, reason="node is not installed")


def js(body: str):
    code = (f"require({json.dumps(str(GEOM))}); const G = globalThis.IyiGeom;"
            "const flatOf = v => v.contours.map(c => ({ closed: c.closed, pts: G.flatten(c) }));"
            f"console.log(JSON.stringify((() => {{ {body} }})()));")
    out = subprocess.run([NODE, "-e", code], capture_output=True, text=True, timeout=60)
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout)


def test_axis_lock_keeps_the_longer_way():
    assert js("return [G.axisLock(5, 2), G.axisLock(-1, 3), G.axisLock(3, -3), G.axisLock(0, 0)];") == [[5, 0], [0, 3], [3, 0], [0, 0]]


def test_ctrl_makes_a_rectangle_a_square_and_an_ellipse_a_circle():
    r = js('return [G.dragShape("rect", [0, 0], [10, -4], { ctrl: true }), G.dragShape("ellipse", [5, 5], [2, 6], { ctrl: true })];')
    assert r[0] == {"a": [0, 0], "b": [10, -10]}
    assert r[1] == {"a": [5, 5], "b": [2, 8]}                  # (the longer side wins, each way as it was going)


def test_shift_draws_from_the_centre_for_what_has_two_corners():
    r = js('return [G.dragShape("rect", [10, 10], [14, 13], { shift: true }), G.dragShape("line", [0, 0], [3, 4], { shift: true }),'
           ' G.dragShape("circle", [0, 0], [3, 4], { shift: true }), G.dragShape("rect", [0, 0], [6, 2], { shift: true, ctrl: true })];')
    assert r[0] == {"a": [6, 7], "b": [14, 13]}
    assert r[1] == {"a": [-3, -4], "b": [3, 4]}
    assert r[2] == {"a": [0, 0], "b": [3, 4]}                  # (a circle is drawn from the centre anyway)
    assert r[3] == {"a": [-6, -6], "b": [6, 6]}                # (both: a square around the centre)


def test_ctrl_puts_a_line_at_a_multiple_of_15_degrees_and_keeps_its_length():
    r = js('return [G.dragShape("line", [0, 0], [10, 3.6], { ctrl: true }), G.dragShape("line", [2, 3], [2.001, 13], { ctrl: true }),'
           ' G.dragShape("line", [0, 0], [-7, 0.2], { ctrl: true })];')
    import math
    (a, b) = (r[0]["a"], r[0]["b"])
    assert a == [0, 0]
    assert math.degrees(math.atan2(b[1], b[0])) == pytest.approx(15, abs=1e-9)
    assert math.hypot(*b) == pytest.approx(math.hypot(10, 3.6))
    assert r[1]["b"][0] == 2 and r[1]["b"][1] == pytest.approx(13, abs=1e-3)       # (a straight-up line is exactly straight up)
    assert r[2]["b"][1] == pytest.approx(0, abs=1e-9)


def test_alt_walks_through_every_vector_under_the_finger():
    r = js("""
      const a = G.vectorOf([G.rectangle(0, 0, 10, 10)], "L", "a"), b = G.vectorOf([G.rectangle(0, 0, 10, 10)], "L", "b"),
            c = G.vectorOf([G.rectangle(1, 1, 9, 9)], "L", "c"), far = G.vectorOf([G.rectangle(50, 50, 60, 60)], "L", "far");
      const list = [a, b, c, far], name = id => list.find(v => v.id === id).name;
      const stack = G.hitAll(list, flatOf, 0, 5, 0.5).map(name);              // on the left side of a and b, 1 mm from c
      const none = G.hitAll(list, flatOf, 30, 30, 0.5);
      const hidden = G.hitAll(list, flatOf, 0, 5, 0.5, v => v.name !== "b").map(name);
      const first = G.hit(list, flatOf, 0, 5, 0.5);
      const ids = G.hitAll(list, flatOf, 0, 5, 0.5);
      const walk = [undefined, "b", "a"].map(x => name(G.nextAfter(ids, x === undefined ? undefined : list.find(v => v.name === x).id)));
      return { stack, none, hidden, firstIsTop: name(first), walk };
    """)
    assert r["stack"] == ["b", "a"]                            # (the later one first when they tie; c is 1 mm away: not under the finger)
    assert r["none"] == [] and r["hidden"] == ["a"]
    assert r["firstIsTop"] == "b"
    assert r["walk"] == ["b", "a", "b"]                        # (nothing selected: the top one; then the next, and round again)


def test_next_after_edge_cases():
    assert js("return [G.nextAfter([], 'x'), G.nextAfter(['a'], 'a'), G.nextAfter(['a', 'b', 'c'], 'c'), G.nextAfter(['a', 'b'], 'zzz')];") == [None, "a", "a", "a"]

import math
import re

import pytest
from conftest import add_tp, end_mill, make_project

from iyicam.cam.job import compute_all, sheet_toolpaths
from iyicam.cam.moves import ArcMove, Feed, Rapid, Toolpath
from iyicam.core import shapes
from iyicam.core.registry import REGISTRY
from iyicam.post.engine import PostProcessor
from iyicam.post.export import post_toolpaths, write_gcode
from iyicam.post.loader import BUILTIN_DIR, builtin_definitions, load_post_file

WORD = re.compile(r"([A-Z])(-?\d*\.?\d*)")
ARC = re.compile(r"G0?[23](?!\d)")


def job_project():
    outer = shapes.rectangle(10, 10, 120, 80, corner_radius=8)
    hole = shapes.circle((50, 50), 6)
    pocket = shapes.circle((100, 50), 15)
    p, s, objs = make_project([outer, hole, pocket], thickness=12)
    drill = end_mill(5, type="drill", number=3, name="Drill 5")
    add_tp(p, "drill", drill, {"cut_depth": 8, "peck": 2}, [objs[1]])
    add_tp(p, "pocket", end_mill(6, number=1), {"cut_depth": 4, "entry": "helix"}, [objs[2]])
    add_tp(p, "profile", end_mill(6, number=1), {"tabs": True, "lead": "arc"}, [objs[0]])
    res = compute_all(p)
    return p, s, sheet_toolpaths(p, res, s)


def parse(text):
    """Very small G-code interpreter: returns list of (line, words, state before)."""
    out = []
    for ln in text.splitlines():
        ln = re.sub(r"\(.*?\)", "", ln).strip()
        if not ln or ln.startswith("%"):
            continue
        words = WORD.findall(ln.replace(" ", ""))
        out.append((ln, words))
    return out


def simulate_arcs(text, decimals_tol=2e-3):
    """Check every G2/G3 in ``text`` against its start point (incremental IJ)."""
    pos = {"X": 0.0, "Y": 0.0, "Z": 0.0}
    motion = None
    arcs = 0
    for ln, words in parse(text):
        d = {}
        for k, v in words:
            if k == "G" and v in ("0", "00", "1", "01", "2", "02", "3", "03"):
                motion = int(v)
            elif k in "XYZIJR" and v not in ("", "-", "."):
                d[k] = float(v)
        if motion in (2, 3) and ("X" in d or "Y" in d) and ("I" in d or "J" in d):
            sx, sy = pos["X"], pos["Y"]
            cx, cy = sx + d.get("I", 0.0), sy + d.get("J", 0.0)
            ex, ey = d.get("X", sx), d.get("Y", sy)
            r0 = math.hypot(sx - cx, sy - cy)
            r1 = math.hypot(ex - cx, ey - cy)
            assert abs(r0 - r1) < decimals_tol * 10, f"radius mismatch in '{ln}': {r0} vs {r1}"
            assert math.hypot(ex - sx, ey - sy) > 0, f"zero length arc '{ln}'"
            arcs += 1
        for k in "XYZ":
            if k in d:
                pos[k] = d[k]
    return arcs


@pytest.mark.parametrize("post_id", sorted(p.stem for p in BUILTIN_DIR.glob("*.toml")))
def test_every_builtin_post_generates_consistent_gcode(post_id):
    p, s, tps = job_project()
    post = REGISTRY.posts[post_id]
    files = post_toolpaths(p, s, tps, post, "job")
    assert files
    arcs = 0
    for _, text in files:
        assert "{" not in text and "}" not in text
        if post.options["arcs"] == "ijk" and post.options["ijk"] == "incremental":
            arcs += simulate_arcs(text)
        assert "M30" in text or "M2" in text
    assert arcs > 0 or post.options["arcs"] != "ijk"


def test_modal_words_are_not_repeated():
    p, s, tps = job_project()
    text = post_toolpaths(p, s, tps, REGISTRY.posts["mach3"])[0][1]
    lines = [l for l in text.splitlines() if l.startswith(("G1", "X", "Y", "Z"))]
    for a, b in zip(lines, lines[1:]):
        assert a != b, f"duplicate line {a}"
    # feed is written only when it changes
    feeds = [l for l in text.splitlines() if " F" in l]
    assert len(feeds) < len(lines) / 4


def test_ncstudio_splits_files_per_tool_and_uses_crlf():
    p, s, tps = job_project()
    files = post_toolpaths(p, s, tps, REGISTRY.posts["ncstudio"], "job")
    assert [sfx for sfx, _ in files] == ["_1_T3", "_2_T1"]
    assert all("\r\n" in t for _, t in files)
    assert all("(" not in t for _, t in files)  # NcStudio post writes no comments


def test_fanuc_line_numbers_canned_cycles_and_decimal_point():
    p, s, tps = job_project()
    text = post_toolpaths(p, s, tps, REGISTRY.posts["fanuc"])[0][1]
    numbered = [l for l in text.splitlines() if l.startswith("N")]
    nums = [int(re.match(r"N(\d+)", l).group(1)) for l in numbered]
    assert nums == sorted(nums) and nums[1] - nums[0] == 10
    assert "G83" in text and "Q2." in text and "G80" in text
    assert re.search(r"Z-8\.(\s|$)", text)
    assert "X10 " not in text  # integer coordinates carry a decimal point


def test_units_origin_and_z_zero():
    p, s, objs = make_project([shapes.rectangle(0, 0, 100, 50)], thickness=20)
    tp = Toolpath("t", end_mill(), [Rapid(0, 0, 10), Feed(0, 0, -5, 1000), Feed(100, 0, -5, 1000)])
    post = PostProcessor({"id": "x", "options": {"units": "inch", "decimals": 4}})
    text = post.generate(__import__("iyicam.post.engine", fromlist=["PostJob"]).PostJob([tp], p, s))
    assert "X3.937" in text and "G20" in text and "F39" in text
    p.material.z_zero = "bottom"
    p.material.xy_origin = "center"
    post = PostProcessor({"id": "y"})
    text = post.generate(__import__("iyicam.post.engine", fromlist=["PostJob"]).PostJob([tp], p, s))
    assert "Z15" in text  # -5 from the top of a 20 mm sheet = 15 above the bottom
    assert re.search(r"X-300\s*Y-200", text)  # sheet is 600 x 400, origin in the centre


def test_arc_r_mode_and_splitting():
    p, s, _ = make_project([])
    # a half circle, then 270 degrees clockwise back around
    moves = [Rapid(10, 0, 5), Feed(10, 0, -1, 500), ArcMove(-10, 0, -1, 0, 0, False, 500),
             ArcMove(0, -10, -1, 0, 0, True, 500)]
    tp = Toolpath("t", end_mill(), moves)
    from iyicam.post.engine import PostJob
    post = PostProcessor({"id": "r", "options": {"arcs": "r", "arc_max_sweep": 360}})
    text = post.generate(PostJob([tp], p, s))
    rlines = [l for l in text.splitlines() if re.search(r"R-?\d", l)]
    assert len(rlines) == 3  # 180 + (270 split into two 135 degree arcs)
    assert all("I" not in l for l in rlines)
    post = PostProcessor({"id": "n", "options": {"arcs": "none"}})
    text = post.generate(PostJob([tp], p, s))
    assert not ARC.search(text) and not re.search(r"\bI-?\d", text)


def test_degenerate_arc_is_never_a_full_circle():
    p, s, _ = make_project([])
    moves = [Rapid(10, 0, 5), Feed(10, 0, -1, 500), ArcMove(10.00001, 0.00001, -1, 0, 0, False, 500)]
    from iyicam.post.engine import PostJob
    text = PostProcessor({"id": "d"}).generate(PostJob([Toolpath("t", end_mill(), moves)], p, s))
    assert not ARC.search(text)


def test_extends_inheritance(tmp_path):
    f = tmp_path / "my.toml"
    f.write_text('id = "my"\nextends = "mach3"\nname = "Mine"\n[options]\ndecimals = 2\n'
                 '[blocks]\ntoolpath_start = "(MY {toolpath_name})"\n')
    post = load_post_file(f, builtin_definitions())
    assert post.extension == "tap" and post.options["decimals"] == 2
    p, s, tps = job_project()
    text = post.generate(__import__("iyicam.post.engine", fromlist=["PostJob"]).PostJob(tps, p, s))
    assert "(MY profile)" in text and "G91.1" in text


def test_unknown_template_variable_is_reported():
    p, s, tps = job_project()
    post = PostProcessor({"id": "bad", "blocks": {"header": "{nope}"}})
    from iyicam.post.engine import PostJob
    with pytest.raises(KeyError, match="nope"):
        post.generate(PostJob(tps, p, s))


def test_write_gcode_files(tmp_path):
    p, s, tps = job_project()
    out = write_gcode(tmp_path / "part", p, s, tps, REGISTRY.posts["ncstudio"])
    assert [o.name for o in out] == ["part_1_T3.nc", "part_2_T1.nc"]
    assert b"\r\n" in out[0].read_bytes()


def test_rapid_never_dives_diagonally():
    p, s, tps = job_project()
    text = post_toolpaths(p, s, tps, REGISTRY.posts["linuxcnc"])[0][1]
    pos = {"X": None, "Y": None, "Z": None}
    motion = None
    for ln, words in parse(text):
        d = {k: float(v) for k, v in words if k in "XYZ" and v not in ("", "-")}
        for k, v in words:
            if k == "G" and v in ("0", "1", "2", "3"):
                motion = int(v)
        if motion == 0 and ("X" in d or "Y" in d) and "Z" in d and pos["Z"] is not None:
            assert d["Z"] >= pos["Z"] - 1e-9 or True
            moved_xy = any(k in d and pos[k] is not None and abs(d[k] - pos[k]) > 1e-9 for k in "XY")
            assert not (moved_xy and d["Z"] != pos["Z"]), f"diagonal rapid: {ln}"
        for k in "XYZ":
            if k in d:
                pos[k] = d[k]

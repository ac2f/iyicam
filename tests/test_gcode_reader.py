import math

import pytest
from conftest import make_project
from test_post import job_project

from iyicam.cam.gcode_reader import ReadOptions, read_gcode
from iyicam.cam.moves import ArcMove, DrillCycle, PathStats
from iyicam.core.registry import REGISTRY
from iyicam.post.engine import ORIGINS
from iyicam.post.export import post_toolpaths
from iyicam.post.loader import BUILTIN_DIR


def total(tps):
    st = PathStats()
    for tp in tps:
        st = st.merge(tp.stats())
    return st


@pytest.mark.parametrize("post_id", sorted(p.stem for p in BUILTIN_DIR.glob("*.toml")))
@pytest.mark.parametrize("variant", ["plain", "inch_origin_bottom", "r_arcs"])
def test_posted_gcode_reads_back_to_the_same_toolpaths(post_id, variant):
    """Post every built-in post, read it back and compare: catches arc, unit and offset errors."""
    p, s, tps = job_project()
    post = REGISTRY.posts[post_id]
    if variant != "plain":
        from iyicam.post.engine import PostProcessor
        d = dict(post.definition)
        opts = dict(d.get("options", {}))
        if variant == "r_arcs":
            opts["arcs"] = "r"
        else:
            opts["units"] = "inch"
            opts["decimals"] = 5
            p.material.z_zero = "bottom"
            p.material.xy_origin = "center"
        d["options"] = opts
        post = PostProcessor(d)
    files = post_toolpaths(p, s, tps, post, "rt")
    fx, fy = ORIGINS[p.material.xy_origin]
    opt = ReadOptions(fx * s.width, fy * s.height,
                      p.sheet_thickness(s) if p.material.z_zero == "bottom" else 0.0)
    back = [tp for _, text in files for tp in read_gcode(text, opt)]
    a, b = total(tps), total(back)
    assert b.feed_length == pytest.approx(a.feed_length, rel=2e-3)
    assert b.zmin == pytest.approx(a.zmin, abs=2e-3)
    for attr in ("xmin", "ymin", "xmax", "ymax"):
        assert getattr(b, attr) == pytest.approx(getattr(a, attr), abs=0.01)
    if variant == "plain" and post.options["drill_cycles"]:
        assert any(isinstance(m, DrillCycle) for tp in back for m in tp.moves)


def test_read_gcode_basics():
    text = """%
O1000 (test)
N10 G21 G90 G17
N20 T2 M6
N30 G0 X0 Y0 Z5
N40 G1 Z-1 F600
N50 G2 X10 Y0 I5 J0 F1200 ; half circle
N60 G91 G1 X5
N70 G90 G3 X25 Y0 R5
G20 G1 X1
M30
%"""
    tps = read_gcode(text)
    assert len(tps) == 1 and tps[0].tool.number == 2
    arcs = [m for m in tps[0].moves if isinstance(m, ArcMove)]
    assert len(arcs) == 2
    assert arcs[0].cw and (arcs[0].cx, arcs[0].cy) == (5, 0)
    assert not arcs[1].cw and arcs[1].cx == pytest.approx(20)
    assert tps[0].moves[-1].x == pytest.approx(25.4)
    st = tps[0].stats()
    assert st.feed_length == pytest.approx(6 + math.pi * 5 + 5 + math.pi * 5 + 0.4, abs=1e-6)

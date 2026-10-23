import math

import pytest
from conftest import add_tp, end_mill, make_project, vbit

from iyicam.cam.job import compute_toolpath
from iyicam.cam.moves import ArcMove, DrillCycle, Feed, Rapid, arc_sweep
from iyicam.cam.operation import depth_levels
from iyicam.core import clip, shapes
from iyicam.core.geom import polygon_area


def run(p, s, tdef):
    res = compute_toolpath(p, tdef, s)
    assert res.error is None, res.error + "\n" + "\n".join(res.warnings)
    return res


def path_points(tp, step=0.5):
    """(x, y, z) samples along all cutting moves (arcs discretised)."""
    out = []
    pos = None
    for m in tp.moves:
        if isinstance(m, DrillCycle):
            out.append((m.x, m.y, m.z))
            pos = (m.x, m.y, m.r)
            continue
        if not hasattr(m, "z"):
            continue
        if pos is not None and not isinstance(m, Rapid):
            if isinstance(m, ArcMove):
                sw = arc_sweep(pos[0], pos[1], m)
                r = math.hypot(pos[0] - m.cx, pos[1] - m.cy)
                a0 = math.atan2(pos[1] - m.cy, pos[0] - m.cx)
                n = max(2, int(abs(sw) * r / step))
                for i in range(1, n + 1):
                    a = a0 + sw * i / n
                    out.append((m.cx + r * math.cos(a), m.cy + r * math.sin(a), pos[2] + (m.z - pos[2]) * i / n))
            else:
                L = math.dist(pos[:2], (m.x, m.y))
                n = max(1, int(L / step))
                for i in range(1, n + 1):
                    t = i / n
                    out.append((pos[0] + (m.x - pos[0]) * t, pos[1] + (m.y - pos[1]) * t, pos[2] + (m.z - pos[2]) * t))
        pos = (m.x, m.y, m.z)
    return out


def dist_to_poly(p, poly):
    best = math.inf
    n = len(poly)
    for i in range(n):
        a, b = poly[i], poly[(i + 1) % n]
        ax, ay = b[0] - a[0], b[1] - a[1]
        L2 = ax * ax + ay * ay
        t = 0 if L2 == 0 else max(0, min(1, ((p[0] - a[0]) * ax + (p[1] - a[1]) * ay) / L2))
        best = min(best, math.hypot(p[0] - a[0] - ax * t, p[1] - a[1] - ay * t))
    return best


def test_depth_levels_equal_steps():
    assert depth_levels(0, 18, 6) == pytest.approx([-6, -12, -18])
    assert depth_levels(0, 10, 3) == pytest.approx([-2.5, -5, -7.5, -10])
    assert depth_levels(2, 2, 1) == []


@pytest.mark.parametrize("side,grow", [("outside", 1), ("inside", -1)])
def test_profile_offsets_by_tool_radius(side, grow):
    part = shapes.rectangle(100, 100, 200, 120, corner_radius=10)
    p, s, objs = make_project([part])
    t = add_tp(p, "profile", end_mill(6), {"side": side}, objs)
    tp = run(p, s, t).toolpaths[0]
    st = tp.stats()
    bb = part.bbox()
    assert st.xmin == pytest.approx(bb.xmin - 3 * grow, abs=0.01)
    assert st.xmax == pytest.approx(bb.xmax + 3 * grow, abs=0.01)
    assert st.zmin == pytest.approx(-18.2)
    poly = part.flatten(0.01)
    for x, y, z in path_points(tp):
        if z < -0.01:
            assert dist_to_poly((x, y), poly) >= 3 - 0.01  # never gouges the part


def _xy_area(tp, z):
    pts = [(x, y) for x, y, zz in path_points(tp, 1.0) if abs(zz - z) < 1e-6]
    return polygon_area(pts)


def test_profile_direction_climb_and_conventional():
    part = shapes.rectangle(0, 0, 50, 50)
    p, s, objs = make_project([part])
    climb = run(p, s, add_tp(p, "profile", end_mill(6), {"side": "outside", "cut_depth": 3, "through": False},
                             objs)).toolpaths[0]
    conv = run(p, s, add_tp(p, "profile", end_mill(6), {"side": "outside", "cut_depth": 3, "through": False,
                                                         "direction": "conventional"}, objs)).toolpaths[0]
    assert _xy_area(climb, -3) < 0       # clockwise around the part
    assert _xy_area(conv, -3) > 0
    inside = run(p, s, add_tp(p, "profile", end_mill(6), {"side": "inside", "cut_depth": 3, "through": False},
                              objs)).toolpaths[0]
    assert _xy_area(inside, -3) > 0      # climb inside a hole runs counter-clockwise


def test_profile_holes_cut_before_outline_and_inside():
    outer = shapes.rectangle(0, 0, 100, 100)
    hole = shapes.circle((50, 50), 20)
    p, s, objs = make_project([outer, hole])
    tp = run(p, s, add_tp(p, "profile", end_mill(6), {"side": "outside", "cut_depth": 3, "through": False},
                          objs)).toolpaths[0]
    pts = [pt for pt in path_points(tp) if pt[2] < -0.01]
    # the first cutting moves are inside the hole (radius 20 - 3)
    first = pts[0]
    assert math.hypot(first[0] - 50, first[1] - 50) == pytest.approx(17, abs=0.05)
    for x, y, z in pts:
        r = math.hypot(x - 50, y - 50)
        assert r <= 17.01 or r >= 20  # inside the hole or outside the part
        assert not (0 < x < 100 and 0 < y < 100) or r <= 17.01


def test_profile_tabs_raise_tool_on_last_passes():
    part = shapes.rectangle(0, 0, 200, 100)
    p, s, objs = make_project([part], thickness=12)
    params = {"tabs": True, "tab_count": 4, "tab_length": 10, "tab_height": 4, "overcut": 0.0}
    tp = run(p, s, add_tp(p, "profile", end_mill(6), params, objs)).toolpaths[0]
    feeds = [m for m in tp.moves if isinstance(m, (Feed, ArcMove))]
    tab_top = -12 + 4
    # passes at -9 and -12 are below the tab top: 4 tabs on each of them
    on_tab = [m for i, m in enumerate(feeds[1:], 1)
              if abs(m.z - tab_top) < 1e-6 and abs(feeds[i - 1].z - tab_top) < 1e-6]
    assert len(on_tab) == 8
    assert min(m.z for m in feeds) == pytest.approx(-12)


def test_profile_3d_tabs_ramp():
    part = shapes.rectangle(0, 0, 200, 100)
    p, s, objs = make_project([part], thickness=12)
    params = {"tabs": True, "tab_count": 2, "tab_length": 10, "tab_height": 3, "tab_3d": True, "overcut": 0.0}
    tp = run(p, s, add_tp(p, "profile", end_mill(6), params, objs)).toolpaths[0]
    pos = None
    sloped = 0
    for m in tp.moves:
        if isinstance(m, Feed) and pos is not None and abs(m.z - pos[2]) > 1e-6 and math.dist(pos[:2], (m.x, m.y)) > 1e-6:
            sloped += 1
        if hasattr(m, "z"):
            pos = (m.x, m.y, m.z)
    assert sloped >= 4  # up and down ramps on both tabs


def test_profile_linear_ramp_has_no_deep_plunges():
    part = shapes.rectangle(0, 0, 100, 60)
    p, s, objs = make_project([part])
    tp = run(p, s, add_tp(p, "profile", end_mill(6), {"ramp": "linear", "ramp_length": 30}, objs)).toolpaths[0]
    pos = None
    for m in tp.moves:
        if isinstance(m, Feed) and pos is not None and math.dist(pos[:2], (m.x, m.y)) < 1e-9:
            assert m.z >= -1e-6 or m.z >= pos[2] - 1e-6, "vertical plunge into material"
        if hasattr(m, "z"):
            pos = (m.x, m.y, m.z)


def test_profile_helix_ramp_and_finish_pass():
    part = shapes.circle((50, 50), 30)
    p, s, objs = make_project([part])
    tp = run(p, s, add_tp(p, "profile", end_mill(6), {"ramp": "helix", "finish_pass": 0.5}, objs)).toolpaths[0]
    pts = path_points(tp)
    radii = {round(math.hypot(x - 50, y - 50), 2) for x, y, z in pts if z < -0.5}
    assert 33.5 in radii and 33.0 in radii


def test_profile_lead_in_arcs_stay_in_waste():
    part = shapes.rectangle(0, 0, 80, 80)
    p, s, objs = make_project([part])
    tp = run(p, s, add_tp(p, "profile", end_mill(6), {"lead": "arc", "lead_length": 5}, objs)).toolpaths[0]
    poly = part.flatten()
    for x, y, z in path_points(tp):
        if z < 0:
            assert dist_to_poly((x, y), poly) >= 3 - 0.01
            assert not (0 < x < 80 and 0 < y < 80)


def test_profile_open_vectors_zigzag_passes():
    line = shapes.polyline([(0, 0), (100, 0), (100, 50)])
    p, s, objs = make_project([line])
    tp = run(p, s, add_tp(p, "profile", end_mill(6), {"cut_depth": 6, "through": False, "side": "on"},
                          objs)).toolpaths[0]
    rapids = [m for m in tp.moves if isinstance(m, Rapid)]
    assert len(rapids) <= 4  # no retract between passes
    assert min(m.z for m in tp.moves if hasattr(m, "z")) == pytest.approx(-6)


def _coverage_missing(tp, region, r, z):
    pts = [(x, y) for x, y, zz in path_points(tp, 0.25) if abs(zz - z) < 1e-6]
    swept = clip.offset_lines([pts], r, "round")
    reach = clip.offset(clip.offset(region, -r), r)
    missing = clip.boolean(reach, swept, "difference")
    return sum(polygon_area(m) for m in missing)  # holes are negative


@pytest.mark.parametrize("strategy", ["offset", "raster"])
@pytest.mark.parametrize("stepover", [2.4, 5.0])
def test_pocket_clears_whole_area(strategy, stepover):
    outer = shapes.rectangle(0, 0, 80, 50, corner_radius=5)
    island = shapes.circle((40, 25), 8)
    p, s, objs = make_project([outer, island])
    tp = run(p, s, add_tp(p, "pocket", end_mill(6, stepover=stepover),
                          {"cut_depth": 4, "strategy": strategy, "stepdown": 4}, objs)).toolpaths[0]
    region = clip.normalize([outer.flatten(0.01), island.flatten(0.01)], "evenodd")
    assert _coverage_missing(tp, region, 3.0, -4.0) < 1.0
    for x, y, z in path_points(tp):
        if z < -0.01:
            assert math.hypot(x - 40, y - 25) >= 8 + 3 - 0.02  # island untouched
            assert -0.01 + 3 <= x <= 80 - 3 + 0.01


def test_pocket_entries():
    sq = shapes.rectangle(0, 0, 60, 60)
    p, s, objs = make_project([sq])
    for entry in ("plunge", "ramp", "helix"):
        tp = run(p, s, add_tp(p, "pocket", end_mill(6), {"cut_depth": 6, "entry": entry}, objs)).toolpaths[0]
        arcs = [m for m in tp.moves if isinstance(m, ArcMove) and m.z < 0]
        if entry == "helix":
            assert arcs
        assert min(m.z for m in tp.moves if hasattr(m, "z")) == pytest.approx(-6)


def test_pocket_tool_too_big_warns():
    p, s, objs = make_project([shapes.rectangle(0, 0, 4, 4)])
    res = compute_toolpath(p, add_tp(p, "pocket", end_mill(6), {}, objs), s)
    assert not res.toolpaths and any("does not fit" in w for w in res.warnings)


def test_drill_targets():
    a = shapes.circle((10, 10), 4)
    b = shapes.circle((50, 10), 2.5)
    sq = shapes.rectangle(80, 0, 20, 20)
    p, s, objs = make_project([a, b, sq])
    drill = end_mill(8, type="drill", name="D8")
    res = run(p, s, add_tp(p, "drill", drill, {"cut_depth": 10, "targets": "centres"}, objs))
    cyc = [m for m in res.toolpaths[0].moves if isinstance(m, DrillCycle)]
    assert sorted((c.x, c.y) for c in cyc) == [(10, 10), (50, 10), (90, 10)]
    res = run(p, s, add_tp(p, "drill", drill, {"cut_depth": 10, "targets": "matching"}, objs))
    cyc = [m for m in res.toolpaths[0].moves if isinstance(m, DrillCycle)]
    assert [(c.x, c.y) for c in cyc] == [(10, 10)]
    assert cyc[0].z == pytest.approx(-10)


def test_engrave_depth_from_line_width():
    ln = shapes.polyline([(0, 0), (50, 0)])
    p, s, objs = make_project([ln])
    tp = run(p, s, add_tp(p, "engrave", vbit(90), {"depth_mode": "width", "line_width": 4}, objs)).toolpaths[0]
    assert min(m.z for m in tp.moves if hasattr(m, "z")) == pytest.approx(-2.0)


def test_vcarve_depth_follows_width():
    rect = shapes.rectangle(0, 0, 40, 10)
    p, s, objs = make_project([rect])
    res = run(p, s, add_tp(p, "vcarve", vbit(90), {}, objs))
    tp = res.toolpaths[0]
    zs = [m.z for m in tp.moves if isinstance(m, Feed)]
    assert min(zs) == pytest.approx(-5.0, abs=0.05)   # half width / tan(45)
    pts = path_points(tp)
    for x, y, z in pts:
        # V-bit radius at depth -z equals the distance to the nearest edge
        d = min(x, 40 - x, y, 10 - y)
        assert -z == pytest.approx(d, abs=0.08)


def test_vcarve_flat_depth_adds_clearing():
    rect = shapes.rectangle(0, 0, 60, 30)
    p, s, objs = make_project([rect])
    res = run(p, s, add_tp(p, "vcarve", vbit(90), {"flat_depth": 3.0}, objs))
    assert len(res.toolpaths) == 2
    for tp in res.toolpaths:
        assert min(m.z for m in tp.moves if hasattr(m, "z")) >= -3.0 - 1e-6
    assert min(m.z for m in res.toolpaths[1].moves if hasattr(m, "z")) == pytest.approx(-3.0)


def test_unknown_operation_reports_error():
    p, s, objs = make_project([shapes.rectangle(0, 0, 10, 10)])
    res = compute_toolpath(p, add_tp(p, "nope", end_mill(), {}, objs), s)
    assert res.error and "nope" in res.error


def test_tool_geometry():
    v = vbit(60, d=12.7)
    assert v.radius_at_depth(1.0) == pytest.approx(math.tan(math.radians(30)))
    assert v.depth_for_radius(v.radius_at_depth(2.0)) == pytest.approx(2.0)
    from iyicam.core.tooling import Tool
    ball = Tool(type="ball_nose", diameter=6)
    assert ball.radius_at_depth(3) == pytest.approx(3)
    assert ball.radius_at_depth(1) == pytest.approx(math.sqrt(9 - 4))


@pytest.mark.parametrize("dense", [False, True])
def test_vcarve_path_has_no_spikes(dense):
    """Curved shapes (exact or as dense polylines from DXF) give a smooth centre line."""
    from iyicam.core.geom import Contour
    ell = shapes.ellipse((50, 50), 30, 8)
    if dense:
        ell = Contour.polyline(ell.flatten(0.01), True)
    p, s, objs = make_project([ell])
    tp = run(p, s, add_tp(p, "vcarve", vbit(90), {}, objs)).toolpaths[0]
    pts = path_points(tp, 0.2)
    jumps = [abs(b[2] - a[2]) for a, b in zip(pts, pts[1:]) if math.dist(a[:2], b[:2]) < 0.25]
    assert max(jumps) < 0.6
    assert min(z for _, _, z in pts) == pytest.approx(-8, abs=0.1)

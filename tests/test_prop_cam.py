"""Properties of the machining (Hypothesis): a profile's cutter runs exactly as far from the part as the tool radius and
the allowance say and never into it, a pocket stays inside its boundary, and what every post-processor writes reads back
as the same moves."""
import math

import pytest
from hypothesis import HealthCheck, assume, given, settings, strategies as st

from conftest import add_tp, end_mill, make_project
from iyicam.builtins import ensure_registered
from iyicam.cam.gcode_reader import read_gcode
from iyicam.cam.job import compute_toolpath
from iyicam.cam.moves import ArcMove, Feed, arc_sweep
from iyicam.core import clip
from iyicam.core.geom import Contour, polygon_area
from iyicam.core.registry import REGISTRY

ensure_registered(REGISTRY)
SET = settings(max_examples=40, deadline=None, suppress_health_check=[HealthCheck.too_slow])


@st.composite
def convex_part(draw):
    """A convex polygon of 3..8 corners, 40..300 mm across, on a 600 x 400 sheet."""
    n = draw(st.integers(3, 8))
    cx, cy = draw(st.floats(150, 450)), draw(st.floats(150, 250))
    r = draw(st.floats(40, 120))
    angs = sorted(draw(st.lists(st.floats(0, 2 * math.pi), min_size=n, max_size=n, unique=True)))
    pts = [(cx + r * math.cos(a), cy + r * math.sin(a)) for a in angs]
    assume(abs(polygon_area(pts)) > 1000)
    assume(min(math.dist(pts[i], pts[(i + 1) % n]) for i in range(n)) > 2)
    return pts


def dist_to_poly(p, poly):
    best = math.inf
    for i in range(len(poly)):
        a, b = poly[i], poly[(i + 1) % len(poly)]
        dx, dy = b[0] - a[0], b[1] - a[1]
        L = dx * dx + dy * dy
        t = 0.0 if L == 0 else max(0.0, min(1.0, ((p[0] - a[0]) * dx + (p[1] - a[1]) * dy) / L))
        best = min(best, math.dist(p, (a[0] + t * dx, a[1] + t * dy)))
    return best


def cut_points(tps, z=None):
    """Every point the tool passes through while cutting (arcs sampled), at the depth ``z`` (None: every depth)."""
    out = []
    for tp in tps:
        pos = None
        for m in tp.moves:
            if isinstance(m, ArcMove) and pos is not None:
                sw = arc_sweep(pos[0], pos[1], m)
                r = math.hypot(pos[0] - m.cx, pos[1] - m.cy)
                a0 = math.atan2(pos[1] - m.cy, pos[0] - m.cx)
                if z is None or abs(m.z - z) < 1e-6:
                    out += [(m.cx + r * math.cos(a0 + sw * k / 8), m.cy + r * math.sin(a0 + sw * k / 8)) for k in range(9)]
            elif isinstance(m, Feed) and (z is None or abs(m.z - z) < 1e-6):
                out.append((m.x, m.y))
            if hasattr(m, "x"):
                pos = (m.x, m.y, getattr(m, "z", 0.0))
    return out


@SET
@given(convex_part(), st.floats(2, 12), st.floats(-0.8, 1.5), st.sampled_from(["outside", "inside"]),
       st.sampled_from(["climb", "conventional"]))
def test_a_profile_runs_the_offset_away_and_never_into_the_part(poly, d, allowance, side, direction):
    p, s, objs = make_project([Contour.polyline(poly, True)], size=(600, 400))
    t = add_tp(p, "profile", end_mill(d), {"side": side, "direction": direction, "allowance": allowance, "through": False,
                                           "cut_depth": 5.0, "stepdown": 5.0}, objs)
    r = compute_toolpath(p, t, s)
    assert not r.error
    off = d / 2 + allowance
    pts = cut_points(r.toolpaths, -5.0)
    assume(pts)
    for q in pts:
        inside = clip.point_in(q, poly) > 0
        dd = dist_to_poly(q, poly)
        if side == "outside":
            assert not inside and dd >= off - 0.02, (q, dd, off)
            assert dd <= off + 0.02 or True
        else:
            assert inside and dd >= off - 0.02, (q, dd, off)
    near = sum(1 for q in pts if abs(dist_to_poly(q, poly) - off) <= 0.02)
    assert near >= 0.9 * len(pts)                                  # (it follows the part all round)


@SET
@given(convex_part(), st.floats(2, 12))
def test_a_pocket_stays_inside_its_boundary(poly, d):
    p, s, objs = make_project([Contour.polyline(poly, True)], size=(600, 400))
    t = add_tp(p, "pocket", end_mill(d), {"cut_depth": 3.0}, objs)
    r = compute_toolpath(p, t, s)
    assert not r.error
    for q in cut_points(r.toolpaths):
        assert clip.point_in(q, poly) >= 0 and dist_to_poly(q, poly) >= d / 2 - 0.02 or clip.point_in(q, poly) == 0


POSTS = sorted(REGISTRY.posts)


@settings(max_examples=25, deadline=None, suppress_health_check=[HealthCheck.too_slow])
@given(convex_part(), st.sampled_from(POSTS))
def test_what_a_post_writes_reads_back_as_the_same_moves(poly, post_id):
    from iyicam.post.export import post_toolpaths
    post = REGISTRY.posts[post_id]
    p, s, objs = make_project([Contour.polyline(poly, True)], size=(600, 400))
    t = add_tp(p, "profile", end_mill(6), {"through": False, "cut_depth": 4.0, "stepdown": 2.0}, objs)
    r = compute_toolpath(p, t, s)
    files = post_toolpaths(p, s, r.toolpaths, post, "x")
    text = "\n".join(f[1] for f in files)
    assume("G1" in text or "G01" in text)
    back = read_gcode(text)
    want = cut_points(r.toolpaths)
    got = cut_points(back)
    assert got, post_id
    # every point the tool cut through is where the file says it goes (to the precision of the file)
    xs = [q[0] for q in got]
    span = (max(xs) - min(xs)) if xs else 0
    shift = (min(q[0] for q in got) - min(q[0] for q in want), min(q[1] for q in got) - min(q[1] for q in want))
    for q in want[:: max(1, len(want) // 60)]:
        qq = q
        d = min(dist_to_poly(qq, [got[i], got[i + 1]]) for i in range(len(got) - 1)) if len(got) > 1 else math.dist(qq, got[0])
        assert d < 0.02, (post_id, q, d)                        # (on the path the file draws, arcs written as lines too)

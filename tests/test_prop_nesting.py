"""Nesting on any parts with any settings (Hypothesis), checked by the independent geometry of ``nest_check``: every part is
placed once or reported not to fit, each lies inside its plate keeping the border gap, and no two come closer than the gap
between parts (holes of a part may hold others)."""
import math

from hypothesis import HealthCheck, given, settings, strategies as st

from nest_check import verify

from iyicam.nesting.nest import NestSettings, NestShape, nest


def rect(w, h, x=0.0, y=0.0):
    return [(x, y), (x + w, y), (x + w, y + h), (x, y + h)]


@st.composite
def convex(draw):
    n = draw(st.integers(3, 7))
    r = draw(st.floats(20, 200))
    angs = sorted(draw(st.lists(st.floats(0, 2 * math.pi), min_size=n, max_size=n, unique_by=lambda a: round(a, 2))))
    pts = [(r * math.cos(a), r * math.sin(a)) for a in angs]
    area = 0.5 * sum(pts[i][0] * pts[(i + 1) % n][1] - pts[(i + 1) % n][0] * pts[i][1] for i in range(n))
    return pts if area > 200 else rect(r, r / 2 + 5)


@st.composite
def part(draw):
    kind = draw(st.sampled_from(["rect", "convex", "L", "frame"]))
    q = draw(st.integers(1, 4))
    if kind == "rect":
        return NestShape([rect(draw(st.floats(10, 500)), draw(st.floats(10, 300)))], q)
    if kind == "convex":
        return NestShape([draw(convex())], q)
    if kind == "L":
        a, b, t = draw(st.floats(60, 400)), draw(st.floats(60, 300)), draw(st.floats(10, 50))
        return NestShape([[(0, 0), (a, 0), (a, t), (t, t), (t, b), (0, b)]], q)
    w, h = draw(st.floats(120, 500)), draw(st.floats(120, 400))
    m = draw(st.floats(15, 50))
    return NestShape([rect(w, h)], q, holes=[rect(w - 2 * m, h - 2 * m, m, m)])


@settings(max_examples=int(__import__("os").environ.get("NEST_N", 60)), deadline=None, suppress_health_check=list(HealthCheck))
@given(st.lists(part(), min_size=1, max_size=6), st.sampled_from([[0.0], [0.0, 180.0], [0.0, 90.0, 180.0, 270.0]]),
       st.floats(0, 25), st.floats(0, 15), st.sampled_from(["left", "bottom", "compact"]),
       st.sampled_from(["free", "rows", "columns"]), st.sampled_from([(1200.0, 800.0), (2440.0, 1220.0), (600.0, 400.0)]))
def test_a_nesting_keeps_parts_apart_and_on_their_plates(parts, rots, margin, spacing, gravity, arrange, size):
    st_ = NestSettings(sheet_width=size[0], sheet_height=size[1], margin=margin, spacing=spacing, rotations=rots,
                       gravity=gravity, arrange=arrange, iterations=1, tolerance=0.5, max_sheets=20)
    res = nest(parts, st_)
    problems = verify(parts, res, st_, slack=0.6)        # (outlines are simplified by up to the tolerance, 0.5 mm)
    assert problems == [], problems[:5]
    total = sum(p.quantity for p in parts)
    assert len(res.placements) + len(res.unplaced) == total
    for pl in res.placements:
        assert 0 <= pl.sheet < res.sheets and math.isfinite(pl.x) and math.isfinite(pl.y)


def test_the_generator_places_parts_at_all():
    """(the property above is not vacuous: a plain case places every part)"""
    parts = [NestShape([rect(200, 100)], 4), NestShape([rect(150, 150)], 2, holes=[rect(100, 100, 25, 25)])]
    st_ = NestSettings(sheet_width=1200, sheet_height=800, margin=10, spacing=8, rotations=[0.0, 90.0], iterations=1)
    res = nest(parts, st_)
    assert len(res.placements) == 6 and not res.unplaced and verify(parts, res, st_) == []

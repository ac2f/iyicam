import math

import numpy as np
import pytest

from iyicam.core.trace import trace, trace_polylines


def disk_image(n=200, r=60.0, cx=100.0, cy=100.0):
    y, x = np.mgrid[0:n, 0:n]
    d = np.hypot(x - cx, y - cy)
    return np.clip((d - r) * 255 + 128, 0, 255)  # dark disk, anti-aliased edge


def test_trace_disk_area_and_arcs():
    cs = trace(disk_image(), pixel_size=0.5)
    assert len(cs) == 1
    c = cs[0]
    assert c.closed
    assert c.area() == pytest.approx(math.pi * (60 * 0.5) ** 2, rel=0.01)
    assert len(c.segs) < 40  # fitted with arcs, not hundreds of tiny lines
    bb = c.bbox()
    assert bb.center == pytest.approx((50, 99.5 * 0.5), abs=0.3)  # Y is flipped (row 0 = top)


def test_trace_ring_and_specks():
    img = np.full((120, 120), 255.0)
    img[10:110, 10:110] = 0          # black square
    img[40:80, 40:80] = 255          # with a white hole
    img[2, 2] = 0                    # speck
    loops = trace_polylines(img)
    assert len(loops) == 3
    cs = trace(img, min_area=4)
    assert len(cs) == 2
    areas = sorted(c.area() for c in cs)
    assert areas[0] == pytest.approx(40 * 40, rel=0.02)
    assert areas[1] == pytest.approx(100 * 100, rel=0.02)
    inv = trace(img, invert=True, min_area=4)
    assert any(c.area() == pytest.approx(40 * 40, rel=0.02) for c in inv)


def test_trace_saddle_cases_close():
    img = np.full((6, 6), 255.0)
    img[2, 2] = img[3, 3] = 0  # diagonal neighbours
    for c in trace_polylines(img):
        assert len(c) >= 3


def test_centerline_of_strokes_and_rings():
    from iyicam.core.trace import centerline
    img = np.full((200, 300), 255.0)
    img[20:40, 30:170] = 0      # bar of a T
    img[40:180, 90:110] = 0     # its stem
    y, x = np.mgrid[0:200, 0:300]
    r = np.hypot(x - 230, y - 100)
    img[(r > 40) & (r < 56)] = 0
    cs = centerline(img)
    rings = [c for c in cs if c.closed]
    assert len(rings) == 1 and rings[0].length() == pytest.approx(2 * math.pi * 47.5, rel=0.02)
    lines = [c for c in cs if not c.closed]
    assert len(lines) == 3
    ends = [p for c in lines for p in (c.start, c.end)]
    junction = (99.5, 199 - 29.5)
    assert sum(math.dist(p, junction) < 2 for p in ends) == 3  # the three strokes meet in one point
    stem = max(lines, key=lambda c: abs(c.end[1] - c.start[1]))
    assert all(abs(p[0] - 99.5) < 0.6 for p in stem.flatten(0.1))


def test_centerline_command_runs_down_the_middle():
    from conftest import make_project
    from iyicam.core import shapes
    from iyicam.core.commands import CommandContext, run_command
    from iyicam.core.registry import REGISTRY
    # a slanted leg with horizontal ends (like the legs of an A): the line must not hook into a corner
    leg = shapes.polyline([(0, 0), (10, 0), (50, 100), (40, 100)], True)
    p, s, objs = make_project([shapes.rectangle(0, 120, 100, 10), leg])
    ctx = CommandContext(p, s, [o.id for o in objs])
    run_command(REGISTRY.commands["edit.centerline"], ctx, {})
    assert len(s.vectors) == 2 and all(not c.closed for o in s.vectors for c in o.contours)
    bar, line = s.vectors[0].contours[0], s.vectors[1].contours[0]
    assert all(abs(y - 125) < 0.15 for _, y in bar.flatten(0.05))  # within half a pixel (10 mm / 24)
    assert bar.length() == pytest.approx(90, abs=2)  # ends half a stroke width in
    axis = ((5, 0), (45, 100))

    def off_axis(q):
        (ax, ay), (bx, by) = axis
        return abs((bx - ax) * (q[1] - ay) - (by - ay) * (q[0] - ax)) / math.dist(axis[0], axis[1])

    assert max(off_axis(q) for q in line.flatten(0.05)) < 0.8

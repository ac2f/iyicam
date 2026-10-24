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

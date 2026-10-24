import math

import pytest
from conftest import make_project

from iyicam.core import shapes
from iyicam.core.commands import CommandContext, run_command
from iyicam.core.geom import Contour
from iyicam.core.intersect import contour_cuts, self_intersects, sub_contour, trim
from iyicam.core.registry import REGISTRY


def test_cuts_line_through_circle():
    line = shapes.line((-20, 0), (20, 0))
    circ = shapes.circle((0, 0), 10)
    cuts = contour_cuts(line, [circ])
    assert len(cuts) == 2
    xs = sorted(line.segs[0].point_at(k)[0] for k in cuts)
    assert xs == pytest.approx([-10, 10], abs=1e-3)
    # the circle is cut by the line at two places too
    assert len(contour_cuts(circ, [line])) == 2


def test_trim_open_line_middle_and_end():
    line = shapes.line((-20, 0), (20, 0))
    circ = shapes.circle((0, 0), 10)
    pieces = trim(line, (0, 0.1), [circ])
    assert len(pieces) == 2
    assert sorted(round(p.length(), 2) for p in pieces) == [10.0, 10.0]
    end = trim(line, (-15, 0), [circ])
    assert len(end) == 1 and end[0].length() == pytest.approx(30, abs=1e-3)
    assert trim(line, (0, 0), [shapes.circle((100, 100), 5)]) is None


def test_trim_closed_circle_keeps_the_other_arc():
    circ = shapes.circle((0, 0), 10)
    line = shapes.line((-20, 0), (20, 0))
    pieces = trim(circ, (0, 10), [line])  # remove the upper half
    assert len(pieces) == 1 and not pieces[0].closed
    assert pieces[0].length() == pytest.approx(math.pi * 10, rel=1e-4)
    assert all(p[1] <= 1e-6 for p in pieces[0].flatten(0.01))


def test_trim_rectangle_corner_overlap():
    a = shapes.rectangle(0, 0, 20, 20)
    b = shapes.rectangle(10, 10, 20, 20)
    pieces = trim(a, (20, 15), [b])  # the part of a's right edge inside b
    assert len(pieces) == 1
    assert pieces[0].length() == pytest.approx(80 - 20, abs=1e-6)


def test_sub_contour_wraps_closed():
    sq = shapes.rectangle(0, 0, 10, 10)
    part = sub_contour(sq, 3.5, 0.5)  # from the middle of the left edge round to the bottom edge
    assert part.length() == pytest.approx(10, abs=1e-9)


def test_self_intersection_detection():
    bowtie = Contour.polyline([(0, 0), (10, 10), (10, 0), (0, 10)], True)
    assert self_intersects(bowtie)
    assert not self_intersects(shapes.star((0, 0), 10, 4, 5))


def test_vector_doctor_fixes_and_reports():
    gap = Contour.polyline([(0, 0), (10, 0), (10, 10), (0.03, 0.02)], False)
    opened = shapes.polyline([(50, 0), (60, 0)])
    tiny = shapes.circle((80, 80), 0.01)
    bowtie = Contour.polyline([(100, 0), (110, 10), (110, 0), (100, 10)], True)
    sq = shapes.rectangle(200, 0, 10, 10)
    p, s, objs = make_project([gap, opened, tiny, bowtie, sq, shapes.rectangle(200, 0, 10, 10)])
    ctx = CommandContext(p, s)
    run_command(REGISTRY.commands["edit.doctor"], ctx, {"gap": 0.1, "min_size": 0.05, "fix": True})
    assert objs[0].closed  # gap closed
    ids = {v.id for v in s.vectors}
    assert objs[2].id not in ids  # tiny deleted
    assert len([v for v in s.vectors if v.bbox().xmin == 200]) == 1  # duplicate deleted
    assert set(ctx.new_selection) == {objs[1].id, objs[3].id}  # left for the user: open + crossing
    assert "1 open" in ctx.messages[0] and "1 crossing" in ctx.messages[0]

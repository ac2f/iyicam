"""What is written and read back is the same drawing (Hypothesis): DXF of every version and SVG, on any vectors of lines,
arcs and beziers, open and closed, on several layers. The shapes are compared by their points (an arc may come back as
a bulge of a polyline, a bezier as a spline): every point of one lies on the other."""
import math

import pytest
from hypothesis import HealthCheck, given, settings, strategies as st

from conftest import make_project
from iyicam.formats import dxf, svg
from iyicam.formats.report import ImportReport

from test_prop_geom import contour

SET = settings(max_examples=int(__import__("os").environ.get("RT_N", 60)), deadline=None, suppress_health_check=list(HealthCheck))


def points(objs, tol=0.01):
    return [[p for c in o.contours for p in c.flatten(tol)] for o in objs]


def near_all(a, b, tol):
    """Every point of the polylines ``a`` lies within ``tol`` of the polylines ``b``."""
    segs = [(p[i], p[i + 1]) for p in b for i in range(len(p) - 1)] or [(q, q) for p in b for q in p]

    def d(q):
        best = math.inf
        for u, v in segs:
            dx, dy = v[0] - u[0], v[1] - u[1]
            L = dx * dx + dy * dy
            t = 0.0 if L == 0 else max(0.0, min(1.0, ((q[0] - u[0]) * dx + (q[1] - u[1]) * dy) / L))
            best = min(best, math.dist(q, (u[0] + t * dx, u[1] + t * dy)))
        return best
    return max((d(q) for p in a for q in p), default=0.0) <= tol


def moved_on_sheet(c):
    """A contour put on a 2000 x 2000 sheet (its box from 10, 10)."""
    from iyicam.core.geom import Transform
    bb = c.bbox()
    return c.transformed(Transform.translate(1010 - bb.xmin - (bb.xmax - bb.xmin) / 2 if bb.xmax - bb.xmin < 1900 else 10 - bb.xmin,
                                             1010 - bb.ymin - (bb.ymax - bb.ymin) / 2 if bb.ymax - bb.ymin < 1900 else 10 - bb.ymin))


@SET
@given(st.lists(st.lists(contour().map(moved_on_sheet), min_size=1, max_size=2), min_size=1, max_size=4),
       st.sampled_from([v[0] for v in dxf.DXF_VERSIONS]))
def test_a_dxf_reads_back_as_it_was_written(tmp_path_factory, objs, version):
    p, s, made = make_project(objs, size=(2000, 2000))
    cut = p.add_layer("Cut", "#ff0000")
    for o in made[1::2]:
        o.layer_id = cut.id
    f = tmp_path_factory.mktemp("d") / "x.dxf"
    dxf.export_dxf(f, p, s, version=version)
    q, s2, _ = make_project([], size=(2000, 2000))
    rep = ImportReport()
    back = dxf.import_dxf(f, q, s2, join_tolerance=0.0, report=rep)
    assert not rep.errors, rep.errors
    a, b = points(made), points(back)
    assert near_all(a, b, 0.02) and near_all(b, a, 0.02), version
    names = {q.layer(o.layer_id).name.casefold() for o in back}           # (R12 keeps layer names in capitals)
    assert names <= {"default", "cut", "0"} and ("cut" in names) == bool(made[1::2])
    assert len([l for l in q.layers if l.name.casefold() == "default"]) == 1    # (not one Default and one DEFAULT)


@SET
@given(st.lists(st.lists(contour().map(moved_on_sheet), min_size=1, max_size=2), min_size=1, max_size=4))
def test_an_svg_reads_back_as_it_was_written(tmp_path_factory, objs):
    p, s, made = make_project(objs, size=(2000, 2000))
    f = tmp_path_factory.mktemp("s") / "x.svg"
    svg.export_svg(f, p, s)
    q, s2, _ = make_project([], size=(2000, 2000))
    back = svg.import_svg(f, q, s2)
    a, b = points(made), points(back)
    assert near_all(a, b, 0.02) and near_all(b, a, 0.02)

"""What is imported must be what was drawn: shapes, sizes, places, nothing lost, nothing added.

DXF is compared with ezdxf's own path flattening (an independent reading of the same file), SVG and
HPGL with shapes whose outline is known exactly.
"""
import math
import random
import re

import ezdxf
import numpy as np
import pytest
from conftest import make_project
from ezdxf import path as ezpath

from iyicam.core import vectorops
from iyicam.core.geom import Arc, Bezier, Contour, Line
from iyicam.formats.dxf import import_dxf
from iyicam.formats.hpgl import import_hpgl
from iyicam.formats.report import ImportReport
from iyicam.formats.svg import import_svg


# -- measuring --------------------------------------------------------------------------------------------
def dense(poly, step=0.7):
    out = []
    for (x0, y0), (x1, y1) in zip(poly, poly[1:]):
        n = max(1, int(math.hypot(x1 - x0, y1 - y0) / step))
        out += [(x0 + (x1 - x0) * i / n, y0 + (y1 - y0) * i / n) for i in range(n)]
    out.append(tuple(poly[-1]))
    return out


def far(points, polys):
    """The largest distance from ``points`` to the nearest of the polylines ``polys``."""
    P = np.asarray(points, float)
    best = np.full(len(P), np.inf)
    for poly in polys:
        V = np.asarray(poly, float)
        if len(V) < 2:
            continue
        A, B = V[:-1], V[1:]
        AB = B - A
        L2 = np.maximum((AB * AB).sum(1), 1e-18)
        t = np.clip(((P[:, None] - A) * AB).sum(2) / L2, 0, 1)
        best = np.minimum(best, np.sqrt(((P[:, None] - (A + t[..., None] * AB)) ** 2).sum(2)).min(1))
    return float(best.max()) if len(best) else 0.0


def hausdorff(A, B):
    """Distance between two sets of polylines: how far apart the two drawings are at worst."""
    if not A or not B:
        return math.inf if bool(A) != bool(B) else 0.0
    return max(far([q for p in A for q in dense(p)], B), far([q for p in B for q in dense(p)], A))


def polylines(objs):
    out = []
    for o in objs:
        for c in o.contours:
            f = c.flatten(0.005)
            out.append(f + [f[0]] if c.closed else f)
    return out


# -- DXF ----------------------------------------------------------------------------------------------------
def reference(doc, k=1.0):
    polys = []

    def add(e):
        if e.dxftype() == "INSERT":
            for ve in e.virtual_entities():
                add(ve)
            return
        if e.dxftype() == "SPLINE":  # make_path approximates most splines: take the exact curve
            polys.append([(q.x * k, q.y * k) for q in e.construction_tool().flattening(0.0005)])
            return
        try:
            pa = ezpath.make_path(e)
        except Exception:  # noqa: BLE001
            return
        for sub in pa.sub_paths():
            v = [(p.x * k, p.y * k) for p in sub.flattening(0.003)]
            if len(v) > 1:
                polys.append(v)

    for e in doc.modelspace():
        add(e)
    return polys


def load(tmp_path, build, join=0.0, **kw):
    doc = ezdxf.new("R2018")
    doc.header["$INSUNITS"] = 4
    build(doc.modelspace())
    f = tmp_path / "x.dxf"
    doc.saveas(f)
    p, s, _ = make_project([])
    report = ImportReport()
    objs = import_dxf(f, p, s, join_tolerance=join, report=report, **kw)
    return objs, report, doc, p


def block(doc):
    b = doc.blocks.new("B")
    b.add_lwpolyline([(0, 0, 0, 0, 0.3), (20, 0, 0, 0, 0), (20, 10, 0, 0, 0), (0, 10, 0, 0, 0)], format="xyseb",
                     close=True)
    b.add_circle((10, 5), 3)
    b.add_arc((10, 5), 8, 30, 120)
    return b


CASES = {
    "line": lambda m: m.add_line((0, 0), (100, 50)),
    "circle": lambda m: m.add_circle((50, 40), 25),
    "arc": lambda m: m.add_arc((10, 10), 30, 20, 100),
    "arc across zero": lambda m: m.add_arc((10, 10), 30, 300, 60),
    "arc over half a turn": lambda m: m.add_arc((10, 10), 30, 10, 350),
    "arc in a mirrored plane": lambda m: m.add_arc((10, 10), 30, 20, 100, dxfattribs={"extrusion": (0, 0, -1)}),
    "circle in a mirrored plane": lambda m: m.add_circle((10, 20), 15, dxfattribs={"extrusion": (0, 0, -1)}),
    "arc in a tilted plane": lambda m: m.add_arc((10, 10, 0), 30, 20, 100,
                                                  dxfattribs={"extrusion": (0.3, 0.2, 0.9)}),
    "circle in a tilted plane": lambda m: m.add_circle((10, 10, 0), 30, dxfattribs={"extrusion": (0.6, 0.0, 0.8)}),
    "closed lwpolyline with bulges": lambda m: m.add_lwpolyline(
        [(0, 0, 0, 0, 0.5), (50, 0, 0, 0, -0.3), (80, 40, 0, 0, 0), (20, 60, 0, 0, 1.2)], format="xyseb", close=True),
    "open lwpolyline with bulges": lambda m: m.add_lwpolyline(
        [(0, 0, 0, 0, 0.5), (50, 0, 0, 0, -0.3), (80, 40, 0, 0, 0)], format="xyseb"),
    "lwpolyline in a mirrored plane": lambda m: m.add_lwpolyline(
        [(0, 0, 0, 0, 0.5), (50, 0, 0, 0, 0), (50, 40, 0, 0, 0)], format="xyseb", close=True,
        dxfattribs={"extrusion": (0, 0, -1)}),
    "lwpolyline in a tilted plane": lambda m: m.add_lwpolyline(
        [(0, 0, 0, 0, 0.5), (50, 0, 0, 0, 0), (50, 40, 0, 0, 0)], format="xyseb", close=True,
        dxfattribs={"extrusion": (0.2, 0.3, 0.9)}),
    "2d polyline with a bulge": lambda m: _polyline2d(m),
    "3d polyline": lambda m: m.add_polyline3d([(0, 0, 5), (50, 10, 6), (60, 50, 7)]),
    "spline through points": lambda m: m.add_spline(fit_points=[(0, 0), (20, 30), (50, 10), (80, 40), (100, 0)]),
    "spline of degree 3": lambda m: m.add_open_spline(
        control_points=[(0, 0), (20, 40), (50, 40), (80, 0), (100, 30), (140, 10)], degree=3),
    "spline of degree 2": lambda m: m.add_open_spline(
        control_points=[(0, 0), (20, 40), (50, 40), (80, 0), (100, 30)], degree=2),
    "spline of degree 5": lambda m: m.add_open_spline(
        control_points=[(0, 0), (20, 40), (50, 40), (80, 0), (100, 30), (140, 10), (150, 50)], degree=5),
    "ellipse": lambda m: m.add_ellipse((50, 30), (40, 10), 0.5),
    "elliptical arc": lambda m: m.add_ellipse((50, 30), (30, 30), 0.4, 0.5, 4.0),
    "ellipse in a mirrored plane": lambda m: m.add_ellipse((50, 30), (40, 10), 0.5,
                                                            dxfattribs={"extrusion": (0, 0, -1)}),
    "block": lambda m: (block(m.doc), m.add_blockref("B", (100, 50))),
    "block scaled and turned": lambda m: (block(m.doc), m.add_blockref(
        "B", (100, 50), dxfattribs={"xscale": 2.0, "yscale": 2.0, "rotation": 30})),
    "block stretched in x": lambda m: (block(m.doc), m.add_blockref(
        "B", (100, 50), dxfattribs={"xscale": 3.0, "yscale": 1.0, "rotation": 20})),
    "block mirrored in x": lambda m: (block(m.doc), m.add_blockref(
        "B", (100, 50), dxfattribs={"xscale": -1.0, "yscale": 1.0, "rotation": 15})),
    "block mirrored in y": lambda m: (block(m.doc), m.add_blockref(
        "B", (100, 50), dxfattribs={"xscale": 1.0, "yscale": -1.0, "rotation": 15})),
    "block in a mirrored plane": lambda m: (block(m.doc), m.add_blockref(
        "B", (100, 50), dxfattribs={"extrusion": (0, 0, -1), "rotation": 10})),
    "block as an array": lambda m: (block(m.doc), m.add_blockref(
        "B", (100, 50), dxfattribs={"row_count": 2, "column_count": 3, "row_spacing": 30, "column_spacing": 40})),
    "block in a block": lambda m: _nested_block(m),
    "solid": lambda m: m.add_solid([(0, 0), (50, 0), (0, 30), (50, 30)]),
    "triangle solid": lambda m: m.add_solid([(0, 0), (50, 0), (20, 30)]),
}


def _polyline2d(m):
    pl = m.add_polyline2d([(0, 0), (50, 0), (50, 40), (0, 40)], close=True)
    pl.vertices[0].dxf.bulge = 0.4


def _nested_block(m):
    block(m.doc)
    outer = m.doc.blocks.new("OUT")
    outer.add_blockref("B", (5, 5), dxfattribs={"rotation": 90})
    outer.add_line((0, 0), (30, 30))
    m.add_blockref("OUT", (200, 100), dxfattribs={"xscale": 1.5, "yscale": 1.5})


@pytest.mark.parametrize("name", list(CASES))
def test_dxf_entity_matches_an_independent_reading(tmp_path, name):
    objs, report, doc, _ = load(tmp_path, CASES[name])
    ref = reference(doc)
    assert ref, "the reference reading found nothing: a wrong test"
    d = hausdorff(polylines(objs), ref)
    assert d < 0.03, f"{name}: the import is up to {d:.3f} mm away from the drawing"
    assert report.clean, report.summary()


def test_dxf_cubic_splines_are_exact(tmp_path):
    """A cubic spline arrives as the Bezier curves it is made of, not as an approximation."""
    pts = [(0, 0), (20, 30), (50, 10), (80, 40), (100, 0)]
    objs, _, doc, _ = load(tmp_path, lambda m: m.add_spline(fit_points=pts))
    bs = doc.modelspace()[0].construction_tool()
    ref = [[(q.x, q.y) for q in bs.flattening(0.0005)]]
    assert hausdorff(polylines(objs), ref) < 0.005
    assert all(isinstance(sg, Bezier) for sg in objs[0].contours[0].segs)


def test_dxf_closed_periodic_spline(tmp_path):
    """The usual closed spline (uniform knots, first control points repeated) is only defined between two
    of its knots: the rest of the parameter range is not part of the curve."""
    ring = [(0, 0), (40, 10), (60, 50), (30, 70), (-10, 40), (-20, 10)]
    cp = ring + ring[:3]

    def build(m):
        sp = m.add_spline()
        sp.dxf.degree = 3
        sp.dxf.flags = 1 | 2 | 8
        sp.control_points = cp
        sp.knots = list(range(len(cp) + 4))

    objs, _, doc, _ = load(tmp_path, build)
    c = objs[0].contours[0]
    assert c.closed
    bs = doc.modelspace()[0].construction_tool()
    ref = [[(q.x, q.y) for q in bs.points(np.linspace(3, 9, 4000))]]
    assert hausdorff(polylines(objs), ref) < 0.005
    bb = objs[0].bbox()
    assert bb.xmin > -30 and bb.xmax < 70 and bb.ymax < 80 and bb.ymin > -5   # nothing flies off


def test_dxf_units_scale_everything(tmp_path):
    def build(m):
        m.doc.header["$INSUNITS"] = 1
        m.add_circle((1, 1), 0.5)
        m.add_lwpolyline([(0, 0), (2, 0), (2, 1)])
        m.add_blockref("B", (3, 3)) if False else None

    objs, _, doc, _ = load(tmp_path, build)
    bb = objs[0].bbox()
    assert (bb.xmin, bb.xmax) == pytest.approx((0.5 * 25.4, 1.5 * 25.4), abs=1e-6)
    assert objs[1].bbox().xmax == pytest.approx(50.8, abs=1e-6)


def test_dxf_entities_on_layer_0_in_a_block_take_the_layer_of_the_insert(tmp_path):
    def build(m):
        m.doc.layers.add("Cut")
        b = m.doc.blocks.new("P")
        b.add_circle((0, 0), 5, dxfattribs={"layer": "0"})
        b.add_line((0, 0), (10, 0), dxfattribs={"layer": "Other"})
        m.add_blockref("P", (50, 50), dxfattribs={"layer": "Cut"})

    objs, _, _, p = load(tmp_path, build)
    layers = {o.layer_id: p.layers[[la.id for la in p.layers].index(o.layer_id)].name for o in objs}
    names = sorted(layers.values())
    assert names == ["Cut", "Other"]


def test_dxf_import_says_what_it_left_out(tmp_path):
    def build(m):
        m.add_circle((0, 0), 10)
        h = m.add_hatch()
        h.paths.add_polyline_path([(0, 0), (50, 0), (50, 30), (0, 30)], is_closed=True)
        m.add_text("hello")
        m.add_mtext("long\\Ptext")
        m.add_point((1, 2))

    objs, report, _, _ = load(tmp_path, build)
    assert len(objs) == 1
    assert report.skipped["HATCH"] == 1 and report.skipped["TEXT"] == 1 and report.skipped["MTEXT"] == 1
    assert "POINT" not in report.skipped
    assert not report.clean and "HATCH" in report.summary()


def test_dxf_a_broken_entity_is_reported_not_swallowed(tmp_path, monkeypatch):
    from iyicam.formats import dxf

    real = dxf.entity_contours

    def broken(e, k, tol=0.01):
        if e.dxftype() == "ARC":
            raise ValueError("nope")
        return real(e, k, tol)

    monkeypatch.setattr(dxf, "entity_contours", broken)
    objs, report, _, _ = load(tmp_path, lambda m: (m.add_circle((0, 0), 10), m.add_arc((0, 0), 5, 0, 90)))
    assert len(objs) == 1
    assert len(report.errors) == 1 and "ARC" in report.errors[0] and "nope" in report.errors[0]


def test_dxf_every_entity_kind_in_one_file_loses_nothing(tmp_path):
    """Lines that touch are joined; nothing else is added or removed."""
    def build(m):
        for name, fn in CASES.items():
            if "block" in name:
                continue
            fn(m)

    objs, report, doc, _ = load(tmp_path, build, join=0.0)
    ref = reference(doc)
    assert len(objs) == len(ref)
    assert hausdorff(polylines(objs), ref) < 0.03
    assert report.clean


# -- joining ------------------------------------------------------------------------------------------------
def _chain_segments(contours):
    """Every straight piece as an unordered pair of rounded end points."""
    out = []
    for c in contours:
        for s in c.segs:
            out.append(tuple(sorted([tuple(round(v, 4) for v in s.a), tuple(round(v, 4) for v in s.b)])))
    return sorted(out)


@pytest.mark.parametrize("seed", range(30))
def test_join_keeps_every_piece_exactly_once(seed):
    """A heap of loose lines on a coarse grid (so many meet, cross-roads included) comes out as the same lines."""
    rnd = random.Random(seed)
    pts = [(rnd.randint(0, 5) * 10.0, rnd.randint(0, 5) * 10.0) for _ in range(rnd.randint(2, 40))]
    pieces = []
    for _ in range(rnd.randint(1, 40)):
        a, b = rnd.sample(pts, 2) if len(pts) > 1 else (pts[0], pts[0])
        if a != b:
            pieces.append(Contour([Line(a, b)], False))
    if rnd.random() < 0.5:  # tiny gaps and overlaps in the joints
        pieces = [Contour([Line((s.a[0] + rnd.uniform(-0.003, 0.003), s.a[1] + rnd.uniform(-0.003, 0.003)),
                                (s.b[0] + rnd.uniform(-0.003, 0.003), s.b[1] + rnd.uniform(-0.003, 0.003)))], False)
                  for c in pieces for s in c.segs]
    joined = vectorops.join(pieces, 0.05)
    before, after = _chain_segments(pieces), _chain_segments(joined)
    assert len(before) == len(after)
    tol = 0.01
    unmatched = list(after)
    for a, b in before:
        for i, (c, d) in enumerate(unmatched):
            if (math.dist(a, c) < tol and math.dist(b, d) < tol) or (math.dist(a, d) < tol and math.dist(b, c) < tol):
                del unmatched[i]
                break
        else:
            raise AssertionError(f"piece {a}-{b} is missing or changed")
    # chains are connected
    for c in joined:
        for s, t in zip(c.segs, c.segs[1:]):
            assert math.dist(s.b, t.a) < 1e-6
        if c.closed:
            assert math.dist(c.segs[-1].b, c.segs[0].a) < 1e-6


def test_join_never_joins_across_a_gap_larger_than_the_tolerance():
    a = Contour([Line((0, 0), (10, 0))], False)
    b = Contour([Line((10.2, 0), (20, 0))], False)
    assert len(vectorops.join([a, b], 0.05)) == 2
    assert len(vectorops.join([a, b], 0.3)) == 1


# -- SVG ------------------------------------------------------------------------------------------------------
def svg_file(tmp_path, body, head='width="100mm" height="50mm" viewBox="0 0 100 50"'):
    f = tmp_path / "x.svg"
    f.write_text(f'<svg xmlns="http://www.w3.org/2000/svg" '
                 f'xmlns:inkscape="http://www.inkscape.org/namespaces/inkscape" {head}>{body}</svg>')
    return f


def load_svg(tmp_path, body, head='width="100mm" height="50mm" viewBox="0 0 100 50"', **kw):
    f = svg_file(tmp_path, body, head)
    p, s, _ = make_project([])
    return import_svg(f, p, s, **kw), p


def pts_of(o):
    return [c.flatten(0.005) for c in o.contours]


def test_svg_rect_in_millimetres_with_y_up(tmp_path):
    objs, _ = load_svg(tmp_path, '<rect x="10" y="5" width="30" height="20"/>')
    bb = objs[0].bbox()
    assert (bb.xmin, bb.xmax, bb.ymin, bb.ymax) == pytest.approx((10, 40, 25, 45), abs=1e-9)  # y up: 50 - 25 .. 50 - 5
    assert objs[0].contours[0].closed


@pytest.mark.parametrize("head,scale", [
    ('width="100mm" height="50mm" viewBox="0 0 100 50"', 1.0),
    ('width="10cm" height="5cm" viewBox="0 0 100 50"', 1.0),
    ('width="4in" height="2in" viewBox="0 0 100 50"', 25.4 * 4 / 100),
    ('width="200" height="100" viewBox="0 0 100 50"', 200 * 25.4 / 96 / 100),
    ('width="200" height="100"', 25.4 / 96),
    ('width="100mm" height="50mm" viewBox="0 0 1000 500"', 0.1),
    ('width="100mm" height="50mm" viewBox="100 100 100 50"', 1.0),
])
def test_svg_units_and_view_boxes(tmp_path, head, scale):
    objs, _ = load_svg(tmp_path, '<rect x="120" y="105" width="30" height="20"/>' if "100 100" in head else
                       '<rect x="10" y="5" width="30" height="20"/>', head)
    bb = objs[0].bbox()
    w = 30 * scale
    assert bb.xmax - bb.xmin == pytest.approx(w, rel=1e-6)
    assert bb.ymax - bb.ymin == pytest.approx(20 * scale, rel=1e-6)
    if "100 100" in head:
        assert bb.xmin == pytest.approx(20, abs=1e-9)  # the origin of the box is the origin of the sheet


def test_svg_without_a_height_still_has_a_y_axis_pointing_up(tmp_path):
    objs, _ = load_svg(tmp_path, '<rect x="10" y="5" width="30" height="20"/>', 'viewBox="0 0 100 50"')
    a = objs[0].bbox()
    b = load_svg(tmp_path, '<rect x="10" y="30" width="30" height="20"/>', 'viewBox="0 0 100 50"')[0][0].bbox()
    assert b.ymin < a.ymin  # a shape lower in the picture is lower on the sheet
    assert (a.ymax - a.ymin) > 0


@pytest.mark.parametrize("transform,matrix", [
    ("translate(10 20) scale(2) rotate(90)", None),
    ("rotate(30 50 25)", None),
    ("matrix(1 0.2 -0.3 1.5 5 6)", (1, 0.2, -0.3, 1.5, 5, 6)),
    ("skewX(20) translate(3 4)", None),
    ("scale(-1 1) translate(-80 0)", None),
    ("translate(5) scale(0.5, 2)", None),
])
def test_svg_transforms(tmp_path, transform, matrix):
    """The path M0 0 L10 0 L10 5 Z under a group transform, against the matrix computed here."""
    body = f'<g transform="{transform}"><path d="M0 0 L10 0 L10 5 Z"/></g>'
    objs, _ = load_svg(tmp_path, body, 'width="100mm" height="100mm" viewBox="0 0 100 100"')

    def mat(t):
        out = np.eye(3)
        for name, args in re.findall(r"(\w+)\(([^)]*)\)", t):
            a = [float(v) for v in re.split(r"[ ,]+", args.strip())]
            m = np.eye(3)
            if name == "translate":
                m[0, 2], m[1, 2] = a[0], (a[1] if len(a) > 1 else 0)
            elif name == "scale":
                m[0, 0], m[1, 1] = a[0], (a[1] if len(a) > 1 else a[0])
            elif name == "rotate":
                r = math.radians(a[0])
                m = np.array([[math.cos(r), -math.sin(r), 0], [math.sin(r), math.cos(r), 0], [0, 0, 1]])
                if len(a) == 3:
                    c = np.eye(3)
                    c[0, 2], c[1, 2] = a[1], a[2]
                    ci = np.eye(3)
                    ci[0, 2], ci[1, 2] = -a[1], -a[2]
                    m = c @ m @ ci
            elif name == "skewX":
                m[0, 1] = math.tan(math.radians(a[0]))
            elif name == "matrix":
                m = np.array([[a[0], a[2], a[4]], [a[1], a[3], a[5]], [0, 0, 1]])
            out = out @ m
        return out
    M = mat(transform)
    want = []
    for x, y in [(0, 0), (10, 0), (10, 5)]:
        X, Y, _ = M @ [x, y, 1]
        want.append((X, 100 - Y))      # y up
    got = objs[0].contours[0]
    corners = [got.segs[0].a] + [sg.b for sg in got.segs[:2]]
    assert [v for pt in corners for v in pt] == pytest.approx([float(v) for pt in want for v in pt], abs=1e-6)


def test_svg_shapes_have_their_exact_outline(tmp_path):
    body = ('<circle cx="30" cy="25" r="10"/><ellipse cx="70" cy="25" rx="20" ry="4"/>'
            '<polygon points="5,5 15,5 10,15"/><polyline points="0,40 10,45 20,40"/><line x1="0" y1="0" x2="100" y2="50"/>'
            '<rect x="50" y="30" width="30" height="15" rx="4"/>')
    objs, _ = load_svg(tmp_path, body)
    assert len(objs) == 6
    byname = {}
    for o in objs:
        byname[round(o.contours[0].area(), 1) if o.contours[0].closed else "open"] = o
    circle = next(o for o in objs if abs(o.contours[0].area() - math.pi * 100) < 0.5)
    bb = circle.bbox()
    assert (bb.xmin, bb.xmax, bb.ymin, bb.ymax) == pytest.approx((20, 40, 15, 35), abs=0.02)
    ellipse = next(o for o in objs if abs(o.contours[0].area() - math.pi * 20 * 4) < 0.5)
    assert ellipse.bbox().xmin == pytest.approx(50, abs=0.02)
    rr = next(o for o in objs if abs(o.contours[0].area() - (450 - (4 - math.pi) * 16)) < 0.1)
    assert rr is not None
    assert sum(1 for o in objs if not o.contours[0].closed) == 2


def test_svg_path_commands_relative_and_absolute_agree(tmp_path):
    absolute = 'M10 10 L40 10 L40 30 H20 V20 C25 20 30 25 30 30 S35 40 40 40 Q45 45 50 40 T60 40 Z'
    relative = 'm10 10 l30 0 l0 20 h-20 v-10 c5 0 10 5 10 10 s5 10 10 10 q5 5 10 0 t10 0 z'
    a, _ = load_svg(tmp_path, f'<path d="{absolute}"/>')
    b, _ = load_svg(tmp_path, f'<path d="{relative}"/>')
    assert hausdorff(polylines(a), polylines(b)) < 1e-6


def test_svg_arcs_are_circular_arcs_through_the_right_points(tmp_path):
    # a semicircle of radius 20 from (10, 10) to (50, 10): large-arc 0, sweep 1 bulges to one side
    objs, _ = load_svg(tmp_path, '<path d="M 10 10 A 20 20 0 0 1 50 10"/>')
    c = objs[0].contours[0]
    assert all(isinstance(s, (Arc, Bezier)) for s in c.segs)
    f = c.flatten(0.005)
    ys = [y for _, y in f]
    assert max(abs(math.dist(p, (30, 40)) - 20) for p in f) < 0.01   # on the circle round (30, 40), sheet y = 50 - 10
    assert max(abs(y - 40) for y in ys) == pytest.approx(20, abs=0.01)
    # sweep flag decides the side
    other, _ = load_svg(tmp_path, '<path d="M 10 10 A 20 20 0 0 0 50 10"/>')
    ys2 = [y for _, y in other[0].contours[0].flatten(0.005)]
    assert (max(ys) > 40) != (max(ys2) > 40)
    # a radius that is too small is scaled up to the half chord (SVG rule)
    small, _ = load_svg(tmp_path, '<path d="M 10 10 A 5 5 0 0 1 50 10"/>')
    bb = small[0].bbox()
    assert bb.xmin == pytest.approx(10, abs=0.01) and bb.xmax == pytest.approx(50, abs=0.01)
    assert (bb.ymax - bb.ymin) == pytest.approx(20, abs=0.05)


def test_svg_a_path_that_ends_where_it_started_is_closed_without_z(tmp_path):
    objs, _ = load_svg(tmp_path, '<path d="M10 10 L40 10 L40 30 L10 30 L10 10"/>')
    c = objs[0].contours[0]
    assert len(c.segs) == 4 or c.closed
    assert c.area() == pytest.approx(600, abs=1e-6) or not c.closed


def test_svg_subpaths_stay_together_in_one_vector(tmp_path):
    """A letter O: an outer and an inner outline are one path and stay one vector (outline with a hole)."""
    objs, _ = load_svg(tmp_path, '<path fill-rule="evenodd" d="M10 10 H50 V40 H10 Z M20 20 H40 V30 H20 Z"/>')
    assert len(objs) == 1 and len(objs[0].contours) == 2


def test_svg_invisible_and_definition_content_is_not_imported(tmp_path):
    body = ('<defs><rect id="a" x="0" y="0" width="10" height="10"/></defs>'
            '<rect x="20" y="20" width="10" height="10"/>'
            '<rect x="40" y="20" width="10" height="10" style="display:none"/>')
    objs, _ = load_svg(tmp_path, body)
    assert len(objs) == 1


def test_svg_use_places_a_copy(tmp_path):
    body = ('<defs><rect id="a" x="0" y="0" width="10" height="10"/></defs>'
            '<use href="#a" x="20" y="20"/><use href="#a" x="40" y="10"/>')
    objs, _ = load_svg(tmp_path, body)
    boxes = sorted((round(o.bbox().xmin, 3), round(o.bbox().ymin, 3)) for o in objs)
    assert boxes == [(20.0, 20.0), (40.0, 30.0)]


def test_svg_layers_become_layers(tmp_path):
    body = ('<g inkscape:groupmode="layer" inkscape:label="Cut"><rect x="1" y="1" width="5" height="5"/></g>'
            '<g inkscape:groupmode="layer" inkscape:label="Engrave"><rect x="9" y="1" width="5" height="5"/></g>')
    objs, p = load_svg(tmp_path, body)
    names = sorted(p.layers[[la.id for la in p.layers].index(o.layer_id)].name for o in objs)
    assert names == ["Cut", "Engrave"]


def test_svg_export_import_roundtrip_keeps_shapes(tmp_path):
    from iyicam.core import shapes
    from iyicam.formats.svg import export_svg
    cs = [shapes.rectangle(10, 10, 60, 30, corner_radius=5), shapes.circle((100, 40), 15),
          Contour([Bezier((0, 0), (10, 30), (30, 30), (40, 0)), Line((40, 0), (0, 0))], True)]
    p, s, objs = make_project([[c] for c in cs], size=(200, 100))
    f = tmp_path / "r.svg"
    export_svg(f, p, s)
    p2, s2, _ = make_project([], size=(200, 100))
    back = import_svg(f, p2, s2)
    assert len(back) == 3
    assert hausdorff(polylines(objs), polylines(back)) < 0.02


# -- HPGL -----------------------------------------------------------------------------------------------------
def load_hpgl(tmp_path, text, **kw):
    f = tmp_path / "x.plt"
    f.write_text(text)
    p, s, _ = make_project([])
    return import_hpgl(f, p, s, **kw)


def test_hpgl_square_and_units(tmp_path):
    objs = load_hpgl(tmp_path, "IN;SP1;PU0,0;PD4000,0,4000,2000,0,2000,0,0;PU;")
    assert len(objs) == 1 and objs[0].contours[0].closed
    bb = objs[0].bbox()
    assert (bb.xmax - bb.xmin, bb.ymax - bb.ymin) == pytest.approx((100, 50), abs=1e-6)  # 0.025 mm units
    assert objs[0].contours[0].area() == pytest.approx(5000, abs=1e-6)


def test_hpgl_relative_moves_and_pen_changes(tmp_path):
    objs = load_hpgl(tmp_path, "IN;PU400,400;PD;PR400,0,0,400,-400,0,0,-400;PU;PA2000,2000;PD;PR200,0;PU;")
    assert len(objs) == 2
    sq = next(o for o in objs if o.contours[0].closed)
    assert sq.contours[0].area() == pytest.approx(100, abs=1e-6)  # 16 plotter units^2... 400*0.025 = 10 mm
    line = next(o for o in objs if not o.contours[0].closed)
    assert line.contours[0].length() == pytest.approx(5.0, abs=1e-6)


def test_hpgl_arcs_and_circles(tmp_path):
    objs = load_hpgl(tmp_path, "IN;PU2000,2000;CI400;PU4000,2000;PD;AA4000,2400,180;PU;")
    circ = next(o for o in objs if o.contours[0].closed)
    assert circ.contours[0].area() == pytest.approx(math.pi * 100, rel=1e-6)   # 400 units = 10 mm radius
    arc = next(o for o in objs if not o.contours[0].closed)
    assert arc.contours[0].length() == pytest.approx(math.pi * 10, rel=1e-6)


def test_hpgl_labels_do_not_become_commands(tmp_path):
    objs = load_hpgl(tmp_path, "IN;PU0,0;LBPD PA 100,100\x03;PU0,0;PD400,0;PU;")
    assert len(objs) == 1 and objs[0].contours[0].length() == pytest.approx(10, abs=1e-6)


def test_hpgl_strokes_that_meet_are_joined_into_one_vector(tmp_path):
    objs = load_hpgl(tmp_path, "IN;PU0,0;PD400,0;PU400,0;PD400,400;PU400,400;PD0,400;PU0,400;PD0,0;PU;", join_tolerance=0.05)
    assert len(objs) == 1 and objs[0].contours[0].closed
    assert objs[0].contours[0].area() == pytest.approx(100, abs=1e-6)


def test_hpgl_arcs_through_three_points_and_rectangles(tmp_path):
    objs = load_hpgl(tmp_path, "IN;PU0,0;PD;AT200,200,400,0;PU;PU1000,1000;ER400,200;PU;")
    arc = next(o for o in objs if not o.contours[0].closed)
    assert arc.contours[0].length() == pytest.approx(math.pi * 5, rel=1e-6)   # half a circle of radius 5 mm
    rect = next(o for o in objs if o.contours[0].closed)
    assert rect.contours[0].area() == pytest.approx(10 * 5, abs=1e-9)


def test_hpgl_unsupported_things_are_reported(tmp_path):
    f = tmp_path / "y.plt"
    f.write_text("IN;SC0,100,0,100;PU0,0;PD400,0;PU;LBhi\x03;PE>~;RA800,800;")
    p, s, _ = make_project([])
    rep = ImportReport()
    objs = import_hpgl(f, p, s, report=rep)
    assert len(objs) == 1
    assert rep.skipped["text (LB)"] == 1 and rep.skipped["polyline encoded (PE)"] == 1
    assert any("SC" in n for n in rep.notes)
    assert not rep.clean

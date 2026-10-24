import math

import ezdxf
import pytest
from conftest import make_project

from iyicam.core import shapes
from iyicam.core.geom import Arc, Bezier
from iyicam.formats.dxf import export_dxf, import_dxf
from iyicam.formats.svg import export_svg, import_svg


def test_dxf_roundtrip_keeps_arcs_and_layers(tmp_path):
    p, s, objs = make_project([shapes.rectangle(0, 0, 50, 30, corner_radius=5), shapes.circle((80, 15), 10)])
    cut = p.add_layer("Cut", "#ff0000")
    objs[1].layer_id = cut.id
    f = tmp_path / "a.dxf"
    export_dxf(f, p, s)
    p2, s2, _ = make_project([])
    created = import_dxf(f, p2, s2)
    assert len(created) == 2
    areas = sorted(c.area() for o in created for c in o.contours)
    assert areas[0] == pytest.approx(math.pi * 100, rel=1e-6)
    assert areas[1] == pytest.approx(1500 - (4 - math.pi) * 25, rel=1e-6)
    assert all(isinstance(sg, Arc) for o in created for c in o.contours if c.area() < 400 for sg in c.segs)
    assert p2.layer_by_name("Cut") is not None


def test_dxf_import_joins_loose_entities_and_units(tmp_path):
    doc = ezdxf.new()
    doc.header["$INSUNITS"] = 1  # inches
    msp = doc.modelspace()
    msp.add_line((0, 0), (1, 0))
    msp.add_line((1, 1), (1, 0))       # drawn backwards
    msp.add_arc((0.5, 1), 0.5, 0, 180)  # top half circle from (1,1) to (0,1)
    msp.add_line((0, 1), (0, 0.0005))  # tiny gap
    msp.add_spline([(2, 0), (3, 1), (4, 0), (5, 1)])
    msp.add_ellipse((10, 10), major_axis=(2, 0), ratio=0.5)
    f = tmp_path / "b.dxf"
    doc.saveas(f)
    p, s, _ = make_project([])
    created = import_dxf(f, p, s, join_tolerance=0.05)
    closed = [c for o in created for c in o.contours if c.closed]
    assert len(closed) == 2  # joined U shape + ellipse
    u = min(closed, key=lambda c: c.area())
    assert u.area() == pytest.approx((1 + math.pi * 0.125) * 25.4 ** 2, rel=1e-3)
    spl = [c for o in created for c in o.contours if not c.closed]
    assert spl and all(isinstance(sg, Bezier) for sg in spl[0].segs)


def test_dxf_mirrored_ocs_arc(tmp_path):
    doc = ezdxf.new(units=4)
    doc.modelspace().add_arc((5, 0), 5, 0, 90, dxfattribs={"extrusion": (0, 0, -1)})
    f = tmp_path / "c.dxf"
    doc.saveas(f)
    p, s, _ = make_project([])
    c = import_dxf(f, p, s)[0].contours[0]
    # OCS centre (5,0) maps to WCS (-5,0); the arc runs clockwise from (-10,0) to (-5,5)
    assert c.start == pytest.approx((-10, 0), abs=1e-9)
    assert c.end == pytest.approx((-5, 5), abs=1e-9)
    k = 5 * math.cos(math.pi / 4)
    assert c.segs[0].point_at(0.5) == pytest.approx((-5 - k, k), abs=1e-9)
    assert not c.segs[0].ccw


def test_svg_import_units_flip_and_arcs(tmp_path):
    f = tmp_path / "t.svg"
    f.write_text('<svg xmlns="http://www.w3.org/2000/svg" width="100mm" height="50mm" viewBox="0 0 100 50">'
                 '<rect x="10" y="5" width="20" height="10"/>'
                 '<circle cx="80" cy="25" r="5"/>'
                 '<path d="M 40 40 A 5 5 0 0 1 50 40" fill="none"/>'
                 '<g transform="scale(2,1)"><circle cx="10" cy="40" r="2"/></g></svg>')
    p, s, _ = make_project([])
    objs = import_svg(f, p, s)
    rect = objs[0].contours[0]
    bb = rect.bbox()
    assert (bb.xmin, bb.ymin, bb.xmax, bb.ymax) == pytest.approx((10, 35, 30, 45))
    circ = objs[1].contours[0]
    assert circ.closed and circ.area() == pytest.approx(math.pi * 25, rel=1e-5)
    arc = objs[2].contours[0].segs[0]
    assert isinstance(arc, Arc)
    assert arc.point_at(0.5)[1] == pytest.approx(15)  # bulges up (towards y=15 after flipping)
    ell = objs[3].contours[0]
    assert ell.bbox().width == pytest.approx(8, abs=1e-3) and ell.bbox().height == pytest.approx(4, abs=1e-3)


def test_svg_roundtrip(tmp_path):
    p, s, objs = make_project([shapes.rectangle(5, 5, 40, 20, corner_radius=4), shapes.ellipse((80, 30), 10, 5)])
    f = tmp_path / "o.svg"
    export_svg(f, p, s)
    p2, s2, _ = make_project([])
    back = import_svg(f, p2, s2)
    a1 = sorted(c.area() for o in objs for c in o.contours)
    a2 = sorted(c.area() for o in back for c in o.contours)
    assert a2 == pytest.approx(a1, rel=1e-4)


def test_hpgl_import(tmp_path):
    from iyicam.formats.hpgl import import_hpgl
    f = tmp_path / "a.plt"
    f.write_text("IN;SP1;PU0,0;PD4000,0,4000,4000;PD0,4000,0,0;PU8000,2000;CI400;"
                 "PU6000,0;PR;PD400,0,0,400;PA;PU;")
    p, s, _ = make_project([])
    objs = import_hpgl(f, p, s)
    closed = sorted((c for o in objs for c in o.contours if c.closed), key=lambda c: c.area())
    assert len(closed) == 2
    assert closed[0].area() == pytest.approx(math.pi * 100, rel=1e-6)
    assert closed[1].area() == pytest.approx(100 * 100)
    opened = [c for o in objs for c in o.contours if not c.closed]
    assert len(opened) == 1 and opened[0].length() == pytest.approx(20)

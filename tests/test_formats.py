import math

import ezdxf
import pytest
from conftest import make_project

from iyicam.core import shapes
from iyicam.core.document import VectorObject
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


# -- DXF export: layers, their colours, AutoCAD versions --------------------------------------------------------
ALL_VERSIONS = ["R12", "R2000", "R2004", "R2007", "R2010", "R2013", "R2018"]


def _layered(names_colors):
    """A project with one vector on a layer for each ``(name, colour)`` and the ids of the layers."""
    p, s, _ = make_project([])
    ids = []
    for i, (name, color) in enumerate(names_colors):
        layer = p.add_layer(name, color)
        ids.append(layer.id)
        s.vectors.append(VectorObject([shapes.rectangle(30 * i, 0, 20, 20)], layer.id, f"v{i}"))
    return p, s, ids


@pytest.mark.parametrize("version", ALL_VERSIONS)
def test_dxf_export_every_version_keeps_layers_colours_and_shapes(tmp_path, version):
    p, s, _ = _layered([("Cut", "#ff0000"), ("Engrave", "#0000ff"), ("Pocket", "#00aa00")])
    s.vectors.append(VectorObject([shapes.rectangle(0, 50, 60, 30, corner_radius=6)], p.layer_by_name("Cut").id,
                                  "round"))
    f = tmp_path / f"v_{version}.dxf"
    export_dxf(f, p, s, version=version)
    doc = ezdxf.readfile(str(f))
    assert doc.acad_release == version
    names = {layer.dxf.name.lower(): layer for layer in doc.layers}
    assert {"cut", "engrave", "pocket"} <= set(names)
    assert names["cut"].color == 1 and names["engrave"].color == 5 and names["pocket"].color in range(2, 256)
    if version not in ("R12", "R2000"):
        assert names["cut"].rgb == (255, 0, 0) and names["engrave"].rgb == (0, 0, 255)
    on = {e.dxf.layer.lower() for e in doc.modelspace()}
    assert on == {"cut", "engrave", "pocket"}
    # and back: the shapes, the layers and the arcs of the rounded corners
    p2, s2, _ = make_project([])
    created = import_dxf(f, p2, s2)
    assert len(created) == 4
    assert {p2.layer(o.layer_id).name.lower() for o in created} == {"cut", "engrave", "pocket"}
    assert sum(isinstance(sg, Arc) for o in created for c in o.contours for sg in c.segs) == 4


def test_dxf_export_layer_names_that_dxf_does_not_allow(tmp_path):
    names = ['a/b', 'x:y', 'what?', '<in>', 'q"uote', 'a|b', '', '  ', 'Kes ğşı İç']
    p, s, _ = _layered([(n, "#123456") for n in names])
    for version in ALL_VERSIONS:
        f = tmp_path / f"n_{version}.dxf"
        export_dxf(f, p, s, version=version)                  # (it used to stop with DXFValueError)
        got = {layer.dxf.name for layer in ezdxf.readfile(str(f)).layers}
        assert len(got) >= len(names)                          # every layer has a name of its own
        assert not any(set('<>/\\":;?*|=') & set(n) for n in got)
        p2, s2, _ = make_project([])
        assert len(import_dxf(f, p2, s2)) == len(names)


def test_dxf_export_layer_names_never_merge(tmp_path):
    """"Cut" and "cut" are one layer in AutoCAD, "a/b" and "a:b" would both become "a_b": all stay apart."""
    from iyicam.formats.dxf import layer_names
    p, s, ids = _layered([("Cut", "#ff0000"), ("cut", "#00ff00"), ("a/b", "#0000ff"), ("a:b", "#ffff00"),
                          ("x" * 40, "#000000"), ("x" * 45, "#111111")])
    for version in ALL_VERSIONS:
        names = layer_names(p, ids, version)
        assert len({n.lower() for n in names.values()}) == len(ids)
        if version == "R12":
            assert all(len(n) <= 31 and n == n.upper() and n.isascii() for n in names.values())
        if version in ("R2000", "R2004"):
            assert all(n.isascii() for n in names.values())


def test_dxf_export_keeps_turkish_letters_where_the_version_can_hold_them(tmp_path):
    p, s, _ = _layered([("Kesim ğüşiöçı", "#ff0000")])
    f = tmp_path / "t.dxf"
    export_dxf(f, p, s, version="R2018")
    assert any(layer.dxf.name == "Kesim ğüşiöçı" for layer in ezdxf.readfile(str(f)).layers)
    export_dxf(f, p, s, version="R2000")                       # (these cannot: plain letters, still one layer)
    assert any(layer.dxf.name == "Kesim gusioci" for layer in ezdxf.readfile(str(f)).layers)


def test_dxf_export_selected_objects_only_and_empty(tmp_path):
    p, s, ids = _layered([("A", "#ff0000"), ("B", "#00ff00")])
    f = tmp_path / "sel.dxf"
    export_dxf(f, p, s, [s.vectors[1]])
    doc = ezdxf.readfile(str(f))
    assert {e.dxf.layer for e in doc.modelspace()} == {"B"}
    assert "A" not in doc.layers
    export_dxf(f, p, s, [])                                     # nothing selected is a valid, empty file
    assert len(list(ezdxf.readfile(str(f)).modelspace())) == 0


def test_dxf_export_unknown_version_and_bad_colours(tmp_path):
    p, s, _ = _layered([("A", "not a colour"), ("B", "#12")])
    with pytest.raises(ValueError, match="R2010"):
        export_dxf(tmp_path / "x.dxf", p, s, version="R9999")
    export_dxf(tmp_path / "ok.dxf", p, s)                       # (a colour that cannot be read is left out)
    assert len(list(ezdxf.readfile(str(tmp_path / "ok.dxf")).modelspace())) == 2


def test_dxf_export_default_units_are_millimetres(tmp_path):
    p, s, _ = _layered([("A", "#ff0000")])
    f = tmp_path / "u.dxf"
    export_dxf(f, p, s)
    doc = ezdxf.readfile(str(f))
    assert doc.header["$INSUNITS"] == 4 and doc.header["$MEASUREMENT"] == 1


def test_svg_export_is_valid_xml_whatever_the_layer_is_called(tmp_path):
    import xml.dom.minidom
    p, s, _ = _layered([('Kes "iç" <1> & \'2\'', "#ff0000")])
    f = tmp_path / "q.svg"
    export_svg(f, p, s)
    xml.dom.minidom.parse(str(f))
    p2, s2, _ = make_project([])
    assert len(import_svg(f, p2, s2)) == 1
    assert p2.layer_by_name('Kes "iç" <1> & \'2\'') is not None       # (and the name comes back as it was)

"""ArtCAM models (.art) and projects (.3dp) of ArtCAM 2008 and 2018: sheets, the vectors the toolpaths cut, and the
toolpaths with ArtCAM's settings."""
import base64
import math
import os
from pathlib import Path

import pytest

from artcam_builder import TOOL, art_file, circle_line, compound_file, project_file, rect_line
from iyicam.cam.job import compute_toolpath
from iyicam.core.geom import BBox
from iyicam.formats import artcam
from iyicam.formats.cfb import CompoundFile
from iyicam.formats.report import ImportReport

D = TOOL["diameter"] / 2 + TOOL["allowance"]        # how far out the line of the profile runs: 2.5


def parts():
    """A plate 100 x 50 with a hole of 20 in it, a disc of 60: the lines of an outside profile of them."""
    return [(11, rect_line(50, 50, 100, 50, D)), (12, circle_line(100, 75, 10 - D, ccw=False)),
            (13, circle_line(300, 100, 30 + D))]


def three_sheets():
    return [{"name": "k88881", "contours": parts()},
            {"name": "k88882", "contours": [(20, rect_line(400, 200, 80, 40, D))]},
            {"name": "k88883", "contours": [(30, circle_line(600, 400, 25 + D))]}]


@pytest.fixture(params=[2008, 2018])
def version(request):
    return request.param


def test_a_compound_file_gives_back_its_streams():
    streams = {"A/B/c": b"x" * 5000, "A/d": b"short", "e": bytes(range(256)) * 3}
    cf = CompoundFile(compound_file(streams))
    assert {p for p in cf.paths() if not p.endswith("/")} == set(streams)
    for p, data in streams.items():
        assert cf.read(p) == data


def test_the_settings_of_every_toolpath_are_read(tmp_path, version):
    f = tmp_path / "model.art"
    f.write_bytes(art_file(three_sheets(), size=(1510.0, 3010.0), version=version))
    (m,) = artcam.read_file(f)
    assert (m.width, m.height) == (1510.0, 3010.0)
    assert m.version.startswith(f"ArtCAM {version}")
    assert [t.name for t in m.toolpaths] == ["k88881", "k88882", "k88883"]
    t = m.toolpaths[0]
    assert t.kind == "profile" and not t.problems
    assert len(t.contours) == 3 and all(c.closed for c in t.contours)
    assert [c.vector_id for c in t.contours] == [11, 12, 13]
    assert t.tool["diameter"] == 6.0 and t.tool["number"] == 7 and t.tool["name"] == "End Mill 6 mm"
    assert (t.tool["stepdown"], t.tool["stepover"]) == (4.0, 2.4)
    assert (t.tool["spindle"], t.tool["feed"], t.tool["plunge"]) == (18000.0, 50.0, 20.0)
    assert t.safe_z == 12.0 and t.home == (0.0, 0.0, 40.0)
    assert t.start_depth == pytest.approx(0.5) and t.finish_depth == pytest.approx(9.5)
    assert t.allowance == pytest.approx(-0.5) and t.tolerance == pytest.approx(0.025)
    assert t.z_levels == pytest.approx([-3.5, -6.5, -9.5])
    assert t.values["ProfileOutside"] is True and t.values["ClimbMill"] is True


def test_a_model_becomes_a_sheet_for_each_toolpath_with_its_vectors(tmp_path, version):
    f = tmp_path / "model.art"
    f.write_bytes(art_file(three_sheets(), size=(1510.0, 3010.0), version=version))
    report = ImportReport()
    p = artcam.read_project(f, report)
    assert [s.name for s in p.sheets] == ["k88881", "k88882", "k88883"]
    assert all((s.width, s.height) == (1510.0, 3010.0) for s in p.sheets)
    s = p.sheets[0]
    assert len(s.vectors) == 2                                   # the plate (with its hole) and the disc
    plate = min(s.vectors, key=lambda o: o.bbox().xmin)
    bb = plate.bbox()
    assert (bb.xmin, bb.ymin, bb.xmax, bb.ymax) == pytest.approx((50, 50, 150, 100), abs=0.01)   # sharp corners again
    assert len(plate.contours) == 2
    hole = min(plate.contours, key=lambda c: c.bbox().width)
    assert hole.bbox().width == pytest.approx(20.0, abs=0.01)
    disc = max(s.vectors, key=lambda o: o.bbox().xmin)
    assert disc.bbox().width == pytest.approx(60.0, abs=0.01)
    assert "encrypted" in report.summary() and "relief" in report.summary()
    assert report.read["ArtCAM profile"] == 3


def test_the_toolpaths_have_artcams_settings(tmp_path, version):
    f = tmp_path / "model.art"
    f.write_bytes(art_file(three_sheets(), version=version))
    p = artcam.read_project(f)
    assert [t.name for t in p.toolpaths] == ["k88881", "k88882", "k88883"]
    for t, s in zip(p.toolpaths, p.sheets):
        assert t.op == "profile" and t.sheet_ids == [s.id]
        assert set(t.selection.vector_ids) == {o.id for o in s.vectors}
        assert t.params["side"] == "outside" and t.params["direction"] == "climb"
        assert t.params["allowance"] == -0.5 and t.params["start_depth"] == 0.5
        assert t.params["through"] is False and t.params["cut_depth"] == 9.5 and t.params["stepdown"] == 4.0
        assert t.tool.diameter == 6.0 and t.tool.number == 7 and t.tool.type == "end_mill"
        assert (t.tool.feed_rate, t.tool.plunge_rate, t.tool.spindle_speed) == (3000.0, 1200.0, 18000)  # mm/sec x 60
        assert t.safe_z == 12.0
        assert s.thickness == 9.5                               # (ArtCAM keeps no thickness: the deepest cut)
    assert p.material.safe_z == 12.0 and p.material.home == (0.0, 0.0, 40.0) and p.material.z_zero == "top"


def test_iyicam_cuts_where_artcam_cut(tmp_path, version):
    f = tmp_path / "model.art"
    f.write_bytes(art_file(three_sheets(), version=version))
    p = artcam.read_project(f)
    t, s = p.toolpaths[0], p.sheets[0]
    r = compute_toolpath(p, t, s)
    assert not r.error
    zs = sorted({round(m.z, 4) for tp in r.toolpaths for m in tp.moves if hasattr(m, "f")})
    assert {-3.5, -6.5, -9.5} <= set(zs) and min(zs) == -9.5     # the passes of ArtCAM: 0.5 -> 9.5, at most 4 each
    bottom = [(m.x, m.y) for tp in r.toolpaths for m in tp.moves if hasattr(m, "f") and abs(m.z + 9.5) < 1e-6]

    def from_parts(q):                     # how far a point is from the plate, its hole and the disc (outside them)
        dx, dy = max(50 - q[0], 0, q[0] - 150), max(50 - q[1], 0, q[1] - 100)
        return min(math.hypot(dx, dy) if (dx or dy) else 1e9, abs(math.dist(q, (100, 75)) - 10),
                   abs(math.dist(q, (300, 100)) - 30))
    assert len(bottom) > 8 and all(from_parts(q) == pytest.approx(D, abs=0.02) for q in bottom)
    assert len(r.toolpaths[0].cuts) == 3


def test_toolpaths_that_cut_the_same_vectors_share_a_sheet(tmp_path):
    tps = [{"name": "rough", "contours": [(5, rect_line(10, 10, 50, 50, D))]},
           {"name": "other", "contours": [(9, rect_line(200, 10, 50, 50, D))]},
           {"name": "finish", "contours": [(5, rect_line(10, 10, 50, 50, 3 + 0.0))], "allowance": 0.0}]
    f = tmp_path / "m.art"
    f.write_bytes(art_file(tps))
    p = artcam.read_project(f)
    assert [s.name for s in p.sheets] == ["rough, finish", "other"]
    assert [len(s.vectors) for s in p.sheets] == [1, 1]          # (one vector for both)
    rough, other, finish = p.toolpaths
    assert rough.selection.vector_ids == finish.selection.vector_ids == [p.sheets[0].vectors[0].id]
    assert [t.sheet_ids for t in p.toolpaths] == [[p.sheets[0].id], [p.sheets[1].id], [p.sheets[0].id]]


def test_a_project_of_two_models(tmp_path, version):
    f = tmp_path / "p.3dp"
    f.write_bytes(project_file([("left", three_sheets()[:1], (1000.0, 500.0)),
                                ("right", three_sheets()[1:2], (2000.0, 1000.0))], version=version))
    p = artcam.read_project(f)
    assert [(s.name, s.width, s.height) for s in p.sheets] == [("left: k88881", 1000.0, 500.0),
                                                              ("right: k88882", 2000.0, 1000.0)]
    assert [t.name for t in p.toolpaths] == ["k88881", "k88882"]


def test_opening_one_in_the_engine_makes_a_project_of_it(tmp_path):
    from iyicam.service import ops
    data = project_file([("tabela", three_sheets(), (1510.0, 3010.0))])
    r = ops.op_import({"name": "tabela.3dp", "data_b64": base64.b64encode(data).decode()})
    p = r["project"]
    assert [s["name"] for s in p["sheets"]] == ["k88881", "k88882", "k88883"]
    assert [t["name"] for t in p["toolpaths"]] == ["k88881", "k88882", "k88883"]
    assert len(r["added"]) == 4 and "encrypted" in r["report"]["summary"]
    schema = ops.op_schema({})
    assert any("art" in i["extensions"] and "3dp" in i["extensions"] for i in schema["importers"])


def test_importing_one_into_an_open_project_adds_its_sheets(tmp_path):
    from conftest import make_project
    from iyicam.core import shapes
    p, s, _ = make_project([shapes.rectangle(0, 0, 10, 10)])
    f = tmp_path / "m.art"
    f.write_bytes(art_file(three_sheets()[:2]))
    objs = artcam.import_artcam(f, p, s)
    assert [x.name for x in p.sheets] == [s.name, "k88881", "k88882"] and len(s.vectors) == 1
    assert len(objs) == 3 and len(p.toolpaths) == 2


def test_what_is_not_an_artcam_file_is_said(tmp_path):
    f = tmp_path / "x.art"
    f.write_bytes(b"not at all" * 100)
    with pytest.raises(artcam.ArtCamError, match="not an ArtCAM file"):
        artcam.read_file(f)
    f.write_bytes(compound_file({"Something/else": b"1234"}))
    with pytest.raises(artcam.ArtCamError, match="no ArtCAM model"):
        artcam.read_file(f)


def test_a_toolpath_whose_line_cannot_be_read_is_said(tmp_path):
    from artcam_builder import model_streams
    s = model_streams(three_sheets()[:2])
    key = [k for k in s if k.endswith("/Data2")][1]
    s[key] = s[key][:200]                                        # (cut short)
    f = tmp_path / "m.art"
    f.write_bytes(compound_file(s))
    report = ImportReport()
    p = artcam.read_project(f, report)
    assert [t.name for t in p.toolpaths] == ["k88881"]
    assert any("k88882" in e for e in report.errors)


def test_the_desktop_opens_one_as_a_project_of_its_own(tmp_path, monkeypatch):
    from PySide6.QtWidgets import QApplication, QMessageBox
    from iyicam.gui.app import create_window
    said = []
    monkeypatch.setattr(QMessageBox, "information", lambda *a, **k: said.append(a[2]))
    monkeypatch.setattr(QMessageBox, "warning", lambda *a, **k: said.append(a[2]))
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.Discard)
    app, w = create_window(["test"], load_plugins=False)
    w.ctrl.settings.auto_recalculate = False
    try:
        f = tmp_path / "tabela.art"
        f.write_bytes(art_file(three_sheets()))
        w.open_project(str(f))
        QApplication.processEvents()
        c = w.ctrl
        assert [s.name for s in c.project.sheets] == ["k88881", "k88882", "k88883"]
        assert len(c.project.toolpaths) == 3 and c.project.path is None and c.dirty
        assert "3 toolpath(s)" in w.statusBar().currentMessage()
        assert said and "encrypted" in said[0]
    finally:
        for d in w.documents():
            d.dirty = False
        w.close()


SAMPLES = os.environ.get("IYICAM_ARTCAM_SAMPLES", "")


@pytest.mark.skipif(not SAMPLES or not Path(SAMPLES).is_dir(), reason="IYICAM_ARTCAM_SAMPLES: a folder of real ArtCAM files")
def test_real_files():
    """Real files (a folder given in IYICAM_ARTCAM_SAMPLES): every one opens, every toolpath is read, and iyiCAM's
    profile on the vectors runs on ArtCAM's line."""
    files = sorted(p for p in Path(SAMPLES).iterdir() if p.suffix.lower() in (".art", ".3dp"))
    assert files
    for f in files:
        models = artcam.read_file(f)
        report = ImportReport()
        p = artcam.read_project(f, report)
        for m in models:
            assert all(not t.problems for t in m.toolpaths), f
        assert p.toolpaths and not report.errors, f
        art = {t.name: t for m in models for t in m.toolpaths}
        for t in p.toolpaths:
            s = next(x for x in p.sheets if x.id == t.sheet_ids[0])
            r = compute_toolpath(p, t, s)
            assert not r.error
            assert sum(len(tp.cuts) for tp in r.toolpaths) == len(art[t.name].contours), (f, t.name)

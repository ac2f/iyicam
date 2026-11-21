"""A real CorelDRAW 2026 drawing (version 29, 'T') and the DXF CorelDRAW exported of it: the built-in reader gives the same
outlines, the drawing (beside a small A4 page) lands on the sheet, and File > Open makes a project of it."""
from pathlib import Path

import pytest

from iyicam.core.document import Project
from iyicam.core.geom import BBox, polygon_area
from iyicam.formats import cdr, cdr_native
from iyicam.formats.dxf import import_dxf
from iyicam.formats.report import ImportReport

DATA = Path(__file__).parent / "data"
CDR, DXF = DATA / "cdr2026-test.cdr", DATA / "cdr2026-test.dxf"


def box(objs):
    bb = BBox()
    for o in objs:
        bb = bb.union(o.bbox())
    return bb


def area(objs):
    return sum(abs(polygon_area(c.flatten(0.05))) for o in objs for c in o.contours if c.closed)


def test_the_version_is_2026_and_the_builtin_reader_is_checked_against_it():
    assert cdr.sniff(CDR) == {"container": "zip", "version": 29}
    assert cdr.version_name(29) == "2026" and cdr_native.version_name(2900) == "2026"
    d = cdr_native.read(CDR)
    assert d.version == 2900 and not d.unverified and len(d.page.shapes) == 114
    assert cdr._plan(2900, None)[0].name == "builtin"           # (checked: it goes first)


def test_the_drawing_is_the_one_corel_exported_as_dxf_and_lands_on_the_sheet():
    p = Project("t")
    rep = ImportReport()
    objs = cdr.import_cdr(CDR, p, p.sheets[0], report=rep, converter="builtin")
    q = Project("d")
    ref = import_dxf(DXF, q, q.sheets[0])
    assert len(objs) == len(ref) == 114
    a, b = box(objs), box(ref)
    assert abs(a.width - b.width) < 0.2 and abs(a.height - b.height) < 0.2 and abs(a.width - 1300) < 0.5
    assert abs(area(objs) - area(ref)) < 1e-3 * area(ref)
    # the page is an A4 beside the drawing: the drawing is moved onto the sheet from its own lower left corner
    assert rep.page is not None and abs(rep.page[0] - 210) < 0.01 and abs(rep.page[1] - 297) < 0.01
    assert abs(a.xmin) < 1e-6 and abs(a.ymin) < 1e-6
    assert rep.via == "CorelDRAW 2026 (builtin)" and not rep.notes and not rep.summary()   # (nothing to warn about)


def test_file_open_makes_a_project_of_the_drawing(tmp_path, monkeypatch):
    pytest.importorskip("PySide6.QtWidgets")
    from gui_robot import rwin_gen
    gen = rwin_gen(tmp_path, monkeypatch)
    win, robot = next(gen)
    try:
        monkeypatch.setenv("PATH", str(tmp_path))          # (no LibreOffice: the built-in reader, as on most PCs)
        n = win.tabs.count()
        win.open_project(str(CDR))
        c = win.ctrl
        assert win.tabs.count() == n and c.project.name == "cdr2026-test" and c.project.path is None
        s = c.sheet
        assert len(s.vectors) == 114 and abs(s.width - 1300) <= 1 and abs(s.height - 1980) <= 1
        bb = box(s.vectors)
        assert bb.xmin >= -1e-6 and bb.ymin >= -1e-6 and bb.xmax <= s.width + 1e-6 and bb.ymax <= s.height + 1e-6
        assert not c.dirty and not c.undo_stack.can_undo and robot.problems() == []
        win.open_project(str(DXF))                          # (a second drawing: a tab of its own)
        assert win.tabs.count() == n + 1 and win.ctrl.project.name == "cdr2026-test" and len(win.ctrl.sheet.vectors) == 114
    finally:
        gen.close()

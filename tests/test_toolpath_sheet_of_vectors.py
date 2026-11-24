"""A toolpath is calculated where its vectors are.

A toolpath of chosen vectors kept to one sheet was calculated on that sheet only, also when its vectors were not there (moved
onto a sheet the nesting made, or chosen on another sheet than the one the toolpath window was opened on): it made nothing
and said nothing, and making it again (by vectors or by layers) did the same."""
import pytest

from conftest import add_tp, end_mill, make_project
from iyicam.builtins import ensure_registered
from iyicam.cam.job import compute_all, sheets_for
from iyicam.core import shapes
from iyicam.core.document import Selection, Sheet
from iyicam.core.registry import REGISTRY

ensure_registered(REGISTRY)


def two_sheets():
    p, s1, objs = make_project([shapes.rectangle(10, 10, 100, 60), shapes.rectangle(200, 10, 100, 60)], size=(600, 400))
    s3 = Sheet("1560 x 3050", 600, 400)
    p.sheets.append(s3)
    return p, s1, s3, objs


def moved(p, s1, s3, objs):
    """What a nesting does: the vectors go to another sheet."""
    s1.vectors = [v for v in s1.vectors if v not in objs]
    s3.vectors.extend(objs)


def test_a_toolpath_of_vectors_kept_to_a_sheet_follows_them_to_another_sheet():
    p, s1, s3, objs = two_sheets()
    t = add_tp(p, "profile", end_mill(6), {"through": True}, objs)
    t.sheet_ids = [s1.id]
    moved(p, s1, s3, objs)
    assert sheets_for(p, t) == [s3]
    res = compute_all(p, REGISTRY)
    assert (t.id, s3.id) in res and res[(t.id, s3.id)].toolpaths and res[(t.id, s3.id)].toolpaths[0].moves


def test_a_sheet_kept_that_holds_some_of_the_vectors_is_kept():
    p, s1, s3, objs = two_sheets()
    t = add_tp(p, "profile", end_mill(6), {"through": True}, objs)
    t.sheet_ids = [s1.id]
    s1.vectors.remove(objs[1])
    s3.vectors.append(objs[1])                      # (one vector on each sheet: the toolpath was kept to sheet 1)
    assert sheets_for(p, t) == [s1]


def test_a_toolpath_of_layers_kept_to_a_sheet_without_them_goes_where_they_are():
    p, s1, s3, objs = two_sheets()
    lay = p.ensure_layer("d")
    for o in objs:
        o.layer_id = lay.id
    t = add_tp(p, "profile", end_mill(6), {"through": True}, objs)
    t.selection = Selection("layers", [], [lay.id])
    t.sheet_ids = [s1.id]
    moved(p, s1, s3, objs)
    assert sheets_for(p, t) == [s3]
    t.sheet_ids = None
    assert sheets_for(p, t) == [s1, s3]             # (every sheet: as it was)


def test_a_toolpath_whose_vectors_are_gone_says_so():
    p, s1, s3, objs = two_sheets()
    t = add_tp(p, "profile", end_mill(6), {"through": True}, objs)
    t.sheet_ids = [s1.id]
    s1.vectors = []
    res = compute_all(p, REGISTRY)
    assert any("gone" in w for w in res[(t.id, s1.id)].warnings)


def test_the_window_puts_a_new_toolpath_on_the_sheet_of_the_vectors_chosen():
    pytest.importorskip("PySide6.QtWidgets")
    from PySide6.QtWidgets import QApplication
    from iyicam.core.document import VectorObject
    from iyicam.gui.app import create_window
    from iyicam.gui.dialogs import ToolpathDialog
    app, w = create_window(["test"], load_plugins=False)
    c = w.ctrl
    c.settings.auto_recalculate = False
    s1 = c.sheet
    s3 = c.project.add_sheet("1560 x 3050", 600, 400)
    o = VectorObject([shapes.rectangle(10, 10, 100, 60)], c.project.layers[0].id)
    s3.vectors.append(o)
    c.set_sheet(s1.id)
    dlg = ToolpathDialog(c, "profile", None, w, tool=end_mill(6))       # (opened on sheet 1)
    dlg.all_sheets.setChecked(False)
    dlg.rb_sel.setChecked(True)
    dlg.vector_ids = [o.id]                                             # (the vectors chosen on the other sheet)
    t = dlg.toolpath()
    assert t.sheet_ids == [s3.id]
    dlg.close()
    w.ctrl.dirty = False
    w.close()
    QApplication.processEvents()

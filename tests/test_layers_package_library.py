"""A package of toolpaths put on layers (each toolpath on its own layers, or on those chosen), and the vector library (groups in
groups, drawings kept and put in again)."""
import json

import pytest

from conftest import add_tp, end_mill, make_project
from iyicam.builtins import ensure_registered
from iyicam.cam import tp_packages as P
from iyicam.cam.job import compute_all, sheet_toolpaths
from iyicam.core import shapes
from iyicam.core.document import Selection, VectorObject
from iyicam.core.registry import REGISTRY
from iyicam.core.veclib import ROOT, LibraryError, VectorLibrary

ensure_registered(REGISTRY)


# ------------------------------------------------------------------------------------------------ packages on layers
def layered():
    p, s, objs = make_project([shapes.rectangle(100, 100, 200, 100), shapes.circle((200, 150), 20)], size=(600, 400))
    outer, inner = p.ensure_layer("Dış"), p.ensure_layer("İç")
    objs[0].layer_id, objs[1].layer_id = outer.id, inner.id
    return p, s, objs, outer, inner


def pkg(layers=(["Dış"], ["İç"])):
    return P.check({"name": "Katman", "tool": end_mill(4).to_dict(), "items": [
        {"name": "Dış", "op": "profile", "params": {"side": "outside", "cut_depth": 2.0}, "layers": layers[0]},
        {"name": "İç", "op": "profile", "params": {"side": "inside", "cut_depth": 1.0}, "layers": layers[1]}]})


def test_on_layers_each_toolpath_machines_its_own_layers():
    p, s, objs, outer, inner = layered()
    tps, _ = P.build(pkg(), p, s, Selection("layers", [], []), REGISTRY, make_group=False, this_sheet=False)
    assert [(t.selection.mode, t.selection.layer_ids) for t in tps] == [("layers", [outer.id]), ("layers", [inner.id])]
    p.toolpaths += tps
    res = compute_all(p, REGISTRY)
    a, b = sheet_toolpaths(p, res, s)
    xs = [m.x for m in a.moves if hasattr(m, "x")]
    assert min(xs) == pytest.approx(98, abs=0.01)                          # (the rectangle, outside)
    from iyicam.cam.moves import ArcMove, Feed
    low = min(m.z for m in b.moves if hasattr(m, "z"))
    rs = [((m.x - 200) ** 2 + (m.y - 150) ** 2) ** 0.5 for m in b.moves
          if isinstance(m, (Feed, ArcMove)) and abs(m.z - low) < 1e-6]
    assert rs and all(r == pytest.approx(18, abs=0.01) for r in rs)       # (the circle, inside: 20 - the tool radius)


def test_on_layers_a_toolpath_without_layers_of_its_own_takes_those_chosen_and_a_choice_wins():
    p, s, objs, outer, inner = layered()
    tps, _ = P.build(pkg(([], ["Not there"])), p, s, Selection("layers", [], [outer.id]), REGISTRY,
                     item_layers={1: [inner.id]})
    assert [t.selection.layer_ids for t in tps] == [[outer.id], [inner.id]]
    with pytest.raises(P.PackageError):
        P.build(pkg(([], [])), p, s, Selection("layers", [], []), REGISTRY)     # (no layer at all: said, not made)


def test_on_selected_vectors_the_layers_of_the_package_do_not_count():
    p, s, objs, outer, inner = layered()
    tps, _ = P.build(pkg(), p, s, Selection("vectors", [objs[0].id], []), REGISTRY)
    assert all(t.selection.mode == "vectors" and t.selection.vector_ids == [objs[0].id] for t in tps)


def test_a_package_made_of_toolpaths_on_layers_keeps_their_layer_names():
    p, s, objs, outer, inner = layered()
    t = add_tp(p, "profile", end_mill(4), {"side": "outside"}, objs)
    t.selection = Selection("layers", [], [outer.id])
    made = P.from_toolpaths("X", [t], REGISTRY, p)
    assert made["items"][0]["layers"] == ["Dış"]
    assert P.check(json.loads(json.dumps(made)))["items"][0]["layers"] == ["Dış"]


# ------------------------------------------------------------------------------------------------ the vector library
def test_groups_in_groups_moved_and_ordered(tmp_path):
    lib = VectorLibrary(path=tmp_path / "lib.json")
    logos = lib.add_group("Logolar")
    a = lib.add_group("Müşteri A", logos["id"])
    b = lib.add_group("Müşteri B", logos["id"])
    parts = lib.add_group("Parçalar")
    lib.shift(b["id"], -1)
    assert [g["name"] for g in lib.find(logos["id"])[0]["groups"]] == ["Müşteri B", "Müşteri A"]
    lib.move(parts["id"], a["id"])
    assert lib.path_of(parts["id"]) == ["Logolar", "Müşteri A"]
    with pytest.raises(LibraryError):
        lib.move(logos["id"], parts["id"])                                 # (a group cannot go into itself)
    lib.rename(parts["id"], "Parts")
    lib.remove(a["id"])
    assert lib.find(parts["id"])[0] is None and [g["name"] for g in lib.root["groups"]] == ["Logolar"]


def test_a_drawing_is_kept_at_zero_and_put_in_again_with_new_ids(tmp_path):
    p, s, objs = make_project([shapes.rectangle(100, 50, 40, 20), shapes.circle((200, 80), 10)])
    lib = VectorLibrary(path=tmp_path / "lib.json")
    g = lib.add_group("Etiketler")
    it = lib.add_item("Etiket", objs, p, g["id"])
    assert it["size"] == [110.0, 40.0]
    lib.save()
    back = VectorLibrary.load(tmp_path / "lib.json")
    item = back.items()[0]
    vs = back.vectors_of(item, "L", (10.0, 20.0))
    assert [(round(v.bbox().xmin, 3), round(v.bbox().ymin, 3)) for v in vs] == [(10.0, 20.0), (100.0, 40.0)]
    assert {v.id for v in vs}.isdisjoint({o.id for o in objs}) and all(v.layer_id == "L" for v in vs)


def test_a_drawing_can_go_back_on_the_layers_it_came_from(tmp_path):
    p, s, objs = make_project([shapes.rectangle(0, 0, 10, 10)])
    objs[0].layer_id = p.ensure_layer("Gravür").id
    lib = VectorLibrary(path=tmp_path / "lib.json")
    item = lib.add_item("X", objs, p)
    q, _, _ = make_project([shapes.rectangle(0, 0, 1, 1)])
    vs = lib.vectors_of(item, q.layers[0].id, project=q, own_layers=True)
    assert q.layer(vs[0].layer_id).name == "Gravür"


def test_a_group_is_exported_and_imported_with_new_ids(tmp_path):
    p, s, objs = make_project([shapes.rectangle(0, 0, 10, 10)])
    lib = VectorLibrary(path=tmp_path / "a.json")
    g = lib.add_group("Paylaş")
    sub = lib.add_group("Alt", g["id"])
    lib.add_item("Kare", objs, p, sub["id"])
    data = json.loads(json.dumps(lib.export_group(g["id"])))
    other = VectorLibrary(path=tmp_path / "b.json")
    got = other.import_data(data)
    assert got["name"] == "Paylaş" and got["groups"][0]["items"][0]["name"] == "Kare"
    assert got["id"] != g["id"]


def test_a_damaged_library_file_is_an_empty_library(tmp_path):
    f = tmp_path / "lib.json"
    f.write_text("{not json", encoding="utf-8")
    assert VectorLibrary.load(f).items() == []


# ------------------------------------------------------------------------------------------------ the desktop
@pytest.fixture
def win():
    pytest.importorskip("PySide6.QtWidgets")
    from PySide6.QtWidgets import QApplication
    from iyicam.gui.app import create_window
    app, w = create_window(["test"], load_plugins=False)
    w.show()
    QApplication.processEvents()
    w.ctrl.settings.auto_recalculate = False
    yield w
    w.ctrl.dirty = False
    w.close()
    QApplication.processEvents()


def test_the_library_panel_keeps_the_selection_and_puts_it_in_again(win, monkeypatch):
    from PySide6.QtWidgets import QInputDialog
    c = win.ctrl
    panel = win.library_panel
    o = VectorObject([shapes.rectangle(0, 0, 30, 20)], c.project.layers[0].id)
    c.add_vectors([o], "R")
    c.select([o.id])
    monkeypatch.setattr(QInputDialog, "getText", staticmethod(lambda *a, **k: ("Logolar", True)))
    panel.new_group()
    monkeypatch.setattr(QInputDialog, "getText", staticmethod(lambda *a, **k: ("Müşteri", True)))
    panel.new_group()                                                      # (in the group selected: a subgroup)
    monkeypatch.setattr(QInputDialog, "getText", staticmethod(lambda *a, **k: ("Kutu", True)))
    panel.add_selection()
    lib = VectorLibrary.load()
    item = lib.items()[0]
    assert item["name"] == "Kutu" and lib.path_of(item["id"]) == ["Logolar", "Müşteri"]
    n = len(c.sheet.vectors)
    made = panel.insert()
    assert len(c.sheet.vectors) == n + 1 and c.selection == [made[0].id]
    bb = made[0].bbox()
    assert (bb.width, bb.height) == (pytest.approx(30), pytest.approx(20))
    c.undo()
    assert len(c.sheet.vectors) == n


def test_dragging_in_the_tree_moves_a_drawing_into_another_group(win, monkeypatch):
    p, s, objs = make_project([shapes.rectangle(0, 0, 10, 10)])
    panel = win.library_panel
    lib = panel.lib
    a = lib.add_group("A")
    b = lib.add_group("B")
    it = lib.add_item("Kare", objs, p, a["id"])
    panel.refresh()
    tree = panel.tree
    ta, tb = tree.topLevelItem(0), tree.topLevelItem(1)
    child = ta.takeChild(0)                                                # (what a drop does to the tree)
    tb.addChild(child)
    panel._sync_from_tree()
    assert [i["name"] for i in lib.find(b["id"])[0]["items"]] == ["Kare"] and lib.find(a["id"])[0]["items"] == []
    assert VectorLibrary.load().find(it["id"])[1]["id"] == b["id"]           # (kept in the file)


def test_the_package_window_without_a_selection_puts_the_package_on_layers(win, monkeypatch):
    from iyicam.gui.packages import PackageApplyDialog
    c = win.ctrl
    outer = c.project.ensure_layer("Dış")
    inner = c.project.ensure_layer("İç")
    c.add_vectors([VectorObject([shapes.rectangle(100, 100, 200, 100)], outer.id),
                   VectorObject([shapes.circle((200, 150), 20)], inner.id)], "R")
    c.select([])
    P.save(pkg())
    dlg = PackageApplyDialog(c, P.load()[0], [], win)
    assert dlg.rb_layers.isChecked() and not dlg.rb_sel.isEnabled()
    tps, _ = dlg.toolpaths()
    assert [t.selection.layer_ids for t in tps] == [[outer.id], [inner.id]]
    dlg.layer_boxes[1][0].setCurrentIndex(dlg.layer_boxes[1][0].findData(outer.id))   # (this time: the inner cut on Dış too)
    assert [t.selection.layer_ids for t in dlg.toolpaths()[0]] == [[outer.id], [outer.id]]
    monkeypatch.setattr(PackageApplyDialog, "exec", lambda self: 1)
    n = len(c.project.toolpaths)
    win.apply_package(P.load()[0]["id"])
    assert len(c.project.toolpaths) == n + 2 and c.project.toolpaths[-1].selection.mode == "layers"

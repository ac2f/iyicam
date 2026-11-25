"""Isolated groups of sheets (core.scope): their own layers, toolpaths, nesting and G-code; nothing of the rest on them."""
import pytest

from conftest import add_tp, end_mill, make_project
from iyicam.cam.job import compute_all, sheets_for, shown_on
from iyicam.core import scope, shapes
from iyicam.core.document import Sheet, VectorObject, project_from_dict
from iyicam.core.registry import REGISTRY


def project():
    """Three sheets: A (a part on the Default layer), B and C (a part each on the layer Cut); a toolpath of every sheet on
    Default, one of the vectors of B and C."""
    p, a, objs = make_project([shapes.rectangle(10, 10, 100, 50)], size=(600, 400))
    a.name = "A"
    cut = p.add_layer("Cut")
    b = p.add_sheet("B", 600, 400)
    c = p.add_sheet("C", 600, 400)
    vb = VectorObject([shapes.rectangle(20, 20, 80, 40)], cut.id)
    vc = VectorObject([shapes.circle((200, 200), 30)], cut.id)
    b.vectors.append(vb)
    c.vectors.append(vc)
    t_all = add_tp(p, "profile", end_mill(6), {"through": True}, [])
    t_all.name = "every sheet"
    t_all.selection.mode, t_all.selection.layer_ids = "layers", [p.layers[0].id, cut.id]
    t_bc = add_tp(p, "profile", end_mill(6), {"through": True}, [vb, vc])
    t_bc.name = "B and C"
    return p, (a, b, c), (t_all, t_bc), (vb, vc), cut


def test_isolating_a_sheet_gives_it_a_group_layers_and_toolpaths_of_its_own():
    p, (a, b, c), (t_all, t_bc), (vb, vc), cut = project()
    name = scope.isolate(p, [b.id])
    assert name == "B" and b.group == "B" and b.isolated and not a.isolated and not c.isolated
    # the layer of its vector was copied to the group: the vector is on the copy, the rest keeps the layer
    lb = p.layer(vb.layer_id)
    assert lb.id != cut.id and lb.name == "Cut" and lb.sheet_group == "B"
    assert cut.applies(a) and cut.applies(c) and not cut.applies(b) and lb.applies(b) and not lb.applies(c)
    assert [l.name for l in p.layers_for(b)] == ["Cut"]                        # (nothing of the rest on it)
    # the toolpaths that were calculated on it come along: copied (they were on other sheets too), each with its vectors
    assert all(b.id not in [s.id for s in sheets_for(p, t)] for t in (t_all, t_bc))
    mine = [t for t in p.toolpaths if t.sheet_group == "B"]
    assert sorted(t.name for t in mine) == ["B and C", "every sheet"]
    copy_bc = next(t for t in mine if t.name == "B and C")
    assert copy_bc.selection.vector_ids == [vb.id] and t_bc.selection.vector_ids == [vc.id]
    copy_all = next(t for t in mine if t.name == "every sheet")
    assert lb.id in copy_all.selection.layer_ids                               # (its layers: the group's copies)
    assert [s.name for s in sheets_for(p, copy_bc)] == ["B"] and [s.name for s in sheets_for(p, t_all)] == ["A", "C"]


def test_a_toolpath_of_the_isolated_sheets_only_moves_with_them():
    p, (a, b, c), (t_all, t_bc), (vb, vc), cut = project()
    n = len(p.toolpaths)
    scope.set_group(p, [b.id, c.id], "BC")
    name = scope.isolate(p, [b.id])                    # (a sheet of a group: the whole group is isolated)
    assert name == "BC" and b.isolated and c.isolated
    assert t_bc.sheet_group == "BC" and len(p.toolpaths) == n + 1              # (B and C moved, every sheet copied)
    assert shown_on(p, t_bc, b) and not shown_on(p, t_bc, a)


def test_everything_is_calculated_on_its_own_side():
    p, (a, b, c), (t_all, t_bc), (vb, vc), cut = project()
    scope.isolate(p, [b.id])
    res = compute_all(p, REGISTRY)
    on = {(p.toolpath(tid).name, p.sheet(sid).name, p.toolpath(tid).sheet_group) for (tid, sid), r in res.items() if r.toolpaths}
    assert ("every sheet", "B", "") not in on and ("every sheet", "B", "B") in on
    assert ("every sheet", "A", "") in on and ("every sheet", "C", "") in on
    assert all(grp == "B" for name, sh, grp in on if sh == "B")


def test_ending_the_isolation_keeps_what_is_the_groups():
    p, (a, b, c), (t_all, t_bc), (vb, vc), cut = project()
    scope.isolate(p, [b.id])
    scope.unisolate(p, "B")
    assert not b.isolated and b.group == "B"
    assert b.id in [s.id for s in sheets_for(p, t_all)]                        # (the rest's are on it again)
    assert any(t.sheet_group == "B" for t in p.toolpaths)                     # (and its own stay)


def test_a_sheet_put_in_or_taken_out_of_an_isolated_group_keeps_its_vectors_visible():
    p, (a, b, c), (t_all, t_bc), (vb, vc), cut = project()
    scope.isolate(p, [b.id])
    scope.set_group(p, [c.id], "B")                    # (into the isolated group: isolated with it)
    assert c.isolated and p.layer(vc.layer_id).applies(c) and p.layer(vc.layer_id).sheet_group == "B"
    assert p.layer(vc.layer_id).id == p.layer(vb.layer_id).id                 # (the group's Cut, not another copy)
    scope.set_group(p, [c.id], "")                     # (out of it: part of the rest again)
    assert not c.isolated and p.layer(vc.layer_id).id == cut.id


def test_renaming_a_group_takes_its_layers_and_toolpaths_along():
    p, (a, b, c), _t, (vb, vc), cut = project()
    scope.isolate(p, [b.id])
    scope.rename_group(p, "B", "Doors")
    assert b.group == "Doors" and p.layer(vb.layer_id).sheet_group == "Doors"
    assert {t.sheet_group for t in p.toolpaths} == {"", "Doors"}


def test_the_project_file_keeps_the_isolation_and_the_groups_of_layers():
    p, (a, b, c), _t, (vb, vc), cut = project()
    scope.isolate(p, [b.id])
    cut.group = "Cutting"
    q = project_from_dict(p.to_dict())
    assert q.sheet(b.id).isolated and not q.sheet(a.id).isolated
    assert q.layer(cut.id).group == "Cutting"


def test_a_sheet_made_from_one_of_an_isolated_group_is_in_it():
    p, (a, b, c), _t, _v, _cut = project()
    scope.isolate(p, [b.id])
    s = p.add_sheet(like=b)
    assert s.group == "B" and s.isolated
    assert not p.add_sheet(like=a).isolated


def test_sides():
    p, (a, b, c), (t_all, t_bc), _v, _cut = project()
    scope.isolate(p, [b.id])
    assert [s.name for s in scope.sheets_with(p, a)] == ["A", "C"] and [s.name for s in scope.sheets_with(p, b)] == ["B"]
    assert scope.groups_of_scope(p, "B") == ["B"] and scope.groups_of_scope(p, "") == []
    assert scope.toolpath_in_scope(p, t_all, "") and not scope.toolpath_in_scope(p, t_all, "B")


# -- the engine ------------------------------------------------------------------------------------------------------

def test_the_engine_isolates_and_arranges(tmp_path):
    from iyicam.service import ops
    p, (a, b, c), _t, (vb, vc), cut = project()
    r = ops.op_sheet_scope({"project": p.to_dict(), "action": "isolate", "sheets": [b.id]})
    q = project_from_dict(r["project"])
    assert r["group"] == "B" and q.sheet(b.id).isolated
    r = ops.op_sheet_scope({"project": r["project"], "action": "arrange", "order": [c.id, b.id, a.id],
                            "groups": {c.id: "B", b.id: "B", a.id: ""}})
    q = project_from_dict(r["project"])
    assert [s.name for s in q.sheets] == ["C", "B", "A"] and q.sheet(c.id).isolated
    r = ops.op_sheet_scope({"project": r["project"], "action": "rename", "old": "B", "name": "Doors"})
    assert {s["group"] for s in r["project"]["sheets"] if s.get("group")} == {"Doors"}
    r = ops.op_sheet_scope({"project": r["project"], "action": "unisolate", "name": "Doors"})
    assert not any(s.get("isolated") for s in r["project"]["sheets"])
    with pytest.raises(Exception):
        ops.op_sheet_scope({"project": r["project"], "action": "nonsense"})


def test_the_engine_nests_an_isolated_group_on_its_own():
    from iyicam.service import ops
    p, (a, b, c), _t, (vb, vc), cut = project()
    for k in range(6):
        b.vectors.append(VectorObject([shapes.rectangle(0, 0, 250, 150)], vb.layer_id))
    scope.isolate(p, [b.id])
    r = ops.op_nest({"project": p.to_dict(), "sheet": b.id, "settings": {"place_on": "all", "spacing": 5, "margin": 5,
                                                                           "iterations": 1, "time_limit": 5}})
    q = project_from_dict(r["project"])
    assert r["new_sheets"] and r["new_sheets_group"] == "B"
    for sid in r["new_sheets"]:
        assert q.sheet(sid).group == "B" and q.sheet(sid).isolated
    assert len(q.sheet(a.id).vectors) == 1 and len(q.sheet(c.id).vectors) == 1    # (the rest untouched)


def test_after_nesting_the_new_sheets_go_into_the_group_of_the_sheet_or_the_one_chosen():
    from iyicam.service import ops
    p, (a, b, c), _t, _v, _cut = project()
    for k in range(12):
        a.vectors.append(VectorObject([shapes.rectangle(0, 0, 250, 150)], p.layers[0].id))
    scope.set_group(p, [a.id], "Doors")
    base = {"spacing": 5, "margin": 5, "iterations": 1, "time_limit": 5, "width": 600, "height": 400}
    r = ops.op_nest({"project": p.to_dict(), "sheet": a.id, "settings": dict(base), "objects": [v.id for v in a.vectors]})
    q = project_from_dict(r["project"])
    assert r["new_sheets"] and all(q.sheet(s).group == "Doors" for s in r["new_sheets"])    # (the default)
    r = ops.op_nest({"project": p.to_dict(), "sheet": a.id, "settings": {**base, "new_sheets_group": "Extra"},
                     "objects": [v.id for v in a.vectors]})
    q = project_from_dict(r["project"])
    assert all(q.sheet(s).group == "Extra" for s in r["new_sheets"])
    r2 = ops.op_sheet_scope({"project": r["project"], "action": "new_sheets", "sheets": r["new_sheets"], "sheet": a.id,
                             "name": ""})
    q = project_from_dict(r2["project"])
    assert all(not q.sheet(s).group for s in r["new_sheets"])


def test_gcode_of_every_sheet_of_a_side():
    from iyicam.service import ops
    p, (a, b, c), _t, _v, _cut = project()
    scope.isolate(p, [b.id])
    post = next(iter(REGISTRY.posts))
    rest = ops.op_gcode({"project": p.to_dict(), "post": post, "file_name": "x",
                         "sheets": [s.id for s in scope.sheets_with(p, a)]})
    grp = ops.op_gcode({"project": p.to_dict(), "post": post, "file_name": "x", "sheets": [b.id]})
    assert len(rest["files"]) >= 2 and grp["files"]
    text = "\n".join(f.get("text", "") for f in grp["files"])
    assert text                                                                # (the group's own toolpaths are written)


# -- the desktop ------------------------------------------------------------------------------------------------------

def test_the_desktop_panels_keep_to_the_side_of_the_sheet_open(tmp_path, monkeypatch):
    pytest.importorskip("PySide6.QtWidgets")
    from gui_robot import rwin_gen
    gen = rwin_gen(tmp_path, monkeypatch)
    win, _ = next(gen)
    try:
        c = win.ctrl
        p, (a, b, cc), (t_all, t_bc), _v, _cut = project()
        c._replace(p)
        c.set_sheet(b.id)
        assert c.isolate([b.id]) == "B"
        from iyicam.gui.panels import LayersPanel
        lp = win.findChild(LayersPanel)
        lp.refresh()
        names = [lp.tree.topLevelItem(i).text(0).split(" (")[0] for i in range(lp.tree.topLevelItemCount())]
        assert names == ["Cut"]
        assert p.layer(c.layer_id).sheet_group == "B"                         # (the current layer is the group's)
        from iyicam.gui.panels import ToolpathsPanel
        tp = win.findChild(ToolpathsPanel)
        tp.refresh()
        listed = [tp.tree.topLevelItem(i).data(0, 0x0100) for i in range(tp.tree.topLevelItemCount())]
        assert listed and all(p.toolpath(x).sheet_group == "B" for x in listed if x)
        c.set_sheet(a.id)
        assert p.layer(c.layer_id).sheet_group == ""
        tp.refresh()
        listed = [tp.tree.topLevelItem(i).data(0, 0x0100) for i in range(tp.tree.topLevelItemCount())]
        assert listed and all(p.toolpath(x).sheet_group == "" for x in listed if x)
    finally:
        gen.close()


def test_dropping_a_sheet_on_a_sheet_makes_a_group(tmp_path, monkeypatch):
    pytest.importorskip("PySide6.QtWidgets")
    from gui_robot import rwin_gen
    from iyicam.gui.panels import SheetsPanel
    gen = rwin_gen(tmp_path, monkeypatch)
    win, _ = next(gen)
    try:
        c = win.ctrl
        p, (a, b, cc), _t, _v, _cut = project()
        c._replace(p)
        sp = win.findChild(SheetsPanel)
        sp.refresh()
        t = sp.tree
        rows = [t.topLevelItem(i) for i in range(t.topLevelItemCount())]
        moved = t.takeTopLevelItem(t.indexOfTopLevelItem(rows[2]))
        rows[1].addChild(moved)                               # (C dropped on B)
        sp._dropped()
        assert b.group == cc.group and b.group.startswith("Group") and not a.group
    finally:
        gen.close()


def test_a_group_is_not_renamed_onto_another_group():
    p, (a, b, c), _t, _v, _cut = project()
    scope.isolate(p, [b.id])
    scope.set_group(p, [a.id], "Doors")
    with pytest.raises(ValueError, match="already"):
        scope.rename_group(p, "B", "Doors")            # (an isolated group and an ordinary one would be one half and half)
    assert b.group == "B" and b.isolated and a.group == "Doors" and not a.isolated

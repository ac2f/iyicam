"""Groups of sheets: toolpaths made for a group only, nesting onto a group; and posts with sums in their blocks."""
import copy

import pytest
from conftest import add_tp, end_mill, make_project

from iyicam.builtins import ensure_registered
from iyicam.cam.job import compute_all, sheet_toolpaths, sheets_for
from iyicam.core import shapes
from iyicam.core.document import Project, project_from_dict, project_to_dict
from iyicam.core.registry import REGISTRY
from iyicam.post import editing
from iyicam.post.export import post_toolpaths
from iyicam.service import ServiceHost


def two_groups():
    p, s, objs = make_project([[shapes.rectangle(10, 10, 100, 60)]], size=(500, 300))
    p.add_sheet("B", 400, 300)
    p.add_sheet("C", 400, 300)
    p.sheets[0].group, p.sheets[1].group = "A", "A"
    p.sheets[2].group = "B"
    for sh in p.sheets[1:]:
        sh.vectors = [copy.deepcopy(v) for v in p.sheets[0].vectors]
        for v in sh.vectors:
            v.id = v.id + sh.id[:3]
    return p


def test_groups_of_sheets_are_kept_in_the_file():
    p = two_groups()
    q = project_from_dict(project_to_dict(p))
    assert [s.group for s in q.sheets] == ["A", "A", "B"]
    assert "group" not in project_to_dict(Project("x"))["sheets"][0]


def test_a_toolpath_for_a_sheet_group_is_calculated_on_its_sheets_only():
    ensure_registered(REGISTRY)
    p = two_groups()
    add_tp(p, "profile", end_mill(6), {"through": True}, [v for sh in p.sheets for v in sh.vectors])
    t = p.toolpaths[0]
    assert len(sheets_for(p, t)) == 3
    t.sheet_group = "B"
    assert [s.name for s in sheets_for(p, t)] == ["C"]
    t.sheet_group = "A"
    res = compute_all(p, REGISTRY)
    assert {sid for (_, sid) in res} == {p.sheets[0].id, p.sheets[1].id}
    assert not sheet_toolpaths(p, res, p.sheets[2])
    t.sheet_group = "nobody"
    assert sheets_for(p, t) == []
    t.sheet_ids = [p.sheets[2].id]                      # (sheets that are listed win)
    assert [s.name for s in sheets_for(p, t)] == ["C"]


def test_the_engine_nests_onto_the_sheets_of_a_group():
    p = two_groups()
    for sh in p.sheets:
        sh.vectors = []
    loose = Project("n")
    loose.sheets = p.sheets
    from iyicam.core.document import VectorObject
    loose.sheets[2].vectors = [VectorObject([shapes.rectangle(-900 + i * 120, -900, 100, 60)], loose.layers[0].id, f"n{i}")
                               for i in range(4)]
    h = ServiceHost()
    r = h.handle("nest", {"project": loose.to_dict(), "settings": {"place_on": "group", "group": "A", "no_new_sheets": True}})
    assert "error" not in r, r
    q = Project()
    q.load_state(r["project"])
    assert [len(s.vectors) for s in q.sheets] == [4, 0, 0] or sum(len(s.vectors) for s in q.sheets[:2]) == 4
    assert h.handle("nest", {"project": loose.to_dict(), "settings": {"place_on": "group", "group": "zz"}})["error"] == "unknown_group"


def test_the_controller_groups_sheets_and_the_dialogs_offer_them(tmp_path, monkeypatch):
    pytest.importorskip("PySide6.QtWidgets")
    from gui_robot import rwin_gen
    from test_cutedit import parts
    from iyicam.gui.dialogs import NestingDialog, ToolpathDialog
    gen = rwin_gen(tmp_path, monkeypatch)
    win, _ = next(gen)
    try:
        c, t, objs = parts(win)
        with c.edit("add", geometry=False):
            c.project.add_sheet("S2")
        assert c.set_sheet_group([c.project.sheets[0].id, c.project.sheets[1].id], "Doors") == 2
        assert c.sheet_groups() == ["Doors"] and len(c.sheets_in("Doors")) == 2
        dlg = ToolpathDialog(c, "profile", None, win, sheet_group="Doors")
        assert dlg.group_box.currentData() == "Doors" and not dlg.all_sheets.isEnabled()
        made = dlg.toolpath()
        assert made.sheet_group == "Doors" and made.sheet_ids is None
        nd = NestingDialog(c, win)
        assert nd.form.widgets["target"].findData("group:Doors") >= 0
        assert c.set_sheet_group([c.project.sheets[0].id], "") == 1
        dlg.close()
        nd.close()
    finally:
        gen.close()


# -- dynamic posts -------------------------------------------------------------------------------------------
def post_with(footer):
    from iyicam.core import shapes as sh
    p, s, objs = make_project([[sh.rectangle(10, 10, 100, 60)]], size=(500, 300))
    add_tp(p, "profile", end_mill(6), {"through": True}, objs)
    ensure_registered(REGISTRY)
    res = compute_all(p, REGISTRY)
    d = copy.deepcopy(editing.info(REGISTRY.posts["mach3"]))
    d["id"], d["name"] = "dyn", "dyn"
    d["blocks"]["footer"] = footer
    return post_toolpaths(p, s, sheet_toolpaths(p, res, s), editing.build_post(d), "x")[0][1].splitlines()


def test_a_block_can_have_sums_of_variables():
    out = post_with("G0 Z{safe_z}\nG0 Y{sheet_height + 50}\nG0 X{sheet_width/2 - 0.5}\nG0 X{(home_x+10)*2}\nM30")
    assert "G0 Y350" in out and "G0 X249.5" in out and "G0 X20" in out


def test_a_sum_in_inches_is_in_inches():
    from conftest import make_project as mp
    out = post_with("G0 Y{sheet_height}")
    assert "G0 Y300" in out


def test_a_wrong_sum_is_refused_and_an_unknown_name_is_warned_about():
    with pytest.raises(editing.PostEditError):
        editing.check_blocks({"footer": "G0 Y{sheet_height+}"})
    _, warn = editing.check_blocks({"footer": "G0 Y{sheet_height+foo}"})
    assert warn and "foo" in warn[0]
    _, warn = editing.check_blocks({"footer": "G0 Y{sheet_height+50}"})
    assert not warn


def test_write_gcode_after_calculate_does_not_calculate_again(monkeypatch):
    from iyicam.service import ops
    p, s, objs = make_project([[shapes.rectangle(10, 10, 100, 60)], [shapes.circle((200, 60), 20)]], size=(500, 300))
    add_tp(p, "profile", end_mill(6), {"through": True}, objs[:1])
    add_tp(p, "profile", end_mill(3), {"through": True}, objs[1:])
    h = ServiceHost()
    c = h.handle("compute", {"project": p.to_dict(), "preview": False})
    assert "error" not in c
    q = Project()
    q.load_state(p.to_dict())
    for vid, vd in c["patch"]["vectors"].items():
        from iyicam.core.document import vector_from_dict
        for sh in q.sheets:
            for i, v in enumerate(sh.vectors):
                if v.id == vid:
                    sh.vectors[i] = vector_from_dict(vd)
    for tid, seq in c["patch"]["sequences"].items():
        q.toolpath(tid).sequence = seq["sequence"] if "sequence" in seq else seq
    calls = []
    real = ops.calculate
    monkeypatch.setattr(ops, "calculate", lambda *a, **k: (calls.append(1), real(*a, **k))[1])
    g = h.handle("gcode", {"project": q.to_dict(), "post": "mach3"})
    assert g["ok"] and g["reused"] is True and not calls
    q.toolpaths[1].enabled = False                      # (switching one off is not a change of the toolpaths)
    g = h.handle("gcode", {"project": q.to_dict(), "post": "mach3"})
    assert g["reused"] is True and not calls
    q.toolpaths[0].params["overcut"] = 0.9              # (a change is)
    g = h.handle("gcode", {"project": q.to_dict(), "post": "mach3"})
    assert g["reused"] is False and calls
    again = h.handle("gcode", {"project": q.to_dict(), "post": "mach3", "recalculate": True})
    assert again["reused"] is False

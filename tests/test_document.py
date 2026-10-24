import json

import pytest
from conftest import add_tp, end_mill, make_project, vbit

from iyicam.core import shapes
from iyicam.core.commands import CommandContext, CommandError, run_command
from iyicam.core.document import Project
from iyicam.core.geom import Arc, Bezier
from iyicam.core.params import Param, is_visible, resolve
from iyicam.core.registry import REGISTRY
from iyicam.core.tooling import ToolLibrary
from iyicam.core.undo import UndoStack


def test_project_roundtrip(tmp_path):
    p, s, objs = make_project([[shapes.circle((10, 10), 5), shapes.rectangle(0, 0, 30, 30)],
                               shapes.ellipse((50, 50), 10, 5)])
    lay = p.add_layer("Engrave", "#00ff00")
    objs[1].layer_id = lay.id
    p.add_sheet("Second", 1000, 500).thickness = 12
    add_tp(p, "vcarve", vbit(), {"flat_depth": 2, "clear_tool": end_mill(3)}, objs)
    f = tmp_path / "x.icam"
    p.save(f)
    q = Project.load(f)
    assert q.path == f
    assert [l.name for l in q.layers] == ["Default", "Engrave"]
    assert len(q.sheets) == 2 and q.sheets[1].thickness == 12
    v = q.sheets[0].vectors
    assert isinstance(v[0].contours[0].segs[0], Arc) and isinstance(v[1].contours[0].segs[0], Bezier)
    assert v[0].contours[0].area() == pytest.approx(objs[0].contours[0].area())
    t = q.toolpaths[0]
    assert t.op == "vcarve" and t.params["clear_tool"].diameter == 3
    assert json.loads(f.read_text())["format"] == "iyicam-project"


def test_load_rejects_other_json(tmp_path):
    f = tmp_path / "x.json"
    f.write_text("{}")
    with pytest.raises(ValueError):
        Project.load(f)


def test_undo_redo():
    p, s, objs = make_project([shapes.rectangle(0, 0, 10, 10)])
    u = UndoStack()
    u.checkpoint(p, "add circle")
    p.sheets[0].vectors.append(make_project([shapes.circle((0, 0), 1)])[2][0])
    assert len(p.sheets[0].vectors) == 2
    assert u.undo(p) and len(p.sheets[0].vectors) == 1
    assert u.redo(p) and len(p.sheets[0].vectors) == 2
    assert not u.can_redo and u.undo_label == "add circle"


def test_remove_layer_moves_or_deletes():
    p, s, objs = make_project([shapes.rectangle(0, 0, 10, 10), shapes.rectangle(20, 0, 10, 10)])
    l2 = p.add_layer("Two")
    objs[1].layer_id = l2.id
    p.remove_layer(l2.id, move_to=p.layers[0].id)
    assert len(s.vectors) == 2 and all(v.layer_id == p.layers[0].id for v in s.vectors)
    p.remove_layer(p.layers[0].id)
    assert not s.vectors and p.layers  # a default layer always exists


def test_layer_selection_for_toolpaths():
    p, s, objs = make_project([shapes.rectangle(0, 0, 10, 10), shapes.rectangle(20, 0, 10, 10)])
    cut = p.add_layer("Cut")
    objs[1].layer_id = cut.id
    t = add_tp(p, "profile", end_mill(), {}, [])
    t.selection.mode = "layers"
    t.selection.layer_ids = [cut.id]
    assert p.vectors_for(t, s) == [objs[1]]


def test_tool_library_persistence(tmp_path):
    lib = ToolLibrary()
    assert lib.groups()[0] == "End Mills" and lib.find("V-Bit 90°")
    lib.tools[0].feed_rate = 1234
    f = tmp_path / "tools.json"
    lib.save(f)
    assert ToolLibrary.load(f).tools[0].feed_rate == 1234
    f.write_text("garbage")
    assert ToolLibrary.load_or_default(f).tools  # broken file falls back to defaults


def test_params_resolve_and_visibility():
    ps = [Param("a", "A", "int", 3, min=1, max=5), Param("b", "B", "choice", "x", choices=[("x", "X"), ("y", "Y")]),
          Param("c", "C", "length", 1.0, visible={"b": "y"})]
    v = resolve(ps, {"a": "9", "b": "nope", "extra": 1})
    assert v == {"a": 5, "b": "x", "c": 1.0, "extra": 1}
    assert not is_visible(ps[2], v) and is_visible(ps[2], {"b": "y"})


def run(cmd, p, s, sel=(), **values):
    ctx = CommandContext(p, s, [o.id for o in sel])
    run_command(REGISTRY.commands[cmd], ctx, values)
    return ctx


def test_creation_commands_default_to_sheet_centre():
    p, s, _ = make_project([])
    run("create.rectangle", p, s, width=100, height=50, radius=5)
    run("create.circle", p, s, diameter=20, x=10, y=10)
    run("create.polygon", p, s, sides=8)
    run("create.star", p, s)
    run("create.ellipse", p, s)
    run("create.slot", p, s)
    run("create.arc", p, s)
    run("create.spiral", p, s)
    assert s.vectors[0].bbox().center == pytest.approx((300, 200))
    assert s.vectors[1].bbox().center == pytest.approx((10, 10))
    assert len(s.vectors) == 8


def test_editing_commands():
    p, s, objs = make_project([shapes.rectangle(0, 0, 20, 20), shapes.rectangle(10, 10, 20, 20)])
    run("edit.weld", p, s, objs)
    assert len(s.vectors) == 1 and s.vectors[0].contours[0].area() == pytest.approx(700)
    w = s.vectors[0]
    run("edit.offset", p, s, [w], distance=2, direction="outward", keep=False)
    assert len(s.vectors) == 1 and s.vectors[0].contours[0].area() > 700
    run("corner.fillet", p, s, s.vectors, radius=1)
    with pytest.raises(CommandError):
        run("edit.weld", p, s, s.vectors[:1])


def test_transform_and_array_commands():
    p, s, objs = make_project([shapes.rectangle(0, 0, 10, 10)])
    run("xform.move", p, s, objs, dx=5, dy=-5)
    assert objs[0].bbox().xmin == pytest.approx(5)
    run("xform.rotate", p, s, objs, angle=90)
    run("xform.scale", p, s, objs, sx=200, uniform=True)
    assert objs[0].bbox().width == pytest.approx(20)
    run("xform.size", p, s, objs, width=50, uniform=True)
    assert objs[0].bbox().height == pytest.approx(50)
    run("xform.mirror", p, s, objs, axis="horizontal", copy=True)
    assert len(s.vectors) == 2
    run("arrange.array", p, s, objs, rows=2, cols=3, spacing="gap", dx=5, dy=5)
    assert len(s.vectors) == 7
    run("arrange.circular", p, s, [s.vectors[0]], count=4, about="sheet")
    assert len(s.vectors) == 10
    run("arrange.align", p, s, s.vectors, mode="left", relative="sheet", each=True)
    assert all(v.bbox().xmin == pytest.approx(0) for v in s.vectors)
    ctx = run("edit.dedupe", p, s)
    assert ctx.messages


def test_join_explode_group_commands():
    p, s, objs = make_project([shapes.polyline([(0, 0), (10, 0)]), shapes.polyline([(10, 0), (10, 10)]),
                               shapes.polyline([(10, 10), (0, 0)])])
    run("edit.join", p, s, objs, tolerance=0.01)
    assert len(s.vectors) == 1 and s.vectors[0].closed
    run("edit.explode", p, s, s.vectors)
    assert len(s.vectors) == 3
    run("edit.group", p, s, s.vectors)
    assert len(s.vectors) == 1 and len(s.vectors[0].contours) == 3
    run("edit.ungroup", p, s, s.vectors)
    assert len(s.vectors) == 3
    run("select.open", p, s)


def test_copy_along_vector():
    p, s, objs = make_project([shapes.rectangle(0, 0, 4, 2), shapes.line((0, 50), (100, 50))])
    run("arrange.along", p, s, objs, count=5, rotate=True)
    copies = s.vectors[2:]
    assert len(copies) == 5
    xs = sorted(round(o.bbox().center[0], 6) for o in copies)
    assert xs == pytest.approx([0, 25, 50, 75, 100])
    circle = shapes.circle((0, 0), 30)
    p, s, objs = make_project([shapes.rectangle(-2, -1, 4, 2), circle])
    run("arrange.along", p, s, objs, count=4, rotate=True)
    rot = [o for o in s.vectors[2:] if abs(o.bbox().center[0] - 30) < 1e-6]
    assert rot and rot[0].bbox().width == pytest.approx(2, abs=1e-6)  # turned 90 degrees to the tangent


def test_feeds_suggestion():
    from iyicam.core.feeds import suggest
    c = suggest("mdf", 6.0, 2, 18000)
    assert c.feed_rate == pytest.approx(18000 * 2 * 0.30)
    assert suggest("aluminium", 6.0, 1, 18000).feed_rate < c.feed_rate / 5


def test_door_wizard_vectors_and_toolpaths():
    from iyicam.cam.job import compute_all
    p, s, _ = make_project([], size=(1000, 1000))
    run("create.door", p, s, width=400, height=700, frame=70, panels=2, rail=70, style="arch", arch=60,
        grooves=2, groove_spacing=8, x=500, y=500, pocket_tool=end_mill(6), edge_tool=vbit(90),
        groove_tool=vbit(60, d=6), cut_tool=end_mill(8))
    outline, *rest = s.vectors
    panels = [o for o in rest if o.name == "Panel"]
    grooves = [o for o in rest if o.name == "Groove"]
    assert outline.bbox().center == pytest.approx((500, 500))
    assert len(panels) == 2 and len(grooves) == 4
    bottom, top = sorted(panels, key=lambda o: o.bbox().ymin)
    assert bottom.contours[0].area() == pytest.approx(260 * 245)
    bb = top.bbox()
    assert (bb.xmin, bb.xmax, bb.ymax) == pytest.approx((370, 630, 850 - 70))
    assert any(isinstance(sg, Arc) for sg in top.contours[0].segs)
    for g in grooves:  # grooves run inside their panel
        assert any(g.bbox().xmin > pn.bbox().xmin and g.bbox().ymax < pn.bbox().ymax for pn in panels)
    assert [l.name for l in p.layers][-3:] == ["Door outline", "Door panels", "Door grooves"]
    assert [t.name for t in p.toolpaths] == ["Door grooves", "Door pocket", "Door panel edge", "Door cut-out"]
    res = compute_all(p)
    assert all(r.error is None and r.toolpaths for r in res.values())
    with pytest.raises(CommandError):
        run("create.door", p, s, width=100, height=100, frame=60)

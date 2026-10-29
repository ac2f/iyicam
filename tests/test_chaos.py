"""A long random session: vectors, commands, layers, sheets, toolpaths, nesting, saving; undo and redo all of it.

After every step the project must be consistent (no dangling layer, sheet or selection, no broken geometry, no
toolpath error), saving and loading must give the same project, and undoing everything must give the project
we started with, redoing everything the one we ended with.
"""
import json
import os
import random
import zlib

import pytest

pytest.importorskip("PySide6.QtWidgets")

from conftest import end_mill  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402
from test_cam_fuzz import random_value, scenario  # noqa: E402
from test_command_fuzz import broken, same, state  # noqa: E402

from iyicam.core import clip  # noqa: E402
from iyicam.core.document import Project, Selection, ToolpathDef, VectorObject  # noqa: E402
from iyicam.core.registry import REGISTRY  # noqa: E402
from iyicam.nesting.nest import NestSettings  # noqa: E402
from iyicam.nesting.parts import nest_objects  # noqa: E402

STEPS = int(os.environ.get("IYICAM_CHAOS_STEPS", "30"))
SEEDS = int(os.environ.get("IYICAM_CHAOS_SEEDS", "3"))


@pytest.fixture(scope="module")
def ctrl(tmp_path_factory):
    os.environ["IYICAM_HOME"] = str(tmp_path_factory.mktemp("home"))
    from iyicam.gui.app import create_window
    app, win = create_window(["test"], load_plugins=False)
    win.ctrl.settings.auto_recalculate = False
    yield win.ctrl
    win.ctrl.dirty = False
    win.close()
    QApplication.processEvents()


# -- consistency ---------------------------------------------------------------------------------------------
def consistent(c) -> str | None:
    p = c.project
    lids = [la.id for la in p.layers]
    sids = [s.id for s in p.sheets]
    if not lids or len(set(lids)) != len(lids):
        return f"layers: {lids}"
    if not sids or len(set(sids)) != len(sids):
        return f"sheets: {sids}"
    vids = [v.id for s in p.sheets for v in s.vectors]
    if len(set(vids)) != len(vids):
        return "a vector twice in the project"
    for s in p.sheets:
        for v in s.vectors:
            if v.layer_id not in lids:
                return f"vector {v.id} is on the layer {v.layer_id} that does not exist"
    for t in p.toolpaths:
        if any(x not in lids for x in t.selection.layer_ids):
            return f"toolpath {t.name} uses a layer that does not exist"
        if t.sheet_ids is not None and any(x not in sids for x in t.sheet_ids):
            return f"toolpath {t.name} uses a sheet that does not exist"
    if c.sheet_id not in sids:
        return "the current sheet does not exist"
    if c.layer_id not in lids:
        return "the current layer does not exist"
    on_sheet = {v.id for v in c.sheet.vectors}
    if any(i not in on_sheet for i in c.selection):
        return "the selection holds a vector that is not on the sheet"
    return broken(c)


def full_state(c):
    p = c.project
    return (state(c), json.dumps([[t.id, t.name, t.op, t.params, t.sheet_ids, t.selection.mode,
                                   t.selection.layer_ids, t.enabled] for t in p.toolpaths], default=str, sort_keys=True),
            [(s.id, s.name, round(s.width, 6), round(s.height, 6)) for s in p.sheets])


def same_full(a, b) -> bool:
    return same(a[0], b[0]) and a[1] == b[1] and a[2] == b[2]


# -- the steps ------------------------------------------------------------------------------------------------
def step_add(c, r):
    c.add_vectors([VectorObject(cs, r.choice(c.project.layers).id, f"v{r.randint(0, 99)}")
                   for cs in scenario(r)], select=r.random() < 0.7)


def step_delete(c, r):
    c.delete_selection()


def step_select(c, r):
    vs = c.sheet.vectors
    c.select([v.id for v in r.sample(vs, min(len(vs), r.randint(0, 4)))])


def step_command(c, r):
    cmd = REGISTRY.commands[r.choice(sorted(REGISTRY.commands))]
    c.run_command(cmd, {p.name: random_value(p, r) for p in cmd.params})


def step_add_layer(c, r):
    with c.edit("Add layer", geometry=False):
        la = c.project.add_layer(f"L{r.randint(0, 99)}")
    c.set_layer(la.id)


def step_delete_layer(c, r):
    p = c.project
    la = r.choice(p.layers)
    others = [x for x in p.layers if x.id != la.id]
    with c.edit("Delete layer"):
        p.remove_layer(la.id, r.choice(others).id if others and r.random() < 0.6 else None)
    c.layer_id = p.layers[0].id


def step_move_to_layer(c, r):
    la = r.choice(c.project.layers)
    objs = [o for o in c.selected_objects() if c.editable(o)]
    with c.edit("Move to layer"):
        for o in objs:
            o.layer_id = la.id


def step_add_sheet(c, r):
    with c.edit("Add sheet", geometry=False):
        s = c.project.add_sheet(None, r.choice([300, 600, 1200]), r.choice([200, 400, 800]))
    c.set_sheet(s.id)


def step_duplicate_sheet(c, r):
    src = c.sheet
    with c.edit("Duplicate sheet", geometry=False):
        s = c.project.add_sheet(src.name + " copy", src.width, src.height)
        s.vectors = [v.copy() for v in src.vectors]
    c.set_sheet(s.id)


def step_delete_sheet(c, r):
    if len(c.project.sheets) <= 1:
        return
    with c.edit("Delete sheet"):
        c.project.remove_sheet(c.sheet_id)
    c.set_sheet(c.project.sheets[0].id)


def step_add_toolpath(c, r):
    op_id = r.choice(["profile", "profile", "pocket", "drill", "engrave"])
    op = REGISTRY.operations[op_id]
    params = {q.name: random_value(q, r) for q in op.params()}
    for k in ("cut_depth", "stepdown"):   # keep the jobs small: the chaos is about the project, not the toolpath
        if isinstance(params.get(k), float):
            params[k] = min(max(params[k], 0.5 if k == "stepdown" else 1.0), 12.0)
    sel = (Selection("layers", [], [la.id for la in r.sample(c.project.layers, r.randint(1, len(c.project.layers)))])
           if r.random() < 0.5 else Selection("vectors", [v.id for v in c.sheet.vectors], []))
    t = ToolpathDef(f"{op_id} {r.randint(0, 99)}", op_id, end_mill(r.choice([3.0, 6.0])), params, sel)
    if r.random() < 0.3:
        t.sheet_ids = [c.sheet_id]
    c.add_toolpath(t)


def step_remove_toolpath(c, r):
    if c.project.toolpaths:
        c.remove_toolpaths({r.choice(c.project.toolpaths).id})


def step_move_toolpath(c, r):
    if c.project.toolpaths:
        c.move_toolpath(r.choice(c.project.toolpaths).id, r.choice([-1, 1]))


def step_sequence(c, r):
    tps = [t for t in c.project.toolpaths if t.op in ("profile", "engrave")]
    if tps:
        c.set_sequence(r.choice(tps).id, strategy=r.choice(["nearest", "smart", "rows_bl", "centre_out", "small_first"]),
                       grouping=r.choice(["depth", "part"]), smart_starts=r.random() < 0.5)


def step_nest(c, r):
    objs = c.selected_objects() or list(c.sheet.vectors)
    if not objs:
        return
    st = NestSettings(c.sheet.width, c.sheet.height, 5, 4, r.choice([[0], [0, 90]]), r.choice(["left", "compact"]),
                      iterations=r.choice([1, 3]))
    c.checkpoint("Nesting")
    try:
        nest_objects(c.project, objs, st, group_inner=r.random() < 0.5, fill_holes=r.random() < 0.5,
                     new_sheets=True, use_sheets=[c.sheet])
    except clip.TooComplex:        # (a pile of copies on one spot: refused, and nothing is changed)
        c.undo_stack.rollback(c.project)
        return
    except Exception:
        c.undo_stack.rollback(c.project)
        raise
    c.notify()


def step_material(c, r):
    with c.edit("Material", geometry=False):
        m = c.project.material
        m.thickness = r.choice([3.0, 12.0, 18.0])
        m.safe_z = r.choice([5.0, 10.0, 20.0])


def step_recalculate(c, r):
    c.recalculate()


def step_save_load(c, r, tmp):
    path = tmp / "chaos.icam"
    c.save_project(path)
    back = Project.load(path)
    mine = json.loads(json.dumps(c.project.to_dict(), default=str, sort_keys=True))
    theirs = json.loads(json.dumps(back.to_dict(), default=str, sort_keys=True))
    assert mine == theirs, "saving and loading changed the project"


STEPS_LIST = [
    (step_add, 5), (step_delete, 2), (step_select, 3), (step_command, 6), (step_add_layer, 1), (step_delete_layer, 1),
    (step_move_to_layer, 1), (step_add_sheet, 1), (step_duplicate_sheet, 1), (step_delete_sheet, 1),
    (step_add_toolpath, 3), (step_remove_toolpath, 1), (step_move_toolpath, 1), (step_sequence, 1),
    (step_nest, 1), (step_material, 1), (step_recalculate, 2),
]


@pytest.mark.parametrize("seed", range(SEEDS))
def test_a_long_random_session(ctrl, seed, tmp_path):
    r = random.Random(zlib.crc32(b"chaos") + seed)
    ctrl.new_project(600, 400, 18)
    ctrl.undo_stack.clear()
    start = full_state(ctrl)
    log = []
    names, weights = [f for f, _ in STEPS_LIST], [w for _, w in STEPS_LIST]
    done_edits = 0
    for n in range(STEPS):
        f = r.choices(names, weights)[0]
        log.append(f.__name__)
        before_edits = len(ctrl.undo_stack._undo)
        try:
            f(ctrl, r)
        except Exception as exc:  # noqa: BLE001
            import traceback
            pytest.fail(f"step {n} {f.__name__} raised {type(exc).__name__}: {exc}\n{traceback.format_exc()[-1500:]}\n"
                        f"steps so far: {log}")
        done_edits += len(ctrl.undo_stack._undo) - before_edits
        problem = consistent(ctrl)
        assert problem is None, f"after step {n} {f.__name__}: {problem}\nsteps so far: {log}"
        if r.random() < 0.15:
            step_save_load(ctrl, r, tmp_path)
        if r.random() < 0.1:   # undo, then redo: the same project
            before = full_state(ctrl)
            if ctrl.undo_stack.can_undo:
                ctrl.undo()
                assert consistent(ctrl) is None, f"after an undo at step {n}: {consistent(ctrl)}"
                ctrl.redo()
                assert same_full(full_state(ctrl), before), f"redo at step {n} did not restore the project\n{log}"
    ctrl.recalculate()
    for tp_def in ctrl.project.toolpaths:
        for res in ctrl.results_for(tp_def):
            assert res.error is None or "on top of each other" in res.error, f"{tp_def.name}: {res.error}\nsteps: {log}"
    end = full_state(ctrl)
    steps_back = 0
    while ctrl.undo_stack.can_undo:
        ctrl.undo()
        steps_back += 1
        assert consistent(ctrl) is None, f"after undoing {steps_back} steps: {consistent(ctrl)}"
    assert same_full(full_state(ctrl), start), f"undoing everything does not give the first project\n{log}"
    while ctrl.undo_stack.can_redo:
        ctrl.redo()
        assert consistent(ctrl) is None
    assert same_full(full_state(ctrl), end), f"redoing everything does not give the last project\n{log}"

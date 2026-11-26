"""The desktop's controller under any sequence of edits (Hypothesis state machine): undo gives back exactly the project before
an edit and redo exactly the one after; the project always survives its file; no vector is on a layer that is not there;
a group is isolated as one; and every toolpath keeps to one side of the project."""
import json
import math
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from hypothesis import HealthCheck, settings, strategies as st
from hypothesis.stateful import RuleBasedStateMachine, invariant, precondition, rule

from PySide6.QtWidgets import QApplication

from iyicam.builtins import ensure_registered
from iyicam.cam.job import sheets_for
from iyicam.core import scope, shapes
from iyicam.core.document import Selection, ToolpathDef, VectorObject, project_from_dict
from iyicam.core.geom import Transform
from iyicam.core.registry import REGISTRY
from iyicam.core.tooling import Tool

ensure_registered(REGISTRY)
APP = QApplication.instance() or QApplication([])
CMDS = ["xform.rot90l", "xform.flip_h", "edit.offset", "edit.weld", "edit.reverse", "arrange.center", "model.rot90r",
        "model.mirror_tb", "edit.close", "edit.explode", "corner.fillet"]


def state(c):
    d = c.project.to_dict()
    d.pop("metadata", None)                  # (revisions of G-code files are not taken back by undo, on purpose)
    return json.dumps(d, sort_keys=True)


class Editing(RuleBasedStateMachine):
    def __init__(self):
        super().__init__()
        import tempfile
        os.environ["IYICAM_HOME"] = tempfile.mkdtemp(prefix="iyi-sm-")
        from iyicam.gui.controller import Controller
        self.c = Controller(REGISTRY)
        self.c.settings.auto_recalculate = False
        c = self.c
        c.sheet.thickness = 18.0                 # (a sheet is given its thickness when it is first kept)
        c.add_vectors([VectorObject([shapes.rectangle(10, 10, 80, 40)], c.layer_id),
                       VectorObject([shapes.circle((200, 100), 30)], c.layer_id)])
        c.undo_stack.clear()
        self.history = []                    # (state before, state after) of every edit

    def edit(self, fn):
        before = state(self.c)
        fn()
        after = state(self.c)
        if after != before:
            self.history.append((before, after))

    # -- the edits ------------------------------------------------------------------------------------------------------
    @rule(x=st.floats(0, 500), y=st.floats(0, 300), r=st.floats(1, 60))
    def draw(self, x, y, r):
        self.edit(lambda: self.c.add_vectors([VectorObject([shapes.circle((x, y), r)], self.c.layer_id)]))

    @rule(data=st.data())
    def select(self, data):
        vs = self.c.sheet.vectors
        if vs:
            self.c.select(data.draw(st.lists(st.sampled_from([v.id for v in vs]), max_size=3, unique=True)))

    @rule(cid=st.sampled_from(CMDS))
    def command(self, cid):
        cmd = REGISTRY.commands.get(cid)
        if cmd is not None:
            self.edit(lambda: self.c.run_command(cmd, None, show_report=False))

    @rule(dx=st.floats(-50, 50), dy=st.floats(-50, 50), copies=st.integers(0, 2))
    def move(self, dx, dy, copies):
        self.edit(lambda: self.c.transform_selection(Transform.translate(dx, dy), copies))

    @rule()
    def delete(self):
        self.edit(self.c.delete_selection)

    @rule()
    def add_sheet(self):
        def go():
            with self.c.edit("Add sheet", geometry=False):
                s = self.c.project.add_sheet(like=self.c.sheet)
            self.c.set_sheet(s.id)
        self.edit(go)

    @rule(data=st.data())
    def open_sheet(self, data):
        self.c.set_sheet(data.draw(st.sampled_from([s.id for s in self.c.project.sheets])))

    @rule(data=st.data(), name=st.sampled_from(["", "A", "B"]))
    def group(self, data, name):
        ids = data.draw(st.lists(st.sampled_from([s.id for s in self.c.project.sheets]), min_size=1, max_size=3, unique=True))
        self.edit(lambda: self.c.set_sheet_group(ids, name))

    @rule()
    def isolate(self):
        self.edit(lambda: self.c.isolate([self.c.sheet_id]))

    @rule()
    def unisolate(self):
        s = self.c.sheet
        if s.group:
            self.edit(lambda: self.c.unisolate(s.group))

    @rule(data=st.data())
    def arrange(self, data):
        ids = [s.id for s in self.c.project.sheets]
        order = data.draw(st.permutations(ids))
        groups = {i: data.draw(st.sampled_from(["", "A", "C"])) for i in ids}
        self.edit(lambda: self.c.arrange_sheets(list(order), groups))

    @rule(d=st.sampled_from([3.0, 6.0]), by_layer=st.booleans())
    def toolpath(self, d, by_layer):
        from iyicam.core.scope import new_item_scope
        sel = (Selection("layers", [], [self.c.layer_id]) if by_layer
               else Selection("vectors", [v.id for v in self.c.selected_objects()], []))
        t = ToolpathDef("p", "profile", Tool(diameter=d), {"through": True}, sel, sheet_group=new_item_scope(self.c.sheet))
        self.edit(lambda: self.c.add_toolpath(t))

    @rule(data=st.data())
    def remove_toolpath(self, data):
        tps = self.c.project.toolpaths
        if tps:
            tid = data.draw(st.sampled_from([t.id for t in tps]))
            self.edit(lambda: self.c.remove_toolpaths({tid}))

    @rule(delta=st.sampled_from([-1, 1]))
    def move_sheet(self, delta):
        self.edit(lambda: self.c.move_sheet(self.c.sheet_id, delta))

    @rule()
    def new_layer(self):
        def go():
            with self.c.edit("Add layer", geometry=False):
                l = self.c.project.add_layer(f"L{len(self.c.project.layers)}")
                l.sheet_group = scope.new_item_scope(self.c.sheet)
            self.c.set_layer(l.id)
        self.edit(go)

    @precondition(lambda self: self.history and self.c.undo_stack.can_undo)
    @rule()
    def undo_redo(self):
        before, after = self.history[-1]
        if state(self.c) != after:                   # (only right after the last edit)
            return
        self.c.undo()
        assert state(self.c) == before, "undo did not give back the project before the edit"
        self.c.redo()
        assert state(self.c) == after, "redo did not give back the project after the edit"

    # -- what always holds ----------------------------------------------------------------------------------------------
    @invariant()
    def sound(self):
        p = self.c.project
        for s in p.sheets:
            for v in s.vectors:
                l = p.layer(v.layer_id)
                assert l is not None and l.applies(s), (s.name, s.group, s.isolated, l and l.sheet_group)
                for c in v.contours:
                    for x, y in c.flatten(0.5):
                        assert math.isfinite(x) and math.isfinite(y)
        for g in {s.group for s in p.sheets if s.group}:
            assert len({s.isolated for s in p.sheets if s.group == g}) == 1
        for t in p.toolpaths:
            assert len({scope.scope_of(s) for s in sheets_for(p, t)}) <= 1
        cur = p.layer(self.c.layer_id)
        assert cur is not None and cur.applies(self.c.sheet), "the current layer is not on the sheet open"
        d = p.to_dict()
        assert project_from_dict(json.loads(json.dumps(d))).to_dict() == d

    def teardown(self):
        self.c.deleteLater()


Editing.TestCase.settings = settings(max_examples=int(os.environ.get("SM_N", 40)), stateful_step_count=30, deadline=None,
                                     suppress_health_check=list(HealthCheck))
TestEditing = Editing.TestCase

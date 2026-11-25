"""Properties of projects (Hypothesis): the file round trip, and the rules of groups and isolation under any sequence of
changes (no vector on a layer that is not there, a group is isolated as one, a toolpath keeps to one side)."""
import json
import math

from hypothesis import given, settings, strategies as st
from hypothesis.stateful import RuleBasedStateMachine, invariant, precondition, rule

from iyicam.cam.job import sheets_for
from iyicam.core import scope, shapes
from iyicam.core.document import (Layer, Project, Selection, Sheet, ToolpathDef, VectorObject, project_from_dict)
from iyicam.core.geom import Transform
from iyicam.core.sheetturn import HOW, turn_sheet
from iyicam.core.tooling import Tool

from test_prop_geom import contour

SET = settings(max_examples=60, deadline=None)
names = st.text(alphabet="abcçğıöşüABC xyz-_09", min_size=1, max_size=8).map(str.strip).filter(bool)


@st.composite
def projects(draw):
    p = Project(draw(names))
    p.layers = [Layer(draw(names), "#123456", draw(st.booleans()), draw(st.booleans()), group=draw(st.sampled_from(["", "G1", "G2"])))
                for _ in range(draw(st.integers(1, 4)))]
    p.sheets = []
    for k in range(draw(st.integers(1, 4))):
        s = Sheet(f"S{k}", draw(st.floats(10, 3000)), draw(st.floats(10, 3000)), thickness=draw(st.floats(0.5, 50)))
        s.group = draw(st.sampled_from(["", "A", "B"]))
        s.isolated = bool(s.group) and draw(st.booleans())
        if draw(st.booleans()):
            s.outline = [[(0.0, 0.0), (s.width, 0.0), (s.width * 0.5, s.height)]]
        for _ in range(draw(st.integers(0, 3))):
            s.vectors.append(VectorObject(draw(st.lists(contour(), min_size=1, max_size=2)), draw(st.sampled_from(p.layers)).id,
                                          draw(st.text(max_size=5))))
        p.sheets.append(s)
    for g in {s.group for s in p.sheets if s.group}:          # (a group is isolated as one)
        iso = any(s.isolated for s in p.sheets if s.group == g)
        for s in p.sheets:
            if s.group == g:
                s.isolated = iso
    vids = [v.id for s in p.sheets for v in s.vectors]
    for _ in range(draw(st.integers(0, 3))):
        sel = (Selection("layers", [], [draw(st.sampled_from(p.layers)).id]) if draw(st.booleans()) or not vids
               else Selection("vectors", draw(st.lists(st.sampled_from(vids), max_size=3, unique=True)), []))
        t = ToolpathDef(draw(names), "profile", Tool(diameter=draw(st.floats(0.5, 20))), {"cut_depth": draw(st.floats(0, 30))},
                        sel, draw(st.booleans()), None, safe_z=draw(st.one_of(st.none(), st.floats(1, 50))),
                        sheet_group=draw(st.sampled_from(["", "A", "B"])))
        p.toolpaths.append(t)
    p.material.thickness = draw(st.floats(0.5, 50))
    return p


@SET
@given(projects())
def test_a_project_comes_back_from_its_file_as_it_was(p):
    d = p.to_dict()
    text = json.dumps(d)                                      # (it is JSON)
    q = project_from_dict(json.loads(text))
    assert q.to_dict() == d


@SET
@given(projects(), st.sampled_from(HOW))
def test_turning_a_sheet_keeps_everything_on_the_plate_and_four_quarter_turns_are_none(p, how):
    s = p.sheets[0]
    before = json.dumps([v.to_dict() if hasattr(v, "to_dict") else None for v in []])
    first = [[(round(x, 6), round(y, 6)) for c in v.contours for x, y in c.flatten(0.5)] for v in s.vectors]
    w, h = s.width, s.height
    turn_sheet(p, s, how)
    assert {s.width, s.height} == {w, h}
    if how in ("left", "right"):
        assert (s.width, s.height) == (h, w)
    for _ in range(3 if how in ("left", "right") else 1):
        turn_sheet(p, s, how)
    assert (s.width, s.height) == (w, h)
    again = [[(round(x, 6), round(y, 6)) for c in v.contours for x, y in c.flatten(0.5)] for v in s.vectors]
    assert all(math.dist(a, b) < 1e-6 * max(1, w, h) for va, vb in zip(first, again) for a, b in zip(va, vb))
    assert before is not None


# -- isolation under any sequence of changes ----------------------------------------------------------------------------

class Isolation(RuleBasedStateMachine):
    def __init__(self):
        super().__init__()
        p = Project("iso")
        p.layers = [Layer("Default"), Layer("Cut", "#ff0000")]
        p.sheets = []
        for k in range(4):
            s = Sheet(f"S{k}", 600, 400, thickness=18.0)
            s.vectors.append(VectorObject([shapes.rectangle(10 * k, 10, 50, 30)], p.layers[k % 2].id))
            p.sheets.append(s)
        p.toolpaths.append(ToolpathDef("all", "profile", Tool(), {}, Selection("layers", [], [p.layers[1].id])))
        p.toolpaths.append(ToolpathDef("v", "profile", Tool(), {}, Selection("vectors", [p.sheets[0].vectors[0].id,
                                                                                         p.sheets[1].vectors[0].id], [])))
        self.p = p

    sheet_ids = st.integers(0, 3)

    @rule(ids=st.lists(sheet_ids, min_size=1, max_size=3, unique=True), name=st.sampled_from(["", "A", "B", "C"]))
    def group(self, ids, name):
        scope.set_group(self.p, [self.p.sheets[i].id for i in ids], name)

    @rule(i=sheet_ids)
    def isolate(self, i):
        g = scope.isolate(self.p, [self.p.sheets[i].id])
        assert g and self.p.sheets[i].isolated and self.p.sheets[i].group == g

    @rule(i=sheet_ids)
    def unisolate(self, i):
        s = self.p.sheets[i]
        if s.group:
            scope.unisolate(self.p, s.group)
            assert not s.isolated

    @rule(i=sheet_ids, name=st.sampled_from(["A", "B", "C", "D"]))
    def rename(self, i, name):
        s = self.p.sheets[i]
        if s.group and not any(x.group == name for x in self.p.sheets):
            scope.rename_group(self.p, s.group, name)

    @rule(i=sheet_ids)
    def add_like(self, i):
        if len(self.p.sheets) < 8:
            self.p.add_sheet(like=self.p.sheets[i])

    @rule(i=sheet_ids)
    def add_layer_here(self, i):
        s = self.p.sheets[i]
        l = self.p.add_layer(f"L{len(self.p.layers)}")
        l.sheet_group = scope.new_item_scope(s)
        s.vectors.append(VectorObject([shapes.circle((100, 100), 10)], l.id))

    @invariant()
    def no_vector_is_on_a_layer_that_is_not_there(self):
        for s in self.p.sheets:
            for v in s.vectors:
                l = self.p.layer(v.layer_id)
                assert l is not None and l.applies(s), (s.name, s.group, s.isolated, l.name, l.sheet_group)

    @invariant()
    def a_group_is_isolated_as_one(self):
        for g in {s.group for s in self.p.sheets if s.group}:
            assert len({s.isolated for s in self.p.sheets if s.group == g}) == 1, g
        assert not any(s.isolated and not s.group for s in self.p.sheets)

    @invariant()
    def a_toolpath_keeps_to_one_side(self):
        for t in self.p.toolpaths:
            sides = {scope.scope_of(s) for s in sheets_for(self.p, t)}
            assert len(sides) <= 1, (t.name, sides)

    @invariant()
    def the_file_round_trip_keeps_it(self):
        d = self.p.to_dict()
        assert project_from_dict(d).to_dict() == d


Isolation.TestCase.settings = settings(max_examples=150, stateful_step_count=25, deadline=None)
TestIsolation = Isolation.TestCase

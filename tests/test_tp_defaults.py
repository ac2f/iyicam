"""The person's defaults of new toolpaths: how far a through cut (and a drill through) goes below the material; 0 too."""
import sys

import pytest

from conftest import add_tp, end_mill, make_project
from iyicam.builtins import ensure_registered
from iyicam.cam import tp_defaults
from iyicam.cam.job import compute_all
from iyicam.cam.moves import DrillCycle
from iyicam.core import shapes
from iyicam.core.registry import REGISTRY
from iyicam.core.tooling import Tool

ensure_registered(REGISTRY)


@pytest.fixture(autouse=True)
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("IYICAM_HOME", str(tmp_path))


def deepest(p, t, sheet):
    res = compute_all(p, REGISTRY)[(t.id, sheet.id)]
    return min(m.z for tp in res.toolpaths for m in tp.moves if getattr(m, "z", None) is not None)


def test_the_built_in_defaults_until_the_person_sets_them():
    assert tp_defaults.load() == {"through_overcut": 0.2, "drill_overcut": 1.0}
    tp_defaults.save({"through_overcut": 0.0})
    assert tp_defaults.load() == {"through_overcut": 0.0, "drill_overcut": 1.0}
    with pytest.raises(ValueError):
        tp_defaults.save({"drill_overcut": -1})


def test_the_schema_gives_new_toolpaths_the_persons_defaults():
    from iyicam.service import ops
    tp_defaults.save({"through_overcut": 0.0, "drill_overcut": 0.0})
    sch = ops.op_schema({})
    ov = {o["id"]: next(p["default"] for p in o["params"] if p["name"] == "overcut") for o in sch["operations"]
          if any(p["name"] == "overcut" for p in o["params"])}
    assert ov["profile"] == 0.0 and ov["drill"] == 0.0
    assert ops.op_tp_defaults({})["values"] == {"through_overcut": 0.0, "drill_overcut": 0.0}


def test_overcut_is_not_hidden_behind_more_options():
    from iyicam.core.params import is_advanced
    op = REGISTRY.operations["profile"]
    assert not is_advanced(next(p for p in op.params() if p.name == "overcut"))


def test_a_through_cut_and_a_drill_with_no_overcut_stop_at_the_bottom():
    p, s1, objs = make_project([shapes.rectangle(10, 10, 100, 60), shapes.circle((200, 100), 5)], size=(600, 400))
    thick = p.sheet_thickness(s1)
    t = add_tp(p, "profile", end_mill(6), {"through": True, "overcut": 0.0}, objs[:1])
    assert deepest(p, t, s1) == pytest.approx(-thick)
    p.toolpaths.remove(t)
    drill = Tool.from_dict({**end_mill(6).to_dict(), "type": "drill", "angle": 118})
    t = add_tp(p, "drill", drill, {"through": True, "overcut": 0.0}, objs[1:])
    res = compute_all(p, REGISTRY)[(t.id, s1.id)]
    assert min(m.z for tp in res.toolpaths for m in tp.moves if isinstance(m, DrillCycle)) == pytest.approx(-thick)

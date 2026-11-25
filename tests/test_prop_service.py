"""Every operation of the engine on any request (Hypothesis): it answers, or says what is wrong with a ``ServiceError``; it
never fails with another error (the web app would show "internal error") and never hangs."""
import base64
import math
import os
import signal

import pytest
from hypothesis import HealthCheck, given, settings, strategies as st

from conftest import add_tp, end_mill, make_project
from iyicam.core import shapes
from iyicam.service import ops
from iyicam.service.errors import ServiceError

SKIP = {"op_console_run", "op_console_reset", "op_plugin_install", "op_plugin_set"}   # (run code / change the program)
OPS = sorted(n for n in dir(ops) if n.startswith("op_") and n not in SKIP)
SLOW = {"op_nest", "op_simulate", "op_sim_timeline", "op_trace_bitmap", "op_calibrate_time"}


def base_project():
    p, s, objs = make_project([shapes.rectangle(10, 10, 100, 60), shapes.circle((200, 100), 30),
                               shapes.rectangle(300, 50, 40, 40)], size=(600, 400))
    add_tp(p, "profile", end_mill(6), {"through": True}, objs[:2])
    add_tp(p, "pocket", end_mill(6), {"cut_depth": 3}, objs[2:])
    p.add_sheet("Two")
    return p


BASE = base_project().to_dict()
SHEET = BASE["sheets"][0]["id"]
VIDS = [v["id"] for v in BASE["sheets"][0]["vectors"]]
TIDS = [t["id"] for t in BASE["toolpaths"]]
LIDS = [l["id"] for l in BASE["layers"]]

junk = st.one_of(st.none(), st.booleans(), st.integers(-10**9, 10**9), st.floats(allow_nan=True, allow_infinity=True),
                 st.text(max_size=12), st.lists(st.one_of(st.integers(-5, 5), st.text(max_size=4)), max_size=3),
                 st.dictionaries(st.text(max_size=5), st.integers(-3, 3), max_size=3))
ids = st.one_of(st.sampled_from(VIDS + TIDS + LIDS + [SHEET, "nope", ""]), junk)
idlists = st.one_of(st.lists(st.sampled_from(VIDS + TIDS + LIDS + [SHEET, "nope"]), max_size=4), junk)
FIELDS = {
    "sheet": ids, "selection": idlists, "toolpaths": idlists, "toolpath": st.one_of(ids, st.just(BASE["toolpaths"][0])),
    "objects": idlists, "ids": idlists, "keys": idlists, "id": ids, "index": junk, "action": st.one_of(st.text(max_size=8),
    st.sampled_from(["group", "arrange", "rename", "isolate", "unisolate", "new_sheets", "add", "delete", "move", "order",
                     "preview", "save", "list", "reverse", "set", "clear", "manual_order"])),
    "name": junk, "command": st.one_of(st.sampled_from(["xform.rot90l", "model.rot90l", "edit.offset", "arrange.align",
                                                        "create.rect", "nope"]), junk),
    "values": st.one_of(junk, st.dictionaries(st.sampled_from(["distance", "angle", "dx", "dy", "x", "y", "width", "copies"]),
                                             junk, max_size=3)),
    "settings": st.one_of(junk, st.dictionaries(st.sampled_from(["place_on", "rotation", "spacing", "margin", "width",
                                                                 "height", "iterations", "time_limit", "group", "angles",
                                                                 "new_sheets_group", "quantity"]), junk, max_size=4)),
    "post": st.one_of(st.sampled_from(["mach3", "grbl", "fanuc", "nope"]), junk), "file_name": junk, "format": junk,
    "data_b64": st.one_of(st.binary(max_size=64).map(lambda b: base64.b64encode(b).decode()), junk),
    "text": junk, "font": junk, "size": junk, "step": junk, "resolution": junk, "per_toolpath": junk, "sheets": idlists,
    "group": junk, "old": junk, "order": idlists, "groups": junk, "params": junk, "op": st.one_of(st.sampled_from(
        ["profile", "pocket", "drill", "nope"]), junk), "tool": junk, "item": junk, "items": junk, "path": junk,
    "options": junk, "recalculate": junk, "preview": junk, "thickness": junk, "width": junk, "height": junk,
}
FIELD_NAMES = sorted(FIELDS)


@st.composite
def payloads(draw):
    d = {}
    if draw(st.integers(0, 9)) < 8:
        d["project"] = draw(st.one_of(st.just(BASE), st.just({**BASE, "sheets": []}), st.just({**BASE, "toolpaths": "x"}),
                                      st.just({"format": "iyicam-project"}), junk))
    for k in draw(st.lists(st.sampled_from(FIELD_NAMES), max_size=5, unique=True)):
        d[k] = draw(FIELDS[k])
    return d


class Hang(Exception):
    pass


def _alarm(*_):
    raise Hang()


@pytest.fixture(autouse=True)
def _home(tmp_path, monkeypatch):
    monkeypatch.setenv("IYICAM_HOME", str(tmp_path / "home"))


@pytest.mark.parametrize("name", OPS)
def test_an_operation_answers_or_says_what_is_wrong(name):
    fn = getattr(ops, name)

    @settings(max_examples=15 if name in SLOW else 60, deadline=None, database=None,
              suppress_health_check=list(HealthCheck))
    @given(payloads())
    def run(payload):
        old = signal.signal(signal.SIGALRM, _alarm)
        signal.alarm(60 if name in SLOW else 20)
        try:
            out = fn(payload)
            assert out is None or isinstance(out, dict), type(out)
        except ServiceError:
            pass
        except Hang:
            pytest.fail(f"{name} did not answer in time: {payload!r:.400}")
        finally:
            signal.alarm(0)
            signal.signal(signal.SIGALRM, old)

    run()

"""The generator of the numbers a browser implementation of the editing is tested against (tools/make_js_golden.py).

The mobile page (in the Cnc-Assistant project) edits in JavaScript and checks itself against the file this makes.
If a command here changes, the file must be made again; this keeps the generator itself honest.
"""
import importlib.util
import json
from pathlib import Path

import pytest

from iyicam.core.registry import REGISTRY

TOOL = Path(__file__).resolve().parents[1] / "tools" / "make_js_golden.py"


@pytest.fixture(scope="module")
def golden(tmp_path_factory):
    spec = importlib.util.spec_from_file_location("make_js_golden", TOOL)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    out = tmp_path_factory.mktemp("golden") / "golden.json"
    assert mod.main(["-o", str(out)]) == 0
    return mod, json.loads(out.read_text(encoding="utf-8"))


def test_the_listed_commands_exist_and_every_one_has_cases(golden):
    mod, g = golden
    for cmd in mod.LOCAL:
        assert cmd in REGISTRY.commands, cmd
    assert {c["command"] for c in g["cases"]} == set(mod.LOCAL)
    assert {c["id"] for c in g["schema"]} == set(mod.LOCAL)


def test_the_cases_carry_what_a_test_needs(golden):
    _, g = golden
    ids = {v["id"] for v in g["project"]["sheets"][0]["vectors"]}
    assert len(g["cases"]) > 100 and len(g["params"]) > 30
    for c in g["cases"]:
        assert set(c["selection"]) <= ids and c["sheet"] == "S0"
        if c["ok"]:
            assert c["vectors"] and isinstance(c["selection_after"], list) and "messages" in c
        else:
            assert c["error"]                                # a refusal has its words
    assert any(not c["ok"] for c in g["cases"]) and any(c["ok"] for c in g["cases"])


def test_the_project_has_every_kind_of_segment(golden):
    _, g = golden
    kinds = {s[0] for v in g["project"]["sheets"][0]["vectors"] for c in v["contours"] for s in c["segs"]}
    assert kinds == {"L", "A", "C"}
    assert any(len(v["contours"]) > 1 for v in g["project"]["sheets"][0]["vectors"])          # a compound vector
    assert any(v.get("locked_starts") for v in g["project"]["sheets"][0]["vectors"])
    assert any(not c["closed"] for v in g["project"]["sheets"][0]["vectors"] for c in v["contours"])


def test_what_a_refusal_says_is_what_the_program_says(golden):
    _, g = golden
    said = {c["error"] for c in g["cases"] if not c["ok"]}
    assert any("select at least" in e for e in said) and any("no size" in e for e in said)
    assert any("at most" in e for e in said)

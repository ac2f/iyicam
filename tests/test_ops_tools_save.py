"""The tool database of the user kept through the service (the web app saves it with ``tools_save``)."""
import pytest

from iyicam.core import paths
from iyicam.core.tooling import ToolLibrary
from iyicam.service import ops
from iyicam.service.errors import ServiceError


def test_the_tools_saved_are_those_the_schema_gives_back(tmp_path, monkeypatch):
    monkeypatch.setenv("IYICAM_HOME", str(tmp_path))
    tools = ops.op_schema({})["tools"]
    tools[0]["name"] = "Changed on the web"
    out = ops.op_tools_save({"tools": tools[:2], "groups": ["Empty group"]})
    assert [t["name"] for t in out["tools"]][0] == "Changed on the web" and "Empty group" in out["groups"]
    lib = ToolLibrary.load(paths.tools_file())
    assert len(lib.tools) == 2 and lib.tools[0].name == "Changed on the web"
    assert ops.op_schema({})["tools"][0]["name"] == "Changed on the web"


def test_a_list_that_is_not_one_of_tools_is_refused(tmp_path, monkeypatch):
    monkeypatch.setenv("IYICAM_HOME", str(tmp_path))
    with pytest.raises(ServiceError):
        ops.op_tools_save({"tools": "nope"})

"""Plugins on the page: look at them, install one (a .py or a zip with a package), switch it on and off, reload it, remove it.

What matters: nothing is written before the file was looked at (name, size, a zip's paths, links, file kinds, valid Python), a
plugin that raises while loading is rolled back and its error is shown, what a plugin registers is in the engine at once (the
page builds its lists from ``schema``) and goes with the plugin, the owner of the engine can turn installing off, a hub's own
engine never installs one, and what was switched off stays off.
"""
import base64
import io
import json
import zipfile

import pytest

from iyicam.core.registry import REGISTRY
from iyicam.service import ServiceHost
from iyicam.service import pluginhost
from iyicam.service.errors import ServiceError

HELLO = b'''
PLUGIN = {"name": "Hello", "version": "1.0", "author": "me", "description": "Adds a square"}

def register(api):
    def run(ctx, values):
        ctx.add(api.shapes.rectangle(0, 0, values["size"], values["size"]), name="Square")
    api.add_command("hello.square", "Hello Square...", run, menu="Plugins/Hello",
                    params=[api.Param("size", "Size", "length", 50.0, min=1.0)])
    api.add_gcode_filter(lambda text, job, post: text)
'''


@pytest.fixture
def host(tmp_path, monkeypatch):
    monkeypatch.setenv("IYICAM_HOME", str(tmp_path / "home"))
    monkeypatch.delenv("IYICAM_PLUGIN_INSTALL", raising=False)
    monkeypatch.delenv("IYICAM_SERVICE_PLUGINS", raising=False)
    h = ServiceHost(store=None)
    yield h
    for info in list(pluginhost.PLUGINS.manager.plugins.values()):
        pluginhost.PLUGINS.manager.unload(info)


def b64(data: bytes) -> str:
    return base64.b64encode(data).decode()


def install(host, name, data):
    return host.call("plugin_install", {"name": name, "data_b64": b64(data)})


def commands(host):
    return [c["id"] for c in host.call("schema", {})["commands"]]


def make_zip(files: dict, links=()):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for name, data in files.items():
            zf.writestr(name, data)
        for name in links:
            info = zipfile.ZipInfo(name)
            info.external_attr = (0o120777 << 16)
            zf.writestr(info, "target")
    return buf.getvalue()


def test_a_plugin_file_is_installed_loaded_and_offered_at_once(host, tmp_path):
    assert host.call("plugins", {})["plugins"] == [] and host.call("plugins", {})["install"] is True
    out = install(host, "hello.py", HELLO)
    p = out["plugin"]
    assert out["installed"] == "hello" and p["loaded"] and not p["error"] and p["enabled"]
    assert (p["name"], p["version"], p["author"]) == ("Hello", "1.0", "me")
    assert p["provides"]["commands"] == ["hello.square"] and p["provides"]["gcode_filters"] == 1 and p["removable"]
    assert "hello.square" in commands(host)                                      # (the page builds its lists from schema)
    assert (tmp_path / "home" / "plugins" / "hello.py").exists()
    from conftest import make_project
    from iyicam.core import shapes
    proj, s, objs = make_project([shapes.rectangle(0, 0, 10, 10)], size=(300, 300))
    r = host.call("command", {"project": proj.to_dict(), "command": "hello.square", "values": {"size": 40}})
    assert r["ok"] and sum(len(x["vectors"]) for x in r["project"]["sheets"]) == 2     # (it runs: a square is added)


def test_a_plugin_goes_off_comes_back_reloads_and_is_removed_with_everything_it_gave(host, tmp_path):
    install(host, "hello.py", HELLO)
    off = host.call("plugin_set", {"key": "hello", "action": "disable"})["plugins"][0]
    assert not off["enabled"] and not off["loaded"] and "hello.square" not in commands(host) and off["provides"]["gcode_filters"] == 0
    assert json.loads((tmp_path / "home" / "plugins.json").read_text())["disabled"] == ["hello"]      # (it stays off)
    with pytest.raises(ServiceError) as e:
        host.call("plugin_set", {"key": "hello", "action": "reload"})
    assert e.value.code == "plugin_disabled"
    assert host.call("plugin_set", {"key": "hello", "action": "enable"})["plugins"][0]["loaded"]
    assert "hello.square" in commands(host)
    assert host.call("plugin_set", {"key": "hello", "action": "reload"})["plugins"][0]["loaded"]
    gone = host.call("plugin_set", {"key": "hello", "action": "remove"})
    assert gone["plugins"] == [] and "hello.square" not in commands(host)
    assert not (tmp_path / "home" / "plugins" / "hello.py").exists()
    assert json.loads((tmp_path / "home" / "plugins.json").read_text())["disabled"] == []


def test_a_new_start_of_the_engine_keeps_what_was_switched_off(host, tmp_path):
    install(host, "hello.py", HELLO)
    host.call("plugin_set", {"key": "hello", "action": "disable"})
    pluginhost.PLUGINS._manager = None                         # (the engine starts again)
    infos = pluginhost.PLUGINS.load_all()
    assert [(i.key, i.enabled, i.loaded) for i in infos] == [("hello", False, False)]


def test_a_plugin_that_raises_while_loading_is_rolled_back_and_says_why(host):
    bad = b'def register(api):\n    api.add_command("bad.one", "Bad", lambda c, v: None)\n    raise RuntimeError("boom")\n'
    out = install(host, "bad.py", bad)
    p = out["plugin"]
    assert not p["loaded"] and "boom" in p["error"] and "bad.one" not in commands(host)       # (what it had registered is gone)
    assert {"plugin": "bad", "error": p["error"]} in host.call("ping", {})["plugin_errors"]


def test_what_is_sent_is_looked_at_before_anything_is_written(host, tmp_path):
    plugins = tmp_path / "home" / "plugins"
    cases = [("a.txt", b"x"), ("1bad.py", HELLO), ("bad name.py", HELLO), ("ok.py", b"def broken(:\n"),
             ("ok.py", b""), ("ok.py", b"x" * (pluginhost.MAX_FILE + 1)), ("ok.zip", b"not a zip"), ("ok.zip", make_zip({"../x.py": "1"})),
             ("ok.zip", make_zip({"/abs.py": "1"})), ("ok.zip", make_zip({"pkg/__init__.py": "x = 1", "pkg/run.sh": "rm -rf /"})),
             ("ok.zip", make_zip({"pkg/__init__.py": "x = 1"}, links=["pkg/link.py"])),
             ("ok.zip", make_zip({"pkg/a.py": "x = 1"})), ("ok.zip", make_zip({"a/__init__.py": "", "b/__init__.py": ""})),
             ("ok.zip", make_zip({"pkg/__init__.py": "def f(:"}))]
    for name, data in cases:
        with pytest.raises(ServiceError) as e:
            install(host, name, data)
        assert e.value.code == "invalid_plugin", name
    assert not plugins.exists() or not list(plugins.iterdir())
    with pytest.raises(ServiceError):
        host.call("plugin_install", {"name": "a.py"})                       # (no data)
    out = install(host, "../../evil.py", HELLO)                             # (only the name of the file counts: never a path)
    assert out["installed"] == "evil" and (plugins / "evil.py").exists() and not (tmp_path / "evil.py").exists()


def test_a_zip_with_a_package_is_installed_as_a_package(host, tmp_path):
    files = {"shapes_pack/__init__.py": HELLO.decode(), "shapes_pack/data.json": "{}", "shapes_pack/sub/more.py": "y = 2"}
    out = install(host, "pack.zip", make_zip(files))
    p = out["plugin"]
    assert out["installed"] == "shapes_pack" and p["loaded"] and p["kind"] == "package" and p["provides"]["commands"] == ["hello.square"]
    assert (tmp_path / "home" / "plugins" / "shapes_pack" / "sub" / "more.py").exists()
    host.call("plugin_set", {"key": "shapes_pack", "action": "remove"})
    assert not (tmp_path / "home" / "plugins" / "shapes_pack").exists()
    out = install(host, "solo.zip", make_zip({"solo_one.py": HELLO.decode()}))          # (a zip with one file)
    assert out["installed"] == "solo_one" and out["plugin"]["loaded"]
    out = install(host, "whole.zip", make_zip({"__init__.py": HELLO.decode(), "helper.py": "z = 3"}))   # (the zip is the package)
    assert out["installed"] == "whole" and out["plugin"]["loaded"]


def test_installing_again_replaces_the_plugin(host):
    install(host, "hello.py", HELLO)
    newer = HELLO.replace(b"1.0", b"2.0")
    out = install(host, "hello.py", newer)
    assert out["plugin"]["version"] == "2.0" and len(out["plugins"]) == 1 and commands(host).count("hello.square") == 1


def test_the_owner_of_the_engine_can_turn_installing_off(host, monkeypatch):
    install(host, "hello.py", HELLO)
    monkeypatch.setenv("IYICAM_PLUGIN_INSTALL", "off")
    listing = host.call("plugins", {})
    assert listing["install"] is False and listing["plugins"][0]["loaded"]                 # (what is there still works)
    for op, payload in (("plugin_install", {"name": "x.py", "data_b64": b64(HELLO)}), ("plugin_set", {"key": "hello", "action": "remove"})):
        with pytest.raises(ServiceError) as e:
            host.call(op, payload)
        assert e.value.code == "plugin_install_off"
    assert host.call("plugin_set", {"key": "hello", "action": "disable"})["plugins"][0]["enabled"] is False      # (on / off is allowed)


def test_plugins_can_be_turned_off_altogether(host, monkeypatch):
    monkeypatch.setenv("IYICAM_SERVICE_PLUGINS", "0")
    out = host.call("plugins", {})
    assert out["plugins_on"] is False and out["install"] is False
    with pytest.raises(ServiceError) as e:
        install(host, "hello.py", HELLO)
    assert e.value.code == "plugins_off"


def test_a_plugin_that_was_not_found_or_an_odd_action_is_said(host):
    for key, action, code in (("nope", "remove", "plugin_not_found"), ("nope", "reload", "plugin_not_found"), ("x", "burn", "bad_request")):
        with pytest.raises(ServiceError) as e:
            host.call("plugin_set", {"key": key, "action": action})
        assert e.value.code == code
import textwrap

import pytest
from conftest import add_tp, end_mill, make_project

from iyicam.builtins import register as register_builtins
from iyicam.cam.job import compute_toolpath
from iyicam.core import shapes
from iyicam.core.commands import CommandContext, run_command
from iyicam.core.registry import Registry
from iyicam.plugins.api import PluginAPI
from iyicam.plugins.manager import PluginManager
from iyicam.post.engine import PostJob

PLUGIN = textwrap.dedent('''
    PLUGIN = {"name": "Test Plugin", "version": "1.2", "author": "me", "description": "for tests"}

    class Surfacing:
        pass

    def register(api):
        class Spiral(api.Operation):
            id = "test.spiral"
            label = "Test Spiral"

            @classmethod
            def params(cls):
                return [api.Param("depth", "Depth", "length", 1.0)]

            def compute(self, ctx):
                mb = api.MoveBuilder(ctx.safe_z, ctx.clearance, ctx.tool.feed_rate, ctx.tool.plunge_rate)
                bb = ctx.objects[0].bbox()
                mb.travel(bb.xmin, bb.ymin, -self["depth"])
                mb.feed(bb.xmax, bb.ymax, -self["depth"])
                return [self.make_toolpath(ctx, mb.finish())]

        api.add_operation(Spiral)

        def square(ctx, values):
            ctx.add(api.shapes.rectangle(0, 0, values["size"], values["size"]), name="sq")

        api.add_command("test.square", "Square", square, menu="Plugins/Test",
                        params=[api.Param("size", "Size", "length", 25.0)])
        api.add_postprocessor({"id": "test.post", "name": "Test Post", "extension": "tst",
                               "blocks": {"header": "(HELLO {project_name})"}})
        api.add_gcode_filter(lambda text, job, post: text + "(FILTERED)\\n")
        api.on("toolpath_computed", lambda *a: CALLS.append("computed"))

    CALLS = []

    def unregister(api):
        CALLS.append("bye")
''')


@pytest.fixture
def reg():
    r = Registry()
    register_builtins(PluginAPI("builtin", r))
    return r


def test_plugin_lifecycle(tmp_path, reg):
    (tmp_path / "myplug.py").write_text(PLUGIN)
    (tmp_path / "_private.py").write_text("raise SystemExit")
    pm = PluginManager(reg, [tmp_path], use_entry_points=False)
    infos = pm.load_all()
    assert [i.key for i in infos] == ["myplug"]
    info = infos[0]
    assert info.loaded and info.error == ""
    assert (info.name, info.version, info.author) == ("Test Plugin", "1.2", "me")
    assert "test.spiral" in reg.operations and "test.square" in reg.commands and "test.post" in reg.posts

    p, s, objs = make_project([shapes.rectangle(10, 10, 20, 20)])
    t = add_tp(p, "test.spiral", end_mill(), {"depth": 2}, objs)
    res = compute_toolpath(p, t, s, reg)
    assert res.error is None and res.toolpaths
    assert info.module.CALLS == ["computed"]

    ctx = CommandContext(p, s)
    run_command(reg.commands["test.square"], ctx, {"size": 40})
    assert s.vectors[-1].bbox().width == pytest.approx(40)

    # gcode filters registered in this registry are applied by the module level REGISTRY only,
    # so call the filter directly here
    post = reg.posts["test.post"]
    text = post.generate(PostJob(res.toolpaths, p, s))
    assert "(HELLO t)" in text
    assert reg.gcode_filters[0][1](text, None, post).endswith("(FILTERED)\n")

    pm.set_enabled("myplug", False)
    assert "test.spiral" not in reg.operations and "test.post" not in reg.posts
    assert not reg.gcode_filters and not reg.hooks.get("toolpath_computed")
    assert "profile" in reg.operations  # built-ins untouched
    pm.set_enabled("myplug", True)
    assert "test.spiral" in reg.operations


def test_broken_plugin_is_reported_not_raised(tmp_path, reg):
    (tmp_path / "bad.py").write_text("def register(api):\n    api.add_command('x', 'X', lambda c, v: None)\n"
                                     "    raise RuntimeError('boom')\n")
    pkg = tmp_path / "pkg"
    pkg.mkdir()
    (pkg / "__init__.py").write_text("from .helper import VALUE\n"
                                     "def register(api):\n    api.add_command('pkg.cmd', 'P', lambda c, v: None)\n")
    (pkg / "helper.py").write_text("VALUE = 1\n")
    pm = PluginManager(reg, [tmp_path], use_entry_points=False)
    infos = {i.key: i for i in pm.load_all()}
    assert not infos["bad"].loaded and "boom" in infos["bad"].error
    assert "x" not in reg.commands  # partial registrations are rolled back
    assert infos["pkg"].loaded and "pkg.cmd" in reg.commands


def test_example_plugins_load(reg):
    from pathlib import Path
    ex = Path(__file__).resolve().parents[1] / "examples" / "plugins"
    pm = PluginManager(reg, [ex], use_entry_points=False)
    infos = pm.load_all()
    assert infos, "example plugins missing"
    for i in infos:
        assert i.loaded, i.error


def test_api_validates_hooks(reg):
    api = PluginAPI("x", reg)
    with pytest.raises(ValueError):
        api.on("not_a_hook", lambda: None)


@pytest.mark.parametrize("lid", [False, True])
def test_box_maker_panels_are_complementary(reg, lid):
    """Sum of panel volumes equals the box shell volume: no overlaps and no gaps at joints."""
    from pathlib import Path
    from iyicam.core.geom import polygon_area
    ex = Path(__file__).resolve().parents[1] / "examples" / "plugins"
    PluginManager(reg, [ex], use_entry_points=False).load_all()
    p, s, _ = make_project([], size=(2000, 1000))
    W, D, H, t = 200.0, 150.0, 100.0, 6.0
    ctx = CommandContext(p, s)
    run_command(reg.commands["box_maker.box"], ctx, {"width": W, "depth": D, "height": H, "thickness": t,
                                                     "finger": 20, "lid": lid, "dogbones": False})
    vol = sum(abs(polygon_area(o.contours[0].flatten())) for o in s.vectors) * t
    inner = (W - 2 * t) * (D - 2 * t) * (H - (2 * t if lid else t))
    assert vol == pytest.approx(W * D * H - inner)
    run_command(reg.commands["box_maker.box"], CommandContext(p, s), {"dogbones": True, "tool": 3})
    assert all(o.contours[0].closed for o in s.vectors)

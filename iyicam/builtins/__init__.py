"""Built-in features, registered through the same API that plugins use."""
from __future__ import annotations

from ..core import paths
from ..core.registry import REGISTRY, Registry
from ..plugins.api import PluginAPI


def register(api: PluginAPI) -> None:
    from ..cam.drill import DrillOp
    from ..cam.engrave import EngraveOp
    from ..cam.fluting import FlutingOp
    from ..cam.inlay import InlayOp
    from ..cam.pocket import PocketOp
    from ..cam.profile import ProfileOp
    from ..cam.vcarve import VCarveOp
    from ..formats import dxf, hpgl, svg
    from ..post.loader import BUILTIN_DIR, load_posts
    from .commands import commands

    for op in (ProfileOp, PocketOp, DrillOp, EngraveOp, VCarveOp, InlayOp, FlutingOp):
        api.add_operation(op)
    for cmd in commands():
        api.add_command(cmd)
    for post in load_posts([BUILTIN_DIR]):
        api.add_postprocessor(post)
    api.add_importer(("dxf",), "AutoCAD DXF", dxf.import_dxf)
    api.add_importer(("svg",), "Scalable Vector Graphics", svg.import_svg)
    api.add_importer(("plt", "hpgl", "hpg"), "HPGL plotter file", hpgl.import_hpgl)
    api.add_exporter("dxf", "AutoCAD DXF", dxf.export_dxf)
    api.add_exporter("svg", "Scalable Vector Graphics", svg.export_svg)


def load_user_posts(registry: Registry = REGISTRY) -> list:
    """Posts in ``~/.iyicam/posts`` (they may ``extends`` a built-in post)."""
    from ..post.loader import BUILTIN_DIR, load_posts, read_definition
    known = {p.stem: read_definition(p) for p in BUILTIN_DIR.glob("*.toml")}
    api = PluginAPI("user-posts", registry)
    out = []
    for post in load_posts([paths.posts_dir()], known):
        out.append(api.add_postprocessor(post))
    return out


_done: set[int] = set()


def ensure_registered(registry: Registry = REGISTRY) -> Registry:
    """Register built-ins once per registry (safe to call repeatedly)."""
    if id(registry) not in _done:
        register(PluginAPI("builtin", registry))
        _done.add(id(registry))
    return registry

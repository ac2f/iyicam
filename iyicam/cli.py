"""Command line interface for batch work (no GUI needed).

    python -m iyicam posts
    python -m iyicam info job.icam
    python -m iyicam gcode job.icam --post mach3 --sheet all -o out/job.tap
    python -m iyicam import drawing.dxf -o job.icam --thickness 18
    python -m iyicam nest job.icam --width 2800 --height 2100 --spacing 8 -o nested.icam
    python -m iyicam nest job.icam --leftover 900x600:2 --stock leftovers.json -o nested.icam
    python -m iyicam nest job.icam --angles 0,22.5,90 --rotate-layer Grain=none -o nested.icam
    python -m iyicam nest job.icam --squeeze-angle 5 --squeeze-margin 3 --apply-squeeze -o nested.icam
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .builtins import ensure_registered, load_user_posts
from .cam.job import compute_all, sheet_toolpaths
from .cam.moves import PathStats, format_time
from .core import paths
from .core.document import Project
from .core.registry import REGISTRY
from .formats.report import ImportReport, run_importer
from .plugins.manager import PluginManager

COMMANDS = ("posts", "info", "gcode", "import", "nest", "plugins")


def _setup(plugins: bool = True) -> None:
    ensure_registered(REGISTRY)
    try:
        load_user_posts(REGISTRY)
    except Exception as exc:
        print(f"warning: user posts: {exc}", file=sys.stderr)
    if plugins:
        for info in PluginManager(REGISTRY, [paths.plugins_dir()]).load_all():
            if info.error:
                print(f"warning: plugin {info.key} failed to load", file=sys.stderr)


def cmd_posts(a) -> int:
    for p in sorted(REGISTRY.posts.values(), key=lambda p: p.id):
        print(f"{p.id:16s} {p.name:32s} *.{p.extension:4s} {p.description}")
    return 0


def cmd_plugins(a) -> int:
    pm = PluginManager(REGISTRY, [paths.plugins_dir()])
    for i in pm.discover():
        print(f"{i.key:20s} {'enabled' if i.enabled else 'disabled'} {i.path or i.entry_point}")
    return 0


def cmd_info(a) -> int:
    p = Project.load(a.project)
    res = compute_all(p)
    print(f"{p.name}: {len(p.sheets)} sheet(s), {len(p.layers)} layer(s), {len(p.toolpaths)} toolpath(s)")
    for i, s in enumerate(p.sheets, 1):
        st = PathStats()
        for tp in sheet_toolpaths(p, res, s):
            st = st.merge(tp.stats(p.material.rapid_rate))
        print(f"  sheet {i} '{s.name}' {s.width:g} x {s.height:g}: {len(s.vectors)} vectors, "
              f"machining {format_time(st.time_s)}")
    for (tid, sid), r in res.items():
        t = p.toolpath(tid)
        for w in ([r.error] if r.error else []) + r.warnings:
            print(f"  ! {t.name}: {w.splitlines()[0]}")
    return 0


def cmd_gcode(a) -> int:
    from .post.export import write_gcode
    p = Project.load(a.project)
    post = REGISTRY.posts.get(a.post)
    if post is None:
        print(f"unknown post '{a.post}', see 'iyicam posts'", file=sys.stderr)
        return 2
    res = compute_all(p)
    for r in res.values():
        if r.error:
            print(f"error: {r.error}", file=sys.stderr)
    if a.sheet != "all" and not (a.sheet.isdigit() and 1 <= int(a.sheet) <= len(p.sheets)):
        print(f"error: --sheet must be 'all' or a number from 1 to {len(p.sheets)}", file=sys.stderr)
        return 2
    sheets = p.sheets if a.sheet == "all" else [p.sheets[int(a.sheet) - 1]]
    out = Path(a.output) if a.output else Path(a.project).with_suffix("." + post.extension)
    written = []
    for i, s in enumerate(sheets, 1):
        tps = sheet_toolpaths(p, res, s)
        if not tps:
            continue
        path = out if len(sheets) == 1 else out.with_name(f"{out.stem}_{i}{out.suffix}")
        written += write_gcode(path, p, s, tps, post)
    for w in written:
        print(w)
    return 0 if written else 1


def cmd_import(a) -> int:
    p = Project(Path(a.file).stem)
    p.material.thickness = a.thickness
    imp = REGISTRY.importer_for(Path(a.file).suffix)
    if imp is None:
        print(f"no importer for {a.file}", file=sys.stderr)
        return 2
    report = ImportReport()
    objs = run_importer(imp.func, a.file, p, p.sheets[0], report)
    if report.summary():
        print(report.summary(), file=sys.stderr)
    bb = p.sheets[0].bbox()
    if not bb.empty:
        p.sheets[0].width = max(p.sheets[0].width, bb.xmax + 10)
        p.sheets[0].height = max(p.sheets[0].height, bb.ymax + 10)
    p.save(a.output)
    print(f"{len(objs)} vectors -> {a.output}")
    return 0


def cmd_nest(a) -> int:
    from .core.stock import StockItem, load_stock
    from .nesting.nest import NestSettings
    from .nesting.parts import nest_objects, parse_angles, parse_rule, squeeze_lines
    from .nesting.stock import use_up
    p = Project.load(a.project)
    objs = [v for s in p.sheets for v in s.vectors]
    step = a.rotation
    rots = [0.0] if step <= 0 else [k * step for k in range(int(round(360 / step)))]
    if a.angles:  # any angles at all
        try:
            rots = parse_angles(a.angles)
        except ValueError:
            print(f"--angles: numbers separated by commas expected, not {a.angles!r}", file=sys.stderr)
            return 2
    layer_rules = {}
    for spec in a.rotate_layer or []:  # LAYER=RULE
        name, _, rule = spec.rpartition("=")
        layer = p.layer_by_name(name)
        if layer is None:
            print(f"--rotate-layer: no layer named {name!r}", file=sys.stderr)
            return 2
        try:
            layer_rules[layer.id] = parse_rule(rule)
        except ValueError as e:
            print(f"--rotate-layer: {e}", file=sys.stderr)
            return 2
    st = NestSettings(a.width, a.height, a.margin, a.spacing, rots, a.gravity, iterations=a.iterations,
                      time_limit=a.time_limit, max_points=a.max_points, squeeze_angle=a.squeeze_angle,
                      squeeze_margin=a.squeeze_margin,
                      squeeze_step=a.squeeze_step)
    stock = [] if a.no_project_stock else list(p.stock)
    if a.stock:
        stock += load_stock(a.stock)
    for spec in a.leftover or []:  # WxH or WxH:quantity
        size, _, qty = spec.partition(":")
        w, _, h = size.lower().partition("x")
        stock.append(StockItem(f"Leftover {w}x{h}", float(w), float(h), None, int(qty or 1), True))
    plates = bool(stock) or a.no_new_sheets
    out = nest_objects(p, objs, st, {o.id: a.quantity for o in objs}, [p.sheets[0]],
                       stock=stock if plates else None, use_sheets=[] if plates else None,
                       new_sheets=not a.no_new_sheets, layer_rules=layer_rules, tilt_step=a.tilt_step,
                       choose=(lambda strict, better: True) if a.apply_squeeze else None,
                       group_inner=a.group_inner, fill_holes=a.fill_holes)
    if plates and not a.keep_stock:
        p.stock = use_up(p.stock, out.plates)
    p.save(a.output)
    print(f"placed {out.placed}, unplaced {out.unplaced}, plates {out.result.sheets}")
    for sh, u in zip(out.sheets, out.result.utilization):
        print(f"  {sh.name}: {sh.width:g} x {sh.height:g}{' (shape)' if sh.outline else ''}, {u * 100:.1f}% used")
    if out.squeezed:
        print("squeezed in:")
        print("\n".join(squeeze_lines(out.parts, out.squeezed)))
    if out.suggestion is not None:
        print(f"suggestion: squeezing parts in would leave {len(out.suggestion.unplaced)} unplaced on "
              f"{out.suggestion.sheets} plate(s); use --apply-squeeze to take it:")
        print("\n".join(squeeze_lines(out.parts, out.suggestion.squeezed)))
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="iyicam", description="iyiCAM command line tools")
    ap.add_argument("--no-plugins", action="store_true", help="do not load user plugins")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("posts", help="list post-processors")
    sub.add_parser("plugins", help="list plugins")
    s = sub.add_parser("info", help="summarise a project")
    s.add_argument("project")
    s = sub.add_parser("gcode", help="calculate toolpaths and write G-code")
    s.add_argument("project")
    s.add_argument("--post", default="mach3")
    s.add_argument("--sheet", default="1", help="sheet number or 'all'")
    s.add_argument("-o", "--output")
    s = sub.add_parser("import", help="create a project from a DXF / SVG file")
    s.add_argument("file")
    s.add_argument("-o", "--output", required=True)
    s.add_argument("--thickness", type=float, default=18.0)
    s = sub.add_parser("nest", help="nest every vector of a project")
    s.add_argument("project")
    s.add_argument("-o", "--output", required=True)
    s.add_argument("--width", type=float, default=2800.0)
    s.add_argument("--height", type=float, default=2100.0)
    s.add_argument("--margin", type=float, default=10.0)
    s.add_argument("--spacing", type=float, default=8.0)
    s.add_argument("--rotation", type=float, default=90.0, help="rotation step in degrees (0 = none)")
    s.add_argument("--gravity", default="left", choices=["left", "bottom", "compact", "reach_x", "reach_y", "diag"])
    s.add_argument("--iterations", type=int, default=8,
                   help="layouts tried (1 = one quick pass); the one using the least material is kept")
    s.add_argument("--time-limit", type=float, default=0.0,
                   help="seconds after which no further layout is tried (0 = no limit)")
    s.add_argument("--group-inner", action="store_true",
                   help="inner pieces (göbek) stay with their part instead of being nested on their own")
    s.add_argument("--fill-holes", action="store_true", help="place smaller parts in the holes of parts")
    s.add_argument("--quantity", type=int, default=1)
    s.add_argument("--leftover", action="append", metavar="WxH[:N]",
                   help="a rectangular leftover plate to use first (N of them); can be repeated")
    s.add_argument("--stock", metavar="FILE", help="stock file (JSON) with leftovers of any shape")
    s.add_argument("--no-project-stock", action="store_true", help="ignore the stock saved in the project")
    s.add_argument("--no-new-sheets", action="store_true", help="only use the leftovers and stock plates")
    s.add_argument("--keep-stock", action="store_true", help="do not take the plates used out of the project's stock")
    s.add_argument("--max-points", type=int, default=40,
                   help="points per part outline; more are simplified (much faster, a little looser), 0 = no limit")
    s.add_argument("--angles", metavar="LIST", help="any angles in degrees, e.g. 0,22.5,90 (instead of --rotation)")
    s.add_argument("--rotate-layer", action="append", metavar="LAYER=RULE",
                   help="how far the parts of a layer may turn: none, flip, limit:X, limit-flip:X, step:X, "
                        "default; can be repeated")
    s.add_argument("--tilt-step", type=float, default=5.0, help="step of limited rotation (limit:X), degrees")
    s.add_argument("--squeeze-angle", type=float, default=0.0,
                   help="parts that do not fit may be tilted by up to this many degrees (0 = no)")
    s.add_argument("--squeeze-margin", type=float, default=0.0,
                   help="... and come this many mm closer to the edge than the border gap (0 = no)")
    s.add_argument("--squeeze-step", type=float, default=2.0, help="tilt step of squeezing, degrees")
    s.add_argument("--apply-squeeze", action="store_true",
                   help="take the squeezed layout when it is better (else it is only suggested)")
    a = ap.parse_args(argv)
    _setup(not a.no_plugins)
    try:
        return {"posts": cmd_posts, "plugins": cmd_plugins, "info": cmd_info, "gcode": cmd_gcode,
                "import": cmd_import, "nest": cmd_nest}[a.cmd](a)
    except FileNotFoundError as exc:
        print(f"error: {exc.filename or exc} was not found", file=sys.stderr)
    except (OSError, ValueError, KeyError, TypeError, IndexError) as exc:   # a bad file or a bad number
        print(f"error: {type(exc).__name__}: {exc}", file=sys.stderr)
    except Exception as exc:  # noqa: BLE001  (e.g. a damaged DXF: ezdxf's own errors)
        print(f"error: {type(exc).__name__}: {exc}", file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())

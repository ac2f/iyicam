"""The operations of the service: JSON in, JSON out, no window, no files that the caller has to name.

Every function takes the whole project as plain data (the ``.icam`` JSON) and returns plain data.  Nothing is
remembered between calls: the project lives where the editing is done (the phone), and this runs where the
heavy work and the files are (the computer).  What the GUI's ``Controller`` does after a calculation (keep the
cut order, move smart start points into the vectors and calculate again) is done the same way here, or the
G-code would differ from the one the desktop program writes.
"""
from __future__ import annotations

import inspect
import json
import math
import os
import tempfile
import threading
import time
from pathlib import Path
from typing import Callable

from .. import __version__
from ..builtins import ensure_registered, load_user_posts
from ..cam.job import ComputeCancelled, compute_all, sheet_toolpaths
from ..cam.moves import PathStats
from ..cam.simulate import auto_resolution, shade, simulate
from ..core import paths, vectorops
from ..core.commands import CommandContext, CommandError, run_command
from ..core.document import LAYER_COLORS, Project, project_from_dict, toolpath_to_dict, vector_to_dict
from ..core.geom import dist
from ..core.params import resolve
from ..core.registry import REGISTRY
from ..core.stock import StockItem
from ..core.tooling import ToolLibrary
from ..formats.cdr import CdrError
from ..formats.report import ImportReport, run_importer
from ..plugins.manager import PluginManager
from .errors import JobCancelled, ServiceError
from .jobs import JobControl
from .png import encode_png
from .preview import toolpath_preview
from .wire import b64d, b64e, jsonable, param_to_dict, tool_values

API_VERSION = 1

_ready_lock = threading.Lock()
_ready = False
PLUGIN_ERRORS: list[dict] = []


def ensure_ready() -> None:
    """Register the built-in features, the user's posts and (unless ``IYICAM_SERVICE_PLUGINS=0``) the plugins."""
    global _ready
    with _ready_lock:
        if _ready:
            return
        ensure_registered(REGISTRY)
        try:
            load_user_posts(REGISTRY)
        except Exception as exc:  # noqa: BLE001  (a damaged user post must not stop the service)
            PLUGIN_ERRORS.append({"plugin": "user-posts", "error": f"{type(exc).__name__}: {exc}"})
        if os.environ.get("IYICAM_SERVICE_PLUGINS", "1") != "0":
            for info in PluginManager(REGISTRY, [paths.plugins_dir()]).load_all():
                if info.error:
                    PLUGIN_ERRORS.append({"plugin": info.key, "error": str(info.error)})
        _ready = True


# ---------------------------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------------------------

def load_project(d) -> Project:
    if not isinstance(d, dict):
        raise ServiceError("bad_request", "the project must be given as an object (the .icam JSON)")
    try:
        return project_from_dict(d)
    except (ValueError, KeyError, TypeError, IndexError) as exc:
        raise ServiceError("invalid_project", f"not a valid iyiCAM project: {type(exc).__name__}: {exc}") from exc


def _sheet_of(project: Project, sid) -> "object":
    if sid is None:
        return project.sheets[0]
    s = project.sheet(str(sid))
    if s is None:
        raise ServiceError("sheet_not_found", "there is no such sheet in the project", sheet=str(sid))
    return s


def _report_dict(r: ImportReport) -> dict:
    return {"read": dict(r.read), "skipped": dict(r.skipped), "errors": list(r.errors), "notes": list(r.notes),
            "summary": r.summary(), "clean": r.clean, "via": r.via}


def _stem(project: Project, given=None) -> str:
    name = str(given or project.name or "job")
    keep = "".join(c if (c.isalnum() or c in "-_ .") else "_" for c in name).strip(" .") or "job"
    return keep[:80]


def _file(name: str, mime: str, data: bytes) -> dict:
    return {"name": name, "mime": mime, "data_b64": b64e(data)}


def _num(payload: dict, key: str, default: float, lo: float | None = None, hi: float | None = None) -> float:
    v = payload.get(key, default)
    try:
        v = float(v)
    except (TypeError, ValueError) as exc:
        raise ServiceError("bad_request", f"'{key}' must be a number", field=key) from exc
    if not math.isfinite(v) or (lo is not None and v < lo) or (hi is not None and v > hi):
        raise ServiceError("bad_request", f"'{key}' is out of range", field=key)
    return v


# ---------------------------------------------------------------------------------------------
# schema, ping
# ---------------------------------------------------------------------------------------------

def op_ping(_payload=None) -> dict:
    ensure_ready()
    return {"ok": True, "api": API_VERSION, "iyicam": __version__, "plugin_errors": list(PLUGIN_ERRORS)}


def op_schema(_payload=None) -> dict:
    """Everything an interface needs to build its forms: toolpath types, vector commands, posts, file formats,
    the tools of the user."""
    ensure_ready()
    from ..cam import sequence as seq
    from ..core.tooling import TOOL_TYPES
    from ..formats.dxf import DXF_VERSIONS
    from ..post.engine import ORIGINS
    operations = [{"id": c.id, "label": c.label, "description": c.description,
                   "tool_types": list(c.tool_types) if c.tool_types else None, "needs_closed": c.needs_closed,
                   "extra_tool_params": list(c.extra_tool_params), "params": [param_to_dict(p) for p in c.params()]}
                  for c in REGISTRY.operations.values()]
    commands = [{"id": c.id, "label": c.label, "menu": c.menu, "min_selection": c.min_selection,
                 "shortcut": c.shortcut, "tooltip": c.tooltip, "source": c.source,
                 "params": [param_to_dict(p) for p in c.params]} for c in REGISTRY.commands.values()]
    from ..post.editing import builtin_ids, is_user_post
    posts = [{"id": p.id, "name": p.name, "extension": p.extension, "description": p.description,
              "builtin": p.id in builtin_ids(), "editable": is_user_post(p)}
             for p in sorted(REGISTRY.posts.values(), key=lambda p: p.id)]
    importers = [{"extensions": list(i.extensions), "label": i.label} for i in REGISTRY.importers.values()]
    importers.insert(0, {"extensions": ["icam"], "label": "iyiCAM project"})
    try:
        from ..formats import cdr as cdr_format
        cdr_info = cdr_format.describe()
    except Exception:  # noqa: BLE001  (a broken converter lookup must not take the whole schema down)
        cdr_info = {"available": False, "converters": [], "install": "", "reads": ""}
    exporters = [{"extension": e.extension, "label": e.label, "params": [param_to_dict(p) for p in e.params]}
                 for e in REGISTRY.exporters.values()]
    exporters.insert(0, {"extension": "icam", "label": "iyiCAM project", "params": []})
    try:
        tools = [t.to_dict() for t in ToolLibrary.load_or_default(paths.tools_file()).tools]
    except Exception:  # noqa: BLE001  (a damaged tool file: the interface can still work with its own tools)
        tools = []
    return {"api": API_VERSION, "iyicam": __version__, "operations": operations, "commands": commands,
            "posts": posts, "importers": importers, "exporters": exporters, "tools": tools,
            "cdr": cdr_info, "layer_colors": list(LAYER_COLORS), "origins": sorted(ORIGINS), "dxf_versions": [list(v) for v in DXF_VERSIONS],
            "tool_types": [[k, v] for k, v in TOOL_TYPES.items()],
            "sequence": {"strategies": [list(x) for x in seq.STRATEGIES], "groupings": [list(x) for x in seq.GROUPINGS],
                         "directions": [list(x) for x in seq.DIRECTIONS],
                         "defaults": jsonable({k: v for k, v in seq.DEFAULTS.items() if k not in ("order", "directions")})},
            "plugin_errors": list(PLUGIN_ERRORS)}


def op_validate(payload: dict) -> dict:
    """Read a project and give it back as this program would write it (damaged parts are refused, missing
    ones filled with defaults)."""
    p = load_project(payload.get("project"))
    return {"ok": True, "project": p.to_dict(),
            "counts": {"sheets": len(p.sheets), "layers": len(p.layers), "toolpaths": len(p.toolpaths),
                       "vectors": sum(len(s.vectors) for s in p.sheets)}}


# ---------------------------------------------------------------------------------------------
# import / export
# ---------------------------------------------------------------------------------------------

def op_import(payload: dict) -> dict:
    """Bytes of a file in (``name``, ``data_b64``), a project out.  With ``project`` given the drawing is added to
    it (to ``sheet``) instead of starting a new project."""
    ensure_ready()
    name = Path(str(payload.get("name") or "file")).name
    ext = Path(name).suffix.lower().lstrip(".")
    raw = payload.get("data")                       # (the bytes themselves when the caller is in this process: no base64 round trip)
    data = bytes(raw) if isinstance(raw, (bytes, bytearray)) else b64d(payload.get("data_b64"))
    if not data:
        raise ServiceError("empty_file", "the file is empty", name=name)
    report = ImportReport()
    if ext == "icam":
        try:
            d = json.loads(data.decode("utf-8-sig"))
        except (UnicodeDecodeError, ValueError) as exc:
            raise ServiceError("invalid_project", f"not a valid iyiCAM project: {exc}", name=name) from exc
        return {"project": load_project(d).to_dict(), "report": _report_dict(report), "added": []}
    imp = REGISTRY.importer_for(ext)
    if imp is None:
        raise ServiceError("unsupported_file", f"there is no importer for .{ext} files", extension=ext)
    existing = payload.get("project")
    if existing:
        project = load_project(existing)
        sheet = _sheet_of(project, payload.get("sheet"))
    else:
        project = Project(Path(name).stem)
        project.material.thickness = _num(payload, "thickness", 18.0, 0.01, 1000.0)
        sheet = project.sheets[0]
    try:
        accepted = set(inspect.signature(imp.func).parameters)
    except (TypeError, ValueError):
        accepted = set()
    options = {k: v for k, v in (payload.get("options") or {}).items()
               if k in accepted and k not in ("path", "project", "sheet", "report")}
    with tempfile.TemporaryDirectory(prefix="iyicam-import-") as tmp:
        src = Path(tmp) / f"import.{ext}"
        src.write_bytes(data)
        try:
            objs = run_importer(imp.func, str(src), project, sheet, report, **options)
        except ServiceError:
            raise
        except CdrError as exc:        # (the words are the person's: no "CdrError:" in front of them)
            raise ServiceError(exc.code, str(exc), name=name, **exc.params) from exc
        except Exception as exc:  # noqa: BLE001  (a damaged file: ezdxf and friends raise their own kinds)
            raise ServiceError("import_failed", f"{name} could not be read: {type(exc).__name__}: {exc}",
                               name=name) from exc
    if not existing:      # a new project: the sheet is made to hold the drawing (as the command line does)
        bb = sheet.bbox()
        if not bb.empty:
            sheet.width = max(sheet.width, bb.xmax + 10)
            sheet.height = max(sheet.height, bb.ymax + 10)
    return {"project": project.to_dict(), "report": _report_dict(report), "added": [o.id for o in objs]}


def op_export(payload: dict) -> dict:
    """A project out as files: ``format`` ``dxf`` / ``svg`` / ``icam`` (or whatever a plugin exports), one file per
    sheet when ``sheet`` is ``"all"``, only ``selection`` when given."""
    ensure_ready()
    project = load_project(payload.get("project"))
    fmt = str(payload.get("format") or "dxf").lower().lstrip(".")
    stem = _stem(project, payload.get("file_name"))
    if fmt == "icam":
        text = json.dumps(project.to_dict(), separators=(",", ":")).encode("utf-8")
        return {"files": [_file(f"{stem}.icam", "application/json", text)]}
    exp = REGISTRY.exporters.get(fmt)
    if exp is None:
        raise ServiceError("unsupported_format", f"there is no exporter for '{fmt}'", format=fmt,
                           known=sorted(REGISTRY.exporters) + ["icam"])
    which = payload.get("sheet", "all" if len(project.sheets) > 1 else None)
    sheets = list(project.sheets) if which == "all" else [_sheet_of(project, which)]
    names = {p.name for p in exp.params}
    options = {k: v for k, v in resolve(exp.params, payload.get("options")).items() if k in names}
    selection = payload.get("selection")
    files = []
    with tempfile.TemporaryDirectory(prefix="iyicam-export-") as tmp:
        for i, s in enumerate(sheets, 1):
            objs = s.pick(selection) if selection else None
            out = Path(tmp) / f"{stem}{'' if len(sheets) == 1 else f'_{i}'}.{fmt}"
            try:
                exp.func(str(out), project, s, objs, **options)
            except Exception as exc:  # noqa: BLE001
                raise ServiceError("export_failed", f"export failed: {type(exc).__name__}: {exc}", format=fmt) from exc
            mime = {"dxf": "application/dxf", "svg": "image/svg+xml"}.get(fmt, "application/octet-stream")
            files.append(_file(out.name, mime, out.read_bytes()))
    return {"files": files}


# ---------------------------------------------------------------------------------------------
# a vector command
# ---------------------------------------------------------------------------------------------

def op_command(payload: dict) -> dict:
    """Run one of the program's vector commands (the same ones the menus run) on a selection."""
    ensure_ready()
    project = load_project(payload.get("project"))
    sheet = _sheet_of(project, payload.get("sheet"))
    cmd = REGISTRY.commands.get(str(payload.get("command")))
    if cmd is None:
        raise ServiceError("unknown_command", "there is no such command", command=str(payload.get("command")))
    selection = [str(i) for i in (payload.get("selection") or [])]
    ctx = CommandContext(project, sheet, selection, payload.get("active_layer") or None)
    try:
        run_command(cmd, ctx, tool_values(cmd.params, payload.get("values")))
    except CommandError as exc:       # nothing is returned: the caller's project stays as it was
        raise ServiceError("command_failed", str(exc), command=cmd.id) from exc
    except (ValueError, TypeError, KeyError, ArithmeticError) as exc:
        raise ServiceError("command_failed", f"{cmd.label}: {type(exc).__name__}: {exc}", command=cmd.id) from exc
    keep = ctx.new_selection if ctx.new_selection is not None else selection
    return {"ok": True, "project": project.to_dict(), "selection": [v.id for v in sheet.pick(keep)],
            "messages": list(ctx.messages), "changed": bool(ctx.changed)}


# ---------------------------------------------------------------------------------------------
# toolpaths: calculation, G-code, simulation
# ---------------------------------------------------------------------------------------------

def _store_orders(project: Project, res: dict) -> None:
    from ..cam.job import sheets_for
    for t in project.toolpaths:
        orders = [res[(t.id, s.id)].order for s in sheets_for(project, t)
                  if (t.id, s.id) in res and res[(t.id, s.id)].order is not None]
        if orders:
            t.sequence["order"] = [k for o in orders for k in o]


def _apply_start_updates(project: Project, res: dict, forced: set[str]) -> set[str]:
    """Smart start points are written into the vectors (a new node where needed); the ids that changed."""
    updates: dict = {}
    for (tid, _), r in res.items():
        t = project.toolpath(tid)
        if t is not None and r.start_updates and (t.sequence.get("smart_starts") or tid in forced):
            updates.update(r.start_updates)
    changed: set[str] = set()
    for key, q in updates.items():
        vid, _, ci = key.rpartition(":")
        found = project.find_vector(vid)
        if found is None or not ci.lstrip("-").isdigit():
            continue
        o, ci = found[1], int(ci)
        if not 0 <= ci < len(o.contours) or ci in o.locked_starts:
            continue
        c = vectorops.set_start_point(o.contours[ci], q)
        if dist(c.start, o.contours[ci].start) > 1e-3:
            o.contours[ci] = c
            changed.add(vid)
    return changed


def calculate(project: Project, cancel: Callable[[], bool] | None = None, progress: Callable | None = None,
              only: set[str] | None = None, force: set[str] | None = None) -> tuple[dict, set[str]]:
    """What the GUI does after every change: calculate, keep the cut order, move smart start points into the
    vectors and calculate again when any moved.  Returns ``(results, ids of the vectors that changed)``."""
    events: list = []
    res = compute_all(project, REGISTRY, only, None, force or None, cancel=cancel, progress=progress, events=events)
    for hook, args in events:
        REGISTRY.emit(hook, *args)
    _store_orders(project, res)
    changed = _apply_start_updates(project, res, force or set())
    if changed:
        events = []
        res = compute_all(project, REGISTRY, only, None, None, cancel=cancel, progress=progress, events=events)
        for hook, args in events:
            REGISTRY.emit(hook, *args)
        _store_orders(project, res)
    return res, changed


def _only_sheets(project: Project, ids) -> None:
    """A throw-away project of just these sheets (a toolpath on another sheet is then not even calculated)."""
    if ids is None or ids == "all":
        return
    want = {str(i) for i in (ids if isinstance(ids, list) else [ids])}
    keep = [s for s in project.sheets if s.id in want]
    if not keep:
        raise ServiceError("sheet_not_found", "there is no such sheet in the project", sheet=sorted(want))
    project.sheets = keep


def _stats_of(project: Project, tps) -> PathStats:
    st = PathStats()
    for tp in tps:
        st = st.merge(tp.stats(project.material.rapid_rate))
    return st


def _stats_dict(st: PathStats) -> dict:
    has = st.feed_length > 0
    return {"time_s": round(st.time_s, 2), "feed_length": round(st.feed_length, 2),
            "rapid_length": round(st.rapid_length, 2), "zmin": round(st.zmin, 3) if has else 0.0,
            "bbox": [round(st.xmin, 3), round(st.ymin, 3), round(st.xmax, 3), round(st.ymax, 3)] if has else None}


def _ctl_hooks(ctl: JobControl | None, label: str):
    if ctl is None:
        return None, None

    def progress(k, n, name=""):
        ctl.progress(k, n, f"{label} {name}".strip())
    return ctl.cancelled, progress


def op_compute(payload: dict, ctl: JobControl | None = None) -> dict:
    """Calculate the toolpaths.  Returns what each toolpath did on each sheet (warnings, time, where it cuts as
    polylines) and what changed in the project itself (start points moved into the vectors, the cut order)."""
    ensure_ready()
    project = load_project(payload.get("project"))
    _only_sheets(project, payload.get("sheets"))
    cancel, prog = _ctl_hooks(ctl, "Calculating")
    only = {str(i) for i in payload["toolpaths"]} if payload.get("toolpaths") else None
    force = {str(i) for i in payload.get("optimise_starts") or []}
    t0 = time.perf_counter()
    try:
        res, changed = calculate(project, cancel, (lambda k, n, name: prog(k, n, name)) if prog else None, only, force)
    except ComputeCancelled as exc:
        raise JobCancelled() from exc
    want_preview = payload.get("preview", True)
    items = []
    for (tid, sid), r in res.items():
        t, sheet = project.toolpath(tid), project.sheet(sid)
        step = float(payload.get("preview_step") or min(5.0, max(0.1, max(sheet.width, sheet.height) / 2000.0)))
        items.append({"toolpath": tid, "sheet": sid, "name": t.name, "op": t.op, "enabled": t.enabled,
                      "error": r.error, "warnings": [w[:2000] for w in r.warnings],
                      "stats": _stats_dict(_stats_of(project, r.toolpaths)), "order": r.order,
                      "preview": toolpath_preview(r.toolpaths, step) if want_preview and t.enabled else None})
    totals = {s.id: round(_stats_of(project, sheet_toolpaths(project, res, s)).time_s, 2) for s in project.sheets}
    patch = {"vectors": {vid: vector_to_dict(v) for vid in changed
                         for _s, v in [project.find_vector(vid)] if v is not None},
             "sequences": {t.id: toolpath_to_dict(t)["sequence"] for t in project.toolpaths}}
    return {"results": items, "patch": jsonable(patch), "totals": totals,
            "seconds": round(time.perf_counter() - t0, 2)}


def op_gcode(payload: dict, ctl: JobControl | None = None) -> dict:
    """G-code for the sheets: one file per sheet (and per tool for a post that splits by tool)."""
    ensure_ready()
    from ..post.export import post_toolpaths
    project = load_project(payload.get("project"))
    post = _draft_post(payload.get("post_draft")) if payload.get("post_draft") else \
        REGISTRY.posts.get(str(payload.get("post") or ""))
    if post is None:
        raise ServiceError("unknown_post", "there is no such post-processor", post=str(payload.get("post")),
                           known=sorted(REGISTRY.posts))
    _only_sheets(project, payload.get("sheets"))
    cancel, prog = _ctl_hooks(ctl, "Calculating")
    try:
        res, _ = calculate(project, cancel, (lambda k, n, name: prog(k, n, name)) if prog else None)
    except ComputeCancelled as exc:
        raise JobCancelled() from exc
    errors = [f"{project.toolpath(t).name}: {r.error}" for (t, _s), r in res.items() if r.error]
    stem = _stem(project, payload.get("file_name"))
    program = int(_num(payload, "program_number", 1000, 0, 999999))
    files, warnings = [], []
    many = len(project.sheets) > 1
    for i, sheet in enumerate(project.sheets, 1):
        tps = sheet_toolpaths(project, res, sheet)
        if not tps:
            continue
        base = f"{stem}_{i}" if many else stem
        for tp in tps:
            warnings += [w for w in tp.warnings if w not in warnings]
        for suffix, text in post_toolpaths(project, sheet, tps, post, base, program):
            files.append({"name": f"{base}{suffix}.{post.extension}", "mime": "text/plain", "text": text,
                          "sheet": sheet.id})
    return {"files": files, "errors": errors, "warnings": [w[:2000] for w in warnings], "ok": bool(files)}


# ---------------------------------------------------------------------------------------------
# post-processors: list, make, change, delete, try; and what a line of G-code says
# ---------------------------------------------------------------------------------------------

def _draft_post(draft):
    """The post of a draft (nothing is written), or ``invalid_post`` with what is wrong."""
    from ..post.editing import PostEditError, build_post
    try:
        return build_post(draft)
    except PostEditError as exc:
        raise ServiceError("invalid_post", str(exc)) from exc


def _posts_answer() -> dict:
    from ..builtins import USER_POST_ERRORS
    from ..post import editing, guide
    return {"posts": [editing.info(p) for p in sorted(REGISTRY.posts.values(), key=lambda p: p.name.lower())],
            "errors": [{"file": Path(f).name, "message": m} for f, m in sorted(USER_POST_ERRORS.items())],
            "specs": editing.specs(), "glossary": {lang: guide.glossary(lang) for lang in guide.LANGS}}


def _reload_user_posts() -> None:
    REGISTRY.remove_source("user-posts")
    load_user_posts(REGISTRY)


def op_posts(_payload=None) -> dict:
    """Every post with its options and blocks (and whether it is the user's own, so it can be changed), a
    description of each option, block and variable for building a form, the files of the user that cannot be
    loaded, and the glossary of G-code words."""
    ensure_ready()
    return _posts_answer()


def op_post_save(payload: dict) -> dict:
    """Save a draft (``post``: ``{id?, name, description, extension, extends, options, blocks}``) as a post of the
    user, ``~/.iyicam/posts/<id>.toml``; only what differs from the post it extends is written.  Answers like
    ``posts`` plus ``saved`` (the id), ``warnings`` and ``file``."""
    ensure_ready()
    from ..post.editing import PostEditError, save_user_post
    try:
        target, warnings = save_user_post(payload.get("post"), REGISTRY.posts)
    except PostEditError as exc:
        raise ServiceError("invalid_post", str(exc)) from exc
    except OSError as exc:
        raise ServiceError("os_error", f"the post could not be written: {exc}") from exc
    _reload_user_posts()
    from ..builtins import USER_POST_ERRORS
    bad = USER_POST_ERRORS.get(str(target))
    if bad:                                              # (it was written but cannot be read back: say so)
        raise ServiceError("invalid_post", f"the post was written but cannot be loaded: {bad}", file=target.name)
    from ..post.loader import read_definition
    return {**_posts_answer(), "saved": read_definition(target).get("id", target.stem), "file": target.name,
            "warnings": warnings}


def op_post_delete(payload: dict) -> dict:
    """Delete a post of the user (``id``); the built-in posts and those of plugins cannot be deleted."""
    ensure_ready()
    from ..post.editing import PostEditError, delete_user_post
    pid = str(payload.get("id") or "")
    post = REGISTRY.posts.get(pid)
    if post is None:
        raise ServiceError("unknown_post", "there is no such post-processor", post=pid)
    try:
        delete_user_post(post)
    except PostEditError as exc:
        raise ServiceError("invalid_post", str(exc)) from exc
    except OSError as exc:
        raise ServiceError("os_error", f"the post could not be deleted: {exc}") from exc
    _reload_user_posts()
    return {**_posts_answer(), "deleted": pid}


def op_post_preview(payload: dict) -> dict:
    """The G-code a draft (``post``) writes for a small sample job with arcs, a drilling and two tools: quick, so
    it can be asked after every change of a form."""
    ensure_ready()
    from ..post.editing import PostEditError, clean_draft, sample_files
    post = _draft_post(payload.get("post"))
    try:
        warnings = clean_draft(payload.get("post"))[1]
        files = sample_files(post)
    except PostEditError as exc:
        raise ServiceError("invalid_post", str(exc)) from exc
    except KeyError as exc:                              # (an unknown {variable} in a block)
        raise ServiceError("post_failed", str(exc.args[0] if exc.args else exc)) from exc
    except (ValueError, TypeError, IndexError) as exc:   # (a block the formatter cannot read, ...)
        raise ServiceError("post_failed", f"{type(exc).__name__}: {exc}") from exc
    return {"files": [{"name": f"sample{sfx}.{post.extension}", "text": text} for sfx, text in files],
            "warnings": warnings, "arcs": post.options["arcs"], "ijk": post.options["ijk"]}


def op_gcode_explain(payload: dict) -> dict:
    """What one line of a G-code file says.  ``lines``: the lines of the file up to the one asked (at most 5000: the
    position the tool has when the line starts is followed through them), ``index``: the line, ``lang``: ``en`` or
    ``tr``, ``post``: the id of the post that wrote it (it knows the units and how I J are counted)."""
    from ..post import guide
    ensure_ready()
    lines = payload.get("lines")
    if not isinstance(lines, list) or not lines or len(lines) > 5000 or not all(isinstance(x, str) for x in lines):
        raise ServiceError("bad_request", "'lines' must be a list of 1 to 5000 lines")
    index = payload.get("index", len(lines) - 1)
    if isinstance(index, bool) or not isinstance(index, int) or not 0 <= index < len(lines):
        raise ServiceError("bad_request", "'index' must be the number of a line (from 0)", field="index")
    lines = [x[:300] for x in lines]
    post = REGISTRY.posts.get(str(payload.get("post") or ""))
    inch = None if post is None else post.options["units"] == "inch"
    absolute = bool(post and post.options["ijk"] == "absolute")
    return guide.explain(lines, index, str(payload.get("lang") or "en"), inch=inch, arc_absolute=absolute)


def op_simulate(payload: dict, ctl: JobControl | None = None) -> dict:
    """Material removal of one sheet as a PNG (the picture the desktop program shows)."""
    ensure_ready()
    project = load_project(payload.get("project"))
    sheet = _sheet_of(project, payload.get("sheet"))
    project.sheets = [sheet]
    cancel, prog = _ctl_hooks(ctl, "Calculating")
    try:
        res, _ = calculate(project, cancel, (lambda k, n, name: prog(k, n, name)) if prog else None)
    except ComputeCancelled as exc:
        raise JobCancelled() from exc
    tps = sheet_toolpaths(project, res, sheet)
    if not tps:
        raise ServiceError("no_toolpaths", "there are no calculated toolpaths on this sheet")
    cells = int(_num(payload, "cells", 800_000, 10_000, 4_000_000))
    resolution = auto_resolution(sheet.width, sheet.height, cells) if not payload.get("resolution") else \
        _num(payload, "resolution", 0.5, 0.02, 50.0)

    def sim_progress(k, n):
        if ctl is not None:
            ctl.progress(k, n, "Simulating")
    hm = simulate(tps, sheet.width, sheet.height, project.sheet_thickness(sheet), resolution, sim_progress,
                  ctl.cancelled if ctl else None, sheet.outline)
    if ctl is not None and ctl.cancelled():
        raise JobCancelled()
    rgb = shade(hm)
    h, w, _ = rgb.shape
    return {"png_b64": b64e(encode_png(rgb)), "width_px": int(w), "height_px": int(h), "resolution": hm.res,
            "extent": [hm.x0 - hm.res / 2, hm.y0 - hm.res / 2, hm.x0 + (w - 0.5) * hm.res, hm.y0 + (h - 0.5) * hm.res],
            "sheet": sheet.id, "time_s": round(_stats_of(project, tps).time_s, 2)}


# ---------------------------------------------------------------------------------------------
# nesting
# ---------------------------------------------------------------------------------------------

def op_nest(payload: dict, ctl: JobControl | None = None) -> dict:
    """Nest the vectors of a project (the options are those of ``iyicam nest``; defaults are the same)."""
    ensure_ready()
    from ..nesting.nest import NestCancelled, NestSettings
    from ..nesting.parts import nest_objects, parse_angles, parse_rule, squeeze_lines
    from ..nesting.stock import use_up
    p = load_project(payload.get("project"))
    s = payload.get("settings") or {}
    if not isinstance(s, dict):
        raise ServiceError("bad_request", "'settings' must be an object")
    ids = payload.get("objects")
    objs = [v for sh in p.sheets for v in sh.vectors if not ids or v.id in set(map(str, ids))]
    if not objs:
        raise ServiceError("nothing_to_nest", "there are no vectors to nest")
    step = _num(s, "rotation", 90.0, 0.0, 360.0)
    rots = [0.0] if step <= 0 else [k * step for k in range(int(round(360 / step)))]
    if s.get("angles"):
        try:
            rots = parse_angles(str(s["angles"]))
        except ValueError as exc:
            raise ServiceError("bad_request", "'angles': numbers separated by commas expected", field="angles") from exc
    rules = {}
    for lname, rule in (s.get("rotate_layers") or {}).items():
        layer = p.layer_by_name(str(lname))
        if layer is None:
            raise ServiceError("bad_request", f"no layer named {lname!r}", field="rotate_layers")
        try:
            rules[layer.id] = parse_rule(str(rule))
        except ValueError as exc:
            raise ServiceError("bad_request", f"rotate_layers: {exc}", field="rotate_layers") from exc
    from ..nesting.nest import ARRANGEMENTS, CORNERS, GRAVITIES
    for key, allowed, default in (("gravity", GRAVITIES, "left"), ("arrange", ARRANGEMENTS, "free"), ("corner", CORNERS, "bl")):
        if str(s.get(key, default)) not in allowed:
            raise ServiceError("bad_request", f"'{key}' must be one of: {', '.join(allowed)}", field=key, allowed=list(allowed))
    st = NestSettings(_num(s, "width", 2800.0, 1.0), _num(s, "height", 2100.0, 1.0), _num(s, "margin", 10.0, 0.0),
                      _num(s, "spacing", 8.0, 0.0), rots, str(s.get("gravity", "left")),
                      gravity_only=bool(s.get("gravity_only")), arrange=str(s.get("arrange", "free")),
                      corner=str(s.get("corner", "bl")),
                      iterations=int(_num(s, "iterations", 8, 1, 200)), time_limit=_num(s, "time_limit", 0.0, 0.0),
                      max_points=int(_num(s, "max_points", 40, 0)), squeeze_angle=_num(s, "squeeze_angle", 0.0, 0.0),
                      squeeze_margin=_num(s, "squeeze_margin", 0.0, 0.0), squeeze_step=_num(s, "squeeze_step", 2.0, 0.1))
    stock = [] if s.get("no_project_stock") else list(p.stock)
    for d in s.get("stock") or []:
        stock.append(StockItem.from_dict(d))
    for spec in s.get("leftovers") or []:        # "WxH" or "WxH:quantity"
        size, _, qty = str(spec).partition(":")
        w, _, h = size.lower().partition("x")
        try:
            stock.append(StockItem(f"Leftover {w}x{h}", float(w), float(h), None, int(qty or 1), True))
        except ValueError as exc:
            raise ServiceError("bad_request", f"leftover {spec!r}: WxH or WxH:N expected", field="leftovers") from exc
    no_new = bool(s.get("no_new_sheets"))
    plates = bool(stock) or no_new
    qty = int(_num(s, "quantity", 1, 1, 10000))
    quantities = {o.id: qty for o in objs}
    quantities.update({str(k): int(v) for k, v in (payload.get("quantities") or {}).items() if str(k) in quantities})
    cancel = ctl.cancelled if ctl else None
    progress = (lambda k, n: ctl.progress(k, n, "Nesting")) if ctl else None
    try:
        out = nest_objects(p, objs, st, quantities, [p.sheets[0]], progress=progress, cancel=cancel,
                           stock=stock if plates else None, use_sheets=[] if plates else None,
                           new_sheets=not no_new, layer_rules=rules, tilt_step=_num(s, "tilt_step", 5.0, 0.1),
                           choose=(lambda strict, better: True) if s.get("apply_squeeze") else None,
                           group_inner=bool(s.get("group_inner")), fill_holes=bool(s.get("fill_holes")))
    except NestCancelled as exc:
        raise JobCancelled() from exc
    if plates and not s.get("keep_stock"):
        p.stock = use_up(p.stock, out.plates)
    return {"project": p.to_dict(), "placed": out.placed, "unplaced": out.unplaced, "plates": out.result.sheets,
            "sheets": [{"id": sh.id, "name": sh.name, "width": sh.width, "height": sh.height,
                        "shape": bool(sh.outline), "utilization": round(u, 4)}
                       for sh, u in zip(out.sheets, out.result.utilization)],
            "squeezed": squeeze_lines(out.parts, out.squeezed) if out.squeezed else [],
            "suggestion": ({"unplaced": len(out.suggestion.unplaced), "plates": out.suggestion.sheets,
                            "lines": squeeze_lines(out.parts, out.suggestion.squeezed)}
                           if out.suggestion is not None else None)}

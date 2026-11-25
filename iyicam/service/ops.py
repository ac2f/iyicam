"""The operations of the service: JSON in, JSON out, no window, no files that the caller has to name.

Every function takes the whole project as plain data (the ``.icam`` JSON) and returns plain data.  Nothing is
remembered between calls: the project lives where the editing is done (the phone), and this runs where the
heavy work and the files are (the computer).  What the GUI's ``Controller`` does after a calculation (keep the
cut order, move smart start points into the vectors and calculate again) is done the same way here, or the
G-code would differ from the one the desktop program writes.
"""
from __future__ import annotations

import base64
import hashlib
import inspect
import json
import math
import os
import tempfile
import threading
import time
from collections import OrderedDict
from pathlib import Path
from typing import Callable

from .. import __version__
from ..builtins import ensure_registered, load_user_posts
from ..cam.job import ComputeCancelled, compute_all, sheet_toolpaths, sheets_for, shown_on
from ..cam.moves import PathStats
from ..cam.simulate import auto_resolution, shade, simulate
from ..core import paths, vectorops
from ..core.commands import CommandContext, CommandError, run_command
from ..core.document import LAYER_COLORS, Project, project_from_dict, toolpath_to_dict, vector_to_dict
from ..core.geom import dist
from ..core.params import resolve
from ..core.registry import REGISTRY
from ..core.stock import StockItem
from ..core.tooling import Tool, ToolLibrary
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
            from .pluginhost import PLUGINS
            for info in PLUGINS.load_all():              # (what the page switched off stays off)
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


_RESERVED = {"CON", "PRN", "AUX", "NUL"} | {f"COM{i}" for i in range(1, 10)} | {f"LPT{i}" for i in range(1, 10)}


def _stem(project: Project, given=None) -> str:
    name = str(given or project.name or "job")
    keep = "".join(c if (c.isalnum() or c in "-_ .") else "_" for c in name).strip(" .") or "job"
    if keep.split(".")[0].upper() in _RESERVED:           # (CON, NUL ... are not file names on Windows)
        keep = "_" + keep
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

def op_plugins(_payload=None) -> dict:
    """The plugins of this engine: ``{plugins: [{key, name, version, author, description, enabled, loaded, error, kind,
    removable, provides}], install, plugins_on, folder, limits}``; ``provides`` lists what each gave (commands, operations,
    importers, exporters, posts, gcode_filters, hooks)."""
    ensure_ready()
    from .pluginhost import PLUGINS
    return PLUGINS.listing()


def op_plugin_install(payload: dict) -> dict:
    """Install a plugin: ``name`` (``my_plugin.py`` or ``my_plugin.zip``) and ``data_b64``; it is loaded at once (``enable``
    false: only written). The answer is that of ``plugins`` with ``installed`` and the ``plugin``."""
    ensure_ready()
    from .pluginhost import PLUGINS
    raw = payload.get("data_b64")
    if not isinstance(raw, str):
        raise ServiceError("bad_request", "'data_b64' (the file) is needed", field="data_b64")
    try:
        data = base64.b64decode(raw, validate=False)
    except (ValueError, TypeError) as exc:
        raise ServiceError("bad_request", "'data_b64' is not base64", field="data_b64") from exc
    out = PLUGINS.install(payload.get("name"), data)
    PLUGIN_ERRORS[:] = [e for e in PLUGIN_ERRORS if e.get("plugin") != out["installed"]]
    if out["plugin"]["error"]:
        PLUGIN_ERRORS.append({"plugin": out["installed"], "error": out["plugin"]["error"]})
    return out


def op_plugin_set(payload: dict) -> dict:
    """``key`` and ``action`` (``enable``, ``disable``, ``reload``, ``remove``); answers like ``plugins``."""
    ensure_ready()
    from .pluginhost import PLUGINS
    return PLUGINS.set(payload.get("key"), payload.get("action"))


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
    from ..cam import tp_defaults
    mine = tp_defaults.load()                 # (the person's defaults of new toolpaths: how far a through cut goes below)
    operations = [{"id": c.id, "label": c.label, "description": c.description,
                   "tool_types": list(c.tool_types) if c.tool_types else None, "needs_closed": c.needs_closed,
                   "extra_tool_params": list(c.extra_tool_params),
                   "params": [param_to_dict(p) for p in tp_defaults.apply(c.id, c.params(), mine)]}
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
            "sequence": {"strategies": [list(x) for x in seq.grouped_strategies()], "groupings": [list(x) for x in seq.GROUPINGS],
                         "directions": [list(x) for x in seq.DIRECTIONS],
                         # (a toolpath made on the page starts with smart start points: the start points go where the cut is held
                         # until it closes; the engine's own default stays off for projects and calls that never said)
                         "defaults": jsonable({**{k: v for k, v in seq.DEFAULTS.items() if k not in ("order", "directions")},
                                               "smart_starts": True})},
            "naming": {"variables": __import__("iyicam.post.naming", fromlist=["x"]).VARIABLES,
                       "segment_variables": __import__("iyicam.post.naming", fromlist=["x"]).SEGMENT_VARIABLES,
                       "default_segment": __import__("iyicam.post.naming", fromlist=["x"]).DEFAULT_SEGMENT,
                       "default_segment_sep": __import__("iyicam.post.naming", fromlist=["x"]).DEFAULT_SEGMENT_SEP,
                       "default_parts": ["{project_name}"], "default_sep": "_"},
            "plugin_errors": list(PLUGIN_ERRORS), "new_project": _new_project_info()}


def _new_project_info() -> dict:
    from ..core import presets
    return {"presets": [[n, w, h] for n, w, h in presets.sizes(None, REGISTRY)],
            "templates": [{"id": t["id"], "label": t["label"], "description": t["description"],
                           "params": [param_to_dict(p) for p in t["params"]]} for t in presets.templates(REGISTRY)
                                         if t["id"] != presets.BLANK]}   # (the page draws on a sheet: it starts with one)


def op_new_project(payload: dict) -> dict:
    """A new project: blank with a size (``width``, ``height``, ``thickness``), or started by a template (``template`` and its
    ``values``): the ways to start are in ``schema.new_project`` (Blank, and what plugins brought)."""
    ensure_ready()
    from ..core import presets
    w, h, th = _num(payload, "width", 600.0, 1.0, 1e6), _num(payload, "height", 400.0, 1.0, 1e6), _num(payload, "thickness", 18.0, 0.01, 1e4)
    tid = str(payload.get("template") or "")
    values = payload.get("values") if isinstance(payload.get("values"), dict) else {}
    if tid and tid != presets.BLANK and tid not in REGISTRY.new_project_templates:
        raise ServiceError("bad_request", f"there is no way to start a project called {tid!r}", field="template",
                           known=sorted(REGISTRY.new_project_templates))
    try:
        p = presets.create(w, h, th, tid, values, REGISTRY)
    except Exception as exc:                          # (a plugin's template that fails: said, nothing is made)
        raise ServiceError("template_failed", f"{type(exc).__name__}: {exc}", template=tid) from exc
    p.name = str(payload.get("name") or "") or p.name
    return {"project": p.to_dict()}


# -- how the toolpaths are listed on a sheet -------------------------------------------------------------------------
def op_toolpath_view(payload: dict) -> dict:
    """The toolpaths as the Toolpaths panel lists them on a sheet: ``names`` (id -> the name with its ``{variables}`` put in,
    for this sheet) and ``shown`` (the ids of the toolpaths that belong on it: one kept to a group of sheets or to some
    sheets is not listed on the others, ``cam.job.shown_on``)."""
    ensure_ready()
    from ..post.naming import toolpath_name
    project = load_project(payload.get("project"))
    sheet = _sheet_of(project, payload.get("sheet"))
    return {"sheet": sheet.id, "names": {t.id: toolpath_name(project, t, sheet) for t in project.toolpaths},
            "shown": [t.id for t in project.toolpaths if shown_on(project, t, sheet)]}


# -- the person's defaults of new toolpaths --------------------------------------------------------------------------
def op_tp_defaults(payload: dict) -> dict:
    """The person's defaults of new toolpaths (``cam.tp_defaults``): ``values`` given are kept first. Answers ``values``
    and ``keys`` ([key, label, built-in default])."""
    ensure_ready()
    from ..cam import tp_defaults
    vals = payload.get("values")
    if vals is not None:
        if not isinstance(vals, dict):
            raise ServiceError("bad_request", "'values' must be an object", field="values")
        try:
            tp_defaults.save(vals)
        except ValueError as exc:
            raise ServiceError("bad_request", str(exc), field="values") from exc
    return {"values": tp_defaults.load(), "keys": [list(k) for k in tp_defaults.KEYS]}


# -- the tool database of the user ------------------------------------------------------------------------------------
def op_tools_save(payload: dict) -> dict:
    """Keep the tool database of the user (``tools``: the tools as ``schema`` gives them; ``groups``: the groups in their
    order, empty ones too). The whole database is replaced: what an interface leaves out is removed."""
    ensure_ready()
    raw = payload.get("tools")
    if not isinstance(raw, list) or not all(isinstance(d, dict) for d in raw):
        raise ServiceError("bad_request", "'tools' must be a list of tools", field="tools")
    try:
        tools = [Tool.from_dict(d) for d in raw]
    except Exception as exc:  # noqa: BLE001
        raise ServiceError("bad_request", f"a tool cannot be read: {exc}", field="tools") from exc
    groups = [str(g) for g in payload.get("groups") or [] if isinstance(g, str)]
    lib = ToolLibrary(tools, paths.tools_file(), groups)
    lib.save()
    return {"tools": [t.to_dict() for t in lib.tools], "groups": lib.groups()}


# -- presets of toolpaths -----------------------------------------------------------------------------------------
def _tp_of(project: Project, d) -> "object":
    from ..core.document import toolpath_from_dict
    if not isinstance(d, dict):
        raise ServiceError("bad_request", "'toolpath' must be a toolpath object", field="toolpath")
    try:
        return toolpath_from_dict({"id": "preset", "name": "", "op": "profile", "tool": Tool().to_dict(), **d})
    except (KeyError, TypeError, ValueError) as exc:
        raise ServiceError("bad_request", f"the toolpath cannot be read: {exc}", field="toolpath") from exc


def op_tp_presets(payload: dict) -> dict:
    """The presets of toolpaths, the variables their sums may use, and (with ``toolpath``) what a preset can keep of that one."""
    ensure_ready()
    from ..cam import tp_presets
    out = {"presets": tp_presets.load(), "variables": [{"name": n, "text": {"en": en, "tr": tr}} for n, en, tr in tp_presets.VARIABLES]}
    if payload.get("toolpath") is not None:
        project = load_project(payload.get("project")) if payload.get("project") else Project()
        t = _tp_of(project, payload.get("toolpath"))
        op = REGISTRY.operations.get(t.op)
        out["fields"] = tp_presets.fields(t, op.params() if op else [])
    return out


def op_tp_preset_save(payload: dict) -> dict:
    ensure_ready()
    from ..cam import tp_presets
    try:
        kept = tp_presets.save(payload.get("preset"))
    except tp_presets.PresetError as exc:
        raise ServiceError("bad_request", str(exc), field="preset") from exc
    return {"saved": kept, "presets": tp_presets.load()}


def op_tp_preset_delete(payload: dict) -> dict:
    ensure_ready()
    from ..cam import tp_presets
    if not tp_presets.delete(str(payload.get("id") or "")):
        raise ServiceError("not_found", "there is no such preset", id=str(payload.get("id")))
    return {"presets": tp_presets.load()}


def op_tp_preset_apply(payload: dict) -> dict:
    """A toolpath with a preset put on it: the sums of the preset worked out with this project and sheet."""
    ensure_ready()
    from ..cam import tp_presets
    from ..core.document import toolpath_to_dict
    project = load_project(payload.get("project"))
    pre = next((p for p in tp_presets.load() if p.get("id") == payload.get("id")), None)
    if pre is None:
        raise ServiceError("not_found", "there is no such preset", id=str(payload.get("id")))
    t = _tp_of(project, payload.get("toolpath"))
    if pre.get("op") and not payload.get("toolpath", {}).get("op"):
        t.op = pre["op"]
    sheet = _sheet_of(project, payload.get("sheet")) if payload.get("sheet") else project.sheets[0]
    op = REGISTRY.operations.get(t.op)
    notes = tp_presets.apply(pre, t, project, sheet, op.params() if op else [])
    return {"toolpath": jsonable(toolpath_to_dict(t)), "notes": notes}


def op_calibrate_time(payload: dict) -> dict:
    """The acceleration (and correction) at which the calculated toolpaths of a sheet take the time that was measured
    (``seconds``). The project is as it is: the toolpaths of the last calculation are used (calculated now if there is none)."""
    ensure_ready()
    from ..cam import timing
    project = load_project(payload.get("project"))
    seconds = _num(payload, "seconds", 0.0, 0.0, 1e7)
    if seconds <= 0:
        raise ServiceError("bad_request", "'seconds' must be the measured time, more than 0", field="seconds")
    res = _calculated_get(project)
    if res is None:
        res, _ = calculate(project)
    sheet = _sheet_of(project, payload.get("sheet")) if payload.get("sheet") else project.sheets[0]
    tps = [tp for tp in sheet_toolpaths(project, res, sheet) if tp.moves]
    if not tps:
        raise ServiceError("nothing_to_time", "there are no calculated toolpaths on this sheet")
    mc = timing.calibrate_all([tp.moves for tp in tps], seconds, project.material.machine())
    before = sum(tp.stats(project.material.machine()).time_s for tp in tps)
    after = sum(tp.stats(mc).time_s for tp in tps)
    return {"accel": round(mc.accel, 1), "time_scale": round(mc.scale, 4), "estimate_before": round(before, 1),
            "estimate_after": round(after, 1), "measured": seconds}


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
            "messages": list(ctx.messages), "changed": bool(ctx.changed),
            "result": jsonable(ctx.result())}          # (what the command handed back: report sections to show, warnings, data)


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


# What was calculated last (a few projects): "Write G-code" after "Calculate" writes those toolpaths, it does not calculate them
# again. The key is the project as it is after the calculation (its start points moved, its orders kept), without what does not
# change a toolpath (switched on or off, names, groups), so a page that applied the answer and asks for the G-code gets a hit.
_CALCULATED: "OrderedDict[str, dict]" = OrderedDict()
_CALC_LOCK = threading.Lock()


def _calc_key(project: Project) -> str:
    d = project.to_dict()
    for t in d.get("toolpaths", []):
        for k in ("enabled", "name", "group"):
            t.pop(k, None)
    d.pop("name", None)
    d.pop("metadata", None)
    return hashlib.sha1(json.dumps(d, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def _calculated_get(project: Project):
    with _CALC_LOCK:
        key = _calc_key(project)
        res = _CALCULATED.get(key)
        if res is not None:
            _CALCULATED.move_to_end(key)
        return res


def _calculated_put(project: Project, res: dict) -> None:
    with _CALC_LOCK:
        _CALCULATED[_calc_key(project)] = res
        while len(_CALCULATED) > 3:
            _CALCULATED.popitem(last=False)


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
        st = st.merge(tp.stats(project.material.machine()))
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
    if only is None and payload.get("sheets") in (None, "all"):
        _calculated_put(project, res)                     # (what "Write G-code" writes if the project stays as it is)
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


def _only_toolpaths(project, ids, group) -> None:
    """Save only some toolpaths: the ones named in ``ids`` (a list of ids), or those of the group ``group``; the others
    are switched off in this copy of the project.  Neither given: the ones that are switched on, as always."""
    if group:
        want = {t.id for t in project.toolpaths if t.group == str(group)}
        if not want:
            raise ServiceError("unknown_group", "there is no such group of toolpaths", group=str(group))
    elif ids is not None:
        if not isinstance(ids, (list, tuple)) or not all(isinstance(i, str) for i in ids):
            raise ServiceError("bad_request", "toolpaths must be a list of toolpath ids")
        want = set(ids)
        known = {t.id for t in project.toolpaths}
        if not want or not want <= known:
            raise ServiceError("unknown_toolpath", "some of the toolpaths are not in the project",
                               unknown=sorted(want - known)[:10])
    else:
        return
    for t in project.toolpaths:
        t.enabled = t.id in want


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
    cached = _calculated_get(project) if payload.get("recalculate") is not True else None
    _only_sheets(project, payload.get("sheets"))
    _only_toolpaths(project, payload.get("toolpaths"), payload.get("group"))
    cancel, prog = _ctl_hooks(ctl, "Calculating")
    reused = False
    if cached is not None and all((t.id, s.id) in cached for t in project.toolpaths if t.enabled
                                  for s in sheets_for(project, t)):
        res, reused = cached, True                        # the toolpaths as they were calculated: not calculated again
        for (tid, _sid), r in res.items():                # (a toolpath that was renamed since is written under its name now)
            d = project.toolpath(tid)
            for tp in r.toolpaths:
                if d is not None:
                    tp.name = d.name
    else:
        try:
            res, _ = calculate(project, cancel, (lambda k, n, name: prog(k, n, name)) if prog else None)
        except ComputeCancelled as exc:
            raise JobCancelled() from exc
    errors = [f"{project.toolpath(t).name}: {r.error}" for (t, _s), r in res.items() if r.error]
    stem = _stem(project, payload.get("file_name"))
    program = int(_num(payload, "program_number", 1000, 0, 999999))
    parts = sep = None
    if payload.get("name_parts") is not None:            # the name as the person wrote it: prefixes with variables
        from ..post import naming
        try:
            parts, sep = naming.check(payload.get("name_parts"), payload.get("name_sep", naming.DEFAULT_SEP))
        except naming.NamingError as exc:
            raise ServiceError("bad_request", str(exc), field="name_parts") from exc
    files, warnings = [], []
    many = len(project.sheets) > 1
    groups = {t.group for t in project.toolpaths if t.enabled and t.group}
    for i, sheet in enumerate(project.sheets, 1):
        tps = sheet_toolpaths(project, res, sheet)
        if not tps:
            continue
        if payload.get("together", True) is not False:          # (the toolpaths of a group are cut one after the other)
            from ..post import naming as _nm
            tps = _nm.group_order(project, tps)
        for tp in tps:
            warnings += [w for w in tp.warnings if w not in warnings]
        if parts is not None:
            from ..post import naming
            from ..post.export import named_files
            by_group = payload.get("revision_by_group") if isinstance(payload.get("revision_by_group"), dict) else {}
            rev = by_group.get(sheet.group, payload.get("revision")) if sheet.group else payload.get("revision")
            grp = str(payload.get("group") or (next(iter(groups)) if len(groups) == 1 else ""))

            def stem_of(g, sheet=sheet, i=i, rev=rev, grp=grp):
                vals = naming.values_for(project, sheet, i, len(project.sheets), post.name, grp, g, rev,
                                         segment=str(payload.get("segment_template") or naming.DEFAULT_SEGMENT),
                                         segment_sep=(str(payload["segment_sep"])[:4] if isinstance(payload.get("segment_sep"), str)
                                                      else naming.DEFAULT_SEGMENT_SEP),
                                         stack=payload.get("stack", True) is not False)
                name = naming.render(parts, vals, sep)
                # (a job with several sheets needs a number in the name or the files would overwrite each other)
                return f"{name}_{i}" if many and not naming.uses(parts, "sheet_number", "sheet_name") else name
            for fstem, text in named_files(project, sheet, tps, post, stem_of, program):
                files.append({"name": f"{fstem}.{post.extension}", "mime": "text/plain", "text": text, "sheet": sheet.id})
            continue
        base = f"{stem}_{i}" if many else stem
        for suffix, text in post_toolpaths(project, sheet, tps, post, base, program):
            files.append({"name": f"{base}{suffix}.{post.extension}", "mime": "text/plain", "text": text,
                          "sheet": sheet.id})
    seen: dict[str, int] = {}                             # (two files with one name would overwrite each other when saved)
    for f in files:
        key = f["name"].lower()
        if key in seen:
            seen[key] += 1
            stem, dot, ext = f["name"].rpartition(".")
            f["name"] = f"{stem}_{seen[key]}{dot}{ext}" if dot else f"{f['name']}_{seen[key]}"
        else:
            seen[key] = 1
    return {"files": files, "errors": errors, "warnings": [w[:2000] for w in warnings], "ok": bool(files), "reused": reused}


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

def op_leftover(payload: dict) -> dict:
    """The material left on a sheet once its parts are cut out, as plates for the next job: ``{"plates": [...]}`` with
    ``outline`` polygons that go into the ``plates`` of a nesting as they are. ``project``, ``sheet`` (an id; the first
    sheet by default), ``clearance`` (how far the cuts reach round the parts, 3), ``min_width`` (30), ``min_area`` (2500)."""
    ensure_ready()
    from ..nesting.stock import leftover_of_sheet
    p = load_project(payload.get("project"))
    sid = str(payload.get("sheet") or "")
    sheet = next((x for x in p.sheets if x.id == sid), p.sheets[0] if p.sheets else None)
    if sheet is None:
        raise ServiceError("bad_request", "the project has no sheet")
    items = leftover_of_sheet(sheet, _num(payload, "clearance", 3.0, 0.0), _num(payload, "min_width", 30.0, 0.0),
                              _num(payload, "min_area", 2500.0, 0.0))
    return {"sheet": sheet.id, "plates": [{**it.to_dict(), "id": None} for it in items]}


PLATE_KEYS = ("width", "height", "radius", "diameter", "hole", "sides", "notch_width", "notch_height", "points", "holes")


def plate_from_dict(d, k: int = 0) -> StockItem:
    """A plate of a nesting from its description: ``{"width": 1000, "height": 2000, "quantity": 3}`` is a rectangle;
    ``"shape"`` (rect, rounded, circle, ellipse, polygon, l, points) and its sizes (see
    :func:`iyicam.core.stock.shaped_outline`) make any other; ``name``, ``thickness``, ``leftover`` and ``outline``
    (the polygons of any shape) are optional."""
    from ..core.stock import shaped_plate
    where = f"plates[{k}]"
    if not isinstance(d, dict):
        raise ServiceError("bad_request", f"{where} must be an object", field="plates")
    try:
        thickness = None if d.get("thickness") in (None, "", 0) else float(d["thickness"])
        qty = int(d.get("quantity", 1))
        if qty < 0:
            raise ValueError("the quantity cannot be negative")
        if d.get("outline"):              # any shape: its polygons (the first is the outline, the rest holes)
            from ..core.stock import normalized
            w, h, outline = normalized([[(float(x), float(y)) for x, y in poly] for poly in d["outline"]])
            return StockItem(str(d.get("name") or f"Plate {k + 1}"), w, h, outline, qty, bool(d.get("leftover", False)),
                             thickness)
        sizes = {key: d[key] for key in PLATE_KEYS if d.get(key) is not None}
        return shaped_plate(str(d.get("shape") or "rect"), str(d.get("name") or ""), qty, bool(d.get("leftover", False)),
                            thickness, **sizes)
    except (ValueError, TypeError, KeyError) as exc:
        raise ServiceError("bad_request", f"{where}: {exc}", field="plates") from exc


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
                      max_points=int(_num(s, "max_points", 300, 0)), squeeze_angle=_num(s, "squeeze_angle", 0.0, 0.0),
                      squeeze_margin=_num(s, "squeeze_margin", 0.0, 0.0), squeeze_step=_num(s, "squeeze_step", 2.0, 0.1),
                      min_leftover=_num(s, "min_leftover", 0.0, 0.0))   # (leftover narrower than this is not used)
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
    listed = s.get("plates") or []                # the sizes of this job, with their quantities, in the order they are used
    if not isinstance(listed, list):
        raise ServiceError("bad_request", "'plates' must be a list", field="plates")
    for k, d in enumerate(listed):
        stock.append(plate_from_dict(d, k))
    no_new = bool(s.get("no_new_sheets"))
    reserve = s.get("reserve")                    # what does not fit the plates above goes on sheets of this size
    if reserve is False:
        no_new = True
    elif reserve is not None:
        if not isinstance(reserve, dict):
            raise ServiceError("bad_request", "'reserve' must be an object (width, height, quantity) or false", field="reserve")
        st.sheet_width = _num(reserve, "width", st.sheet_width, 1.0)
        st.sheet_height = _num(reserve, "height", st.sheet_height, 1.0)
        st.reserve_quantity = int(_num(reserve, "quantity", 0, 0, 100000))
    else:
        st.reserve_quantity = int(_num(s, "reserve_quantity", 0, 0, 100000))
    order = str(s.get("plate_order") or ("listed" if listed else "leftovers"))
    from ..nesting.nest import STOCK_ORDERS
    if order not in STOCK_ORDERS:
        raise ServiceError("bad_request", f"'plate_order' must be one of: {', '.join(STOCK_ORDERS)}", field="plate_order",
                           allowed=list(STOCK_ORDERS))
    st.stock_order = order
    place_on = str(s.get("place_on") or "new")    # new: the plates and sheets above; current / all: the free area of sheets first
    if place_on not in ("new", "current", "all", "group"):
        raise ServiceError("bad_request", "'place_on' must be one of: new, current, all, group", field="place_on")
    sheet_id = str(payload.get("sheet") or "")
    group = str(s.get("group") or payload.get("group") or "")
    if place_on == "group" and not any(x.group == group for x in p.sheets):
        raise ServiceError("unknown_group", "there is no such group of sheets", group=group)
    on_sheets = ([next((x for x in p.sheets if x.id == sheet_id), p.sheets[0])] if place_on == "current"
                 else list(p.sheets) if place_on == "all" else [x for x in p.sheets if x.group == group]
                 if place_on == "group" else [])
    plates = bool(stock) or no_new or bool(on_sheets)
    qty = int(_num(s, "quantity", 1, 1, 10000))
    quantities = {o.id: qty for o in objs}
    quantities.update({str(k): int(v) for k, v in (payload.get("quantities") or {}).items() if str(k) in quantities})
    cancel = ctl.cancelled if ctl else None
    progress = (lambda k, n: ctl.progress(k, n, "Nesting")) if ctl else None
    try:
        out = nest_objects(p, objs, st, quantities, [] if on_sheets else [p.sheets[0]], progress=progress, cancel=cancel,
                           stock=stock if plates else None, use_sheets=on_sheets if plates else None,
                           new_sheets=not no_new, layer_rules=rules, tilt_step=_num(s, "tilt_step", 5.0, 0.1),
                           choose=(lambda strict, better: True) if s.get("apply_squeeze") else None,
                           group_inner=bool(s.get("group_inner")), fill_holes=bool(s.get("fill_holes")))
    except NestCancelled as exc:
        raise JobCancelled() from exc
    if plates and not s.get("keep_stock"):
        p.stock = use_up(p.stock, out.plates)
    return {"project": p.to_dict(), "placed": out.placed, "unplaced": out.unplaced, "plates": out.result.sheets,
            "sheets": [{"id": sh.id, "name": sh.name, "width": sh.width, "height": sh.height,
                        "shape": bool(sh.outline), "utilization": round(u, 4),
                        "reserve": made is None}                     # (a sheet of the reserve size: the plates were full)
                       for sh, u, made in zip(out.sheets, out.result.utilization, out.plates)],
            "squeezed": squeeze_lines(out.parts, out.squeezed) if out.squeezed else [],
            "suggestion": ({"unplaced": len(out.suggestion.unplaced), "plates": out.suggestion.sheets,
                            "lines": squeeze_lines(out.parts, out.suggestion.squeezed)}
                           if out.suggestion is not None else None)}


# -- the operations of the features of the web app (each module adds its op_* by name) ------------------------------
from .ops_draw import *  # noqa: E402,F401,F403
from .ops_job import *  # noqa: E402,F401,F403

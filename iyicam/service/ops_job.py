"""JSON operations of the job features of the web app (see ops.py: JSON in, JSON out, ServiceError for what is wrong).

Every `op_*` here is an operation of ops.py too (ops.py takes them in), so the browser's engine and any other interface
call them by name. Keep the work in iyicam.core / cam / nesting / post: an op only translates JSON to it and back.

What is here (the desktop does each of these in ``iyicam/gui``; the work itself is in the engine):

* the job report and the backplot of a G-code file (``job_report``, ``backplot``);
* packages of toolpaths (``packages``, ``package_*``) and toolpath templates (``template_save``, ``template_load``);
* the stock of plates (``stock_plate``, ``stock_from_vectors``, ``stock_leftover``, ``stock_export``, ``stock_import``);
* the cut order panel (``cut_order``, ``cut_edit``) and the 2D simulation (``sim_timeline``);
* the Python console (``console_run``, ``console_reset``)."""
from __future__ import annotations

import code
import contextlib
import io
import json
import traceback

from .errors import JobCancelled, ServiceError, as_dict, as_list
from .jobs import JobControl

__all__ = ["op_job_report", "op_backplot", "op_packages", "op_package_save", "op_package_delete", "op_package_from_toolpaths",
           "op_package_info", "op_package_items", "op_package_apply", "op_package_import", "op_package_export", "op_template_save",
           "op_template_load", "op_stock_plate", "op_stock_from_vectors", "op_stock_leftover", "op_stock_export",
           "op_stock_import", "op_cut_order", "op_cut_edit", "op_sim_timeline", "op_console_run", "op_console_reset"]


def _ops():
    from . import ops          # (imported when used: ops.py imports this module at its end)
    return ops


def _calc(project, only=None, force=None, ctl=None):
    """``ops.calculate`` with the hooks of a job control; what the toolpaths of ``only`` (ids) are on every sheet."""
    ops = _ops()
    from ..cam.job import ComputeCancelled
    cancel, prog = ops._ctl_hooks(ctl, "Calculating")
    try:
        return ops.calculate(project, cancel, (lambda k, n, name: prog(k, n, name)) if prog else None, only, force)
    except ComputeCancelled as exc:
        raise JobCancelled() from exc


def _text(payload: dict, key: str = "text") -> str:
    """Text given as ``text`` or as ``data_b64`` (a file)."""
    if isinstance(payload.get(key), str):
        return payload[key]
    if isinstance(payload.get("data_b64"), str):
        from .wire import b64d
        return b64d(payload["data_b64"]).decode("utf-8", "replace")
    raise ServiceError("bad_request", f"'{key}' (the text of the file) is needed", field=key)


# ---------------------------------------------------------------------------------------------
# job report, backplot
# ---------------------------------------------------------------------------------------------

def op_job_report(payload: dict, ctl: JobControl | None = None) -> dict:
    """The job report (an HTML page: every sheet's toolpaths with tool, depth, cut length and time). ``project``,
    ``inch`` (lengths in inches). The toolpaths are calculated first. ``{name, html}``."""
    ops = _ops()
    ops.ensure_ready()
    from ..cam.jobreport import job_report_html
    project = ops.load_project(payload.get("project"))
    res, _ = _calc(project, ctl=ctl)
    return {"name": f"{ops._stem(project)}_report.html", "html": job_report_html(project, res, bool(payload.get("inch")))}


def op_backplot(payload: dict) -> dict:
    """Show an existing G-code file over a sheet. ``text`` (the file), ``project`` and ``sheet`` (the material setup maps the
    coordinates of the file to the sheet), optional ``step`` (mm: how fine the drawing of the moves is). Answers the
    drawing of the moves like ``compute`` does (``preview``), ``tools`` and the ``stats`` (cut length, time)."""
    ops = _ops()
    ops.ensure_ready()
    from ..cam.gcode_reader import ReadOptions, read_gcode
    from ..core import paths
    from ..core.tooling import ToolLibrary
    from ..post.engine import ORIGINS
    from .preview import toolpath_preview
    project = ops.load_project(payload.get("project"))
    sheet = ops._sheet_of(project, payload.get("sheet"))
    text = _text(payload)
    m = project.material
    fx, fy = ORIGINS.get(m.xy_origin, (0.0, 0.0))
    tools = {t.number: t for t in ToolLibrary.load_or_default(paths.tools_file()).tools}
    opt = ReadOptions(fx * sheet.width, fy * sheet.height, project.sheet_thickness(sheet) if m.z_zero == "bottom" else 0.0, tools)
    tps = read_gcode(text, opt)
    step = float(payload.get("step") or min(5.0, max(0.1, max(sheet.width, sheet.height) / 2000.0)))
    return {"tools": [{"name": tp.name, "number": tp.tool.number} for tp in tps], "stats": ops._stats_dict(ops._stats_of(project, tps)),
            "preview": toolpath_preview(tps, step), "sheet": sheet.id}


# ---------------------------------------------------------------------------------------------
# packages of toolpaths and toolpath templates
# ---------------------------------------------------------------------------------------------

def _package_error(exc) -> ServiceError:
    return ServiceError("bad_request", str(exc), field="package")


def _package_of(payload: dict) -> dict:
    """The package named by ``id`` (a kept one) or given as ``package``."""
    from ..cam import tp_packages as P
    try:
        if isinstance(payload.get("package"), dict):
            return P.check(payload["package"])
    except P.PackageError as exc:
        raise _package_error(exc) from exc
    pkg = P.get(str(payload.get("id") or ""))
    if pkg is None:
        raise ServiceError("not_found", "there is no such package", id=str(payload.get("id")))
    return pkg


def _packages_answer() -> dict:
    from ..cam import tp_packages as P
    from ..core.registry import REGISTRY
    from ..post import naming
    return {"packages": P.load(), "variables": [{"name": n, "text": {"en": en, "tr": tr}} for n, en, tr in P.VARIABLES],
            "toolpath_variables": naming.TOOLPATH_VARIABLES, "ask_default": list(P.DEFAULT_ASK), "group_default": P.DEFAULT_GROUP,
            "operations": [{"id": o.id, "label": o.label} for o in REGISTRY.operations.values()]}


def op_packages(_payload=None) -> dict:
    """The packages of toolpaths kept in the user folder, the variables their names may use and the toolpath types."""
    _ops().ensure_ready()
    return _packages_answer()


def op_package_save(payload: dict) -> dict:
    """Keep ``package`` (one with the same id is replaced); or ``packages``: the whole list is replaced (what the
    packages window does when it is saved). Answers like ``packages`` plus ``saved`` (the ids)."""
    _ops().ensure_ready()
    from ..cam import tp_packages as P
    try:
        if isinstance(payload.get("packages"), list):
            kept = [P.check(p) for p in payload["packages"]]
            P._write(kept)
            saved = [p["id"] for p in kept]
        else:
            saved = [P.save(payload.get("package"))["id"]]
    except P.PackageError as exc:
        raise _package_error(exc) from exc
    return {**_packages_answer(), "saved": saved}


def op_package_delete(payload: dict) -> dict:
    _ops().ensure_ready()
    from ..cam import tp_packages as P
    if not P.delete(str(payload.get("id") or "")):
        raise ServiceError("not_found", "there is no such package", id=str(payload.get("id")))
    return _packages_answer()


def op_package_from_toolpaths(payload: dict) -> dict:
    """A package made of toolpaths of the project (``toolpaths``: their ids; none: all of them), named ``name``; it is kept.
    The tool of the first is the package's."""
    ops = _ops()
    ops.ensure_ready()
    from ..cam import tp_packages as P
    from ..core.registry import REGISTRY
    project = ops.load_project(payload.get("project"))
    ids = [str(i) for i in as_list(payload, "toolpaths")]
    tps = [t for t in project.toolpaths if t.id in ids] or list(project.toolpaths)
    try:
        pkg = P.save(P.from_toolpaths(str(payload.get("name") or "").strip(), tps, REGISTRY, project))
    except P.PackageError as exc:
        raise _package_error(exc) from exc
    return {**_packages_answer(), "saved": [pkg["id"]]}


def op_package_info(payload: dict) -> dict:
    """What the window that puts a package on a drawing shows: for every toolpath of the package its type, the settings that
    are asked (as the schema describes a setting, with the package's value as default) and its layers; the name of the group
    it would make on ``sheet`` of ``project``."""
    ops = _ops()
    ops.ensure_ready()
    from ..cam import tp_packages as P
    from ..core.registry import REGISTRY
    from .wire import jsonable, param_to_dict
    pkg = _package_of(payload)
    project = ops.load_project(payload.get("project"))
    sheet = ops._sheet_of(project, payload.get("sheet"))
    items = []
    for it in pkg["items"]:
        op = REGISTRY.operations.get(it["op"])
        items.append({**jsonable(it), "label": op.label if op else it["op"], "available": op is not None,
                      "asked": [param_to_dict(p) for p in P.asked_params(it, REGISTRY)]})
    template = payload.get("group_template")
    group, n = P.group_name(pkg, project, sheet, template if isinstance(template, str) and template else None)
    return {"package": {**jsonable(pkg), "items": items}, "group": group, "n": n}


def op_package_items(payload: dict) -> dict:
    """Toolpaths of the project (``toolpaths``: their ids; none: all of them) as toolpaths of a package whose tool is ``tool``
    (the desktop's "From selected toolpaths"): ``{items}`` to add to a package."""
    ops = _ops()
    ops.ensure_ready()
    from ..cam import tp_packages as P
    from ..core.registry import REGISTRY
    from .wire import jsonable
    project = ops.load_project(payload.get("project"))
    ids = [str(i) for i in as_list(payload, "toolpaths")]
    tps = [t for t in project.toolpaths if t.id in ids] or list(project.toolpaths)
    if not tps:
        raise ServiceError("no_toolpaths", "the project has no toolpaths yet")
    try:
        items = [P.item_from_toolpath(t, payload.get("tool"), op.params() if (op := REGISTRY.operations.get(t.op)) else None, project)
                 for t in tps]
    except (TypeError, ValueError, KeyError, AttributeError) as exc:      # (a tool of the wrong shape)
        raise ServiceError("bad_request", f"the tool of the package cannot be read: {exc}", field="tool") from exc
    return {"items": jsonable(items)}


def op_package_apply(payload: dict) -> dict:
    """The toolpaths of a package on a selection (the desktop's ``PackageApplyDialog`` then ``tp_packages.build``): ``package``
    (or ``id``), ``project``, ``sheet``, ``selection`` ``{mode: "vectors"|"layers", vectors, layers}``, and for this time
    ``tool``, ``values`` / ``names`` / ``item_tools`` / ``item_layers`` (by the number of the toolpath in the package),
    ``skip`` (numbers), ``make_group``, ``group_template``, ``this_sheet``. Answers ``toolpaths`` (as the project keeps them:
    nothing is added to it), ``names`` (as the project will show them) and ``group``."""
    ops = _ops()
    ops.ensure_ready()
    from ..cam import tp_packages as P
    from ..core.document import Selection, toolpath_to_dict
    from ..core.registry import REGISTRY
    from ..core.tooling import Tool
    from ..post import naming
    from .wire import jsonable
    pkg = _package_of(payload)
    project = ops.load_project(payload.get("project"))
    sheet = ops._sheet_of(project, payload.get("sheet"))
    s = as_dict(payload, "selection")
    sel = Selection("layers" if s.get("mode") == "layers" else "vectors", [str(v) for v in s.get("vectors") or []],
                    [str(v) for v in s.get("layers") or []])
    if sel.mode == "vectors" and not sel.vector_ids:
        raise ServiceError("bad_request", "select the vectors the package machines (or choose layers)", field="selection")

    def keyed(d, f=lambda v: v):
        return {int(k): f(v) for k, v in (d or {}).items()}
    try:
        tps, group = P.build(
            pkg, project, sheet, sel, REGISTRY, tool=Tool.from_dict(payload["tool"]) if payload.get("tool") else None,
            values=keyed(payload.get("values")), names=keyed(payload.get("names"), str),
            skip={int(i) for i in as_list(payload, "skip")},
            make_group=payload.get("make_group") if isinstance(payload.get("make_group"), bool) else None,
            group_template=payload.get("group_template") or None,
            this_sheet=payload.get("this_sheet") if isinstance(payload.get("this_sheet"), bool) else None,
            item_tools=keyed(payload.get("item_tools"), Tool.from_dict),
            item_layers=keyed(payload.get("item_layers"), lambda v: [str(x) for x in v]))
    except P.PackageError as exc:
        raise _package_error(exc) from exc
    except (TypeError, ValueError, KeyError) as exc:
        raise ServiceError("bad_request", f"the package cannot be used: {exc}", field="package") from exc
    return {"toolpaths": [jsonable(toolpath_to_dict(t)) for t in tps], "group": group, "package": pkg["name"],
            "names": [naming.toolpath_name(project, t, sheet) for t in tps]}


def op_package_import(payload: dict) -> dict:
    """Packages from a ``.icampkg`` / ``.json`` file (``text`` or ``data_b64``). With ``keep`` (default true) they are added
    to the kept ones (new ids). Answers ``imported`` (the packages) and the kept list."""
    _ops().ensure_ready()
    from ..cam import tp_packages as P
    try:
        data = json.loads(_text(payload))
        got = [P.check({**d, "id": ""}) for d in (data.get("packages") if isinstance(data, dict) else data)]
    except (ValueError, TypeError, AttributeError, P.PackageError) as exc:
        raise ServiceError("bad_request", f"these are not packages of toolpaths: {exc}", field="text") from exc
    if payload.get("keep", True) is not False:
        P._write(P.load() + got)
    return {**_packages_answer(), "imported": got}


def op_package_export(payload: dict) -> dict:
    """A package as the text of a ``.icampkg`` file: ``{name, text}``."""
    _ops().ensure_ready()
    from ..cam import tp_packages as P
    pkg = _package_of(payload)
    return {"name": P.export_name(pkg), "text": json.dumps({"format": "iyicam-toolpath-packages", "packages": [pkg]},
                                                           indent=1, ensure_ascii=False)}


def op_template_save(payload: dict) -> dict:
    """The toolpaths of a project as a template (``.icamtpl``): the layers they machine by name, no vectors. ``{name, text,
    count}``."""
    ops = _ops()
    ops.ensure_ready()
    from ..core.document import toolpath_to_dict
    from .wire import jsonable
    project = ops.load_project(payload.get("project"))
    if not project.toolpaths:
        raise ServiceError("no_toolpaths", "there are no toolpaths to save")
    layers = {layer.id: layer.name for layer in project.layers}
    data = []
    for t in project.toolpaths:
        d = jsonable(toolpath_to_dict(t))
        d["selection"]["layer_names"] = [layers.get(i, "") for i in t.selection.layer_ids]
        d["selection"]["vectors"] = []
        data.append(d)
    return {"name": "toolpaths.icamtpl", "count": len(data),
            "text": json.dumps({"format": "iyicam-toolpaths", "toolpaths": data}, indent=1)}


def op_template_load(payload: dict) -> dict:
    """Toolpaths of a template (``text``) added to ``project`` (its layers are made when it has none of that name); they
    machine those layers on every sheet. ``{project, added}``."""
    ops = _ops()
    ops.ensure_ready()
    from ..core.document import new_id, toolpath_from_dict
    project = ops.load_project(payload.get("project"))
    try:
        data = json.loads(_text(payload))
        if not isinstance(data, dict) or "toolpaths" not in data:
            raise ValueError("this is not a toolpath template")
        added = []
        for d in data.get("toolpaths", []):
            names = d.get("selection", {}).get("layer_names", [])
            t = toolpath_from_dict(d)
            t.id = new_id()
            t.sheet_ids = None
            t.selection.layer_ids = [project.ensure_layer(n).id for n in names if n]
            t.selection.mode = "layers"
            project.toolpaths.append(t)
            added.append(t.id)
    except (ValueError, KeyError, TypeError, AttributeError) as exc:
        raise ServiceError("bad_request", f"the template cannot be read: {exc}", field="text") from exc
    return {"project": project.to_dict(), "added": added}


# ---------------------------------------------------------------------------------------------
# stock of plates
# ---------------------------------------------------------------------------------------------

def op_stock_plate(payload: dict) -> dict:
    """A plate for the stock: ``shape`` (rect, rounded, circle, ellipse, polygon, l) and its sizes, ``name``, ``quantity``,
    ``leftover`` (as ``nest`` takes plates). ``{plate}``."""
    ops = _ops()
    ops.ensure_ready()
    return {"plate": ops.plate_from_dict(payload).to_dict()}


def op_stock_from_vectors(payload: dict) -> dict:
    """Plates drawn as closed vectors: each outline among ``selection`` (vector ids of ``sheet``) is a plate, closed vectors
    inside it are its holes. ``{plates}`` (empty when there is no closed outline)."""
    ops = _ops()
    ops.ensure_ready()
    from ..nesting.stock import stock_from_vectors
    project = ops.load_project(payload.get("project"))
    sheet = ops._sheet_of(project, payload.get("sheet"))
    objs = sheet.pick([str(i) for i in as_list(payload, "selection")])
    items = stock_from_vectors(objs, int(payload.get("quantity", 1)), payload.get("leftover", True) is not False)
    return {"plates": [it.to_dict() for it in items]}


def op_stock_leftover(payload: dict) -> dict:
    """The material left on a sheet once its parts are cut out, as plates (with ids, to go into the stock). ``project``,
    ``sheet``, ``clearance`` (3), ``min_width`` (30), ``min_area`` (2500). ``{plates}``."""
    ops = _ops()
    ops.ensure_ready()
    from ..nesting.stock import leftover_of_sheet
    project = ops.load_project(payload.get("project"))
    sheet = ops._sheet_of(project, payload.get("sheet"))
    items = leftover_of_sheet(sheet, ops._num(payload, "clearance", 3.0, 0.0), ops._num(payload, "min_width", 30.0, 0.0),
                              ops._num(payload, "min_area", 2500.0, 0.0))
    return {"sheet": sheet.id, "plates": [it.to_dict() for it in items]}


def op_stock_export(payload: dict) -> dict:
    """The stock of a project (``project``) as the text of a stock file: ``{name, text}``."""
    ops = _ops()
    ops.ensure_ready()
    from ..core.stock import FORMAT, FORMAT_VERSION, StockItem
    raw = payload.get("plates")
    if raw is None:
        raw = ops.load_project(payload.get("project")).stock
    items = [it if isinstance(it, StockItem) else StockItem.from_dict(it) for it in raw]
    return {"name": "stock.json", "text": json.dumps({"format": FORMAT, "version": FORMAT_VERSION,
                                                       "items": [it.to_dict() for it in items]}, indent=1)}


def op_stock_import(payload: dict) -> dict:
    """Plates from the text of a stock file; they are new entries (new ids). ``{plates}``."""
    _ops().ensure_ready()
    import uuid
    from ..core.stock import FORMAT, StockItem
    try:
        d = json.loads(_text(payload))
        if not isinstance(d, dict) or d.get("format") != FORMAT:
            raise ValueError("not an iyiCAM stock file")
        items = [StockItem.from_dict(x) for x in d.get("items", [])]
    except (ValueError, KeyError, TypeError) as exc:
        raise ServiceError("bad_request", f"the stock file cannot be read: {exc}", field="text") from exc
    for it in items:
        it.id = uuid.uuid4().hex[:12]
    return {"plates": [it.to_dict() for it in items]}


# ---------------------------------------------------------------------------------------------
# the cut order panel
# ---------------------------------------------------------------------------------------------

def _sequenced(project) -> list:
    """Toolpaths that cut vector by vector (profile, engrave, ...): the ones with a cut order."""
    from ..cam.profile import ProfileOp
    from ..core.registry import REGISTRY
    out = []
    for t in project.toolpaths:
        op = REGISTRY.operations.get(t.op)
        if op is not None and issubclass(op, ProfileOp):
            out.append(t)
    return out


def _compute_one(project, tid, force=False, ctl=None):
    from ..cam.job import ComputeCancelled, compute_all
    from ..core.registry import REGISTRY
    ops = _ops()
    cancel, prog = ops._ctl_hooks(ctl, "Calculating")
    try:
        return compute_all(project, REGISTRY, {tid}, None, {tid} if force else None, cancel=cancel,
                           progress=(lambda k, n, name: prog(k, n, name)) if prog else None, events=[])
    except ComputeCancelled as exc:
        raise JobCancelled() from exc


def op_cut_order(payload: dict, ctl: JobControl | None = None) -> dict:
    """What the cut order panel shows of one toolpath on one sheet: ``project``, ``sheet``, ``toolpath`` (an id; empty: only
    the list of toolpaths with a cut order is answered). Calculates that toolpath alone and answers ``cuts`` in machining
    order (each: ``key``, ``closed``, ``direction``, ``quality``, ``place``, ``lines``, ``start``, ``entry``, ``end``),
    ``settings`` (the cut order settings with defaults), ``strategy`` (what Auto chose), ``route`` (mm between the cuts),
    ``toolpaths`` and, with ``options`` true, the choices (strategies, directions, groupings).

    A **preview**: ``changes`` (cut order settings to try instead) and ``smart_starts`` (start points at the best places)
    calculate the toolpath with them without changing anything: ``preview`` says what would change (``reordered``,
    ``route_before``, ``route_after``, ``directions``, ``starts``: the start points that would move) and ``cuts`` are the new
    ones (with ``polylines`` true each has ``poly``, the cut as x, y numbers, to draw it)."""
    ops = _ops()
    ops.ensure_ready()
    from ..cam import cutedit
    from ..core.geom import dist
    from .wire import jsonable
    project = ops.load_project(payload.get("project"))
    sheet = ops._sheet_of(project, payload.get("sheet"))
    out: dict = {"toolpaths": [{"id": t.id, "name": t.name} for t in _sequenced(project)], "sheet": sheet.id}
    if payload.get("options"):
        out["options"] = cutedit.options()
    tid = str(payload.get("toolpath") or "")
    t = project.toolpath(tid)
    if t is None:
        return out
    home = tuple(project.material.home[:2])
    changes = payload.get("changes") if isinstance(payload.get("changes"), dict) else {}
    smart = bool(payload.get("smart_starts"))
    polylines = bool(payload.get("polylines"))
    res = _compute_one(project, tid, ctl=ctl).get((tid, sheet.id))
    before = cutedit.real_cuts(res)
    shown, shown_res = before, res
    if changes or smart:
        old = t.sequence
        t.sequence = {**old, **changes}
        try:
            res2 = _compute_one(project, tid, smart, ctl).get((tid, sheet.id))
        finally:
            t.sequence = old
        shown, shown_res = cutedit.real_cuts(res2), res2
        info = cutedit.summary(before, shown, home)
        starts = []
        if smart and res2 is not None:
            for key, q in res2.start_updates.items():
                got = cutedit.contour_of(project, key)
                if got is not None and got[1] not in got[0].locked_starts and dist(got[2].start, q) > 1e-3:
                    starts.append({"key": key, "from": [round(v, 3) for v in got[2].start], "to": [round(v, 3) for v in q]})
        out["preview"] = {**info, "starts": starts}
        out["settings"] = {k: v for k, v in cutedit.seq.settings({**t.sequence, **changes}).items() if k != "order"}
    else:
        out["settings"] = {k: v for k, v in cutedit.seq.settings(t.sequence).items() if k != "order"}
    out["cuts"] = jsonable(cutedit.listing(shown, home, polylines))
    out["route"] = round(cutedit.cutinfo.route_length(shown, home), 2)
    out["strategy"] = getattr(shown_res, "strategy", None)
    out["warnings"] = [w[:500] for w in (shown_res.warnings if shown_res else [])
                       if any(x in w.lower() for x in ("order", "parts", "start point"))]
    out["error"] = shown_res.error if shown_res else None
    out["toolpath"] = tid
    return out


def op_cut_edit(payload: dict, ctl: JobControl | None = None) -> dict:
    """Change the cut order, the directions or the start points of a toolpath (what the cut order panel's buttons do).
    ``project``, ``sheet``, ``toolpath`` and ``action``:

    * ``sequence``: ``changes`` (cut order settings) are put into the toolpath; ``reorder`` (true) orders the cuts again with
      the strategy once, ``reset`` forgets the order and makes it from nothing;
    * ``optimise``: the start points of the toolpath's vectors are moved to the best places once;
    * ``manual_order``: ``keys`` are cut from place ``index`` (0 based; ``mode`` ``from`` / ``end`` / ``swap``);
    * ``move_start``: ``key`` starts ``delta`` mm further along its contour; ``place``: ``key`` (or ``keys``) starts at
      ``place`` (``sw`` ... ``e``); ``start_at``: ``key`` starts at ``point``; ``smart_one``: ``key`` is given to smart start
      points; ``release``: start points set by hand (of ``key``, of the vectors ``vectors``, or all of the toolpath) are
      given back.

    Answers ``{project, changed}``: the project as it is now (the toolpaths are not calculated)."""
    ops = _ops()
    ops.ensure_ready()
    from ..cam import cutedit
    from .wire import jsonable
    project = ops.load_project(payload.get("project"))
    sheet = ops._sheet_of(project, payload.get("sheet"))
    tid = str(payload.get("toolpath") or "")
    t = project.toolpath(tid)
    if t is None:
        raise ServiceError("unknown_toolpath", "there is no such toolpath", toolpath=tid)
    action = str(payload.get("action") or "")
    before = json.dumps(project.to_dict(), sort_keys=True)
    key = str(payload.get("key") or "")
    ok = True
    if action == "sequence":
        ch = payload.get("changes") if isinstance(payload.get("changes"), dict) else {}
        t.sequence.update(jsonable(ch))
        if payload.get("reset"):
            t.sequence.update(order=[], auto_order=True)
        elif payload.get("reorder"):
            t.sequence["order"] = []
    elif action == "optimise":
        _calc(project, {tid}, {tid}, ctl)
    elif action == "manual_order":
        res = _compute_one(project, tid, ctl=ctl).get((tid, sheet.id))
        picked = [str(k) for k in as_list(payload, "keys")]
        ok = bool(picked) and cutedit.manual_order(project, tid, sheet, res, int(payload.get("index") or 0), picked,
                                                   str(payload.get("mode") or "from"))
    elif action == "move_start":
        ok = cutedit.move_start(project, key, float(payload.get("delta") or 0.0))
    elif action == "place":
        keys = [str(k) for k in as_list(payload, "keys") or ([key] if key else [])]
        ok = any([cutedit.start_at_place(project, k, str(payload.get("place") or "")) for k in keys])
    elif action == "start_at":
        got = cutedit.contour_of(project, key)
        pt = payload.get("point")
        ok = got is not None and isinstance(pt, (list, tuple)) and len(pt) == 2 and \
            bool(cutedit.set_starts(project, [(got[0].id, got[1], (float(pt[0]), float(pt[1])))]))
    elif action == "smart_one":
        got = cutedit.contour_of(project, key)
        ok = got is not None and got[2].closed
        if ok:
            cutedit.release_starts(project, key=key)
            res = _compute_one(project, tid, True, ctl).get((tid, sheet.id))
            q = res.start_updates.get(key) if res is not None else None
            ok = q is not None and bool(cutedit.set_starts(project, [(got[0].id, got[1], q)], lock=False))
    elif action == "release":
        if key:
            cutedit.release_starts(project, key=key)
        else:
            ids = [str(v) for v in as_list(payload, "vectors")] or [o.id for o in project.vectors_for(t, sheet)]
            cutedit.release_starts(project, ids)
    else:
        raise ServiceError("bad_request", f"unknown action '{action}'", field="action")
    return {"project": project.to_dict(), "changed": json.dumps(project.to_dict(), sort_keys=True) != before, "ok": bool(ok)}


# ---------------------------------------------------------------------------------------------
# the 2D simulation, played move by move
# ---------------------------------------------------------------------------------------------

def op_sim_timeline(payload: dict, ctl: JobControl | None = None) -> dict:
    """The moves of the calculated toolpaths of a sheet on a time line, for playing them (``cam.sim2d``). ``project``,
    ``sheet``, ``toolpaths`` (ids; none: every toolpath that is on) and ``backplot`` (the text of a G-code file played after
    them). Answers ``total`` (seconds), ``thickness``, ``tools`` (names of the toolpaths played) with their ``diameters``, and the moves as parallel
    lists: ``x``, ``y``, ``z``, ``t`` (``n + 1`` points: where each move starts and ends, and when) and, for each of the
    ``n`` moves, ``k`` (index in ``tools``), ``w`` (the width of the cut) and ``c`` (how deep it cuts: ``-1`` nothing,
    0..1 of the thickness, ``2`` through)."""
    ops = _ops()
    ops.ensure_ready()
    from ..cam import sim2d
    from ..cam.gcode_reader import ReadOptions, read_gcode
    from ..cam.job import sheet_toolpaths
    project = ops.load_project(payload.get("project"))
    sheet = ops._sheet_of(project, payload.get("sheet"))
    project.sheets = [sheet]
    res, _ = _calc(project, ctl=ctl)
    want = {str(i) for i in as_list(payload, "toolpaths")}
    tps = sheet_toolpaths(project, res, sheet, enabled_only=not want)
    tps = [tp for tp in tps if not want or tp.def_id in want]
    if payload.get("backplot"):
        from ..core import paths
        from ..core.tooling import ToolLibrary
        from ..post.engine import ORIGINS
        m = project.material
        fx, fy = ORIGINS.get(m.xy_origin, (0.0, 0.0))
        lib = {t.number: t for t in ToolLibrary.load_or_default(paths.tools_file()).tools}
        tps += read_gcode(_text(payload, "backplot"), ReadOptions(fx * sheet.width, fy * sheet.height,
                          project.sheet_thickness(sheet) if m.z_zero == "bottom" else 0.0, lib))
    if not tps:
        raise ServiceError("no_toolpaths", "there is no calculated toolpath to play on this sheet: calculate first")
    thickness = max(project.sheet_thickness(sheet), 1e-6)
    tl = sim2d.build(tps, machine=project.material.machine())
    pcs = tl.pieces
    x, y, z, t, k, w, c = [], [], [], [], [], [], []
    if pcs:
        p0 = pcs[0]
        x.append(round(p0.x0, 3)); y.append(round(p0.y0, 3)); z.append(round(p0.z0, 3)); t.append(round(p0.t0, 4))
    for pc in pcs:
        x.append(round(pc.x1, 3)); y.append(round(pc.y1, 3)); z.append(round(pc.z1, 3)); t.append(round(pc.t1, 4))
        k.append(pc.tool)
        top = max(pc.z0, pc.z1)
        if pc.kind != sim2d.KIND_FEED or top >= -1e-6:
            w.append(0); c.append(-1)
            continue
        depth = -min(pc.z0, pc.z1)
        tool = tl.toolpaths[pc.tool].tool
        w.append(round(tool.radius_at_depth(depth) * 2 if tool.is_tapered else tool.diameter, 3))
        c.append(2 if depth >= thickness - 1e-3 else round(min(1.0, depth / thickness), 3))
    return {"total": round(tl.total, 3), "thickness": thickness, "sheet": sheet.id, "tools": [tp.name for tp in tl.toolpaths],
            "diameters": [round(tp.tool.diameter, 3) for tp in tl.toolpaths], "x": x, "y": y, "z": z, "t": t, "k": k, "w": w, "c": c, "moves": len(pcs)}


# ---------------------------------------------------------------------------------------------
# the Python console
# ---------------------------------------------------------------------------------------------

BANNER = """iyiCAM Python console (this runs in the browser).  Available names:
  project, sheet(), selected(), ctrl, REGISTRY
  shapes, vectorops, geom, add(contour_or_list), run(command_id, **values), report_text(result)
Example:  add(shapes.circle((100, 100), 25))
          r = run("edit.doctor"); print(r["messages"])   # run() returns what the command handed back (report_text(r) reads a report)
What you change in the project comes back to the drawing as one step of Undo."""


class _Ctx:
    """What a script sees as ``ctrl`` (a small part of the desktop's controller): the project, the open sheet, the layer and
    the selection of the window the console was asked from."""

    def __init__(self, project, sheet, selection, layer_id):
        self.project, self.sheet, self.selection, self.layer_id = project, sheet, list(selection), layer_id

    def selected_objects(self):
        return self.sheet.pick(self.selection)

    def add_vectors(self, objs, label: str = "Add vectors", select: bool = True) -> None:
        objs = [o for o in objs if o.prune()]
        self.sheet.vectors.extend(objs)
        if select:
            self.selection = [o.id for o in objs]


class _Console:
    def __init__(self):
        from ..core import geom, shapes, vectorops
        from ..core.commands import report_text
        from ..core.registry import REGISTRY
        self.cur: _Ctx | None = None
        self.ns = {"REGISTRY": REGISTRY, "shapes": shapes, "vectorops": vectorops, "geom": geom, "report_text": report_text,
                   "add": self._add, "run": self._run, "sheet": lambda: self.cur.sheet, "selected": lambda: self.cur.selected_objects()}
        self.interp = code.InteractiveInterpreter(self.ns)
        self.buffer: list[str] = []

    def _add(self, contours, name: str = "script"):
        from ..core.document import VectorObject
        cs = contours if isinstance(contours, list) else [contours]
        obj = VectorObject(cs, self.cur.layer_id, name)
        self.cur.add_vectors([obj], "Script")
        return obj

    def _run(self, cmd_id: str, **values):
        """Run a command and return what it handed back (``False`` if it failed: the reason is printed)."""
        from ..core.commands import CommandContext, CommandError, run_command
        from ..core.registry import REGISTRY
        from .wire import tool_values
        cmd = REGISTRY.commands.get(cmd_id)
        if cmd is None:
            print(f"there is no command '{cmd_id}'")
            return False
        c = self.cur
        ctx = CommandContext(c.project, c.sheet, list(c.selection), c.layer_id)
        try:
            run_command(cmd, ctx, tool_values(cmd.params, values))
        except (CommandError, ValueError, TypeError, KeyError, ArithmeticError) as exc:
            print(f"{cmd.label}: {exc}")
            return False
        if ctx.new_selection is not None:
            c.selection = [v.id for v in c.sheet.pick(ctx.new_selection)]
        return ctx.result()

    def execute(self, line: str) -> tuple[str, bool]:
        self.buffer.append(line)
        buf = io.StringIO()
        try:
            with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(buf):
                more = self.interp.runsource("\n".join(self.buffer), "<console>", "single")
        except SystemExit:
            more = False
        except Exception:  # noqa: BLE001  (what a script does wrong is shown, the engine stays)
            buf.write(traceback.format_exc())
            more = False
        if not more:
            self.buffer = []
        return buf.getvalue(), bool(more)


_CONSOLE: _Console | None = None


def op_console_reset(_payload=None) -> dict:
    """A new console: the variables of the earlier one are forgotten. ``{banner}``."""
    global _CONSOLE
    _ops().ensure_ready()
    _CONSOLE = _Console()
    return {"banner": BANNER}


def op_console_run(payload: dict) -> dict:
    """Run one line of Python (``line``) in the console of this engine, on the open project (``project``, ``sheet``,
    ``selection``, ``layer``). The variables are kept from line to line. Answers ``output`` (what was printed, and errors),
    ``more`` (the line is not finished: the next one goes on), and, when the script changed the project, ``changed`` true
    with the new ``project`` and ``selection``."""
    global _CONSOLE
    ops = _ops()
    ops.ensure_ready()
    if _CONSOLE is None:
        _CONSOLE = _Console()
    con = _CONSOLE
    project = ops.load_project(payload.get("project"))
    sheet = ops._sheet_of(project, payload.get("sheet"))
    layer = str(payload.get("layer") or (project.layers[0].id if project.layers else ""))
    con.cur = _Ctx(project, sheet, [str(i) for i in as_list(payload, "selection")], layer)
    con.ns["project"] = project
    con.ns["ctrl"] = con.cur
    before = json.dumps(project.to_dict(), sort_keys=True)
    line = payload.get("line")
    if not isinstance(line, str):
        raise ServiceError("bad_request", "'line' (a line of Python) is needed", field="line")
    out, more = con.execute(line)
    # (a script that put another project in the name ``project`` changes nothing: only the project that was given counts)
    after = json.dumps(project.to_dict(), sort_keys=True)
    answer = {"output": out, "more": more, "changed": after != before}
    if answer["changed"]:
        answer["project"] = project.to_dict()
        answer["selection"] = [v.id for v in sheet.pick(con.cur.selection)]
    return answer

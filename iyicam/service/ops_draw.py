"""JSON operations of the draw features of the web app (see ops.py: JSON in, JSON out, ServiceError for what is wrong).

Every `op_*` here is an operation of ops.py too (ops.py takes them in), so the browser's engine and any other interface
call them by name. Keep the work in iyicam.core / cam / nesting / post: an op only translates JSON to it and back.

What the ops do (the desktop does the same in ``gui/tools.py``, ``gui/main_window.py``, ``gui/controller.py`` and
``gui/veclib_panel.py``, where it is tied to Qt):

* ``vector_edit``    edit nodes: move, insert, delete, break, convert, reverse, open / close, set the start (one or a few vectors)
* ``start_points``   Vectors > Start Points: corners, middles of sides, next / previous, opposite, smart (best place), release
* ``start_probe``    where a start point would go for a pointer (clings to corners, middles, arc extremes), and the places it clings to
* ``trim``           the Trim tool: the piece of a vector between two crossings is removed
* ``text``           the Text tool: letters of a font as a vector (``fonts`` lists the fonts)
* ``text_on_curve``  Vectors > Text on Curve
* ``trace_bitmap``   Vectors > Trace Bitmap: the browser sends the grey values of the picture
* ``veclib``         the vector library (kept in the user folder, as the desktop's)
* ``sheet_outline``  Nesting > Set / Clear Sheet Outline
"""
from __future__ import annotations

import math

from .errors import ServiceError
from .wire import b64d

__all__: list[str] = ["op_vector_edit", "op_start_points", "op_start_probe", "op_trim", "op_text", "op_fonts",
                      "op_text_on_curve", "op_trace_bitmap", "op_veclib", "op_sheet_outline", "op_sheet_scope"]


# ---------------------------------------------------------------------------------------------
# helpers (ops.py is imported inside the functions: it imports this module when it is done)
# ---------------------------------------------------------------------------------------------

def _ops():
    from . import ops
    return ops


def _setup(payload: dict):
    """``(project, sheet)`` of a payload."""
    ops = _ops()
    ops.ensure_ready()
    project = ops.load_project(payload.get("project"))
    return project, ops._sheet_of(project, payload.get("sheet"))


def _pt(v, what: str = "point") -> tuple[float, float]:
    try:
        x, y = v
        x, y = float(x), float(y)
        if not (math.isfinite(x) and math.isfinite(y)):
            raise ValueError
        return (x, y)
    except (TypeError, ValueError) as exc:
        raise ServiceError("bad_request", f"'{what}' must be a point [x, y]", field=what) from exc


def _int(v, what: str) -> int:
    try:
        return int(v)
    except (TypeError, ValueError) as exc:
        raise ServiceError("bad_request", f"'{what}' must be a whole number", field=what) from exc


def _num(v, what: str, default=None, lo=None, hi=None) -> float:
    if v is None and default is not None:
        return default
    try:
        v = float(v)
    except (TypeError, ValueError) as exc:
        raise ServiceError("bad_request", f"'{what}' must be a number", field=what) from exc
    if not math.isfinite(v) or (lo is not None and v < lo) or (hi is not None and v > hi):
        raise ServiceError("bad_request", f"'{what}' is out of range", field=what)
    return v


def _layer_id(project, given) -> str:
    if given and any(l.id == given for l in project.layers):
        return str(given)
    return project.layers[0].id


def _add_vector(project, sheet, contours, layer, name: str, label: str):
    """Put contours on the sheet as a new vector; returns it."""
    from ..core.document import VectorObject
    obj = VectorObject([c for c in contours if c.segs], _layer_id(project, layer), name)
    if not obj.prune():
        raise ServiceError("nothing_made", f"{label}: there is nothing to draw")
    sheet.vectors.append(obj)
    return obj


def _vectors(payload: dict) -> list:
    from ..core.document import vector_from_dict
    out = []
    for d in payload.get("vectors") or []:
        try:
            out.append(vector_from_dict(d))
        except (KeyError, TypeError, ValueError, IndexError) as exc:
            raise ServiceError("bad_request", f"not a vector: {type(exc).__name__}: {exc}", field="vectors") from exc
    return out


def _contour(obj, ci, what: str = "contour"):
    ci = _int(ci, what)
    if obj is None or not 0 <= ci < len(obj.contours):
        raise ServiceError("bad_request", "there is no such contour", field=what)
    return ci


# ---------------------------------------------------------------------------------------------
# edit nodes
# ---------------------------------------------------------------------------------------------

def op_vector_edit(payload: dict) -> dict:
    """Edit the nodes of vectors (the Edit Nodes tool).  ``vectors``: the vectors concerned (as in the project), ``action``:

    * ``move_nodes``  ``nodes`` [[vector id, contour, node], ...] and ``dx``, ``dy`` (all moved by the same, the handles of
      their beziers with them), or ``to`` [x, y] for the first of them and the rest by the same step
    * ``move_control`` ``vector``, ``contour``, ``seg``, ``which`` (1 or 2), ``to`` [x, y]: a bezier handle
    * ``insert``      ``vector``, ``contour``, ``seg``, ``t`` (0 to 1): a node on a span
    * ``delete_nodes`` ``nodes``
    * ``break``       ``vector``, ``contour``, ``seg``, ``t``: the contour cut there (a closed one becomes an open one)
    * ``convert``     ``vector``, ``contour``, ``seg``, ``kind`` (line, arc, bezier)
    * ``reverse``, ``open``, ``close``  ``vector``, ``contour``
    * ``set_start``   ``vector``, ``contour``, ``point``: the closed contour starts there, and keeps it (smart start points leave it)

    Answers ``{vectors: [...changed vectors], removed: [ids of vectors left without a contour]}``."""
    from ..core import vectorops
    from ..core.document import vector_to_dict
    from ..core.startplace import set_starts
    _ops().ensure_ready()
    objs = {o.id: o for o in _vectors(payload)}
    act = str(payload.get("action"))
    touched: set[str] = set()

    def one():
        o = objs.get(str(payload.get("vector")))
        if o is None:
            raise ServiceError("bad_request", "there is no such vector", field="vector")
        return o, _contour(o, payload.get("contour"))

    def replace(o, ci, res):
        if isinstance(res, list):
            o.contours[ci:ci + 1] = res
        else:
            o.contours[ci] = res
        touched.add(o.id)

    def nodes():
        out = []
        for n in payload.get("nodes") or []:
            vid, ci, ni = str(n[0]), _int(n[1], "nodes"), _int(n[2], "nodes")
            o = objs.get(vid)
            if o is None or not 0 <= ci < len(o.contours) or not 0 <= ni < len(o.contours[ci].nodes()):
                raise ServiceError("bad_request", "a node is not there", field="nodes")
            out.append((vid, ci, ni))
        if not out:
            raise ServiceError("bad_request", "no nodes given", field="nodes")
        return out

    try:
        if act == "move_nodes":
            sel = nodes()
            if payload.get("to") is not None:
                to = _pt(payload["to"], "to")
                base = objs[sel[0][0]].contours[sel[0][1]].nodes()[sel[0][2]]
                dx, dy = to[0] - base[0], to[1] - base[1]
            else:
                dx, dy = _num(payload.get("dx"), "dx"), _num(payload.get("dy"), "dy")
            orig = {(v, c): objs[v].contours[c] for v, c, _ in sel}
            for (vid, ci), c0 in orig.items():
                base_nodes, c = c0.nodes(), c0
                for v, k, ni in sel:
                    if v == vid and k == ci:
                        c = vectorops.move_node(c, ni, (base_nodes[ni][0] + dx, base_nodes[ni][1] + dy))
                objs[vid].contours[ci] = c
                touched.add(vid)
        elif act == "move_control":
            o, ci = one()
            replace(o, ci, vectorops.move_control(o.contours[ci], _int(payload.get("seg"), "seg"), _int(payload.get("which"), "which"),
                                                  _pt(payload.get("to"), "to")))
        elif act == "insert":
            o, ci = one()
            replace(o, ci, vectorops.insert_node(o.contours[ci], _int(payload.get("seg"), "seg"), _num(payload.get("t"), "t", lo=0.0, hi=1.0)))
        elif act == "delete_nodes":
            by: dict[tuple[str, int], list[int]] = {}
            for vid, ci, ni in nodes():
                by.setdefault((vid, ci), []).append(ni)
            for (vid, ci), ns in by.items():
                c = objs[vid].contours[ci]
                for ni in sorted(ns, reverse=True):
                    c = vectorops.delete_node(c, ni)
                objs[vid].contours[ci] = c
                touched.add(vid)
        elif act == "break":
            o, ci = one()
            replace(o, ci, vectorops.break_at(o.contours[ci], _int(payload.get("seg"), "seg"), _num(payload.get("t"), "t", lo=0.0, hi=1.0)))
        elif act == "convert":
            o, ci = one()
            kind = str(payload.get("kind"))
            if kind not in ("line", "arc", "bezier"):
                raise ServiceError("bad_request", "kind is line, arc or bezier", field="kind")
            replace(o, ci, vectorops.convert_segment(o.contours[ci], _int(payload.get("seg"), "seg"), kind))
        elif act == "reverse":
            o, ci = one()
            replace(o, ci, o.contours[ci].reversed())
        elif act == "open":
            o, ci = one()
            replace(o, ci, vectorops.break_at(o.contours[ci], 0, 0.0))
        elif act == "close":
            o, ci = one()
            replace(o, ci, vectorops.close(o.contours[ci]))
        elif act == "set_start":
            o, ci = one()
            set_starts([(o, ci, _pt(payload.get("point")))])
            touched.add(o.id)
        else:
            raise ServiceError("bad_request", "unknown action", field="action", known=["move_nodes", "move_control", "insert", "delete_nodes",
                               "break", "convert", "reverse", "open", "close", "set_start"])
    except (IndexError, ValueError, ZeroDivisionError, TypeError) as exc:
        raise ServiceError("edit_failed", f"{type(exc).__name__}: {exc}") from exc
    removed = [i for i in touched if not objs[i].contours]
    return {"ok": True, "vectors": [vector_to_dict(objs[i]) for i in objs if i in touched and i not in removed], "removed": removed}


# ---------------------------------------------------------------------------------------------
# start points
# ---------------------------------------------------------------------------------------------

def op_start_points(payload: dict) -> dict:
    """Where the cuts of the selected closed vectors start (Vectors > Start Points).  ``action``: ``place`` (``place``: sw se nw ne
    for the corners, s n w e for the middles of the sides), ``step`` (``direction`` 1 next corner, -1 previous), ``opposite``,
    ``release`` (the starts go back to smart start points), ``smart`` (the best place, calculated with a toolpath: ``toolpath`` or
    the one that machines the selection), ``set`` (``items`` [[vector id, contour, [x, y]], ...]; ``lock`` default true).  The vectors
    are the ``selection`` of the sheet, or only the contours ``contours`` [[vector id, contour], ...] when given.  Answers ``{project, moved, changed, message}`` (what the desktop says in its status bar)."""
    from ..cam.job import compute_all
    from ..core import startplace
    from ..core.registry import REGISTRY
    project, sheet = _setup(payload)
    objs = sheet.pick([str(i) for i in payload.get("selection") or []])
    keys = startplace.cut_keys(objs)
    if payload.get("contours") is not None:                     # (only these contours: [[vector id, contour], ...], what the tool's keys mean)
        want = {(str(a), _int(b, "contours")) for a, b in payload["contours"]}
        objs = [o for o in sheet.vectors if any(o.id == w[0] for w in want)]
        keys = [(o, ci) for o, ci in startplace.cut_keys(objs) if (o.id, ci) in want]
    act = str(payload.get("action"))
    moved, msg = 0, ""
    before = {o.id: ([c.start for c in o.contours], list(o.locked_starts)) for o in objs}

    def started(n: int) -> str:
        if not keys:
            return "Select closed vectors first: only they have a start point"
        if n:
            return f"{n} start point(s) set by hand: smart start points keep them (undo: Ctrl+Z)"
        return "There is no other corner to start at"

    if act == "place":
        place = str(payload.get("place"))
        if place not in startplace.PLACES:
            raise ServiceError("bad_request", "unknown place", field="place", known=list(startplace.PLACES))
        moved = startplace.starts_at_place(keys, place)
        msg = started(moved)
    elif act == "step":
        moved = startplace.step_starts(keys, -1 if _int(payload.get("direction", 1), "direction") < 0 else 1)
        msg = started(moved)
    elif act == "opposite":
        moved = startplace.start_opposite(keys)
        msg = started(moved)
    elif act == "release":
        moved = startplace.release_starts(objs, keys if payload.get("contours") is not None else None)
        msg = f"{moved} start point(s) given back to smart start points" if moved else "No start point was set by hand"
    elif act == "set":
        items = []
        for it in payload.get("items") or []:
            o = sheet.vector(str(it[0]))
            if o is not None:
                items.append((o, _int(it[1], "items"), _pt(it[2], "items")))
        objs = [o for o, _, _ in items]
        before.update({o.id: ([c.start for c in o.contours], list(o.locked_starts)) for o in objs})
        moved = startplace.set_starts(items, payload.get("lock", True) is not False)
        msg = "Start point set" if moved else ""
    elif act == "smart":
        if not keys:
            msg = "Select closed vectors first: only they have a start point"
        else:
            tid = startplace.default_toolpath(project, sheet, REGISTRY, [o.id for o in objs], payload.get("toolpath"))
            if tid is None:
                msg = "Add a profile or engraving toolpath first"
            else:
                for o in objs:
                    o.locked_starts = []                      # (a start set by hand is given back first)
                res = compute_all(project, REGISTRY, {tid}, None, {tid}, events=[])
                r = res.get((tid, sheet.id))
                items = []
                if r is not None:
                    from ..core.geom import dist
                    for o, ci in keys:
                        q = r.start_updates.get(f"{o.id}:{ci}")
                        if q is not None and dist(o.contours[ci].start, q) > 1e-3:
                            items.append((o, ci, q))
                moved = startplace.set_starts(items, lock=False) and len(items)
                msg = f"{moved} start point(s) moved to the best place" + ("; Toolpaths > Recalculate All (F5) calculates the toolpaths" if moved else "")
    else:
        raise ServiceError("bad_request", "unknown action", field="action",
                           known=["place", "step", "opposite", "release", "smart", "set"])
    changed = any(([c.start for c in o.contours], list(o.locked_starts)) != before.get(o.id) for o in objs)
    return {"ok": True, "project": project.to_dict() if changed else None, "moved": int(moved), "changed": changed, "message": msg}


def op_start_probe(payload: dict) -> dict:
    """What the Set Start Point tool needs of a contour: ``contour`` (as in the project) and ``point``.  Answers ``{point, kind}``
    of where the start goes for a pointer there (``cling``: a corner, a middle or an arc extreme within ``reach`` mm wins; else
    the nearest point; kinds node, quadrant, mid, edge) and, with ``features``, the ``features`` it clings to [[x, y, kind], ...]."""
    from ..core import vectorops
    from ..core.document import contour_from_dict
    _ops().ensure_ready()
    try:
        c = contour_from_dict(payload["contour"])
    except (KeyError, TypeError, ValueError, IndexError) as exc:
        raise ServiceError("bad_request", f"not a contour: {exc}", field="contour") from exc
    if not c.segs:
        raise ServiceError("bad_request", "the contour is empty", field="contour")
    p = _pt(payload.get("point"))
    feats = vectorops.start_features(c)
    if payload.get("cling", True) is not False:
        q, kind = vectorops.snap_on_contour(c, p, _num(payload.get("reach"), "reach", 3.0, lo=0.0), feats)
    else:
        q, kind = c.nearest(p)[2], "edge"
    out = {"ok": True, "point": [q[0], q[1]], "kind": kind}
    if payload.get("features"):
        out["features"] = [[f[0][0], f[0][1], f[1]] for f in feats[:400]]
    return out


# ---------------------------------------------------------------------------------------------
# trim
# ---------------------------------------------------------------------------------------------

def op_trim(payload: dict) -> dict:
    """The Trim tool: ``vector`` (id) and ``point`` [x, y] on it.  The piece of its nearest contour round the point, between two
    crossings with the other visible vectors of the sheet, is removed.  Answers ``{project, changed, message}`` (``project`` is
    null when nothing could be trimmed there)."""
    from ..core.intersect import trim
    project, sheet = _setup(payload)
    obj = sheet.vector(str(payload.get("vector")))
    if obj is None or not obj.contours:
        raise ServiceError("bad_request", "there is no such vector", field="vector")
    w = _pt(payload.get("point"))
    ci = min(range(len(obj.contours)), key=lambda k: obj.contours[k].nearest(w)[3])
    target = obj.contours[ci]
    hidden = {l.id for l in project.layers if not l.visible}
    cutters = [c for o in sheet.vectors if o.layer_id not in hidden for c in o.contours if c is not target]
    pieces = trim(target, w, cutters)
    if pieces is None:
        return {"ok": True, "project": None, "changed": False,
                "message": "Nothing to trim here: the vector does not cross other vectors around this point."}
    obj.contours = obj.contours[:ci] + pieces + obj.contours[ci + 1:]
    if not obj.contours:
        project.remove_vectors([obj.id])
    return {"ok": True, "project": project.to_dict(), "changed": True, "message": "Trimmed"}


# ---------------------------------------------------------------------------------------------
# text
# ---------------------------------------------------------------------------------------------

def op_fonts(_payload=None) -> dict:
    """The fonts the engine has: ``{families: [...]}`` (a font file of the person can be given to ``text`` as ``font_b64``)."""
    from ..core import textgeom
    return {"ok": True, "families": textgeom.families(), "default": textgeom.DEFAULT_FAMILY}


def _font_bytes(payload: dict) -> bytes | None:
    return b64d(payload["font_b64"]) if payload.get("font_b64") else None


def _text_args(v: dict) -> dict:
    return {"family": str(v.get("font") or ""), "height": _num(v.get("height"), "height", 10.0, lo=0.01, hi=100000),
            "bold": bool(v.get("bold")), "italic": bool(v.get("italic")), "font_bytes": _font_bytes(v)}


def op_text(payload: dict) -> dict:
    """The Text tool: ``text``, ``font`` (a name of ``fonts``), ``height`` (capitals, mm), ``bold``, ``italic``, ``align`` (left,
    center, right), ``spacing`` (mm after each letter), ``at`` [x, y] where the baseline starts; ``layer``.  With a project the
    text is added to the sheet as a vector (answers ``{project, selection}``), without ``project`` only ``contours`` are made
    (``{vector}``: for a preview)."""
    from ..core import textgeom
    from ..core.document import vector_to_dict, VectorObject
    text = str(payload.get("text") or "")
    if not text.strip():
        raise ServiceError("bad_request", "write some text first", field="text")
    at = _pt(payload.get("at", [0, 0]), "at")
    try:
        cs = textgeom.text_contours(text, spacing=_num(payload.get("spacing"), "spacing", 0.0), align=str(payload.get("align") or "left"),
                                    **_text_args(payload))
    except textgeom.TextError as exc:
        raise ServiceError("bad_request", str(exc)) from exc
    from ..core.geom import Transform
    T = Transform.translate(*at)
    cs = [c.transformed(T) for c in cs]
    name = text.strip().split("\n")[0][:24] or "Text"
    if payload.get("project") is None:
        _ops().ensure_ready()
        return {"ok": True, "vector": vector_to_dict(VectorObject(cs, "", name))}
    project, sheet = _setup(payload)
    obj = _add_vector(project, sheet, cs, payload.get("layer"), name, "Text")
    return {"ok": True, "project": project.to_dict(), "selection": [obj.id]}


def op_text_on_curve(payload: dict) -> dict:
    """Text along the one selected vector (its first contour): ``text``, ``font``, ``height``, ``spacing``, ``offset`` (to the left of
    the vector), ``align`` (start, center, end), ``reverse``, ``bold``, ``italic``.  Answers ``{project, selection}``."""
    from ..core import textgeom
    project, sheet = _setup(payload)
    sel = sheet.pick([str(i) for i in payload.get("selection") or []])
    if len(sel) != 1 or not sel[0].contours:
        raise ServiceError("bad_selection", "Select one vector to put the text on.")
    text = str(payload.get("text") or "")
    if not text.strip():
        raise ServiceError("bad_request", "write some text first", field="text")
    curve = sel[0].contours[0]
    if payload.get("reverse"):
        curve = curve.reversed()
    try:
        cs = textgeom.text_on_curve(text, curve=curve, spacing=_num(payload.get("spacing"), "spacing", 0.0),
                                    offset=_num(payload.get("offset"), "offset", 0.0), align=str(payload.get("align") or "center"),
                                    **_text_args(payload))
    except textgeom.TextError as exc:
        raise ServiceError("bad_request", str(exc)) from exc
    obj = _add_vector(project, sheet, cs, payload.get("layer"), text[:24], "Text on curve")
    return {"ok": True, "project": project.to_dict(), "selection": [obj.id]}


# ---------------------------------------------------------------------------------------------
# trace bitmap
# ---------------------------------------------------------------------------------------------

def op_trace_bitmap(payload: dict) -> dict:
    """Turn a picture into vectors.  The browser decodes the picture and sends its grey values: ``gray_b64`` (one byte per pixel, row by
    row from the top), ``width``, ``height`` (pixels), ``name``.  ``options``: ``width`` (mm of the result, default 80 % of the
    sheet, at most 200), ``threshold`` (1-254), ``mode`` (outline or centerline), ``invert``, ``smoothing``, ``min_area``, ``center``.
    Answers ``{project, selection, contours}``."""
    import numpy as np
    from ..core.geom import Transform
    from ..core.trace import centerline, trace
    project, sheet = _setup(payload)
    w, h = _int(payload.get("width"), "width"), _int(payload.get("height"), "height")
    raw = b64d(payload.get("gray_b64") or "")
    if w < 2 or h < 2 or w * h > 40_000_000 or len(raw) != w * h:
        raise ServiceError("bad_request", "the picture must be width x height grey bytes (at most 40 million pixels)")
    gray = np.frombuffer(raw, dtype=np.uint8).reshape(h, w).astype(float)
    o = payload.get("options") or {}
    threshold = _num(o.get("threshold"), "threshold", 128.0, lo=1, hi=254)
    invert, smoothing = bool(o.get("invert")), _num(o.get("smoothing"), "smoothing", 0.6, lo=0.05, hi=10.0)
    min_area = _num(o.get("min_area"), "min_area", 8.0, lo=0.0)
    px = _num(o.get("width"), "width of the result", min(sheet.width * 0.8, 200.0), lo=0.01) / max(1, w)
    if o.get("mode") == "centerline":
        contours = centerline(gray, threshold, invert, px, smoothing, max(4.0, min_area ** 0.5))
    else:
        contours = trace(gray, threshold, invert, px, min_area, smoothing)
    if not contours:
        raise ServiceError("nothing_found", "Nothing was found at this threshold.")
    obj = _add_vector(project, sheet, contours, payload.get("layer"), str(payload.get("name") or "Trace")[:60], "Trace bitmap")
    if o.get("center", True):
        bb = obj.bbox()
        obj.transform(Transform.translate(sheet.width / 2 - bb.center[0], sheet.height / 2 - bb.center[1]))
    return {"ok": True, "project": project.to_dict(), "selection": [obj.id], "contours": len(contours)}


# ---------------------------------------------------------------------------------------------
# the vector library
# ---------------------------------------------------------------------------------------------

def _lib_tree(g: dict) -> dict:
    return {"id": g["id"], "name": g["name"],
            "groups": [_lib_tree(s) for s in g["groups"]],
            "items": [{"id": it["id"], "name": it["name"], "size": it.get("size") or [0, 0], "vectors": len(it["vectors"])} for it in g["items"]]}


def op_veclib(payload: dict) -> dict:
    """The vector library (``vector_library.json`` in the user folder, shared by every project).  ``action``:

    * ``tree`` the library: ``{tree: {id, name, groups, items}}`` (items have id, name, size, vectors: how many)
    * ``add_group`` ``name``, ``parent`` (group id, default the top); ``rename`` ``id``, ``name``; ``remove`` ``id``;
      ``shift`` ``id``, ``step`` (-1 up, 1 down); ``to_top`` ``id``; ``move`` ``id``, ``to`` (group), ``index``
    * ``add_item`` keeps the selection (``project``, ``sheet``, ``selection``) as a drawing named ``name`` in ``group``
    * ``insert`` puts the item ``id`` in the sheet with its box starting at ``at`` ([x, y]; the sheet's middle when missing), on
      ``layer`` or (``own_layers``) the layers it came from; answers ``{project, selection}``
    * ``preview`` the picture of an item ``id``: ``{size, paths: [[[x, y], ...], ...], closed: [bool, ...]}``
    * ``export`` the group or item ``id`` (default the whole): ``{data}``; ``import`` ``data`` into ``group``: a group of its own

    Every answer but ``preview`` and ``export`` has the ``tree`` and, where something was made, its ``id``."""
    from ..core.veclib import ROOT, LibraryError, VectorLibrary
    ops = _ops()
    ops.ensure_ready()
    lib = VectorLibrary.load()
    act = str(payload.get("action") or "tree")
    out: dict = {"ok": True}
    changed = False
    try:
        if act == "tree":
            pass
        elif act == "add_group":
            out["id"] = lib.add_group(str(payload.get("name") or ""), str(payload.get("parent") or ROOT))["id"]
            changed = True
        elif act == "rename":
            lib.rename(str(payload.get("id")), str(payload.get("name") or "")); out["id"] = str(payload.get("id")); changed = True
        elif act == "remove":
            lib.remove(str(payload.get("id"))); changed = True
        elif act == "shift":
            lib.shift(str(payload.get("id")), -1 if _int(payload.get("step", 1), "step") < 0 else 1); out["id"] = str(payload.get("id")); changed = True
        elif act == "to_top":
            lib.move(str(payload.get("id")), ROOT); out["id"] = str(payload.get("id")); changed = True
        elif act == "move":
            idx = payload.get("index")
            lib.move(str(payload.get("id")), str(payload.get("to") or ROOT), None if idx is None else _int(idx, "index"))
            out["id"] = str(payload.get("id")); changed = True
        elif act == "add_item":
            project, sheet = _setup(payload)
            objs = sheet.pick([str(i) for i in payload.get("selection") or []])
            if not objs:
                raise LibraryError("Select the vectors to keep in the library first.")
            name = str(payload.get("name") or "") or objs[0].name or "Drawing"
            out["id"] = lib.add_item(name, objs, project, str(payload.get("group") or ROOT))["id"]
            changed = True
        elif act == "insert":
            project, sheet = _setup(payload)
            node, _ = lib.find(str(payload.get("id")))
            if node is None or lib.is_group(node):
                raise LibraryError("Select a drawing of the library (not a group).")
            w, h = (node.get("size") or [0, 0])[:2]
            at = _pt(payload["at"], "at") if payload.get("at") is not None else (sheet.width / 2 - w / 2, sheet.height / 2 - h / 2)
            made = lib.vectors_of(node, _layer_id(project, payload.get("layer")), at, project, own_layers=bool(payload.get("own_layers")))
            sheet.vectors.extend(made)
            out.update(project=project.to_dict(), selection=[o.id for o in made], name=node["name"])
        elif act == "preview":
            node, _ = lib.find(str(payload.get("id")))
            if node is None or lib.is_group(node):
                raise LibraryError("Select a drawing of the library (not a group).")
            from ..core.document import contour_from_dict
            size = node.get("size") or [0, 0]
            tol = max(size[0], size[1], 1.0) / 300.0
            paths, closed = [], []
            for d in node["vectors"]:
                for cd in d.get("contours", []):
                    try:
                        pts = contour_from_dict(cd).flatten(tol)
                    except (KeyError, ValueError, TypeError):
                        continue
                    if pts:
                        paths.append([[round(x, 3), round(y, 3)] for x, y in pts]); closed.append(bool(cd.get("closed")))
            return {"ok": True, "size": size, "paths": paths, "closed": closed, "name": node["name"], "vectors": len(node["vectors"])}
        elif act == "export":
            return {"ok": True, "data": lib.export_group(str(payload.get("id") or ROOT))}
        elif act == "import":
            g = lib.import_data(payload.get("data"), str(payload.get("group") or ROOT))
            if payload.get("name") and (not g["name"] or g["name"] == "Imported"):
                g["name"] = str(payload["name"])
            out["id"] = g["id"]; changed = True
        else:
            raise ServiceError("bad_request", "unknown action", field="action")
        if changed:
            lib.save()
    except LibraryError as exc:
        raise ServiceError("library", str(exc)) from exc
    except OSError as exc:
        raise ServiceError("library", f"The library could not be saved: {exc}") from exc
    out["tree"] = _lib_tree(lib.root)
    return out


# ---------------------------------------------------------------------------------------------
# the outline of the sheet
# ---------------------------------------------------------------------------------------------

def op_sheet_outline(payload: dict) -> dict:
    """Make the sheet a plate of any shape.  ``selection``: the closed vectors that outline it (they are used up; closed ones inside
    an outline are holes); ``clear`` true: a plain rectangle again.  The sheet is moved so the outline starts at the origin, with
    everything on it, and its size becomes the size of the outline.  Answers ``{project, changed, message}``."""
    from ..core import clip, vectorops
    from ..core.geom import Transform
    from ..core.stock import normalized
    project, sheet = _setup(payload)
    if payload.get("clear"):
        had = bool(sheet.outline)
        sheet.outline = None
        return {"ok": True, "project": project.to_dict() if had else None, "changed": had,
                "message": "The sheet is a rectangle again" if had else "The sheet has no outline"}
    objs = [o for o in sheet.pick([str(i) for i in payload.get("selection") or []]) if any(c.closed for c in o.contours)]
    polys = clip.normalize([p for o in objs for p in vectorops.region([c for c in o.contours if c.closed])])
    if not polys:
        raise ServiceError("bad_selection", "Select the closed vectors that outline the plate (and the closed vectors inside them that are holes) first.")
    ids = {o.id for o in objs}
    sheet.vectors = [v for v in sheet.vectors if v.id not in ids]
    polys = clip.normalize(polys)
    w, h, norm = normalized(polys)
    T = Transform.translate(-min(x for poly in polys for x, _ in poly), -min(y for poly in polys for _, y in poly))
    for v in sheet.vectors:
        v.transform(T)
    sheet.width, sheet.height, sheet.outline = w, h, norm
    return {"ok": True, "project": project.to_dict(), "changed": True, "message": f"The sheet is now {w:.1f} x {h:.1f} mm, shaped by {len(objs)} vector(s)"}


# ---------------------------------------------------------------------------------------------
def op_sheet_scope(payload: dict) -> dict:
    """Groups of sheets and their isolation (core.scope), the same as the desktop's Sheets panel.

    ``action``: ``group`` (``sheets`` into the group ``name``; "" takes them out), ``arrange`` (``order``: every sheet id in
    the new order, ``groups``: id -> group, as a drag left them), ``rename`` (the group ``old`` to ``name``), ``isolate``
    (``sheets``: their group, or a new group of them), ``unisolate`` (the group ``name``), ``new_sheets`` (the sheets
    ``sheets`` a nesting made into the group ``name``, ``null``: the group of the sheet ``sheet``).  Answers ``{project,
    group}``: the project as it is after, and the group it is about."""
    from ..core import scope
    from ..core.document import project_from_dict
    d = payload.get("project")
    if not isinstance(d, dict):
        raise ServiceError("bad_request", "the project must be given as an object (the .icam JSON)")
    project = project_from_dict(d)
    act = str(payload.get("action") or "")
    ids = [str(x) for x in payload.get("sheets") or []]
    name = payload.get("name")
    group = ""
    if act == "group":
        group = str(name or "").strip()
        scope.set_group(project, ids, group)
    elif act == "arrange":
        by = {s.id: s for s in project.sheets}
        order = [str(x) for x in payload.get("order") or []]
        project.sheets = [by[i] for i in order if i in by] + [s for s in project.sheets if s.id not in set(order)]
        per: dict[str, list[str]] = {}
        for sid, g in (payload.get("groups") or {}).items():
            if str(sid) in by:
                per.setdefault(str(g or "").strip(), []).append(str(sid))
        for g, sids in per.items():
            scope.set_group(project, sids, g)
    elif act == "rename":
        group = str(name or "").strip()
        try:
            scope.rename_group(project, str(payload.get("old") or ""), group)
        except ValueError as exc:
            raise ServiceError("group_exists", str(exc), group=group) from exc
    elif act == "isolate":
        group = scope.isolate(project, ids)
    elif act == "unisolate":
        group = str(name or "")
        scope.unisolate(project, group)
    elif act == "new_sheets":
        here = project.sheet(str(payload.get("sheet") or ""))
        made = [s for s in project.sheets if s.id in set(ids)]
        group = scope.group_new_sheets(project, made, name, here)
    else:
        raise ServiceError("bad_request", "'action' must be one of: group, arrange, rename, isolate, unisolate, new_sheets",
                           field="action")
    return {"ok": True, "project": project.to_dict(), "group": group}

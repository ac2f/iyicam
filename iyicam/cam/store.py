"""Calculated toolpaths in a project file: what was calculated is kept as it was, so a file that is opened shows its toolpaths
without calculating them again (a hand-made order, start points moved by hand and what an older version of the algorithm made
all stay). The kept toolpaths are used only while the project is exactly what they were calculated from (its signature)."""
from __future__ import annotations

import hashlib
import json

from ..core.document import Project, contour_from_dict, contour_to_dict
from ..core.tooling import Tool
from .job import ComputeResult
from .moves import ArcMove, Comment, DrillCycle, Dwell, Feed, Rapid, Toolpath

VERSION = 1


def signature(project: Project) -> str:
    """What the toolpaths depend on: the whole project but its name, its metadata (the revision numbers ...) and what a toolpath
    is called, whether it is on and which group it is in."""
    d = project.to_dict()
    d.pop("name", None)
    d.pop("metadata", None)
    for t in d.get("toolpaths", []):
        for k in ("name", "enabled", "group"):
            t.pop(k, None)
    return hashlib.sha1(json.dumps(d, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def toolpath_signature(project: Project, tdef) -> str:
    """What one toolpath depends on: its own settings (not its name, whether it is on or its group), the material and, on every
    sheet it is calculated on, the sheet and the vectors it machines. A change anywhere else leaves it as it is."""
    from ..core.document import toolpath_to_dict, vector_to_dict
    from .job import sheets_for
    t = toolpath_to_dict(tdef)
    for k in ("name", "enabled", "group"):
        t.pop(k, None)
    seq = dict(t.get("sequence") or {})
    if seq.get("auto_order", True):
        seq.pop("order", None)                  # (an automatic order is what the calculation writes back: not a setting)
    t["sequence"] = seq
    sheets = []
    for sh in sheets_for(project, tdef):
        machined = []
        for o in project.vectors_for(tdef, sh):
            d = vector_to_dict(o)
            d.pop("name", None)
            machined.append(d)
        sheets.append({"id": sh.id, "w": sh.width, "h": sh.height, "t": project.sheet_thickness(sh),
                       "outline": sh.outline, "machined": machined})
    m = dict(vars(project.material))
    blob = json.dumps([t, m, sheets], sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha1(blob.encode()).hexdigest()


def _r(v: float) -> float:
    return round(float(v), 9)


def _move(m) -> list:
    if isinstance(m, Rapid):
        return ["r", _r(m.x), _r(m.y), _r(m.z)]
    if isinstance(m, Feed):
        return ["f", _r(m.x), _r(m.y), _r(m.z), _r(m.f)]
    if isinstance(m, ArcMove):
        return ["a", _r(m.x), _r(m.y), _r(m.z), _r(m.cx), _r(m.cy), 1 if m.cw else 0, _r(m.f)]
    if isinstance(m, DrillCycle):
        return ["d", _r(m.x), _r(m.y), _r(m.z), _r(m.r), _r(m.f), _r(m.peck), _r(m.dwell)]
    if isinstance(m, Dwell):
        return ["w", _r(m.seconds)]
    return ["c", getattr(m, "text", "")]


def _unmove(a: list):
    k = a[0]
    if k == "r":
        return Rapid(*a[1:4])
    if k == "f":
        return Feed(*a[1:5])
    if k == "a":
        return ArcMove(a[1], a[2], a[3], a[4], a[5], bool(a[6]), a[7])
    if k == "d":
        return DrillCycle(*a[1:8])
    if k == "w":
        return Dwell(a[1])
    return Comment(a[1])


def _cut(c: dict) -> dict:
    out = {}
    for k, v in c.items():
        if k in ("path", "loop") and v is not None:
            out[k] = {"__contour__": contour_to_dict(v)}
        elif isinstance(v, tuple):
            out[k] = {"__tuple__": [list(x) if isinstance(x, tuple) else x for x in v]}
        elif isinstance(v, (str, int, float, bool)) or v is None:
            out[k] = v
        elif isinstance(v, list):
            out[k] = v
    return out


def _uncut(c: dict) -> dict:
    out = {}
    for k, v in c.items():
        if isinstance(v, dict) and "__contour__" in v:
            out[k] = contour_from_dict(v["__contour__"])
        elif isinstance(v, dict) and "__tuple__" in v:
            out[k] = tuple(tuple(x) if isinstance(x, list) else x for x in v["__tuple__"])
        else:
            out[k] = v
    return out


def results_to_dict(project: Project, results: dict) -> dict:
    items = []
    for (tid, sid), r in results.items():
        items.append({
            "toolpath": tid, "sheet": sid, "error": r.error, "warnings": list(r.warnings), "order": r.order,
            "start_updates": {k: [_r(q[0]), _r(q[1])] for k, q in (r.start_updates or {}).items()},
            "toolpaths": [{"name": tp.name, "tool": tp.tool.to_dict(), "op": tp.op, "def_id": tp.def_id, "sheet_id": tp.sheet_id,
                           "safe_z": tp.safe_z, "color": tp.color, "warnings": list(tp.warnings),
                           "moves": [_move(m) for m in tp.moves], "cuts": [_cut(c) for c in tp.cuts]} for tp in r.toolpaths]})
    return {"version": VERSION, "sig": signature(project), "items": items}


def results_from_dict(d: dict) -> dict:
    out = {}
    for it in d.get("items", []):
        tps = []
        for t in it.get("toolpaths", []):
            tp = Toolpath(t["name"], Tool.from_dict(t["tool"]), [_unmove(m) for m in t.get("moves", [])], t.get("op", ""),
                          t.get("def_id", ""), t.get("sheet_id", ""), t.get("safe_z", 10.0), t.get("color", ""),
                          list(t.get("warnings", [])), [_uncut(c) for c in t.get("cuts", [])])
            tps.append(tp)
        out[(it["toolpath"], it["sheet"])] = ComputeResult(
            tps, list(it.get("warnings", [])), it.get("error"), it.get("order"),
            {k: tuple(q) for k, q in (it.get("start_updates") or {}).items()})
    return out


def usable(project: Project, d) -> bool:
    """Whether the kept toolpaths are of this project as it is now."""
    return isinstance(d, dict) and d.get("version") == VERSION and d.get("sig") == signature(project)

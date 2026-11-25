"""Looking at and editing the cut order, the cutting directions and the start points of a project, without a window.

The desktop program does this in its controller (``gui/cutcontrol.py``, ``gui/controller.py``); these are the same steps on a
:class:`~iyicam.core.document.Project`, so that the web app (or any other interface) calls them by name.  Nothing here
calculates: the caller gives what was calculated (``ComputeResult``) and calculates again when it wants the new picture."""
from __future__ import annotations

from ..core import vectorops
from ..core.document import Project, Sheet
from ..core.geom import Contour, Pt, dist
from . import cutinfo
from . import sequence as seq

#: where a start point can be put (corners: the extreme point that way; sides: the middle of that side)
PLACES = {"sw": "bottom left corner", "se": "bottom right corner", "nw": "top left corner", "ne": "top right corner",
          "s": "bottom", "n": "top", "w": "left", "e": "right"}


def options() -> dict:
    """What the cut order panel offers: the strategies in groups, the cutting directions, the inner-piece groupings and the
    defaults of every setting."""
    groups: list[dict] = []
    for key, label, group in seq.grouped_strategies():
        if not groups or groups[-1]["group"] != group:
            groups.append({"group": group, "strategies": []})
        groups[-1]["strategies"].append({"key": key, "label": label})
    return {"strategies": groups, "directions": [{"key": k, "label": v} for k, v in seq.DIRECTIONS],
            "groupings": [{"key": k, "label": v} for k, v in seq.GROUPINGS], "defaults": seq.settings({}),
            "places": [{"key": k, "label": v} for k, v in PLACES.items()]}


def place_point(c: Contour, place: str) -> Pt | None:
    """The point of a closed contour for a place: its extreme point towards a corner (``sw``, ``se``, ``nw``, ``ne``) or the
    point of a side's middle (``s``, ``n``, ``w``, ``e``)."""
    bb = c.bbox()
    dirs = {"sw": (-1, -1), "se": (1, -1), "nw": (-1, 1), "ne": (1, 1)}
    if place in dirs:
        dx, dy = dirs[place]
        pts = c.nodes() + c.flatten(0.05)
        return max(pts, key=lambda p: (dx * p[0] + dy * p[1], -dist(p, bb.center) * 1e-9))
    mid = {"s": (bb.center[0], bb.ymin), "n": (bb.center[0], bb.ymax),
           "w": (bb.xmin, bb.center[1]), "e": (bb.xmax, bb.center[1])}.get(place)
    return None if mid is None else c.nearest(mid)[2]


def real_cuts(res) -> list[dict]:
    """The cuts of one calculated toolpath on one sheet in machining order."""
    return cutinfo.real_cuts([c for tp in (res.toolpaths if res else []) for c in tp.cuts])


def listing(cuts: list[dict], home: Pt = (0.0, 0.0), polylines: bool = False) -> list[dict]:
    """The cuts as plain data: ``key`` (``vector id:contour``), ``closed``, ``direction`` (``cw`` / ``ccw`` / ``None``),
    ``quality``, ``place``, ``lines`` (what is said about the start point), ``start``, ``entry``, ``end`` and, with
    ``polylines``, ``poly`` (the cut as a flat list of x, y)."""
    out = []
    for c in cuts:
        e = cutinfo.explain(c)
        item = {"key": c["key"], "closed": bool(c.get("closed")), "direction": e["direction"], "quality": e["quality"],
                "place": e["place"], "lines": e["lines"], "size": round(cutinfo.cut_size(c), 3),
                "start": [round(v, 4) for v in c["start"]], "entry": [round(v, 4) for v in (c.get("entry") or c["start"])],
                "end": [round(v, 4) for v in (c.get("end") or c.get("close") or c["start"])]}
        if polylines and c.get("path") is not None and c["path"].segs:
            pts = c["path"].flatten(0.2)
            item["poly"] = [round(v, 3) for p in pts for v in p]
        out.append(item)
    return out


def summary(before: list[dict], after: list[dict], home: Pt) -> dict:
    """What the cut order of ``after`` changes against ``before`` (a preview): cuts at another place, the travel between the
    cuts in mm, and the cuts that are cut the other way round."""
    ob, oa = [c["key"] for c in before], [c["key"] for c in after]
    reordered = sum(1 for a, b in zip(ob, oa) if a != b) + abs(len(ob) - len(oa))
    was = {c["key"]: cutinfo.cut_direction(c) for c in before}
    return {"reordered": reordered, "route_before": round(cutinfo.route_length(before, home), 2),
            "route_after": round(cutinfo.route_length(after, home), 2),
            "directions": sum(1 for c in after if c["key"] in was and was[c["key"]] != cutinfo.cut_direction(c))}


# ------------------------------------------------------------------------------------------------ the order
def sheet_cut_keys(project: Project, tid: str, sheet: Sheet, res) -> list[str]:
    """The cuts of a toolpath on a sheet in the order they are made; cuts the stored order does not have yet follow in the order
    the calculation made them (the same as the desktop's ``sheet_cut_keys``)."""
    t = project.toolpath(tid)
    if t is None:
        return []
    on_sheet = {f"{o.id}:{i}" for o in project.vectors_for(t, sheet) for i in range(len(o.contours))}
    mine = [k for k in t.sequence.get("order", []) if k in on_sheet]
    known = set(mine)
    made = list(res.order or []) if res is not None else []
    keys = [f"{o.id}:{i}" for o in project.vectors_for(t, sheet) for i, c in enumerate(o.contours) if c.segs]
    valid = set(keys)
    extra = [k for k in dict.fromkeys(made) if k in valid and k not in known]
    known |= set(extra)
    return mine + extra + [k for k in keys if k not in known]


def set_sheet_order(project: Project, tid: str, new_mine: list[str]) -> bool:
    """Cut this sheet's cuts of a toolpath in the order ``new_mine`` (the other sheets keep theirs); freezes the order."""
    t = project.toolpath(tid)
    if t is None or not new_mine:
        return False
    want = set(new_mine)
    it = iter(new_mine)
    order = [next(it) if k in want else k for k in t.sequence.get("order", [])]
    order += list(it)
    t.sequence["order"] = order
    t.sequence["auto_order"] = False
    return True


def manual_order(project: Project, tid: str, sheet: Sheet, res, index: int, picked: list[str], mode: str = "from") -> bool:
    """Cut ``picked`` from place ``index`` (0 based) on (``mode`` ``from``), or that many places from the end (``end``), or
    swapped in pairs (``swap``); the order is frozen."""
    keys = sheet_cut_keys(project, tid, sheet, res)
    if mode == "end":
        new = seq.manual_from_end(keys, index, picked)
    elif mode == "swap":
        new = seq.swapped(keys, list(zip(picked[0::2], picked[1::2])))
    else:
        new = seq.manual_from(keys, index, picked)
    return set_sheet_order(project, tid, new)


# ------------------------------------------------------------------------------------------------ start points
def contour_of(project: Project, key: str):
    vid, _, ci = key.rpartition(":")
    found = project.find_vector(vid)
    if found is None or not ci.lstrip("-").isdigit() or not 0 <= int(ci) < len(found[1].contours):
        return None
    return found[1], int(ci), found[1].contours[int(ci)]


def set_starts(project: Project, items: list[tuple[str, int, Pt]], lock: bool = True) -> int:
    """Start contours at points: ``(vector id, contour index, point)``; the start is kept there (``lock``). How many
    contours now start where they were asked to."""
    done = 0
    for vid, ci, point in items:
        found = project.find_vector(vid)
        if found is None:
            continue
        o = found[1]
        if not 0 <= ci < len(o.contours) or not o.contours[ci].closed or not o.contours[ci].segs:
            continue
        o.contours[ci] = vectorops.set_start_point(o.contours[ci], point)
        if lock and ci not in o.locked_starts:
            o.locked_starts.append(ci)
        done += 1
    return done


def move_start(project: Project, key: str, delta: float) -> bool:
    """Move the start point of a closed contour ``delta`` mm along it and keep it there."""
    got = contour_of(project, key)
    if got is None or not got[2].closed:
        return False
    o, ci, c = got
    length = c.length()
    if length <= 1e-9 or abs(delta) < 1e-9:
        return False
    return bool(set_starts(project, [(o.id, ci, c.point_at_length(delta % length))]))


def start_at_place(project: Project, key: str, place: str) -> bool:
    got = contour_of(project, key)
    if got is None or not got[2].closed:
        return False
    q = place_point(got[2], place)
    return q is not None and bool(set_starts(project, [(got[0].id, got[1], q)]))


def release_starts(project: Project, vector_ids: list[str] | None = None, key: str | None = None) -> int:
    """Give start points set by hand back to smart start points (the contour of ``key``, the vectors ``vector_ids``, or all)."""
    n = 0
    for _s, o in project.iter_vectors():
        if key is not None:
            got = contour_of(project, key)
            if got is None or got[0] is not o or got[1] not in o.locked_starts:
                continue
            o.locked_starts.remove(got[1])
            n += 1
        elif (vector_ids is None or o.id in vector_ids) and o.locked_starts:
            n += len(o.locked_starts)
            o.locked_starts = []
    return n

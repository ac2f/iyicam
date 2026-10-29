"""What a calculated toolpath says about its cuts: the route between them, the direction they are cut in and how
good their start points are. Used to show the cut order on the drawing and to explain it in words."""
from __future__ import annotations

import math

from ..core.geom import Pt, dist


def real_cuts(cuts: list[dict]) -> list[dict]:
    """The cuts of a toolpath in machining order, one per vector contour (a finish pass follows its roughing cut)."""
    return sorted((c for c in cuts if not c.get("finish") and "start" in c), key=lambda c: c["index"])


def route(cuts: list[dict], home: Pt) -> list[tuple[Pt, Pt]]:
    """The rapid moves between the cuts: ``(from, to)`` from where the tool is done with a cut to where it goes
    down for the next (the first from ``home``)."""
    out = []
    pos = home
    for c in real_cuts(cuts):
        q = tuple(c.get("entry") or c["start"])
        out.append((tuple(pos), q))
        pos = tuple(c.get("end") or c.get("close") or c["start"])
    return out


def route_length(cuts: list[dict], home: Pt) -> float:
    """The length in mm of the rapid moves between the cuts (what a better order saves)."""
    return sum(dist(a, b) for a, b in route(cuts, home))


def direction(path) -> str | None:
    """``"cw"`` or ``"ccw"`` for a closed path the tool walks, ``None`` for an open one."""
    if path is None or not getattr(path, "closed", False) or not path.segs:
        return None
    return "ccw" if path.is_ccw() else "cw"


def cut_direction(cut: dict) -> str | None:
    """:func:`direction` of the path of a cut (worked out once: a calculated cut does not change)."""
    if "_dir" not in cut:
        cut["_dir"] = direction(cut.get("path"))
    return cut["_dir"]


def cut_size(cut: dict) -> float:
    """The longer side of the box around the path of a cut, in mm (worked out once)."""
    if "_size" not in cut:
        path = cut.get("path")
        bb = path.bbox() if path is not None and path.segs else None
        cut["_size"] = max(bb.width, bb.height) if bb is not None else 0.0
    return cut["_size"]


DIRECTION_NAMES = {"cw": "clockwise", "ccw": "counter-clockwise", None: "along the line"}
DIRECTION_ARROWS = {"cw": "↻", "ccw": "↺", None: "→"}


def place_on(point: Pt, box) -> str:
    """Where ``point`` lies on a box, in words: ``"right side"``, ``"top left corner"``, ..."""
    w, h = box.width, box.height
    u = (point[0] - box.xmin) / w if w > 1e-9 else 0.5
    v = (point[1] - box.ymin) / h if h > 1e-9 else 0.5
    horiz = "left" if u < 0.25 else "right" if u > 0.75 else ""
    vert = "bottom" if v < 0.25 else "top" if v > 0.75 else ""
    if horiz and vert:
        return f"{vert} {horiz} corner"
    if horiz or vert:
        return f"{horiz or vert} side"
    return "middle"


def held_word(h: float) -> str:
    return "well held" if h >= 0.66 else "held" if h >= 0.4 else "weakly held"


def explain(cut: dict) -> dict:
    """``{"direction", "place", "quality", "lines"}`` for a cut: where it closes, how it is held and why the start
    point is (or is not) a good one. ``quality`` is ``"hand"`` (set by hand), ``"good"``, ``"warn"`` or ``"plain"``
    (not optimised)."""
    if "_explain" in cut:
        return cut["_explain"]
    path = cut.get("path") or cut.get("loop")
    d = cut_direction(cut)
    lines = [f"Cut {DIRECTION_NAMES[d]}"]
    close = cut.get("close")
    box = path.bbox() if path is not None and path.segs else None
    place = place_on(close, box) if close is not None and box is not None else ""
    quality = "plain"
    if "ok" in cut:
        if close is not None:
            lines.append(f"Closes on the {place}")
        if cut.get("locked"):
            quality = "hand"
            lines.append("Start point set by hand")
        if "held" in cut and not cut.get("locked"):
            lines.append(f"The last stretch is {held_word(cut['held'])} by the material around it")
        if cut.get("corner") is not None and math.isfinite(cut["corner"]):
            lines.append(f"{cut['corner']:.1f} mm along the cut from the nearest corner")
        gap = cut.get("gap")
        if gap is not None:
            lines.append("No cut area near the plunge" if not math.isfinite(gap)
                         else f"{max(gap, 0.0):.1f} mm of material between the plunge and cut areas")
        if cut["ok"]:
            quality = quality if quality == "hand" else "good"
        else:
            quality = "warn" if quality != "hand" else "hand"
            lines.append("Too close to a corner or to an area that is cut already")
    else:
        lines.append("Start point as drawn (smart start points are off)" if close is not None else "Open: cut on the line")
    cut["_explain"] = {"direction": d, "place": place, "quality": quality, "lines": lines}
    return cut["_explain"]

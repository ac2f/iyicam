#!/usr/bin/env python3
"""Make the "golden" numbers a browser implementation of iyiCAM's editing is tested against.

The mobile page edits a project in JavaScript (move, turn, scale, mirror, align, arrays, ...).  To be sure it gives
what this program gives, this script runs the real commands on a project made of every kind of shape and writes
the inputs and the results to a JSON file; the JavaScript tests run the same inputs and compare.

    python tools/make_js_golden.py -o golden.json

The file belongs in the other project (Cnc-Assistant/tests/js/fixtures/golden.json); make it again whenever a
command of the list below changes here.
"""
from __future__ import annotations

import argparse
import copy
import json
import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from iyicam import __version__  # noqa: E402
from iyicam.builtins import ensure_registered  # noqa: E402
from iyicam.core import shapes  # noqa: E402
from iyicam.core.commands import CommandContext, CommandError, run_command  # noqa: E402
from iyicam.core.document import Project, VectorObject, project_from_dict, vector_to_dict  # noqa: E402
from iyicam.core.geom import Arc, Bezier, Contour, Line  # noqa: E402
from iyicam.core.params import Param  # noqa: E402
from iyicam.core.registry import REGISTRY  # noqa: E402
from iyicam.service.wire import jsonable, param_to_dict  # noqa: E402

LOCAL = ["xform.move", "xform.move_to", "xform.rotate", "xform.scale", "xform.size", "xform.mirror", "xform.slant",
         "xform.rot90l", "xform.rot90r", "xform.rot180", "xform.flip_h", "xform.flip_v", "arrange.align",
         "arrange.center", "arrange.distribute", "arrange.array", "arrange.circular", "edit.group", "edit.ungroup",
         "edit.reverse", "select.all", "select.invert", "select.open"]


def build_project() -> Project:
    p = Project("golden")
    p.layers[0].id = "L0"
    lay2 = p.add_layer("Second")
    lay2.id = "L1"
    s = p.sheets[0]
    s.id, s.width, s.height = "S0", 500.0, 300.0
    open_mixed = Contour([Line((300, 200), (330, 230)), Arc((330, 230), (360, 230), -0.6),
                          Bezier((360, 230), (375, 260), (395, 200), (410, 215))], False)
    compound = [shapes.rectangle(20, 150, 90, 60), shapes.circle((65, 180), 18)]
    arc_only = Contour([Arc((200, 200), (260, 200), 0.4)], False)
    point_like = Contour([Line((450, 20), (450, 20))], False)
    items = [
        ("v01", "L0", "Rect", [shapes.rectangle(10, 10, 100, 60)]),
        ("v02", "L0", "Rounded", [shapes.rectangle(130, 10, 80, 50, corner_radius=12)]),
        ("v03", "L1", "Circle", [shapes.circle((250, 40), 25)]),
        ("v04", "L1", "Ellipse", [shapes.ellipse((330, 40), 30, 15, 20)]),
        ("v05", "L0", "Mixed", [open_mixed]),
        ("v06", "L0", "Compound", compound),
        ("v07", "L1", "Star", [shapes.star((160, 110), 30, 14, 5, 10)]),
        ("v08", "L1", "ArcOnly", [arc_only]),
        ("v09", "L0", "Slot", [shapes.slot((300, 130), (380, 150), 16)]),
        ("v10", "L0", "Locked", [shapes.rectangle(400, 100, 40, 40)]),
        ("v11", "L0", "Point", [point_like]),
    ]
    for vid, layer, name, cs in items:
        s.vectors.append(VectorObject(cs, layer, name, vid, [0] if vid == "v10" else []))
    return p


SELECTIONS = {
    "one": ["v01"],
    "three": ["v01", "v02", "v03"],
    "unordered": ["v03", "v01", "v02"],
    "arcs": ["v03", "v08", "v09"],
    "compound": ["v06"],
    "mixed": ["v04", "v05", "v07", "v10"],
    "all": [f"v{i:02d}" for i in range(1, 11)],
    "point": ["v11"],
    "none": [],
}

COPY = {"copy": True}
CASES: list[tuple[str, dict, list[str]]] = []


def case(cmd, values, *sels):
    for s in sels:
        CASES.append((cmd, values, s))


case("xform.move", {"dx": 12.5, "dy": -3}, "one", "three", "unordered", "compound", "mixed")
case("xform.move", {"dx": 7, "dy": 9, **COPY}, "one", "arcs", "compound")
case("xform.move", {"dx": 1, "dy": 1}, "none")
for anchor in ("bottom_left", "center", "top_left", "top_right", "bottom_right"):
    case("xform.move_to", {"anchor": anchor, "x": 100, "y": 50}, "three", "arcs")
case("xform.rotate", {"angle": 33}, "one", "arcs", "mixed", "compound")
case("xform.rotate", {"angle": 90, "about": "origin"}, "three", "arcs")
case("xform.rotate", {"angle": -45, "about": "point", "px": 120, "py": 80}, "three", "compound")
case("xform.rotate", {"angle": 17, **COPY}, "one", "mixed")
case("xform.scale", {"sx": 150}, "one", "arcs", "mixed", "compound")
case("xform.scale", {"sx": 50, "sy": 200, "uniform": False}, "arcs", "compound", "three")
case("xform.scale", {"sx": 80, "sy": 120, "uniform": False, "about": "origin", **COPY}, "arcs", "mixed")
case("xform.scale", {"sx": 200, "about": "point", "px": 10, "py": 10}, "three")
case("xform.size", {"width": 80, "height": 80}, "one", "arcs", "mixed")
case("xform.size", {"width": 80, "height": 20, "uniform": False}, "arcs", "compound", "three")
case("xform.size", {"width": 50, "height": 50}, "point")
case("xform.mirror", {"axis": "horizontal"}, "arcs", "mixed", "compound")
case("xform.mirror", {"axis": "vertical", "about": "point", "px": 200, "py": 150}, "three", "arcs")
case("xform.mirror", {"axis": "horizontal", **COPY}, "one", "arcs")
case("xform.slant", {"angle_x": 15, "angle_y": 0}, "one", "arcs", "compound")
case("xform.slant", {"angle_x": 30, "angle_y": 10, "about": "origin"}, "mixed", "three")
case("xform.slant", {"angle_x": -20, "angle_y": 5, **COPY}, "arcs")
for c in ("xform.rot90l", "xform.rot90r", "xform.rot180", "xform.flip_h", "xform.flip_v"):
    case(c, {}, "one", "arcs", "mixed", "compound", "unordered")
for mode in ("left", "hcenter", "right", "bottom", "vcenter", "top", "center"):
    for relative in ("selection", "sheet"):
        case("arrange.align", {"mode": mode, "relative": relative, "each": True}, "three")
        case("arrange.align", {"mode": mode, "relative": relative, "each": False}, "arcs")
case("arrange.center", {}, "one", "three", "mixed")
case("arrange.distribute", {"axis": "x"}, "all", "unordered", "arcs")
case("arrange.distribute", {"axis": "y"}, "all", "mixed")
case("arrange.distribute", {"axis": "x"}, "one")
case("arrange.array", {"rows": 2, "cols": 3, "spacing": "gap", "dx": 10, "dy": 10}, "one", "arcs", "compound")
case("arrange.array", {"rows": 3, "cols": 2, "spacing": "pitch", "dx": 60, "dy": 40}, "three")
case("arrange.array", {"rows": 1000, "cols": 1000}, "three")
case("arrange.circular", {"count": 6, "angle": 360, "about": "point", "cx": 250, "cy": 150}, "one", "arcs")
case("arrange.circular", {"count": 4, "angle": 180, "about": "sheet", "rotate_copies": False}, "one", "three")
case("arrange.circular", {"count": 5, "angle": 90, "about": "origin"}, "compound")
case("edit.group", {"name": "Together"}, "three", "unordered", "arcs")
case("edit.group", {}, "mixed")
case("edit.group", {}, "one")
case("edit.ungroup", {}, "compound", "three", "all")
case("edit.reverse", {}, "one", "arcs", "mixed", "compound")
for c in ("select.all", "select.invert", "select.open"):
    case(c, {}, "none", "three", "all")

PARAM_CASES = [
    (Param("a", "A", "float", 1.5), [None, "2.5", 3, "x1", True, 7.25]),
    (Param("a", "A", "length", 5.0, min=0.0, max=100.0), [-5, 150, "50", 0, 100]),
    (Param("a", "A", "angle", 90.0), [45, "-30", 400]),
    (Param("a", "A", "percent", 100.0, min=0.001), [0, 50, "75.5"]),
    (Param("a", "A", "int", 3, min=1, max=1000), [0, 2.4, 2.5, 2.6, "7", 5000, -1]),
    (Param("a", "A", "bool", False), [True, False, "yes", "No", "on", "1", "true", "0", 1, 0, "TRUE"]),
    (Param("a", "A", "choice", "x", choices=[("x", "X"), ("y", "Y")]), ["y", "z", "x", 5, None]),
    (Param("a", "A", "point", None), [[1, 2], ["3", "4.5"], (7, 8)]),
    (Param("a", "A", "str", ""), ["hello", 5, "12.50"]),   # (what a text field gives; str(True) is not one of them)
    (Param("a", "A", "text", ""), ["multi\nline"]),
    (Param("a", "A", "font", "Arial"), ["Verdana"]),
    (Param("a", "A", "feed", 1000.0), ["1500", 20]),
]


def norm_contour(c: dict, nd: int = 6) -> dict:
    def r(v):
        return round(v, nd) + 0.0 if isinstance(v, float) else v
    return {"closed": c["closed"], "start": [r(x) for x in c["start"]],
            "segs": [[s[0]] + [r(x) for x in s[1:]] for s in c["segs"]]}


def run_case(project_dict: dict, cmd_id: str, values: dict, selection: list[str]) -> dict:
    p = project_from_dict(copy.deepcopy(project_dict))
    sheet = p.sheets[0]
    ctx = CommandContext(p, sheet, selection)
    try:
        run_command(REGISTRY.commands[cmd_id], ctx, values)
    except CommandError as exc:
        return {"ok": False, "error": str(exc)}
    keep = ctx.new_selection if ctx.new_selection is not None else selection
    vectors = []
    for v in sheet.vectors:
        d = vector_to_dict(v)
        d["contours"] = [norm_contour(c) for c in d["contours"]]
        vectors.append(d)
    return {"ok": True, "vectors": vectors, "selection_after": [v.id for v in sheet.pick(keep)],
            "messages": list(ctx.messages)}


def geometry(project: Project) -> list[dict]:
    out = []
    for v in project.sheets[0].vectors:
        bb = v.bbox()
        out.append({"id": v.id, "bbox": None if bb.empty else [bb.xmin, bb.ymin, bb.xmax, bb.ymax],
                    "flat": [[[round(x, 6), round(y, 6)] for x, y in c.flatten(0.05)] for c in v.contours]})
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("-o", "--output", required=True)
    a = ap.parse_args(argv)
    ensure_registered(REGISTRY)
    project = build_project()
    pd = project.to_dict()
    schema = [{"id": c.id, "label": c.label, "min_selection": c.min_selection,
               "params": [param_to_dict(x) for x in c.params]} for c in (REGISTRY.commands[i] for i in LOCAL)]
    cases = [{"command": cmd, "values": values, "selection": SELECTIONS[sel], "selection_name": sel,
              "sheet": "S0", **run_case(pd, cmd, values, SELECTIONS[sel])} for cmd, values, sel in CASES]
    params = [{"param": param_to_dict(p), "value": jsonable(v),
               "expected": jsonable(p.coerce(v))} for p, vals in PARAM_CASES for v in vals
              if not (isinstance(v, str) and v.startswith("x1") and p.kind in ("float",))]
    out = {"generator": "iyicam tools/make_js_golden.py", "iyicam": __version__, "project": pd, "schema": schema,
           "geometry": geometry(project), "cases": cases, "params": params}
    Path(a.output).write_text(json.dumps(out, separators=(",", ":"), allow_nan=False), encoding="utf-8")
    ok = sum(1 for c in cases if c["ok"])
    print(f"{len(cases)} cases ({ok} ok, {len(cases) - ok} refused), {len(params)} parameter cases -> {a.output}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

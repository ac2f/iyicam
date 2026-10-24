"""Built-in vector commands (creation, editing, transforms, arrays, alignment)."""
from __future__ import annotations

import math

from ..core import doors, shapes, vectorops
from ..core.commands import Command, CommandContext, CommandError
from ..core.document import VectorObject
from ..core.geom import BBox, Contour, Transform
from ..core.params import Param

CREATE = "Vectors/Create"
EDIT = "Vectors/Edit"
CORNER = "Vectors/Corners"
XFORM = "Vectors/Transform"
ARRANGE = "Vectors/Arrange"
SELECT = "Edit/Select"


def _sel_bbox(ctx: CommandContext) -> BBox:
    bb = BBox()
    for o in ctx.selected:
        bb = bb.union(o.bbox())
    return bb


def _centre(ctx: CommandContext, values: dict) -> tuple[float, float]:
    x, y = values.get("x"), values.get("y")
    return (ctx.sheet.width / 2 if x is None else x, ctx.sheet.height / 2 if y is None else y)


def _pos_params(label: str = "Centre") -> list[Param]:
    return [Param("x", f"{label} X (empty = sheet centre)", "length", None, group="Position"),
            Param("y", f"{label} Y (empty = sheet centre)", "length", None, group="Position")]


# ---------------------------------------------------------------------------
# creation
# ---------------------------------------------------------------------------

def _rect(ctx, v):
    cx, cy = _centre(ctx, v)
    ctx.add(shapes.rectangle_centered(cx, cy, v["width"], v["height"], corner_radius=v["radius"],
                                      corner=v["corner"], rotation=v["angle"]), name="Rectangle")


def _circle(ctx, v):
    ctx.add(shapes.circle(_centre(ctx, v), v["diameter"] / 2), name="Circle")


def _ellipse(ctx, v):
    ctx.add(shapes.ellipse(_centre(ctx, v), v["width"] / 2, v["height"] / 2, v["angle"]), name="Ellipse")


def _polygon(ctx, v):
    ctx.add(shapes.polygon(_centre(ctx, v), v["radius"], v["sides"], v["angle"], v["inscribed"],
                           v["corner_radius"]), name="Polygon")


def _star(ctx, v):
    ctx.add(shapes.star(_centre(ctx, v), v["outer"], v["inner"], v["points"], v["angle"]), name="Star")


def _slot(ctx, v):
    cx, cy = _centre(ctx, v)
    a = math.radians(v["angle"])
    h = v["length"] / 2 - v["width"] / 2
    p0 = (cx - h * math.cos(a), cy - h * math.sin(a))
    p1 = (cx + h * math.cos(a), cy + h * math.sin(a))
    ctx.add(shapes.slot(p0, p1, v["width"]), name="Slot")


def _arc(ctx, v):
    ctx.add(shapes.arc(_centre(ctx, v), v["radius"], v["start"], v["end"], v["ccw"]), name="Arc")


def _spiral(ctx, v):
    ctx.add(shapes.spiral(_centre(ctx, v), v["start_radius"], v["pitch"], v["turns"]), name="Spiral")


def _bbox_rect(ctx, v):
    bb = _sel_bbox(ctx)
    if bb.empty:
        raise CommandError("Nothing selected")
    bb = bb.inflated(v["offset"])
    ctx.add(shapes.rectangle(bb.xmin, bb.ymin, bb.width, bb.height, v["radius"]), name="Boundary")


def _door(ctx, v):
    from ..core.document import Selection, ToolpathDef
    try:
        d = doors.door(0.0, 0.0, v["width"], v["height"], v["frame"], v["panels"], v["rail"], v["style"],
                       v["radius"], v["arch"], v["outer_radius"], v["grooves"], v["groove_spacing"],
                       v["groove_side"])
    except ValueError as exc:
        raise CommandError(str(exc)) from None
    cx, cy = _centre(ctx, v)
    T = Transform.translate(cx - v["width"] / 2, cy - v["height"] / 2)

    def layer(name):
        lay = ctx.project.layer_by_name(name) or ctx.project.add_layer(name)
        return lay.id

    outline = ctx.add(d.outline.transformed(T), layer("Door outline"), "Door")
    panels = [ctx.add(c.transformed(T), layer("Door panels"), "Panel") for c in d.panels]
    grooves = [ctx.add(c.transformed(T), layer("Door grooves"), "Groove") for c in d.grooves]
    jobs = [("Door grooves", "engrave", v["groove_tool"], grooves, {"cut_depth": v["groove_depth"]}),
            ("Door pocket", "pocket", v["pocket_tool"], panels, {"cut_depth": v["pocket_depth"]}),
            ("Door panel edge", "engrave", v["edge_tool"], panels, {"cut_depth": v["pocket_depth"]}),
            ("Door cut-out", "profile", v["cut_tool"], [outline], {"side": "outside", "through": True})]
    for name, op, tool, objs, params in jobs:
        if tool is not None and objs:
            ctx.project.toolpaths.append(ToolpathDef(name, op, tool, params,
                                                     Selection("vectors", [o.id for o in objs])))


# ---------------------------------------------------------------------------
# editing
# ---------------------------------------------------------------------------

def _offset(ctx, v):
    d = v["distance"] * (1 if v["direction"] == "outward" else -1)
    join = "miter" if v["corners"] == "sharp" else "round"
    new = []
    for o in ctx.selected:
        cs = vectorops.offset(o.contours, d, join)
        if cs:
            new.append(ctx.add(cs, o.layer_id if v["layer"] == "same" else None, "Offset"))
    if not new:
        raise CommandError("Offset produced no vectors (distance too large?)")
    if not v["keep"]:
        ctx.remove([o.id for o in ctx.selected])
    ctx.select([o.id for o in new])


def _weld(ctx, v):
    sel = ctx.selected
    out = vectorops.weld([o.contours for o in sel])
    ctx.remove([o.id for o in sel])
    new = ctx.add(out, sel[0].layer_id, "Welded")
    ctx.select([new.id])


def _subtract(ctx, v):
    sel = ctx.selected
    base = max(sel, key=lambda o: o.bbox().width * o.bbox().height) if v["base"] == "largest" else sel[0]
    others = [c for o in sel if o is not base for c in o.contours]
    out = vectorops.boolean(base.contours, others, "difference")
    ctx.remove([o.id for o in sel])
    if out:
        ctx.select([ctx.add(out, base.layer_id, "Subtracted").id])


def _intersect(ctx, v):
    sel = ctx.selected
    cur = sel[0].contours
    for o in sel[1:]:
        cur = vectorops.boolean(cur, o.contours, "intersection")
    ctx.remove([o.id for o in sel])
    if cur:
        ctx.select([ctx.add(cur, sel[0].layer_id, "Intersection").id])


def _join(ctx, v):
    sel = ctx.selected
    by_layer: dict[str, list[Contour]] = {}
    for o in sel:
        by_layer.setdefault(o.layer_id, []).extend(o.contours)
    ctx.remove([o.id for o in sel])
    ids = []
    for lid, cs in by_layer.items():
        for c in vectorops.join(cs, v["tolerance"]):
            ids.append(ctx.add([c], lid, select=False).id)
    ctx.select(ids)
    ctx.message(f"{len(sel)} vectors joined into {len(ids)}")


def _map_contours(fn):
    def run(ctx, v):
        for o in ctx.selected:
            ctx.replace(o, [fn(c, v) for c in o.contours])
    return run


def _close(c, v):
    return vectorops.close(c, v["tolerance"])


def _ungroup(ctx, v):
    ids = []
    for o in ctx.selected:
        if len(o.contours) < 2:
            ids.append(o.id)
            continue
        for c in o.contours:
            ids.append(ctx.add([c], o.layer_id, o.name, select=False).id)
        ctx.remove([o.id])
    ctx.select(ids)


def _explode(ctx, v):
    ids = []
    for o in ctx.selected:
        for c in o.contours:
            for piece in vectorops.explode(c):
                ids.append(ctx.add([piece], o.layer_id, select=False).id)
        ctx.remove([o.id])
    ctx.select(ids)


def _group(ctx, v):
    sel = ctx.selected
    cs = [c for o in sel for c in o.contours]
    ctx.remove([o.id for o in sel])
    ctx.select([ctx.add(cs, sel[0].layer_id, v["name"] or "Group").id])


def _dedupe(ctx, v):
    tol = v["tolerance"]
    seen: list[tuple[VectorObject, list]] = []
    dupes = []
    for o in ctx.sheet.vectors if v["scope"] == "sheet" else ctx.selected:
        sig = sorted((round(c.length() / tol), round(c.bbox().xmin / tol), round(c.bbox().ymin / tol),
                      round(c.bbox().xmax / tol), round(c.bbox().ymax / tol)) for c in o.contours)
        if any(sig == s for _, s in seen):
            dupes.append(o.id)
        else:
            seen.append((o, sig))
    ctx.remove(dupes)
    ctx.message(f"{len(dupes)} duplicate vector(s) deleted")


def _doctor(ctx, v):
    """Find (and optionally fix) open, nearly closed, tiny, duplicate and self-crossing vectors."""
    from ..core.intersect import nearly_closed, self_intersects
    gap, small = v["gap"], v["min_size"]
    objs = ctx.selected or list(ctx.sheet.vectors)
    found = {"open": [], "gaps": [], "tiny": [], "crossing": [], "duplicate": []}
    seen = set()
    for o in objs:
        sig = tuple(sorted((round(c.length(), 3), round(c.bbox().xmin, 3), round(c.bbox().ymin, 3))
                           for c in o.contours))
        if sig in seen:
            found["duplicate"].append(o)
            continue
        seen.add(sig)
        bb = o.bbox()
        if max(bb.width, bb.height) < small:
            found["tiny"].append(o)
            continue
        for c in o.contours:
            if nearly_closed(c, gap):
                found["gaps"].append(o)
                break
            if not c.closed:
                found["open"].append(o)
                break
            if self_intersects(c):
                found["crossing"].append(o)
                break
    if v["fix"]:
        for o in found["gaps"]:
            ctx.replace(o, [vectorops.close(c, gap) if nearly_closed(c, gap) else c for c in o.contours])
        ctx.remove([o.id for o in found["tiny"] + found["duplicate"]])
        problems = found["open"] + found["crossing"]
        fixed = f" Fixed: closed {len(found['gaps'])}, deleted {len(found['tiny']) + len(found['duplicate'])}."
    else:
        problems = [o for k in found for o in found[k]]
        fixed = ""
    ctx.select([o.id for o in problems])
    parts = [f"{len(found[k])} {k}" for k in ("open", "gaps", "tiny", "crossing", "duplicate") if found[k]]
    ctx.message(("Vector check: " + ", ".join(parts) + "." if parts else "Vector check: no problems found.") + fixed)


# ---------------------------------------------------------------------------
# transforms / arrangement
# ---------------------------------------------------------------------------

def _apply(ctx, T: Transform, copy: bool = False):
    ids = []
    for o in ctx.selected:
        if copy:
            n = o.copy()
            n.transform(T)
            ids.append(ctx.add_object(n, select=False).id)
        else:
            o.transform(T)
            ctx.changed = True
            ids.append(o.id)
    ctx.select(ids)


def _pivot(ctx, v) -> tuple[float, float]:
    if v.get("about") == "origin":
        return (0.0, 0.0)
    if v.get("about") == "point":
        return (v["px"], v["py"])
    return _sel_bbox(ctx).center


def _move(ctx, v):
    _apply(ctx, Transform.translate(v["dx"], v["dy"]), v["copy"])


def _move_to(ctx, v):
    bb = _sel_bbox(ctx)
    fx, fy = {"bottom_left": (0, 0), "center": (0.5, 0.5), "top_left": (0, 1), "top_right": (1, 1),
              "bottom_right": (1, 0)}[v["anchor"]]
    ax, ay = bb.xmin + bb.width * fx, bb.ymin + bb.height * fy
    _apply(ctx, Transform.translate(v["x"] - ax, v["y"] - ay))


def _rotate(ctx, v):
    _apply(ctx, Transform.rotate(math.radians(v["angle"]), _pivot(ctx, v)), v["copy"])


def _scale(ctx, v):
    sx = v["sx"] / 100.0
    sy = sx if v["uniform"] else v["sy"] / 100.0
    _apply(ctx, Transform.scale(sx, sy, _pivot(ctx, v)), v["copy"])


def _size(ctx, v):
    bb = _sel_bbox(ctx)
    if bb.width <= 0 and bb.height <= 0:
        raise CommandError("Selection has no size")
    if v["uniform"]:
        sx = sy = v["width"] / bb.width if bb.width > 0 else v["height"] / bb.height
    else:
        sx = v["width"] / bb.width if bb.width > 0 else 1.0
        sy = v["height"] / bb.height if bb.height > 0 else 1.0
    _apply(ctx, Transform.scale(sx, sy, bb.center))


def _mirror(ctx, v):
    _apply(ctx, Transform.mirror(v["axis"] == "horizontal", _pivot(ctx, v)), v["copy"])


def _align(ctx, v):
    sel = ctx.selected
    ref = BBox(0, 0, ctx.sheet.width, ctx.sheet.height) if v["relative"] == "sheet" else _sel_bbox(ctx)
    mode = v["mode"]
    for group in ([[o] for o in sel] if v["each"] else [sel]):
        bb = BBox()
        for o in group:
            bb = bb.union(o.bbox())
        dx = dy = 0.0
        if mode in ("left", "right", "hcenter", "center"):
            dx = {"left": ref.xmin - bb.xmin, "right": ref.xmax - bb.xmax}.get(mode, ref.center[0] - bb.center[0])
        if mode in ("bottom", "top", "vcenter", "center"):
            dy = {"bottom": ref.ymin - bb.ymin, "top": ref.ymax - bb.ymax}.get(mode, ref.center[1] - bb.center[1])
        for o in group:
            o.transform(Transform.translate(dx, dy))
    ctx.changed = True


def _distribute(ctx, v):
    sel = sorted(ctx.selected, key=lambda o: o.bbox().center[0 if v["axis"] == "x" else 1])
    if len(sel) < 3:
        return
    k = 0 if v["axis"] == "x" else 1
    a, b = sel[0].bbox().center[k], sel[-1].bbox().center[k]
    for i, o in enumerate(sel[1:-1], 1):
        target = a + (b - a) * i / (len(sel) - 1)
        d = target - o.bbox().center[k]
        o.transform(Transform.translate(d, 0) if k == 0 else Transform.translate(0, d))
    ctx.changed = True


def _array_rect(ctx, v):
    bb = _sel_bbox(ctx)
    px = v["dx"] + (bb.width if v["spacing"] == "gap" else 0.0)
    py = v["dy"] + (bb.height if v["spacing"] == "gap" else 0.0)
    ids = [o.id for o in ctx.selected]
    for r in range(v["rows"]):
        for c in range(v["cols"]):
            if r == 0 and c == 0:
                continue
            T = Transform.translate(c * px, r * py)
            for o in ctx.selected:
                n = o.copy()
                n.transform(T)
                ids.append(ctx.add_object(n, select=False).id)
    ctx.select(ids)


def _array_circ(ctx, v):
    centre = (v["cx"], v["cy"]) if v["about"] == "point" else (0.0, 0.0)
    if v["about"] == "sheet":
        centre = (ctx.sheet.width / 2, ctx.sheet.height / 2)
    total = v["angle"]
    n = v["count"]
    step = total / n if abs(total - 360.0) < 1e-9 else total / max(1, n - 1)
    ids = [o.id for o in ctx.selected]
    for i in range(1, n):
        ang = math.radians(step * i)
        for o in ctx.selected:
            nobj = o.copy()
            if v["rotate_copies"]:
                nobj.transform(Transform.rotate(ang, centre))
            else:
                c0 = o.bbox().center
                c1 = Transform.rotate(ang, centre).apply(c0)
                nobj.transform(Transform.translate(c1[0] - c0[0], c1[1] - c0[1]))
            ids.append(ctx.add_object(nobj, select=False).id)
    ctx.select(ids)


def _along_path(ctx, v):
    """Copy the selection along the open (or last selected) vector of the selection."""
    sel = ctx.selected
    path_obj = next((o for o in sel if not o.closed), sel[-1])
    items = [o for o in sel if o is not path_obj]
    if not items:
        raise CommandError("Select the vectors to copy and the path vector")
    path = path_obj.contours[0]
    L = path.length()
    n = v["count"]
    if path.closed:
        positions = [L * k / n for k in range(n)]
    else:
        positions = [L * k / max(1, n - 1) for k in range(n)] if n > 1 else [0.0]
    bb = BBox()
    for o in items:
        bb = bb.union(o.bbox())
    ref = bb.center
    ids = []
    for s in positions:
        p = path.point_at_length(s)
        T = Transform.translate(p[0] - ref[0], p[1] - ref[1])
        if v["rotate"]:
            tang = path.tangent_at_length(s)
            ang = math.atan2(tang[1], tang[0])
            T = Transform.rotate(ang, p) @ T
        for o in items:
            n_obj = o.copy()
            n_obj.transform(T)
            ids.append(ctx.add_object(n_obj, select=False).id)
    if v["delete_originals"]:
        ctx.remove([o.id for o in items])
    ctx.select(ids)


# ---------------------------------------------------------------------------
# selection helpers
# ---------------------------------------------------------------------------

def _select_open(ctx, v):
    ctx.select([o.id for o in ctx.sheet.vectors if not o.closed])


def _select_all(ctx, v):
    ctx.select([o.id for o in ctx.sheet.vectors])


def _select_similar(ctx, v):
    if not ctx.selected:
        raise CommandError("Select a vector first")
    ref = ctx.selected[0]
    tol = v["tolerance"]
    rl = sum(c.length() for c in ref.contours)
    rb = ref.bbox()
    ids = []
    for o in ctx.sheet.vectors:
        ob = o.bbox()
        if (abs(sum(c.length() for c in o.contours) - rl) <= tol and len(o.contours) == len(ref.contours)
                and abs(ob.width * ob.height - rb.width * rb.height) <= tol * (rb.width + rb.height + 1)):
            ids.append(o.id)
    ctx.select(ids)


def _invert(ctx, v):
    sel = set(ctx.selection_ids)
    ctx.select([o.id for o in ctx.sheet.vectors if o.id not in sel])


def commands() -> list[Command]:
    pos = _pos_params
    about = [Param("about", "About", "choice", "center",
                   choices=[("center", "Selection centre"), ("origin", "Origin (0,0)"), ("point", "Point")]),
             Param("px", "Point X", "length", 0.0, visible={"about": "point"}),
             Param("py", "Point Y", "length", 0.0, visible={"about": "point"})]
    copy = [Param("copy", "Make a copy", "bool", False)]
    corner_ch = [("round", "Round"), ("chamfer", "Chamfer"), ("inverted", "Inverted round")]
    return [
        # create
        Command("create.rectangle", "Rectangle...", _rect, CREATE, [
            Param("width", "Width", "length", 100.0, min=0.001), Param("height", "Height", "length", 60.0, min=0.001),
            Param("radius", "Corner radius", "length", 0.0, min=0.0),
            Param("corner", "Corner type", "choice", "round", choices=corner_ch),
            Param("angle", "Angle", "angle", 0.0)] + pos()),
        Command("create.circle", "Circle...", _circle, CREATE,
                [Param("diameter", "Diameter", "length", 50.0, min=0.001)] + pos()),
        Command("create.ellipse", "Ellipse...", _ellipse, CREATE, [
            Param("width", "Width", "length", 80.0, min=0.001), Param("height", "Height", "length", 40.0, min=0.001),
            Param("angle", "Angle", "angle", 0.0)] + pos()),
        Command("create.polygon", "Polygon...", _polygon, CREATE, [
            Param("sides", "Sides", "int", 6, min=3, max=500), Param("radius", "Radius", "length", 40.0, min=0.001),
            Param("inscribed", "Vertices on radius", "bool", True), Param("angle", "Angle", "angle", 0.0),
            Param("corner_radius", "Corner radius", "length", 0.0, min=0.0)] + pos()),
        Command("create.star", "Star...", _star, CREATE, [
            Param("points", "Points", "int", 5, min=3, max=200), Param("outer", "Outer radius", "length", 40.0),
            Param("inner", "Inner radius", "length", 18.0), Param("angle", "Angle", "angle", 0.0)] + pos()),
        Command("create.slot", "Slot...", _slot, CREATE, [
            Param("length", "Overall length", "length", 60.0, min=0.001), Param("width", "Width", "length", 10.0),
            Param("angle", "Angle", "angle", 0.0)] + pos()),
        Command("create.arc", "Arc...", _arc, CREATE, [
            Param("radius", "Radius", "length", 30.0, min=0.001), Param("start", "Start angle", "angle", 0.0),
            Param("end", "End angle", "angle", 90.0), Param("ccw", "Counter-clockwise", "bool", True)] + pos()),
        Command("create.spiral", "Spiral...", _spiral, CREATE, [
            Param("start_radius", "Start radius", "length", 2.0, min=0.0), Param("pitch", "Pitch", "length", 5.0),
            Param("turns", "Turns", "float", 5.0, min=0.1)] + pos()),
        Command("create.boundary", "Bounding Box", _bbox_rect, CREATE, [
            Param("offset", "Offset", "length", 0.0), Param("radius", "Corner radius", "length", 0.0, min=0.0)],
                min_selection=1),
        Command("create.door", "Door / Panel...", _door, CREATE, [
            Param("width", "Door width", "length", 400.0, min=10.0, group="Door"),
            Param("height", "Door height", "length", 700.0, min=10.0, group="Door"),
            Param("outer_radius", "Outer corner radius", "length", 0.0, min=0.0, group="Door"),
            Param("frame", "Frame width", "length", 70.0, min=1.0, group="Panels",
                  help="From the door edge to the panel line"),
            Param("panels", "Panels (stacked)", "int", 1, min=1, max=6, group="Panels"),
            Param("rail", "Rail between panels", "length", 70.0, min=1.0, group="Panels"),
            Param("style", "Panel shape", "choice", "square", choices=doors.PANEL_STYLES, group="Panels"),
            Param("radius", "Corner radius / chamfer", "length", 15.0, min=0.0, group="Panels",
                  visible={"style": ["round", "cove", "chamfer"]}),
            Param("arch", "Arch rise", "length", 60.0, min=0.0, group="Panels", visible={"style": "arch"}),
            Param("grooves", "Groove lines", "int", 0, min=0, max=10, group="Grooves"),
            Param("groove_spacing", "Groove spacing", "length", 8.0, min=0.1, group="Grooves"),
            Param("groove_side", "Grooves on", "choice", "panel",
                  choices=[("panel", "The panel (inside)"), ("frame", "The frame (outside)")], group="Grooves"),
            Param("pocket_tool", "Panel pocket tool (none = no toolpath)", "tool", None, group="Toolpaths"),
            Param("pocket_depth", "Panel depth", "length", 6.0, min=0.0, group="Toolpaths"),
            Param("edge_tool", "Panel edge tool (V-bit / profile bit)", "tool", None, group="Toolpaths"),
            Param("groove_tool", "Groove tool", "tool", None, group="Toolpaths"),
            Param("groove_depth", "Groove depth", "length", 2.0, min=0.0, group="Toolpaths"),
            Param("cut_tool", "Cut-out tool", "tool", None, group="Toolpaths"),
        ] + pos(), tooltip="Cabinet door with stacked panels, decorative grooves and optional toolpaths"),
        # edit
        Command("edit.offset", "Offset...", _offset, EDIT, [
            Param("distance", "Distance", "length", 3.0, min=0.0),
            Param("direction", "Direction", "choice", "outward", choices=[("outward", "Outward"), ("inward", "Inward")]),
            Param("corners", "Corners", "choice", "round", choices=[("round", "Round"), ("sharp", "Sharp")]),
            Param("keep", "Keep original", "bool", True),
            Param("layer", "Layer", "choice", "same", choices=[("same", "Same as original"), ("active", "Active layer")])],
                min_selection=1, shortcut="Ctrl+Shift+O"),
        Command("edit.weld", "Weld (Union)", _weld, EDIT, min_selection=2, shortcut="Ctrl+Shift+W"),
        Command("edit.subtract", "Subtract", _subtract, EDIT, [
            Param("base", "Keep", "choice", "first", choices=[("first", "First selected"), ("largest", "Largest")])],
                min_selection=2),
        Command("edit.intersect", "Intersect", _intersect, EDIT, min_selection=2),
        Command("edit.join", "Join Vectors...", _join, EDIT, [Param("tolerance", "Gap tolerance", "length", 0.1, min=0.0)],
                min_selection=1, shortcut="Ctrl+J"),
        Command("edit.close", "Close Vectors", _map_contours(_close), EDIT,
                [Param("tolerance", "Snap tolerance", "length", 0.1, min=0.0)], min_selection=1),
        Command("edit.reverse", "Reverse Direction", _map_contours(lambda c, v: c.reversed()), EDIT, min_selection=1),
        Command("edit.group", "Group", _group, EDIT, [Param("name", "Name", "str", "")], min_selection=2,
                shortcut="Ctrl+G"),
        Command("edit.ungroup", "Ungroup", _ungroup, EDIT, min_selection=1, shortcut="Ctrl+Shift+G"),
        Command("edit.explode", "Explode into Segments", _explode, EDIT, min_selection=1),
        Command("edit.smooth", "Smooth / Fit Arcs...", _map_contours(lambda c, v: vectorops.smooth(c, v["tolerance"])),
                EDIT, [Param("tolerance", "Tolerance", "length", 0.02, min=0.001)], min_selection=1),
        Command("edit.simplify", "Simplify...", _map_contours(lambda c, v: vectorops.simplify(c, v["tolerance"])),
                EDIT, [Param("tolerance", "Tolerance", "length", 0.05, min=0.001)], min_selection=1),
        Command("edit.polyline", "Convert to Polyline...", _map_contours(lambda c, v: vectorops.to_polyline(c, v["tolerance"])),
                EDIT, [Param("tolerance", "Tolerance", "length", 0.01, min=0.001)], min_selection=1),
        Command("edit.doctor", "Vector Doctor...", _doctor, EDIT, [
            Param("gap", "Close gaps up to", "length", 0.1, min=0.0),
            Param("min_size", "Tiny if smaller than", "length", 0.05, min=0.0),
            Param("fix", "Fix automatically", "bool", True)],
                tooltip="Checks the selection (or the whole sheet) for open, nearly closed, tiny, duplicate and "
                        "self-crossing vectors, fixes what it can and selects the rest."),
        Command("edit.dedupe", "Delete Duplicates...", _dedupe, EDIT, [
            Param("tolerance", "Tolerance", "length", 0.01, min=0.0001),
            Param("scope", "Search in", "choice", "sheet", choices=[("sheet", "Whole sheet"), ("selection", "Selection")])]),
        # corners
        Command("corner.fillet", "Fillet Corners...", _map_contours(lambda c, v: vectorops.fillet(c, v["radius"])),
                CORNER, [Param("radius", "Radius", "length", 3.0, min=0.0)], min_selection=1),
        Command("corner.chamfer", "Chamfer Corners...", _map_contours(lambda c, v: vectorops.chamfer(c, v["distance"])),
                CORNER, [Param("distance", "Distance", "length", 3.0, min=0.0)], min_selection=1),
        Command("corner.dogbone", "Dog-bone / T-bone Corners...",
                _map_contours(lambda c, v: vectorops.dogbone(c, v["diameter"], v["cut"], v["style"], v["clearance"])),
                CORNER, [Param("diameter", "Tool diameter", "length", 6.0, min=0.01),
                         Param("cut", "Cut", "choice", "inside", choices=[("inside", "Inside (pockets, slots)"),
                                                                          ("outside", "Outside (parts, tenons)")]),
                         Param("style", "Style", "choice", "dogbone", choices=[("dogbone", "Dog-bone"), ("tbone", "T-bone")]),
                         Param("clearance", "Clearance", "length", 0.05, min=0.0)], min_selection=1),
        # transforms
        Command("xform.move", "Move...", _move, XFORM, [Param("dx", "Move X", "length", 0.0),
                                                       Param("dy", "Move Y", "length", 0.0)] + copy,
                min_selection=1, shortcut="Ctrl+M"),
        Command("xform.move_to", "Position...", _move_to, XFORM, [
            Param("anchor", "Anchor", "choice", "bottom_left",
                  choices=[("bottom_left", "Bottom left"), ("center", "Centre"), ("top_left", "Top left"),
                           ("top_right", "Top right"), ("bottom_right", "Bottom right")]),
            Param("x", "X", "length", 0.0), Param("y", "Y", "length", 0.0)], min_selection=1),
        Command("xform.rotate", "Rotate...", _rotate, XFORM, [Param("angle", "Angle", "angle", 90.0)] + about + copy,
                min_selection=1, shortcut="Ctrl+R"),
        Command("xform.scale", "Scale...", _scale, XFORM, [
            Param("sx", "Scale X %", "percent", 100.0, min=0.001), Param("sy", "Scale Y %", "percent", 100.0, min=0.001),
            Param("uniform", "Keep proportions", "bool", True)] + about + copy, min_selection=1),
        Command("xform.size", "Set Size...", _size, XFORM, [
            Param("width", "Width", "length", 100.0, min=0.0), Param("height", "Height", "length", 100.0, min=0.0),
            Param("uniform", "Keep proportions (use width)", "bool", True)], min_selection=1),
        Command("xform.mirror", "Mirror...", _mirror, XFORM, [
            Param("axis", "Mirror", "choice", "horizontal",
                  choices=[("horizontal", "Left / right"), ("vertical", "Top / bottom")])] + about + copy,
                min_selection=1),
        Command("arrange.align", "Align...", _align, ARRANGE, [
            Param("mode", "Align", "choice", "left", choices=[
                ("left", "Left"), ("hcenter", "Centre horizontally"), ("right", "Right"), ("bottom", "Bottom"),
                ("vcenter", "Centre vertically"), ("top", "Top"), ("center", "Centre")]),
            Param("relative", "Relative to", "choice", "selection",
                  choices=[("selection", "Selection"), ("sheet", "Sheet")]),
            Param("each", "Move each vector separately", "bool", True)], min_selection=1),
        Command("arrange.center", "Centre on Sheet", lambda ctx, v: _align(ctx, {"mode": "center", "relative": "sheet",
                                                                                 "each": False}),
                ARRANGE, min_selection=1, shortcut="F9"),
        Command("arrange.distribute", "Distribute...", _distribute, ARRANGE, [
            Param("axis", "Direction", "choice", "x", choices=[("x", "Horizontally"), ("y", "Vertically")])],
                min_selection=3),
        Command("arrange.array", "Rectangular Array...", _array_rect, ARRANGE, [
            Param("rows", "Rows", "int", 2, min=1, max=1000), Param("cols", "Columns", "int", 3, min=1, max=1000),
            Param("spacing", "Spacing is", "choice", "gap", choices=[("gap", "Gap between copies"), ("pitch", "Pitch")]),
            Param("dx", "X spacing", "length", 10.0), Param("dy", "Y spacing", "length", 10.0)], min_selection=1),
        Command("arrange.circular", "Circular Array...", _array_circ, ARRANGE, [
            Param("count", "Number of copies", "int", 6, min=2, max=1000),
            Param("angle", "Total angle", "angle", 360.0),
            Param("about", "Centre", "choice", "point", choices=[("point", "Point"), ("sheet", "Sheet centre"),
                                                                ("origin", "Origin")]),
            Param("cx", "Centre X", "length", 0.0, visible={"about": "point"}),
            Param("cy", "Centre Y", "length", 0.0, visible={"about": "point"}),
            Param("rotate_copies", "Rotate copies", "bool", True)], min_selection=1),
        Command("arrange.along", "Copy Along Vector...", _along_path, ARRANGE, [
            Param("count", "Number of copies", "int", 5, min=1, max=10000),
            Param("rotate", "Rotate copies to follow the vector", "bool", True),
            Param("delete_originals", "Delete the originals", "bool", False)], min_selection=2,
                tooltip="Select the vectors to copy and one open vector (or select the path last)."),
        # selection
        Command("select.all", "Select All", _select_all, SELECT, shortcut="Ctrl+A"),
        Command("select.invert", "Invert Selection", _invert, SELECT, shortcut="Ctrl+Shift+I"),
        Command("select.open", "Select Open Vectors", _select_open, SELECT),
        Command("select.similar", "Select Similar...", _select_similar, SELECT,
                [Param("tolerance", "Tolerance", "length", 0.1, min=0.0)], min_selection=1),
    ]

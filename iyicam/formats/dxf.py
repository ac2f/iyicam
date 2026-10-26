"""DXF import / export (ezdxf)."""
from __future__ import annotations

import math
from pathlib import Path

import ezdxf
from ezdxf import colors as dxf_colors
from ezdxf import path as ezpath
from ezdxf.math import cubic_bezier_from_ellipse

from ..core import vectorops
from ..core.document import Project, Sheet, VectorObject
from ..core.geom import Arc, Bezier, Contour, Line, Segment, make_arc, polar
from .report import ImportReport

# $INSUNITS -> millimetres
UNIT_SCALE = {0: 1.0, 1: 25.4, 2: 304.8, 4: 1.0, 5: 10.0, 6: 1000.0, 8: 25.4e-6, 9: 0.0254,
              10: 914.4, 13: 0.001, 14: 100.0}


UNIT_NAMES = {0: "unitless", 1: "inches", 2: "feet", 4: "millimetres", 5: "centimetres", 6: "metres",
              8: "microinches", 9: "mils", 10: "yards", 13: "microns", 14: "decimetres"}


def detect_units(path: str | Path) -> tuple[int, str]:
    """``($INSUNITS code, readable name)`` of a DXF file."""
    try:
        code = int(ezdxf.readfile(str(path)).header.get("$INSUNITS", 0))
    except Exception:
        return 0, "unknown"
    return code, UNIT_NAMES.get(code, f"code {code}")


def _scale(doc, override: float | None) -> float:
    if override:
        return override
    try:
        return UNIT_SCALE.get(int(doc.header.get("$INSUNITS", 4)), 1.0)
    except (TypeError, ValueError):
        return 1.0


def _poly_segments(pts: list[tuple[float, float, float]], closed: bool, k: float, w=None) -> list[Segment]:
    """``pts`` are (x, y, bulge) in OCS; bulge belongs to the segment leaving the vertex."""
    w = w or (lambda x, y: (x, y))
    flip = getattr(w, "flip", False)
    segs: list[Segment] = []
    n = len(pts)
    last = n if closed else n - 1
    for i in range(last):
        x0, y0, b = pts[i]
        x1, y1, _ = pts[(i + 1) % n]
        a, c = w(x0 * k, y0 * k), w(x1 * k, y1 * k)
        if math.hypot(c[0] - a[0], c[1] - a[1]) < 1e-9:
            continue
        segs.append(make_arc(a, c, -b if flip else b))
    return segs


def _flat_ocs(e) -> bool:
    """The entity lies in a plane parallel to the drawing (extrusion along +Z or -Z)."""
    try:
        ext = e.dxf.extrusion
    except Exception:
        return True
    return abs(ext.x) < 1e-9 and abs(ext.y) < 1e-9


def _ocs_mapper(e, k: float):
    """Map OCS (x, y) to WCS (x, y) for an extrusion along Z; a mirrored OCS (-Z) reverses arcs."""
    try:
        ext = e.dxf.extrusion
    except Exception:
        ext = None
    if ext is None or ext.z > 0:
        def w(x, y):
            return (x, y)
        w.flip = False
        return w

    ocs = e.ocs()

    def w(x, y):
        p = ocs.to_wcs((x, y, 0.0))  # OCS -> WCS is a rotation, so scaling commutes
        return (p.x, p.y)
    w.flip = True
    return w


def _quad_to_cubic(a, q, b) -> Bezier:
    return Bezier(a, (a[0] + 2 / 3 * (q[0] - a[0]), a[1] + 2 / 3 * (q[1] - a[1])),
                  (b[0] + 2 / 3 * (q[0] - b[0]), b[1] + 2 / 3 * (q[1] - b[1])), b)


def _via_path(e, k: float) -> list[Contour]:
    """Any entity ezdxf can turn into a path, seen from above (also planes tilted in space)."""
    contours: list[Contour] = []
    for sub in ezpath.make_path(e).sub_paths():
        segs: list[Segment] = []
        cur = (sub.start.x * k, sub.start.y * k)
        for cmd in sub.commands():
            end = (cmd.end.x * k, cmd.end.y * k)
            if cmd.type == ezpath.Command.LINE_TO:
                if math.dist(cur, end) > 1e-9:
                    segs.append(Line(cur, end))
            elif cmd.type == ezpath.Command.CURVE3_TO:
                segs.append(_quad_to_cubic(cur, (cmd.ctrl.x * k, cmd.ctrl.y * k), end))
            elif cmd.type == ezpath.Command.CURVE4_TO:
                segs.append(Bezier(cur, (cmd.ctrl1.x * k, cmd.ctrl1.y * k), (cmd.ctrl2.x * k, cmd.ctrl2.y * k), end))
            cur = end
        if not segs:
            continue
        closed = bool(sub.is_closed) or math.dist(segs[0].a, segs[-1].b) < 1e-6
        if closed and math.dist(segs[0].a, segs[-1].b) > 1e-9:
            segs.append(Line(segs[-1].b, segs[0].a))
        contours.append(Contour(segs, closed))
    return contours


def _spline_segments(e, k: float) -> list[Segment]:
    """A spline as curves: exact for cubic (and lower) splines, finely approximated otherwise.

    A spline whose knots are not clamped (the usual closed "periodic" splines) is defined only between
    the knots ``t[degree]`` and ``t[count]``; the rest of the parameter range is not part of the curve."""
    bs = e.construction_tool()
    segs: list[Segment] = []

    def pt(v):
        return (v.x * k, v.y * k)
    exact = not bs.is_rational and bs.degree <= 3
    if bs.is_clamped:
        if exact:
            for cp in bs.bezier_decomposition():
                p = [pt(v) for v in cp]
                if len(p) == 4:
                    segs.append(Bezier(*p))
                elif len(p) == 3:
                    segs.append(_quad_to_cubic(*p))
                elif len(p) == 2 and math.dist(p[0], p[1]) > 1e-9:
                    segs.append(Line(*p))
            return segs
        for bz in bs.cubic_bezier_approximation(level=6):
            segs.append(Bezier(*[pt(v) for v in bz.control_points]))
        return segs
    knots = list(bs.knots())
    lo, hi = knots[bs.degree], knots[bs.count]
    spans = sorted({t for t in knots if lo - 1e-12 <= t <= hi + 1e-12})
    for u0, u1 in zip(spans, spans[1:]):
        if u1 - u0 < 1e-12:
            continue
        if exact:  # one polynomial of degree <= 3 per span: a cubic Bezier through four samples is exact
            c = [pt(bs.point(u0 + (u1 - u0) * i / 3)) for i in range(4)]
            if u1 >= hi - 1e-12:
                c[3] = pt(bs.point(hi - (hi - u0) * 1e-9))  # the end of the domain, from the inside
            p1 = tuple((-5 * c[0][j] + 18 * c[1][j] - 9 * c[2][j] + 2 * c[3][j]) / 6 for j in (0, 1))
            p2 = tuple((2 * c[0][j] - 9 * c[1][j] + 18 * c[2][j] - 5 * c[3][j]) / 6 for j in (0, 1))
            segs.append(Bezier(c[0], p1, p2, c[3]))
        else:
            pts = [pt(bs.point(u0 + (u1 - u0) * i / 64)) for i in range(65)]
            segs.extend(Line(a, b) for a, b in zip(pts, pts[1:]) if math.dist(a, b) > 1e-9)
    for i in range(1, len(segs)):  # the pieces meet exactly
        if segs[i - 1].b != segs[i].a:
            segs[i] = segs[i].with_ends(segs[i - 1].b, segs[i].b)
    return segs


# kinds that are read, and kinds known to be left out (reported, never silently lost)
READ_KINDS = ("LINE", "ARC", "CIRCLE", "LWPOLYLINE", "POLYLINE", "SPLINE", "ELLIPSE", "SOLID", "TRACE")
CONTAINER_KINDS = ("INSERT", "VIEWPORT", "ATTDEF", "ATTRIB", "POINT")  # nothing to cut in these themselves


def entity_contours(e, k: float, tol: float = 0.01) -> list[Contour]:
    t = e.dxftype()
    if t in ("ARC", "CIRCLE", "LWPOLYLINE") or (t == "POLYLINE" and not (
            e.is_3d_polyline or e.is_poly_face_mesh or e.is_polygon_mesh)):
        if not _flat_ocs(e):
            return _via_path(e, k)
    if t in ("SOLID", "TRACE"):
        pts = [(v.x * k, v.y * k) for v in e.wcs_vertices()]
        dedup = [p for i, p in enumerate(pts) if i == 0 or math.dist(p, pts[i - 1]) > 1e-9]
        if len(dedup) > 1 and math.dist(dedup[0], dedup[-1]) < 1e-9:
            dedup.pop()
        if len(dedup) < 3:
            return [Contour.polyline(dedup, False)] if len(dedup) == 2 else []
        return [Contour.polyline(dedup, True)]
    if t == "LINE":
        a, b = e.dxf.start, e.dxf.end
        return [Contour([Line((a.x * k, a.y * k), (b.x * k, b.y * k))], False)]
    if t in ("ARC", "CIRCLE"):
        w = _ocs_mapper(e, k)
        c = (e.dxf.center.x * k, e.dxf.center.y * k)
        r = e.dxf.radius * k
        if t == "CIRCLE":
            a, b = w(*polar(c, r, 0.0)), w(*polar(c, r, math.pi))
            bl = -1.0 if w.flip else 1.0
            return [Contour([Arc(a, b, bl), Arc(b, a, bl)], True)]
        sa, ea = math.radians(e.dxf.start_angle), math.radians(e.dxf.end_angle)
        sweep = (ea - sa) % (2 * math.pi) or 2 * math.pi
        n = 1 if sweep <= math.pi else 2
        segs = []
        for i in range(n):
            a0 = sa + sweep * i / n
            p0, p1 = w(*polar(c, r, a0)), w(*polar(c, r, a0 + sweep / n))
            bl = math.tan(sweep / n / 4)
            segs.append(Arc(p0, p1, -bl if w.flip else bl))
        return [Contour(segs, False)]
    if t == "LWPOLYLINE":
        pts = [(p[0], p[1], p[2]) for p in e.get_points("xyb")]
        closed = bool(e.closed)
        if len(pts) < 2:
            return []
        if not closed and math.hypot(pts[0][0] - pts[-1][0], pts[0][1] - pts[-1][1]) < 1e-9 and len(pts) > 2:
            pts = pts[:-1]
            closed = True
        segs = _poly_segments(pts, closed, k, _ocs_mapper(e, k))
        return [Contour(segs, closed)] if segs else []
    if t == "POLYLINE":
        if e.is_3d_polyline or e.is_poly_face_mesh or e.is_polygon_mesh:
            pts = [(v.dxf.location.x, v.dxf.location.y, 0.0) for v in e.vertices]
            w = None
        else:
            pts = [(v.dxf.location.x, v.dxf.location.y, v.dxf.get("bulge", 0.0)) for v in e.vertices]
            w = _ocs_mapper(e, k)
        if len(pts) < 2:
            return []
        segs = _poly_segments(pts, bool(e.is_closed), k, w)
        return [Contour(segs, bool(e.is_closed))] if segs else []
    if t == "SPLINE":
        try:
            segs = _spline_segments(e, k)
        except Exception:
            pts = [(v.x * k, v.y * k) for v in e.flattening(tol)]
            return [Contour.polyline(pts, bool(e.closed))] if len(pts) > 1 else []
        if not segs:
            return []
        closed = bool(e.closed) or math.dist(segs[0].a, segs[-1].b) < 1e-6
        if closed and math.dist(segs[0].a, segs[-1].b) > 1e-9:
            segs.append(Line(segs[-1].b, segs[0].a))
        return [Contour(segs, closed)]
    if t == "ELLIPSE":
        segs = []
        for bz in cubic_bezier_from_ellipse(e.construction_tool()):
            p = bz.control_points
            segs.append(Bezier((p[0].x * k, p[0].y * k), (p[1].x * k, p[1].y * k),
                               (p[2].x * k, p[2].y * k), (p[3].x * k, p[3].y * k)))
        if not segs:
            return []
        closed = math.dist(segs[0].a, segs[-1].b) < 1e-6
        return [Contour(segs, closed)]
    return []


def _iter_entities(layout, report: ImportReport | None = None, depth: int = 0, inherit: str | None = None):
    """``(entity, layer name)`` of everything in ``layout``, blocks (INSERT) opened.

    Things in a block that are on layer "0" belong to the layer of the INSERT, as in AutoCAD."""
    for e in layout:
        try:
            kind = e.dxftype()
            layer = e.dxf.layer
        except Exception as exc:  # noqa: BLE001
            if report is not None:
                report.error(f"unreadable entity: {exc}")
            continue
        if inherit is not None and layer == "0":
            layer = inherit
        if kind == "INSERT":
            if depth >= 8:
                if report is not None:
                    report.error(f"block {e.dxf.get('name', '?')} is nested more than 8 levels deep")
                continue
            try:
                inner = list(e.virtual_entities())
            except Exception as exc:  # noqa: BLE001
                if report is not None:
                    report.error(f"block {e.dxf.get('name', '?')} could not be opened: {exc}")
                continue
            yield from _iter_entities(inner, report, depth + 1, layer)
        else:
            yield e, layer


def import_dxf(path: str | Path, project: Project, sheet: Sheet, layer_id: str | None = None,
               join_tolerance: float = 0.01, keep_layers: bool = True,
               unit_scale: float | None = None, report: ImportReport | None = None) -> list[VectorObject]:
    """Read a DXF into ``sheet``; returns the created vectors.

    ``report`` (optional) is filled with the kinds of entities that were left out and what failed."""
    doc = ezdxf.readfile(str(path))
    k = _scale(doc, unit_scale)
    by_layer: dict[str, list[Contour]] = {}
    for e, layer in _iter_entities(doc.modelspace(), report):
        kind = e.dxftype()
        try:
            cs = entity_contours(e, k)
        except Exception as exc:  # noqa: BLE001
            if report is not None:
                report.error(f"{kind} {e.dxf.get('handle', '')}: {type(exc).__name__}: {exc}")
            continue
        if cs:
            by_layer.setdefault(layer if keep_layers else "", []).extend(cs)
            if report is not None:
                report.read[kind] += 1
        elif report is not None and kind not in READ_KINDS:
            if kind not in CONTAINER_KINDS:
                report.skip(kind)
        elif report is not None:
            report.skip(f"empty {kind}")
    created = []
    for lname, cs in by_layer.items():
        if keep_layers and lname:
            layer = project.layer_by_name(lname)
            if layer is None:
                color = None
                try:
                    aci = doc.layers.get(lname).color
                    r, g, b = dxf_colors.aci2rgb(abs(aci)) if 0 < abs(aci) < 256 and aci != 7 else (40, 40, 40)
                    color = f"#{r:02x}{g:02x}{b:02x}"
                except Exception:
                    pass
                layer = project.add_layer(lname, color)
            lid = layer.id
        else:
            lid = layer_id or project.layers[0].id
        joined = vectorops.join(cs, join_tolerance) if join_tolerance > 0 else cs
        for c in joined:
            obj = VectorObject([c], lid)
            sheet.vectors.append(obj)
            created.append(obj)
    return created


def _contour_points(c: Contour, tol: float = 0.01) -> list[tuple[float, float, float]]:
    """LWPOLYLINE points (x, y, bulge); beziers are flattened."""
    pts: list[tuple[float, float, float]] = []
    for s in c.segs:
        if isinstance(s, Arc):
            pts.append((s.a[0], s.a[1], s.bulge))
        elif isinstance(s, Bezier):
            fl = [s.a] + s.flatten(tol)[:-1]
            pts.extend((p[0], p[1], 0.0) for p in fl)
        else:
            pts.append((s.a[0], s.a[1], 0.0))
    if not c.closed and c.segs:
        pts.append((c.end[0], c.end[1], 0.0))
    return pts


def export_dxf(path: str | Path, project: Project, sheet: Sheet,
               objects: list[VectorObject] | None = None) -> None:
    doc = ezdxf.new("R2010", setup=True)
    doc.header["$INSUNITS"] = 4
    msp = doc.modelspace()
    for obj in objects if objects is not None else sheet.vectors:
        layer = project.layer(obj.layer_id)
        lname = layer.name if layer else "0"
        if lname not in doc.layers:
            doc.layers.add(lname)
        for c in obj.contours:
            if not c.segs:
                continue
            pts = _contour_points(c)
            msp.add_lwpolyline([(x, y, 0, 0, b) for x, y, b in pts], format="xyseb",
                               close=c.closed, dxfattribs={"layer": lname})
    doc.saveas(str(path))

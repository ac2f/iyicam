"""DXF import / export (ezdxf)."""
from __future__ import annotations

import math
from pathlib import Path

import ezdxf
from ezdxf import colors as dxf_colors
from ezdxf.math import cubic_bezier_from_ellipse

from ..core import vectorops
from ..core.document import Project, Sheet, VectorObject
from ..core.geom import Arc, Bezier, Contour, Line, Segment, make_arc, polar

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


def _ocs_mapper(e, k: float):
    """Map OCS (x, y) to WCS (x, y); arcs drawn in a mirrored OCS reverse direction."""
    try:
        ext = e.dxf.extrusion
    except Exception:
        ext = None
    if ext is None or (abs(ext.x) < 1e-12 and abs(ext.y) < 1e-12 and ext.z > 0):
        def w(x, y):
            return (x, y)
        w.flip = False
        return w
    ocs = e.ocs()

    def w(x, y):
        p = ocs.to_wcs((x, y, 0.0))  # OCS -> WCS is a rotation, so scaling commutes
        return (p.x, p.y)
    w.flip = ext.z < 0
    return w


def entity_contours(e, k: float, tol: float = 0.01) -> list[Contour]:
    t = e.dxftype()
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
            bs = e.construction_tool()
            beziers = bs.cubic_bezier_approximation(level=4)
            segs = []
            for bz in beziers:
                p = bz.control_points
                segs.append(Bezier((p[0].x * k, p[0].y * k), (p[1].x * k, p[1].y * k),
                                   (p[2].x * k, p[2].y * k), (p[3].x * k, p[3].y * k)))
        except Exception:
            pts = [(v.x * k, v.y * k) for v in e.flattening(tol)]
            return [Contour.polyline(pts, bool(e.closed))] if len(pts) > 1 else []
        closed = bool(e.closed) or (segs and math.dist(segs[0].a, segs[-1].b) < 1e-6)
        return [Contour(segs, closed)] if segs else []
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


def _iter_entities(layout, depth: int = 0):
    for e in layout:
        if e.dxftype() == "INSERT" and depth < 8:
            try:
                for ve in e.virtual_entities():
                    if ve.dxftype() == "INSERT":
                        yield from _iter_entities([ve], depth + 1)
                    else:
                        yield ve
            except Exception:
                continue
        else:
            yield e


def import_dxf(path: str | Path, project: Project, sheet: Sheet, layer_id: str | None = None,
               join_tolerance: float = 0.01, keep_layers: bool = True,
               unit_scale: float | None = None) -> list[VectorObject]:
    """Read a DXF into ``sheet``; returns the created vectors."""
    doc = ezdxf.readfile(str(path))
    k = _scale(doc, unit_scale)
    by_layer: dict[str, list[Contour]] = {}
    for e in _iter_entities(doc.modelspace()):
        try:
            cs = entity_contours(e, k)
        except Exception:
            continue
        if cs:
            by_layer.setdefault(e.dxf.layer if keep_layers else "", []).extend(cs)
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

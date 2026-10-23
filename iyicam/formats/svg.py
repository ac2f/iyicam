"""SVG import (svgelements) and export."""
from __future__ import annotations

import math
from pathlib import Path
from xml.sax.saxutils import escape

from svgelements import SVG, Arc as SArc, Close, CubicBezier, Group, Line as SLine, Move, Path as SPath
from svgelements import QuadraticBezier, Shape

from ..core.document import Project, Sheet, VectorObject
from ..core.geom import Arc, Bezier, Contour, Line, Segment, dist

PX = 25.4 / 96.0  # svgelements works in 96 dpi pixels


def _layer_name(e) -> str | None:
    """Inkscape layer label (or id) of the closest parent group."""
    node = getattr(e, "parent", None)
    while node is not None:
        if isinstance(node, Group):
            vals = getattr(node, "values", {}) or {}
            label = vals.get("{http://www.inkscape.org/namespaces/inkscape}label") or vals.get("inkscape:label")
            if label:
                return str(label)
        node = getattr(node, "parent", None)
    return None


def import_svg(path: str | Path, project: Project, sheet: Sheet, layer_id: str | None = None,
               keep_layers: bool = True, scale: float = 1.0) -> list[VectorObject]:
    svg = SVG.parse(str(path), ppi=96)
    k = PX * scale
    H = (svg.height or 0) * k

    def m(p) -> tuple[float, float]:
        return (float(p.x) * k, H - float(p.y) * k)

    created = []
    for e in svg.elements():
        if not isinstance(e, Shape) or isinstance(e, Group):
            continue
        try:
            sp = SPath(e)
            sp.reify()
        except Exception:
            continue
        contours: list[Contour] = []
        segs: list[Segment] = []

        def flush(closed: bool):
            nonlocal segs
            if segs:
                if closed and dist(segs[-1].b, segs[0].a) > 1e-9:
                    segs.append(Line(segs[-1].b, segs[0].a))
                elif closed:
                    segs[-1] = segs[-1].with_ends(segs[-1].a, segs[0].a)
                contours.append(Contour(segs, closed))
            segs = []

        for seg in sp:
            if isinstance(seg, Move):
                flush(False)
                continue
            if isinstance(seg, Close):
                flush(True)
                continue
            a = m(seg.start)
            b = m(seg.end)
            if isinstance(seg, SLine):
                if dist(a, b) > 1e-9:
                    segs.append(Line(a, b))
            elif isinstance(seg, CubicBezier):
                segs.append(Bezier(a, m(seg.control1), m(seg.control2), b))
            elif isinstance(seg, QuadraticBezier):
                q = m(seg.control)
                segs.append(Bezier(a, (a[0] + 2 / 3 * (q[0] - a[0]), a[1] + 2 / 3 * (q[1] - a[1])),
                                   (b[0] + 2 / 3 * (q[0] - b[0]), b[1] + 2 / 3 * (q[1] - b[1])), b))
            elif isinstance(seg, SArc):
                if abs(seg.rx - seg.ry) <= 1e-6 * max(1.0, abs(seg.rx)):
                    if abs(seg.sweep) >= 2 * math.pi - 1e-9:
                        for sub in seg.as_cubic_curves():
                            segs.append(Bezier(m(sub.start), m(sub.control1), m(sub.control2), m(sub.end)))
                    elif dist(a, b) > 1e-9:
                        segs.append(Arc.through(a, m(seg.point(0.5)), b))
                else:
                    for sub in seg.as_cubic_curves():
                        segs.append(Bezier(m(sub.start), m(sub.control1), m(sub.control2), m(sub.end)))
            # keep the chain exactly connected
            if len(segs) >= 2 and segs[-2].b != segs[-1].a:
                segs[-1] = segs[-1].with_ends(segs[-2].b, segs[-1].b)
        flush(False)
        contours = [c for c in contours if c.segs]
        if not contours:
            continue
        lid = layer_id or project.layers[0].id
        if keep_layers:
            ln = _layer_name(e)
            if ln:
                lid = project.ensure_layer(ln).id
        name = str(getattr(e, "id", "") or "")
        obj = VectorObject(contours, lid, name if name != "None" else "")
        sheet.vectors.append(obj)
        created.append(obj)
    return created


def contour_path_data(c: Contour, height: float) -> str:
    def p(pt):
        return f"{pt[0]:.4f},{height - pt[1]:.4f}"
    if not c.segs:
        return ""
    out = [f"M{p(c.start)}"]
    for s in c.segs:
        if isinstance(s, Line):
            out.append(f"L{p(s.b)}")
        elif isinstance(s, Arc):
            r = s.radius
            large = 1 if abs(s.sweep) > math.pi else 0
            sweep = 0 if s.ccw else 1  # y is flipped in SVG
            out.append(f"A{r:.4f},{r:.4f} 0 {large},{sweep} {p(s.b)}")
        elif isinstance(s, Bezier):
            out.append(f"C{p(s.c1)} {p(s.c2)} {p(s.b)}")
    if c.closed:
        out.append("Z")
    return " ".join(out)


def export_svg(path: str | Path, project: Project, sheet: Sheet,
               objects: list[VectorObject] | None = None) -> None:
    W, H = sheet.width, sheet.height
    parts = [f'<svg xmlns="http://www.w3.org/2000/svg" '
             f'xmlns:inkscape="http://www.inkscape.org/namespaces/inkscape" '
             f'width="{W:g}mm" height="{H:g}mm" viewBox="0 0 {W:g} {H:g}">']
    objs = objects if objects is not None else sheet.vectors
    for layer in project.layers:
        mine = [o for o in objs if o.layer_id == layer.id]
        if not mine:
            continue
        parts.append(f'<g inkscape:groupmode="layer" inkscape:label="{escape(layer.name)}">')
        for o in mine:
            d = " ".join(contour_path_data(c, H) for c in o.contours)
            parts.append(f'<path d="{d}" fill="none" stroke="{layer.color}" stroke-width="0.2"/>')
        parts.append("</g>")
    parts.append("</svg>")
    Path(path).write_text("\n".join(parts), encoding="utf-8")

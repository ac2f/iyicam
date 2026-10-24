"""HPGL (plotter / cutter .plt files) import."""
from __future__ import annotations

import math
import re
from pathlib import Path

from ..core import vectorops
from ..core.document import Project, Sheet, VectorObject
from ..core.geom import Arc, Contour, Line, Segment, polar

PLU = 0.025  # millimetres per plotter unit
_CMD = re.compile(r"([A-Z]{2})([^A-Z;]*)")


def parse_hpgl(text: str, scale: float = PLU) -> list[Contour]:
    """Pen-down strokes of an HPGL program as open contours (millimetres)."""
    pos = (0.0, 0.0)
    pen_down = False
    absolute = True
    strokes: list[list[Segment]] = []
    cur: list[Segment] = []

    def flush():
        nonlocal cur
        if cur:
            strokes.append(cur)
        cur = []

    def nums(arg: str) -> list[float]:
        return [float(x) for x in re.split(r"[,\s]+", arg.strip()) if x]

    for cmd, arg in _CMD.findall(text.upper()):
        vals = nums(arg) if arg.strip() else []
        if cmd in ("PU", "PD", "PA", "PR"):
            if cmd == "PA":
                absolute = True
            elif cmd == "PR":
                absolute = False
            if cmd == "PU":
                pen_down = False
                flush()
            elif cmd == "PD":
                pen_down = True
            for i in range(0, len(vals) - 1, 2):
                x, y = vals[i] * scale, vals[i + 1] * scale
                nxt = (x, y) if absolute else (pos[0] + x, pos[1] + y)
                if pen_down and math.dist(pos, nxt) > 1e-9:
                    cur.append(Line(pos, nxt))
                elif not pen_down:
                    flush()
                pos = nxt
        elif cmd in ("AA", "AR") and len(vals) >= 3:
            cx, cy = vals[0] * scale, vals[1] * scale
            centre = (cx, cy) if cmd == "AA" else (pos[0] + cx, pos[1] + cy)
            sweep = math.radians(vals[2])
            r = math.dist(pos, centre)
            if r > 1e-9 and abs(sweep) > 1e-9:
                a0 = math.atan2(pos[1] - centre[1], pos[0] - centre[0])
                n = max(1, math.ceil(abs(sweep) / math.pi))
                for k in range(n):
                    seg = Arc.from_center(centre, r, a0 + sweep * k / n, sweep / n)
                    seg = Arc(pos if k == 0 else seg.a, seg.b, seg.bulge)
                    if pen_down:
                        cur.append(seg)
                    pos = seg.b
        elif cmd == "CI" and vals:
            r = vals[0] * scale
            a, b = polar(pos, r, 0.0), polar(pos, r, math.pi)
            strokes.append([Arc(a, b, 1.0), Arc(b, a, 1.0)])
        elif cmd in ("IN", "DF"):
            flush()
            pen_down, absolute = False, True
    flush()
    return [Contour(s, len(s) > 1 and math.dist(s[0].a, s[-1].b) < 1e-9) for s in strokes if s]


def import_hpgl(path: str | Path, project: Project, sheet: Sheet, layer_id: str | None = None,
                join_tolerance: float = 0.05, scale: float = 1.0, **_) -> list[VectorObject]:
    text = Path(path).read_text(encoding="latin-1", errors="replace")
    contours = parse_hpgl(text, PLU * scale)
    contours = vectorops.join(contours, join_tolerance) if join_tolerance > 0 else contours
    lid = layer_id or project.layers[0].id
    objs = [VectorObject([c], lid) for c in contours]
    sheet.vectors.extend(objs)
    return objs

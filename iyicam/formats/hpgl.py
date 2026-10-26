"""HPGL (plotter / cutter .plt files) import."""
from __future__ import annotations

import math
import re
from pathlib import Path

from ..core import vectorops
from ..core.document import Project, Sheet, VectorObject
from ..core.geom import Arc, Contour, Line, Segment, polar
from .report import ImportReport

PLU = 0.025  # millimetres per plotter unit
_CMD = re.compile(r"([A-Z]{2})([^A-Z;]*)")
_NUM = re.compile(r"[-+]?(?:\d+\.?\d*|\.\d+)")
_LABEL = re.compile(r"LB[^\x03]*\x03", re.IGNORECASE)   # text to print: letters, not commands
# drawing commands that are not read, and commands that change the meaning of the numbers
UNSUPPORTED = {"PE": "polyline encoded (PE)", "EW": "edge wedge (EW)", "WG": "filled wedge (WG)",
               "RA": "filled rectangle (RA)", "RR": "filled rectangle (RR)", "FT": "fill type (FT)",
               "BZ": "Bezier (BZ)", "BR": "Bezier (BR)"}
SCALING = ("SC", "IP", "IW")


def parse_hpgl(text: str, scale: float = PLU, report: ImportReport | None = None) -> list[Contour]:
    """Pen-down strokes of an HPGL program as open contours (millimetres)."""
    if report is not None and _LABEL.search(text):
        report.skip("text (LB)", len(_LABEL.findall(text)))
    text = _LABEL.sub("", text)
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
        return [float(x) for x in _NUM.findall(arg)]

    seen_scaling = False
    for cmd, arg in _CMD.findall(text.upper()):
        vals = nums(arg) if arg.strip() else []
        if cmd in UNSUPPORTED and report is not None:
            report.skip(UNSUPPORTED[cmd])
        if cmd in SCALING and not seen_scaling and report is not None:
            seen_scaling = True
            report.notes.append(f"{cmd} (plotter scaling) is ignored: coordinates are read as 0.025 mm units.")
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
        elif cmd in ("AT", "RT") and len(vals) >= 4:   # arc through the pen position, a second and an end point
            p2 = (vals[0] * scale, vals[1] * scale)
            p3 = (vals[2] * scale, vals[3] * scale)
            if cmd == "RT":
                p2, p3 = (pos[0] + p2[0], pos[1] + p2[1]), (pos[0] + p3[0], pos[1] + p3[1])
            if math.dist(pos, p3) > 1e-9 and abs((p2[0] - pos[0]) * (p3[1] - pos[1])
                                                 - (p2[1] - pos[1]) * (p3[0] - pos[0])) > 1e-12:
                seg = Arc.through(pos, p2, p3)
                if pen_down:
                    cur.append(seg)
            elif pen_down and math.dist(pos, p3) > 1e-9:
                cur.append(Line(pos, p3))   # three points on a line: a straight stroke
            pos = p3
        elif cmd in ("EA", "ER") and len(vals) >= 2:   # a rectangle with the pen position as one corner
            x, y = vals[0] * scale, vals[1] * scale
            q = (x, y) if cmd == "EA" else (pos[0] + x, pos[1] + y)
            if abs(q[0] - pos[0]) > 1e-9 and abs(q[1] - pos[1]) > 1e-9:
                corners = [pos, (q[0], pos[1]), q, (pos[0], q[1]), pos]
                strokes.append([Line(a, b) for a, b in zip(corners, corners[1:])])
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
                join_tolerance: float = 0.05, scale: float = 1.0, report: ImportReport | None = None,
                **_) -> list[VectorObject]:
    text = Path(path).read_text(encoding="latin-1", errors="replace")
    contours = parse_hpgl(text, PLU * scale, report)
    contours = vectorops.join(contours, join_tolerance) if join_tolerance > 0 else contours
    lid = layer_id or project.layers[0].id
    objs = [VectorObject([c], lid) for c in contours]
    sheet.vectors.extend(objs)
    if report is not None:
        report.read["stroke"] += len(objs)
    return objs

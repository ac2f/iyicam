"""An independent check of a nesting result: parts do not overlap, keep their distances and stay on the plate.

It uses only the shapes and the placements (plain geometry in millimetres), nothing of the nester.
"""
from __future__ import annotations

import math

import pyclipper

S = 1000.0


def _move(polys, pl):
    ca, sa = math.cos(math.radians(pl.angle)), math.sin(math.radians(pl.angle))
    return [[(x * ca - y * sa + pl.x, x * sa + y * ca + pl.y) for x, y in poly] for poly in polys]


def placed(shape, pl):
    """The part's outline polygons (millimetres) where the placement puts them."""
    return _move(shape.polys, pl)


def placed_holes(shape, pl):
    return _move(getattr(shape, "holes", []) or [], pl)


def _int(poly):
    return [(round(x * S), round(y * S)) for x, y in poly]


def _grow(polys, d):
    pco = pyclipper.PyclipperOffset(2.0, 0.25 * S)
    for p in polys:
        ip = _int(p)
        if pyclipper.Orientation(ip) is False:
            ip.reverse()
        pco.AddPath(ip, pyclipper.JT_ROUND, pyclipper.ET_CLOSEDPOLYGON)
    return pco.Execute(round(d * S))


def _solid(outline, holes, d):
    """The part grown by ``d``: its outline grown, its holes shrunk."""
    grown = _grow(outline, d)
    if not holes:
        return grown
    pc = pyclipper.Pyclipper()
    pc.AddPaths(grown, pyclipper.PT_SUBJECT, True)
    pc.AddPaths(_grow(holes, -d), pyclipper.PT_CLIP, True)
    return pc.Execute(pyclipper.CT_DIFFERENCE, pyclipper.PFT_NONZERO, pyclipper.PFT_NONZERO)


def _area(paths):
    return sum(abs(pyclipper.Area(p)) for p in paths) / S / S


def _bbox(polys):
    xs = [x for p in polys for x, _ in p]
    ys = [y for p in polys for _, y in p]
    return min(xs), min(ys), max(xs), max(ys)


def verify(shapes, res, st, plate_of=None, slack: float = 0.06) -> list[str]:
    """Problems of the layout ``res`` (an empty list: none).

    ``plate_of(sheet)`` gives the ``(x0, y0, x1, y1)`` of a plate; by default every plate is the
    rectangle of the settings. ``slack`` is the tolerance in millimetres."""
    problems = []
    by_sheet: dict[int, list] = {}
    seen = set()
    for pl in res.placements:
        if (pl.shape, pl.copy) in seen:
            problems.append(f"part {pl.shape} copy {pl.copy} is placed twice")
        seen.add((pl.shape, pl.copy))
        by_sheet.setdefault(pl.sheet, []).append((pl, placed(shapes[pl.shape], pl),
                                                  placed_holes(shapes[pl.shape], pl)))
    for i, sh in enumerate(shapes):
        for c in range(sh.quantity):
            if (i, c) not in seen and (i, c) not in set(res.unplaced):
                problems.append(f"part {i} copy {c} is neither placed nor reported unplaced")
    if len(seen) != len(res.placements):
        problems.append("placements are not unique")
    margin, half = st.margin, st.spacing / 2.0
    for k, items in by_sheet.items():
        x0, y0, x1, y1 = plate_of(k) if plate_of else (0.0, 0.0, st.sheet_width, st.sheet_height)
        grown = []
        for pl, polys, holes in items:
            bx0, by0, bx1, by1 = _bbox(polys)
            if bx0 < x0 + margin - slack or by0 < y0 + margin - slack or bx1 > x1 - margin + slack \
                    or by1 > y1 - margin + slack:
                problems.append(f"sheet {k}: part {pl.shape} copy {pl.copy} is outside the border "
                                f"({bx0:.2f}, {by0:.2f}, {bx1:.2f}, {by1:.2f})")
            grown.append(_solid(polys, holes, half - slack))
        for i in range(len(items)):
            bi = _bbox(items[i][1])
            for j in range(i + 1, len(items)):
                bj = _bbox(items[j][1])
                if bi[0] - st.spacing > bj[2] or bj[0] - st.spacing > bi[2] or bi[1] - st.spacing > bj[3] \
                        or bj[1] - st.spacing > bi[3]:
                    continue
                pc = pyclipper.Pyclipper()
                pc.AddPaths(grown[i], pyclipper.PT_SUBJECT, True)
                pc.AddPaths(grown[j], pyclipper.PT_CLIP, True)
                common = _area(pc.Execute(pyclipper.CT_INTERSECTION, pyclipper.PFT_NONZERO, pyclipper.PFT_NONZERO))
                if common > 0.05:
                    problems.append(f"sheet {k}: parts {items[i][0].shape}.{items[i][0].copy} and "
                                    f"{items[j][0].shape}.{items[j][0].copy} are closer than the spacing "
                                    f"({common:.2f} mm2 in common)")
    return problems

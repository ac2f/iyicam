"""Checks of a cut order that look at the layout only (the boxes of the parts), the way a person looks at a plate.

A cut order of columns (bottom -> top, then the next column to the right) must keep two things:

* the support: a part that is cut earlier must not stand on the side where the sheet holds a later one (up or right of
  it, within ``HOLD_REACH`` mm and with parts that really face each other);
* the columns: a part that stands wholly to the right of another one, in the same rows, is not cut before it (a column
  is finished before the next is begun), unless a support rule above makes it so.

:func:`violations` lists what breaks them; the tests run it over many nested layouts, and a calculation can report
it, so a plate that breaks a rule is never found by hand."""
from __future__ import annotations

from ..core.geom import BBox

HOLD_REACH = 30.0       # mm: a part this near, up or right of a later one, took its hold away
FACING = 4.0            # mm: how much the two must face each other (overlap across the gap)
STEP = 1.0              # mm of tolerance on edges


def hold_pairs(boxes: dict[str, BBox], reach: float = HOLD_REACH) -> set[tuple[str, str]]:
    """``(a, b)``: ``a`` is on the hold side (right or up) of ``b`` within ``reach``; ``a`` must then be cut after ``b``."""
    out = set()
    items = list(boxes.items())
    for ka, A in items:
        for kb, B in items:
            if ka == kb:
                continue
            oy = min(A.ymax, B.ymax) - max(A.ymin, B.ymin)
            ox = min(A.xmax, B.xmax) - max(A.xmin, B.xmin)
            gx, gy = A.xmin - B.xmax, A.ymin - B.ymax
            if (-STEP <= gx <= reach and oy > FACING) or (-STEP <= gy <= reach and ox > FACING):
                out.add((ka, kb))
    return out


def outlines(order: list[str], contour_boxes: dict[str, BBox]) -> tuple[list[str], dict[str, BBox]]:
    """The order of the parts: of every vector (key ``id:contour``) its outline, the contour with the biggest box, at
    the place it is cut (the holes of a part are cut inside it: they say nothing about the part next to it)."""
    big: dict[str, str] = {}
    for k in order:
        vid = k.rsplit(":", 1)[0]
        b = contour_boxes[k]
        if vid not in big or b.width * b.height > contour_boxes[big[vid]].width * contour_boxes[big[vid]].height:
            big[vid] = k
    keep = set(big.values())
    return [k for k in order if k in keep], {k: contour_boxes[k] for k in keep}


def violations(order: list[str], boxes: dict[str, BBox], columns: bool = True) -> list[tuple[str, str, str]]:
    """``(earlier, later, rule)`` for every pair of the order that breaks the support or (``columns``) the column rule."""
    pos = {k: i for i, k in enumerate(order) if k in boxes}
    holds = hold_pairs({k: boxes[k] for k in pos})
    bad = []
    for a, b in sorted(holds):
        if pos[a] < pos[b]:
            bad.append((a, b, "support"))
    if columns:
        # an earlier part wholly to the right of a later one, when the later one is not held up by a support rule:
        # cutting the later one first would have been the column order, unless a chain of support pairs forbids it
        before: dict[str, set[str]] = {}
        for a, b in holds:                       # a after b
            before.setdefault(a, set()).add(b)

        def must_follow(x: str, y: str, seen=None) -> bool:
            """x has to be cut after y by a chain of support pairs."""
            seen = seen or set()
            for z in before.get(x, ()):
                if z == y or (z not in seen and must_follow(z, y, seen | {z})):
                    return True
            return False
        keys = sorted(pos, key=pos.get)
        for i, a in enumerate(keys):
            A = boxes[a]
            for b in keys[i + 1:]:
                B = boxes[b]
                if (A.xmin >= B.xmax + STEP and min(A.ymax, B.ymax) - max(A.ymin, B.ymin) > FACING
                        and not must_follow(a, b) and not must_follow(b, a)):
                    bad.append((a, b, "column"))
    return bad

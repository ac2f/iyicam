"""What cannot be cut: vectors lying on one another, and parts closer together than the tool needs.

A toolpath on closed vectors (a pocket, a V-carving, a profile inside / outside) is not made when

* **two closed vectors overlap**: their outlines cross, or one lies exactly on the other (a copy on top). What is inside
  is then not known (the cut would go round one through the other). A vector wholly inside another is a hole or a part
  in a hole: that is fine;
* (a profile outside) **two parts are closer together than the tool needs**: the cut of one part is a band as wide as
  the tool beside it (beyond the allowance), and it went into the part next to it. Two parts need a gap of the tool
  diameter plus twice the allowance; so does a part lying in a hole of another, from the edge of that hole;
* (a profile) **a hole (cut outside) or a shape (cut inside) is narrower than the tool**: the tool does not fit in.

Plain functions on contours (no window): the engine refuses the toolpath with what is wrong, the desktop says it in a
window before the toolpath is made.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from ..core import clip
from ..core.geom import polygon_area

TOL = 0.01                # mm: outlines flattened this finely
AREA_EPS = 1e-4           # mm²: less than this in common is touching, not overlapping


@dataclass
class Problems:
    overlaps: list[tuple[str, str]] = field(default_factory=list)           # contour keys "vector:contour"
    tight: list[tuple[str, str, float]] = field(default_factory=list)       # two parts and the gap they need (mm)
    narrow: list[tuple[str, float]] = field(default_factory=list)           # a hole / shape and the width it needs

    def __bool__(self) -> bool:
        return bool(self.overlaps or self.tight or self.narrow)

    def vector_ids(self) -> list[str]:
        keys = [k for a, b in self.overlaps for k in (a, b)] + [k for a, b, _ in self.tight for k in (a, b)] + \
               [k for k, _ in self.narrow]
        return list(dict.fromkeys(k.split(":")[0] for k in keys))

    def text(self) -> str:
        out = []
        if self.overlaps:
            n = len({k.split(":")[0] for a, b in self.overlaps for k in (a, b)})
            out.append(f"{n} vectors lie on one another ({len(self.overlaps)} overlap(s): outlines that cross, or a "
                       "copy on top): what is inside them is not known")
        if self.tight:
            need = max(g for _, _, g in self.tight)
            out.append(f"{len(self.tight)} pair(s) of parts are closer together than the tool needs ({need:.3g} mm: "
                       "the tool diameter and twice the allowance): the cut of one would go into the other")
        if self.narrow:
            need = max(w for _, w in self.narrow)
            out.append(f"{len(self.narrow)} hole(s) / shape(s) are narrower than the tool ({need:.3g} mm): it does not "
                       "fit in")
        return "; ".join(out)


def _bbox(poly):
    xs = [p[0] for p in poly]
    ys = [p[1] for p in poly]
    return min(xs), min(ys), max(xs), max(ys)


def _meets(a, b, grow=0.0) -> bool:
    return not (a[2] + grow < b[0] or b[2] + grow < a[0] or a[3] + grow < b[1] or b[3] + grow < a[1])


def _rings(objects, tol=TOL):
    """Every closed contour as ``(key, polygon, area, bbox)``."""
    out = []
    for o in objects:
        for i, c in enumerate(o.contours):
            if not c.closed or not c.segs:
                continue
            pts = c.flatten(tol)
            if len(pts) > 1 and pts[0] == pts[-1]:
                pts = pts[:-1]
            if len(pts) < 3:
                continue
            signed = polygon_area(pts)
            if abs(signed) <= AREA_EPS:
                continue
            if signed < 0:                     # (every ring counter-clockwise: a solid; a hole is a reversed one)
                pts = pts[::-1]
            a = abs(signed)
            out.append((f"{o.id}:{i}", pts, a, _bbox(pts)))
    return out


def check(objects, *, tool_diameter: float = 0.0, allowance: float = 0.0, side: str | None = None) -> Problems:
    """What keeps a toolpath on these vectors from being cut. ``side``: a profile's side ("outside" / "inside"; None for
    a pocket and the like: only overlaps are looked for)."""
    rings = _rings(objects)
    pr = Problems()
    n = len(rings)
    order = sorted(range(n), key=lambda i: rings[i][3][0])
    inside_of: dict[int, list[int]] = {i: [] for i in range(n)}       # the rings each ring lies wholly in
    for ii, i in enumerate(order):                                    # (sweep along x: only rings whose boxes meet)
        ki, pi, ai, bi = rings[i]
        for j in order[ii + 1:]:
            kj, pj, aj, bj = rings[j]
            if bj[0] > bi[2]:
                break
            if not _meets(bi, bj):
                continue
            common = clip.area(clip.boolean([pi], [pj], "intersection"))
            if common <= AREA_EPS:
                continue
            small, big = (i, j) if ai <= aj else (j, i)
            # (the common part computed in Clipper's whole numbers differs from the area of the drawn points by a few
            # ten-thousandths of a mm²: a ten-thousandth of the area, at least a hundredth of a mm², is the same)
            same_area = lambda x, y: abs(x - y) <= max(0.01, 1e-4 * max(x, y))  # noqa: E731
            if same_area(common, min(ai, aj)) and not same_area(ai, aj):
                inside_of[small].append(big)                         # (wholly inside: a hole, or a part in a hole)
            else:
                pr.overlaps.append((ki, kj))                         # (crossing outlines, or one exactly on the other)
    if pr.overlaps or side not in ("outside", "inside") or tool_diameter <= 0:
        return pr
    depth = {i: len(inside_of[i]) for i in range(n)}
    parent = {i: min(inside_of[i], key=lambda k: rings[k][2]) if inside_of[i] else None for i in range(n)}
    base = tool_diameter / 2.0 + allowance                           # (where the centre of the tool goes)
    # parts: an outline (even depth) with the holes right in it
    parts = [i for i in range(n) if depth[i] % 2 == 0]
    holes = {i: [h for h in range(n) if parent[h] == i and depth[h] == depth[i] + 1] for i in parts}
    if base > 0:
        for i in parts:
            if side == "outside":
                for h in holes[i]:                                   # a hole the tool does not fit in
                    if not clip.offset([rings[h][1]], -base):
                        pr.narrow.append((rings[h][0], 2 * base))
            elif not clip.offset([rings[i][1]], -base):              # a shape cut inside that the tool does not fit in
                pr.narrow.append((rings[i][0], 2 * base))
    need = tool_diameter + 2.0 * allowance                           # the gap two parts cut outside need
    if side != "outside" or need <= 0:
        return pr
    half = need / 2.0 - 1e-4                                         # (exactly the gap needed is fine)
    region = {i: [rings[i][1]] + [list(reversed(rings[h][1])) for h in holes[i]] for i in parts}
    grown: dict[int, list] = {}

    def grow(i):
        if i not in grown:
            grown[i] = clip.offset(region[i], half)
        return grown[i]
    porder = sorted(parts, key=lambda i: rings[i][3][0])
    for ii, i in enumerate(porder):
        bi = rings[i][3]
        for j in porder[ii + 1:]:
            bj = rings[j][3]
            if bj[0] > bi[2] + need:
                break
            if not _meets(bi, bj, need):
                continue
            if clip.area(clip.boolean(grow(i), grow(j), "intersection")) > AREA_EPS:
                pr.tight.append((rings[i][0], rings[j][0], need))
    return pr


def needs_closed(op, values: dict) -> bool:
    """Whether a toolpath of this kind with these settings works on the inside of closed vectors."""
    if getattr(op, "needs_closed", False):
        return True
    return getattr(op, "id", "") == "profile" and values.get("side", "outside") != "on"


def check_toolpath(op, values: dict, objects, tool_diameter: float) -> Problems:
    """:func:`check` with what a toolpath of ``op`` with ``values`` cuts (nothing for one that does not need closed
    vectors: an engraving, a profile on the line, drilling)."""
    if not needs_closed(op, values):
        return Problems()
    side = values.get("side") if getattr(op, "id", "") == "profile" else None
    return check(objects, tool_diameter=tool_diameter, allowance=float(values.get("allowance") or 0.0), side=side)

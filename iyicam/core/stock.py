"""Stock plates: new sheets and leftovers of any shape that jobs are nested onto."""
from __future__ import annotations

import json
import math
import uuid
from dataclasses import dataclass, field
from pathlib import Path

from . import clip
from .geom import Pt

FORMAT = "iyicam-stock"
FORMAT_VERSION = 1


def rect_outline(width: float, height: float) -> list[list[Pt]]:
    return [[(0.0, 0.0), (width, 0.0), (width, height), (0.0, height)]]


@dataclass
class StockItem:
    """One kind of plate in the stock.

    ``outline`` are the outlines of the material in the plate's own coordinates (outer
    boundaries and holes, origin at the lower left of the bounding box); ``None`` is a plain
    ``width`` x ``height`` rectangle. ``quantity`` is the number of such plates (0 = no limit).
    ``leftover`` plates are used before new ones. ``thickness`` of ``None`` is the material's."""

    name: str
    width: float
    height: float
    outline: list[list[Pt]] | None = None
    quantity: int = 0
    leftover: bool = False
    thickness: float | None = None
    id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])

    def region(self) -> list[list[Pt]]:
        """Outlines of the material (a rectangle when the plate has no outline of its own)."""
        if self.outline:
            return [list(p) for p in self.outline]
        return rect_outline(self.width, self.height)

    @property
    def area(self) -> float:
        return abs(clip.area(clip.normalize(self.region())))

    @property
    def is_rect(self) -> bool:
        return not self.outline

    def copy(self) -> "StockItem":
        return StockItem(self.name, self.width, self.height, [list(p) for p in self.outline] if self.outline else None,
                         self.quantity, self.leftover, self.thickness)

    def to_dict(self) -> dict:
        return {"id": self.id, "name": self.name, "width": self.width, "height": self.height,
                "outline": [[[x, y] for x, y in p] for p in self.outline] if self.outline else None,
                "quantity": self.quantity, "leftover": self.leftover, "thickness": self.thickness}

    @classmethod
    def from_dict(cls, d: dict) -> "StockItem":
        outline = [[(float(x), float(y)) for x, y in p] for p in d["outline"]] if d.get("outline") else None
        return cls(d.get("name", "Plate"), float(d["width"]), float(d["height"]), outline,
                   int(d.get("quantity", 0)), bool(d.get("leftover", False)), d.get("thickness"),
                   d.get("id") or uuid.uuid4().hex[:12])


SHAPE_KINDS = ("rect", "rounded", "circle", "ellipse", "polygon", "l", "points")
ARC_TOLERANCE = 0.05       # how far the corners of a polygon that stands for a curve are from the curve (mm)


def _arc_steps(radius: float, tol: float = ARC_TOLERANCE) -> int:
    """Corners for a full circle so that no chord is further than ``tol`` from it (24 at least)."""
    if radius <= tol:
        return 24
    n = int(min(720, max(24, math.ceil(math.pi / math.acos(1.0 - tol / radius)))))
    return n + (-n) % 4                 # (a multiple of 4: the corners reach the extremes, so the box is the full size)


def ellipse_points(width: float, height: float, tol: float = ARC_TOLERANCE) -> list[Pt]:
    """Counter-clockwise polygon whose corners lie on the ellipse (so it is inside it), lower left of its box at the origin."""
    a, b = width / 2.0, height / 2.0
    n = _arc_steps(max(a, b), tol)
    return [(a + a * math.cos(2 * math.pi * k / n), b + b * math.sin(2 * math.pi * k / n)) for k in range(n)]


def shaped_outline(kind: str, **p) -> list[list[Pt]]:
    """The outlines (outer boundary first, then holes) of a plate of a shape, lower left of its box at the origin.

    ``rect`` (``width``, ``height``); ``rounded`` (``width``, ``height``, ``radius``); ``circle`` (``diameter``, optional
    ``hole``: the diameter of a hole in the middle); ``ellipse`` (``width``, ``height``); ``polygon`` (``sides``,
    ``diameter`` across the corners, a corner up); ``l`` (``width``, ``height``, ``notch_width``, ``notch_height``: the
    corner taken out at the top right); ``points`` (``points``: any polygon, optional ``holes``: polygons inside it).
    Curves are polygons whose corners lie on the curve, so a part that is inside the polygon is inside the shape."""
    f = lambda k, default=None: float(p[k]) if p.get(k) is not None else default
    if kind == "rect":
        return rect_outline(f("width"), f("height"))
    if kind == "rounded":
        w, h = f("width"), f("height")
        r = max(0.0, min(f("radius", 0.0), w / 2.0, h / 2.0))
        if r <= 0:
            return rect_outline(w, h)
        n = max(6, _arc_steps(r) // 4)
        pts: list[Pt] = []
        for cx, cy, a0 in ((w - r, r, -90.0), (w - r, h - r, 0.0), (r, h - r, 90.0), (r, r, 180.0)):
            for k in range(n + 1):
                a = math.radians(a0 + 90.0 * k / n)
                pts.append((cx + r * math.cos(a), cy + r * math.sin(a)))
        return [pts]
    if kind == "circle":
        d = f("diameter")
        out = [ellipse_points(d, d)]
        hole = f("hole", 0.0)
        if hole and 0 < hole < d:
            off = (d - hole) / 2.0
            out.append([(x + off, y + off) for x, y in ellipse_points(hole, hole)])
        return out
    if kind == "ellipse":
        return [ellipse_points(f("width"), f("height"))]
    if kind == "polygon":
        n = int(p.get("sides") or 0)
        if n < 3:
            raise ValueError("a polygon needs at least 3 sides")
        r = f("diameter") / 2.0
        pts = [(r * math.cos(math.pi / 2 + 2 * math.pi * k / n), r * math.sin(math.pi / 2 + 2 * math.pi * k / n))
               for k in range(n)]
        return [pts]
    if kind == "l":
        w, h, nw, nh = f("width"), f("height"), f("notch_width"), f("notch_height")
        if not (0 < nw < w and 0 < nh < h):
            raise ValueError("the notch of an L must be smaller than the plate")
        return [[(0.0, 0.0), (w, 0.0), (w, h - nh), (w - nw, h - nh), (w - nw, h), (0.0, h)]]
    if kind == "points":
        pts = [(float(x), float(y)) for x, y in p.get("points") or []]
        if len(pts) < 3:
            raise ValueError("a plate outline needs at least 3 points")
        return [pts] + [[(float(x), float(y)) for x, y in hole] for hole in p.get("holes") or [] if len(hole) >= 3]
    raise ValueError(f"unknown plate shape {kind!r} (one of: {', '.join(SHAPE_KINDS)})")


def shaped_plate(kind: str, name: str = "", quantity: int = 1, leftover: bool = False, thickness: float | None = None,
                 **p) -> "StockItem":
    """A plate of a shape (see :func:`shaped_outline`) as a stock item: its size is that of its bounding box."""
    polys = shaped_outline(kind, **p)
    w, h, outline = normalized(polys)
    if w <= 0 or h <= 0:
        raise ValueError("the plate has no size")
    if not name:
        name = {"rect": f"{w:g} x {h:g}", "rounded": f"{w:g} x {h:g} rounded", "circle": f"Circle \u00d8{w:g}",
                "ellipse": f"Ellipse {w:g} x {h:g}", "polygon": f"{int(p.get('sides') or 0)}-gon \u00d8{float(p.get('diameter') or 0):g}",
                "l": f"L {w:g} x {h:g}", "points": f"Offcut {w:g} x {h:g}"}[kind]
    return StockItem(name, w, h, None if kind == "rect" else outline, quantity, leftover, thickness)


def normalized(polys: list[list[Pt]]) -> tuple[float, float, list[list[Pt]]]:
    """``(width, height, outlines)`` with the outlines moved so their bounding box starts at the origin."""
    polys = clip.normalize(polys)
    xs = [x for p in polys for x, _ in p]
    ys = [y for p in polys for _, y in p]
    x0, y0 = min(xs), min(ys)
    return max(xs) - x0, max(ys) - y0, [[(x - x0, y - y0) for x, y in p] for p in polys]


def save_stock(path: str | Path, items: list[StockItem]) -> None:
    Path(path).write_text(json.dumps({"format": FORMAT, "version": FORMAT_VERSION,
                                      "items": [it.to_dict() for it in items]}, indent=1), encoding="utf-8")


def load_stock(path: str | Path) -> list[StockItem]:
    d = json.loads(Path(path).read_text(encoding="utf-8"))
    if d.get("format") != FORMAT:
        raise ValueError("not an iyiCAM stock file")
    items = [StockItem.from_dict(x) for x in d.get("items", [])]
    for it in items:
        it.id = uuid.uuid4().hex[:12]  # loaded plates are new entries, whatever they were called before
    return items

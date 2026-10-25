"""Stock plates: new sheets and leftovers of any shape that jobs are nested onto."""
from __future__ import annotations

import json
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

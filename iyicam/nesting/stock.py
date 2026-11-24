"""Stock plates for nesting: leftovers drawn as vectors, the leftover of a cut sheet, free areas of sheets."""
from __future__ import annotations

from ..core import clip
from ..core.document import Sheet, VectorObject
from ..core.stock import StockItem, normalized
from .nest import Container
from .parts import _region, group_parts, part_outline

__all__ = ["StockItem", "to_container", "sheet_container", "stock_from_vectors", "leftover_of_sheet", "use_up"]


def wide_enough(polys, min_width: float):
    """The part of the material ``polys`` where a disc of ``min_width`` fits (its corners kept sharp): strips and pockets
    narrower than that are left out."""
    if min_width <= 0 or not polys:
        return polys
    opened = clip.offset(clip.offset(polys, -min_width / 2, "round"), min_width / 2, "miter")
    return clip.boolean(opened, polys, "intersection") if opened else []


def to_container(item: StockItem, min_width: float = 0.0) -> Container:
    """A plate of the stock; a leftover of a shape of its own without its arms narrower than ``min_width``. A plain
    rectangle, and a plate narrower than that as a whole, are as they are: they were kept on purpose."""
    region = item.region()
    if min_width > 0 and item.leftover and item.outline and min(item.width, item.height) >= min_width:
        wide = wide_enough(clip.normalize(region), min_width)
        if wide:
            region = wide
    return Container(region, [], item.quantity, item.leftover, item.name, item)


def sheet_container(sheet: Sheet, moving: set[str] | None = None, tol: float = 0.05, min_width: float = 0.0) -> Container:
    """The free area of a sheet: its material, with the parts that stay on it as obstacles.

    Vectors in ``moving`` (the ones being nested) do not count. With ``min_width`` the material left between those parts
    that is narrower than that (strips between cuts, pockets closed in by parts) is an obstacle too: no part is put there."""
    fixed = [o for o in sheet.vectors if not moving or o.id not in moving]
    obstacles = [p for part in group_parts(fixed, tol) for p in part_outline(part, tol)]
    if min_width > 0 and obstacles and min(sheet.width, sheet.height) >= min_width:   # (a narrow sheet as a whole: as it is)
        material = clip.boolean(clip.normalize(sheet.region()), clip.normalize(obstacles, "nonzero"), "difference")
        narrow = clip.boolean(material, wide_enough(material, min_width), "difference") if material else []
        narrow = [q for q in narrow if abs(clip.area([q])) > 1.0 or clip.area([q]) < 0]
        obstacles = obstacles + narrow
    return Container(sheet.region(), obstacles, 1, True, sheet.name, sheet)


def stock_from_vectors(objects: list[VectorObject], quantity: int = 1, leftover: bool = True,
                       tol: float = 0.05) -> list[StockItem]:
    """One plate per closed outline among the vectors; closed vectors inside it are holes of the plate.

    This is how a leftover is entered by drawing (or importing) its outline."""
    items = []
    for part in group_parts(objects, tol):
        polys = _region(part.root, tol)
        for m in part.members:
            polys += _region(m, tol)
        polys = clip.normalize(polys)
        if not polys:
            continue
        w, h, outline = normalized(polys)
        items.append(StockItem(part.root.name or f"Leftover {len(items) + 1}", w, h, outline, quantity, leftover))
    return items


def leftover_of_sheet(sheet: Sheet, clearance: float = 3.0, min_width: float = 30.0, min_area: float = 2500.0,
                      tol: float = 0.05) -> list[StockItem]:
    """The material that is left on a sheet once its parts are cut out, as plates for the next job.

    ``clearance`` is how far the cuts reach round the parts (the tool radius and a little more),
    strips narrower than ``min_width`` and pieces smaller than ``min_area`` are not worth keeping."""
    material = clip.normalize(sheet.region())
    used = [p for part in group_parts(sheet.vectors, tol) for p in part_outline(part, tol)]
    left = clip.boolean(material, clip.offset(used, clearance), "difference") if used else material
    if min_width > 0 and left:
        opened = clip.offset(clip.offset(left, -min_width / 2, "round"), min_width / 2, "miter")
        left = clip.boolean(opened, left, "intersection")  # the opening only ever takes material away
    items = []
    for outer, holes in clip.polytree(left):
        if abs(clip.area([outer])) - sum(abs(clip.area([h])) for h in holes) < min_area:
            continue
        w, h, outline = normalized([outer] + holes)
        items.append(StockItem(f"{sheet.name} leftover {len(items) + 1}", w, h, outline, 1, True, sheet.thickness))
    items.sort(key=lambda it: -it.area)
    return items


def use_up(stock: list[StockItem], made_from: list) -> list[StockItem]:
    """The stock after plates were used: plates with a quantity are counted down, used up ones leave.

    ``made_from`` is what every sheet of a nesting was made from (``NestOutcome.plates``)."""
    used: dict[str, int] = {}
    for m in made_from:
        if isinstance(m, StockItem):
            used[m.id] = used.get(m.id, 0) + 1
    import dataclasses
    keep = []
    for it in stock:
        n = used.get(it.id, 0)
        if it.quantity > 0 and n:
            left = it.quantity - n
            if left <= 0:
                continue
            it = dataclasses.replace(it, quantity=left)        # (a copy: the item somebody else holds is not changed, and 0 means "as many as needed")
        keep.append(it)
    return keep

"""Rectangles on rectangular plates: a packer that is much better at it than the free-form nesting.

The free-form nesting places parts one after the other by their outlines; for rectangles that leaves holes a
rectangle packer does not leave (a hand-made layout of 270 rectangles with 90 % of the plates covered was packed
by it with 41 parts left over).  This module packs boxes with the MaxRects method (the free rectangles of a plate are
kept, every part goes to the best of them), over many orders of the parts and several rules, and keeps the best
result.  :func:`nest` in ``nest.py`` uses it when everything is a rectangle that may only turn by right angles and
every plate is a plain rectangle, and takes its result only when it is better than the free-form one, so the
nesting of any other shape is not touched."""
from __future__ import annotations

import random
import time
from typing import Callable

from . import nest as N

EPS = 1e-6
HEURISTICS = ("bssf", "baf", "bl")


class _Bin:
    __slots__ = ("x0", "y0", "x1", "y1", "free", "items", "box")

    def __init__(self, box, rect):
        self.box = box
        self.x0, self.y0, self.x1, self.y1 = rect
        self.free = [(self.x0, self.y0, self.x1 - self.x0, self.y1 - self.y0)]
        self.items: list[tuple] = []          # (shape, copy, x, y, w, h, rotated) in the inflated boxes

    def best(self, w: float, h: float, heur: str):
        """The best free rectangle for a w x h box: ``(score, x, y)`` or None."""
        best = None
        for fx, fy, fw, fh in self.free:
            if fw + EPS < w or fh + EPS < h:
                continue
            if heur == "bssf":
                s = (min(fw - w, fh - h), max(fw - w, fh - h))
            elif heur == "baf":
                s = (fw * fh - w * h, min(fw - w, fh - h))
            else:                              # bottom left: the lowest top edge, then the leftmost
                s = (fy + h, fx)
            if best is None or s < best[0]:
                best = (s, fx, fy)
        return best

    def put(self, x: float, y: float, w: float, h: float) -> None:
        out = []
        for fx, fy, fw, fh in self.free:
            if x >= fx + fw - EPS or x + w <= fx + EPS or y >= fy + fh - EPS or y + h <= fy + EPS:
                out.append((fx, fy, fw, fh))
                continue
            if x > fx + EPS:
                out.append((fx, fy, x - fx, fh))
            if x + w < fx + fw - EPS:
                out.append((x + w, fy, fx + fw - x - w, fh))
            if y > fy + EPS:
                out.append((fx, fy, fw, y - fy))
            if y + h < fy + fh - EPS:
                out.append((fx, y + h, fw, fy + fh - y - h))
        # a free rectangle inside another one is no use
        out.sort(key=lambda r: -r[2] * r[3])
        keep: list[tuple] = []
        for r in out:
            if not any(r[0] >= k[0] - EPS and r[1] >= k[1] - EPS and r[0] + r[2] <= k[0] + k[2] + EPS
                       and r[1] + r[3] <= k[1] + k[3] + EPS for k in keep):
                keep.append(r)
        self.free = keep


def rect_like(polys) -> tuple[float, float, float, float] | None:
    """The bounding box of a part that is a rectangle, or one with its corners rounded or cut a little (the box
    holds at least 99 % of it, and it is convex): it is packed as the box."""
    r = N._axis_rect(polys)
    if r is not None:
        return r
    if len(polys) != 1 or len(polys[0]) < 4:
        return None
    pts = polys[0]
    x0, y0 = min(x for x, _ in pts), min(y for _, y in pts)
    x1, y1 = max(x for x, _ in pts), max(y for _, y in pts)
    box = (x1 - x0) * (y1 - y0)
    if box <= 0 or abs(N.polygon_area(pts)) < 0.99 * box:
        return None
    return (x0, y0, x1, y1)


def _box_of(polys):
    pts = [p for poly in polys for p in poly]
    return (min(x for x, _ in pts), min(y for _, y in pts), max(x for x, _ in pts), max(y for _, y in pts))


def eligible(n: "N.Nester") -> bool:
    """Most of the area is rectangles (that turn by right angles only) on plain rectangular plates. A part that is not
    a rectangle (lettering, say) is packed as the box round it: the layout is only taken when it is better than
    the free-form one, which still decides for every job that is not mostly rectangles."""
    if n.st.arrange != "free" or not n.shapes:
        return False
    rect_area = total = 0.0
    for i, sh in enumerate(n.shapes):
        if sh.holes or (n.angles[i] and any(round(a % 90.0, 4) not in (0.0, 90.0) for a in n.angles[i])):
            return False
        if n.cls[i] < 0:
            continue
        w = max(0, sh.quantity) * n.gross[i]
        total += w
        if rect_like(sh.polys) is not None:
            rect_area += w
    if total <= 0 or rect_area < 0.8 * total:
        return False
    return all(b.rect is not None and not b.paths and not b.c.obstacles for b in n.boxes if b.usable)


def _sizes(n: "N.Nester"):
    """Per shape: (w, h, can stay, can turn, rect) with the box of the part grown by the spacing."""
    S = n.st.spacing
    out = []
    for i, sh in enumerate(n.shapes):
        if not n.angles[i]:
            out.append(None)
            continue
        x0, y0, x1, y1 = rect_like(sh.polys) or _box_of(sh.polys)
        angs = {round(a % 360.0, 4) for a in n.angles[i]}
        stay = bool(angs & {0.0, 180.0})
        turn = bool(angs & {90.0, 270.0})
        out.append((x1 - x0 + S, y1 - y0 + S, stay, turn, (x0, y0, x1, y1), sorted(angs)))
    return out


def _pack(n, sizes, order, plates, heur, bin_first, cancel):
    """One layout: the items in this order, each into the first open plate that has room (or the best of them);
    a new plate is opened from ``plates`` when none has."""
    from .nest import clip
    S = clip.SCALE
    bins: list[_Bin] = []
    made: dict[int, int] = {}
    nxt = 0
    unplaced = []
    for item in order:
        si = item[0]
        sz = sizes[si]
        shapes = []
        if sz[2]:
            shapes.append((sz[0], sz[1], False))
        if sz[3] and (sz[0] != sz[1] or not sz[2]):
            shapes.append((sz[1], sz[0], True))
        placed = False
        for attempt in range(2):                 # the open plates, then (if none has room) a new plate
            choice = None
            for bi, b in enumerate(bins):
                for w, h, rot in shapes:
                    c = b.best(w, h, heur)
                    if c is not None:
                        key = (bi if bin_first else 0, c[0])
                        if choice is None or key < choice[0]:
                            choice = (key, bi, c[1], c[2], w, h, rot)
                if choice is not None and bin_first:
                    break                        # first plate that takes it
            if choice is not None:
                _, bi, x, y, w, h, rot = choice
                bins[bi].put(x, y, w, h)
                bins[bi].items.append((item[0], item[1], x, y, w, h, rot))
                placed = True
                break
            # open the next plate that is able to take it at all
            opened = False
            while nxt < len(plates) and not opened:
                box = plates[nxt]
                q = box.c.quantity
                if q > 0 and made.get(box.index, 0) >= q:
                    nxt += 1
                    continue
                if len(bins) >= n.st.max_sheets:
                    break
                rect = tuple(v / S for v in box.rect)
                rect = (rect[0], rect[1], rect[2] - n.st.tolerance, rect[3] - n.st.tolerance)    # (as safe as the free-form layout is at the edge)
                if not any(w <= rect[2] - rect[0] + EPS and h <= rect[3] - rect[1] + EPS for w, h, _ in shapes):
                    break                        # this one is too big for the plate in front: it is not placed
                bins.append(_Bin(box, rect))
                made[box.index] = made.get(box.index, 0) + 1
                opened = True
            if not opened:
                break
        if not placed:
            unplaced.append(item)
    return bins, unplaced


def _result(n, sizes, bins, unplaced) -> "N.NestResult":
    res = N.NestResult()
    res.unplaced = list(unplaced)
    half = n.st.spacing / 2.0
    count: dict[int, int] = {}
    for sheet, b in enumerate(bins):
        res.stock.append(b.box.index)
        units = sum(1 for x in res.stock[:-1] if x == b.box.index)
        res.units.append(units)
        area = 0.0
        ux0 = uy0 = 1e18
        ux1 = uy1 = -1e18
        for si, cp, x, y, w, h, rot in b.items:
            px, py = x + half, y + half                  # the part's own box (the box above is grown by the spacing)
            pw, ph = w - n.st.spacing, h - n.st.spacing
            x0, y0, x1, y1 = sizes[si][4]
            angs = sizes[si][5]
            if not rot:
                ang = 0.0 if 0.0 in angs else 180.0
            else:
                ang = 90.0 if 90.0 in angs else 270.0
            # the part turns about the origin of its own coordinates, then moves by (tx, ty)
            if ang == 0.0:
                tx, ty = px - x0, py - y0
            elif ang == 90.0:
                tx, ty = px + y1, py - x0
            elif ang == 180.0:
                tx, ty = px + x1, py + y1
            else:
                tx, ty = px - y0, py + x1
            res.placements.append(N.Placement(si, cp, sheet, ang, tx, ty))
            area += n.areas[si]
            ux0, uy0, ux1, uy1 = min(ux0, px), min(uy0, py), max(ux1, px + pw), max(uy1, py + ph)
        res.used.append((ux0, uy0, ux1, uy1) if b.items else (0.0, 0.0, 0.0, 0.0))
        free = b.box.free
        res.utilization.append(area / free if free > 0 else 0.0)
    res.sheets = len(bins)
    return res


def pack_rects(n: "N.Nester", order: list[tuple[int, int]], iterations: int, time_limit: float = 0.0,
               seed: int = 1, cancel: Callable[[], bool] | None = None, progress: Callable | None = None):
    """The best layout of the items by many orders and rules, or None when the shapes are not all rectangles."""
    if not eligible(n):
        return None
    sizes = _sizes(n)
    items = [it for it in order if sizes[it[0]] is not None]
    cant = [it for it in order if sizes[it[0]] is None]
    plates = n._plates()
    if not items or not plates:
        return None
    rng = random.Random(seed)
    t0 = time.perf_counter()
    limit = time_limit if time_limit > 0 else 12.0
    keys = {
        "area": lambda it: -(sizes[it[0]][0] * sizes[it[0]][1]),
        "long": lambda it: -max(sizes[it[0]][0], sizes[it[0]][1]),
        "short": lambda it: -min(sizes[it[0]][0], sizes[it[0]][1]),
        "height": lambda it: -sizes[it[0]][1],
        "width": lambda it: -sizes[it[0]][0],
        "perim": lambda it: -(sizes[it[0]][0] + sizes[it[0]][1]),
    }

    def score(bins, unplaced):
        cost = sum(b.box.material for b in bins if not b.box.c.leftover)
        last = bins[-1] if bins else None
        # the part of the last plate that is used: the less the better (the rest is a plate saved or a leftover)
        used = 0.0
        if last is not None and last.items:
            used = sum(w * h for _, _, _, _, w, h, _ in last.items)
        return (len(unplaced), cost, len(bins), round(used, 1))

    best = None
    tried = 0
    plan = [(k, h, bf) for k in keys for h in HEURISTICS for bf in (True, False)]
    budget = max(len(plan), 40 * max(1, iterations))
    cur_order = None
    while tried < budget:
        if cancel and cancel():
            raise N.NestCancelled()
        if tried > 0 and time.perf_counter() - t0 > limit:
            break
        if tried < len(plan):
            k, h, bf = plan[tried]
            ordr = sorted(items, key=keys[k])
        else:
            h, bf = rng.choice(HEURISTICS), rng.random() < 0.7
            ordr = list(cur_order)
            if rng.random() < 0.3:                          # a new order with noise
                kk = rng.choice(list(keys))
                ordr = sorted(items, key=lambda it: keys[kk](it) * rng.uniform(0.85, 1.15))
            else:                                           # a few swaps / moves in the best order so far
                for _ in range(rng.randint(1, max(2, len(ordr) // 12))):
                    a = rng.randrange(len(ordr))
                    b = min(len(ordr) - 1, max(0, a + rng.randint(-8, 8)))
                    ordr.insert(b, ordr.pop(a))
        bins, unplaced = _pack(n, sizes, ordr, plates, h, bf, cancel)
        sc = score(bins, unplaced)
        if best is None or sc < best[0]:
            best = (sc, bins, unplaced, ordr)
            cur_order = ordr
        tried += 1
        if progress:
            progress(min(tried, budget), budget)
        if best[0][0] == 0 and tried >= len(plan) and best[0][2] <= 1:
            break                                           # everything on one plate: nothing better exists
    if best is None:
        return None
    res = _result(n, sizes, best[1], best[2] + cant)
    return res

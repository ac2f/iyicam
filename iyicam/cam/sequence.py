"""Cut order: which vector is machined when.

Every strategy obeys the rule that keeps material supported: whatever lies
inside a closed cut is machined before that cut (holes before their outline, a
part nested in a hole before the hole). Between parts the chosen strategy
decides: shortest travel, rows / columns / diagonals from a corner, zig-zag
sweeps, smallest parts first, ...

Orders are lists of item keys (``"<vector id>:<contour index>"``) so they
survive geometry edits such as a moved start point. A frozen (manual) order is
kept as it is: new items are appended and only the support rules may move an
item.

Cut areas of neighbouring parts can also enclose scrap together (a small part
nested between bigger ones): :func:`island_constraints` removes every cut area
from the sheet at once, finds the material islands that would end up loose and
requires whatever lies in such an island to be cut before its surroundings are
complete. :func:`verify_support` replays an order cut by cut and reports any
cut made in material that is no longer connected to the sheet frame.
"""
from __future__ import annotations

import math
from collections import defaultdict
from dataclasses import dataclass, field

import numpy as np

from ..core import clip
from ..core.geom import BBox, Contour, Pt, dist, point_in_polygon, polygon_area

STRATEGIES: list[tuple[str, str]] = [
    ("smart", "Smart: shortest route (optimised)"),
    ("nearest", "Shortest travel (nearest next)"),
    ("rows_bl", "Rows: bottom left → top right"),
    ("rows_br", "Rows: bottom right → top left"),
    ("rows_tl", "Rows: top left → bottom right"),
    ("rows_tr", "Rows: top right → bottom left"),
    ("zigzag_up", "Bottom → top (zig-zag rows)"),
    ("zigzag_down", "Top → bottom (zig-zag rows)"),
    ("zigzag_right", "Left → right (zig-zag columns)"),
    ("zigzag_left", "Right → left (zig-zag columns)"),
    ("cols_bl", "Columns: bottom left → top right"),
    ("cols_br", "Columns: bottom right → top left"),
    ("cols_tl", "Columns: top left → bottom right"),
    ("cols_tr", "Columns: top right → bottom left"),
    ("diag_bl", "Diagonal: bottom left → top right"),
    ("diag_br", "Diagonal: bottom right → top left"),
    ("diag_tl", "Diagonal: top left → bottom right"),
    ("diag_tr", "Diagonal: top right → bottom left"),
    ("small_first", "Smallest parts first"),
    ("centre_out", "From the sheet centre outwards"),
    ("edges_in", "From the sheet edges inwards"),
]

GROUPINGS: list[tuple[str, str]] = [
    ("depth", "Inner pieces (göbek) of all parts first, then the outlines"),
    ("part", "Part by part (its inner pieces, then its outline)"),
]

DIRECTIONS: list[tuple[str, str]] = [
    ("toolpath", "As the toolpath says"),
    ("climb", "Climb milling"),
    ("conventional", "Conventional milling"),
    ("cw", "Outlines clockwise"),
    ("ccw", "Outlines counter-clockwise"),
    ("mirror", "Mirrored with the sweep (clockwise for bottom left → top right)"),
]

DEFAULTS: dict = {
    "auto_order": True,        # re-order with the strategy on every calculation
    "strategy": "nearest",
    "grouping": "depth",
    "band": 0.0,               # row / column width for the row strategies (0 = automatic)
    "order": [],               # the current (or frozen) order of item keys
    "smart_starts": False,     # move the vectors' start points to the best places
    "start_margin": 1.0,       # keep the plunge this far from machined areas
    "corner_distance": 0.0,    # keep the start this far from corners (0 = automatic)
    "direction": "toolpath",   # cutting direction of part outlines, see DIRECTIONS
    "directions": {},          # the same per strategy: {strategy: direction}, over "direction"
}

_CORNERS = {"bl": (1.0, 1.0), "br": (-1.0, 1.0), "tl": (1.0, -1.0), "tr": (-1.0, -1.0)}
_ZIGZAG = {"zigzag_up": ("rows", 1.0, 1.0), "zigzag_down": ("rows", 1.0, -1.0),
           "zigzag_right": ("cols", 1.0, 1.0), "zigzag_left": ("cols", -1.0, 1.0)}


_ZIGZAG_HOLD = {"zigzag_up": (0.0, 1.0), "zigzag_down": (0.0, -1.0), "zigzag_right": (1.0, 0.0),
                "zigzag_left": (-1.0, 0.0)}


def hold_direction(strategy: str, centre: Pt = (0.0, 0.0), sheet: BBox | None = None) -> Pt | None:
    """Where the sheet is held most firmly while a part at ``centre`` is cut: towards the
    material the sweep has not reached yet (right and top for bottom left -> top right)."""
    if strategy in _ZIGZAG_HOLD:
        return _ZIGZAG_HOLD[strategy]
    if strategy.split("_")[-1] in _CORNERS and strategy.split("_")[0] in ("rows", "cols", "diag"):
        sx, sy = _CORNERS[strategy.split("_")[-1]]
        return (sx * math.sqrt(0.5), sy * math.sqrt(0.5))
    if strategy in ("centre_out", "edges_in") and sheet is not None and not sheet.empty:
        dx, dy = centre[0] - sheet.center[0], centre[1] - sheet.center[1]
        n = math.hypot(dx, dy)
        if n > 1e-6:
            k = 1.0 if strategy == "centre_out" else -1.0
            return (k * dx / n, k * dy / n)
    return None


def mirrored_clockwise(strategy: str) -> bool | None:
    """Outline direction of 'mirrored with the sweep': clockwise for bottom left -> top right and
    every sweep that is a turn of it, counter-clockwise for its mirror images."""
    h = hold_direction(strategy)
    if h is None or strategy in ("centre_out", "edges_in"):
        return None
    if h[0] == 0.0 or h[1] == 0.0:  # zig-zag sweeps: up and right as bottom left -> top right
        return h[0] + h[1] > 0
    return h[0] * h[1] > 0


def direction_for(cfg: dict) -> str:
    """The cutting direction chosen for the strategy in use (see DIRECTIONS)."""
    s = settings(cfg)
    return (s.get("directions") or {}).get(s["strategy"], s["direction"])


def milling(cfg: dict, side: str, climb: bool) -> tuple[bool, bool | None]:
    """``(climb, clockwise)`` for a profile cut on ``side`` whose own setting is ``climb``.

    Climb milling runs clockwise around the outside of a part and counter-clockwise
    inside it. ``clockwise`` is only set for cuts on the line, which have no side
    to mill on: their closed vectors are then turned that way.
    """
    d = direction_for(cfg)
    if d in ("climb", "conventional"):
        return (d == "climb", None)
    cw = {"cw": True, "ccw": False}.get(d)
    if d == "mirror":
        cw = mirrored_clockwise(settings(cfg)["strategy"])
    if cw is None:
        return (climb, None)
    if side == "outside":
        return (cw, None)
    if side == "inside":
        return (not cw, None)
    return (climb, cw)


def settings(d: dict | None) -> dict:
    """Sequencing settings with defaults filled in."""
    out = dict(DEFAULTS)
    out.update(d or {})
    out["order"] = list(out.get("order") or [])
    out["directions"] = dict(out.get("directions") or {})
    return out


@dataclass(eq=False)
class SeqItem:
    """Something cut in one go: a vector contour, or a machined loop standing for it."""

    key: str
    contour: Contour             # decides containment, position and size
    frees: bool = True           # a closed cut through the material sets its inside free
    kerf: list | None = None     # machined area polygons, for the geometric support check
    solid: list | None = None    # part material this cut leaves (outline minus holes), if known
    parent: str | None = None    # innermost closed item around this one
    depth: int = 0
    bbox: BBox = field(init=False)
    poly: list = field(init=False)
    area: float = field(init=False)

    def __post_init__(self) -> None:
        self.bbox = self.contour.bbox()
        self.poly = self.contour.flatten(0.05) if self.contour.closed else []
        self.area = abs(polygon_area(self.poly)) if self.poly else 0.0

    @property
    def closed(self) -> bool:
        return self.contour.closed

    @property
    def center(self) -> Pt:
        return self.bbox.center

    def entry(self, pos: Pt) -> tuple[Pt, Pt]:
        """Where the item is entered from ``pos`` and where the tool is when it is done."""
        c = self.contour
        if c.closed:
            q = c.nearest(pos)[2]
            return q, q
        if dist(pos, c.end) < dist(pos, c.start):
            return c.end, c.start
        return c.start, c.end


# ---------------------------------------------------------------------------
# containment
# ---------------------------------------------------------------------------

def containment(items: list[SeqItem]) -> None:
    """Set ``parent`` (innermost closed item around each item) and ``depth``."""
    closed = sorted((it for it in items if it.closed and it.area > 0), key=lambda it: it.area)
    if closed:
        cb = np.array([[o.bbox.xmin, o.bbox.ymin, o.bbox.xmax, o.bbox.ymax] for o in closed])
        ca = np.array([o.area for o in closed])
    for it in items:
        it.parent, it.depth = None, 0
        if not closed:
            continue
        b = it.bbox
        fits = ((cb[:, 0] <= b.xmin) & (cb[:, 1] <= b.ymin) & (cb[:, 2] >= b.xmax) & (cb[:, 3] >= b.ymax)
                & ((ca > it.area) if it.closed else True))
        cand = np.flatnonzero(fits)
        if not len(cand):
            continue
        c = it.contour
        L = c.length()
        probes = [c.point_at_length(L * f) for f in ((0.0, 1 / 3, 2 / 3) if c.closed else (0.0, 0.5, 1.0))]
        for i in cand:  # smallest first: the first one found is the innermost
            o = closed[i]
            if o is not it and sum(point_in_polygon(p, o.poly) for p in probes) >= 2:
                it.parent = o.key
                break
    by_key = {it.key: it for it in items}
    for it in items:
        d, p, seen = 0, it.parent, set()
        while p is not None and p not in seen:
            seen.add(p)
            d += 1
            p = by_key[p].parent if p in by_key else None
        it.depth = d


# ---------------------------------------------------------------------------
# strategies
# ---------------------------------------------------------------------------

def _plan(strategy: str) -> tuple[str, float, float, bool]:
    """``(pattern, x sign, y sign, zig-zag)`` of a sweep strategy."""
    if strategy in _ZIGZAG:
        pat, sx, sy = _ZIGZAG[strategy]
        return pat, sx, sy, True
    pat, corner = strategy.split("_")
    sx, sy = _CORNERS[corner]
    return pat, sx, sy, False


def _nearest(items: list[SeqItem], pos: Pt) -> list[SeqItem]:
    """Greedy nearest next item (exact distances, pruned with bounding boxes)."""
    n = len(items)
    if n <= 1:
        return list(items)
    bb = np.array([[it.bbox.xmin, it.bbox.ymin, it.bbox.xmax, it.bbox.ymax] for it in items])
    alive = np.ones(n, bool)
    out = []
    for _ in range(n):
        x, y = pos
        lb = np.hypot(np.maximum(0.0, np.maximum(bb[:, 0] - x, x - bb[:, 2])),
                      np.maximum(0.0, np.maximum(bb[:, 1] - y, y - bb[:, 3])))
        lb[~alive] = np.inf
        best, best_d, best_exit = -1, math.inf, pos
        for i in np.argsort(lb, kind="stable"):
            if lb[i] > best_d or not alive[i]:
                break
            q, e = items[i].entry(pos)
            d = dist(pos, q)
            if d < best_d - 1e-9:
                best, best_d, best_exit = int(i), d, e
        alive[best] = False
        out.append(items[best])
        pos = best_exit
    return out


def route_length(items: list[SeqItem], pos: Pt) -> float:
    """How far the tool travels between the cuts when ``items`` are cut in this order, starting from ``pos``
    (every item is entered where it is nearest to the tool, as the cut does)."""
    total = 0.0
    for it in items:
        q, out = it.entry(pos)
        total += dist(pos, q)
        pos = out
    return total


SMART_LIMIT = 400     # more items than this: the smart strategy stays with the nearest-next order (it is O(n^2))


def _two_opt(pts: np.ndarray, start: np.ndarray, passes: int = 60) -> list[int]:
    """A short path through ``pts`` from ``start`` (2-opt: reverse stretches of the path while that shortens it)."""
    n = len(pts)
    P = np.vstack([start[None, :], pts])
    D = np.hypot(P[:, None, 0] - P[None, :, 0], P[:, None, 1] - P[None, :, 1])
    tour = list(range(n + 1))                 # (0 is the start: it stays first)
    for _ in range(passes):
        improved = False
        for i in range(1, n):
            a = tour[i - 1]
            ti = tour[i]
            js = np.arange(i + 1, n + 1)
            tj = np.array([tour[j] for j in js])
            nxt = np.array([tour[j + 1] if j + 1 <= n else -1 for j in js])
            old = D[a, ti] + np.where(nxt >= 0, D[tj, np.maximum(nxt, 0)], 0.0)
            new = D[a, tj] + np.where(nxt >= 0, D[ti, np.maximum(nxt, 0)], 0.0)
            gain = old - new
            k = int(np.argmax(gain))
            if gain[k] > 1e-9:
                j = int(js[k])
                tour[i:j + 1] = tour[i:j + 1][::-1]
                improved = True
        if not improved:
            break
    return [k - 1 for k in tour[1:]]


def _smart(items: list[SeqItem], pos: Pt) -> list[SeqItem]:
    """Nearest-next, then the route shortened by 2-opt; never longer than nearest-next."""
    greedy = _nearest(items, pos)
    if len(greedy) < 4 or len(greedy) > SMART_LIMIT:
        return greedy
    pts = np.array([it.center for it in greedy], float)
    better = [greedy[i] for i in _two_opt(pts, np.asarray(pos, float))]
    return better if route_length(better, pos) < route_length(greedy, pos) - 1e-9 else greedy


def _rows(ys: np.ndarray, sizes: list[float], band: float) -> np.ndarray:
    """Row number (0, 1, 2, ... without gaps) of every item along ``ys``.

    With a ``band`` the rows are bands of that height; otherwise a new row starts
    wherever the next item is more than half a typical item size further on.
    """
    if band > 0:
        raw = np.floor((ys - ys.min()) / band + 1e-9)
    else:
        gap = 0.5 * max(1.0, float(np.median(sizes)))
        order = np.argsort(ys, kind="stable")
        raw = np.empty(len(ys))
        r = 0
        for n, i in enumerate(order):
            if n and ys[i] - ys[order[n - 1]] > gap:
                r += 1
            raw[i] = r
    return np.searchsorted(np.unique(raw), raw)


def rank(items: list[SeqItem], strategy: str, pos: Pt, band: float = 0.0,
         sheet: BBox | None = None) -> list[SeqItem]:
    """Order ``items`` (which do not contain each other) by ``strategy``."""
    items = list(items)
    if len(items) > 1 and strategy == "smart":
        return _smart(items, pos)
    if len(items) <= 1 or strategy == "nearest":
        return _nearest(items, pos)
    cx = np.array([it.center[0] for it in items])
    cy = np.array([it.center[1] for it in items])
    idx = list(range(len(items)))
    if strategy == "small_first":
        idx.sort(key=lambda i: (round(items[i].area, 6), cy[i], cx[i]))
    elif strategy in ("centre_out", "edges_in"):
        if sheet is None or sheet.empty:
            sheet = BBox.of_points([it.center for it in items])
        mx, my = sheet.center
        d = np.hypot(cx - mx, cy - my)
        sign = 1.0 if strategy == "centre_out" else -1.0
        idx.sort(key=lambda i: (sign * round(d[i], 6), cy[i], cx[i]))
    else:
        pat, sx, sy, zig = _plan(strategy)
        xs, ys = sx * cx, sy * cy
        if pat == "diag":
            idx.sort(key=lambda i: (round(xs[i] + ys[i], 6), xs[i]))
        else:
            if pat == "cols":
                xs, ys = ys, xs  # columns are rows of the transposed layout
            row = _rows(ys, [it.bbox.height if pat == "rows" else it.bbox.width for it in items], band)
            idx.sort(key=lambda i: (row[i], -xs[i] if zig and row[i] % 2 else xs[i], ys[i]))
    return [items[i] for i in idx]


def order_items(items: list[SeqItem], strategy: str = "nearest", grouping: str = "depth",
                pos: Pt = (0.0, 0.0), band: float = 0.0, sheet: BBox | None = None) -> list[str]:
    """Keys in cutting order; ``containment`` must have been run on the full item set."""
    present = {it.key for it in items}
    out: list[str] = []

    def exit_of(it: SeqItem, p: Pt) -> Pt:
        return it.entry(p)[1]

    if grouping == "part":
        kids: dict[str | None, list[SeqItem]] = defaultdict(list)
        for it in items:
            kids[it.parent if it.parent in present else None].append(it)

        def emit(group: list[SeqItem], p: Pt) -> Pt:
            for it in rank(group, strategy, p, band, sheet):
                p = emit(kids.get(it.key, []), p)
                out.append(it.key)
                p = exit_of(it, p)
            return p

        emit(kids[None], pos)
        return out
    for d in sorted({it.depth for it in items}, reverse=True):
        for it in rank([it for it in items if it.depth == d], strategy, pos, band, sheet):
            out.append(it.key)
            pos = exit_of(it, pos)
    return out


def repair(order: list[str], items: dict[str, SeqItem]) -> tuple[list[str], bool]:
    """Move inner items in front of the closed items around them; otherwise keep the order."""
    kids: dict[str, list[str]] = defaultdict(list)
    for k in order:
        p = items[k].parent
        if p is not None and p in items:
            kids[p].append(k)
    out: list[str] = []
    placed: set[str] = set()

    def place(k: str) -> None:
        if k in placed:
            return
        placed.add(k)
        for c in kids.get(k, []):
            place(c)
        out.append(k)

    for k in order:
        place(k)
    return out, out != order


def descendants(key: str, items: dict[str, SeqItem]) -> set[str]:
    out = set()
    for k, it in items.items():
        p, seen = it.parent, set()
        while p is not None and p not in seen:
            if p == key:
                out.add(k)
                break
            seen.add(p)
            p = items[p].parent if p in items else None
    return out


def sequence(items: list[SeqItem], cfg: dict, pos: Pt = (0.0, 0.0),
             sheet: BBox | None = None) -> tuple[list[str], list[str]]:
    """The cutting order of ``items`` for the settings ``cfg`` and notes for the user.

    Automatic: the strategy decides. Frozen: the stored order is kept, new items
    follow it in strategy order. Either way inner items come first and, when cut
    areas touch, the geometric support check has the last word.
    """
    s = settings(cfg)
    by_key = {it.key: it for it in items}
    containment(items)
    notes: list[str] = []
    if s["auto_order"] or not s["order"]:
        order = order_items(items, s["strategy"], s["grouping"], pos, s["band"], sheet)
    else:
        order = list(dict.fromkeys(k for k in s["order"] if k in by_key))
        known = set(order)
        rest = [it for it in items if it.key not in known]
        if rest:
            last = by_key[order[-1]].entry(pos)[1] if order else pos
            order += order_items(rest, s["strategy"], s["grouping"], last, s["band"], sheet)
    order, changed = repair(order, by_key)
    if changed and not s["auto_order"]:
        notes.append("Cut order adjusted: vectors inside others are cut first")
    order, moved = apply_islands(order, island_constraints(items, sheet), by_key)
    if moved:
        notes.append(f"Cut order adjusted: {len(moved)} cut(s) moved forward, they lie in scrap that the "
                     "cuts around them set free")
    bad = gouges(items)
    if bad:
        notes.append(f"{len(bad)} cut(s) run into neighbouring parts: the parts are closer than the tool allows")
    return order, notes


def manual_from(order: list[str], index: int, picked: list[str]) -> list[str]:
    """Put ``picked`` (in that order) at position ``index`` (0 based); the rest keeps its order."""
    chosen = list(dict.fromkeys(picked))
    taken = set(chosen)
    rest = [k for k in order if k not in taken]
    index = max(0, min(index, len(rest)))
    return rest[:index] + chosen + rest[index:]


# ---------------------------------------------------------------------------
# geometric support check
# ---------------------------------------------------------------------------

def ancestors(it: SeqItem, items: dict[str, SeqItem]) -> set[str]:
    out, p = set(), it.parent
    while p is not None and p not in out:
        out.add(p)
        p = items[p].parent if p in items else None
    return out


def _bboxes(polys_list: list[list]) -> np.ndarray:
    out = np.full((len(polys_list), 4), np.nan)
    for i, polys in enumerate(polys_list):
        pts = [p for poly in polys or [] for p in poly]
        if pts:
            a = np.asarray(pts)
            out[i] = (*a.min(axis=0), *a.max(axis=0))
    return out


def _near(boxes: np.ndarray, bb: tuple, pad: float = 0.01) -> np.ndarray:
    return np.flatnonzero(~np.isnan(boxes[:, 0]) & (boxes[:, 0] <= bb[2] + pad) & (boxes[:, 2] >= bb[0] - pad)
                          & (boxes[:, 1] <= bb[3] + pad) & (boxes[:, 3] >= bb[1] - pad))


def island_constraints(items: list[SeqItem], sheet: BBox | None = None,
                       tol: float = 0.01) -> list[tuple[set[str], set[str]]]:
    """``(inner keys, surrounding keys)`` pairs: the inner items must be cut before the last
    surrounding one, or they would be cut in loose material.

    All cut areas of freeing cuts are removed from the sheet at once; every piece
    of material left that does not reach the sheet frame is an island. The cuts
    whose areas border an island from outside close it; items bordering it from
    inside, or lying in it, are in it.
    """
    freeing = [it for it in items if it.frees and it.kerf]
    if not freeing:
        return []
    frame = _frame(items, sheet)
    cut = clip.union([p for it in freeing for p in it.kerf])
    material = frame.material(cut)
    kerfed = [it for it in items if it.kerf]
    boxes = _bboxes([it.kerf for it in kerfed])
    by_key = {it.key: it for it in items}
    anc = {it.key: ancestors(it, by_key) for it in kerfed}
    ib = np.array([[it.bbox.xmin, it.bbox.ymin, it.bbox.xmax, it.bbox.ymax] for it in items])
    out = []
    for outer, holes in clip.polytree(material):
        if frame.touches(outer):
            continue  # still part of the sheet
        a = np.asarray(outer)
        lo, hi = a.min(axis=0), a.max(axis=0)
        if not np.any((ib[:, 0] >= lo[0]) & (ib[:, 2] <= hi[0]) & (ib[:, 1] >= lo[1]) & (ib[:, 3] <= hi[1])):
            continue  # nothing can lie in it: a loose scrap with no cut after it
        near = _near(boxes, (*lo, *hi), 2 * tol)
        keys = [kerfed[i].key for i in near]
        if all(x in anc[y] or y in anc[x] for i, x in enumerate(keys) for y in keys[i + 1:]):
            continue  # one family (a part and what is inside it): the containment rule covers it
        grown = clip.offset([outer] + holes, tol)
        inner, around = set(), set()
        for i in near:
            it = kerfed[i]
            if clip.area(clip.boolean(it.kerf, grown, "intersection")) <= tol * tol:
                continue
            probe = it.contour.point_at_length(0.0)
            (inner if point_in_polygon(probe, outer) else around).add(it.key)
        if inner and around:
            out.append((inner, around))
    return out


def apply_islands(order: list[str], constraints: list[tuple[set[str], set[str]]],
                  items: dict[str, SeqItem]) -> tuple[list[str], list[str]]:
    """Move inner items (with what lies inside them) in front of the last surrounding cut."""
    moved: list[str] = []
    for _ in range(len(order) + 1):
        pos = {k: i for i, k in enumerate(order)}
        change = None
        for inner, around in constraints:
            last = max((pos[k] for k in around if k in pos), default=-1)
            late = [k for k in inner if pos.get(k, -1) > last >= 0]
            if late:
                change = (last, late)
                break
        if change is None:
            return order, moved
        last, late = change
        block_keys = set(late)
        for k in late:
            block_keys |= descendants(k, items)
        block = [k for k in order if k in block_keys and order.index(k) > last]
        rest = [k for k in order if k not in set(block)]
        at = rest.index(order[last])
        order = rest[:at] + block + rest[at:]
        moved += [k for k in late if k not in moved]
    return order, moved


def gouges(items: list[SeqItem], tol: float = 0.05) -> list[tuple[str, str]]:
    """``(cut, part)`` pairs where a cut area reaches into another part's material."""
    solids = [it for it in items if it.solid]
    if not solids:
        return []
    by_key = {it.key: it for it in items}
    boxes = _bboxes([it.solid for it in solids])
    out = []
    for it in items:
        if not it.kerf:
            continue
        a = np.asarray([p for poly in it.kerf for p in poly])
        mine = ancestors(it, by_key) | {it.key}
        for i in _near(boxes, (*a.min(axis=0), *a.max(axis=0))):
            other = solids[i]
            if other.key in mine or it.key in ancestors(other, by_key):
                continue
            if clip.area(clip.boolean(it.kerf, other.solid, "intersection")) > tol:
                out.append((it.key, other.key))
    return out


class _Frame:
    """The sheet with a rim of held material round it: pieces of material that reach the rim's outer
    edge are still part of the sheet. A leftover's rim follows its outline."""

    RIM = 5.0

    def __init__(self, items: list[SeqItem], sheet: BBox | None):
        bb = BBox() if sheet is None else BBox(sheet.xmin, sheet.ymin, sheet.xmax, sheet.ymax)
        region = getattr(sheet, "region", None)
        if not region:  # a rectangle: the rim also takes in cuts that leave the sheet
            for it in items:
                for poly in it.kerf or []:
                    for p in poly:
                        bb.add_point(p)
        self.bb = bb.inflated(self.RIM)
        if region:
            self.base = clip.offset(region, self.RIM)
            self.inner = clip.offset(region, self.RIM - 1.0)
        else:
            f = self.bb
            self.base = [[(f.xmin, f.ymin), (f.xmax, f.ymin), (f.xmax, f.ymax), (f.xmin, f.ymax)]]
            self.inner = None

    def material(self, cut: list) -> list:
        return clip.boolean(self.base, cut, "difference")

    def touches(self, outer, border: float = 1.0) -> bool:
        """Whether a piece of material (its outer polygon) reaches the outer edge of the rim."""
        if self.inner is not None:
            return abs(clip.area(clip.boolean([outer], self.inner, "difference"))) > 1e-6
        f = self.bb
        return any(x <= f.xmin + border or x >= f.xmax - border or y <= f.ymin + border or y >= f.ymax - border
                   for x, y in outer)


def _frame(items: list[SeqItem], sheet: BBox | None) -> _Frame:
    return _Frame(items, sheet)


def _detached(cut_union: list, frame: _Frame) -> list:
    """Material pieces (polygons, holes included) that no longer touch the sheet frame."""
    out = []
    for outer, holes in clip.polytree(frame.material(cut_union)):
        if not frame.touches(outer):
            out += [outer] + holes
    return out


def verify_support(order: list[str], items: dict[str, SeqItem], sheet: BBox | None = None) -> list[int]:
    """Indices of cuts that would run in material earlier cuts have already set free.

    Replays the order: after every freeing cut its area is removed from the sheet
    and each following cut must stay in material connected to the sheet frame.
    Running into another part's material is gouging (see :func:`gouges`), not
    a question of order, and is not counted here.
    """
    seq = [items[k] for k in order if k in items]
    frame = _frame(seq, sheet)
    union: list = []
    bad = []
    for i, it in enumerate(seq):
        if it.kerf and union:
            loose = _detached(union, frame)
            if loose:
                mine = ancestors(it, items)
                others = [p for o in seq if o.solid and o is not it and o.key not in mine for p in o.solid]
                hit = clip.boolean(it.kerf, loose, "intersection")
                if others and hit:
                    hit = clip.boolean(hit, others, "difference")
                if abs(clip.area(hit)) > 0.05:
                    bad.append(i)
        if it.frees and it.kerf:
            union = clip.union(union + it.kerf)
    return bad

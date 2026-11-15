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
from collections import OrderedDict, defaultdict
from dataclasses import dataclass, field

import numpy as np

from ..core import clip
from ..core.geom import BBox, Contour, Pt, dist, point_in_polygon, polygon_area
from .pathutil import PathWalker
from .startpoints import EDGE_HOLD, _span_distance, closing_zone, corners, sample

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
    loop: Contour | None = None  # the loop the tool walks, in cutting direction (where the cut closes is judged on it)
    radius: float = 0.0          # tool radius of that loop
    fixed: Pt | None = None      # where its cut closes when that is set by hand and kept
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


def _nearest(items: list[SeqItem], pos: Pt, wait: dict[str, set[str]] | None = None) -> list[SeqItem]:
    """Greedy nearest next item (exact distances, pruned with bounding boxes).

    ``wait[key]`` are items that have to be cut before ``key``; an item waits for them as long as another
    item can be taken (when everything left waits, the nearest goes: a ring of waiting items is broken)."""
    n = len(items)
    if n <= 1:
        return list(items)
    bb = np.array([[it.bbox.xmin, it.bbox.ymin, it.bbox.xmax, it.bbox.ymax] for it in items])
    alive = np.ones(n, bool)
    index = {it.key: i for i, it in enumerate(items)}
    need = np.zeros(n, int)                       # how many of the items it waits for are still to be cut
    after: dict[int, list[int]] = defaultdict(list)
    for k, owners in (wait or {}).items():
        if k in index:
            for o in owners:
                if o in index and o != k:
                    need[index[k]] += 1
                    after[index[o]].append(index[k])
    out = []
    for _ in range(n):
        x, y = pos
        lb = np.hypot(np.maximum(0.0, np.maximum(bb[:, 0] - x, x - bb[:, 2])),
                      np.maximum(0.0, np.maximum(bb[:, 1] - y, y - bb[:, 3])))
        usable = alive & (need == 0)
        if not usable.any():
            usable = alive
        lb[~usable] = np.inf
        best, best_d, best_exit = -1, math.inf, pos
        for i in np.argsort(lb, kind="stable"):
            if lb[i] > best_d or not usable[i]:
                break
            q, e = items[i].entry(pos)
            d = dist(pos, q)
            if d < best_d - 1e-9:
                best, best_d, best_exit = int(i), d, e
        alive[best] = False
        for j in after.get(best, ()):
            need[j] -= 1
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


def _two_opt(pts: np.ndarray, start: np.ndarray, passes: int = 60, before: list[tuple[int, int]] | None = None
             ) -> list[int]:
    """A short path through ``pts`` from ``start`` (2-opt: reverse stretches of the path while that shortens it).
    ``before`` are pairs ``(a, b)`` of points ``a`` has to be visited before ``b``: no stretch that holds both is reversed."""
    n = len(pts)
    P = np.vstack([start[None, :], pts])
    D = np.hypot(P[:, None, 0] - P[None, :, 0], P[:, None, 1] - P[None, :, 1])
    tour = list(range(n + 1))                 # (0 is the start: it stays first)
    pa = np.array([a + 1 for a, _ in before or ()], int)
    pb = np.array([b + 1 for _, b in before or ()], int)
    at = np.arange(n + 1)                    # where every point stands in the tour
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
            for k in np.argsort(-gain)[:8 if len(pa) else 1]:
                if gain[k] <= 1e-9:
                    break
                j = int(js[k])
                if len(pa) and np.any((at[pa] >= i) & (at[pa] <= j) & (at[pb] >= i) & (at[pb] <= j)):
                    continue
                tour[i:j + 1] = tour[i:j + 1][::-1]
                at[tour[i:j + 1]] = np.arange(i, j + 1)
                improved = True
                break
        if not improved:
            break
    return [k - 1 for k in tour[1:]]


def _smart(items: list[SeqItem], pos: Pt, wait: dict[str, set[str]] | None = None) -> list[SeqItem]:
    """Nearest-next, then the route shortened by 2-opt; never longer than nearest-next."""
    greedy = _nearest(items, pos, wait)
    if len(greedy) < 4 or len(greedy) > SMART_LIMIT:
        return greedy
    pts = np.array([it.center for it in greedy], float)
    where = {it.key: i for i, it in enumerate(greedy)}
    before = [(where[a], where[b]) for b, owners in (wait or {}).items() for a in owners
              if a in where and b in where and where[a] < where[b]]
    better = [greedy[i] for i in _two_opt(pts, np.asarray(pos, float), before=before)]
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
         sheet: BBox | None = None, wait: dict[str, set[str]] | None = None) -> list[SeqItem]:
    """Order ``items`` (which do not contain each other) by ``strategy``. ``wait``: see :func:`_nearest`
    (the route strategies take it into account)."""
    items = list(items)
    if len(items) > 1 and strategy == "smart":
        return _smart(items, pos, wait)
    if len(items) <= 1 or strategy == "nearest":
        return _nearest(items, pos, wait)
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
        # a part is where the sweep first reaches it: its corner on the side the sweep starts from (parts that
        # stand on a common line are in one row however tall they are)
        ex = np.array([it.bbox.xmin if sx > 0 else it.bbox.xmax for it in items])
        ey = np.array([it.bbox.ymin if sy > 0 else it.bbox.ymax for it in items])
        xs, ys = sx * ex, sy * ey
        if pat == "diag":
            idx.sort(key=lambda i: (round(xs[i] + ys[i], 6), xs[i]))
        else:
            if pat == "cols":
                xs, ys = ys, xs  # columns are rows of the transposed layout
            row = _rows(ys, [it.bbox.height if pat == "rows" else it.bbox.width for it in items], band)
            idx.sort(key=lambda i: (row[i], -xs[i] if zig and row[i] % 2 else xs[i], ys[i]))
    return [items[i] for i in idx]


def order_items(items: list[SeqItem], strategy: str = "nearest", grouping: str = "depth",
                pos: Pt = (0.0, 0.0), band: float = 0.0, sheet: BBox | None = None,
                wait: dict[str, set[str]] | None = None) -> list[str]:
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
            for it in rank(group, strategy, p, band, sheet, wait):
                p = emit(kids.get(it.key, []), p)
                out.append(it.key)
                p = exit_of(it, p)
            return p

        emit(kids[None], pos)
        return out
    for d in sorted({it.depth for it in items}, reverse=True):
        for it in rank([it for it in items if it.depth == d], strategy, pos, band, sheet, wait):
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
             sheet: BBox | None = None, zones: bool = False) -> tuple[list[str], list[str]]:
    """The cutting order of ``items`` for the settings ``cfg`` and notes for the user.

    Automatic: the strategy decides. Frozen: the stored order is kept, new items
    follow it in strategy order. Either way inner items come first and, when cut
    areas touch, the geometric support check has the last word.

    With ``zones`` (the start points are placed by the program) an automatic order is also made to
    respect the closing zones: a cut is made before the neighbours that lie next to the place where it
    closes, so that it closes held by sheet that is still there (:func:`apply_zones`).
    """
    s = settings(cfg)
    by_key = {it.key: it for it in items}
    containment(items)
    notes: list[str] = []
    sig = _signature(items, s, pos, sheet, zones)
    hit = _ORDER_CACHE.get(sig)
    if hit is not None:
        _ORDER_CACHE.move_to_end(sig)
        return list(hit[0]), list(hit[1])
    islands = island_constraints(items, sheet)
    loose_fixed: list[str] = []

    def settle(order: list[str]) -> tuple[list[str], bool, list[str]]:
        order, changed = repair(order, by_key)
        order, moved = apply_islands(order, islands, by_key)
        return order, changed, moved

    if s["auto_order"] or not s["order"]:
        zoning = zones and s["strategy"] not in POLICY_STRATEGIES
        blockers = zone_blockers(items, s, sheet) if zoning else {}
        route = s["strategy"] in ("nearest", "smart")
        wait: dict[str, set[str]] = defaultdict(set)      # a route strategy keeps the neighbours that would take the
        if route and blockers:                             # sheet away from the closing zone of a cut for after it
            plain = order_items(items, s["strategy"], s["grouping"], pos, s["band"], sheet)
            wishes = _acyclic({a: set(bs) for a, bs in blockers.items()}, {k: i for i, k in enumerate(plain)})
            for a, bs in wishes.items():                   # (wishes that can all be had: none waits for itself)
                for b in bs:
                    wait[b].add(a)
        n = len(items)
        rp = Replayer(by_key, sheet) if n <= REPLAY_LIMIT else None    # (bigger jobs are not replayed: it takes too long)

        def made(wait_for) -> tuple[list[str], bool, list]:
            order, _, shifted = settle(order_items(items, s["strategy"], s["grouping"], pos, s["band"], sheet, wait_for))
            loose_fixed.extend(shifted)
            if rp is None:
                return order, False, []
            order, fixed, snaps = mend_loose(order, by_key, rp)
            loose_fixed.extend(fixed)
            return order, bool(snaps) or rp.nothing or rp.slow, snaps

        order, ok, snaps = made(wait if route and wait else None)
        if route and wait and rp is not None and not ok:
            order, ok, snaps = made(None)                                 # (the plain route)
        if zoning and blockers and (rp is None or ok):
            order = zone_order(order, blockers, by_key, pull_reach(n) if rp is not None else 2, settle,
                               sheet if rp is not None else None, replayer=rp, snaps=snaps)
        if zoning and rp is not None and ok:
            # every cut has to close held by sheet that is still there: whatever is not yet, goes in front of what takes it
            order, unheld = support_order(order, by_key, SupportMap(items, s, sheet), settle, sheet, rp)
            if unheld:
                notes.append(f"{len(unheld)} cut(s) cannot be made to close held by sheet that is still there: "
                             "parts packed round them are cut before them whatever the order")
    else:
        order = list(dict.fromkeys(k for k in s["order"] if k in by_key))
        known = set(order)
        rest = [it for it in items if it.key not in known]
        if rest:
            last = by_key[order[-1]].entry(pos)[1] if order else pos
            order += order_items(rest, s["strategy"], s["grouping"], last, s["band"], sheet)
    order, changed, moved = settle(order)
    if changed and not s["auto_order"]:
        notes.append("Cut order adjusted: vectors inside others are cut first")
    moved = list(dict.fromkeys(moved + loose_fixed))
    if moved:
        notes.append(f"Cut order adjusted: {len(moved)} cut(s) moved forward, they lie in scrap that the "
                     "cuts around them set free")
    bad = gouges(items)
    if bad:
        notes.append(f"{len(bad)} cut(s) run into neighbouring parts: the parts are closer than the tool allows")
    _ORDER_CACHE[sig] = (list(order), list(notes))
    while len(_ORDER_CACHE) > 8:
        _ORDER_CACHE.popitem(last=False)
    return order, notes


_ORDER_CACHE: "OrderedDict[tuple, tuple[list[str], list[str]]]" = OrderedDict()


def _signature(items: list[SeqItem], s: dict, pos: Pt, sheet: BBox | None, zones: bool) -> tuple:
    """What an order depends on: the settings, the sheet and every item's place, size and cut area (not where along
    its outline it starts: moving the start points does not change the order)."""
    sh = None if sheet is None else (round(sheet.xmin, 3), round(sheet.ymin, 3), round(sheet.xmax, 3), round(sheet.ymax, 3),
                                     repr(getattr(sheet, "region", None)))
    cfg = (s["strategy"], s["grouping"], s["band"], s["auto_order"], () if s["auto_order"] else tuple(s["order"]),
           s["corner_distance"], bool(zones))   # (an automatic order does not depend on the one made last time)
    rows = tuple((it.key, round(it.bbox.xmin, 3), round(it.bbox.ymin, 3), round(it.bbox.xmax, 3), round(it.bbox.ymax, 3),
                  round(it.area, 0), it.frees, round(it.radius, 3), None if it.fixed is None else tuple(round(v, 3) for v in it.fixed),
                  len(it.kerf or ()), len(it.solid or ())) for it in items)
    return (cfg, sh, tuple(round(v, 3) for v in pos), rows)


# ---------------------------------------------------------------------------
# closing zones
# ---------------------------------------------------------------------------

REPLAY_LIMIT = 400      # a zone order is replayed (:func:`is_supported`) up to this many cuts; more: only neighbours swap places
POLICY_STRATEGIES = ('small_first', 'centre_out', 'edges_in')   # an order with a purpose of its own: left as it is
ZONE_RINGS = (1.5, 5.0)     # how far beyond the cut the material has to be there (the two rings next to it)


FULL_SUPPORT = 0.99    # a cut is held when what is beyond it where it closes is there (all but a speck)
ZONE_BAND = 8.0        # mm along the loop from the target of the closing zone that still count as closing in it
ZONE_BAND_FRACTION = 0.08   # ... or this fraction of the part (its longer side), whichever is more


class SupportMap:
    """Where along its outline every cut could close held by sheet that is still there, and which other cuts would take
    that sheet away: the places and the cuts are worked out once, then asked for any order.

    The sheet beyond a cut is probed in the two rings next to it (1.5 and 5 mm beyond the cut width) at ``samples``
    places along the loop; a place holds the cut when the rings are there at it, at its neighbours and along the stretch
    cut last before it (6 tool radii), the way :func:`startpoints.choose_start` judges it. A place is also only usable
    when the plunging tool keeps ``start_margin`` from the cut areas of the cuts before it (it never goes down in
    them): :func:`startpoints.choose_start` leaves such places out, so this map does too.
    """

    def __init__(self, items: list[SeqItem], cfg: dict, sheet: BBox | None = None):
        cand = [it for it in items if it.loop is not None and it.closed and it.frees and it.depth % 2 == 0 and it.radius > 0]
        kerfed = [it for it in items if it.kerf]
        boxes = _bboxes([it.kerf for it in kerfed])
        by_key = {it.key: it for it in items}
        self.info: dict[str, dict] = {}
        region = getattr(sheet, "region", None)
        margin = float(cfg.get("start_margin", 1.0))
        grown: dict[tuple[str, float], list] = {}                  # the cut area of a cut grown by what the plunge keeps clear
        for it in cand:
            loop = it.loop
            walker = PathWalker(loop.segs, True)
            L = walker.length
            if L <= 1e-6:
                continue
            r = it.radius
            corner = cfg.get("corner_distance") or max(2.0, r)
            corner = min(corner, L / 8)
            S = int(min(240, max(64, L / 1.5)))                  # (the places choose_start samples the loop at)
            ss = np.arange(S) * L / S
            P, T = sample(walker, ss)
            cw = not loop.is_ccw()
            N = np.c_[-T[:, 1], T[:, 0]] if cw else np.c_[T[:, 1], -T[:, 0]]
            spans = corners(walker, tight=2 * r + 1.0)
            valid = _span_distance(ss, spans, L) >= corner - 1e-6
            if it.fixed is not None:                       # a start set by hand stays: only that place
                i0, t0, _, _ = loop.nearest(it.fixed)
                s0 = walker.cum[i0] + t0 * (walker.cum[i0 + 1] - walker.cum[i0])
                near = np.abs(((ss - s0 + L / 2) % L) - L / 2) <= L / S
                valid = near
            rings = []
            for d in ZONE_RINGS:
                Q = P + N * (r + d)
                if region:
                    on = clip.region_mask(Q, region)
                    p_on = clip.region_mask(P, region)
                elif sheet is not None:
                    on = (Q[:, 0] >= sheet.xmin) & (Q[:, 0] <= sheet.xmax) & (Q[:, 1] >= sheet.ymin) & (Q[:, 1] <= sheet.ymax)
                    p_on = (P[:, 0] >= sheet.xmin) & (P[:, 0] <= sheet.xmax) & (P[:, 1] >= sheet.ymin) & (P[:, 1] <= sheet.ymax)
                else:
                    on, p_on = np.ones(len(Q), bool), np.ones(len(Q), bool)
                edge = np.where(on, 1.0, np.where(p_on, EDGE_HOLD, 1.0))     # (off the sheet: its rim is still held)
                rings.append((Q, edge))
            lo, hi = np.min([q.min(axis=0) for q, _ in rings], axis=0), np.max([q.max(axis=0) for q, _ in rings], axis=0)
            hits: dict[str, tuple[np.ndarray, np.ndarray, np.ndarray]] = {}
            related = ancestors(it, by_key) | descendants(it.key, by_key)
            for j in _near(boxes, (*lo, *hi), 0.01):
                o = kerfed[j]
                if o is it or o.key in related:
                    continue
                h = [clip.region_mask(q, o.kerf) for q, _ in rings]
                g = grown.get((o.key, round(r, 3)))
                if g is None:
                    g = grown[(o.key, round(r, 3))] = clip.offset(o.kerf, r + margin)
                h0 = clip.region_mask(P, g)                       # (the tool going down here would touch that cut area)
                if h[0].any() or h[1].any() or h0.any():
                    hits[o.key] = (h[0], h[1], h0)
            m = max(1, int(math.floor(min(L / 3, 6 * r + corner) / (L / S) + 0.5)))      # (the samples cut last before a place)
            zone = closing_zone(walker, hold_direction(cfg["strategy"], it.center, sheet), cw, corner, spans)
            target = zone.target / L * S
            u = ((zone.corner[0] - ss) if zone.before else (ss - zone.corner[1])) % L       # mm from the corner of the zone
            gap = np.where(u <= L / 2, np.abs(u - zone.delta), 1e9)                          # (past the corner: not the zone)
            width = max(ZONE_BAND, ZONE_BAND_FRACTION * max(float(np.ptp(P[:, 0])), float(np.ptp(P[:, 1]))))
            band = valid & (gap <= width)
            self.info[it.key] = dict(S=S, m=min(m, S - 1), valid=valid, band=band if band.any() else valid,
                                     edge=(rings[0][1], rings[1][1]), hits=hits, target=target, gap=gap, width=width)

    def best(self, key: str, earlier: set[str], band: bool = False) -> tuple[float, int, set[str]]:
        """``(support, place, cuts that take it away)`` of the best place for the cut ``key`` to close at when the cuts
        ``earlier`` have been made: the place with the most support, the zone deciding among equals. With ``band``
        only the places that still count as the closing zone are looked at."""
        inf = self.info[key]
        S, m = inf["S"], inf["m"]
        any1 = np.zeros(S, bool)
        any5 = np.zeros(S, bool)
        any0 = np.zeros(S, bool)
        for b, (h1, h5, h0) in inf["hits"].items():
            if b in earlier:
                any1 |= h1
                any5 |= h5
                any0 |= h0
        loc = 0.6 * np.where(any1, 0.0, inf["edge"][0]) + 0.4 * np.where(any5, 0.0, inf["edge"][1])
        near = np.minimum.reduce([np.roll(loc, d) for d in (-1, 0, 1)])
        c = np.concatenate([[0.0], np.cumsum(np.concatenate([loc, loc]))])
        idx = np.arange(S) + S                                      # (the stretch before a place: the m samples in front of it)
        mean = (c[idx] - c[idx - m]) / m
        ok = (inf["band"] if band else inf["valid"]) & ~any0
        sup = np.where(ok, np.minimum(near, mean), -1.0)
        k = int(np.argmax(np.round(sup, 3) - 1e-4 * inf["gap"]))
        blockers = {b for b, (h1, h5, h0) in inf["hits"].items() if b in earlier
                    and ((h1 | h5)[(np.arange(k - m, k + 2)) % S].any() or h0[k])}
        return float(sup[k]), k, blockers

    def cheapest(self, key: str, earlier: set[str], band: bool = False) -> set[str]:
        """The fewest cuts that would have to come after ``key`` for some place to hold it fully (the zone deciding
        among equals); with ``band`` only the places that still count as the closing zone."""
        inf = self.info[key]
        S, m = inf["S"], inf["m"]
        cover = []
        names = [b for b in inf["hits"] if b in earlier]
        for b in names:
            h1, h5, h0 = inf["hits"][b]
            x = (h1 | h5)
            c = np.concatenate([[0], np.cumsum(np.concatenate([x, x, x]).astype(int))])
            idx = np.arange(S) + S
            cover.append(((c[idx + 2] - c[idx - m]) > 0) | h0)        # the stretch before a place and its neighbour, the plunge
        if not names:
            return set()
        M = np.array(cover)                                         # (cuts x places): does the cut take this place away
        ok = inf["band"] if band else inf["valid"]
        cost = np.where(ok, M.sum(axis=0), 10 ** 6)
        k = int(np.argmin(cost * 1000.0 + inf["gap"]))
        return {names[i] for i in range(len(names)) if M[i, k]}


def support_order(order: list[str], items: dict[str, SeqItem], smap: SupportMap, settle, sheet: BBox | None,
                  replayer: Replayer | None = None, tries: int = 6) -> tuple[list[str], list[str]]:
    """``(order, keys still not held)``: every cut that would not close held by sheet that is still there, in its
    closing zone (a neighbour that is cut before it takes the sheet away from every place of the zone), is brought in
    front of the fewest cuts that make a place of the zone hold it. What cannot be had in the zone is then asked
    anywhere along the cut. Pulls that would leave a cut in loose scrap are given up (:func:`zone_order`)."""
    def unheld(order: list[str], band: bool) -> dict[str, set[str]]:
        pos = {k: i for i, k in enumerate(order)}
        out: dict[str, set[str]] = {}
        for k in order:
            if k in smap.info:
                earlier = set(order[:pos[k]])
                if smap.best(k, earlier, band)[0] < FULL_SUPPORT:
                    out[k] = smap.cheapest(k, earlier, band)
        return out

    snaps: list = []
    if replayer is not None:
        replayer.first_loose(order, snaps)
    for band in (True, False):
        for _ in range(tries):
            wishes = {k: w for k, w in unheld(order, band).items() if w}
            if not wishes:
                break
            progressed = False
            if replayer is not None and len(wishes) > 1:        # all at once first (a few replays), then the rest
                fresh = []
                batch = zone_order(order, wishes, items, len(order), settle, sheet, tries=6,
                                   replayer=replayer, snaps=snaps, out_snaps=fresh)
                if batch != order:
                    order, snaps, progressed = batch, fresh, True
            for k in [k for k in order if k in wishes]:         # one cut at a time: a pull that leaves scrap loose is
                pos = {x: i for i, x in enumerate(order)}      # given up without the harmless ones going with it
                earlier = set(order[:pos[k]])
                if smap.best(k, earlier, band)[0] >= FULL_SUPPORT:
                    continue                                    # (an earlier pull has held it already)
                w = smap.cheapest(k, earlier, band)
                if not w:
                    continue
                fresh: list = []
                new = zone_order(order, {k: w}, items, len(order), settle, sheet if replayer is not None else None,
                                 replayer=replayer, snaps=snaps if replayer is not None else None, out_snaps=fresh)
                if new == order:
                    continue
                if replayer is not None:
                    snaps = fresh
                order = new
                progressed = True
            if not progressed:
                break
    if replayer is not None:
        # what is left: bring the cut in front of what takes its sheet away, and the cuts that would then be cut in loose
        # scrap in front of what closes it; kept when the cuts close nearer their zones than before (a cut that cannot be
        # held is worth a lot) and none gets worse
        def excess(o: list[str]) -> float:
            ps = {k: i for i, k in enumerate(o)}
            tot = 0.0
            for k in o:
                if k in smap.info:
                    sup, place, _ = smap.best(k, set(o[:ps[k]]), False)
                    inf = smap.info[k]
                    tot += 1000.0 if sup < FULL_SUPPORT else max(0.0, float(inf["gap"][place]) - inf["width"])
            return tot

        cur = excess(order)
        for _ in range(3):
            improved = False
            for band in (True, False):
                for k in list(unheld(order, band)):
                    pos = {x: i for i, x in enumerate(order)}
                    earlier = set(order[:pos[k]])
                    if smap.best(k, earlier, band)[0] >= FULL_SUPPORT:
                        continue
                    w = smap.cheapest(k, earlier, band)
                    if not w:
                        continue
                    pulled = settle(_pull(order, {k: w}, items))[0]
                    if pulled == order:
                        continue
                    mended, _, ok = mend_loose(pulled, items, replayer)
                    if not ok:
                        continue
                    now = (len(unheld(order, False)), len(unheld(order, True)))
                    new = (len(unheld(mended, False)), len(unheld(mended, True)))
                    ex = excess(mended)
                    if new[0] <= now[0] and ((new[1] <= now[1] and new != now) or ex < cur - 1e-6):
                        order, cur, improved = mended, ex, True
            if not improved:
                break
    return order, list(unheld(order, False))


def zone_blockers(items: list[SeqItem], cfg: dict, sheet: BBox | None = None) -> dict[str, set[str]]:
    """``{item key: keys of the items whose cut areas lie next to where its cut closes}``.

    A cut closes in its closing zone (:func:`startpoints.closing_zone`: a little above the bottom right
    corner for a sweep from bottom left to top right). What that stretch faces, and the stretch cut last
    before it, has to be sheet that is still there. The items listed are the ones that would take that
    away if they were cut first; the cut should be made before them. Only outlines are looked at: the
    inner cuts of a part are made while it is whole.
    """
    cand = [it for it in items if it.loop is not None and it.closed and it.frees and it.depth % 2 == 0
            and it.radius > 0]
    kerfed = [it for it in items if it.kerf]
    if not cand or not kerfed:
        return {}
    boxes = _bboxes([it.kerf for it in kerfed])
    by_key = {it.key: it for it in items}
    out: dict[str, set[str]] = {}
    for it in cand:
        loop = it.loop
        walker = PathWalker(loop.segs, True)
        L = walker.length
        if L <= 1e-6:
            continue
        r = it.radius
        corner = cfg.get("corner_distance") or max(2.0, r)
        cw = not loop.is_ccw()
        hold = hold_direction(cfg["strategy"], it.center, sheet)
        zone = closing_zone(walker, hold, cw, corner, corners(walker, tight=2 * r + 1.0))
        target = zone.target
        if it.fixed is not None:
            i, t, _, _ = loop.nearest(it.fixed)
            target = walker.cum[i] + t * (walker.cum[i + 1] - walker.cum[i])
        bridge = min(L / 3, 6 * r)
        span = bridge + corner                      # the stretch cut last (6 tool radii), then the close itself
        m = int(max(8, min(40, span / 2.0)))
        s_probe = (target - bridge + np.arange(m + 1) * span / m) % L
        P, T = sample(walker, s_probe)
        N = np.c_[-T[:, 1], T[:, 0]] if cw else np.c_[T[:, 1], -T[:, 0]]   # away from what the cut sets free
        Q = np.concatenate([P + N * (r + d) for d in ZONE_RINGS])
        near_close = np.tile(np.abs(np.arange(m + 1) * span / m - bridge) <= corner, len(ZONE_RINGS))
        lo, hi = Q.min(axis=0), Q.max(axis=0)
        anc = ancestors(it, by_key) | descendants(it.key, by_key)
        blockers: set[str] = set()
        for j in _near(boxes, (*lo, *hi), 0.01):
            o = kerfed[j]
            if o is it or o.key in anc:
                continue
            hit = clip.region_mask(Q, o.kerf)
            if hit[near_close].any() or hit.mean() >= 0.1:
                blockers.add(o.key)
        if blockers:
            out[it.key] = blockers
    return out


def apply_zones(order: list[str], blockers: dict[str, set[str]], items: dict[str, SeqItem],
                reach: int | None = None) -> list[str]:
    """Bring every cut in front of the neighbours that would be cut away next to its closing zone.

    The cut is pulled forward to just before the first of them (with what has to come before it: the
    inner cuts of the part and the cuts that in turn want to come before it), so the sweep stays as it
    was and only the neighbours trade places. A cut is only brought forward past ``reach`` places of the
    order (its own neighbourhood in the sweep, not from one row to another): further than that its start
    point goes where the sheet is held instead. When two cuts want to come before each other the order
    the strategy made is kept for them.
    """
    old = {k: i for i, k in enumerate(order)}
    reach = pull_reach(len(order)) if reach is None else reach
    edges = _acyclic({a: {b for b in bs if old.get(a, 0) - old.get(b, 0) <= reach}
                      for a, bs in blockers.items() if bs}, old)
    return _pull(order, edges, items) if edges else order


def touching(kerfs: list[tuple[str, list]], loose: list, grow: float = 0.3) -> list[str]:
    """Keys of the cut areas ``(key, polygons)`` that touch (or reach into) the loose material ``loose``: only the loose
    polygons next to a cut area are looked at, the loose material can be hundreds of polygons."""
    if not loose or not kerfs:
        return []
    boxes = _bboxes([[p] for p in loose])
    out = []
    for key, polys in kerfs:
        if not polys:
            continue
        a = np.asarray([q for poly in polys for q in poly])
        lo, hi = a.min(axis=0) - grow, a.max(axis=0) + grow
        near = _near(boxes, (*lo, *hi), 0.0)
        if not len(near):
            continue
        grown = clip.offset([loose[i] for i in near], grow)
        if clip.area(clip.boolean(polys, grown, "intersection")) > 1e-3:
            out.append(key)
    return out


def zone_order(order: list[str], blockers: dict[str, set[str]], items: dict[str, SeqItem], reach: int,
               settle, sheet: BBox | None = None, tries: int = 24, replayer: Replayer | None = None,
               snaps: list | None = None, out_snaps: list | None = None) -> list[str]:
    """:func:`apply_zones` that never leaves a cut in loose scrap (with a ``sheet``, the order is replayed).

    When the pulls leave a cut in scrap that the cuts around it have set loose, the cut that was brought in
    front of it from behind it and closes the scrap is the cause: it is left where the strategy had it, and
    the order is made again, until nothing is loose. ``replayer`` and ``snaps`` (the cut areas before every cut
    of ``order``, see :meth:`Replayer.first_loose`) save replaying what the pulls leave as it was."""
    base = {k: i for i, k in enumerate(order)}
    edges = _acyclic({a: {b for b in bs if base.get(a, 0) - base.get(b, 0) <= reach}
                      for a, bs in blockers.items() if bs}, base)
    rp = replayer if replayer is not None else (Replayer(items, sheet) if sheet is not None else None)
    for _ in range(tries):
        if not edges:
            return order
        zoned = _pull(order, edges, items)
        if zoned == order:
            return order
        zoned = settle(zoned)[0]
        if rp is None:
            return zoned
        d = _first_difference(order, zoned)
        fresh = list(snaps[:d + 1]) if snaps else []
        found = rp.first_loose(zoned, fresh, d)
        if found is None:
            if out_snaps is not None:
                out_snaps[:] = fresh
            return zoned
        i, loose = found
        x = zoned[i]
        new = {k: j for j, k in enumerate(zoned)}
        culprits = {zoned[j] for j in range(i) if base.get(zoned[j], -1) > base.get(x, -1)}   # brought in front of it
        if loose:
            culprits = set(touching([(k, items[k].kerf) for k in culprits if items[k].kerf], loose))
        if not culprits:
            return order
        last = max(culprits, key=new.get)         # the one that closes the ring: without it the scrap is not loose yet
        edges = {a: bs for a, bs in edges.items() if a != last}
    return order


def pull_reach(n: int) -> int:
    """How many places earlier than the strategy put it a cut may be brought for its closing zone."""
    return max(6, min(25, round(0.35 * n)))


def _acyclic(edges: dict[str, set[str]], pos: dict[str, int]) -> dict[str, set[str]]:
    """The wishes "a before b" that can all be had at once. When some contradict each other (a wants to be
    before b, b before a) the ones the strategy's order already fulfils win, then the smallest changes."""
    wishes = sorted(((a, b) for a, bs in edges.items() for b in bs if a in pos and b in pos),
                    key=lambda ab: (pos[ab[0]] > pos[ab[1]], abs(pos[ab[0]] - pos[ab[1]])))
    out: dict[str, set[str]] = defaultdict(set)

    def reaches(src: str, dst: str) -> bool:
        seen, stack = {src}, [src]
        while stack:
            for nxt in out.get(stack.pop(), ()):
                if nxt == dst:
                    return True
                if nxt not in seen:
                    seen.add(nxt)
                    stack.append(nxt)
        return False

    for a, b in wishes:
        if a != b and not reaches(b, a):
            out[a].add(b)
    return dict(out)


def _pull(order: list[str], edges: dict[str, set[str]], items: dict[str, SeqItem]) -> list[str]:
    """``order`` with every owner placed before the items it wants to precede (stable otherwise)."""
    owners: dict[str, list[str]] = defaultdict(list)
    for a, bs in edges.items():
        for b in bs:
            owners[b].append(a)
    pos = {k: i for i, k in enumerate(order)}
    kids: dict[str, list[str]] = defaultdict(list)
    for k in order:
        p = items[k].parent
        if p is not None and p in items:
            kids[p].append(k)
    out: list[str] = []
    placed: set[str] = set()
    active: set[str] = set()

    def place(k: str) -> None:
        if k in placed or k in active:
            return
        active.add(k)
        for c in kids.get(k, []):        # the inner cuts first
            place(c)
        for a in sorted(owners.get(k, []), key=lambda x: pos.get(x, 0)):   # who wants to be cut before this one
            place(a)
        active.discard(k)
        placed.add(k)
        out.append(k)

    for k in order:
        place(k)
    return out


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


def _lean(paths: list, tol: int) -> list:
    """Integer paths with the points that lie within ``tol`` of the line between their neighbours taken out."""
    out = []
    for q in paths:
        c = clip.pyclipper.CleanPolygon(q, tol)
        if len(c) >= 3:
            out.append(c)
    return out


class Replayer:
    """Replays cut orders of one set of items cut by cut and says whether a cut would run in material the earlier
    cuts have set loose (:func:`verify_support` worked out faster: the cut areas stay integer paths between the
    cuts, simplified by 0.05 mm). It remembers the cut areas before every cut, so an order that only changes late
    in the sequence is replayed from there. Sheets of any other shape than a rectangle are judged by
    :func:`verify_support`, which gives no paths of the loose material."""

    def __init__(self, items: dict[str, SeqItem], sheet: BBox | None = None):
        self.items, self.sheet = items, sheet
        all_ = list(items.values())
        self.nothing = not any(it.kerf and it.frees for it in all_)    # (nothing is cut free: nothing can be loose)
        self.slow = False
        if self.nothing:
            return
        frame = _frame(all_, sheet)
        if frame.inner is not None:
            self.slow = True
            return
        f = frame.bb
        if not all(math.isfinite(v) for v in (f.xmin, f.ymin, f.xmax, f.ymax)):
            self.nothing = True                    # (no sheet and no cut areas to go by)
            return
        self.base = clip._clean(frame.base)
        border = round(1.0 * clip.SCALE)
        self.lo = (round(f.xmin * clip.SCALE) + border, round(f.ymin * clip.SCALE) + border)
        self.hi = (round(f.xmax * clip.SCALE) - border, round(f.ymax * clip.SCALE) - border)
        tol = round(0.3 * clip.SCALE)             # (the cut areas are simplified by 0.3 mm: a third of the points, the same answer)
        self.kerf = {it.key: _lean(clip._clean(it.kerf), tol) for it in all_ if it.kerf}
        self.solids = {it.key: _lean(clip._clean(it.solid), tol) for it in all_ if it.solid}

    def loose_for(self, it: SeqItem, union: list):
        """The loose material (integer paths) that the cut of ``it`` would run in when the cut areas ``union`` have
        been cut before it, or ``None``."""
        if it.key not in self.kerf or not union:
            return None
        pc_ = clip.pyclipper
        lo_x, lo_y = self.lo
        hi_x, hi_y = self.hi
        pc = clip.Clipper()
        pc.AddPaths(self.base, pc_.PT_SUBJECT, True)
        pc.AddPaths(union, pc_.PT_CLIP, True)
        tree = pc.Execute2(pc_.CT_DIFFERENCE, pc_.PFT_NONZERO, pc_.PFT_NONZERO)
        loose: list = []

        def walk(node):
            for ch in node.Childs:
                if not ch.IsHole:
                    xs = [q[0] for q in ch.Contour]
                    ys = [q[1] for q in ch.Contour]
                    if not (min(xs) <= lo_x or max(xs) >= hi_x or min(ys) <= lo_y or max(ys) >= hi_y):
                        loose.append(ch.Contour)
                        loose.extend(h.Contour for h in ch.Childs)
                for h in ch.Childs:
                    walk(h)

        walk(tree)
        if not loose:
            return None
        mine = ancestors(it, self.items)
        others = [q for k, paths in self.solids.items() if k != it.key and k not in mine for q in paths]
        pc = clip.Clipper()
        pc.AddPaths(self.kerf[it.key], pc_.PT_SUBJECT, True)
        pc.AddPaths(loose, pc_.PT_CLIP, True)
        hit = pc.Execute(pc_.CT_INTERSECTION, pc_.PFT_NONZERO, pc_.PFT_NONZERO)
        if others and hit:
            pc = clip.Clipper()
            pc.AddPaths(hit, pc_.PT_SUBJECT, True)
            pc.AddPaths(others, pc_.PT_CLIP, True)
            hit = pc.Execute(pc_.CT_DIFFERENCE, pc_.PFT_NONZERO, pc_.PFT_NONZERO)
        if abs(sum(pc_.Area(q) for q in hit)) / (clip.SCALE * clip.SCALE) > 0.05:
            return loose
        return None

    def first_loose(self, order: list[str], snaps: list | None = None, valid: int = 0):
        """``(index, loose paths)`` of the first cut of ``order`` that would run in loose material, or ``None``.

        ``snaps`` (a list, filled in) keeps the cut areas before every cut; those up to index ``valid`` are
        taken as they are (the order is the same up to there), the rest is worked out again."""
        if self.nothing:
            return None
        if self.slow:
            bad = verify_support(order, self.items, self.sheet)
            return (bad[0], []) if bad else None
        pc_ = clip.pyclipper
        snaps = snaps if snaps is not None else []
        if not snaps:
            snaps.append([])
            valid = 0
        valid = min(valid, len(snaps) - 1)
        del snaps[valid + 1:]
        union = snaps[valid]
        for i in range(valid, len(order)):
            if i > valid:
                snaps.append(union)
            it = self.items[order[i]]
            loose = self.loose_for(it, union)
            if loose is not None:
                return i, [clip.from_int(q) for q in loose]
            if it.frees and it.key in self.kerf:
                pc = clip.Clipper()
                pc.AddPaths(union + self.kerf[it.key], pc_.PT_SUBJECT, True)
                union = pc.Execute(pc_.CT_UNION, pc_.PFT_NONZERO, pc_.PFT_NONZERO)
        return None


def first_loose(order: list[str], items: dict[str, SeqItem], sheet: BBox | None = None):
    """``(index, loose paths)`` of the first cut of ``order`` in loose material, or ``None`` (see :class:`Replayer`)."""
    return Replayer(items, sheet).first_loose([k for k in order if k in items])


def is_supported(order: list[str], items: dict[str, SeqItem], sheet: BBox | None = None) -> bool:
    """Whether no cut of ``order`` runs in loose material (see :func:`first_loose`)."""
    return first_loose(order, items, sheet) is None


def _first_difference(a: list[str], b: list[str]) -> int:
    for i, (x, y) in enumerate(zip(a, b)):
        if x != y:
            return i
    return min(len(a), len(b))


def mend_loose(order: list[str], items: dict[str, SeqItem], rp: Replayer, tries: int = 30
               ) -> tuple[list[str], list[str], list]:
    """``(order, keys moved, cut areas before every cut)``: a cut that would run in scrap earlier cuts have set loose
    is brought in front of the last of the cuts that close the scrap round it (with what lies inside it), until
    nothing is loose. Gives up (and keeps what it has) when a cut cannot be helped: then the third part is empty."""
    moved: list[str] = []
    snaps: list = []
    valid = 0
    for _ in range(tries):
        found = rp.first_loose(order, snaps, valid)
        if found is None:
            return order, moved, snaps
        i, loose = found
        x = order[i]
        block_keys = {x} | descendants(x, items)
        walls = set(touching([(order[j], items[order[j]].kerf) for j in range(i)
                              if order[j] not in block_keys and items[order[j]].kerf], loose))
        if not walls:
            break
        last = max(j for j in range(i) if order[j] in walls)
        if not rp.slow and len(snaps) > i:
            # the latest place in front of it where its cut is not in loose material (the cut areas before every place
            # are known): in front of the cut that closes the scrap, or earlier when that is loose still
            while last > 0 and rp.loose_for(items[x], snaps[last]) is not None:
                last -= 1
        block = [k for k in order if k in block_keys]
        rest = [k for k in order if k not in block_keys]
        at = rest.index(order[last])
        new = rest[:at] + block + rest[at:]
        new, _ = repair(new, items)
        if new == order:
            break
        valid = _first_difference(order, new)
        order = new
        moved.append(x)
    return order, moved, []


def fix_loose(order: list[str], items: dict[str, SeqItem], sheet: BBox | None = None,
              tries: int = 30) -> tuple[list[str], list[str]]:
    """``(order, keys moved)`` with nothing loose where that can be had (see :func:`mend_loose`)."""
    new, moved, _ = mend_loose(order, items, Replayer(items, sheet), tries)
    return new, moved


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

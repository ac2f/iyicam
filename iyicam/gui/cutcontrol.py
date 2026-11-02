"""Looking at and editing the cut order, the cutting directions and the start points of a document.

Part of :class:`~iyicam.gui.controller.Controller`. A *preview* calculates a toolpath with other settings
without changing anything: the drawing shows what it would be, and applying it makes it so.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from ..cam import cutinfo
from ..cam.job import ComputeCancelled, ComputeResult, compute_all
from ..core.geom import Contour, Pt, dist
from .busy import run_busy


@dataclass
class CutPreview:
    """What a toolpath would be with other cut order settings, and what that changes."""
    tid: str
    label: str
    changes: dict = field(default_factory=dict)       # sequence settings that would change
    smart_starts: bool = False                          # the start points would be moved to the best places
    results: dict = field(default_factory=dict)         # (toolpath id, sheet id) -> ComputeResult
    cuts: list = field(default_factory=list)            # the cuts on the sheet that is open, in the new order
    order_before: list = field(default_factory=list)
    order_after: list = field(default_factory=list)
    route_before: float = 0.0                           # mm the tool travels between the cuts, now ...
    route_after: float = 0.0                            # ... and with the preview
    starts: dict = field(default_factory=dict)          # key -> (start point now, start point of the preview)
    directions: int = 0                                 # cuts that would be cut the other way round

    @property
    def reordered(self) -> int:
        """How many cuts would be cut at another place of the order."""
        return sum(1 for a, b in zip(self.order_before, self.order_after) if a != b) + abs(
            len(self.order_before) - len(self.order_after))

    @property
    def changed(self) -> bool:
        return bool(self.reordered or self.starts or self.directions or abs(self.route_after - self.route_before) > 1e-6)

    def summary(self, fmt=lambda v: f"{v:.0f} mm") -> str:
        parts = []
        if self.reordered or any(k in self.changes for k in ("order", "strategy", "grouping", "band")):
            parts.append(f"{self.reordered} cut(s) at another place of the order")
            if self.route_before > 1e-6:
                delta = (self.route_after - self.route_before) / self.route_before * 100
                parts.append(f"travel between the cuts {fmt(self.route_before)} → {fmt(self.route_after)} "
                             f"({delta:+.0f} %)")
        if self.directions or any(k in self.changes for k in ("direction", "directions")):
            parts.append(f"{self.directions} cut(s) cut the other way round")
        if self.smart_starts:
            parts.append(f"{len(self.starts)} start point(s) would move")
        return "; ".join(parts) if parts else "nothing would change"


PLACES = {"sw": "bottom left corner", "se": "bottom right corner", "nw": "top left corner", "ne": "top right corner",
          "s": "bottom", "n": "top", "w": "left", "e": "right"}


def place_name(place: str) -> str:
    return PLACES.get(place, place)


def place_point(c: Contour, place: str) -> Pt | None:
    """The point of a closed contour for a place: its extreme point towards a corner (``sw``, ``se``, ``nw``, ``ne``) or
    the point of a side's middle (``s``, ``n``, ``w``, ``e``)."""
    bb = c.bbox()
    dirs = {"sw": (-1, -1), "se": (1, -1), "nw": (-1, 1), "ne": (1, 1)}
    if place in dirs:
        dx, dy = dirs[place]
        pts = c.nodes() + c.flatten(0.05)
        return max(pts, key=lambda p: (dx * p[0] + dy * p[1], -dist(p, bb.center) * 1e-9))
    mid = {"s": (bb.center[0], bb.ymin), "n": (bb.center[0], bb.ymax),
           "w": (bb.xmin, bb.center[1]), "e": (bb.xmax, bb.center[1])}.get(place)
    return None if mid is None else c.nearest(mid)[2]


class CutControl:
    """Mixin of the controller: previews of cut order settings, the cut that is looked at and start point edits."""

    # -- what is calculated ---------------------------------------------------------------------------
    def cuts_of(self, tid: str, preview: bool = False) -> list[dict]:
        """The cuts of a toolpath on the open sheet in machining order, from the calculation (or the preview)."""
        t = self.project.toolpath(tid)
        if t is None:
            return []
        if preview and self.cut_preview is not None and self.cut_preview.tid == tid:
            return list(self.cut_preview.cuts)
        r = self.results.get((tid, self.sheet_id))
        return cutinfo.real_cuts([c for tp in (r.toolpaths if r else []) for c in tp.cuts])

    def route_home(self) -> Pt:
        return tuple(self.project.material.home[:2])

    def route_length(self, tid: str) -> float:
        return cutinfo.route_length(self.cuts_of(tid), self.route_home())

    # -- the cut that is looked at ---------------------------------------------------------------------
    def set_focus_cut(self, key: str | None) -> None:
        if key != self.focus_cut:
            self.focus_cut = key
            self.cut_view_changed.emit()

    # -- previews ---------------------------------------------------------------------------------------
    def preview_cut_order(self, tid: str, changes: dict | None = None, smart_starts: bool = False,
                          label: str = "") -> CutPreview | None:
        """Calculate toolpath ``tid`` with ``changes`` of its cut order settings (and the start points at the best
        places, ``smart_starts``) without touching the project: the result is :attr:`cut_preview`, which the
        drawing shows until it is applied or cleared."""
        t = self.project.toolpath(tid)
        if t is None or self._computing:
            return None
        changes = dict(changes or {})
        before = self.cuts_of(tid)
        old = t.sequence
        t.sequence = {**old, **changes}
        state: dict = {}

        def progress(k, n, name):
            state["k"], state["n"], state["label"] = k, n, f"Calculating the preview of {name}..."

        self._computing = True
        try:
            res = run_busy(lambda: compute_all(self.project, self.registry, {tid}, self.results,
                                               {tid} if smart_starts else None,
                                               cancel=lambda: state["cancel"].is_set(), progress=progress,
                                               events=[]),
                           self._busy_window(), "Calculating the preview...", ComputeCancelled, state=state)
        except ComputeCancelled:
            self.message.emit("Preview cancelled")
            return None
        finally:
            t.sequence = old
            self._computing = False
        r: ComputeResult | None = res.get((tid, self.sheet_id))
        after = cutinfo.real_cuts([c for tp in (r.toolpaths if r else []) for c in tp.cuts])
        p = CutPreview(tid, label or "Preview", changes, smart_starts, res, after,
                       [c["key"] for c in before], [c["key"] for c in after],
                       cutinfo.route_length(before, self.route_home()), cutinfo.route_length(after, self.route_home()))
        was = {c["key"]: cutinfo.cut_direction(c) for c in before}
        p.directions = sum(1 for c in after if c["key"] in was and was[c["key"]] != cutinfo.cut_direction(c))
        if smart_starts and r is not None:
            for key, q in r.start_updates.items():
                vid, ci = key.rsplit(":", 1)
                o = self.find_vector(vid)
                if o is not None and int(ci) < len(o.contours) and int(ci) not in o.locked_starts:
                    if dist(o.contours[int(ci)].start, q) > 1e-3:
                        p.starts[key] = (tuple(o.contours[int(ci)].start), tuple(q))
        self.cut_preview = p
        self.cut_view_changed.emit()
        return p

    def clear_cut_preview(self) -> None:
        if self.cut_preview is not None:
            self.cut_preview = None
            self.cut_view_changed.emit()

    def apply_cut_preview(self) -> bool:
        """Make the preview real (one undo step for the settings, one for the moved start points)."""
        p = self.cut_preview
        if p is None:
            return False
        self.cut_preview = None
        self.cut_view_changed.emit()
        if self.project.toolpath(p.tid) is None:
            return False
        if p.changes:
            self.set_sequence(p.tid, **p.changes)
            self.remember_cut_order(**p.changes)
        if p.smart_starts:
            self.optimise_starts({p.tid})
        return True

    # -- start points ---------------------------------------------------------------------------------------
    def contour_of(self, key: str) -> tuple[object, int, Contour] | None:
        vid, ci = key.rsplit(":", 1)
        o = self.find_vector(vid)
        if o is None or not 0 <= int(ci) < len(o.contours):
            return None
        return o, int(ci), o.contours[int(ci)]

    def set_start_points(self, items, lock: bool = True, label: str = "Set start point") -> int:
        """Start several contours at once, ``items`` being ``(vector id, contour index, point)``: one undo step and one
        calculation for all of them (nothing is done, and no undo step made, for starts that are there already and
        kept). How many contours now start where they were asked to."""
        from ..core import vectorops
        valid, todo = 0, []
        for vid, ci, point in items:
            o = self.find_vector(vid)
            if o is None or not 0 <= ci < len(o.contours) or not o.contours[ci].closed or not o.contours[ci].segs:
                continue
            valid += 1
            if dist(o.contours[ci].start, point) > 1e-6 or (lock and ci not in o.locked_starts):
                todo.append((o, ci, point))
        if todo:
            with self.edit(label):
                for o, ci, point in todo:
                    o.contours[ci] = vectorops.set_start_point(o.contours[ci], point)
                    if lock and ci not in o.locked_starts:
                        o.locked_starts.append(ci)
            if self.stale:
                self.recalculate()
        return valid

    def move_start(self, key: str, delta: float) -> bool:
        """Move the start point of a closed vector ``delta`` mm along it (negative: backwards) and keep it there.
        The cut order does not change: it is kept by vector, not by place."""
        got = self.contour_of(key)
        if got is None or not got[2].closed:
            return False
        o, ci, c = got
        L = c.length()
        if L <= 1e-9 or abs(delta) < 1e-9:
            return False
        self.edit_start(o.id, ci, c.point_at_length(delta % L))
        return True

    def _closed_keys(self, keys) -> list[tuple[object, int, Contour]]:
        out = []
        for key in keys:
            got = self.contour_of(key)
            if got is not None and got[2].closed and got[2].segs:
                out.append(got)
        return out

    def start_at_place(self, key: str, place: str) -> bool:
        """Start a closed vector at one of the corners (``sw``, ``se``, ``nw``, ``ne``: its extreme point that way) or
        sides (``s``, ``e``, ``n``, ``w``: the middle of that side) and keep it there."""
        return bool(self.starts_at_place([key], place))

    def starts_at_place(self, keys, place: str) -> int:
        """:meth:`start_at_place` for many contours as one change (one undo step, one calculation)."""
        items = []
        for o, ci, c in self._closed_keys(keys):
            q = place_point(c, place)
            if q is not None:
                items.append((o.id, ci, q))
        return self.set_start_points(items, label="Start point at the " + place_name(place))

    def step_starts(self, keys, direction: int = 1) -> int:
        """Start the contours at their next (``direction`` 1) or previous (-1) corner or arc extreme: one step along
        or back, as one change."""
        from ..core import vectorops
        items = []
        for o, ci, c in self._closed_keys(keys):
            q = vectorops.step_start(c, direction)
            if q is not None:
                items.append((o.id, ci, q))
        return self.set_start_points(items, label="Start point at the " + ("next" if direction > 0 else "previous") + " node")

    def start_opposite(self, keys) -> int:
        """Start the contours half way round from where they start now."""
        items = [(o.id, ci, c.point_at_length(c.length() / 2)) for o, ci, c in self._closed_keys(keys)
                 if c.length() > 1e-9]
        return self.set_start_points(items, label="Start point on the opposite side")

    def edit_start(self, vid: str, ci: int, point: Pt, lock: bool = True) -> None:
        """Start a contour at ``point`` and calculate the toolpaths again at once (what the drawing shows)."""
        self.set_start_point(vid, ci, point, lock)
        if self.stale:
            self.recalculate()

    def smart_start_one(self, tid: str, key: str) -> bool:
        """Let smart start points choose the start of one cut (it stops being a hand-set one). Whether it moved."""
        got = self.contour_of(key)
        if got is None or not got[2].closed:
            return False
        o, ci, c = got
        if ci in o.locked_starts:
            with self.edit("Release start point", geometry=False):
                o.locked_starts.remove(ci)
        p = self.preview_cut_order(tid, smart_starts=True, label="Smart start point")
        self.cut_preview = None
        self.cut_view_changed.emit()
        if p is None or key not in p.starts:
            self.stale = True
            self.recalculate({tid})
            return False
        self.edit_start(o.id, ci, p.starts[key][1], lock=False)
        return True

    def unlock_start(self, key: str) -> bool:
        """Give the start point of one vector back to smart start points."""
        got = self.contour_of(key)
        if got is None or got[1] not in got[0].locked_starts:
            return False
        o, ci, _ = got
        with self.edit("Release start point", geometry=False):
            o.locked_starts.remove(ci)
        self.stale = True
        self.recalculate()
        return True

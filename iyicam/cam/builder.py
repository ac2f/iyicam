"""Helper that accumulates moves and handles retracts, rapids and plunges."""
from __future__ import annotations

from ..core.geom import Segment
from .moves import ArcMove, Comment, Feed, Rapid
from .pathutil import emit_segment


class MoveBuilder:
    def __init__(self, safe_z: float, clearance: float, feed: float, plunge: float,
                 surface: float = 0.0):
        self.safe_z = safe_z
        self.clearance = clearance
        self.feed_rate = feed
        self.plunge_rate = plunge
        self.surface = surface
        self.moves: list = []
        self.pos: tuple[float, float, float] | None = None

    # -- primitives --------------------------------------------------------------
    def rapid(self, x: float, y: float, z: float) -> None:
        if self.pos is not None and _same3(self.pos, (x, y, z)):
            return
        self.moves.append(Rapid(x, y, z))
        self.pos = (x, y, z)

    def feed(self, x: float, y: float, z: float, f: float | None = None) -> None:
        if self.pos is not None and _same3(self.pos, (x, y, z)):
            return
        self.moves.append(Feed(x, y, z, f or self.feed_rate))
        self.pos = (x, y, z)

    def arc(self, x: float, y: float, z: float, cx: float, cy: float, cw: bool, f: float | None = None) -> None:
        self.moves.append(ArcMove(x, y, z, cx, cy, cw, f or self.feed_rate))
        self.pos = (x, y, z)

    def segment(self, s: Segment, z: float, f: float | None = None) -> None:
        self.pos = emit_segment(self.moves, s, z, f or self.feed_rate, self.pos or (s.a[0], s.a[1], z))

    def comment(self, text: str) -> None:
        self.moves.append(Comment(text))

    # -- compound moves ------------------------------------------------------------
    def retract(self) -> None:
        if self.pos is not None and self.pos[2] < self.safe_z - 1e-9:
            self.rapid(self.pos[0], self.pos[1], self.safe_z)

    def plunge(self, z: float) -> None:
        x, y, cz = self.pos
        if z < cz - 1e-9:
            self.feed(x, y, z, self.plunge_rate)
        elif z > cz + 1e-9:
            self.rapid(x, y, z)

    def travel(self, x: float, y: float, z: float, floor: float | None = None) -> None:
        """Move to ``(x, y)`` and plunge to ``z``.

        The tool retracts to safe Z, rapids over, rapids down to ``clearance``
        above ``floor`` (default: the surface) and feeds the remaining depth.
        """
        top = (self.surface if floor is None else floor) + self.clearance
        if self.pos is None:
            self.rapid(x, y, self.safe_z)
        elif abs(self.pos[0] - x) > 1e-9 or abs(self.pos[1] - y) > 1e-9:
            self.retract()
            self.rapid(x, y, self.safe_z)
        if self.pos[2] > top + 1e-9 and z < top:
            self.rapid(x, y, top)
        self.plunge(z)

    def hop(self, x: float, y: float, z: float, floor: float | None = None) -> None:
        """Short re-positioning: lift only to ``clearance`` above the surface (not safe Z),
        then rapid down to ``clearance`` above ``floor`` (already cut depth) and plunge."""
        if self.pos is None:
            self.travel(x, y, z, floor)
            return
        lift = min(self.safe_z, self.surface + self.clearance)
        if self.pos[2] < lift:
            self.rapid(self.pos[0], self.pos[1], lift)
        self.rapid(x, y, self.pos[2])
        top = (self.surface if floor is None else floor) + self.clearance
        if self.pos[2] > top + 1e-9 and z < top:
            self.rapid(x, y, top)
        self.plunge(z)

    def finish(self) -> list:
        self.retract()
        return self.moves


def _same3(a, b, tol: float = 1e-9) -> bool:
    return abs(a[0] - b[0]) <= tol and abs(a[1] - b[1]) <= tol and abs(a[2] - b[2]) <= tol

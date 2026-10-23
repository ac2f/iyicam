"""Pocketing / area clearance with offset or raster patterns, islands and clean-up."""
from __future__ import annotations

import math
from dataclasses import dataclass

from ..core import arcfit, clip, vectorops
from ..core.geom import Contour, Pt, Transform, add, dist, mul, perp, polar
from ..core.params import Param
from .builder import MoveBuilder
from .moves import Toolpath
from .operation import OpContext, Operation, depth_levels, depth_params, final_depth, stepdown
from .pathutil import PathWalker, emit_profile


@dataclass
class PatternPath:
    contour: Contour
    group: int = 0        # paths are cut group by group, nearest neighbour inside a group
    linked: bool = False  # True: reached from the previous path by feeding at depth


def segment_inside(p: Pt, q: Pt, polys: list[clip.Poly], tol: float = 1e-3) -> bool:
    """True when the straight move p-q stays inside the region ``polys``."""
    L = dist(p, q)
    if L < 1e-9:
        return True
    parts = clip.clip_lines([[p, q]], polys)
    if len(parts) != 1:
        return False
    a, b = parts[0][0], parts[0][-1]
    return abs(dist(a, b) - L) <= tol


def circle_inside(c: Pt, r: float, polys: list[clip.Poly]) -> bool:
    circ = [polar(c, r, 2 * math.pi * i / 48) for i in range(48)]
    if not clip.point_in_region(c, polys):
        return False
    return clip.area(clip.boolean([circ], polys, "difference")) < 1e-6


class PocketOp(Operation):
    id = "pocket"
    label = "Pocket (Area Clearance)"
    description = "Clear the area inside closed vectors; inner vectors become islands."
    tool_types = ("end_mill", "ball_nose", "bull_nose", "v_bit", "engraving")
    needs_closed = True

    @classmethod
    def params(cls) -> list[Param]:
        return depth_params(5.0) + [
            Param("strategy", "Strategy", "choice", "offset",
                  choices=[("offset", "Offset (contour parallel)"), ("raster", "Raster (zig-zag)")],
                  group="Pocket"),
            Param("stepover", "Stepover (0 = tool)", "length", 0.0, min=0.0, group="Pocket"),
            Param("direction", "Direction", "choice", "climb",
                  choices=[("climb", "Climb"), ("conventional", "Conventional")], group="Pocket"),
            Param("allowance", "Wall allowance", "length", 0.0, group="Pocket"),
            Param("finish_wall", "Wall finish allowance", "length", 0.0, min=0.0, group="Pocket",
                  help="Leave this much on the wall and remove it with a final full depth pass"),
            Param("order", "Cut order", "choice", "inside_out",
                  choices=[("inside_out", "From the centre outwards"), ("outside_in", "From the wall inwards")],
                  group="Pocket", visible={"strategy": "offset"}),
            Param("raster_angle", "Raster angle", "angle", 0.0, group="Pocket", visible={"strategy": "raster"}),
            Param("profile_pass", "Boundary pass", "choice", "last",
                  choices=[("none", "None"), ("first", "Before raster"), ("last", "After raster")],
                  group="Pocket", visible={"strategy": "raster"}),
            Param("entry", "Entry", "choice", "plunge",
                  choices=[("plunge", "Plunge"), ("ramp", "Zig-zag ramp"), ("helix", "Helix")], group="Entry"),
            Param("ramp_length", "Ramp distance", "length", 15.0, min=0.5, group="Entry",
                  visible={"entry": "ramp"}),
            Param("helix_radius", "Helix radius (0 = auto)", "length", 0.0, min=0.0, group="Entry",
                  visible={"entry": "helix"}),
            Param("ramp_angle", "Helix angle", "angle", 3.0, min=0.2, max=45.0, group="Entry",
                  visible={"entry": "helix"}),
        ]

    # ---------------------------------------------------------------------------
    def compute(self, ctx: OpContext) -> list[Toolpath]:
        v = self.values
        tool = ctx.tool
        tol = ctx.tolerance
        closed = [c for c in ctx.contours if c.closed]
        if len(closed) < len(ctx.contours):
            ctx.warn("Open vectors are ignored by pockets")
        if not closed:
            ctx.warn("Select closed vectors for pocketing")
            return []
        start = v["start_depth"]
        final = final_depth(self, ctx)
        levels = depth_levels(start, final, stepdown(self, ctx))
        if not levels:
            ctx.warn("Cut depth must be deeper than the start depth")
            return []
        r = tool.radius_at_depth(final - start) if tool.is_tapered else tool.radius
        self._r = r
        so = v["stepover"] if v["stepover"] > 0 else tool.stepover
        so = max(0.01, min(so, 2 * r * 0.98))
        region = vectorops.region(closed, tol / 2)
        fin = v["finish_wall"] or 0.0
        inner = clip.offset(region, -(r + v["allowance"] + fin), arc_tol=tol / 2)
        if not inner:
            ctx.warn("The tool does not fit inside the selected vectors")
            return []
        if v["strategy"] == "raster":
            pattern = self._raster(inner, so, r, tol)
        else:
            pattern = self._offset(inner, so, r, tol)
        pattern = self._add_cleanup(pattern, inner, r, tol)
        if v["direction"] == "conventional":
            pattern = [PatternPath(p.contour.reversed() if p.contour.closed else p.contour, p.group)
                       for p in pattern]
        pattern = self._link(pattern, inner, so, r, ctx.material.home[:2])

        surface = -start
        mb = MoveBuilder(ctx.safe_z, ctx.clearance, tool.feed_rate, tool.plunge_rate, surface)
        floor = surface
        for z in levels:
            for k, pp in enumerate(pattern):
                c = pp.contour
                if pp.linked and k > 0:
                    mb.feed(c.start[0], c.start[1], z)
                else:
                    self._enter(mb, c, z, floor, inner)
                self._cut(mb, c, z)
            floor = z
        if fin > 0:
            walls = clip.offset(region, -(r + v["allowance"]), arc_tol=tol / 2)
            for p in walls:
                c = arcfit.fit_contour(p, True, tol)
                if v["direction"] == "conventional":
                    c = c.reversed()
                if mb.pos is not None:
                    c = vectorops.set_start_point(c, mb.pos[:2])
                mb.hop(c.start[0], c.start[1], levels[-1])
                self._cut(mb, c, levels[-1])
        return [self.make_toolpath(ctx, mb.finish())]

    # -- patterns -------------------------------------------------------------------
    def _offset(self, inner: list[clip.Poly], so: float, r: float, tol: float) -> list[PatternPath]:
        rings: list[tuple[int, Contour]] = []
        cur = inner
        lvl = 0
        while cur and lvl < 10000:
            for p in cur:
                if len(p) >= 3:
                    rings.append((lvl, arcfit.fit_contour(p, True, tol)))
            cur = clip.offset(cur, -so, arc_tol=tol / 2)
            lvl += 1
        top = max((l for l, _ in rings), default=0)
        inside_out = self.values["order"] == "inside_out"
        return [PatternPath(c, top - l if inside_out else l) for l, c in rings]

    def _raster(self, inner: list[clip.Poly], so: float, r: float, tol: float) -> list[PatternPath]:
        ang = math.radians(self.values["raster_angle"])
        rot = Transform.rotate(-ang)
        back = Transform.rotate(ang)
        rpolys = [[rot.apply(p) for p in poly] for poly in inner]
        xs = [p[0] for poly in rpolys for p in poly]
        ys = [p[1] for poly in rpolys for p in poly]
        y0, y1 = min(ys), max(ys)
        x0, x1 = min(xs) - 1, max(xs) + 1
        n = max(1, math.ceil((y1 - y0) / so))
        step = (y1 - y0) / n
        lines = [[(x0, y0 + step * (k + 0.5)), (x1, y0 + step * (k + 0.5))] for k in range(n)]
        if n == 1:
            lines = [[(x0, (y0 + y1) / 2), (x1, (y0 + y1) / 2)]]
        segs = clip.clip_lines(lines, rpolys)
        paths = []
        for s in segs:
            pts = [back.apply(p) for p in s]
            if len(pts) >= 2 and dist(pts[0], pts[-1]) > 1e-6:
                paths.append(PatternPath(Contour.polyline([pts[0], pts[-1]], False)))
        mode = self.values["profile_pass"]
        if mode == "none":
            return paths
        bounds = [PatternPath(arcfit.fit_contour(p, True, tol), -1 if mode == "first" else 1) for p in inner]
        return bounds + paths if mode == "first" else paths + bounds

    def _add_cleanup(self, pattern: list[PatternPath], inner: list[clip.Poly], r: float,
                     tol: float) -> list[PatternPath]:
        """Find material the pattern misses and add loops around those slivers."""
        closed = [p.contour.flatten(tol) for p in pattern if p.contour.closed]
        opened = [p.contour.flatten(tol) for p in pattern if not p.contour.closed]
        covered = clip.offset_lines(closed, r * 0.999, join="round", closed=True) if closed else []
        if opened:
            covered += clip.offset_lines(opened, r * 0.999, end="round")
        covered = clip.union(covered)
        reach = clip.offset(inner, r * 0.999)
        missing = clip.boolean(reach, covered, "difference")
        extra = []
        for poly in missing:
            if abs(clip.area([poly])) < max(1e-3, (r * 0.05) ** 2):
                continue
            core = clip.offset([poly], -min(r * 0.02, 0.01))
            for cpoly in core:
                if len(cpoly) >= 3 and clip.area([cpoly]) > 0:
                    extra.append(PatternPath(arcfit.fit_contour(cpoly, True, tol), 1 << 30))
        return pattern + extra

    def _link(self, pattern: list[PatternPath], inner: list[clip.Poly], so: float, r: float,
              home: Pt) -> list[PatternPath]:
        """Order paths group by group (nearest neighbour inside a group) and mark the
        transitions that can be fed at depth because they stay inside the pocket."""
        out: list[PatternPath] = []
        pos = home
        max_link = max(2.5 * so, 2 * r)
        for g in sorted({p.group for p in pattern}):
            remaining = [p for p in pattern if p.group == g]
            while remaining:
                best_i, best_d = 0, math.inf
                for i, pp in enumerate(remaining):
                    c = pp.contour
                    d = c.nearest(pos)[3] if c.closed else min(dist(pos, c.start), dist(pos, c.end))
                    if d < best_d:
                        best_i, best_d = i, d
                pp = remaining.pop(best_i)
                c = pp.contour
                if c.closed:
                    c = vectorops.set_start_point(c, pos)
                elif dist(pos, c.end) < dist(pos, c.start):
                    c = c.reversed()
                linked = bool(out) and dist(pos, c.start) <= max_link and segment_inside(pos, c.start, inner)
                out.append(PatternPath(c, g, linked))
                pos = c.end
        return out

    # -- moves ------------------------------------------------------------------------
    def _cut(self, mb: MoveBuilder, c: Contour, z: float) -> None:
        for s in c.segs:
            mb.segment(s, z)

    def _enter(self, mb: MoveBuilder, c: Contour, z: float, floor: float, inner: list[clip.Poly]) -> None:
        """Move to the start of ``c`` and get down to ``z`` (``floor`` is already cleared)."""
        v = self.values
        p = c.start
        top = floor
        go = mb.travel if mb.pos is None else mb.hop
        entry = v["entry"]
        if entry == "helix":
            if self._helix(mb, c, z, top, inner, go):
                return
            entry = "ramp"
        if entry == "ramp" and c.length() > 1.0:
            go(p[0], p[1], top, floor)
            walker = PathWalker(c.segs, c.closed)
            half = min(v["ramp_length"] / 2, walker.length / 2)
            zm = (top + z) / 2
            mb.pos = emit_profile(mb.moves, walker, 0.0, half, lambda u: top + (zm - top) * u / half,
                                  [], mb.plunge_rate, mb.pos, mb.plunge_rate)
            rev = PathWalker(Contour(walker.pieces(0.0, half), False).reversed().segs, False)
            mb.pos = emit_profile(mb.moves, rev, 0.0, rev.length, lambda u: zm + (z - zm) * u / rev.length,
                                  [], mb.plunge_rate, mb.pos, mb.plunge_rate)
            return
        go(p[0], p[1], z, floor)

    def _helix(self, mb: MoveBuilder, c: Contour, z: float, top: float, inner: list[clip.Poly], go) -> bool:
        v = self.values
        p = c.start
        rh = v["helix_radius"] if v["helix_radius"] > 0 else max(0.5, self._r * 0.5)
        T = c.segs[0].tangent_at(0.0)
        for n in (perp(T), mul(perp(T), -1.0)):
            centre = add(p, mul(n, rh))
            if circle_inside(centre, rh, inner):
                break
        else:
            return False
        go(p[0], p[1], top, top)
        depth = top - z
        per_turn = 2 * math.pi * rh * math.tan(math.radians(v["ramp_angle"]))
        turns = max(1, math.ceil(depth / max(1e-6, per_turn)))
        opp = (2 * centre[0] - p[0], 2 * centre[1] - p[1])
        zc = top
        dz = depth / (2 * turns)
        for _ in range(turns):
            zc -= dz
            mb.arc(opp[0], opp[1], zc, centre[0], centre[1], False, mb.plunge_rate)
            zc -= dz
            mb.arc(p[0], p[1], zc, centre[0], centre[1], False, mb.plunge_rate)
        mb.pos = (p[0], p[1], z)
        return True


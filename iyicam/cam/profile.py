"""Profile (contour) machining: outside / inside / on the vector."""
from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass

from ..core import arcfit, clip, vectorops
from ..core.geom import Arc, BBox, Contour, Line, Pt, add, dist, mul, perp, sub, unit
from ..core.params import Param
from . import sequence as seq
from .builder import MoveBuilder
from .moves import Toolpath
from .operation import OpContext, Operation, depth_levels, depth_params, final_depth, stepdown
from .pathutil import PathWalker, emit_profile, machinable, nearest_on_polys, pl_max_breaks
from .startpoints import Machined, choose_start, shape_kind

NEG_INF = -1e9


@dataclass
class CutItem:
    contour: Contour
    level: int = 0
    finish: bool = False
    wall_right: bool | None = None  # side of the finished wall (None: no leads)
    key: str = ""                   # "<vector id>:<contour index>" of the vector contour it cuts
    source: Contour | None = None   # that vector contour


# finished cuts by everything they depend on: editing one start point recomputes one cut
_CACHE: OrderedDict = OrderedDict()
_LOOPS: OrderedDict = OrderedDict()  # offset loops of a part, by its geometry
_CACHE_SIZE = 20000
CACHE_STATS = {"hits": 0, "misses": 0}


def _signature(c: Contour) -> tuple:
    return tuple((type(s).__name__, round(s.a[0], 6), round(s.a[1], 6), round(s.b[0], 6), round(s.b[1], 6),
                  round(getattr(s, "bulge", 0.0), 9)) for s in c.segs)


def offset_loops(closed: list[Contour], delta: float, sharp: bool, tol: float) -> list[Contour]:
    """Offset a region and fit arcs; keeps the Clipper orientation (region on the left)."""
    key = (tuple(_signature(c) for c in closed), delta, sharp, tol)
    hit = _LOOPS.get(key)
    if hit is not None:
        _LOOPS.move_to_end(key)
        return list(hit)
    reg = vectorops.region(closed, tol / 2)
    polys = clip.offset(reg, delta, "miter" if sharp else "round", arc_tol=tol / 2,
                        miter_limit=10.0 if sharp else 4.0)
    out = [arcfit.fit_contour(p, True, tol) for p in polys if len(p) >= 3]
    _LOOPS[key] = tuple(out)
    if len(_LOOPS) > _CACHE_SIZE:
        _LOOPS.popitem(last=False)
    return out


class TabPlan:
    """Tab positions along a closed loop and the resulting Z floor."""

    def __init__(self, length: float, centres: list[float], tab_len: float, top: float,
                 bottom: float, ramped: bool):
        self.L = length
        self.centres = centres
        self.h = tab_len / 2
        self.top = top
        self.ramp = (top - bottom) if ramped else 0.0

    def floor(self, s: float) -> float:
        best = NEG_INF
        for c in self.centres:
            d = abs((s - c + self.L / 2) % self.L - self.L / 2)
            if d <= self.h + 1e-9:
                return self.top
            if self.ramp > 0 and d <= self.h + self.ramp:
                best = max(best, self.top - (d - self.h))
        return best

    def breaks(self, s0: float, U: float) -> list[float]:
        out = []
        offs = [-self.h, self.h] + ([-self.h - self.ramp, self.h + self.ramp] if self.ramp > 0 else [])
        for c in self.centres:
            for o in offs:
                x = (c + o - s0) % self.L
                while x <= U + 1e-9:
                    out.append(x)
                    x += self.L
        return out


class ProfileOp(Operation):
    id = "profile"
    label = "Profile"
    description = "Cut along vectors on the outside, inside or on the line, with tabs, ramps and leads."
    tool_types = ("end_mill", "ball_nose", "bull_nose", "v_bit", "engraving")

    @classmethod
    def params(cls) -> list[Param]:
        ch = [("outside", "Outside / Right"), ("inside", "Inside / Left"), ("on", "On the vector")]
        return depth_params(6.0, through=True) + [
            Param("side", "Machine", "choice", "outside", choices=ch, group="Profile"),
            Param("direction", "Direction", "choice", "climb",
                  choices=[("climb", "Climb"), ("conventional", "Conventional")], group="Profile"),
            Param("allowance", "Allowance", "length", 0.0, group="Profile",
                  help="Material left on the wall; negative cuts into the part"),
            Param("sharp_corners", "Sharp corners", "bool", False, group="Profile",
                  visible={"side": ["outside", "inside"]},
                  help="Keep offset corners sharp instead of rolling around them"),
            Param("finish_pass", "Finish pass allowance", "length", 0.0, min=0.0, group="Profile",
                  visible={"side": ["outside", "inside"]},
                  help="Leave this much for a final full depth pass (0 = no finish pass)"),
            Param("start_mode", "Start point", "choice", "vector",
                  choices=[("vector", "Vector start point"), ("nearest", "Nearest to previous cut")],
                  group="Profile"),
            Param("tabs", "Add tabs / bridges", "bool", False, group="Tabs"),
            Param("tab_mode", "Tab placement", "choice", "count",
                  choices=[("count", "Number per vector"), ("spacing", "Distance between tabs"),
                           ("manual", "Positions picked on the drawing")],
                  group="Tabs", visible={"tabs": True},
                  help="Manual positions are edited with 'Edit Tabs' in the toolpath list"),
            Param("tab_count", "Tabs per vector", "int", 4, min=1, max=200, group="Tabs",
                  visible={"tabs": True, "tab_mode": "count"}),
            Param("tab_spacing", "Tab spacing", "length", 300.0, min=1.0, group="Tabs",
                  visible={"tabs": True, "tab_mode": "spacing"}),
            Param("tab_length", "Tab length", "length", 8.0, min=0.1, group="Tabs", visible={"tabs": True}),
            Param("tab_height", "Tab thickness", "length", 3.0, min=0.0, group="Tabs", visible={"tabs": True}),
            Param("tab_3d", "Ramped (3D) tabs", "bool", False, group="Tabs", visible={"tabs": True}),
            Param("tab_min_length", "Skip vectors shorter than", "length", 40.0, min=0.0, group="Tabs",
                  visible={"tabs": True}),
            Param("ramp", "Entry", "choice", "none",
                  choices=[("none", "Plunge"), ("linear", "Ramp over distance"), ("helix", "Smooth spiral")],
                  group="Ramp & Leads"),
            Param("ramp_length", "Ramp distance", "length", 20.0, min=0.1, group="Ramp & Leads",
                  visible={"ramp": "linear"}),
            Param("lead", "Lead in / out", "choice", "none",
                  choices=[("none", "None"), ("arc", "Arc"), ("line", "Line")], group="Ramp & Leads",
                  visible={"side": ["outside", "inside"]}),
            Param("lead_length", "Lead radius / length", "length", 5.0, min=0.1, group="Ramp & Leads",
                  visible={"lead": ["arc", "line"]}),
            Param("overlap", "Overlap at end", "length", 0.0, min=0.0, group="Ramp & Leads",
                  help="Continue past the start point to clean the seam"),
        ]

    # ---------------------------------------------------------------------------
    def compute(self, ctx: OpContext) -> list[Toolpath]:
        v = self.values
        tool = ctx.tool
        tol = ctx.tolerance
        start = v["start_depth"]
        final = final_depth(self, ctx)
        levels = depth_levels(start, final, stepdown(self, ctx))
        if not levels:
            ctx.warn("Cut depth must be deeper than the start depth")
            return []
        surface = -start
        r = tool.radius_at_depth(final - start) if tool.is_tapered else tool.radius
        self._tool_r = r + v["allowance"]
        self._sig = (repr(sorted((k, repr(x)) for k, x in v.items())), repr(sorted(tool.to_dict().items())),
                     tuple(levels), surface, final, ctx.safe_z, ctx.clearance)
        sources = [(f"{o.id}:{i}", c) for o in ctx.objects for i, c in enumerate(o.contours) if c.segs]
        cfg = seq.settings(ctx.tdef.sequence)
        self._sheet = BBox(0.0, 0.0, ctx.sheet.width, ctx.sheet.height)
        items = self._items(ctx, sources, r, seq.outline_clockwise(cfg))
        if not items:
            ctx.warn("No vectors to machine")
            return []
        groups: dict[str, list[CutItem]] = {}
        for it in items:
            groups.setdefault(it.key, []).append(it)

        # cut order: one entry per vector contour, whatever its loops
        through = final >= ctx.thickness - 1e-6
        kerf_r = max(self._tool_r, 0.05)
        src = dict(sources)
        sitems = []
        for key, its in groups.items():
            closed = its[0].contour.closed
            loops = [it.contour.flatten(tol * 4) for it in its if not it.finish]
            kerf = clip.offset_lines(loops, kerf_r, closed=closed) if loops else None
            held = v["tabs"] and any(self._tabs(PathWalker(it.contour.segs, True), final, self._tool_r) is not None
                                     for it in its if closed)
            sitems.append(seq.SeqItem(key, src[key], frees=closed and through and not held, kerf=kerf,
                                      solid=self._solids.get(key)))
        if through and v["finish_pass"] and v["side"] != "on" and not v["tabs"]:
            ctx.warn("The finish pass runs along parts the roughing pass has already cut free: add tabs")
        sheet = BBox(0.0, 0.0, ctx.sheet.width, ctx.sheet.height)
        home = ctx.material.home[:2]
        order, notes = seq.sequence(sitems, cfg, home, sheet)
        for n in notes:
            ctx.warn(n)
        ctx.order = order

        # machine them, choosing where each closed cut starts
        locked = {f"{o.id}:{i}" for o in ctx.objects for i in o.locked_starts}
        smart = cfg["smart_starts"] or ctx.force_smart
        machined = Machined()  # what is machined so far: start points keep clear of it
        track = smart or bool(locked)
        if track:
            for prev in ctx.previous:
                machined.add_moves(prev.moves, lambda d, t=prev.tool: t.radius_at_depth(d) if t.is_tapered else t.radius)
        mb = MoveBuilder(ctx.safe_z, ctx.clearance, tool.feed_rate, tool.plunge_rate, surface)
        cuts = []
        free = {it.key: it.frees for it in sitems}
        for idx, key in enumerate(order):
            its = groups[key]
            first = None
            for it in [it for it in its if not it.finish] + [it for it in its if it.finish]:
                c = it.contour
                info = {"key": key, "index": idx, "closed": c.closed, "finish": it.finish, "frees": free[key]}
                if c.closed:
                    n = 1 if it.finish else len(levels)
                    c, close = self._place_start(ctx, it, c, mb, machined, cfg, r, key in locked, smart, first,
                                                 info, n)
                    first = first or close
                    info["close"] = close
                info["start"] = c.start
                if c.closed:
                    info["loop"] = c
                    info["radius"] = kerf_r
                block = self._block(ctx, c, it, levels, surface, final)
                info["moves"] = (len(mb.moves), len(mb.moves) + len(block))
                if block:
                    mb.retract()
                    mb.moves.extend(block)
                    last = next(m for m in reversed(block) if hasattr(m, "z"))
                    mb.pos = (last.x, last.y, last.z)
                    info["entry"] = next((m.x, m.y) for m in block if hasattr(m, "x"))
                    if track:
                        machined.add_polyline(c.flatten(0.1), kerf_r, closed=c.closed)
                        lead_in = self._entry_fn(PathWalker(c.segs, True), it) if c.closed else None
                        if lead_in is not None:
                            machined.add_polyline(lead_in(0.0), kerf_r)
                cuts.append(info)
        tp = self.make_toolpath(ctx, mb.finish())
        tp.cuts = cuts
        return [tp]

    def _items(self, ctx: OpContext, sources: list[tuple[str, Contour]], r: float,
               clockwise: bool | None = None) -> list[CutItem]:
        """Loops to cut, each tagged with the key of the vector contour it belongs to.

        ``clockwise`` overrides the direction of part outlines (holes keep the same
        climb / conventional milling as their outline)."""
        v = self.values
        tol = ctx.tolerance
        closed = [(k, c) for k, c in sources if c.closed]
        opened = [(k, machinable(c, tol)) for k, c in sources if not c.closed]
        items: list[CutItem] = []
        self._solids: dict[str, list] = {}  # part material left by an outside cut, by outline key
        if v["side"] == "on":
            for k, src in closed:
                c = machinable(src, tol)
                if clockwise is not None and c.is_ccw() == clockwise:
                    c = c.reversed()
                items.append(CutItem(c, key=k, source=src))
        elif closed:
            base = r + v["allowance"]
            sign = 1.0 if v["side"] == "outside" else -1.0
            climb = v["direction"] == "climb"
            if clockwise is not None:  # climb milling runs clockwise around the outside of a part
                climb = clockwise if v["side"] == "outside" else not clockwise
            # Clipper keeps the solid on the left; for outside cuts that is the part.
            reverse = climb if v["side"] == "outside" else not climb
            fin = v["finish_pass"] or 0.0
            passes = [(sign * (base + fin), False), (sign * base, True)] if fin > 0 else [(sign * base, False)]
            # every part (an outline with its holes) is offset on its own; parts nested in a
            # hole are parts of their own
            tree = [seq.SeqItem(k, c) for k, c in closed]
            seq.containment(tree)
            for part in (it for it in tree if it.depth % 2 == 0):
                group = [(part.key, part.contour)] + [(h.key, h.contour) for h in tree if h.parent == part.key]
                if v["side"] == "outside":
                    self._solids[part.key] = vectorops.region([gc for _, gc in group], 0.05)
                for delta, is_fin in passes:
                    loops = offset_loops([gc for _, gc in group], delta, v["sharp_corners"], tol)
                    if not loops and not is_fin:
                        ctx.warn("Offset vanished: the tool is too large for some vectors")
                    for lp in loops:
                        probe = (lp.start, lp.point_at_length(lp.length() / 2))
                        key, src = min(group, key=lambda kc: sum(kc[1].nearest(q)[3] for q in probe))
                        items.append(CutItem(lp.reversed() if reverse else lp, finish=is_fin, wall_right=climb,
                                             key=key, source=src))
            if opened:
                ctx.warn("Open vectors are machined on the line")
        items += [CutItem(c, key=k, source=c) for k, c in opened]
        return items

    def _closing_shift(self, L: float, passes: int, it: CutItem) -> float:
        """How far before its closing point a cut starts: passes without a lead-in go on where the
        previous pass ended (by the ramp and the overlap), so the last pass closes further on."""
        v = self.values
        if (v["lead"] != "none" and it.wall_right is not None) or v["ramp"] == "helix":
            return 0.0
        if v["ramp"] == "linear":
            R = min(v["ramp_length"], L)
            return ((passes - 1) * (R + v["overlap"]) + R) % L
        return ((passes - 1) * v["overlap"]) % L

    def _place_start(self, ctx: OpContext, it: CutItem, c: Contour, mb: MoveBuilder, machined: Machined,
                     cfg: dict, r: float, locked: bool, smart: bool, first: Pt | None, info: dict,
                     passes: int) -> tuple[Contour, Pt]:
        """Rotate the loop ``c`` to where the tool starts and return where the cut closes.

        The vector start point marks where the cut closes: that is what holds the part
        to the end. The tool goes down earlier when the passes creep on (ramps, overlap).
        """
        walker = PathWalker(c.segs, True)
        L = walker.length
        pos = mb.pos[:2] if mb.pos is not None else ctx.material.home[:2]
        shift = self._closing_shift(L, passes, it)

        def at(p: Pt) -> float:
            i, t, _, _ = c.nearest(p)
            return walker.cum[i] + t * (walker.cum[i + 1] - walker.cum[i])

        if first is not None:  # the finish pass closes where its roughing pass closed
            s = at(first)
        elif smart or locked:
            hint = at(it.source.start)
            ch = choose_start(walker, r, self._entry_fn(walker, it), machined, pos, hint,
                              cfg["start_margin"], cfg["corner_distance"], locked=locked or not smart,
                              hold=seq.hold_direction(cfg["strategy"], it.source.bbox().center, self._sheet),
                              kind=shape_kind(it.source), sheet=self._sheet, shift=shift)
            s = ch.s
            info.update(gap=ch.gap, ok=ch.ok, locked=locked)
            if not ch.ok:
                ctx.warn("Some cuts have no start point clear of machined areas and corners"
                         if not locked else "A start point set by hand is close to machined areas or a corner")
            if smart and not locked and ctx.start_updates is not None:
                q = it.source.nearest(walker.point_at(s))[2]
                if dist(q, it.source.start) > 1e-3:
                    ctx.start_updates[it.key] = q
        elif self.values["start_mode"] == "nearest" and mb.pos is not None:
            s = (at(pos) + shift) % L  # go down nearest to the tool
        else:
            s = at(it.source.start)
        close = walker.point_at(s)
        return vectorops.set_start_point(c, walker.point_at((s - shift) % L)), close

    def _entry_fn(self, walker: PathWalker, it: CutItem):
        """Points the tool occupies at depth before cutting from ``s``: the plunge, or the lead-in."""
        v = self.values
        lead = v["lead"] if it.wall_right is not None else "none"
        if lead == "none":
            return None

        def entry(s: float) -> list[Pt]:
            P = walker.point_at(s)
            S, seg = self._lead_in(P, walker.tangent_at(s), it.wall_right, lead, v["lead_length"])
            return [S, seg.point_at(0.5), P]

        return entry

    def _block(self, ctx: OpContext, c: Contour, it: CutItem, levels: list[float], surface: float,
               final: float) -> list:
        """Moves of one cut from above its entry point; reused while nothing it depends on changes."""
        key = (_signature(c), c.closed, it.finish, it.wall_right, self._sig)
        hit = _CACHE.get(key)
        if hit is not None:
            _CACHE.move_to_end(key)
            CACHE_STATS["hits"] += 1
            return list(hit)
        CACHE_STATS["misses"] += 1
        tool = ctx.tool
        mb = MoveBuilder(ctx.safe_z, ctx.clearance, tool.feed_rate, tool.plunge_rate, surface)
        if c.closed:
            self._cut_closed(mb, c, levels if not it.finish else [levels[-1]], surface, final, it)
        else:
            self._cut_open(mb, c, levels, surface)
        _CACHE[key] = tuple(mb.moves)
        if len(_CACHE) > _CACHE_SIZE:
            _CACHE.popitem(last=False)
        return list(mb.moves)

    # ---------------------------------------------------------------------------
    def _tabs(self, walker: PathWalker, final: float, tool_r: float) -> TabPlan | None:
        v = self.values
        L = walker.length
        if not v["tabs"]:
            return None
        top = -final + v["tab_height"]
        if v["tab_mode"] == "manual":
            reach = tool_r + v["tab_length"] + 1.0
            centres = []
            for pt in v.get("tab_points") or []:
                i, t, d = nearest_on_polys(tuple(pt), walker.segs)
                if d <= reach:
                    centres.append(walker.cum[i] + t * (walker.cum[i + 1] - walker.cum[i]))
            return TabPlan(L, centres, v["tab_length"], top, -final, v["tab_3d"]) if centres else None
        if L < max(v["tab_min_length"], v["tab_length"] * 2):
            return None
        if v["tab_mode"] == "spacing":
            n = max(1, round(L / max(1.0, v["tab_spacing"])))
        else:
            n = max(1, v["tab_count"])
        if n * v["tab_length"] >= L * 0.8:
            n = max(1, int(L * 0.8 / v["tab_length"]))
        centres = [(k + 0.5) * L / n for k in range(n)]
        return TabPlan(L, centres, v["tab_length"], top, -final, v["tab_3d"])

    def _cut_closed(self, mb: MoveBuilder, c: Contour, levels: list[float], surface: float,
                    final: float, it: CutItem) -> None:
        v = self.values
        walker = PathWalker(c.segs, True)
        L = walker.length
        if L < 1e-6:
            return
        tabs = self._tabs(walker, final, self._tool_r)
        lead = v["lead"] if it.wall_right is not None else "none"
        lead_len = v["lead_length"]
        overlap = v["overlap"]
        ramp = v["ramp"]
        s_cur = 0.0
        z_prev = surface
        floor_at_entry = surface
        passes = list(levels)
        helix = ramp == "helix" and lead == "none"
        if helix:
            passes.append(passes[-1])  # final clean-up lap at full depth
        for k, z in enumerate(passes):
            z_from = z_prev
            if lead != "none":
                U = L + overlap
                pz = lambda u, z=z: z
                breaks: list[float] = []
            elif helix and k < len(passes) - 1:
                U = L
                pz = lambda u, z=z, z0=z_from, U=U: z0 + (z - z0) * min(u, U) / U
                breaks = []
            elif ramp == "linear" and z < z_from - 1e-9:
                R = min(v["ramp_length"], L)
                U = L + R + overlap
                pz = lambda u, z=z, z0=z_from, R=R: z0 + (z - z0) * min(u, R) / R
                breaks = [R]
            else:
                U = L + overlap
                pz = lambda u, z=z: z
                breaks = []
            if tabs is not None and min(z, z_from) < tabs.top:
                s0 = s_cur
                zf = lambda u, pz=pz, s0=s0: max(pz(u), tabs.floor(s0 + u))
                br = pl_max_breaks(pz, lambda u, s0=s0: tabs.floor(s0 + u), breaks + tabs.breaks(s0, U) + [0.0, U])
            else:
                zf, br = pz, breaks
            p_start = walker.point_at(s_cur)
            z_entry_loop = zf(0.0)
            if lead != "none":
                T = walker.tangent_at(s_cur)
                S, lead_seg = self._lead_in(p_start, T, it.wall_right, lead, lead_len)
                z_s = z_from if ramp != "none" else z
                if k == 0:
                    mb.travel(S[0], S[1], z_s)
                else:
                    mb.hop(S[0], S[1], z_s, floor_at_entry)
                floor_at_entry = z_s
                mb.segment(lead_seg, z_entry_loop)
            else:
                if k == 0 or mb.pos is None:
                    mb.travel(p_start[0], p_start[1], z_entry_loop)
                else:
                    if abs(mb.pos[0] - p_start[0]) > 1e-6 or abs(mb.pos[1] - p_start[1]) > 1e-6:
                        mb.feed(p_start[0], p_start[1], mb.pos[2])
                    mb.plunge(z_entry_loop)
            mb.pos = emit_profile(mb.moves, walker, s_cur, U, zf, br, mb.feed_rate, mb.pos, mb.plunge_rate)
            if lead != "none":
                s_end = s_cur + U
                T2 = walker.tangent_at(s_end)
                seg = self._lead_out(walker.point_at(s_end), T2, it.wall_right, lead, lead_len)
                mb.segment(seg, mb.pos[2])
            else:
                s_cur = (s_cur + U) % L
            z_prev = z

    @staticmethod
    def _waste_normal(T: Pt, wall_right: bool) -> Pt:
        n = perp(T)
        return n if wall_right else mul(n, -1.0)

    def _lead_in(self, P: Pt, T: Pt, wall_right: bool, kind: str, R: float):
        N = self._waste_normal(T, wall_right)
        if kind == "arc":
            C = add(P, mul(N, R))
            S = sub(C, mul(T, R))
            ccw = wall_right  # centre on the left of the motion when the waste is on the left
            return S, Arc.from_center_points(S, P, C, ccw)
        S = add(P, mul(unit(sub(N, T)), R))
        return S, Line(S, P)

    def _lead_out(self, P: Pt, T: Pt, wall_right: bool, kind: str, R: float):
        N = self._waste_normal(T, wall_right)
        if kind == "arc":
            C = add(P, mul(N, R))
            E = add(C, mul(T, R))
            return Arc.from_center_points(P, E, C, wall_right)
        return Line(P, add(P, mul(unit(add(N, T)), R)))

    def _cut_open(self, mb: MoveBuilder, c: Contour, levels: list[float], surface: float) -> None:
        v = self.values
        cur = c
        z_prev = surface
        for k, z in enumerate(levels):
            walker = PathWalker(cur.segs, False)
            L = walker.length
            if L < 1e-9:
                return
            if v["ramp"] in ("linear", "helix") and z < z_prev:
                R = min(v["ramp_length"] if v["ramp"] == "linear" else L, L)
                zf = lambda u, z=z, z0=z_prev, R=R: z0 + (z - z0) * min(u, R) / R
                br = [R]
                z0 = z_prev
            else:
                zf = lambda u, z=z: z
                br = []
                z0 = z
            if k == 0:
                mb.travel(cur.start[0], cur.start[1], z0)
            else:
                mb.plunge(z0)
            mb.pos = emit_profile(mb.moves, walker, 0.0, L, zf, br, mb.feed_rate, mb.pos, mb.plunge_rate)
            z_prev = z
            cur = cur.reversed()


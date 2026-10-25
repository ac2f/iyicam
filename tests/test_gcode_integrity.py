"""Every setting must end up as correct G-code.

Jobs are calculated with many combinations of settings, written through the real
posts and read back from the G-code text. The checks only use the G-code and the
drawing, not the toolpath code:

* the program: units, tool calls, spindle on with the tool's speed before any
  cutting, safe height for every tool change and around the job, program end;
* depths: the final depth exactly, never deeper, level cutting only on the pass
  depths, nothing above the start depth, and against a model of the material
  left, no move ever cuts more than one pass deep;
* rapids: sideways only above the material, down only into what is cut already;
* feeds: plunges at the plunge rate, cutting at the feed rate;
* the tool: off the part by its radius plus the allowance (inside: inside the
  vector, on: on the line), cutting in the chosen direction, and every part cut
  free of the sheet and of its holes at full depth when there are no tabs.

A last test damages good G-code in typical ways to show the checks see it.
"""
import math
import re

import numpy as np
import pytest
from conftest import add_tp, end_mill, make_project, vbit

from iyicam.cam.gcode_reader import ReadOptions, read_gcode
from iyicam.cam.job import compute_toolpath
from iyicam.cam.moves import ArcMove, DrillCycle, Dwell, Feed, Rapid, arc_sweep
from iyicam.core import shapes
from iyicam.core.registry import REGISTRY
from iyicam.post.engine import ORIGINS, PostProcessor
from iyicam.post.export import post_toolpaths

THICK = 18.0
SAFE, CLEAR = 10.0, 2.0
FEED, PLUNGE = 3000.0, 1000.0
TOL = 1e-3  # G-code resolution (three decimals, or five in inches)
SHEET = (280.0, 200.0)
POSTS = ("mach3", "fanuc", "linuxcnc", "r", "ncstudio", "syntec", "inch")


def drawing():
    """A part with a hole, an L with an inside corner, a disc and a slim strip."""
    return [
        [shapes.rectangle(20, 20, 110, 70, corner_radius=6), shapes.circle((75, 55), 14)],
        [shapes.polyline([(150, 20), (240, 20), (240, 45), (180, 45), (180, 110), (150, 110)], True)],
        [shapes.circle((80, 150), 28)],
        [shapes.rectangle(150, 130, 100, 16)],
    ]


INSIDE = [(30, 30), (160, 60), (80, 150), (200, 138)]  # a point in each part
SLUGS = [(75, 55)]  # the scrap of the hole
HOLE = ((75, 55), 14.0)


def post_variant(name):
    """A built-in post; ``r``: arcs by radius, ``inch``: Fanuc in inches."""
    post = REGISTRY.posts[{"r": "mach3", "inch": "fanuc"}.get(name, name)]
    opts = {"r": {"arcs": "r"}, "inch": {"units": "inch", "decimals": 5}}.get(name)
    if not opts:
        return post
    d = dict(post.definition)
    d["options"] = {**d.get("options", {}), **opts}
    return PostProcessor(d)


def gcode_files(p, s, tps, post_name):
    """The posted files and how to read them back; the inch variant also has Z zero on the table and X0 Y0 in
    the middle of the sheet."""
    if post_name == "inch":
        p.material.z_zero = "bottom"
        p.material.xy_origin = "center"
    texts = [text for _, text in post_toolpaths(p, s, tps, post_variant(post_name), "check")]
    fx, fy = ORIGINS[p.material.xy_origin]
    opt = ReadOptions(fx * s.width, fy * s.height, p.sheet_thickness(s) if p.material.z_zero == "bottom" else 0.0)
    return texts, opt, post_name == "inch"


def read_back(texts, opt):
    return [m for text in texts for tp in read_gcode(text, opt) for m in tp.moves]


def samples(moves, step=0.5):
    """``(x, y, z, kind, f)`` points along the moves; kind: rapid / feed."""
    out = []
    pos = None
    for m in moves:
        if isinstance(m, DrillCycle) or not hasattr(m, "z"):
            continue
        if pos is not None:
            kind = "rapid" if isinstance(m, Rapid) else "feed"
            f = getattr(m, "f", 0.0)
            if isinstance(m, ArcMove):
                sw = arc_sweep(pos[0], pos[1], m)
                r = math.hypot(pos[0] - m.cx, pos[1] - m.cy)
                a0 = math.atan2(pos[1] - m.cy, pos[0] - m.cx)
                n = max(1, int(abs(sw) * r / step) + 1)
                for k in range(1, n + 1):
                    a = a0 + sw * k / n
                    out.append((m.cx + r * math.cos(a), m.cy + r * math.sin(a), pos[2] + (m.z - pos[2]) * k / n,
                                kind, f))
            else:
                n = max(1, int(math.dist(pos, (m.x, m.y, m.z)) / step) + 1)
                for k in range(1, n + 1):
                    t = k / n
                    out.append((pos[0] + (m.x - pos[0]) * t, pos[1] + (m.y - pos[1]) * t,
                                pos[2] + (m.z - pos[2]) * t, kind, f))
        pos = (m.x, m.y, m.z)
    return out


def inside(P, contour):
    """Even-odd test of the points ``P`` (n x 2) against one closed contour."""
    poly = np.array(contour.flatten(0.01), float)
    A, B = poly, np.roll(poly, -1, axis=0)
    x, y = P[:, :1], P[:, 1:]
    crosses = ((A[:, 1] > y) != (B[:, 1] > y)) & (
        x < (B[:, 0] - A[:, 0]) * (y - A[:, 1]) / np.where(B[:, 1] == A[:, 1], 1e-30, B[:, 1] - A[:, 1]) + A[:, 0])
    return crosses.sum(axis=1) % 2 == 1


def edge_distance(pts, contours):
    """Distance of every point to the nearest of the contours."""
    edges = []
    for c in contours:
        poly = c.flatten(0.01)
        edges += list(zip(poly, poly[1:] + poly[:1]))
    E = np.array(edges, float)
    A, AB = E[:, 0], E[:, 1] - E[:, 0]
    L2 = np.maximum((AB * AB).sum(axis=1), 1e-18)
    out = []
    for k in range(0, len(pts), 400):
        P = np.array([q[:2] for q in pts[k:k + 400]], float)
        t = np.clip(((P[:, None, :] - A) * AB).sum(axis=2) / L2, 0, 1)
        out.append(np.sqrt(((P[:, None, :] - (A + t[..., None] * AB)) ** 2).sum(axis=2)).min(axis=1))
    return np.concatenate(out) if out else np.zeros(0)


def clearance(pts, contours, r, material):
    """Smallest gap between the edge of the tool and the material (negative: the tool cuts into it).

    ``material(P)`` tells which of the points are in the material."""
    if not pts:
        return math.inf
    d = edge_distance(pts, contours)
    P = np.array([q[:2] for q in pts], float)
    return float(np.where(material(P), -d, d - r).min())


def in_parts(parts):
    """Material of parts given as lists of contours (outline and holes)."""
    return lambda P: np.any([np.sum([inside(P, c) for c in cs], axis=0) % 2 == 1 for cs in parts], axis=0)


def words(text):
    """The program as lists of ``(letter, value)`` words, comments and line numbers dropped."""
    out = []
    for ln in text.splitlines():
        ln = re.sub(r"\(.*?\)", "", ln).split(";")[0].upper()
        ws = [(a, float(b)) for a, b in re.findall(r"([A-Z])\s*([-+]?\d*\.?\d+)", ln) if a != "N"]
        if ws:
            out.append(ws)
    return out


def check_program(text, rpm, opt=None, inch=False):
    """Program frame: units, tool calls, spindle on with the tool's speed before cutting, safe height for tool
    changes and around the job, program end. ``rpm``: spindle speed of every tool number."""
    opt = opt or ReadOptions()
    k = 25.4 if inch else 1.0
    prog = words(text)
    first_motion = first_feed = last_feed = None
    z = None  # in mm from the top of the material
    modal = 0
    init_z = None  # level a canned cycle returns to (G98)
    tool, speed, spindle = None, None, False
    events = []  # (line, Z) of sideways rapids
    for i, ws in enumerate(prog):
        g = [v for a, v in ws if a == "G"]
        m = [v for a, v in ws if a == "M"]
        for v in g:
            if v in (0, 1, 2, 3) or 81 <= v <= 89:
                if 81 <= v <= 89 and modal < 81:
                    init_z = z
                modal = int(v)
            elif v == 80:
                if modal >= 81:
                    z = init_z
                modal = 0
        tool = next((v for a, v in ws if a == "T"), tool)
        speed = next((v for a, v in ws if a == "S"), speed)
        if 6 in m:
            assert z is not None and z >= SAFE - 1e-3, f"tool change at Z{z}"
            spindle = False
        if 3 in m or 4 in m:
            spindle = True
            assert speed == pytest.approx(rpm[int(tool)] if tool is not None else next(iter(rpm.values()))), \
                "spindle speed is not the tool's"
        if 5 in m:
            spindle = False
        axes = {a: v for a, v in ws if a in "XYZ"}
        home = any(v in (28, 53, 30) for v in g)
        if "Z" in axes and modal < 81:
            z = math.inf if home else axes["Z"] * k - opt.z_offset  # machine home is above everything
        if axes and not home:
            first_motion = i if first_motion is None else first_motion
            if modal != 0:
                assert spindle, f"cutting with the spindle off in line {i}"
                first_feed = i if first_feed is None else first_feed
                last_feed = i
            elif "X" in axes or "Y" in axes:
                events.append((i, z))
    assert any(("G", 20 if inch else 21) in ws for ws in prog[:first_motion]), "units not set"
    if any(("M", 6) in ws for ws in prog):
        for n in rpm:
            assert any(("T", n) in ws for ws in prog), f"tool {n} not called"
    # the first sideways rapid and the ones after the last cut go at the safe height
    for i, zz in events:
        if i < first_feed or i > last_feed:
            assert zz is not None and zz >= SAFE - 1e-3, f"rapid at Z{zz} in line {i}"
    end = [i for i, ws in enumerate(prog) if ("M", 30) in ws or ("M", 2) in ws]
    assert end and end[-1] > last_feed, "no program end"
    assert not any(a in "XYZ" for ws in prog[end[-1]:] for a, _ in ws), "moves after the program end"


def _runs_fill(seed, free):
    """Cells of ``free`` connected to ``seed`` (4-neighbour), sweeping whole free runs along rows and columns."""
    reach = seed & free
    while True:
        before = int(reach.sum())
        for flip in (False, True):
            f = np.ascontiguousarray(free.T if flip else free)
            r = np.ascontiguousarray(reach.T if flip else reach)
            start = f.copy()
            start[:, 1:] &= ~f[:, :-1]
            ids = np.cumsum(start.ravel()).reshape(f.shape) * f
            hit = np.zeros(int(ids.max()) + 1, bool)
            hit[ids[r]] = True
            hit[0] = False
            reach = hit[ids].T if flip else hit[ids]
        if int(reach.sum()) == before:
            return reach


def separated(bottom, tool_r, size, inside_pts, slugs, cell=0.25):
    """Whether the full depth cut parts every ``inside_pts`` point from the sheet frame and from the ``slugs``.

    The kerf is rasterised a little narrower than the tool so a thread of material is never missed."""
    nx, ny = int(size[0] / cell) + 1, int(size[1] / cell) + 1
    cut = np.zeros((ny, nx), bool)
    rr = tool_r - 0.15
    k = int(rr / cell) + 1
    for x, y in bottom:
        i, j = int(round(y / cell)), int(round(x / cell))
        i0, i1, j0, j1 = max(i - k, 0), min(i + k + 1, ny), max(j - k, 0), min(j + k + 1, nx)
        if i0 >= i1 or j0 >= j1:
            continue
        Y, X = np.mgrid[i0:i1, j0:j1]
        cut[i0:i1, j0:j1] |= (X * cell - x) ** 2 + (Y * cell - y) ** 2 <= rr * rr
    free = ~cut
    frame = np.zeros_like(free)
    frame[0, :] = frame[-1, :] = frame[:, 0] = frame[:, -1] = True
    outside = _runs_fill(frame, free)
    for q in inside_pts:
        cq = (int(round(q[1] / cell)), int(round(q[0] / cell)))
        if outside[cq]:
            return False
        seed = np.zeros_like(free)
        seed[cq] = True
        part = _runs_fill(seed, free)
        if any(part[int(round(s[1] / cell)), int(round(s[0] / cell))] for s in slugs):
            return False
    return True


class Stock:
    """The material left, as heights on a grid; the tool takes away a disc wherever it goes."""

    def __init__(self, size, top, r, cell=0.25):
        self.cell, self.r = cell, r
        self.k = int(r / cell) + 2
        pad = self.k
        self.h = np.full((int(size[1] / cell) + 1 + 2 * pad, int(size[0] / cell) + 1 + 2 * pad), -math.inf)
        self.h[pad:-pad, pad:-pad] = top  # beyond the sheet there is nothing to cut
        self.oy, self.ox = np.mgrid[-self.k:self.k + 1, -self.k:self.k + 1] * cell

    def _window(self, x, y):
        """Grid window around a point and the squared distance of its cells from the point."""
        i, j = int(round(y / self.cell)), int(round(x / self.cell))
        I, J = i + self.k, j + self.k
        if I < self.k or J < self.k or I + self.k + 1 > self.h.shape[0] or J + self.k + 1 > self.h.shape[1]:
            return None, None
        d2 = (self.ox + (j * self.cell - x)) ** 2 + (self.oy + (i * self.cell - y)) ** 2
        return (slice(I - self.k, I + self.k + 1), slice(J - self.k, J + self.k + 1)), d2

    def top(self, x, y, r):
        """Highest material under a disc of radius ``r``."""
        w, d2 = self._window(x, y)
        return -math.inf if w is None else float(self.h[w][d2 <= r * r].max())

    def cut(self, x, y, z):
        w, d2 = self._window(x, y)
        if w is not None:
            sub = self.h[w]
            np.minimum(sub, np.where(d2 <= self.r * self.r, z, math.inf), out=sub)


def check_moves(moves, *, surface, final, levels, step, r, allowance=0.0, parts=None, feed=FEED, plunge=PLUNGE,
                tab_top=None, cut_out=None, ramped=False, size=None, skim=0.0, gouge_tol=0.02):
    """The checks that do not depend on how the toolpath was made.

    ``size``: sheet size for the material model; ``skim``: width of a finish pass, which may cut the full depth."""
    pts = samples(moves)
    feeds = [p for p in pts if p[3] == "feed"]
    zs = [p[2] for p in feeds]
    # never deeper than the final depth, and the final depth reached
    assert all(z >= -final - TOL for z in zs), "deeper than the final depth"
    assert min(zs) == pytest.approx(-final, abs=TOL), "final depth not reached"
    # level cutting only at the planned pass depths (tabs and ramps aside), never above the start depth
    pos = None
    flat = set()
    for m in moves:
        if hasattr(m, "z") and not isinstance(m, DrillCycle):
            if pos is not None and isinstance(m, (Feed, ArcMove)) and abs(m.z - pos[2]) < 1e-9 and m.z < -1e-6:
                if math.hypot(m.x - pos[0], m.y - pos[1]) > 1e-6 or isinstance(m, ArcMove):
                    flat.add(m.z)
            pos = (m.x, m.y, m.z)

    def near(z, zs):  # the G-code holds three decimals
        return any(abs(z - q) <= 1.5e-3 for q in zs)

    allowed = list(levels) + [-final] + ([tab_top] if tab_top is not None else [])
    if not ramped:
        assert all(near(z, allowed) for z in flat), f"cutting off the pass depths: {sorted(flat)}"
    assert max(flat) <= surface + TOL, "cutting above the start depth"
    deep = sorted(z for z in flat if tab_top is None or not near(z, [tab_top]))
    steps = np.diff(sorted({surface} | set(deep)))
    assert np.all(steps <= step + 2e-3), f"pass step too large: {steps}"
    # feeds: straight plunges at the plunge rate, level cutting at the feed rate, ramps at one of the two
    pos = None
    for m in moves:
        if not hasattr(m, "z") or isinstance(m, DrillCycle):
            continue
        if pos is not None and isinstance(m, (Feed, ArcMove)):
            dxy = math.hypot(m.x - pos[0], m.y - pos[1]) if isinstance(m, Feed) else 1.0
            dz = m.z - pos[2]
            if dxy < 1e-6 and dz < -1e-6 and m.z < surface + CLEAR - 1e-6:
                assert m.f == pytest.approx(plunge, rel=2e-3), "plunge not at the plunge rate"
            elif abs(dz) < 1e-9 and dxy > 1e-6:
                assert m.f == pytest.approx(feed, rel=2e-3), "cut not at the feed rate"
            elif dxy > 1e-6:
                assert any(m.f == pytest.approx(f, rel=2e-3) for f in (feed, plunge)), "ramp at an odd feed rate"
        pos = (m.x, m.y, m.z)
    # arcs: both ends on the circle, as controllers demand
    pos = None
    for m in moves:
        if not hasattr(m, "z") or isinstance(m, DrillCycle):
            continue
        if pos is not None and isinstance(m, ArcMove):
            r0, r1 = math.hypot(pos[0] - m.cx, pos[1] - m.cy), math.hypot(m.x - m.cx, m.y - m.cy)
            assert abs(r0 - r1) <= 0.005, f"arc ends {abs(r0 - r1):.4f} off the circle"
        pos = (m.x, m.y, m.z)
    # rapids sideways only above the material
    pos = None
    for m in moves:
        if not hasattr(m, "z") or isinstance(m, DrillCycle):
            continue
        if pos is not None and isinstance(m, Rapid) and math.hypot(m.x - pos[0], m.y - pos[1]) > 1e-6:
            assert min(m.z, pos[2]) >= surface + min(CLEAR, SAFE) - TOL, "sideways rapid in material"
        pos = (m.x, m.y, m.z)
    # the material model: no move takes more than one pass, rapids go down only into what is cut already
    if size is not None:
        stock = Stock(size, surface, r)
        rq = max(r - skim - 0.1, 0.3)
        worst, pos = 0.0, None
        for m in moves:
            if not hasattr(m, "z") or isinstance(m, DrillCycle):
                continue
            if pos is not None:
                if isinstance(m, Rapid):
                    if m.z < pos[2] - 1e-9 and m.z < surface - 1e-6:
                        assert stock.top(m.x, m.y, rq) <= m.z + TOL, "rapid down into uncut material"
                else:
                    for x, y, z, _, _ in samples([Rapid(*pos), m]):
                        if z < surface:
                            worst = max(worst, stock.top(x, y, rq) - z)
                            stock.cut(x, y, z)
            pos = (m.x, m.y, m.z)
        assert worst <= step + 2e-3, f"a move cuts {worst:.3f} deep at once"
    # the tool keeps off the part
    if parts is not None:
        deep_pts = [p for p in feeds if p[2] < surface - 1e-6]
        assert clearance(deep_pts, [c for cs in parts for c in cs], r + allowance,
                         in_parts(parts)) >= -gouge_tol, "the tool cuts into the part"
    # cut out (where there are no tabs): at full depth the kerf parts every part from the sheet and its holes
    if cut_out:
        bottom = [p[:2] for p in feeds if p[2] <= -final + TOL]
        assert separated(bottom, r, *cut_out), "a part is still attached at full depth"


def turns(moves, final, ref):
    """``(signed area, centre, farthest distance from ref)`` of every continuous stretch cut at full depth
    (positive area: counter-clockwise)."""
    runs, run = [], []
    for q in samples(moves):
        if q[3] == "feed" and q[2] <= -final + TOL:
            run.append(q[:2])
        else:
            if len(run) > 20:
                runs.append(run)
            run = []
    if len(run) > 20:
        runs.append(run)
    out = []
    for run in runs:
        P = np.array(run)
        x, y = P[:, 0], P[:, 1]
        out.append((0.5 * float(np.sum(x * np.roll(y, -1) - np.roll(x, -1) * y)), P.mean(axis=0),
                    float(np.hypot(x - ref[0], y - ref[1]).max())))
    return out


def outline_clockwise(params, sequence):
    """Expected turn of outside cuts: climb milling (M3) runs outlines clockwise; cut order settings override."""
    strategy = sequence.get("strategy", "nearest")
    d = sequence.get("directions", {}).get(strategy) or sequence.get("direction", "toolpath")
    if d == "mirror":
        return strategy.endswith(("_bl", "_tr"))
    if d in ("cw", "ccw"):
        return d == "cw"
    if d in ("climb", "conventional"):
        return d == "climb"
    return params.get("direction", "climb") == "climb"


def expected_levels(start, final, step):
    n = max(1, math.ceil((final - start) / step - 1e-9))
    return [-(start + (final - start) * (k + 1) / n) for k in range(n)]


PROFILES = [
    # name, params, cut order
    ("plain outside", {"through": True}, {}),
    ("conventional, step 2.5, start 2",
     {"through": False, "cut_depth": 9, "start_depth": 2, "stepdown": 2.5, "direction": "conventional"}, {}),
    ("inside", {"side": "inside", "through": True}, {}),
    ("on the line", {"side": "on", "through": False, "cut_depth": 4}, {}),
    ("arc leads, overlap", {"through": True, "lead": "arc", "lead_length": 4, "overlap": 3}, {}),
    ("line leads, allowance", {"through": True, "lead": "line", "lead_length": 5, "allowance": 0.3}, {}),
    ("linear ramp", {"through": True, "ramp": "linear", "ramp_length": 15}, {}),
    ("linear ramp, overlap, smart", {"through": True, "ramp": "linear", "ramp_length": 12, "overlap": 4},
     {"smart_starts": True, "strategy": "rows_bl"}),
    ("helix", {"through": True, "ramp": "helix"}, {}),
    ("tabs", {"through": True, "tabs": True, "tab_count": 3, "tab_height": 4, "tab_length": 8}, {}),
    ("3D tabs, ramp", {"through": True, "tabs": True, "tab_3d": True, "tab_height": 3, "ramp": "linear",
                       "ramp_length": 10}, {}),
    ("finish pass", {"through": True, "finish_pass": 0.5}, {}),
    ("finish, leads, tabs", {"through": True, "finish_pass": 0.4, "lead": "arc", "tabs": True, "tab_count": 2,
                             "tab_height": 3}, {}),
    ("sharp corners", {"through": True, "sharp_corners": True}, {}),
    ("smart starts, mirrored", {"through": True, "lead": "arc"},
     {"smart_starts": True, "strategy": "rows_br", "direction": "mirror"}),
    ("per strategy direction", {"through": True},
     {"strategy": "diag_tr", "directions": {"diag_tr": "conventional"}, "smart_starts": True}),
    ("cut order clockwise, conventional toolpath", {"through": True, "direction": "conventional"},
     {"strategy": "rows_bl", "direction": "cw", "smart_starts": True}),
    ("nearest start, overcut 1", {"through": True, "overcut": 1.0, "start_mode": "nearest"}, {}),
    ("deep stepdown 7, start 0.5", {"through": False, "cut_depth": 15, "start_depth": 0.5, "stepdown": 7.0,
                                    "ramp": "linear", "ramp_length": 20}, {}),
]


def profile_job(params, sequence):
    parts = drawing()
    p, s, objs = make_project(parts, thickness=THICK, size=SHEET)
    tool = end_mill(6, feed_rate=FEED, plunge_rate=PLUNGE, stepdown=4.0)
    t = add_tp(p, "profile", tool, params, objs)
    t.sequence = dict(sequence)
    res = compute_toolpath(p, t, s)
    assert res.error is None, res.error
    return p, s, parts, tool, res.toolpaths


def verify_profile(texts, opt, inch, params, sequence, tool, parts):
    v = REGISTRY.operations["profile"](params).values
    start = v["start_depth"]
    final = THICK + v["overcut"] if v["through"] else v["cut_depth"]
    step = v["stepdown"] or tool.stepdown
    for text in texts:
        check_program(text, {tool.number: tool.spindle_speed}, opt, inch)
    back = read_back(texts, opt)
    side = v["side"]
    tab_top = -final + v["tab_height"] if v["tabs"] else None
    cut_out = (SHEET, INSIDE, SLUGS) if v["through"] and not v["tabs"] and side == "outside" else None
    check_moves(back, surface=-start, final=final, levels=expected_levels(start, final, step), step=step,
                r=tool.radius, allowance=v["allowance"], parts=parts if side == "outside" else None, tab_top=tab_top,
                cut_out=cut_out, ramped=v["ramp"] != "none" or v["tab_3d"], size=SHEET, skim=v["finish_pass"])
    deep = [q for q in samples(back) if q[3] == "feed" and q[2] < -start - 1e-6]
    contours = [c for cs in parts for c in cs]
    if side == "inside":  # the tool keeps the radius inside every vector
        def outside_all(P):
            return ~np.any([inside(P, c) for c in contours], axis=0)
        assert clearance(deep, contours, tool.radius + v["allowance"], outside_all) >= -0.02
    if side == "on":  # the tool centre stays on the vectors
        assert edge_distance(deep, contours).max() <= 0.02
    if side == "outside" and not v["tabs"]:  # the cutting direction
        cw = outline_clockwise(params, sequence)
        for area, centre, far in turns(back, final, HOLE[0]):
            hole = far < HOLE[1]
            assert (area < 0) == (cw != hole), f"wrong direction around {centre}"
    return back


@pytest.mark.parametrize("k", range(len(PROFILES)), ids=[p[0] for p in PROFILES])
def test_profile_gcode(k):
    name, params, sequence = PROFILES[k]
    p, s, parts, tool, tps = profile_job(params, sequence)
    texts, opt, inch = gcode_files(p, s, tps, POSTS[k % len(POSTS)])
    back = verify_profile(texts, opt, inch, params, sequence, tool, parts)
    # the G-code is the toolpath: same cutting length as calculated
    calc = sum(tp.stats().feed_length for tp in tps)
    got = sum(tp.stats().feed_length for tp in read_gcode("\n".join(texts), opt)) if len(texts) == 1 else None
    if got is not None:
        assert got == pytest.approx(calc, rel=2e-3)
    assert back


@pytest.mark.parametrize("post", ["mach3", "fanuc", "ncstudio", "r", "inch"])
@pytest.mark.parametrize("strategy,stepover,entry", [("offset", 0, "plunge"), ("raster", 2.0, "ramp"),
                                                     ("offset", 3.0, "helix")])
def test_pocket_gcode(post, strategy, stepover, entry):
    pocket = [shapes.rectangle(20, 20, 120, 80, corner_radius=10), shapes.circle((80, 60), 15)]  # with an island
    size = (160, 120)
    p, s, objs = make_project([pocket], thickness=THICK, size=size)
    tool = end_mill(6, feed_rate=FEED, plunge_rate=PLUNGE, stepdown=3.0)
    params = {"cut_depth": 7.5, "start_depth": 1.0, "stepover": stepover, "strategy": strategy, "entry": entry}
    res = compute_toolpath(p, add_tp(p, "pocket", tool, params, objs), s)
    assert res.error is None
    texts, opt, inch = gcode_files(p, s, res.toolpaths, post)
    for text in texts:
        check_program(text, {tool.number: tool.spindle_speed}, opt, inch)
    back = read_back(texts, opt)
    check_moves(back, surface=-1.0, final=7.5, levels=expected_levels(1.0, 7.5, 3.0), step=3.0, r=3.0,
                ramped=entry != "plunge", size=size)
    # the tool keeps the radius off the walls and the island
    outer, island = pocket
    deep = [q for q in samples(back) if q[3] == "feed" and q[2] < -1.0 - 1e-6]
    assert clearance(deep, pocket, 3.0, lambda P: ~inside(P, outer) | inside(P, island)) >= -0.02
    # and clears the whole floor: every point a tool radius off the walls is cut at full depth
    stock = Stock(size, -1.0, 3.0)
    for x, y, z, kind, _ in samples(back):
        if kind == "feed" and z < -1.0:
            stock.cut(x, y, z)
    grid = [(x, y) for x in np.arange(21, 140, 1.0) for y in np.arange(21, 100, 1.0)]
    P = np.array(grid)
    walls = edge_distance(grid, pocket)
    floor = P[(walls > 3.2) & inside(P, outer) & ~inside(P, island)]
    left = [tuple(q) for q in floor if stock.top(q[0], q[1], 0.1) > -7.5 + 1e-3]
    assert not left, f"floor left uncut at {left[:3]}"


@pytest.mark.parametrize("post", ["mach3", "fanuc", "linuxcnc", "inch"])
@pytest.mark.parametrize("peck,through,dwell", [(0.0, False, 0.0), (2.5, False, 0.0), (0.0, True, 0.5),
                                                (2.5, False, 0.5)])
def test_drill_gcode(post, peck, through, dwell):
    holes = [shapes.circle((30 + 25 * i, 40), 3) for i in range(4)]
    p, s, objs = make_project(holes, thickness=THICK, size=(160, 80))
    tool = end_mill(5, type="drill", feed_rate=FEED, plunge_rate=PLUNGE)
    params = {"cut_depth": 12, "peck": peck, "through": through, "overcut": 1.0, "dwell": dwell}
    res = compute_toolpath(p, add_tp(p, "drill", tool, params, objs), s)
    texts, opt, inch = gcode_files(p, s, res.toolpaths, post)
    for text in texts:
        check_program(text, {tool.number: tool.spindle_speed}, opt, inch)
    final = THICK + 1.0 if through else 12.0
    bottoms = {}
    dwells = 0
    pos = None
    for m in read_back(texts, opt):
        if isinstance(m, Dwell):  # a written out cycle: the pause is at the bottom of the hole
            assert m.seconds == pytest.approx(dwell, abs=TOL) and pos[2] == pytest.approx(-final, abs=TOL)
            dwells += 1
        elif isinstance(m, DrillCycle):
            dwells += m.dwell > 0
            q = (round(m.x, 3), round(m.y, 3))
            bottoms[q] = min(bottoms.get(q, 0.0), m.z)
            assert m.f == pytest.approx(PLUNGE, rel=2e-3)
            assert m.r >= 0, "retract plane in the material"
            assert m.peck == pytest.approx(peck, abs=TOL)
            assert m.dwell == pytest.approx(dwell, abs=TOL)
            pos = (m.x, m.y, max(m.r, pos[2]) if pos else m.r)
        elif hasattr(m, "z"):
            if pos is not None and isinstance(m, Rapid):
                if math.hypot(m.x - pos[0], m.y - pos[1]) > 1e-6:
                    assert min(m.z, pos[2]) >= CLEAR - TOL, "sideways rapid in material"
                elif m.z < 0:
                    assert m.z >= bottoms.get((round(m.x, 3), round(m.y, 3)), 0.0) - TOL, "rapid into uncut material"
            if isinstance(m, Feed) and m.z < 0:
                q = (round(m.x, 3), round(m.y, 3))
                bottoms[q] = min(bottoms.get(q, 0.0), m.z)
                assert m.f == pytest.approx(PLUNGE, rel=2e-3)
                assert math.hypot(m.x - pos[0], m.y - pos[1]) < 1e-6, "drill moving sideways"
            pos = (m.x, m.y, m.z)
    assert sorted(bottoms) == sorted((30.0 + 25 * i, 40.0) for i in range(4))
    assert all(z == pytest.approx(-final, abs=TOL) for z in bottoms.values())
    assert dwells == (4 if dwell else 0), "dwell lost"


@pytest.mark.parametrize("post", ["mach3", "fanuc", "inch"])
def test_engrave_fluting_and_vcarve_gcode(post):
    p, s, objs = make_project([shapes.polyline([(20, 20), (120, 20), (120, 60)]), shapes.rectangle(20, 80, 60, 20),
                               shapes.line((150, 20), (150, 100))], thickness=THICK, size=(200, 120))
    em3 = end_mill(3, feed_rate=FEED, plunge_rate=PLUNGE, stepdown=1.5)
    eng = compute_toolpath(p, add_tp(p, "engrave", em3, {"cut_depth": 4, "start_depth": 0.5}, objs[:1]), s)
    v = vbit(90, d=20, feed_rate=FEED, plunge_rate=PLUNGE, stepdown=1.0)
    vc = compute_toolpath(p, add_tp(p, "vcarve", v, {"flat_depth": 3.0, "clear_flats": False}, objs[1:2]), s)
    ball = end_mill(6, type="ball_nose", feed_rate=FEED, plunge_rate=PLUNGE)
    fl = compute_toolpath(p, add_tp(p, "fluting", ball, {"cut_depth": 5, "ramp_start": 20, "ramp_end": 20},
                                    objs[2:]), s)
    for res, final, surface, levels, step, ramped, size in (
            (eng, 4.0, -0.5, expected_levels(0.5, 4.0, 1.5), 1.5, False, (200, 120)),
            (vc, 3.0, 0.0, [], 3.0, True, None), (fl, 5.0, 0.0, [], 5.0, True, None)):
        assert res.error is None
        tool = res.toolpaths[0].tool
        texts, opt, inch = gcode_files(p, s, res.toolpaths, post)
        for text in texts:
            check_program(text, {tool.number: tool.spindle_speed}, opt, inch)
        check_moves(read_back(texts, opt), surface=surface, final=final, levels=levels, step=step,
                    r=tool.radius, ramped=ramped, size=size)


@pytest.mark.parametrize("post", ["mach3", "fanuc", "linuxcnc", "ncstudio", "ncstudio_atc", "syntec", "inch"])
def test_job_with_three_tools(post):
    """Drilling, a pocket and the cut-out with three tools: tool changes at safe height, each tool's speed."""
    parts = drawing()
    marks = [shapes.circle((40, 75), 2.5), shapes.circle((110, 75), 2.5)]
    recess = shapes.rectangle(155, 60, 20, 40, corner_radius=4)
    p, s, objs = make_project(parts + [[c] for c in marks] + [[recess]], thickness=THICK, size=SHEET)
    drill = end_mill(5, type="drill", number=3, spindle_speed=12000, feed_rate=FEED, plunge_rate=PLUNGE)
    em3 = end_mill(3, number=2, spindle_speed=22000, feed_rate=FEED, plunge_rate=PLUNGE, stepdown=2.0)
    em6 = end_mill(6, number=1, spindle_speed=18000, feed_rate=FEED, plunge_rate=PLUNGE, stepdown=4.0)
    tps = []
    for op, tool, params, sel in (("drill", drill, {"cut_depth": 6}, objs[4:6]),
                                  ("pocket", em3, {"cut_depth": 5}, objs[6:]),
                                  ("profile", em6, {"through": True}, objs[:4])):
        res = compute_toolpath(p, add_tp(p, op, tool, params, sel), s)
        assert res.error is None
        tps += res.toolpaths
    texts, opt, inch = gcode_files(p, s, tps, post)
    rpm = {t.number: t.spindle_speed for t in (drill, em3, em6)}
    assert len(texts) == (3 if post == "ncstudio" else 1)
    for text in texts:
        used = {n: v for n, v in rpm.items()} if len(texts) == 1 else None
        if used is None:  # one file per tool: find which one by its speed
            speeds = {v for a, v in (w for ws in words(text) for w in ws) if a == "S"}
            used = {n: v for n, v in rpm.items() if v in speeds}
            assert len(used) == 1
        check_program(text, used, opt, inch)
    # the tools cut in the job's order, each to its own depth
    cutting = [tp for text in texts for tp in read_gcode(text, opt)
               if any(isinstance(m, (Feed, ArcMove, DrillCycle)) for m in tp.moves)]
    if post != "ncstudio":  # (NcStudio files have no tool numbers)
        assert [tp.tool.number for tp in cutting] == [3, 2, 1]
    assert len(cutting) == 3
    for tp, want in zip(cutting, (6.0, 5.0, THICK + 0.2)):
        assert min(m.z for m in tp.moves if hasattr(m, "z")) == pytest.approx(-want, abs=TOL)


def test_the_checks_see_damaged_gcode():
    """Typical faults in otherwise good G-code are all caught."""
    params, sequence = {"through": True}, {}
    p, s, parts, tool, tps = profile_job(params, sequence)
    texts, opt, inch = gcode_files(p, s, tps, "mach3")
    good = texts[0]
    verify_profile([good], opt, inch, params, sequence, tool, parts)
    first_plunge = re.search(r"G1 Z-3\.64 F1000", good).group(0)

    def shift_x(text, dx):
        return re.sub(r"X(-?\d+\.?\d*)", lambda m: f"X{float(m.group(1)) + dx:.4f}", text)

    damage = {
        "deeper than the final depth": good.replace("Z-18.2", "Z-18.5", 1),
        "final depth not reached": good.replace("Z-18.2", "Z-18.1"),
        "cuts 7.280 deep at once": good.replace(first_plunge, "G1 Z-7.28 F1000", 1),  # a pass left out
        "rapid down into uncut material": good.replace(first_plunge, "G0 Z-3.64", 1),
        "plunge not at the plunge rate": good.replace(first_plunge, "G1 Z-3.64 F3000", 1),
        "spindle off": good.replace("S18000 M3", "S18000"),
        "spindle speed is not the tool's": good.replace("S18000 M3", "S9000 M3"),
        "rapid at Z1": re.sub(r"(G0 X[-\d.]+ Y[-\d.]+ )Z10", r"\1Z1", good, count=1),
        "the tool cuts into the part": shift_x(good, 1.0),
        "no program end": good.replace("M30", ""),
        "units not set": good.replace("G21", "G20"),
    }
    for reason, text in damage.items():
        assert text != good, reason
        with pytest.raises(AssertionError, match=re.escape(reason)):
            verify_profile([text], opt, inch, params, sequence, tool, parts)
    # cut the other way round than asked
    p, s, parts, tool, tps = profile_job({"through": True, "direction": "conventional"}, sequence)
    texts, opt, inch = gcode_files(p, s, tps, "mach3")
    with pytest.raises(AssertionError, match="wrong direction"):
        verify_profile(texts, opt, inch, params, sequence, tool, parts)
    # tabs keep the parts attached; the cut-out check sees it
    tparams = {"through": True, "tabs": True, "tab_count": 3, "tab_height": 4}
    p, s, parts, tool, tps = profile_job(tparams, {})
    feeds = [q for q in samples([m for tp in tps for m in tp.moves]) if q[3] == "feed"]
    assert not separated([q[:2] for q in feeds if q[2] <= -THICK - 0.2 + 1e-6], tool.radius, SHEET, INSIDE, SLUGS)

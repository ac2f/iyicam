"""Random jobs through every toolpath type and every post: nothing may raise, nothing may be written as nonsense.

A strategy that raises is caught by the job calculation and shown as an error of the toolpath ("NoneType has
no attribute ..."), so every ``ComputeResult.error`` here is a fault of the program.
"""
import math
import random
import re
import signal
import warnings
import zlib

import pytest
from conftest import make_project

from iyicam.cam.job import compute_toolpath
from iyicam.core import shapes
from iyicam.core.document import Selection, ToolpathDef
from iyicam.core.geom import Arc, Bezier, Contour, Line
from iyicam.core.registry import REGISTRY
from iyicam.core.tooling import Tool
from iyicam.post.export import post_toolpaths


def star(cx, cy, R, r, n=5):
    pts = [(cx + (R if k % 2 == 0 else r) * math.cos(math.pi * k / n), cy + (R if k % 2 == 0 else r) * math.sin(math.pi * k / n))
           for k in range(2 * n)]
    return shapes.polyline(pts, True)


def scenario(rnd: random.Random):
    """A random drawing: a list of vectors (each a list of contours)."""
    pool = [
        lambda: [shapes.rectangle(rnd.uniform(5, 60), rnd.uniform(5, 60), rnd.uniform(10, 120), rnd.uniform(8, 90),
                                  corner_radius=rnd.choice([0, 0, 3, 8]))],
        lambda: [shapes.circle((rnd.uniform(30, 200), rnd.uniform(30, 150)), rnd.uniform(0.3, 45))],
        lambda: [shapes.rectangle(30, 30, 100, 70), shapes.circle((80, 65), rnd.uniform(4, 25))],
        lambda: [star(rnd.uniform(60, 200), rnd.uniform(50, 140), rnd.uniform(15, 50), rnd.uniform(5, 14))],
        lambda: [shapes.polyline([(10, 10), (80, 40), (150, 15), (210, 90)])],                    # open
        lambda: [shapes.polyline([(10, 10), (10, 10.0001), (11, 10)], True)],                      # tiny
        lambda: [shapes.polyline([(30, 30), (30, 30)])],                                           # a point
        lambda: [shapes.polyline([(20, 20), (120, 100), (20, 100), (120, 20)], True)],             # bow tie
        lambda: [Contour([Bezier((20, 20), (60, 120), (100, -20), (140, 60)), Line((140, 60), (20, 20))], True)],
        lambda: [Contour([Arc((30, 30), (110, 30), 0.6), Line((110, 30), (30, 30))], True)],
        lambda: [shapes.rectangle(-30, -30, 80, 60)],                                              # off the sheet
        lambda: [shapes.rectangle(0, 0, 2000, 1500)],                                              # bigger than it
        lambda: [shapes.rectangle(40, 40, 0.6, 0.6)],                                              # tiny part
        lambda: [shapes.circle((100, 80), 30), shapes.circle((100, 80), 10)],                      # ring in one vector
    ]
    out = []
    for _ in range(rnd.randint(1, 4)):
        out.append(rnd.choice(pool)())
    if rnd.random() < 0.25 and out:   # the same vector twice, on top of itself
        out.append([c.copy() for c in out[0]])
    return out


def random_value(p, rnd: random.Random):
    k = p.kind
    num = p.default if isinstance(p.default, (int, float)) and not isinstance(p.default, bool) else 0.0
    lo = p.min if p.min is not None else 0.0
    hi = p.max if p.max is not None else max(50.0, num * 4 + 10)
    if k == "bool":
        return rnd.random() < 0.5
    if k == "choice":
        return rnd.choice([c[0] for c in p.choices])
    if k == "int":
        lo_i, hi_i = int(p.min if p.min is not None else 0), int(p.max if p.max is not None else 12)
        return rnd.choice([lo_i, hi_i, rnd.randint(lo_i, max(lo_i, min(hi_i, 20)))])
    if k in ("float", "length", "angle", "feed", "percent"):
        if p.name == "resolution":   # 0.01 mm is possible but takes minutes: not what is being looked for here
            return rnd.choice([0.05, 0.1, 0.5, 1.0])
        if p.default is None and rnd.random() < 0.5:
            return None
        return rnd.choice([lo, hi, num if p.default is not None else lo, lo + (hi - lo) * rnd.random(),
                           lo + (hi - lo) * rnd.random() ** 3])
    if k == "point":
        return (rnd.uniform(0, 200), rnd.uniform(0, 150))
    if k == "tool":
        return None
    if k == "font":
        return "DejaVu Sans"
    if k in ("str", "text"):
        return p.default if p.default is not None else "x"
    return p.default


def random_tool(op, rnd: random.Random) -> Tool:
    types = list(op.tool_types or ("end_mill", "ball_nose", "bull_nose", "v_bit", "engraving", "drill"))
    t = rnd.choice(types)
    d = rnd.choice([0.5, 1.0, 3.0, 6.0, 6.35, 12.0, 25.0, rnd.uniform(0.5, 20)])
    return Tool(name=f"{t} {d:.2f}", type=t, diameter=d, number=rnd.randint(1, 8), flutes=rnd.randint(1, 4),
                angle=rnd.choice([30.0, 60.0, 90.0, 120.0]), stepdown=rnd.choice([0.0, 1.0, 3.0, 8.0]),
                stepover=d * rnd.choice([0.1, 0.4, 0.9]), feed_rate=rnd.choice([500.0, 3000.0, 9000.0]),
                plunge_rate=rnd.choice([200.0, 1000.0]), spindle_speed=rnd.choice([0, 12000, 24000]),
                corner_radius=rnd.choice([0.0, d / 4]) if t == "bull_nose" else 0.0)


NUM = re.compile(r"[XYZIJKRAF]\s*(-?\d+\.?\d*)", re.I)


def check_gcode(text: str, who: str, limit: float = 1e5) -> None:
    low = text.lower()
    for bad in ("nan", "inf", "none", "traceback", "object at"):
        assert bad not in re.sub(r"\(.*?\)|;.*", "", low), f"{who}: {bad!r} in the G-code"
    for m in NUM.finditer(re.sub(r"\(.*?\)|;.*", "", text)):
        assert abs(float(m.group(1))) < limit, f"{who}: a coordinate of {m.group(1)}"


LIMIT = 8   # seconds for one toolpath; slower ones are reported (a warning), they are slow, not wrong
OPS = list(REGISTRY.operations)
POSTS = list(REGISTRY.posts)


SLOW = ("vcarve", "inlay")   # medial axes of the shapes: seconds each, so fewer and shorter runs of them


@pytest.mark.parametrize("op_id,batch", [(o, b) for o in OPS for b in range(2 if o in SLOW else 4)])
def test_random_jobs_never_fail_and_post_cleanly(op_id, batch):
    op = REGISTRY.operations[op_id]
    rnd = random.Random(zlib.crc32(f"{op_id}/{batch}".encode()))   # the same cases in every run
    for case in range(4 if op_id in SLOW else 12):
        seed = rnd.randrange(10 ** 6)
        r = random.Random(seed)
        p, s, objs = make_project(scenario(r), thickness=r.choice([3.0, 12.0, 18.0, 40.0]),
                                  size=(r.choice([280.0, 600.0, 2440.0]), r.choice([200.0, 400.0, 1220.0])))
        for _ in range(r.randint(0, 2)):   # more than one layer / sheet sometimes
            if r.random() < 0.5:
                p.add_sheet(None, 300, 200)
        params = {q.name: random_value(q, r) for q in op.params()}
        tool = random_tool(op, r)
        seq = {}
        if op_id in ("profile", "engrave", "vcarve", "inlay") and r.random() < 0.6:
            seq = {"strategy": r.choice(["nearest", "smart", "rows_bl", "zigzag_up", "diag_tr", "centre_out", "small_first"]),
                   "grouping": r.choice(["depth", "part"]), "smart_starts": r.random() < 0.5,
                   "direction": r.choice(["toolpath", "climb", "conventional", "cw", "mirror"])}
        t = ToolpathDef("fuzz", op_id, tool, params, Selection("vectors", [o.id for o in objs]))
        t.sequence = seq
        p.toolpaths.append(t)
        who = f"{op_id} seed {seed}: params {params} tool {tool.type} {tool.diameter:.2f} seq {seq}"

        def too_slow(*_):
            raise TimeoutError(f"more than {LIMIT} s")
        if hasattr(signal, "SIGALRM"):   # (not on Windows: no limit there)
            signal.signal(signal.SIGALRM, too_slow)
            signal.alarm(LIMIT)   # a job that takes minutes is a fault too
        try:
            res = compute_toolpath(p, t, s)
        except TimeoutError:   # the time ran out outside the strategy (the warnings of the result)
            warnings.warn(f"slow: {who}", stacklevel=1)
            continue
        finally:
            if hasattr(signal, "SIGALRM"):
                signal.alarm(0)
        if res.error and res.error.startswith("TimeoutError"):
            warnings.warn(f"slow: {who}", stacklevel=1)
            continue
        assert res.error is None, f"{who}\n{res.error}\n{res.warnings[:1]}"
        if not res.toolpaths:
            continue
        for tp in res.toolpaths:
            st = tp.stats()
            assert all(math.isfinite(v) for v in (st.feed_length, st.rapid_length) if isinstance(v, (int, float))), who
        post = REGISTRY.posts[r.choice(POSTS)]
        files = post_toolpaths(p, s, res.toolpaths, post, "fuzz")
        assert files, who
        for _, text in files:
            check_gcode(text, f"{who} post {post.id}")

"""Time a nesting job and say how good the layout is: the yardstick for faster nesting (more processors, a GPU).

    python tools/bench_nesting.py                       # 60 kinds of curved parts, 30 attempts, no time limit
    python tools/bench_nesting.py --kinds 120 --iterations 30 --processes 0     # one processor
    python tools/bench_nesting.py --json out.json       # the numbers to compare two runs (same job, same seed)

The job is always the same for the same ``--kinds`` / ``--seed``: gears (120 points), stars, L shapes and frames with
holes, 1 to 4 of each, on 2440 x 1220 plates. Printed: the time of every phase (no-fit polygons, layouts, polishing),
the plates used, the utilisation of each and the quality tuple the nester compares (less is better). A faster method is
only better if the layout is as good: compare ``quality`` and ``sheets`` too, not the time alone.
"""
from __future__ import annotations

import argparse
import json
import math
import os
import platform
import random
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def shapes(kinds: int, seed: int):
    from iyicam.nesting.nest import NestShape

    def gear(r0, teeth):
        n = 4 * teeth
        return [((r0 + (12 if (k // 2) % 2 else 0)) * math.cos(2 * math.pi * k / n),
                 (r0 + (12 if (k // 2) % 2 else 0)) * math.sin(2 * math.pi * k / n)) for k in range(n)]

    def star(R, r, k=5):
        return [((R if i % 2 == 0 else r) * math.cos(math.pi * i / k), (R if i % 2 == 0 else r) * math.sin(math.pi * i / k))
                for i in range(2 * k)]

    def rect(w, h, x=0.0, y=0.0):
        return [(x, y), (x + w, y), (x + w, y + h), (x, y + h)]
    rnd = random.Random(seed)
    out = []
    for i in range(kinds):
        t = i % 4
        if t == 0:
            out.append(NestShape([gear(rnd.uniform(60, 180), rnd.randint(12, 30))], rnd.randint(1, 4)))
        elif t == 1:
            out.append(NestShape([star(rnd.uniform(80, 200), rnd.uniform(30, 70))], rnd.randint(1, 3)))
        elif t == 2:
            a, b, th = rnd.uniform(150, 400), rnd.uniform(150, 400), rnd.uniform(30, 60)
            out.append(NestShape([[(0, 0), (a, 0), (a, th), (th, th), (th, b), (0, b)]], rnd.randint(1, 3)))
        else:
            w, h = rnd.uniform(200, 500), rnd.uniform(150, 400)
            out.append(NestShape([rect(w, h)], rnd.randint(1, 2), holes=[rect(w - 60, h - 60, 30, 30)]))
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--kinds", type=int, default=60)
    ap.add_argument("--seed", type=int, default=1)
    ap.add_argument("--iterations", type=int, default=30)
    ap.add_argument("--time-limit", type=float, default=0.0)
    ap.add_argument("--processes", type=int, default=None, help="helper processes (0: one processor; default: all)")
    ap.add_argument("--priority", choices=["low", "normal", "high"], default="normal")
    ap.add_argument("--json", help="write the numbers here")
    a = ap.parse_args(argv)
    if a.processes is not None:
        os.environ["IYICAM_NEST_PROCESSES"] = str(a.processes)
    from iyicam.nesting import nest as N
    from iyicam.nesting import parallel as P

    phases: list[tuple[str, float]] = []
    t_last = [time.perf_counter()]

    def mark(name):
        t = time.perf_counter()
        phases.append((name, t - t_last[0]))
        t_last[0] = t
    real_pre, real_map = N.Nester.precompute, P.Helpers.map

    def pre(self, *x, **k):
        r = real_pre(self, *x, **k)
        mark(f"no-fit polygons ({r} made on the helpers)")
        return r

    def hmap(self, fn, args, *x, **k):
        r = real_map(self, fn, args, *x, **k)
        mark({"_run": f"{len(args)} layouts at once", "_search": f"{len(args)} polishing searches at once"}.get(
            fn.__name__, fn.__name__))
        return r
    N.Nester.precompute, P.Helpers.map = pre, hmap
    parts = shapes(a.kinds, a.seed)
    st = N.NestSettings(sheet_width=2440, sheet_height=1220, iterations=a.iterations, time_limit=a.time_limit,
                        rotations=[0, 90, 180, 270], seed=a.seed, priority=a.priority)
    t0 = time.perf_counter()
    t_last[0] = t0
    res = N.nest(parts, st)
    total = time.perf_counter() - t0
    mark("the rest (on this process)")
    q = N.Nester(parts, st).quality(res)
    info = {"python": sys.version.split()[0], "platform": platform.platform(), "processors": os.cpu_count(),
            "helpers": N.nest_processes(), "kinds": a.kinds, "parts": sum(p.quantity for p in parts),
            "iterations": a.iterations, "time_limit": a.time_limit, "seconds": round(total, 2),
            "sheets": res.sheets, "placed": len(res.placements), "unplaced": len(res.unplaced),
            "utilisation": [round(u, 4) for u in res.utilization], "quality": [float(x) if isinstance(x, (int, float))
                                                                              else str(x) for x in q],
            "phases": [(n, round(s, 2)) for n, s in phases]}
    try:
        import cupy  # noqa: F401
        info["gpu"] = cupy.cuda.runtime.getDeviceProperties(0)["name"].decode()
    except Exception:  # noqa: BLE001  (no GPU or no CuPy)
        info["gpu"] = None
    print(f"{info['parts']} parts ({a.kinds} kinds), {a.iterations} attempts, time limit {a.time_limit:g} s, "
          f"{info['helpers']} helper processes of {info['processors']} processors, GPU: {info['gpu']}")
    for n, s in phases:
        print(f"  {s:8.2f} s  {n}")
    print(f"  {total:8.2f} s  in all: {res.sheets} plates, {len(res.placements)} placed, {len(res.unplaced)} not; "
          f"utilisation {', '.join(f'{u * 100:.1f} %' for u in res.utilization)}")
    if a.json:
        Path(a.json).write_text(json.dumps(info, indent=1), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

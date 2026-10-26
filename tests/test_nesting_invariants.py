"""Nesting never adds or loses a vector, whatever is nested however often, with whatever settings.

Every vector is fingerprinted by what does not change when it is moved or turned (its contours: how many,
open or closed, area, length); the fingerprints of everything in the project before and after must be the
same, plus the copies that were asked for.
"""
import math
import random
from collections import Counter

import pytest
from conftest import make_project

from iyicam.core import shapes
from iyicam.core.document import Project, VectorObject
from iyicam.core.stock import StockItem
from iyicam.nesting.nest import NestSettings
from iyicam.nesting.parts import group_parts, nest_objects


def fingerprint(o: VectorObject):
    cs = []
    for c in o.contours:
        poly = c.flatten(0.01)
        a = abs(sum(x0 * y1 - x1 * y0 for (x0, y0), (x1, y1) in zip(poly, poly[1:] + poly[:1]))) / 2
        per = sum(math.dist(p, q) for p, q in zip(poly, poly[1:] + poly[:1]))
        cs.append((c.closed, round(a, 1), round(per, 1)))
    return tuple(sorted(cs))


def census(project: Project) -> Counter:
    return Counter(fingerprint(v) for s in project.sheets for v in s.vectors)


def random_part(rnd, i):
    k = rnd.choice("rcLtpHn")
    if k == "r":
        return [shapes.rectangle(0, 0, rnd.randint(40, 300), rnd.randint(30, 200), corner_radius=rnd.choice([0, 0, 6]))]
    if k == "c":
        return [shapes.circle((0, 0), rnd.randint(20, 120))]
    if k == "L":
        w, h = rnd.randint(100, 300), rnd.randint(100, 250)
        return [shapes.polyline([(0, 0), (w, 0), (w, h / 3), (w / 3, h / 3), (w / 3, h), (0, h)], True)]
    if k == "t":
        w = rnd.randint(80, 300)
        return [shapes.polyline([(0, 0), (w, 0), (w / 2, rnd.randint(60, 250))], True)]
    if k == "p":  # a part with a hole
        w, h = rnd.randint(120, 300), rnd.randint(100, 250)
        return [shapes.rectangle(0, 0, w, h), shapes.circle((w / 2, h / 2), min(w, h) / 5)]
    if k == "H":  # an open line (engraving) next to nothing
        return [shapes.polyline([(0, 0), (rnd.randint(40, 200), rnd.randint(10, 100))])]
    return [shapes.polyline([(0, 0), (150, 0), (150, 100), (100, 100), (100, 30), (50, 30), (50, 100), (0, 100)], True)]


def fresh(seed, n=12, sheets=1):
    rnd = random.Random(seed)
    parts = []
    for i in range(n):
        cs = random_part(rnd, i)
        dx, dy = rnd.uniform(-200, 2500), rnd.uniform(-200, 1800)
        from iyicam.core.geom import Transform
        cs = [c.transformed(Transform.translate(dx, dy)) for c in cs]
        parts.append(cs)
    p, s, objs = make_project(parts, size=(1200, 800))
    for k in range(1, sheets):
        sh = p.add_sheet(None, 1200, 800)
        extra = [VectorObject([c.copy() for c in o.contours], p.layers[0].id) for o in objs[:3]]
        sh.vectors.extend(extra)
    return p, p.sheets[0], list(p.sheets[0].vectors)


def expected_after(before: Counter, out):
    """The census after nesting: what was there, plus a copy of a part for every copy placed beyond the original
    (the original is the copy 0: when that one was not placed it stays where it was)."""
    want = Counter(before)
    copies: dict[int, set] = {}
    for pl in out.result.placements:
        copies.setdefault(pl.shape, set()).add(pl.copy)
    for i, placed in copies.items():
        extra = len(placed) - (1 if 0 in placed else 0)
        for o in out.parts[i].objects:
            want[fingerprint(o)] += extra
    return want


@pytest.mark.parametrize("seed", range(12))
def test_nesting_and_renesting_keeps_every_vector(seed):
    rnd = random.Random(1000 + seed)
    p, s, objs = fresh(seed, n=rnd.randint(4, 10), sheets=rnd.choice([1, 2]))
    for round_ in range(3):
        pool = [v for sh in p.sheets for v in sh.vectors] if rnd.random() < 0.3 else list(
            rnd.choice(p.sheets).vectors)
        pool = [v for v in pool if rnd.random() < 0.9] or pool
        if not pool:
            continue
        parts = group_parts(pool)
        quantities = {pt.root.id: rnd.choice([1, 1, 1, 2, 3, 0]) for pt in parts}
        before = census(p)
        kw = {}
        mode = rnd.choice(["plain", "stock", "sheets", "both"])
        if mode in ("stock", "both"):
            kw["stock"] = [StockItem("a", rnd.randint(300, 900), rnd.randint(250, 700), None, rnd.choice([0, 1, 2]),
                                     rnd.random() < 0.5)]
        if mode in ("sheets", "both"):
            kw["use_sheets"] = rnd.sample(p.sheets, k=rnd.randint(1, len(p.sheets)))
        if kw:
            kw["new_sheets"] = rnd.random() < 0.7
        st = NestSettings(1200, 800, rnd.choice([5, 10]), rnd.choice([4, 8]),
                          rnd.choice([[0], [0, 90], [0, 90, 180, 270], [0, 45, 90, 135, 180, 225, 270, 315]]),
                          rnd.choice(["left", "bottom", "compact"]), iterations=rnd.choice([1, 1, 2]),
                          squeeze_angle=rnd.choice([0, 0, 6]), squeeze_margin=rnd.choice([0, 0, 3]))
        out = nest_objects(p, pool, st, quantities, [], choose=lambda a, b: rnd.random() < 0.5, **kw)
        after = census(p)
        want = expected_after(before, out)
        assert after == want, (f"seed {seed} round {round_} mode {mode}: "
                               f"extra {after - want}, missing {want - after}")
        # nothing is left on no sheet and no vector is on two sheets
        ids = [v.id for sh in p.sheets for v in sh.vectors]
        assert len(ids) == len(set(ids)), "a vector is on a sheet twice"
        assert out.placed + out.unplaced == sum(quantities.values())


def test_vectors_drawn_twice_on_top_of_each_other_are_reported():
    """Two identical vectors in the same place (a common fault of drawings) are two parts: nesting spreads them
    out, which looks like copies. They are found beforehand."""
    from iyicam.nesting.parts import find_duplicates
    a = shapes.rectangle(10, 10, 100, 60)
    p, s, objs = make_project([[a], [a.copy()], [shapes.circle((300, 50), 30)]], size=(600, 400))
    dups = find_duplicates(objs)
    assert [{o.id for o in g} for g in dups] == [{objs[0].id, objs[1].id}]
    assert find_duplicates([objs[0], objs[2]]) == []

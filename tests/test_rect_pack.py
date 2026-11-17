"""Rectangles on rectangular plates are packed by the rectangle packer (nesting/rects.py), and nothing else is."""
import math
import random

from iyicam.nesting import rects
from iyicam.nesting.nest import Container, Nester, NestSettings, NestShape, nest


def rounded(w, h, r=0.8):
    pts = []
    for cx, cy, a0 in ((w - r, h - r, 0), (r, h - r, 90), (r, r, 180), (w - r, r, 270)):
        for k in range(5):
            a = math.radians(a0 + k * 90 / 4)
            pts.append((cx + r * math.cos(a), cy + r * math.sin(a)))
    return pts


def placed_boxes(shapes, res):
    out = {}
    for p in res.placements:
        pts = shapes[p.shape].polys[0]
        a = math.radians(p.angle)
        c, s = math.cos(a), math.sin(a)
        q = [(x * c - y * s + p.x, x * s + y * c + p.y) for x, y in pts]
        out.setdefault(p.sheet, []).append((min(x for x, _ in q), min(y for _, y in q), max(x for x, _ in q), max(y for _, y in q)))
    return out


def check(shapes, res, plates, margin, spacing):
    for sheet, boxes in placed_boxes(shapes, res).items():
        W, H = plates[res.stock[sheet]]
        for i, b in enumerate(boxes):
            assert b[0] >= margin - 1e-6 and b[1] >= margin - 1e-6 and b[2] <= W - margin + 1e-6 and b[3] <= H - margin + 1e-6
            for c in boxes[:i]:
                gap = max(b[0] - c[2], c[0] - b[2], b[1] - c[3], c[1] - b[3])
                assert gap >= spacing - 1e-6, (b, c, gap)


def job(seed=3, n=60):
    r = random.Random(seed)
    sizes = [(290, 130), (290, 160), (200, 900), (300, 400), (150, 150), (1000, 450)]
    shapes = []
    for _ in range(n):
        w, h = r.choice(sizes)
        shapes.append(NestShape([rounded(w, h)], 1))
    return shapes


def test_rounded_rectangles_are_packed_as_boxes_and_all_fit():
    shapes = job()
    plates = [(1250, 3200)] * 3
    st = NestSettings(margin=10, spacing=8, rotations=[0, 90], iterations=4,
                      containers=[Container.rect(1250, 3200, quantity=3)])
    n = Nester(shapes, st)
    assert rects.eligible(n)
    res = nest(shapes, st)
    assert not res.unplaced
    check(shapes, res, plates, 10, 8)


def test_the_rectangle_packer_is_not_worse_than_the_free_form_layout():
    shapes = job(seed=5, n=80)
    st = NestSettings(margin=10, spacing=8, rotations=[0, 90, 180, 270], iterations=3,
                      containers=[Container.rect(1250, 3200, quantity=0)])
    res = nest(shapes, st)
    n = Nester(shapes, st)
    free = n.run_order(n.default_order())
    assert (len(res.unplaced), res.sheets) <= (len(free.unplaced), free.sheets)
    check(shapes, res, [(1250, 3200)] * res.sheets, 10, 8)


def test_turned_parts_end_up_where_the_layout_says():
    shapes = [NestShape([rounded(900, 200)], 1) for _ in range(4)]
    st = NestSettings(margin=10, spacing=8, rotations=[90], iterations=2,
                      containers=[Container.rect(1000, 1000, quantity=1)])
    res = nest(shapes, st)
    assert not res.unplaced and all(p.angle == 90.0 for p in res.placements)
    check(shapes, res, [(1000, 1000)], 10, 8)


def test_other_shapes_are_left_to_the_free_form_nesting():
    circle = [(50 * math.cos(a * math.pi / 18), 50 * math.sin(a * math.pi / 18)) for a in range(36)]
    shapes = [NestShape([circle], 4)]
    n = Nester(shapes, NestSettings(containers=[Container.rect(500, 500, quantity=1)]))
    assert not rects.eligible(n)
    n = Nester([NestShape([rounded(100, 50)], 2)], NestSettings(rotations=[0, 33], containers=[Container.rect(500, 500)]))
    assert not rects.eligible(n)              # (a turn that is not a right angle)
    n = Nester([NestShape([rounded(100, 50)], 2)], NestSettings(arrange="rows", containers=[Container.rect(500, 500)]))
    assert not rects.eligible(n)              # (strips are what was asked for)


def test_a_plate_that_is_too_small_leaves_the_part_unplaced_and_the_rest_is_placed():
    shapes = [NestShape([rounded(2000, 100)], 1), NestShape([rounded(100, 100)], 3)]
    st = NestSettings(rotations=[0, 90], containers=[Container.rect(500, 500, quantity=1)])
    res = nest(shapes, st)
    assert res.unplaced == [(0, 0)] and len(res.placements) == 3


def test_the_gap_between_neighbours_is_exactly_the_spacing_and_the_edge_gap_the_margin():
    shapes = job(seed=9, n=50)
    st = NestSettings(margin=13, spacing=4.1, rotations=[0, 90], iterations=3,
                      containers=[Container.rect(1250, 3200, quantity=0)])
    res = nest(shapes, st)
    assert not res.unplaced
    near, lows = [], []
    for boxes in placed_boxes(shapes, res).values():
        lows.append((min(b[0] for b in boxes), min(b[1] for b in boxes)))
        for b in boxes:
            up = [c[1] - b[3] for c in boxes if c is not b and min(b[2], c[2]) - max(b[0], c[0]) > 1 and c[1] >= b[3] - 1e-6]
            right = [c[0] - b[2] for c in boxes if c is not b and min(b[3], c[3]) - max(b[1], c[1]) > 1 and c[0] >= b[2] - 1e-6]
            near += [min(g) for g in (up, right) if g and min(g) < 40]       # (the nearest one: the next is further)
    # never closer than the spacing; where a part rests on a neighbour it is exactly the spacing (a part that rests on a taller
    # neighbour is further from the lower one beside it: that is the shape of the parts, not a hole)
    assert near and min(near) >= 4.1 - 1e-6
    assert sum(1 for g in near if abs(g - 4.1) < 1e-6) >= 0.8 * len(near), sorted(set(round(g, 3) for g in near))[:8]
    assert all(abs(x - 13) < 1e-6 and abs(y - 13) < 1e-6 for x, y in lows)

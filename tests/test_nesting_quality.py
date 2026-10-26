"""The nesting layouts are valid (no overlap, spacing, border, counts) and the search only ever improves them."""
import math
import random

import pytest
from nest_check import verify

from iyicam.nesting.nest import NestSettings, NestShape, nest


def rect(w, h):
    return [(0, 0), (w, 0), (w, h), (0, h)]


def disc(r, n=40):
    return [(r + r * math.cos(2 * math.pi * k / n), r + r * math.sin(2 * math.pi * k / n)) for k in range(n)]


def lshape(w, h, t):
    return [(0, 0), (w, 0), (w, t), (t, t), (t, h), (0, h)]


def ushape(w, h, t):
    return [(0, 0), (w, 0), (w, h), (w - t, h), (w - t, t), (t, t), (t, h), (0, h)]


def tshape(w, h, t):
    return [(0, h - t), (0, h), (w, h), (w, h - t), ((w + t) / 2, h - t), ((w + t) / 2, 0), ((w - t) / 2, 0),
            ((w - t) / 2, h - t)]


def star(R, r, n=5):
    return [(R + (R if k % 2 == 0 else r) * math.cos(math.pi * k / n),
             R + (R if k % 2 == 0 else r) * math.sin(math.pi * k / n)) for k in range(2 * n)]


def letter_e(w, h, t):
    return [(0, 0), (w, 0), (w, t), (t, t), (t, h / 2 - t / 2), (w * 0.8, h / 2 - t / 2), (w * 0.8, h / 2 + t / 2),
            (t, h / 2 + t / 2), (t, h - t), (w, h - t), (w, h), (0, h)]


def blob(rx, ry, seed, n=60):
    rnd = random.Random(seed)
    ph = [rnd.uniform(0, 6.28) for _ in range(3)]
    return [(rx + rx * math.cos(2 * math.pi * k / n) * (1 + 0.12 * math.sin(5 * 2 * math.pi * k / n + ph[0])),
             ry + ry * math.sin(2 * math.pi * k / n) * (1 + 0.1 * math.cos(3 * 2 * math.pi * k / n + ph[1])))
            for k in range(n)]


def job(kind, seed=1):
    rnd = random.Random(seed)
    out = []
    if kind == "panels":
        for w, h, q in [(720, 396, 2), (596, 396, 3), (800, 300, 2), (450, 450, 2), (350, 250, 4)]:
            out.append(NestShape([rect(w, h)], q))
    elif kind == "mixed":
        for i in range(12):
            w, h = rnd.randint(150, 420), rnd.randint(120, 320)
            out.append(NestShape([[rect(w, h), lshape(w, h, h // 3), ushape(w, h, h // 3), tshape(w, h, h // 3),
                                   disc(min(w, h) / 2), letter_e(w, h, h // 4)][i % 6]]))
    elif kind == "curvy":
        for i in range(8):
            out.append(NestShape([blob(rnd.randint(100, 250), rnd.randint(80, 200), i)]))
    elif kind == "stars":
        for i in range(8):
            R = rnd.randint(70, 200)
            out.append(NestShape([[star(R, R * 0.45), disc(R)][i % 2]], 1))
    elif kind == "big_and_small":
        out.append(NestShape([rect(900, 500)], 2))
        out.append(NestShape([rect(120, 80)], 12))
        out.append(NestShape([disc(40)], 8))
    return out


KINDS = ["panels", "mixed", "curvy", "stars", "big_and_small"]


def settings(**kw):
    base = dict(sheet_width=1800, sheet_height=1000, margin=10, spacing=8, rotations=[0, 90, 180, 270],
                gravity="left", iterations=1, tolerance=0.5)
    base.update(kw)
    return NestSettings(**base)


@pytest.mark.parametrize("kind", KINDS)
@pytest.mark.parametrize("gravity", ["left", "bottom", "compact", "reach_x", "reach_y", "diag"])
def test_every_gravity_gives_a_valid_layout(kind, gravity):
    shapes = job(kind)
    st = settings(gravity=gravity)
    res = nest(shapes, st)
    assert verify(shapes, res, st) == []
    assert not res.unplaced


@pytest.mark.parametrize("kind", KINDS)
def test_more_attempts_are_valid_and_never_worse(kind):
    shapes = job(kind)
    base_st, many_st = settings(iterations=1), settings(iterations=10)
    one, many = nest(shapes, base_st), nest(shapes, many_st)
    assert verify(shapes, many, many_st) == []

    def key(r):
        x0, y0, x1, y1 = r.used[-1]
        return (len(r.unplaced), r.sheets, round((x1 - x0) * (y1 - y0), 1))
    assert key(many) <= key(one), (key(many), key(one))


@pytest.mark.parametrize("seed", range(6))
def test_valid_with_any_angles_quantities_and_stock(seed):
    rnd = random.Random(seed)
    shapes = job(rnd.choice(KINDS), seed)
    for sh in shapes:
        sh.quantity = rnd.choice([1, 1, 2, 3])
        if rnd.random() < 0.3:
            sh.rotations = [0.0]
    st = settings(rotations=rnd.choice([[0], [0, 90], [0, 45, 90, 135, 180, 225, 270, 315], [0, 22.5, 90]]),
                  gravity=rnd.choice(["left", "bottom", "compact"]), iterations=rnd.choice([1, 4, 8]),
                  spacing=rnd.choice([4, 8, 15]), margin=rnd.choice([0, 10, 25]), seed=seed)
    res = nest(shapes, st)
    assert verify(shapes, res, st) == []


def test_the_search_is_deterministic():
    shapes = job("mixed")
    st = settings(iterations=8)
    a, b = nest(shapes, st), nest(shapes, st)
    assert [(p.shape, p.copy, p.sheet, p.angle, round(p.x, 6), round(p.y, 6)) for p in a.placements] == \
        [(p.shape, p.copy, p.sheet, p.angle, round(p.x, 6), round(p.y, 6)) for p in b.placements]


def test_used_boxes_describe_the_parts():
    shapes = job("mixed")
    st = settings(iterations=6)
    res = nest(shapes, st)
    assert len(res.used) == res.sheets
    from nest_check import _bbox, placed
    for k, (x0, y0, x1, y1) in enumerate(res.used):
        boxes = [_bbox(placed(shapes[p.shape], p)) for p in res.placements if p.sheet == k]
        assert min(b[0] for b in boxes) >= x0 - 0.1 and max(b[2] for b in boxes) <= x1 + 0.1
        assert min(b[1] for b in boxes) >= y0 - 0.1 and max(b[3] for b in boxes) <= y1 + 0.1
        assert x1 - x0 <= st.sheet_width and y1 - y0 <= st.sheet_height


def test_a_search_finds_a_denser_layout_than_one_pass_on_a_hard_job():
    """Rectangles of many sizes: the first pass needs more sheets than a search does (the area used on the
    last sheet is the measure when the number of sheets is equal)."""
    rnd = random.Random(7)
    shapes = [NestShape([rect(rnd.randint(150, 600), rnd.randint(100, 450))]) for _ in range(24)]
    st1, st24 = settings(sheet_width=2440, sheet_height=1220, iterations=1), \
        settings(sheet_width=2440, sheet_height=1220, iterations=24)
    one, many = nest(shapes, st1), nest(shapes, st24)
    assert verify(shapes, many, st24) == []
    assert (many.sheets, many.used[-1][2]) <= (one.sheets, one.used[-1][2])


def test_the_checker_itself_catches_overlaps_borders_and_missing_parts():
    shapes = job("panels")
    st = settings()
    res = nest(shapes, st)
    assert verify(shapes, res, st) == []
    import copy
    bad = copy.deepcopy(res)
    bad.placements[1].x, bad.placements[1].y = bad.placements[0].x, bad.placements[0].y   # on top of the first
    assert any("closer than the spacing" in p for p in verify(shapes, bad, st))
    bad = copy.deepcopy(res)
    bad.placements[0].x = -50
    assert any("outside the border" in p for p in verify(shapes, bad, st))
    bad = copy.deepcopy(res)
    bad.placements.pop()
    assert any("neither placed nor reported" in p for p in verify(shapes, bad, st))

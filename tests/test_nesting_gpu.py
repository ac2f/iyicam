"""Nesting on the graphics card (experimental, ``NestSettings.gpu``): off it changes nothing; on, every layout is valid
(checked by the independent geometry of ``nest_check``), what the card does not do and any failure go the usual way.

The card's code runs here on NumPy (``nest.GPU_BACKEND = "numpy"``, the same steps), so no card is needed; with CuPy and
a card the kernels are compared with NumPy and a layout is made on the card."""
import math
import subprocess
import sys

import numpy as np
import pytest
from hypothesis import HealthCheck, given, settings, strategies as st

from nest_check import verify

from iyicam.nesting import gpu as G
from iyicam.nesting import nest as N
from iyicam.nesting.nest import NestSettings, NestShape, nest


def rect(w, h, x=0.0, y=0.0):
    return [(x, y), (x + w, y), (x + w, y + h), (x, y + h)]


def star(R, r, k=5):
    return [((R if i % 2 == 0 else r) * math.cos(math.pi * i / k), (R if i % 2 == 0 else r) * math.sin(math.pi * i / k))
            for i in range(2 * k)]


def parts():
    return [NestShape([rect(120, 60)], 3), NestShape([star(50, 20)], 3),
            NestShape([[(0, 0), (120, 0), (120, 25), (25, 25), (25, 90), (0, 90)]], 2),
            NestShape([rect(150, 110)], 1, holes=[rect(110, 70, 20, 20)]), NestShape([rect(40, 30)], 4)]


@pytest.fixture
def on_numpy(monkeypatch):
    monkeypatch.setattr(N, "GPU_BACKEND", "numpy")


def settings_(**kw):
    base = dict(sheet_width=500, sheet_height=350, margin=5, spacing=6, rotations=[0.0, 90.0, 180.0, 270.0],
                iterations=2, gpu=True, gpu_pixel=1.0)
    base.update(kw)
    return NestSettings(**base)


def test_off_by_default_and_the_module_is_not_even_loaded():
    assert NestSettings().gpu is False
    code = ("import sys; sys.path.insert(0, 'tests'); from iyicam.nesting.nest import nest, NestSettings, NestShape; "
            "r = nest([NestShape([[(0,0),(50,0),(50,30),(0,30)]], 3)], NestSettings(sheet_width=300, sheet_height=200)); "
            "print(len(r.placements), 'iyicam.nesting.gpu' in sys.modules, r.notes)")
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=120)
    assert out.stdout.split()[:2] == ["3", "False"], out.stdout + out.stderr
    assert out.stdout.strip().endswith("[]")


def test_a_layout_on_the_cards_way_is_valid_and_says_where_it_was_made(on_numpy):
    ps = parts()
    st_ = settings_()
    res = nest(ps, st_)
    assert verify(ps, res, st_, slack=0.6) == []
    assert not res.unplaced and len(res.placements) == sum(p.quantity for p in ps)
    assert any("graphics card" in n for n in res.notes)


def test_parts_go_in_the_holes_of_others(on_numpy):
    ps = [NestShape([rect(300, 300)], 1, holes=[rect(260, 260, 20, 20)]), NestShape([rect(100, 100)], 4)]
    st_ = settings_(sheet_width=320, sheet_height=320, margin=2, spacing=4, rotations=[0.0], iterations=1)
    res = nest(ps, st_)
    assert verify(ps, res, st_, slack=0.6) == []
    assert res.sheets == 1 and not res.unplaced          # (the small ones only fit in the frame's hole)


@pytest.mark.parametrize("gravity", ["left", "bottom", "reach_x", "reach_y"])
def test_every_gravity_of_the_card(on_numpy, gravity):
    ps = parts()
    st_ = settings_(gravity=gravity, gravity_only=True, iterations=1)
    res = nest(ps, st_)
    assert verify(ps, res, st_, slack=0.6) == []
    assert any("graphics card" in n for n in res.notes)


def test_what_the_card_does_not_do_goes_the_usual_way(on_numpy):
    ps = parts()
    for kw in (dict(arrange="rows"), dict(gravity="compact", gravity_only=True), dict(squeeze_margin=3.0)):
        st_ = settings_(**kw)
        res = nest(ps, st_)
        assert verify(ps, res, st_, slack=0.6) == []
        assert any("not on the graphics card" in n for n in res.notes), (kw, res.notes)


def test_a_failure_on_the_card_goes_the_usual_way(on_numpy, monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("out of memory")
    monkeypatch.setattr(G, "nest_on_gpu", boom)
    ps = parts()
    st_ = settings_()
    res = nest(ps, st_)
    assert verify(ps, res, st_, slack=0.6) == [] and not res.unplaced
    assert any("failed" in n and "out of memory" in n for n in res.notes)


def test_a_layout_that_fails_the_exact_check_is_not_taken(on_numpy, monkeypatch):
    monkeypatch.setattr(G, "valid", lambda n, r: False)
    ps = parts()
    st_ = settings_()
    res = nest(ps, st_)
    assert verify(ps, res, st_, slack=0.6) == []
    assert any("exact check" in n for n in res.notes)


def test_cancel_stops_it(on_numpy):
    calls = [0]

    def cancel():
        calls[0] += 1
        return calls[0] > 3
    with pytest.raises(N.NestCancelled):
        nest(parts(), settings_(), cancel=cancel)


def test_without_cupy_it_says_why(monkeypatch):
    monkeypatch.setattr(G, "_why", False)
    import builtins
    real = builtins.__import__

    def no_cupy(name, *a, **k):
        if name == "cupy" or name.startswith("cupy."):
            raise ImportError("no cupy")
        return real(name, *a, **k)
    monkeypatch.setattr(builtins, "__import__", no_cupy)
    why = G.available()
    assert why and "CuPy" in why
    monkeypatch.setattr(G, "_why", False)


@st.composite
def a_part(draw):
    kind = draw(st.sampled_from(["rect", "star", "L", "frame"]))
    q = draw(st.integers(1, 3))
    if kind == "rect":
        return NestShape([rect(draw(st.floats(10, 200)), draw(st.floats(10, 150)))], q)
    if kind == "star":
        R = draw(st.floats(20, 90))
        return NestShape([star(R, R * draw(st.floats(0.3, 0.8)))], q)
    if kind == "L":
        a, b, t = draw(st.floats(40, 200)), draw(st.floats(40, 150)), draw(st.floats(8, 30))
        return NestShape([[(0, 0), (a, 0), (a, t), (t, t), (t, b), (0, b)]], q)
    w, h = draw(st.floats(80, 220)), draw(st.floats(80, 180))
    m = draw(st.floats(12, 30))
    return NestShape([rect(w, h)], q, holes=[rect(w - 2 * m, h - 2 * m, m, m)])


@settings(max_examples=int(__import__("os").environ.get("NEST_N", 25)), deadline=None,
          suppress_health_check=list(HealthCheck))
@given(st.lists(a_part(), min_size=1, max_size=5), st.sampled_from([[0.0], [0.0, 90.0], [0.0, 90.0, 180.0, 270.0]]),
       st.floats(0, 15), st.floats(0, 12), st.sampled_from(["left", "bottom", "reach_x"]),
       st.sampled_from([(600.0, 400.0), (450.0, 300.0)]), st.sampled_from([0.0, 1.5]))
def test_the_cards_layouts_keep_parts_apart_and_on_their_plates(parts_, rots, margin, spacing, gravity, size, pixel):
    old = N.GPU_BACKEND
    N.GPU_BACKEND = "numpy"
    try:
        st_ = NestSettings(sheet_width=size[0], sheet_height=size[1], margin=margin, spacing=spacing, rotations=rots,
                           gravity=gravity, iterations=1, tolerance=0.5, max_sheets=20, gpu=True, gpu_pixel=pixel)
        res = nest(parts_, st_)
    finally:
        N.GPU_BACKEND = old
    assert verify(parts_, res, st_, slack=0.6) == []
    assert len(res.placements) + len(res.unplaced) == sum(p.quantity for p in parts_)


# -- with a card --------------------------------------------------------------------------------------------------------

card = pytest.mark.skipif(G.available() is not None, reason="no NVIDIA card with CuPy here")


@card
def test_the_kernels_find_what_numpy_finds():
    n = N.Nester(parts(), settings_())
    ref, dev = G.GpuNester(n, G.backend("numpy"), 1.0), G.GpuNester(n, G.backend("cupy"), 1.0)
    ref.prepare()
    dev.prepare()
    rng = np.random.default_rng(3)
    box = n.boxes[0]
    for trial in range(6):
        pr, pd = G._Plate(ref.be, box.rect, ref.px), G._Plate(dev.be, box.rect, dev.px)
        keys = list(ref.masks)
        for _ in range(trial * 3):                        # some parts stamped anywhere
            k = keys[rng.integers(len(keys))]
            i, j = int(rng.integers(0, pr.Wr)), int(rng.integers(0, pr.Hr))
            pr.stamp(ref.masks[k], i, j)
            dev.be.stamp([(pd, dev.masks[k], i, j)])
        for rm in (0, 1):
            jobs_r, jobs_d = [], []
            for k in keys:
                a, b = ref.masks[k], dev.masks[k]
                nc, nr = pr.Wr - a.w + 1, pr.Hr - a.h + 1
                if nc > 0 and nr > 0:
                    jobs_r.append((pr, a, 0, nc, nr, rm, 0))
                    jobs_d.append((pd, b, 0, nc, nr, rm, 0))
            assert ref.be.search(None, jobs_r, ref.runs, ref.cores) == dev.be.search(None, jobs_d, dev.runs, dev.cores)
        assert np.array_equal(pr.S, pd.S.get()) and np.array_equal(pr.T, pd.T.get())


@card
def test_a_layout_on_the_card(monkeypatch):
    monkeypatch.setattr(N, "GPU_BACKEND", "cupy")
    ps = parts()
    st_ = settings_(gpu_pixel=0.0)
    res = nest(ps, st_)
    assert verify(ps, res, st_, slack=0.6) == [] and not res.unplaced
    assert any("graphics card" in n for n in res.notes)

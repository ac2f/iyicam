"""The 2D simulation: a time line of the calculated toolpaths (nothing calculated again), only the chosen ones, played at a
speed, and the cut painted on the sheet as the tool goes."""
import pytest

from conftest import add_tp, end_mill, make_project

from iyicam.builtins import ensure_registered
from iyicam.cam import sim2d
from iyicam.cam.job import compute_all, sheet_toolpaths
from iyicam.core import shapes
from iyicam.core.registry import REGISTRY


def job():
    ensure_registered(REGISTRY)
    p, s, objs = make_project([[shapes.rectangle(50, 50, 100, 60)], [shapes.rectangle(250, 50, 100, 60)]], size=(400, 200))
    a = add_tp(p, "profile", end_mill(6), {"through": True}, objs[:1])
    b = add_tp(p, "profile", end_mill(6), {"through": True}, objs[1:])
    res = compute_all(p, REGISTRY)
    return p, s, sheet_toolpaths(p, res, s), a, b


def test_the_time_line_follows_the_moves_and_lasts_as_long_as_the_estimate():
    p, s, tps, a, b = job()
    m = p.material.machine()
    tl = sim2d.build(tps, machine=m)
    want = sum(tp.stats(m).time_s for tp in tps)
    assert tl.total == pytest.approx(want, rel=1e-6) and tl.pieces
    assert all(pc.t1 >= pc.t0 for pc in tl.pieces)
    assert all(a_.t1 == pytest.approx(b_.t0) for a_, b_ in zip(tl.pieces, tl.pieces[1:]))
    x, y, z, k = tl.position(tl.total)
    assert k == 1 and tl.position(-1) is None
    # the tool is in the second part's area while the second toolpath cuts
    mid = next(pc for pc in tl.pieces if pc.tool == 1 and pc.kind == sim2d.KIND_FEED and pc.z1 < 0)
    x, y, z, k = tl.position((mid.t0 + mid.t1) / 2)
    assert k == 1 and 240 <= x <= 360


pytest.importorskip("PySide6.QtWidgets")


@pytest.fixture
def rwin(tmp_path, monkeypatch):
    from gui_robot import rwin_gen
    yield from rwin_gen(tmp_path, monkeypatch)


def test_simulate_plays_the_selected_toolpath_without_calculating_and_paints_the_cut(rwin):
    from gui_robot import process
    from test_cutedit import parts
    win, _ = rwin
    c, t, objs = parts(win, n=2, auto=False)
    calls = []
    orig = c.recalculate
    c.recalculate = lambda *a, **k: calls.append(1) or orig(*a, **k)
    with c.edit("move"):                                       # (old toolpaths are played as they were calculated)
        c.find_vector(objs[0].id).contours[0] = shapes.rectangle(55, 100, 100, 80)
    panel = win.toolpaths_panel
    next(panel._rows()).setSelected(True)
    win.simulate()
    process()
    sp = win.sim_panel
    assert calls == [] and sp.view is not None and win.canvas.sim2d is sp and sp.playing
    assert [tp.def_id for tp in sp.view.timeline.toolpaths] == [t.id]
    sp.timer.stop()
    sp.speed.setCurrentIndex(sp.speed.findData(50))
    for _ in range(3):
        sp._tick()
    assert 0 < sp.view.t < sp.view.timeline.total
    sp.seek(sp.view.timeline.total)
    img = sp.view.image
    cut = sum(1 for yy in range(0, img.height(), 4) for xx in range(0, img.width(), 4) if img.pixelColor(xx, yy).alpha() > 0)
    assert cut > 50                                             # (the outlines are painted as cut)
    shot = win.canvas.grab()
    assert not shot.isNull()
    sp.seek(0.0)
    assert all(sp.view.image.pixelColor(xx, yy).alpha() == 0 for yy in range(0, sp.view.image.height(), 9)
               for xx in range(0, sp.view.image.width(), 9))     # (back to the start: nothing cut)
    sp.which.setCurrentIndex(sp.which.findData("all"))
    process()
    assert sp.view is not None
    sp.stop()
    assert win.canvas.sim2d is None


def test_simulate_says_so_when_nothing_is_calculated(rwin):
    from gui_robot import process
    win, _ = rwin
    win.simulate()
    process()
    assert win.sim_panel.view is None and "calculate first" in win.sim_panel.info.text()

"""Long toolpath calculations: the program stays alive, Cancel works, nothing is changed when cancelled."""
import threading
import time

import pytest

pytest.importorskip("PySide6.QtWidgets")

from conftest import end_mill  # noqa: E402
from PySide6.QtCore import QTimer  # noqa: E402
from PySide6.QtWidgets import QApplication, QProgressDialog  # noqa: E402

from iyicam.cam.operation import Operation  # noqa: E402
from iyicam.core import shapes  # noqa: E402
from iyicam.core.document import Selection, ToolpathDef, VectorObject  # noqa: E402
from iyicam.core.registry import REGISTRY  # noqa: E402


class SlowOp(Operation):
    """Pure Python work that never looks at any flag, for ``SlowOp.seconds`` seconds."""
    id = "slow_test"
    label = "Slow"
    seconds = 1.0
    threads: list = []

    def compute(self, ctx):
        self.threads.append(threading.current_thread())
        t = time.perf_counter()
        n = 0
        while time.perf_counter() - t < self.seconds:
            n += sum(i * i for i in range(300))
        return []


@pytest.fixture
def win(tmp_path, monkeypatch):
    monkeypatch.setenv("IYICAM_HOME", str(tmp_path / "home"))
    monkeypatch.setitem(REGISTRY.operations, "slow_test", SlowOp)
    from iyicam.gui.app import create_window
    app, w = create_window(["test"], load_plugins=False)
    w.show()
    QApplication.processEvents()
    SlowOp.threads.clear()
    yield w
    w.ctrl.dirty = False
    w.close()
    QApplication.processEvents()


def add_slow(ctrl, seconds):
    SlowOp.seconds = seconds
    o = VectorObject([shapes.rectangle(0, 0, 50, 50)], ctrl.layer_id)
    ctrl.add_vectors([o], select=False)
    t = ToolpathDef("slow", "slow_test", end_mill(6), {}, Selection("vectors", [o.id]))
    ctrl.project.toolpaths.append(t)
    return t


def cancel_soon(ms):
    def go():
        for w in QApplication.topLevelWidgets():
            if isinstance(w, QProgressDialog) and w.isVisible():
                w.cancel()
    QTimer.singleShot(ms, go)


def test_a_long_calculation_runs_in_a_thread_and_the_program_stays_alive(win):
    ctrl = win.ctrl
    t = add_slow(ctrl, 1.2)
    ticks = []
    timer = QTimer()
    timer.timeout.connect(lambda: ticks.append(time.perf_counter()))
    timer.start(20)                      # the event loop keeps running during the calculation
    t0 = time.perf_counter()
    ctrl.recalculate()
    timer.stop()
    assert time.perf_counter() - t0 >= 1.1
    assert SlowOp.threads and SlowOp.threads[0] is not threading.main_thread()
    assert len(ticks) > 20, "the interface was frozen during the calculation"
    assert (t.id, ctrl.sheet_id) in ctrl.results and not ctrl.stale


def test_cancel_stops_the_calculation_and_changes_nothing(win):
    ctrl = win.ctrl
    t = add_slow(ctrl, 30.0)             # would run for half a minute
    ctrl.results = {("old", "result"): "kept"}
    cancel_soon(900)
    t0 = time.perf_counter()
    res = ctrl.recalculate()
    assert time.perf_counter() - t0 < 5.0
    assert res == {} and ctrl.stale
    assert ctrl.results == {("old", "result"): "kept"}
    assert (t.id, ctrl.sheet_id) not in ctrl.results
    ctrl.project.toolpaths.remove(t)
    ctrl.recalculate()                   # and the next calculation works as usual
    assert not ctrl.stale


def test_a_quick_calculation_shows_no_window(win):
    ctrl = win.ctrl
    add_slow(ctrl, 0.05)
    seen = []
    QTimer.singleShot(30, lambda: seen.extend(w for w in QApplication.topLevelWidgets()
                                              if isinstance(w, QProgressDialog) and w.isVisible()))
    ctrl.recalculate()
    assert seen == []


def test_plugin_hooks_run_in_the_main_thread(win):
    ctrl = win.ctrl
    add_slow(ctrl, 0.05)
    where = []
    REGISTRY.hooks.setdefault("toolpath_computed", []).append(
        ("test", lambda *a: where.append(threading.current_thread())))
    try:
        ctrl.recalculate()
    finally:
        REGISTRY.hooks["toolpath_computed"] = [h for h in REGISTRY.hooks["toolpath_computed"] if h[0] != "test"]
    assert where and all(t is threading.main_thread() for t in where)


def test_a_failing_strategy_is_still_reported_not_raised(win):
    class Broken(SlowOp):
        id = "broken_test"

        def compute(self, ctx):
            return None.name              # the kind of fault the user reported

    REGISTRY.operations["broken_test"] = Broken
    try:
        ctrl = win.ctrl
        o = VectorObject([shapes.rectangle(0, 0, 50, 50)], ctrl.layer_id)
        ctrl.add_vectors([o], select=False)
        t = ToolpathDef("b", "broken_test", end_mill(6), {}, Selection("vectors", [o.id]))
        ctrl.project.toolpaths.append(t)
        ctrl.recalculate()
        assert "AttributeError" in ctrl.results_for(t)[0].error
    finally:
        del REGISTRY.operations["broken_test"]

"""Every vector command with random settings on random selections: no exception, no broken geometry, and undo /
redo put things back exactly."""
import math
import os
import random
import signal
import zlib

import pytest

pytest.importorskip("PySide6.QtWidgets")

from PySide6.QtWidgets import QApplication  # noqa: E402
from test_cam_fuzz import random_value, scenario  # noqa: E402

from iyicam.core.commands import CommandError  # noqa: E402
from iyicam.core.document import VectorObject  # noqa: E402
from iyicam.core.registry import REGISTRY  # noqa: E402


LIMIT = 15   # seconds for one command
CASES = int(os.environ.get("IYICAM_FUZZ_CASES", "10"))     # more of them: IYICAM_FUZZ_CASES=300 pytest ...
SEED = int(os.environ.get("IYICAM_FUZZ_SEED", "0"))


@pytest.fixture(scope="module")
def ctrl(tmp_path_factory):
    os.environ["IYICAM_HOME"] = str(tmp_path_factory.mktemp("home"))
    from iyicam.gui.app import create_window
    app, win = create_window(["test"], load_plugins=False)
    win.ctrl.settings.auto_recalculate = False
    yield win.ctrl
    win.ctrl.dirty = False
    win.close()
    QApplication.processEvents()


def state(c):
    """What is on the sheets and layers: ids, names and outlines."""
    out = []
    for s in c.project.sheets:
        for v in s.vectors:
            out.append((s.id, v.id, v.layer_id, v.name, tuple(
                (k.closed, tuple(k.flatten(0.05))) for k in v.contours)))
    return sorted(out, key=lambda t: (t[0], t[1])), [(la.id, la.name) for la in c.project.layers]


def same(a, b, tol=1e-6) -> bool:
    """Two states are the same (the points may differ by float noise from saving and loading)."""
    if a[1] != b[1] or len(a[0]) != len(b[0]):
        return False
    for x, y in zip(a[0], b[0]):
        if x[:4] != y[:4] or len(x[4]) != len(y[4]):
            return False
        for (c1, p1), (c2, p2) in zip(x[4], y[4]):
            if c1 != c2 or len(p1) != len(p2) or any(abs(u - w) > tol for q, r in zip(p1, p2) for u, w in zip(q, r)):
                return False
    return True


def broken(c) -> str | None:
    for s in c.project.sheets:
        for v in s.vectors:
            for k in v.contours:
                if not k.segs:
                    return f"{v.id}: an empty contour"
                for x, y in k.flatten(0.05):
                    if not (math.isfinite(x) and math.isfinite(y)) or abs(x) > 1e8 or abs(y) > 1e8:
                        return f"{v.id}: a point at {x}, {y}"
                for a, b in zip(k.segs, k.segs[1:]):
                    if math.dist(a.b, b.a) > 1e-6:
                        return f"{v.id}: segments that do not meet ({a.b} {b.a})"
    return None


def sizes(c) -> dict:
    out = {}
    for s in c.project.sheets:
        for v in s.vectors:
            bb = v.bbox()
            out[v.id] = (bb.width, bb.height)
    return out


def collapsed(before: dict, after: dict) -> str | None:
    """A vector that had a size and has none now was destroyed (a scale of nothing, a size of zero).  (Being made
    very small on purpose, say a thousandth of a percent, is not being destroyed: what counts is the part of the
    size that is left.)"""
    def gone(old: float, new: float) -> bool:
        return old > 1e-6 and new < 1e-9 and new < old * 1e-7

    for vid, (w, h) in after.items():
        if vid not in before:
            continue
        ow, oh = before[vid]
        if (ow > 1e-6 or oh > 1e-6) and w < 1e-9 and h < 1e-9 and (gone(ow, w) or gone(oh, h)):
            return f"{vid} has been squeezed to a point"
        if gone(ow, w) and oh > 1e-6:
            return f"{vid} has lost its width ({before[vid]} -> {(w, h)})"
        if gone(oh, h) and ow > 1e-6:
            return f"{vid} has lost its height ({before[vid]} -> {(w, h)})"
    return None


@pytest.mark.parametrize("cmd_id", sorted(REGISTRY.commands))
def test_command_with_random_settings_and_selections(ctrl, cmd_id):
    cmd = REGISTRY.commands[cmd_id]
    rnd = random.Random(zlib.crc32(cmd_id.encode()) + SEED)   # the same cases in every run
    for case in range(CASES):
        seed = rnd.randrange(10 ** 6)
        r = random.Random(seed)
        ctrl.new_project(600, 400, 18)
        objs = [VectorObject(cs, ctrl.layer_id, f"v{i}") for i, cs in enumerate(scenario(r))]
        if r.random() < 0.5:
            objs.append(VectorObject([c.copy() for c in objs[0].contours], ctrl.layer_id, "twin"))
        ctrl.add_vectors(objs, select=False)
        pick = r.sample(objs, k=min(len(objs), max(cmd.min_selection if hasattr(cmd, "min_selection") else 0,
                                                    r.randint(1, 4))))
        if r.random() < 0.15:
            pick = []
        ctrl.select([o.id for o in pick])
        values = {p.name: random_value(p, r) for p in cmd.params}
        who = f"{cmd_id} seed {seed}: values {values}, {len(pick)} selected"
        before = state(ctrl)
        before_sizes = sizes(ctrl)

        def too_slow(*_):
            raise TimeoutError(f"{who}: more than {LIMIT} s")
        if hasattr(signal, "SIGALRM"):   # (not on Windows: no limit there)
            signal.signal(signal.SIGALRM, too_slow)
            signal.alarm(LIMIT)
        try:
            ok = ctrl.run_command(cmd, values)
        except Exception as exc:  # noqa: BLE001
            pytest.fail(f"{who}\n{type(exc).__name__}: {exc}")
        finally:
            if hasattr(signal, "SIGALRM"):
                signal.alarm(0)
        problem = broken(ctrl) or (collapsed(before_sizes, sizes(ctrl)) if ok else None)
        assert problem is None, f"{who}: {problem}"
        after = state(ctrl)
        if ok:
            ctrl.undo()
            assert same(state(ctrl), before), f"{who}: undo does not restore"
            ctrl.redo()
            assert same(state(ctrl), after), f"{who}: redo does not restore"
        else:
            assert same(state(ctrl), before), f"{who}: a refused command changed something"
        assert CommandError is not None

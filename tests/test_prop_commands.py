"""Every vector command on any selection and any values its window allows (Hypothesis): it does its work or says why
(``CommandError``); it never fails with another error, never leaves a vector that is not sound (finite points, segments
joined end to end) and never hangs."""
import math
import signal

import pytest
from hypothesis import HealthCheck, given, settings, strategies as st

from conftest import make_project
from iyicam.builtins import ensure_registered
from iyicam.core import shapes
from iyicam.core.commands import CommandContext, CommandError, run_command
from iyicam.core.registry import REGISTRY

from test_prop_geom import contour

ensure_registered(REGISTRY)
CMDS = sorted(REGISTRY.commands)


def value_for(p):
    lo = p.min if p.min is not None else (-500.0 if p.kind in ("length", "float", "angle") else 0.0)
    hi = p.max if p.max is not None else (500.0 if p.kind != "percent" else 400.0)
    if p.kind in ("length", "float", "angle", "percent", "feed"):
        return st.floats(min_value=lo, max_value=hi, allow_nan=False)
    if p.kind == "int":
        return st.integers(int(max(lo, -50)), int(min(hi, 50)))
    if p.kind == "bool":
        return st.booleans()
    if p.kind == "choice":
        return st.sampled_from([c[0] for c in p.choices])
    if p.kind in ("str", "text"):
        return st.text(max_size=12)
    return st.just(p.default)


shape = st.one_of(contour(), st.builds(lambda x, y, w, h: shapes.rectangle(x, y, w, h), st.floats(0, 300), st.floats(0, 200),
                                       st.floats(1, 200), st.floats(1, 150)),
                  st.builds(lambda x, y, r: shapes.circle((x, y), r), st.floats(50, 300), st.floats(50, 200), st.floats(1, 80)))


def sound(sheet):
    for v in sheet.vectors:
        for c in v.contours:
            for s, q in zip(c.segs, c.segs[1:]):
                assert math.dist(s.b, q.a) < 1e-3 * max(1.0, abs(s.b[0]), abs(s.b[1])), (v.name, s, q)
            for x, y in c.flatten(0.5):
                assert math.isfinite(x) and math.isfinite(y)


class Hang(Exception):
    pass


def _alarm(*_):
    raise Hang()


@pytest.mark.parametrize("cid", CMDS)
def test_a_command_works_or_says_why(cid):
    cmd = REGISTRY.commands[cid]

    @settings(max_examples=int(__import__("os").environ.get("CMD_N", 40)), deadline=None, database=None, suppress_health_check=list(HealthCheck))
    @given(st.lists(st.lists(shape, min_size=1, max_size=2), min_size=0, max_size=4), st.data())
    def run(objs, data):
        p, s, made = make_project(objs, size=(600, 400))
        sel = [o.id for o in made if data.draw(st.booleans())] or [o.id for o in made[:1]]
        values = {q.name: data.draw(value_for(q)) for q in cmd.params}
        ctx = CommandContext(p, s, sel, p.layers[0].id)
        old = signal.signal(signal.SIGALRM, _alarm)
        signal.alarm(20)
        try:
            run_command(cmd, ctx, values)
        except CommandError:
            return
        except Hang:
            pytest.fail(f"{cid} did not finish: {values}")
        finally:
            signal.alarm(0)
            signal.signal(signal.SIGALRM, old)
        sound(s)

    run()

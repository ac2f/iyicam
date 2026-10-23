import os
import sys
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from iyicam.builtins import ensure_registered  # noqa: E402
from iyicam.core.document import Project, Selection, ToolpathDef, VectorObject  # noqa: E402
from iyicam.core.tooling import Tool  # noqa: E402

ensure_registered()


@pytest.fixture(autouse=True)
def isolated_home(tmp_path, monkeypatch):
    monkeypatch.setenv("IYICAM_HOME", str(tmp_path / "home"))


def end_mill(d=6.0, **kw) -> Tool:
    base = dict(name=f"EM{d}", type="end_mill", diameter=d, number=1, stepdown=3.0, stepover=d * 0.4,
                feed_rate=3000, plunge_rate=1000, spindle_speed=18000)
    base.update(kw)
    return Tool(**base)


def vbit(angle=90.0, d=20.0, **kw) -> Tool:
    base = dict(name=f"V{angle}", type="v_bit", diameter=d, angle=angle, number=2, stepdown=5.0,
                stepover=0.5, feed_rate=2000, plunge_rate=800)
    base.update(kw)
    return Tool(**base)


def make_project(contours_list, thickness=18.0, size=(600.0, 400.0)):
    p = Project("t")
    p.material.thickness = thickness
    s = p.sheets[0]
    s.width, s.height = size
    objs = []
    for cs in contours_list:
        cs = cs if isinstance(cs, list) else [cs]
        o = VectorObject(cs, p.layers[0].id)
        s.vectors.append(o)
        objs.append(o)
    return p, s, objs


def add_tp(p, op, tool, params, objs) -> ToolpathDef:
    t = ToolpathDef(op, op, tool, params, Selection("vectors", [o.id for o in objs]))
    p.toolpaths.append(t)
    return t

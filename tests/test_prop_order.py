"""The cut order on any layout with any strategy (Hypothesis): every contour is cut once and all of it in one go, and inner
pieces and holes are cut before the outline around them (a part cut loose first moves when its holes are cut)."""
from hypothesis import HealthCheck, given, settings, strategies as st

from conftest import add_tp, end_mill
from iyicam.cam.job import compute_toolpath
from iyicam.core import shapes
from iyicam.core.document import Project, Sheet, VectorObject

STRATS = ["auto", "smart", "nearest", "follow", "rows_bl", "rows_br", "rows_tl", "rows_tr", "zigzag_up", "zigzag_down",
          "zigzag_right", "zigzag_left", "cols_bl", "cols_br"]


@st.composite
def layout(draw):
    """Parts in a grid of 300 x 250 cells: a plate with 0..3 holes (one of them may hold an inner piece), or a plain disc."""
    p = Project("o")
    s = p.sheets[0]
    s.width, s.height, s.thickness = 1500.0, 1000.0, 18.0
    inner = {}                                         # contour key -> the key of the contour around it
    n = draw(st.integers(1, 8))
    for k in range(n):
        x0, y0 = 20 + (k % 5) * 300, 20 + (k // 5) * 250
        if draw(st.booleans()):
            cs = [shapes.rectangle(x0, y0, 260, 200)]
            for h in range(draw(st.integers(0, 3))):
                cs.append(shapes.rectangle(x0 + 20 + h * 80, y0 + 40, 60, 120))
            o = VectorObject(cs, p.layers[0].id)
            s.vectors.append(o)
            for h in range(1, len(cs)):
                inner[f"{o.id}:{h}"] = f"{o.id}:0"
            if len(cs) > 1 and draw(st.booleans()):       # an inner piece in the first hole
                piece = VectorObject([shapes.rectangle(x0 + 30, y0 + 55, 40, 90)], p.layers[0].id)
                s.vectors.append(piece)
                inner[f"{piece.id}:0"] = f"{o.id}:0"
        else:
            s.vectors.append(VectorObject([shapes.circle((x0 + 120, y0 + 100), draw(st.floats(20, 95)))], p.layers[0].id))
    return p, s, inner


@settings(max_examples=60, deadline=None, suppress_health_check=list(HealthCheck))
@given(layout(), st.sampled_from(STRATS), st.sampled_from(["toolpath", "climb", "conventional", "cw", "ccw"]),
       st.booleans())
def test_every_contour_is_cut_once_and_insides_first(lay, strategy, direction, smart):
    p, s, inner = lay
    t = add_tp(p, "profile", end_mill(6), {"through": True, "stepdown": 6.0}, list(s.vectors))
    t.sequence.update({"strategy": strategy, "direction": direction, "smart_starts": smart})
    r = compute_toolpath(p, t, s)
    assert not r.error
    cuts = [c for tp in r.toolpaths for c in tp.cuts]
    keys = [c["key"] for c in cuts]
    want = {f"{o.id}:{i}" for o in s.vectors for i, c in enumerate(o.contours) if c.closed}
    assert set(keys) == want                                         # (every contour, nothing else)
    first = {}
    for i, k in enumerate(keys):
        first.setdefault(k, i)
        assert keys.index(k) == i or keys[i - 1] == k               # (all the passes of a contour in one go)
    for k, around in inner.items():
        assert first[k] < first[around], (strategy, k, around)    # (an inside before the outline around it)

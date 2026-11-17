"""Columns (bottom -> top, then the next column to the right) with the support kept, on nested plates: the cut order is
looked at with iyicam.cam.audit, the way a person looks at a plate."""
import random

import pytest

from iyicam.cam import audit
from iyicam.core import shapes
from iyicam.core.document import Project, Selection, ToolpathDef, VectorObject
from iyicam.core.geom import BBox
from iyicam.core.tooling import Tool
from iyicam.service import ServiceHost

# plate 2 of the master döner file (1250 x 3200): a wide part at the bottom, narrow column on the right, a left column
PLATE2 = [(10, 10, 1210, 610), (10, 618, 1010, 918), (10, 926, 1010, 1226), (10, 1234, 1010, 1534),
          (1018, 618, 1218, 1518), (1018, 1526, 1218, 2426), (10, 1542, 910, 1742), (10, 1750, 910, 1950),
          (10, 1958, 910, 2158), (10, 2166, 910, 2366), (10, 2374, 910, 2574), (10, 2582, 910, 2782),
          (10, 2790, 910, 2990), (918, 2434, 1218, 2834), (10, 2998, 300, 3158), (308, 2998, 598, 3158),
          (606, 2998, 896, 3158), (918, 2842, 1208, 3002), (904, 3010, 1194, 3170)]


def computed(boxes, size=(1250, 3200), strategy="cols_bl"):
    p = Project("t")
    p.sheets[0].width, p.sheets[0].height = size
    for i, (x0, y0, x1, y1) in enumerate(boxes):
        p.sheets[0].vectors.append(VectorObject([shapes.rectangle(x0, y0, x1 - x0, y1 - y0)], p.layers[0].id, f"p{i}"))
    tp = ToolpathDef("c", "profile", Tool("EM4", "end_mill", 4.0, 1), {"through": True, "side": "outside", "cut_depth": 6},
                     Selection("layers", [], [p.layers[0].id]))
    tp.sequence.update({"strategy": strategy, "smart_starts": True, "grouping": "part"})
    p.toolpaths = [tp]
    c = ServiceHost().handle("compute", {"project": p.to_dict(), "preview": False})
    assert "error" not in c, c
    by = {v.id: v for v in p.sheets[0].vectors}
    order = c["results"][0]["order"]
    cb = {k: by[k.rsplit(":", 1)[0]].contours[int(k.rsplit(":", 1)[1])].bbox() for k in order}
    return audit.outlines(order, cb), by


def test_the_narrow_right_column_is_cut_after_the_left_column_and_the_support_is_kept():
    (order, boxes), by = computed(PLATE2)
    assert audit.violations(order, boxes) == []
    name = {k: by[k.rsplit(":", 1)[0]].name for k in order}
    cut = [int(name[k][1:]) for k in order]
    left = [i for i, b in enumerate(PLATE2) if b[0] == 10 and b[2] <= 1010 and i not in (0,)]
    assert max(cut.index(i) for i in left) < min(cut.index(i) for i in (4, 5)), cut       # part 6 of the plate is not early


def test_a_wide_part_does_not_swallow_the_columns_beside_it():
    from iyicam.cam.sequence import _rows
    import numpy as np
    xs = np.array([10.0, 10, 10, 10, 1018, 1018, 10, 10])
    widths = [1000, 1000, 1000, 1000, 200, 200, 900, 900]
    r = _rows(xs, widths, 0.0)
    assert r[4] == r[5] != r[0]


def test_the_audit_sees_what_a_person_sees():
    boxes = {"a": BBox(0, 0, 100, 100), "b": BBox(110, 0, 210, 100), "c": BBox(0, 110, 100, 210)}
    assert audit.violations(["a", "b", "c"], boxes) == []
    assert [(a, b, r) for a, b, r in audit.violations(["b", "a", "c"], boxes)] == [("b", "a", "support")]       # b is on a's hold side
    assert [(a, b, r) for a, b, r in audit.violations(["c", "a", "b"], boxes)] == [("c", "a", "support")]


@pytest.mark.parametrize("seed", [0, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13])
def test_random_nested_plates_keep_the_support_and_the_columns(seed):
    r = random.Random(seed)
    fams = [(290, 130), (290, 160), (200, 900), (300, 400), (150, 150), (1000, 450), (600, 1200), (120, 700), (400, 400),
            (80, 500)]
    sizes = r.sample(fams, r.randint(3, 6))
    p = Project("s")
    p.sheets[0].width, p.sheets[0].height = 1250, 3200
    for i in range(r.randint(25, 90)):
        w, h = r.choice(sizes)
        p.sheets[0].vectors.append(VectorObject([shapes.rectangle(-5000 - (i % 10) * 1300, -5000 - (i // 10) * 1300, w, h)],
                                                p.layers[0].id, f"p{i}"))
    tp = ToolpathDef("c", "profile", Tool("EM4", "end_mill", 4.0, 1), {"through": True, "side": "outside", "cut_depth": 6},
                     Selection("layers", [], [p.layers[0].id]))
    tp.sequence.update({"strategy": "cols_bl", "smart_starts": True, "grouping": "part"})
    p.toolpaths = [tp]
    host = ServiceHost()
    n = host.handle("nest", {"project": p.to_dict(), "settings": {"plates": [{"width": 1250, "height": 3200, "quantity": 0}],
                                                                  "iterations": 2, "time_limit": 3}})
    assert "error" not in n and not n["unplaced"]
    q = Project()
    q.load_state(n["project"])
    c = host.handle("compute", {"project": q.to_dict(), "preview": False})
    by = {v.id: v for s in q.sheets for v in s.vectors}
    for it in c["results"]:
        cb = {k: by[k.rsplit(":", 1)[0]].contours[int(k.rsplit(":", 1)[1])].bbox() for k in it["order"]}
        order, boxes = audit.outlines(it["order"], cb)
        assert audit.violations(order, boxes) == []

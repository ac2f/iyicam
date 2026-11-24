"""Columns (and rows) of a sweep where small and big parts lie together.

A new column started wherever the left edge of the next part was more than half the narrower of the two further on: with
bars of 20 mm among letters of 200 mm and shapes of 600 mm almost every part was a column of its own, and the sweep ran
up and down the sheet for each (a sign of a customer: the order jumped back left and down again and again). A column is
now at most a typical part wide from its first part, and a clear gap starts the next one (nested parts make their own);
in a column a part wholly left of another one beside it is cut first."""
import random

import numpy as np
import pytest

from iyicam.cam import audit
from iyicam.cam import sequence as seq
from iyicam.core import shapes
from iyicam.core.geom import BBox


def items_of(boxes):
    out = []
    for k, (x, y, w, h) in enumerate(boxes):
        it = seq.SeqItem(f"p{k}:0", shapes.rectangle(x, y, w, h))
        out.append(it)
    return out


def mixed_sign(seed=1):
    """A sign: small bars along the bottom, letters in rows, two big shapes over half the sheet (boxes overlapping)."""
    rng = random.Random(seed)
    boxes = [(40 + 50 * i, 20, 30, 30) for i in range(6)]                           # small bars
    boxes += [(30 + 210 * i + rng.uniform(-20, 20), 1500 + rng.uniform(-30, 30), 180, 190) for i in range(6)]
    boxes += [(30 + 210 * i + rng.uniform(-20, 20), 1250 + rng.uniform(-30, 30), 200, 180) for i in range(6)]
    boxes += [(40, 100, 480, 470), (620, 100, 600, 470), (60, 650, 450, 480), (700, 650, 500, 480)]
    return items_of(boxes)


def order_of(items, strategy="cols_bl"):
    return [it.key for it in seq.rank(items, strategy, (0.0, 0.0))]


def jumps_back(order, by, columns=True):
    """Cuts that lie wholly before the cut before them in the direction the sweep moves from column to column."""
    n = 0
    for a, b in zip(order, order[1:]):
        A, B = by[a].bbox, by[b].bbox
        n += (B.xmax < A.xmin - 1) if columns else (B.ymax < A.ymin - 1)
    return n


def test_a_sign_of_small_and_big_parts_is_swept_in_few_columns_and_never_back():
    items = mixed_sign()
    by = {it.key: it for it in items}
    order = order_of(items)
    assert jumps_back(order, by) == 0
    ex = np.array([by[k].bbox.xmin for k in order])
    cols = seq._rows(ex, [by[k].bbox.width for k in order], 0.0)
    assert len(set(cols.tolist())) <= 8                   # (a column is a typical part wide: not one per part)
    boxes = {it.key: it.bbox for it in items}
    assert not [v for v in audit.violations(order, boxes, columns=True) if v[2] == "column"]


def test_the_rows_of_the_same_sign_go_up_and_never_back_down():
    items = mixed_sign(2)
    by = {it.key: it for it in items}
    assert jumps_back(order_of(items, "rows_bl"), by, columns=False) == 0


def test_nested_parts_with_gaps_make_their_own_columns():
    # three columns of parts 100 wide with 10 mm gaps, the parts in a column a little out of line
    boxes = [(c * 110 + (r % 2) * 3, r * 60, 100, 50) for c in range(3) for r in range(5)]
    items = items_of(boxes)
    ex = np.array([it.bbox.xmin for it in items])
    cols = seq._rows(ex, [it.bbox.width for it in items], 0.0)
    assert sorted(set(cols.tolist())) == [0, 1, 2]
    assert [int(cols[k]) for k in range(len(items))] == [c for c in range(3) for _ in range(5)]
    order = order_of(items)
    assert order == [f"p{c * 5 + r}:0" for c in range(3) for r in range(5)]     # (each column bottom to top)


def test_in_a_column_the_part_on_the_left_beside_another_goes_first():
    # a narrow part, and a wider one to its right in the same column standing a little lower
    items = items_of([(0, 100, 40, 200), (50, 90, 120, 200)])
    assert order_of(items) == ["p0:0", "p1:0"]


def test_a_band_given_is_the_width_of_the_columns():
    items = items_of([(0, 0, 20, 20), (60, 0, 20, 20), (130, 0, 20, 20), (210, 0, 20, 20)])
    ex = np.array([it.bbox.xmin for it in items])
    cols = seq._rows(ex, [20.0] * 4, 100.0)
    assert cols.tolist() == [0, 0, 1, 2]

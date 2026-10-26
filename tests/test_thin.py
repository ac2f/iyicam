"""The fast thinning gives exactly what the plain whole-picture Zhang-Suen gives."""
import time

import numpy as np
import pytest

from iyicam.core.trace import thin


def thin_reference(mask):
    """Zhang-Suen on the whole picture at every pass (slow, obviously right)."""
    img = np.pad(np.asarray(mask, dtype=bool), 1).astype(np.uint8)
    when = np.zeros(img.shape, np.int32)
    inner = when[1:-1, 1:-1]
    passes = 0
    while True:
        changed = False
        for step in (0, 1):
            c = img[1:-1, 1:-1]
            nb = [img[:-2, 1:-1], img[:-2, 2:], img[1:-1, 2:], img[2:, 2:],
                  img[2:, 1:-1], img[2:, :-2], img[1:-1, :-2], img[:-2, :-2]]
            B = sum(n.astype(np.int8) for n in nb)
            A = sum(((nb[k] == 0) & (nb[(k + 1) % 8] == 1)).astype(np.int8) for k in range(8))
            p2, p4, p6, p8 = nb[0], nb[2], nb[4], nb[6]
            if step == 0:
                side = ((p2 & p4 & p6) == 0) & ((p4 & p6 & p8) == 0)
            else:
                side = ((p2 & p4 & p8) == 0) & ((p2 & p6 & p8) == 0)
            kill = (c == 1) & (B >= 2) & (B <= 6) & (A == 1) & side
            if kill.any():
                c[kill] = 0
                inner[kill] = passes + 1
                changed = True
        if not changed:
            break
        passes += 1
    return img[1:-1, 1:-1].astype(bool), inner.copy()


def blobs(rng, h, w, n):
    m = np.zeros((h, w), bool)
    for _ in range(n):
        y0, x0 = rng.integers(0, h), rng.integers(0, w)
        y1, x1 = min(h, y0 + rng.integers(1, h // 2 + 2)), min(w, x0 + rng.integers(1, w // 2 + 2))
        m[y0:y1, x0:x1] = rng.random() < 0.8
    return m


@pytest.mark.parametrize("seed", range(12))
def test_same_result_as_the_whole_picture_version(seed):
    rng = np.random.default_rng(seed)
    h, w = int(rng.integers(1, 60)), int(rng.integers(1, 60))
    mask = blobs(rng, h, w, int(rng.integers(1, 7)))
    if seed % 3 == 0:
        mask = mask | (rng.random((h, w)) < 0.3)             # noise: ragged edges, holes
    a, b = thin(mask), thin_reference(mask)
    assert np.array_equal(a[0], b[0]) and np.array_equal(a[1], b[1])


@pytest.mark.parametrize("mask", [np.zeros((0, 0), bool), np.zeros((5, 5), bool), np.ones((1, 1), bool),
                                  np.ones((7, 3), bool), np.eye(9, dtype=bool)])
def test_edge_cases(mask):
    a, b = thin(mask), thin_reference(mask)
    assert np.array_equal(a[0], b[0]) and np.array_equal(a[1], b[1])


def test_a_big_filled_picture_is_thinned_in_seconds():
    mask = np.zeros((2000, 2600), bool)
    mask[100:1900, 100:2500] = True
    t = time.time()
    sk, when = thin(mask)
    assert time.time() - t < 20
    assert 0 < sk.sum() < 5000 and when.max() > 500

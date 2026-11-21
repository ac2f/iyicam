"""Nesting the letters of texts (several fonts and sizes): the layouts are valid, and the letters are not made fatter
than they are (the outline point budget), so they lie as close as the gap asks."""
import pytest

from nest_check import verify

from iyicam.core.document import Project, VectorObject
from iyicam.core.geom import Transform
from iyicam.nesting.nest import NestSettings, nest
from iyicam.nesting.parts import prepare_nesting

pytest.importorskip("PySide6.QtWidgets")


def letters(qapp_text):
    p = Project("t")
    s = p.sheets[0]
    for k, (text, family, h) in enumerate((("DEKOTA", "DejaVu Serif", 160), ("Ağaç & 2026", "DejaVu Sans", 90),
                                            ("kuaför", "DejaVu Sans", 60))):
        for c in qapp_text(text, family, h):
            if c.closed and abs(c.area()) > 1:           # (each word on a line of its own: nothing overlaps at the start)
                s.vectors.append(VectorObject([c.transformed(Transform.translate(0, 400 * k))], p.layers[0].id))
    return p, s


@pytest.fixture
def qapp_text():
    from PySide6.QtWidgets import QApplication
    QApplication.instance() or QApplication([])
    from iyicam.gui.qtgeom import text_contours
    return text_contours


def _job(p, s, **kw):
    st = NestSettings(1230, 3050, 10, 6, [0.0, 90.0, 180.0, 270.0], "bottom", gravity_only=True, **kw)
    return prepare_nesting(p, list(s.vectors), st)


def test_text_layouts_are_valid_and_the_letters_are_not_made_fatter(qapp_text):
    p, s = letters(qapp_text)
    lengths = {}
    for mp in (40, 300):
        job = _job(p, s, iterations=6, max_points=mp)
        r = nest(job.shapes, job.settings)
        assert not r.unplaced and r.sheets == 1
        assert verify(job.shapes, r, job.settings) == []
        lengths[mp] = r.used[-1][3]
    assert lengths[300] < lengths[40]            # (40 points made curved letters up to 6 mm fatter on every side)


def test_polishing_and_the_time_left_never_make_a_text_layout_worse_or_invalid(qapp_text):
    p, s = letters(qapp_text)
    plain = _job(p, s, iterations=1)
    r1 = nest(plain.shapes, plain.settings)
    full = _job(p, s, iterations=8, time_limit=4)
    r2 = nest(full.shapes, full.settings)
    assert verify(full.shapes, r2, full.settings) == []
    assert r2.used[-1][3] <= r1.used[-1][3] + 1e-6

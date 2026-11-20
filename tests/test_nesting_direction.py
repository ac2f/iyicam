"""A direction chosen for free nesting is kept: Along X fills the width first and leaves one strip on top, Along Y the
height first and leaves one on the right; the best layout and its polishing are judged along that direction."""
from iyicam.core import shapes
from iyicam.nesting.nest import DIRECTIONS, NestSettings, NestShape, direction_of, nest


def _parts():
    out = []
    for w, h, q in ((300, 120, 4), (180, 180, 3), (90, 260, 4), (140, 70, 6)):
        c = shapes.rectangle(0, 0, w, h)
        out.append(NestShape([[(p[0], p[1]) for p in c.flatten(0.1)]], q))
    return out


def _run(direction: str, iterations: int = 6):
    g, only = DIRECTIONS[direction]
    st = NestSettings(1500.0, 3000.0, 10.0, 8.0, [0.0, 90.0], g, gravity_only=only, iterations=iterations)
    r = nest(_parts(), st)
    assert not r.unplaced and r.sheets == 1
    return r.used[-1]


def test_along_x_fills_the_width_and_is_short_in_y():
    x0, y0, x1, y1 = _run("along_x")
    assert x1 > 1300 and y1 < 1000          # across the 1500 mm sheet, low: the strip on top is free


def test_along_y_fills_the_height_and_is_narrow_in_x():
    x0, y0, x1, y1 = _run("along_y")
    assert y1 > x1 and x1 < 1000


def test_along_x_is_never_taller_than_what_automatic_would_pick():
    # Automatic judges by area and may grow along Y; Along X keeps the layout as low as it found
    assert _run("along_x", 10)[3] <= _run("auto", 10)[3] + 1e-6


def test_old_settings_map_to_a_direction():
    assert direction_of("left", True) == "along_y" and direction_of("reach_y", True) == "along_x"
    assert direction_of("left", False) == "along_x" and direction_of(None, None) == "along_x"

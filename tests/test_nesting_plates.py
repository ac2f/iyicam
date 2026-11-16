"""Several plate sizes with their own quantities, and a reserve plate for what does not fit.

3 sheets of one size and 2 of another are listed; when they are full the parts that are left go onto sheets of the
reserve size (as many as needed, or as many as allowed).  The reserve is always the last to be started, whatever its
size is compared with the others.
"""
import pytest
from conftest import make_project
from test_nesting import assert_on_plates, rect_poly

from iyicam.core import shapes
from iyicam.core.stock import StockItem
from iyicam.nesting.nest import Container, NestSettings, NestShape, nest
from iyicam.nesting.parts import nest_objects


def plates_settings(**kw):
    return NestSettings(margin=10, spacing=8, rotations=[0, 90], iterations=1, containers=[
        Container.rect(1000, 2000, quantity=3, name="100x200"),
        Container.rect(1500, 2000, quantity=2, name="150x200"),
        Container.rect(1200, 2400, quantity=0, reserve=True, name="reserve")], **kw)


def per_plate(res):
    return [sum(1 for p in res.placements if p.sheet == i) for i in range(res.sheets)]


def test_listed_plates_are_filled_in_their_order_then_the_reserve_plates():
    """The reserve (1200 x 2400) is bigger than the first plates and smaller than the second ones; it still waits for
    all of them (it used to be sorted among them by area and, having no limit, took everything left)."""
    st = plates_settings(stock_order="listed")
    shapes_ = [NestShape([rect_poly(480, 480)], 90)]
    res = nest(shapes_, st)
    assert not res.unplaced
    first = res.stock
    assert first[:5] == [0, 0, 0, 1, 1] and set(first[5:]) == {2} and len(first) > 5
    assert res.units[:5] == [0, 1, 2, 0, 1]
    assert_on_plates(shapes_, res, st)


def test_the_default_order_takes_the_reserve_last_too():
    st = plates_settings()                                   # leftovers, then the plates small to large
    res = nest([NestShape([rect_poly(480, 480)], 90)], st)
    assert not res.unplaced
    assert res.stock[:5] == [0, 0, 0, 1, 1] and set(res.stock[5:]) == {2}


def test_the_reserve_can_be_limited_and_what_does_not_fit_is_reported():
    st = plates_settings()
    st.containers[2].quantity = 1
    res = nest([NestShape([rect_poly(480, 480)], 400)], st)
    assert res.stock.count(2) == 1 and len(res.unplaced) > 0
    assert res.sheets == 6


def test_parts_that_fit_no_listed_plate_go_to_the_reserve():
    st = plates_settings()
    shapes_ = [NestShape([rect_poly(500, 500)], 4), NestShape([rect_poly(1150, 2300)], 1)]   # the big one fits the reserve only
    res = nest(shapes_, st)
    assert not res.unplaced
    big = next(p for p in res.placements if p.shape == 1)
    assert res.stock[big.sheet] == 2
    assert_on_plates(shapes_, res, st)


def area_of(st, res):
    return sum(st.containers[i].region[0][2][0] * st.containers[i].region[0][2][1] for i in res.stock)


def test_largest_order_fills_the_big_plates_first():
    shapes_ = [NestShape([rect_poly(700, 900)], 4)]          # 4 parts: 4 on a 150 x 200 plate, 2 on a 100 x 200
    res = nest(shapes_, plates_settings(stock_order="largest"))
    assert res.stock == [1] and not res.unplaced
    res = nest([NestShape([rect_poly(700, 900)], 2)], plates_settings())      # (small to large: the small plate takes both)
    assert res.stock == [0]


def test_the_best_order_of_the_plates_never_uses_more_material_than_the_order_listed():
    """``best`` tries the orders the sizes can be used in and keeps the one that uses the least material."""
    for parts, expect in ((4, [1]), (2, [0]), (6, None), (9, None), (11, None)):
        shapes_ = [NestShape([rect_poly(700, 900)], parts)]
        listed = nest(shapes_, plates_settings(stock_order="listed"))
        best = nest(shapes_, plates_settings(stock_order="best"))
        assert len(best.unplaced) <= len(listed.unplaced)
        if not best.unplaced and not listed.unplaced:
            st = plates_settings()
            assert area_of(st, best) <= area_of(st, listed) + 1e-6, parts
        if expect is not None:
            assert best.stock == expect, parts
    # a list the order of which is a bad one: the big sheet is listed last, and best takes it first for 4 parts
    st = plates_settings(stock_order="best")
    st.containers[0], st.containers[1] = st.containers[1], st.containers[0]
    res = nest([NestShape([rect_poly(700, 900)], 4)], st)
    assert res.sheets == 1 and st.containers[res.stock[0]].region[0][2] == (1500.0, 2000.0)


def test_nest_objects_makes_sheets_of_every_size_and_the_reserve(tmp_path):
    objs = [shapes.rectangle(0, 0, 480, 480) for _ in range(1)]
    p, s, vecs = make_project(objs, size=(500, 500))
    stock = [StockItem("A", 1000, 2000, None, 1, False), StockItem("B", 1500, 2000, None, 1, False)]
    st = NestSettings(1200, 2400, 10, 8, [0.0, 90.0], iterations=1)
    st.reserve_quantity = 2
    out = nest_objects(p, vecs, st, {vecs[0].id: 30}, [], stock=stock, use_sheets=[], new_sheets=True)
    assert out.unplaced == 0
    sizes = [(round(sh.width), round(sh.height)) for sh in out.sheets]
    assert sizes[:2] == [(1000, 2000), (1500, 2000)]
    assert all(sz == (1200, 2400) for sz in sizes[2:]) and 1 <= len(sizes) - 2 <= 2


# ---------------------------------------------------------------------------------------------
# plates of any shape
# ---------------------------------------------------------------------------------------------

def test_shaped_plates_have_the_size_of_their_box_and_their_corners_on_the_curve():
    import math
    from iyicam.core.stock import shaped_plate
    c = shaped_plate("circle", diameter=500, quantity=2)
    assert (c.width, c.height) == (500.0, 500.0) and c.quantity == 2 and c.outline and not c.is_rect
    assert all(abs(math.hypot(x - 250, y - 250) - 250) < 1e-3 for x, y in c.outline[0])        # on the circle, so inside it
    ring = shaped_plate("circle", diameter=500, hole=100)
    assert len(ring.outline) == 2 and abs(ring.area - (math.pi * 250 ** 2 - math.pi * 50 ** 2)) / ring.area < 0.01
    hexa = shaped_plate("polygon", sides=6, diameter=400)
    assert len(hexa.outline[0]) == 6 and hexa.height == pytest.approx(400)
    assert shaped_plate("l", width=500, height=400, notch_width=200, notch_height=150).area == pytest.approx(170000)
    assert shaped_plate("rect", width=300, height=200).outline is None
    for bad in (("polygon", dict(sides=2, diameter=10)), ("l", dict(width=10, height=10, notch_width=20, notch_height=5)),
                ("points", dict(points=[(0, 0), (1, 1)])), ("hexagon", {})):
        with pytest.raises(ValueError):
            shaped_plate(bad[0], **bad[1])


def test_parts_are_nested_inside_a_circle_and_inside_an_irregular_leftover():
    from iyicam.core.stock import shaped_plate
    circle = shaped_plate("circle", diameter=1000)
    offcut = shaped_plate("points", points=[(0, 0), (900, 0), (700, 500), (300, 700), (0, 400)])
    st = NestSettings(margin=10, spacing=8, rotations=[0, 90], iterations=1,
                      containers=[Container(circle.region(), name="circle"), Container(offcut.region(), name="offcut")],
                      stock_order="listed")
    shapes_ = [NestShape([rect_poly(150, 150)], 30)]
    res = nest(shapes_, st)
    assert not res.unplaced and res.sheets == 2
    assert_on_plates(shapes_, res, st)
    import math
    for pl in res.placements:                          # every corner of every part inside the circle of the first plate
        if res.stock[pl.sheet] == 0:
            for x, y in ((0, 0), (150, 0), (150, 150), (0, 150)):
                c, s = math.cos(math.radians(pl.angle)), math.sin(math.radians(pl.angle))
                px, py = x * c - y * s + pl.x, x * s + y * c + pl.y
                assert math.hypot(px - 500, py - 500) <= 500 - 10 + 1e-3


# ---------------------------------------------------------------------------------------------
# the service and the command line
# ---------------------------------------------------------------------------------------------

def _project():
    p, s, objs = make_project([shapes.rectangle(0, 0, 480, 480)], size=(500, 500))
    return p, objs


def test_the_service_takes_plates_a_reserve_and_the_order():
    from iyicam.service.host import ServiceHost
    host = ServiceHost(store=None)
    p, objs = _project()
    out = host.call("nest", {"project": p.to_dict(), "quantities": {objs[0].id: 40}, "settings": {
        "iterations": 1, "plates": [{"width": 1000, "height": 2000, "quantity": 1},
                                    {"shape": "circle", "diameter": 1500, "quantity": 1, "name": "Disc"}],
        "reserve": {"width": 1200, "height": 2400, "quantity": 2}}})
    names = [(x["name"], x["shape"], x["reserve"]) for x in out["sheets"]]
    assert names[:2] == [("1000 x 2000", False, False), ("Disc", True, False)]
    assert all(r for _, _, r in names[2:]) and len(names) <= 4
    assert out["placed"] + out["unplaced"] == 40
    sizes = {(round(x["width"]), round(x["height"])) for x in out["sheets"] if x["reserve"]}
    assert sizes == {(1200, 2400)}
    # no reserve: what does not fit is reported
    out = host.call("nest", {"project": p.to_dict(), "quantities": {objs[0].id: 40}, "settings": {
        "iterations": 1, "plates": [{"width": 1000, "height": 2000, "quantity": 1}], "reserve": False}})
    assert len(out["sheets"]) == 1 and out["unplaced"] > 0


def test_the_service_refuses_bad_plates_and_orders():
    from iyicam.service.errors import ServiceError
    from iyicam.service.host import ServiceHost
    host = ServiceHost(store=None)
    p, objs = _project()
    for settings in ({"plates": [{"shape": "circle"}]}, {"plates": [{"shape": "hexagon", "width": 5}]}, {"plates": "x"},
                     {"plates": [{"width": 100, "height": 100, "quantity": -1}]}, {"plate_order": "random"},
                     {"reserve": "big"}, {"place_on": "moon"}):
        with pytest.raises(ServiceError) as e:
            host.call("nest", {"project": p.to_dict(), "settings": dict({"iterations": 1}, **settings)})
        assert e.value.code == "bad_request"


def test_the_leftover_of_a_sheet_comes_back_as_plates_that_nest_can_take():
    from iyicam.service.host import ServiceHost
    host = ServiceHost(store=None)
    p, objs = _project()
    p.sheets[0].width, p.sheets[0].height = 1000, 600
    out = host.call("leftover", {"project": p.to_dict(), "min_area": 1000})
    assert out["plates"] and all(pl["outline"] for pl in out["plates"])
    again = host.call("nest", {"project": p.to_dict(), "objects": [], "quantities": {objs[0].id: 1},
                               "settings": {"iterations": 1, "plates": out["plates"][:1], "reserve": False}})
    assert again["placed"] == 1 and again["sheets"][0]["shape"]


def test_the_command_line_takes_plates_and_a_reserve(tmp_path, capsys):
    from iyicam import cli
    p, objs = _project()
    src, dst = tmp_path / "a.icam", tmp_path / "o.icam"
    p.save(str(src))
    code = cli.main(["nest", str(src), "-o", str(dst), "--quantity", "30", "--iterations", "1", "--plate", "1000x2000:1",
                     "--plate-circle", "1200:1", "--reserve", "1200x2400:1"])
    out = capsys.readouterr().out
    assert code == 0 and "1000 x 2000" in out and "(shape)" in out and "(reserve)" in out
    code = cli.main(["nest", str(src), "-o", str(dst), "--plate", "oops"])
    assert code == 2

import itertools
import math

import pytest
from conftest import make_project

from iyicam.core import clip, shapes
from iyicam.core.geom import polygon_area
from iyicam.nesting.nest import NestSettings, NestShape, nest
from iyicam.nesting.parts import group_parts, nest_objects


def placed_polys(shapes_, res):
    out = []
    for pl in res.placements:
        c, s = math.cos(math.radians(pl.angle)), math.sin(math.radians(pl.angle))
        for poly in shapes_[pl.shape].polys:
            out.append((pl.sheet, [(x * c - y * s + pl.x, x * s + y * c + pl.y) for x, y in poly]))
    return out


def assert_valid(shapes_, res, st: NestSettings):
    polys = placed_polys(shapes_, res)
    for sheet, poly in polys:
        for x, y in poly:
            assert st.margin - 1e-3 <= x <= st.sheet_width - st.margin + 1e-3
            assert st.margin - 1e-3 <= y <= st.sheet_height - st.margin + 1e-3
    shrink = st.spacing / 2 - 0.05  # parts must keep the spacing between them
    for (s1, a), (s2, b) in itertools.combinations(polys, 2):
        if s1 != s2:
            continue
        ga = clip.offset([a], shrink)
        gb = clip.offset([b], shrink)
        inter = clip.boolean(ga, gb, "intersection")
        assert sum(abs(polygon_area(p)) for p in inter) < 1e-3


def rect_poly(w, h):
    return [(0, 0), (w, 0), (w, h), (0, h)]


def test_rectangles_fit_on_one_sheet_without_overlap():
    st = NestSettings(1000, 1000, margin=10, spacing=10, rotations=[0, 90])
    shapes_ = [NestShape([rect_poly(300, 200)], quantity=10)]
    res = nest(shapes_, st)
    assert res.sheets == 1 and not res.unplaced and len(res.placements) == 10
    assert_valid(shapes_, res, st)
    assert res.utilization[0] == pytest.approx(10 * 300 * 200 / 1e6)


def test_irregular_parts_interlock_and_rotate():
    st = NestSettings(600, 400, margin=5, spacing=4, rotations=[0, 90, 180, 270])
    L = [(0, 0), (200, 0), (200, 60), (60, 60), (60, 200), (0, 200)]
    tri = [(0, 0), (150, 0), (0, 150)]
    circ = shapes.circle((0, 0), 40).flatten(0.2)
    shapes_ = [NestShape([L], 3), NestShape([tri], 4), NestShape([circ], 3)]
    res = nest(shapes_, st)
    assert not res.unplaced
    assert_valid(shapes_, res, st)


def test_overflow_creates_more_sheets_and_reports_unplaced():
    st = NestSettings(500, 500, margin=0, spacing=0, rotations=[0])
    res = nest([NestShape([rect_poly(240, 240)], 9), NestShape([rect_poly(600, 10)], 1)], st)
    assert res.sheets == 3
    assert res.unplaced == [(1, 0)]  # does not fit any sheet


def test_iterations_never_worse():
    st = NestSettings(800, 400, margin=5, spacing=5)
    shapes_ = [NestShape([rect_poly(170, 90)], 6), NestShape([[(0, 0), (120, 0), (60, 100)]], 6)]
    one = nest(shapes_, st)
    st.iterations = 6
    many = nest(shapes_, st)
    assert (len(many.unplaced), many.sheets) <= (len(one.unplaced), one.sheets)
    assert_valid(shapes_, many, st)


def test_group_parts_holes_and_part_in_part():
    outer = shapes.rectangle(0, 0, 200, 200)
    hole = shapes.circle((100, 100), 60)
    disk = shapes.circle((100, 100), 30)         # a separate part sitting in the hole
    text = shapes.polyline([(10, 10), (40, 10)])  # engraving on the big part
    dot = shapes.circle((100, 100), 5)           # hole of the disk
    p, s, objs = make_project([outer, hole, disk, text, dot])
    parts = group_parts(objs)
    assert len(parts) == 2
    big = next(pt for pt in parts if pt.root is objs[0])
    small = next(pt for pt in parts if pt.root is objs[2])
    assert {o.id for o in big.members} == {objs[1].id, objs[3].id}
    assert [o.id for o in small.members] == [objs[4].id]


def test_nest_objects_moves_vectors_and_makes_copies():
    a = shapes.rectangle(0, 0, 100, 50)
    hole = shapes.circle((50, 25), 10)
    b = shapes.circle((300, 300), 30)
    p, s, objs = make_project([a, hole, b], size=(400, 300))
    st = NestSettings(400, 300, margin=10, spacing=6)
    out = nest_objects(p, objs, st, quantities={objs[0].id: 3}, target_sheets=[s])
    assert out.unplaced == 0 and out.placed == 4
    vecs = [v for sh in p.sheets for v in sh.vectors]
    assert len(vecs) == 3 * 2 + 1  # three rectangles with their hole + the disk
    for sh in out.sheets:
        for v in sh.vectors:
            bb = v.bbox()
            assert bb.xmin >= 10 - 1e-6 and bb.ymin >= 10 - 1e-6
            assert bb.xmax <= 390 + 1e-6 and bb.ymax <= 290 + 1e-6
    # holes stay centred in their rectangles
    rects = [v for sh in p.sheets for v in sh.vectors if len(v.contours[0].segs) == 4 and v.contours[0].area() > 4000]
    holes = [v for sh in p.sheets for v in sh.vectors if v.contours[0].area() == pytest.approx(math.pi * 100, rel=1e-6)]
    assert len(rects) == 3 and len(holes) == 3
    for r in rects:
        c = r.bbox().center
        assert any(math.dist(c, h.bbox().center) < 1e-6 for h in holes)


def test_non_convex_decomposition_keeps_parts_apart():
    st = NestSettings(700, 500, margin=5, spacing=3, rotations=[0, 45, 90, 135, 180, 225, 270, 315])
    star = shapes.star((0, 0), 70, 30, 7).flatten()
    U = [(0, 0), (150, 0), (150, 120), (110, 120), (110, 40), (40, 40), (40, 120), (0, 120)]
    shapes_ = [NestShape([star], 6), NestShape([U], 4), NestShape([shapes.circle((0, 0), 18).flatten(0.1)], 6)]
    res = nest(shapes_, st)
    assert not res.unplaced
    assert_valid(shapes_, res, st)


def test_convex_parts_cover_the_polygon():
    import pyclipper
    from iyicam.nesting.nest import convex_parts, is_convex
    for poly in ([(0, 0), (100, 0), (100, 100), (50, 40), (0, 100)], shapes.star((0, 0), 50, 20, 9).flatten()):
        ip = clip.to_int(poly)
        parts = convex_parts(ip)
        assert parts and all(is_convex(p) for p in parts)
        assert sum(abs(pyclipper.Area(p)) for p in parts) == pytest.approx(abs(pyclipper.Area(ip)))


# ---------------------------------------------------------------------------
# plates of any shape, leftovers, several sizes
# ---------------------------------------------------------------------------

from iyicam.nesting.nest import Container  # noqa: E402


def assert_on_plates(shapes_, res, st: NestSettings):
    """Every part lies inside the material of its plate (margin kept to the plate's edges and holes), keeps the
    spacing to what is fixed on the plate and to the other parts."""
    cons = st.containers
    for pl in res.placements:
        box = cons[res.stock[pl.sheet]]
        c, s = math.cos(math.radians(pl.angle)), math.sin(math.radians(pl.angle))
        for poly in shapes_[pl.shape].polys:
            moved = [(x * c - y * s + pl.x, x * s + y * c + pl.y) for x, y in poly]
            inner = clip.offset(clip.normalize(box.region), -(st.margin - 0.05))
            outside = clip.boolean([moved], inner, "difference")
            assert sum(abs(polygon_area(p)) for p in outside) < 1e-2, f"part {pl} leaves the plate"
            if box.obstacles:
                near = clip.boolean(clip.offset([moved], st.spacing / 2 - 0.05),
                                    clip.offset(box.obstacles, st.spacing / 2), "intersection")
                assert sum(abs(polygon_area(p)) for p in near) < 1e-2, f"part {pl} touches what is fixed"
    # parts keep the spacing between them (placements of one plate only)
    flat = [(p.sheet, poly) for p, poly in zip(
        [pl for pl in res.placements for _ in shapes_[pl.shape].polys], [
            [(x * math.cos(math.radians(pl.angle)) - y * math.sin(math.radians(pl.angle)) + pl.x,
              x * math.sin(math.radians(pl.angle)) + y * math.cos(math.radians(pl.angle)) + pl.y) for x, y in poly]
            for pl in res.placements for poly in shapes_[pl.shape].polys])]
    for (s1, a), (s2, b) in itertools.combinations(flat, 2):
        if s1 == s2:
            inter = clip.boolean(clip.offset([a], st.spacing / 2 - 0.05), clip.offset([b], st.spacing / 2 - 0.05),
                                 "intersection")
            assert sum(abs(polygon_area(p)) for p in inter) < 1e-3


def test_l_shaped_leftover():
    L = [(0, 0), (600, 0), (600, 200), (300, 200), (300, 400), (0, 400)]
    st = NestSettings(margin=10, spacing=8, rotations=[0, 90], containers=[Container([L])])
    shapes_ = [NestShape([rect_poly(150, 150)], 4)]
    res = nest(shapes_, st)
    assert res.sheets == 1 and not res.unplaced
    assert_on_plates(shapes_, res, st)
    assert res.utilization[0] == pytest.approx(4 * 150 * 150 / 180000)
    # nothing can go into the missing corner: five do not fit on one plate
    shapes_ = [NestShape([rect_poly(150, 150)], 5)]
    assert nest(shapes_, NestSettings(margin=10, spacing=8, rotations=[0, 90],
                                      containers=[Container([L], quantity=1)])).unplaced == [(0, 4)]


def test_plate_with_a_hole_and_a_triangle_plate():
    hole = shapes.circle((300, 200), 100).flatten(0.2)
    st = NestSettings(margin=10, spacing=8, rotations=[0, 90], containers=[Container([rect_poly(600, 400), hole])])
    shapes_ = [NestShape([rect_poly(150, 150)], 10)]
    res = nest(shapes_, st)
    assert not res.unplaced and res.sheets == 3  # four fit round the hole
    assert_on_plates(shapes_, res, st)
    tri = [(0, 0), (1000, 0), (0, 600)]
    st = NestSettings(margin=10, spacing=8, rotations=[0, 90, 180, 270], containers=[Container([tri], quantity=1)])
    shapes_ = [NestShape([rect_poly(120, 80)], 30)]
    res = nest(shapes_, st)
    assert res.sheets == 1 and len(res.placements) + len(res.unplaced) == 30 and len(res.placements) >= 16
    assert_on_plates(shapes_, res, st)


def test_parts_fixed_on_the_plate_are_avoided():
    fixed = [rect_poly(200, 150)]
    fixed = [[(x + 200, y + 125) for x, y in fixed[0]]]
    st = NestSettings(margin=10, spacing=8, rotations=[0, 90],
                      containers=[Container([rect_poly(600, 400)], obstacles=fixed)])
    shapes_ = [NestShape([rect_poly(140, 100)], 12)]
    res = nest(shapes_, st)
    assert res.sheets == 2 and len(res.placements) == 12
    assert_on_plates(shapes_, res, st)
    # utilisation is measured on the free area
    assert res.utilization[0] == pytest.approx(
        sum(140 * 100 for p in res.placements if p.sheet == 0) / (600 * 400 - 200 * 150))


def test_leftovers_are_used_first_and_the_stock_is_counted():
    st = NestSettings(margin=10, spacing=8, rotations=[0, 90], containers=[
        Container.rect(1000, 1000, name="new"),
        Container.rect(300, 300, quantity=1, leftover=True, name="small"),
        Container.rect(500, 300, quantity=1, leftover=True, name="mid")])
    shapes_ = [NestShape([rect_poly(140, 140)], 14)]
    res = nest(shapes_, st)
    assert not res.unplaced
    assert res.stock == [1, 2, 0]  # small leftover, bigger leftover, then a new sheet
    assert [sum(1 for p in res.placements if p.sheet == i) for i in range(3)] == [1, 3, 10]
    assert_on_plates(shapes_, res, st)
    # with the leftovers big enough nothing new is started
    res = nest([NestShape([rect_poly(140, 140)], 4)], st)
    assert res.stock == [1, 2] and not res.unplaced
    # listed order: the user's order
    st.stock_order = "listed"
    res = nest([NestShape([rect_poly(140, 140)], 4)], st)
    assert res.stock == [0]


def test_limited_stock_runs_out_and_reports_unplaced():
    st = NestSettings(margin=5, spacing=4, rotations=[0, 90], containers=[
        Container.rect(400, 300, quantity=2), Container.rect(100, 100, quantity=3, leftover=True)])
    shapes_ = [NestShape([rect_poly(180, 130)], 10), NestShape([rect_poly(60, 60)], 2)]
    res = nest(shapes_, st)
    # the 4 big parts of one plate each: 2 plates take 2 x 4 = 8 big parts, 2 stay unplaced; small ones go to leftovers
    assert res.sheets == 4 and len(res.unplaced) == 2
    assert sorted(res.stock) == [0, 0, 1, 1]
    assert all(s == 0 for s, _ in res.unplaced)
    assert_on_plates(shapes_, res, st)


def test_a_plate_nothing_fits_is_skipped_and_oversize_parts_are_unplaced():
    st = NestSettings(margin=10, spacing=8, rotations=[0, 90], containers=[
        Container.rect(60, 60, leftover=True), Container.rect(500, 500, quantity=1)])
    res = nest([NestShape([rect_poly(100, 100)], 2), NestShape([rect_poly(900, 900)], 1)], st)
    assert res.stock == [1] and res.unplaced == [(1, 0)] and len(res.placements) == 2


def test_nest_objects_onto_stock_makes_sheets_with_outlines(tmp_path):
    from iyicam.nesting.stock import StockItem
    p, s, objs = make_project([shapes.rectangle(0, 0, 100, 60), shapes.rectangle(0, 0, 90, 90)], size=(500, 400))
    L = [(0, 0), (300, 0), (300, 150), (150, 150), (150, 300), (0, 300)]
    stock = [StockItem("L leftover", 300, 300, outline=[L], quantity=1, leftover=True, thickness=12.0),
             StockItem("Sheet", 1220, 610)]
    st = NestSettings(margin=5, spacing=4, rotations=[0, 90])
    out = nest_objects(p, objs, st, {objs[0].id: 5, objs[1].id: 5}, [], stock=stock)
    assert out.unplaced == 0 and out.placed == 10
    first = out.sheets[0]
    assert first.name.startswith("L leftover") and first.thickness == 12.0
    assert (first.width, first.height) == (300, 300) and first.outline
    assert all(not v.bbox().width > 300 for v in first.vectors)
    plain = out.sheets[-1]
    assert plain.outline is None and (plain.width, plain.height) == (1220, 610)


def test_stock_from_drawn_outlines_with_holes():
    from iyicam.nesting.stock import stock_from_vectors
    outline = shapes.polyline([(100, 100), (500, 100), (500, 300), (300, 300), (300, 500), (100, 500)], True)
    hole = shapes.circle((200, 200), 40)
    p, s, objs = make_project([[outline], [hole], [shapes.rectangle(700, 100, 200, 100)]])
    items = stock_from_vectors(objs, quantity=2)
    assert len(items) == 2
    L = max(items, key=lambda it: it.area)
    assert (L.width, L.height, L.quantity, L.leftover) == (pytest.approx(400), pytest.approx(400), 2, True)
    xs = [x for poly in L.outline for x, _ in poly]
    assert min(xs) == pytest.approx(0)  # the origin is at the corner of the bounding box
    assert len(L.outline) == 2  # the circle is a hole of the plate
    assert L.area == pytest.approx(400 * 200 + 200 * 200 - math.pi * 40 ** 2, rel=2e-3)
    assert min(items, key=lambda it: it.area).is_rect is False  # even a rectangle keeps its (4 point) outline


def test_leftover_of_a_cut_sheet_is_nested_again():
    from iyicam.nesting.stock import leftover_of_sheet
    p, s, objs = make_project([shapes.rectangle(20, 20, 300, 200), shapes.rectangle(340, 20, 100, 360)],
                              size=(600, 400))
    items = leftover_of_sheet(s, clearance=3.0, min_width=30.0, min_area=2500)
    # what is left: the strip above the first part and the area right of the second one (one piece
    # with the strip at the top and bottom), without the 3 mm cut round the parts
    assert items and all(it.leftover and it.quantity == 1 for it in items)
    total = sum(it.area for it in items)
    used = (300 + 6) * (200 + 6) + (100 + 6) * (360 + 6)
    assert total == pytest.approx(600 * 400 - used, rel=0.2)
    assert total <= 600 * 400 - used + 1e-6  # never more than there is
    big = items[0]
    # a new job goes onto it and stays inside the material
    p2, s2, objs2 = make_project([shapes.rectangle(0, 0, 60, 40)], size=(600, 400))
    st = NestSettings(margin=5, spacing=4, rotations=[0, 90])
    out = nest_objects(p2, objs2, st, {objs2[0].id: 6}, [], stock=[big], new_sheets=False)
    assert out.unplaced == 0
    sheet = out.sheets[0]
    mat = clip.normalize(sheet.region())
    for v in sheet.vectors:
        outside = clip.boolean(clip.contours_to_polys(v.contours), mat, "difference")
        assert sum(abs(polygon_area(q)) for q in outside) < 1e-3


def test_new_parts_go_round_what_stays_on_a_sheet():
    p, s, objs = make_project([shapes.rectangle(200, 100, 200, 200), shapes.rectangle(0, 0, 80, 50)], size=(600, 400))
    fixed, part = objs
    out = nest_objects(p, [part], NestSettings(margin=10, spacing=8, rotations=[0, 90]), {part.id: 12},
                       use_sheets=[s], new_sheets=False)
    assert out.unplaced == 0 and out.sheets == [s]
    assert fixed in s.vectors and fixed.bbox().xmin == pytest.approx(200)  # the fixed part did not move
    for v in s.vectors:
        if v is fixed:
            continue
        bb = v.bbox()
        assert bb.xmin >= 10 - 1e-6 and bb.xmax <= 590 + 1e-6 and bb.ymin >= 10 - 1e-6 and bb.ymax <= 390 + 1e-6
        gap = clip.boolean(clip.offset(clip.contours_to_polys(v.contours), 3.9),
                           clip.offset(clip.contours_to_polys(fixed.contours), 0), "intersection")
        assert not gap  # the 8 mm gap to the fixed part is kept


def test_stock_and_outlines_survive_saving(tmp_path):
    from iyicam.core.document import Project
    from iyicam.core.stock import StockItem, load_stock, save_stock
    p, s, objs = make_project([shapes.rectangle(0, 0, 10, 10)])
    L = [[(0, 0), (300, 0), (300, 150), (150, 150), (150, 300), (0, 300)]]
    p.stock = [StockItem("L", 300, 300, L, 3, True, 12.0), StockItem("Full", 2440, 1220)]
    p.sheets[0].outline = L
    p.sheets[0].width = p.sheets[0].height = 300
    p.save(tmp_path / "j.icam")
    q = Project.load(tmp_path / "j.icam")
    assert [(i.name, i.width, i.quantity, i.leftover, i.thickness) for i in q.stock] == \
        [("L", 300, 3, True, 12.0), ("Full", 2440, 0, False, None)]
    assert q.stock[0].outline == [[tuple(pt) for pt in poly] for poly in L]
    assert q.sheets[0].outline == [[tuple(pt) for pt in poly] for poly in L]
    assert q.sheets[0].region() == q.sheets[0].outline
    assert q.stock[1].is_rect and q.stock[1].region() == [[(0, 0), (2440, 0), (2440, 1220), (0, 1220)]]
    save_stock(tmp_path / "stock.json", p.stock)
    again = load_stock(tmp_path / "stock.json")
    assert [i.name for i in again] == ["L", "Full"] and again[0].id != p.stock[0].id
    (tmp_path / "bad.json").write_text('{"format": "other"}')
    with pytest.raises(ValueError):
        load_stock(tmp_path / "bad.json")


def test_cli_nests_onto_leftovers_and_counts_the_stock_down(tmp_path, capsys):
    from iyicam.cli import main
    from iyicam.core.document import Project
    from iyicam.core.stock import StockItem
    p, s, objs = make_project([shapes.rectangle(0, 0, 100, 60)], size=(500, 400))
    p.stock = [StockItem("shop offcut", 400, 300, None, 2, True)]
    p.save(tmp_path / "j.icam")
    rc = main(["--no-plugins", "nest", str(tmp_path / "j.icam"), "-o", str(tmp_path / "n.icam"),
               "--leftover", "300x200:1", "--no-new-sheets", "--quantity", "3", "--margin", "5", "--spacing", "4"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "placed 3, unplaced 0" in out and "300 x 200" in out
    q = Project.load(tmp_path / "n.icam")
    assert (q.sheets[0].width, q.sheets[0].height) == (300, 200) and q.sheets[0].name.startswith("Leftover")
    # the 300 x 200 leftover took them all and is used up; the offcuts were not touched
    assert [(i.name, i.quantity) for i in q.stock] == [("shop offcut", 2)]


def _blob(cx, cy, rx, ry, n=60, wob=0.07):
    return [(cx + rx * math.cos(2 * math.pi * k / n) * (1 + wob * math.sin(5 * k)),
             cy + ry * math.sin(2 * math.pi * k / n) * (1 + wob * math.cos(3 * k))) for k in range(n)]


def mixed_job_on_irregular_plates():
    """A mixed job on leftovers of different shapes (a hole, a bite, a wobbly outline, a trapezoid)."""
    from iyicam.core.stock import normalized
    from iyicam.nesting.stock import StockItem

    def plate(name, polys):
        w, h, outline = normalized(polys)
        return StockItem(name, w, h, outline, 1, True)

    hole = [(x + 250, y + 450) for x, y in shapes.circle((0, 0), 55).flatten(0.2)]
    items = [plate("L with hole", [[(0, 0), (900, 0), (900, 330), (520, 330), (520, 700), (0, 700)], hole]),
             plate("wobbly", [_blob(300, 260, 290, 250)]),
             plate("trapezoid", [[(0, 0), (800, 0), (620, 380), (120, 380)]]),
             StockItem("new", 1220, 810)]
    circ = lambda r: shapes.circle((0, 0), r).flatten(0.2)  # noqa: E731
    L = [(0, 0), (140, 0), (140, 45), (52, 45), (52, 130), (0, 130)]
    tri = [(0, 0), (150, 0), (70, 110)]
    spec = [(rect_poly(150, 90), 14), (circ(48), 8), (L, 10), (tri, 12), (rect_poly(220, 70), 8), (circ(30), 14)]
    return items, [NestShape([poly], n) for poly, n in spec]


def test_nothing_sticks_out_of_an_irregular_plate_or_lies_in_its_hole():
    """Positions where a part only touches the plate's boundary with its reference point, entirely outside
    the plate or in a hole, are zero width slivers of the feasible area: they must not be used."""
    from iyicam.nesting.stock import to_container
    items, shapes_ = mixed_job_on_irregular_plates()
    st = NestSettings(margin=12, spacing=10, rotations=[0, 90, 180, 270], containers=[to_container(i) for i in items])
    res = nest(shapes_, st)
    assert not res.unplaced and res.sheets >= 3
    assert_on_plates(shapes_, res, st)
    # the smallest leftover is filled first
    assert res.stock[0] == 1

"""The operations of the draw features of the web app (iyicam/service/ops_draw.py) and the engine code under them:
text from font files (core/textgeom.py) and start places (core/startplace.py)."""
import base64
import json
import math

import pytest

from conftest import add_tp, end_mill, make_project
from iyicam.core import shapes, textgeom
from iyicam.core.document import vector_to_dict
from iyicam.core.geom import Contour, contours_bbox
from iyicam.service import ops
from iyicam.service.errors import ServiceError


def rect(x0, y0, x1, y1):
    return Contour.polyline([(x0, y0), (x1, y0), (x1, y1), (x0, y1)], True)


def project_with(*contours):
    p, s, objs = make_project([[c] for c in contours])
    return p, s, objs


def call(name, payload):
    return getattr(ops, "op_" + name)(json.loads(json.dumps(payload)))     # (as the browser does: JSON in, JSON out)


def test_ops_are_part_of_ops_py():
    for n in ("vector_edit", "start_points", "start_probe", "trim", "text", "fonts", "text_on_curve", "trace_bitmap", "veclib", "sheet_outline"):
        assert callable(getattr(ops, "op_" + n))


# ------------------------------------------------------------------------------------------ edit nodes
def vec_of(project, i=0):
    return vector_to_dict(project.sheets[0].vectors[i])


def test_move_nodes_moves_a_corner_and_its_neighbours():
    p, s, objs = project_with(rect(0, 0, 10, 10))
    v = vec_of(p)
    r = call("vector_edit", {"vectors": [v], "action": "move_nodes", "nodes": [[v["id"], 0, 2]], "dx": 5, "dy": 3})
    nodes = [tuple(map(float, n)) for n in _nodes(r["vectors"][0])]
    assert (15.0, 13.0) in nodes and len(nodes) == 4


def _nodes(vd):
    from iyicam.core.document import vector_from_dict
    return vector_from_dict(vd).contours[0].nodes()


def test_move_several_nodes_by_the_same_step():
    p, s, objs = project_with(rect(0, 0, 10, 10))
    v = vec_of(p)
    r = call("vector_edit", {"vectors": [v], "action": "move_nodes", "nodes": [[v["id"], 0, 1], [v["id"], 0, 2]], "to": [20, 0]})
    pts = _nodes(r["vectors"][0])
    assert pts[1] == (20.0, 0.0) and pts[2] == (20.0, 10.0)


def test_insert_delete_break_convert_reverse_open_close():
    p, s, objs = project_with(rect(0, 0, 10, 10))
    v = vec_of(p)
    r = call("vector_edit", {"vectors": [v], "action": "insert", "vector": v["id"], "contour": 0, "seg": 0, "t": 0.5})
    assert len(_nodes(r["vectors"][0])) == 5
    v2 = r["vectors"][0]
    r = call("vector_edit", {"vectors": [v2], "action": "delete_nodes", "nodes": [[v["id"], 0, 1]]})
    assert len(_nodes(r["vectors"][0])) == 4
    r = call("vector_edit", {"vectors": [v], "action": "convert", "vector": v["id"], "contour": 0, "seg": 0, "kind": "arc"})
    assert r["vectors"][0]["contours"][0]["segs"][0][0] == "A"
    r = call("vector_edit", {"vectors": [v], "action": "convert", "vector": v["id"], "contour": 0, "seg": 0, "kind": "bezier"})
    assert r["vectors"][0]["contours"][0]["segs"][0][0] == "C"
    r = call("vector_edit", {"vectors": [v], "action": "open", "vector": v["id"], "contour": 0})
    assert r["vectors"][0]["contours"][0]["closed"] is False
    r2 = call("vector_edit", {"vectors": r["vectors"], "action": "close", "vector": v["id"], "contour": 0})
    assert r2["vectors"][0]["contours"][0]["closed"] is True
    r = call("vector_edit", {"vectors": [v], "action": "break", "vector": v["id"], "contour": 1 - 1, "seg": 1, "t": 0.5})
    assert len(r["vectors"][0]["contours"]) == 1 and not r["vectors"][0]["contours"][0]["closed"]
    r = call("vector_edit", {"vectors": [v], "action": "reverse", "vector": v["id"], "contour": 0})
    assert r["vectors"][0]["contours"][0]["start"] != v["contours"][0]["start"] or r["vectors"][0]["contours"][0]["segs"] != v["contours"][0]["segs"]


def test_move_a_bezier_handle():
    c = shapes.ellipse((0, 0), 10, 5)
    p, s, objs = project_with(c)
    v = vec_of(p)
    seg = v["contours"][0]["segs"][0]
    assert seg[0] == "C"
    r = call("vector_edit", {"vectors": [v], "action": "move_control", "vector": v["id"], "contour": 0, "seg": 0, "which": 1, "to": [30, 30]})
    assert r["vectors"][0]["contours"][0]["segs"][0][1:3] == [30, 30]


def test_vector_edit_refuses_what_is_not_there():
    p, s, objs = project_with(rect(0, 0, 10, 10))
    v = vec_of(p)
    with pytest.raises(ServiceError):
        call("vector_edit", {"vectors": [v], "action": "move_nodes", "nodes": [[v["id"], 0, 9]], "dx": 1, "dy": 1})
    with pytest.raises(ServiceError):
        call("vector_edit", {"vectors": [v], "action": "nope"})


def test_set_start_by_edit_keeps_it():
    p, s, objs = project_with(rect(0, 0, 10, 10))
    v = vec_of(p)
    r = call("vector_edit", {"vectors": [v], "action": "set_start", "vector": v["id"], "contour": 0, "point": [10, 5]})
    out = r["vectors"][0]
    assert out["contours"][0]["start"] == [10, 5] and out["locked_starts"] == [0]


# ------------------------------------------------------------------------------------------ start points
def start_of(project, i=0, ci=0):
    return project["sheets"][0]["vectors"][i]["contours"][ci]["start"]


def test_start_at_corners_and_sides():
    p, s, objs = project_with(rect(0, 0, 10, 20))
    base = {"project": p.to_dict(), "sheet": s.id, "selection": [objs[0].id], "action": "place"}
    for place, want in (("sw", [0, 0]), ("se", [10, 0]), ("nw", [0, 20]), ("ne", [10, 20]), ("n", [5, 20]), ("s", [5, 0]), ("w", [0, 10]), ("e", [10, 10])):
        r = call("start_points", {**base, "place": place})
        assert r["changed"] and r["project"], place
        assert start_of(r["project"]) == pytest.approx(want), place
        assert r["project"]["sheets"][0]["vectors"][0]["locked_starts"] == [0]


def test_start_next_previous_opposite_release():
    p, s, objs = project_with(rect(0, 0, 10, 20))
    base = {"project": p.to_dict(), "sheet": s.id, "selection": [objs[0].id]}
    r = call("start_points", {**base, "action": "place", "place": "sw"})
    proj = r["project"]
    nxt = call("start_points", {**base, "project": proj, "action": "step", "direction": 1})
    assert start_of(nxt["project"]) != start_of(proj)
    back = call("start_points", {**base, "project": nxt["project"], "action": "step", "direction": -1})
    assert start_of(back["project"]) == pytest.approx(start_of(proj))
    opp = call("start_points", {**base, "project": proj, "action": "opposite"})
    assert start_of(opp["project"]) == pytest.approx([10, 20]) or start_of(opp["project"]) != start_of(proj)
    rel = call("start_points", {**base, "project": opp["project"], "action": "release"})
    assert rel["project"]["sheets"][0]["vectors"][0].get("locked_starts", []) == []
    assert rel["moved"] == 1


def test_start_points_say_when_nothing_can_be_done():
    p, s, objs = project_with(Contour.polyline([(0, 0), (10, 0), (10, 10)], False))
    r = call("start_points", {"project": p.to_dict(), "sheet": s.id, "selection": [objs[0].id], "action": "place", "place": "sw"})
    assert not r["changed"] and r["project"] is None and "closed" in r["message"]
    with pytest.raises(ServiceError):
        call("start_points", {"project": p.to_dict(), "sheet": s.id, "selection": [], "action": "place", "place": "zz"})


def test_start_set_at_a_point():
    p, s, objs = project_with(rect(0, 0, 10, 20))
    r = call("start_points", {"project": p.to_dict(), "sheet": s.id, "action": "set", "items": [[objs[0].id, 0, [10, 7]]]})
    assert start_of(r["project"]) == pytest.approx([10, 7])


def test_smart_start_needs_a_toolpath_and_then_moves_unlocked_starts():
    p, s, objs = project_with(rect(20, 20, 120, 80))
    sel = [objs[0].id]
    r = call("start_points", {"project": p.to_dict(), "sheet": s.id, "selection": sel, "action": "smart"})
    assert not r["changed"] and "toolpath" in r["message"]
    add_tp(p, "profile", end_mill(6), {"cut_side": "outside"}, objs)
    pd = p.to_dict()
    kept = call("start_points", {"project": pd, "sheet": s.id, "selection": sel, "action": "place", "place": "sw"})["project"]
    r = call("start_points", {"project": kept, "sheet": s.id, "selection": sel, "action": "smart"})
    vec = r["project"]["sheets"][0]["vectors"][0] if r["project"] else kept["sheets"][0]["vectors"][0]
    assert vec.get("locked_starts", []) == []                  # (given back to smart start points first)
    assert "start point" in r["message"]


def test_start_probe_clings_to_corners_and_middles():
    c = rect(0, 0, 10, 10)
    from iyicam.core.document import contour_to_dict
    cd = contour_to_dict(c)
    r = call("start_probe", {"contour": cd, "point": [9.5, 0.2], "reach": 2, "features": True})
    assert r["kind"] == "node" and r["point"] == pytest.approx([10, 0]) and r["features"]
    r = call("start_probe", {"contour": cd, "point": [5.2, 0.1], "reach": 1})
    assert r["kind"] == "mid" and r["point"] == pytest.approx([5, 0])
    r = call("start_probe", {"contour": cd, "point": [2.5, 0.1], "reach": 1})
    assert r["kind"] == "edge" and r["point"][1] == pytest.approx(0)
    r = call("start_probe", {"contour": cd, "point": [9.9, 0.1], "reach": 3, "cling": False})
    assert r["kind"] == "edge"


# ------------------------------------------------------------------------------------------ trim
def test_trim_removes_the_piece_between_two_crossings():
    p, s, objs = project_with(Contour.polyline([(0, 5), (30, 5)], False), Contour.polyline([(10, 0), (10, 10)], False), Contour.polyline([(20, 0), (20, 10)], False))
    r = call("trim", {"project": p.to_dict(), "sheet": s.id, "vector": objs[0].id, "point": [15, 5]})
    assert r["changed"]
    line = [v for v in r["project"]["sheets"][0]["vectors"] if v["id"] == objs[0].id][0]
    assert len(line["contours"]) == 2
    r = call("trim", {"project": p.to_dict(), "sheet": s.id, "vector": objs[1].id, "point": [10, 8]})
    assert r["changed"] is True                                   # (crosses the line once: the part above it goes)
    r = call("trim", {"project": p.to_dict(), "sheet": s.id, "vector": objs[2].id, "point": [20, 9.9]})
    assert r["changed"]


def test_trim_says_when_there_is_nothing_to_trim():
    p, s, objs = project_with(Contour.polyline([(0, 5), (30, 5)], False))
    r = call("trim", {"project": p.to_dict(), "sheet": s.id, "vector": objs[0].id, "point": [15, 5]})
    assert r["project"] is None and not r["changed"] and "Nothing to trim" in r["message"]


# ------------------------------------------------------------------------------------------ text
def test_text_letters_are_as_tall_as_asked_and_holes_turn_the_other_way():
    cs = textgeom.text_contours("H", height=30)
    bb = contours_bbox(cs)
    assert bb.ymin == pytest.approx(0, abs=1e-6) and bb.ymax == pytest.approx(30, abs=0.01)
    o = textgeom.text_contours("O", height=20)
    assert sorted(round(c.signed_area()) > 0 for c in o) == [False, True]


def test_text_has_the_turkish_letters_lines_and_alignment():
    for ch in "ığşİĞŞöüçÖÜÇ":
        assert textgeom.text_contours(ch, height=10), ch
    two = textgeom.text_contours("AB\nC", height=10)
    assert contours_bbox(two).ymin < -10
    left = contours_bbox(textgeom.text_contours("WWWW\nI", height=10, align="left"))
    right = contours_bbox(textgeom.text_contours("WWWW\nI", height=10, align="right"))
    assert left.xmax == pytest.approx(right.xmax, abs=0.5)
    assert textgeom.text_contours("   ", height=10) == []
    ital = contours_bbox(textgeom.text_contours("I", height=10, italic=True))
    up = contours_bbox(textgeom.text_contours("I", height=10))
    assert ital.xmax - ital.xmin > up.xmax - up.xmin


def test_text_op_adds_a_vector_on_the_chosen_layer_at_the_point():
    p, s, objs = project_with()
    r = call("text", {"project": p.to_dict(), "sheet": s.id, "text": "iyiCAM", "font": "DejaVu Serif", "height": 25, "at": [100, 50]})
    v = r["project"]["sheets"][0]["vectors"][0]
    assert r["selection"] == [v["id"]] and v["name"] == "iyiCAM"
    from iyicam.core.document import vector_from_dict
    bb = vector_from_dict(v).bbox()
    assert bb.xmin >= 100 and bb.ymin == pytest.approx(50 - 7, abs=8) and bb.height > 20
    with pytest.raises(ServiceError):
        call("text", {"project": p.to_dict(), "sheet": s.id, "text": "  "})
    only = call("text", {"text": "x", "height": 10})
    assert only["vector"]["contours"]


def test_fonts_are_listed_and_an_own_font_file_can_be_given():
    f = call("fonts", {})
    assert "DejaVu Sans" in f["families"]
    data = (textgeom.FONT_DIR / "DejaVuSansMono.ttf").read_bytes()
    r = call("text", {"text": "W", "height": 10, "font_b64": base64.b64encode(data).decode()})
    assert r["vector"]["contours"]
    with pytest.raises(ServiceError):
        call("text", {"text": "W", "height": 10, "font_b64": base64.b64encode(b"not a font").decode()})


def test_text_on_curve_follows_the_vector():
    arc = Contour.polyline([(0, 0), (100, 0)], False)
    p, s, objs = project_with(arc)
    r = call("text_on_curve", {"project": p.to_dict(), "sheet": s.id, "selection": [objs[0].id], "text": "Hello", "height": 10, "align": "start", "offset": 5})
    new = r["project"]["sheets"][0]["vectors"][-1]
    from iyicam.core.document import vector_from_dict
    bb = vector_from_dict(new).bbox()
    assert r["selection"] == [new["id"]] and bb.xmin >= -1 and bb.ymin >= 4.0 and bb.ymax <= 16
    with pytest.raises(ServiceError):
        call("text_on_curve", {"project": p.to_dict(), "sheet": s.id, "selection": [], "text": "x", "height": 5})


# ------------------------------------------------------------------------------------------ trace
def test_trace_bitmap_makes_a_vector_of_the_dark_area():
    import numpy as np
    img = np.full((60, 80), 255, np.uint8)
    img[15:45, 20:60] = 0
    p, s, objs = project_with()
    r = call("trace_bitmap", {"project": p.to_dict(), "sheet": s.id, "gray_b64": base64.b64encode(img.tobytes()).decode(), "width": 80, "height": 60,
                              "name": "logo", "options": {"width": 80, "threshold": 128}})
    v = r["project"]["sheets"][0]["vectors"][0]
    from iyicam.core.document import vector_from_dict
    bb = vector_from_dict(v).bbox()
    assert v["name"] == "logo" and r["selection"] == [v["id"]]
    assert bb.width == pytest.approx(40, abs=1.5) and bb.height == pytest.approx(30, abs=1.5)
    assert bb.center[0] == pytest.approx(s.width / 2, abs=1) and bb.center[1] == pytest.approx(s.height / 2, abs=1)
    with pytest.raises(ServiceError):
        call("trace_bitmap", {"project": p.to_dict(), "sheet": s.id, "gray_b64": base64.b64encode(bytes([255]) * 4800).decode(), "width": 80, "height": 60})
    with pytest.raises(ServiceError):
        call("trace_bitmap", {"project": p.to_dict(), "sheet": s.id, "gray_b64": "AAAA", "width": 80, "height": 60})
    c = call("trace_bitmap", {"project": p.to_dict(), "sheet": s.id, "gray_b64": base64.b64encode(img.tobytes()).decode(), "width": 80, "height": 60,
                              "options": {"mode": "centerline", "width": 80}})
    assert c["contours"] >= 1


# ------------------------------------------------------------------------------------------ vector library
def test_library_groups_items_and_insert():
    p, s, objs = project_with(rect(5, 5, 25, 15), rect(30, 5, 40, 15))
    t = call("veclib", {"action": "tree"})["tree"]
    assert t["groups"] == [] and t["items"] == []
    g = call("veclib", {"action": "add_group", "name": "Logos"})
    sub = call("veclib", {"action": "add_group", "name": "Customers", "parent": g["id"]})
    assert sub["tree"]["groups"][0]["groups"][0]["name"] == "Customers"
    it = call("veclib", {"action": "add_item", "project": p.to_dict(), "sheet": s.id, "selection": [o.id for o in objs], "name": "Pair", "group": g["id"]})
    item = [i for i in it["tree"]["groups"][0]["items"]][0]
    assert item["name"] == "Pair" and item["size"] == [35.0, 10.0] and item["vectors"] == 2
    pv = call("veclib", {"action": "preview", "id": it["id"]})
    assert len(pv["paths"]) == 2 and pv["closed"] == [True, True]
    r = call("veclib", {"action": "insert", "project": p.to_dict(), "sheet": s.id, "id": it["id"], "at": [100, 100]})
    assert len(r["selection"]) == 2 and len(r["project"]["sheets"][0]["vectors"]) == 4
    from iyicam.core.document import vector_from_dict
    box = contours_bbox([c for v in r["project"]["sheets"][0]["vectors"][2:] for c in vector_from_dict(v).contours])
    assert (box.xmin, box.ymin) == pytest.approx((100, 100))
    mid = call("veclib", {"action": "insert", "project": p.to_dict(), "sheet": s.id, "id": it["id"]})
    assert len(mid["selection"]) == 2
    # the library is kept in the user folder: a second call sees it
    assert call("veclib", {"action": "tree"})["tree"]["groups"][0]["items"][0]["name"] == "Pair"
    r = call("veclib", {"action": "rename", "id": it["id"], "name": "Two"})
    assert r["tree"]["groups"][0]["items"][0]["name"] == "Two"
    r = call("veclib", {"action": "move", "id": it["id"], "to": sub["id"]})
    assert r["tree"]["groups"][0]["groups"][0]["items"][0]["name"] == "Two"
    r = call("veclib", {"action": "to_top", "id": it["id"]})
    assert r["tree"]["items"][0]["name"] == "Two"
    exp = call("veclib", {"action": "export", "id": g["id"]})["data"]
    imp = call("veclib", {"action": "import", "data": exp, "name": "Copy"})
    assert len(imp["tree"]["groups"]) == 2
    r = call("veclib", {"action": "remove", "id": g["id"]})
    assert [x["name"] for x in r["tree"]["groups"]] == ["Logos"] or len(r["tree"]["groups"]) == 1
    with pytest.raises(ServiceError):
        call("veclib", {"action": "add_item", "project": p.to_dict(), "sheet": s.id, "selection": [], "name": "x"})
    with pytest.raises(ServiceError):
        call("veclib", {"action": "insert", "project": p.to_dict(), "sheet": s.id, "id": g["id"]})


def test_library_shift_reorders():
    a = call("veclib", {"action": "add_group", "name": "A"})
    call("veclib", {"action": "add_group", "name": "B"})
    r = call("veclib", {"action": "shift", "id": a["id"], "step": 1})
    assert [g["name"] for g in r["tree"]["groups"]] == ["B", "A"]


# ------------------------------------------------------------------------------------------ sheet outline
def test_sheet_outline_from_vectors_and_clear():
    p, s, objs = project_with(Contour.polyline([(100, 100), (300, 100), (300, 200), (100, 200)], True), Contour.polyline([(150, 130), (250, 130), (250, 170), (150, 170)], True),
                              Contour.polyline([(400, 300), (410, 300), (410, 310)], True))
    keep = objs[2].id
    r = call("sheet_outline", {"project": p.to_dict(), "sheet": s.id, "selection": [objs[0].id, objs[1].id]})
    sh = r["project"]["sheets"][0]
    assert r["changed"] and sh["width"] == pytest.approx(200) and sh["height"] == pytest.approx(100) and sh["outline"]
    assert [v["id"] for v in sh["vectors"]] == [keep]            # (the outlines are used up, the rest moved with the plate)
    assert sh["vectors"][0]["contours"][0]["start"] == pytest.approx([300, 200])
    c = call("sheet_outline", {"project": r["project"], "sheet": s.id, "clear": True})
    assert c["project"]["sheets"][0]["outline"] is None
    again = call("sheet_outline", {"project": c["project"], "sheet": s.id, "clear": True})
    assert again["project"] is None
    with pytest.raises(ServiceError):
        call("sheet_outline", {"project": p.to_dict(), "sheet": s.id, "selection": []})


def test_start_points_can_be_limited_to_some_contours():
    p, s, objs = project_with(rect(0, 0, 10, 20), rect(30, 0, 40, 20))
    base = {"project": p.to_dict(), "sheet": s.id, "selection": [objs[0].id, objs[1].id], "action": "place", "place": "ne"}
    r = call("start_points", {**base, "contours": [[objs[1].id, 0]]})
    vs = r["project"]["sheets"][0]["vectors"]
    assert vs[0]["contours"][0]["start"] == [0, 0] and vs[1]["contours"][0]["start"] == pytest.approx([40, 20])
    # releasing one contour of a vector that keeps two
    r2 = call("start_points", {"project": r["project"], "sheet": s.id, "action": "release", "contours": [[objs[1].id, 0]]})
    assert r2["moved"] == 1 and not r2["project"]["sheets"][0]["vectors"][1].get("locked_starts")


def test_the_font_files_are_in_the_package_the_browser_gets():
    from iyicam.share.pack import Bundle
    import io, zipfile
    names = zipfile.ZipFile(io.BytesIO(Bundle().refresh().data)).namelist()
    assert any(n.endswith("core/fonts/DejaVuSans.ttf") for n in names)
    assert any(n.endswith("service/ops_draw.py") for n in names) and any(n.endswith("core/textgeom.py") for n in names)

"""The edges of isolation and of the compound files that mutation testing showed untested: two isolated groups at once,
layers reused by name, copied toolpaths, the group of new sheets, turning a leftover, and small streams."""
import pytest

from conftest import add_tp, end_mill, make_project
from iyicam.cam.job import sheets_for
from iyicam.core import scope, shapes
from iyicam.core.document import Layer, Sheet, VectorObject
from iyicam.core.geom import polygon_area
from iyicam.core.sheetturn import HOW, sheet_transform, turn_sheet


def three():
    p, a, _ = make_project([shapes.rectangle(0, 0, 10, 10)])
    a.name = "A"
    b = p.add_sheet("B")
    c = p.add_sheet("C")
    b.vectors.append(VectorObject([shapes.rectangle(0, 0, 10, 10)], p.layers[0].id))
    c.vectors.append(VectorObject([shapes.rectangle(0, 0, 10, 10)], p.layers[0].id))
    return p, a, b, c


def test_two_isolated_groups_keep_apart():
    p, a, b, c = three()
    scope.isolate(p, [b.id])
    scope.isolate(p, [c.id])
    assert scope.isolated_groups(p) == ["B", "C"]
    assert [s.name for s in scope.sheets_with(p, b)] == ["B"] and [s.name for s in scope.sheets_with(p, c)] == ["C"]
    assert [s.name for s in scope.sheets_of_scope(p, "B")] == ["B"]
    lb, lc = p.layer(b.vectors[0].layer_id), p.layer(c.vectors[0].layer_id)
    assert lb is not lc and lb.sheet_group == "B" and lc.sheet_group == "C"
    assert scope.layer_in_scope(p, lb, "B") and not scope.layer_in_scope(p, lb, "C") and not scope.layer_in_scope(p, lb, "")
    assert scope.layer_in_scope(p, p.layers[0], "") and not scope.layer_in_scope(p, p.layers[0], "B")
    t = add_tp(p, "profile", end_mill(6), {}, [])
    t.sheet_group = "C"
    assert scope.toolpath_in_scope(p, t, "C") and not scope.toolpath_in_scope(p, t, "B") and not scope.toolpath_in_scope(p, t, "")
    scope.unisolate(p, "B")
    assert not b.isolated and c.isolated                      # (the other one stays isolated)
    assert scope.isolated_groups(p) == ["C"]


def test_a_group_that_is_not_isolated_is_the_rests():
    p, a, b, c = three()
    scope.set_group(p, [a.id, b.id], "Doors")
    t = add_tp(p, "profile", end_mill(6), {}, [])
    t.sheet_group = "Doors"
    assert scope.toolpath_in_scope(p, t, "") and scope.isolated_groups(p) == []
    assert not a.isolated and scope.groups_of_scope(p, "") == ["Doors"]


def test_a_vector_moved_to_a_side_takes_the_layer_of_the_same_name_there():
    p, a, b, c = three()
    scope.isolate(p, [b.id])
    own = p.layer(b.vectors[0].layer_id)
    n = len(p.layers)
    scope.set_group(p, [c.id], "B")                           # (its vector's layer is the rest's "Default": the group has one)
    assert c.vectors[0].layer_id == own.id and len(p.layers) == n
    cut = p.add_layer("Cut", "#ff0000")
    cut.visible, cut.locked = False, True
    cut.group = "Folder"
    a.vectors.append(VectorObject([shapes.circle((5, 5), 2)], cut.id))
    scope.set_group(p, [a.id], "B")                           # (no "Cut" in the group: a copy, right after it, as it was)
    copy_ = p.layer(a.vectors[-1].layer_id)
    assert copy_.id != cut.id and copy_.name == "Cut" and copy_.color == "#ff0000" and copy_.sheet_group == "B"
    assert (copy_.visible, copy_.locked, copy_.group) == (False, True, "Folder")
    assert p.layers.index(copy_) == p.layers.index(cut) + 1


def test_a_vector_on_a_layer_that_is_gone_is_left_alone():
    p, a, b, c = three()
    b.vectors.append(VectorObject([shapes.circle((5, 5), 2)], "no-such-layer"))
    b.vectors.append(VectorObject([shapes.circle((5, 5), 3)], p.layers[0].id))
    scope.isolate(p, [b.id])
    assert b.vectors[1].layer_id == "no-such-layer"
    assert p.layer(b.vectors[2].layer_id).sheet_group == "B"          # (the ones after it are still put right)


def test_isolating_copies_a_toolpath_with_its_own_id_right_after_it_and_keeps_layers_it_had():
    p, a, b, c = three()
    other = p.add_layer("Other")
    first = add_tp(p, "profile", end_mill(6), {}, [])
    first.selection.mode, first.selection.layer_ids = "layers", [p.layers[0].id, other.id]
    second = add_tp(p, "profile", end_mill(6), {}, [])
    second.selection.mode, second.selection.layer_ids = "layers", [p.layers[0].id]
    scope.isolate(p, [b.id])
    ids = [t.id for t in p.toolpaths]
    assert len(set(ids)) == len(ids) and None not in ids
    names = [(t.id == first.id, t.id == second.id, t.sheet_group) for t in p.toolpaths]
    assert names == [(True, False, ""), (False, False, "B"), (False, True, ""), (False, False, "B")]
    cp = p.toolpaths[1]
    assert cp.selection.layer_ids == [b.vectors[0].layer_id, other.id]   # (the moved one, and the one it had as it was)


def test_isolating_nothing_or_sheets_of_two_groups():
    p, a, b, c = three()
    assert scope.isolate(p, []) == "" and not any(s.isolated for s in p.sheets)
    scope.set_group(p, [a.id], "X")
    scope.set_group(p, [b.id], "Y")
    name = scope.isolate(p, [a.id, b.id])                     # (two groups: a new group of them)
    assert name == "A" and a.group == b.group == "A" and a.isolated and b.isolated


def test_a_new_group_name_does_not_take_one_that_is_there():
    p, a, b, c = three()
    assert scope.unique_group_name(p, "  ") == "Group"
    scope.set_group(p, [a.id], "Group")
    scope.set_group(p, [b.id], "Group 2")
    assert scope.unique_group_name(p, "Group") == "Group 3"
    assert scope.unique_group_name(p, "") == "Group 3"
    assert scope.unique_group_name(p, "New") == "New"


def test_the_group_of_new_sheets_in_every_case():
    p, a, b, c = three()
    assert scope.group_new_sheets(p, [], "X", a) == ""
    assert scope.group_new_sheets(p, [b], None, None) == "" and b.group == ""
    scope.set_group(p, [a.id], "Doors")
    assert scope.group_new_sheets(p, [b], None, a) == "Doors" and b.group == "Doors"
    scope.isolate(p, [c.id])
    assert scope.group_new_sheets(p, [b], "C", a) == "" and b.group == "" and not b.isolated   # (into an isolated: no)
    assert scope.group_new_sheets(p, [b], "  New ", a) == "New"
    extra = p.add_sheet("E")
    assert scope.group_new_sheets(p, [extra], "", c) == "C" and extra.isolated              # (from an isolated: in it)


@pytest.mark.parametrize("how", HOW)
def test_a_leftover_outline_keeps_its_direction_when_turned(how):
    p, s, _ = make_project([shapes.rectangle(10, 10, 20, 20)], size=(600, 400))
    s.outline = [[(0, 0), (600, 0), (600, 200), (300, 400), (0, 400)], [(100, 100), (100, 150), (150, 150), (150, 100)]]
    signs = [polygon_area(poly) > 0 for poly in s.outline]
    turn_sheet(p, s, how)
    assert [polygon_area(poly) > 0 for poly in s.outline] == signs        # (the outer one and its hole the same way round)


def test_an_unknown_way_to_turn_says_the_ways():
    with pytest.raises(ValueError, match="left, right, half, mirror_lr, mirror_tb"):
        sheet_transform(10, 10, "sideways")
    T, w, h = sheet_transform(600, 400, "left")
    assert T.apply((0, 0)) == (400, 0) and (w, h) == (400, 600)
    T, w, h = sheet_transform(600, 400, "mirror_lr")
    assert T.apply((0, 7)) == (600, 7)


def test_the_tabs_of_a_toolpath_of_layers_kept_to_the_sheet_turn_with_it():
    p, s, objs = make_project([shapes.rectangle(10, 10, 20, 20)], size=(600, 400))
    t = add_tp(p, "profile", end_mill(6), {"tab_points": [[10, 20]]}, [])
    t.selection.mode, t.selection.layer_ids = "layers", [p.layers[0].id]
    other = p.add_sheet("B")
    t.sheet_ids = [s.id]
    turn_sheet(p, s, "mirror_lr")
    assert t.params["tab_points"] == [[590, 20]]
    t.sheet_ids = [s.id, other.id]                            # (on two sheets: whose are the points? left as they are)
    turn_sheet(p, s, "mirror_lr")
    assert t.params["tab_points"] == [[590, 20]]


# -- compound files: small streams, as real files keep them ------------------------------------------------------------

def test_small_streams_are_read_from_the_mini_stream():
    from artcam_builder import compound_file
    from iyicam.formats.cfb import CompoundFile
    streams = {"a/b": b"x" * 100, "a/c": bytes(range(256)) * 30, "d": b"short", "e/f/g": b"y" * 4095, "e/h": b"z" * 4096,
               "empty": b""}
    cf = CompoundFile(compound_file(streams, cutoff=4096))
    assert cf.cutoff == 4096 and cf.minifat
    for path, data in streams.items():
        assert cf.read(path) == data
    assert cf.exists("a/b") and cf.exists("a/") and not cf.exists("zz")
    with pytest.raises(KeyError):
        cf.read("a/")                                         # (a storage is not a stream)


def test_a_file_shorter_than_a_header_is_not_a_compound_file():
    from iyicam.formats.cfb import MAGIC, CfbError, CompoundFile
    with pytest.raises(CfbError):
        CompoundFile(MAGIC + b"\0" * 100)


def test_a_broken_mini_chain_says_so():
    import struct
    from artcam_builder import compound_file
    from iyicam.formats.cfb import CfbError, CompoundFile
    data = bytearray(compound_file({"a": b"q" * 200, "b": b"r" * 5000}, cutoff=4096))
    cf = CompoundFile(bytes(data))
    first_minifat = struct.unpack_from("<I", data, 0x3C)[0]
    off = (first_minifat + 1) * 512
    struct.pack_into("<I", data, off, 9999)                   # (the chain of "a" points far out)
    with pytest.raises(CfbError):
        CompoundFile(bytes(data)).read("a")
    assert cf.read("b") == b"r" * 5000

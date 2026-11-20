"""File names made of the toolpaths of the file ({segments}): a part for every toolpath, a group's alike toolpaths stacked
into one part with how many there are, and a group cut one after the other. The example of the user: five toolpaths with a
4 mm tool, three of them a group 'oyma' (inside, 2 mm in 1 mm passes), two cuts through 4 mm material."""
import pytest
from conftest import add_tp, end_mill, make_project

from iyicam.builtins import ensure_registered
from iyicam.cam.job import compute_all, sheet_toolpaths
from iyicam.core import shapes
from iyicam.core.registry import REGISTRY
from iyicam.post import naming

WANT = "001d4-i_oyma2s1z10m3-o_kesim4z10-a_kesim4z10"


def job():
    ensure_registered(REGISTRY)
    p, s, objs = make_project([[shapes.rectangle(10 + i * 60, 10, 40, 40)] for i in range(5)], size=(500, 300))
    p.material.thickness, p.material.safe_z = 4.0, 10.0

    def tp(name, side, o, depth=None, group=""):
        prm = {"side": side, "through": depth is None, "overcut": 0.1}
        if depth is not None:
            prm["cut_depth"] = depth
        else:
            prm["stepdown"] = 5.0                       # (a cut through 4 mm in one pass)
        t = add_tp(p, "profile", end_mill(4, stepdown=1.0), prm, [o])
        t.name, t.group = name, group
        return t
    tp("oyma01", "inside", objs[0], 2.0, "oyma")
    tp("oyma02", "inside", objs[1], 2.0, "oyma")
    tp("kesim", "outside", objs[2])
    tp("oyma03", "inside", objs[3], 2.0, "oyma")
    tp("kesim", "on", objs[4])
    res = compute_all(p, REGISTRY)
    return p, s, sheet_toolpaths(p, res, s)


def test_the_example_is_named_and_cut_as_asked():
    p, s, tps = job()
    order = naming.group_order(p, tps)
    assert [t.name for t in order] == ["oyma01", "oyma02", "oyma03", "kesim", "kesim"]
    v = naming.values_for(p, s, 1, 1, "x", "", order, revision=1)
    assert naming.render(["{revision:03}d{tool_diameter}-{segments}"], v) == WANT


def test_the_part_can_be_changed_and_stacking_switched_off():
    p, s, tps = job()
    order = naming.group_order(p, tps)
    v = naming.values_for(p, s, 1, 1, "x", "", order, segment="{side}{name}D{final_depth}[P{passes}]", segment_sep="+",
                          stack=False)
    assert v["segments"] == "ioymaD2P2+ioymaD2P2+ioymaD2P2+okesimD4.1P1+akesimD4.1P1"   # (names without their digits)
    assert naming.render_segment("[m{count}]{name}", {"count": "", "name": "x"}) == "x"
    assert naming.unknown(["{segments}"]) == []


def test_the_engine_cuts_a_group_together_and_names_the_file_so():
    from iyicam.core.document import project_to_dict
    from iyicam.service.ops import op_gcode
    p, s, _ = job()
    p.metadata["revisions"] = {}
    r = op_gcode({"project": project_to_dict(p), "post": "mach3", "name_parts": ["{revision:03}d{tool_diameter}-{segments}"],
                  "revision": 1})
    f = r["files"][0]
    assert f["name"] == WANT + ".tap"
    text = f["text"]
    at = [text.index(f"( {n}") for n in ("oyma01", "oyma02", "oyma03")]
    assert at == sorted(at) and at[2] < text.index("( kesim")            # (the group is cut before the next one)
    r = op_gcode({"project": project_to_dict(p), "post": "mach3", "name_parts": ["{segments}"], "together": False,
                  "stack": False})
    assert r["files"][0]["name"].startswith("i_oyma2s1z10-i_oyma2s1z10-o_kesim4z10-i_oyma2s1z10")


def _named(specs):
    """``{segments}`` of toolpaths made from ``(name, side, depth, allowance, group)``, in this order, with grouping by order."""
    ensure_registered(REGISTRY)
    p, s, objs = make_project([[shapes.rectangle(10 + i * 60, 10, 40, 40)] for i in range(len(specs))], size=(600, 300))
    p.material.thickness, p.material.safe_z = 4.0, 10.0
    for (name, side, depth, allowance, group), o in zip(specs, objs):
        t = add_tp(p, "profile", end_mill(4, stepdown=1.0),
                   {"side": side, "through": False, "cut_depth": depth, "allowance": allowance}, [o])
        t.name, t.group = name, group
    tps = sheet_toolpaths(p, compute_all(p, REGISTRY), s)
    return naming.values_for(p, s, 1, 1, "x", "", tps)["segments"]


def test_names_lose_their_digits_and_alike_toolpaths_in_a_row_are_stacked_without_a_group():
    assert naming.name_without_digits("oyma01") == "oyma" and naming.name_without_digits("harf 3 dis") == "harf dis"
    assert naming.name_without_digits("12") == "12"
    assert _named([("oyma01", "inside", 2.0, 0.0, ""), ("oyma02", "inside", 2.0, 0.0, ""),
                   ("oyma03", "inside", 2.0, 0.0, "")]) == "i_oyma2s1z10m3"


def test_a_different_allowance_or_depth_is_a_part_of_its_own_and_shows():
    # the case of the user: the same side, one with another allowance, one with another depth: nothing is stacked into
    # what it is not, and the part says what differs
    assert _named([("oyma1", "inside", 2.0, 0.0, "g"), ("oyma2", "inside", 2.0, 0.2, "g"), ("oyma3", "inside", 3.0, 0.0, "g"),
                   ("oyma4", "inside", 3.0, 0.0, "g")]) == "i_oyma2s1z10-i_oyma2a0.2s1z10-i_oyma3s1z10m2"
    assert _named([("kesim", "outside", 2.0, -0.1, "")]) == "o_kesim2a-0.1s1z10"


def test_only_toolpaths_in_a_row_are_stacked_the_order_decides():
    assert _named([("oyma", "inside", 2.0, 0.0, ""), ("kesim", "outside", 2.0, 0.0, ""),
                   ("oyma", "inside", 2.0, 0.0, "")]) == "i_oyma2s1z10-o_kesim2s1z10-i_oyma2s1z10"


pytest.importorskip("PySide6.QtWidgets")


@pytest.fixture
def rwin(tmp_path, monkeypatch):
    from gui_robot import rwin_gen
    yield from rwin_gen(tmp_path, monkeypatch)


def test_the_save_window_names_and_orders_the_same_way(rwin):
    from gui_robot import process
    from iyicam.gui.dialogs import ExportDialog
    win, _ = rwin
    c = win.ctrl
    p, s, _ = job()
    c._replace(p)
    c.recalculate()
    process()
    d = ExportDialog(c, win)
    d._name_rows.itemAt(0).layout().itemAt(0).widget().setText("{revision:03}d{tool_diameter}-{segments}")
    d.revision.setValue(1)
    process()
    assert WANT + ".tap" in d.name_preview.text()
    assert [t.name for t in d.toolpaths(c.sheet)][:3] == ["oyma01", "oyma02", "oyma03"]
    d.stack.setChecked(False)
    process()
    assert "i_oyma2s1z10-i_oyma2s1z10-i_oyma2s1z10" in d.name_preview.text()
    d.together.setChecked(False)
    assert [t.name for t in d.toolpaths(c.sheet)] == ["oyma01", "oyma02", "kesim", "oyma03", "kesim"]


def test_the_save_window_reorders_the_toolpaths_and_the_project_and_name_follow(rwin):
    from PySide6.QtCore import QModelIndex
    from gui_robot import process
    from iyicam.gui.dialogs import ExportDialog
    win, _ = rwin
    c = win.ctrl
    p, s, _ = job()
    c._replace(p)
    c.recalculate()
    process()
    d = ExportDialog(c, win)
    d._name_rows.itemAt(0).layout().itemAt(0).widget().setText("{segments}")
    d.together.setChecked(False)
    process()
    names = lambda: [t.name for t in c.project.toolpaths]          # noqa: E731
    assert names() == ["oyma01", "oyma02", "kesim", "oyma03", "kesim"]
    # ▼ on the first kesim: the third oyma comes before it, so the three oyma are in a row and stack
    d.list.setCurrentRow(2)
    d.move_selected(1)
    process()
    assert names() == ["oyma01", "oyma02", "oyma03", "kesim", "kesim"]
    assert [t.name for t in d.toolpaths(c.sheet)][:3] == ["oyma01", "oyma02", "oyma03"]
    assert "i_oyma2s1z10m3-o_kesim4z10-a_kesim4z10" in d.name_preview.text()
    gcode = d.text.toPlainText()
    assert gcode.index("( oyma03") < gcode.index("( kesim")
    # dragging (a row moved in the list) does the same: the last kesim first
    d.list.model().moveRow(QModelIndex(), 4, QModelIndex(), 0)
    process()
    assert names()[0] == "kesim" and d.order()[0] == c.project.toolpaths[0].id
    assert d.name_preview.text().count("a_kesim4z10") == 1 and "a_kesim4z10-i_oyma2s1z10m3" in d.name_preview.text()
    # one undo step per change
    c.undo()
    assert names() == ["oyma01", "oyma02", "oyma03", "kesim", "kesim"]

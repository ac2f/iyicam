"""What was asked together: groups of tools that can be made and changed, tool names with {variables}, a new toolpath that asks
for its tool (and a change of its feed rate that stays its own), toolpaths that do not turn red when another one is added, redo
that works, Ctrl+R that repeats, every shortcut editable, packages of toolpaths, and area clearing with a big cutter."""
import pytest

from conftest import add_tp, end_mill, make_project
from iyicam.builtins import ensure_registered
from iyicam.cam import tp_packages as P
from iyicam.cam.job import compute_all, sheet_toolpaths
from iyicam.cam.moves import Rapid
from iyicam.core import shapes
from iyicam.core.document import Selection, Sheet, ToolpathDef, VectorObject
from iyicam.core.registry import REGISTRY
from iyicam.core.tooling import Tool, ToolLibrary
from iyicam.post.naming import toolpath_name

ensure_registered(REGISTRY)


# ------------------------------------------------------------------------------------------------ tools
def test_a_tool_name_has_variables():
    t = Tool("T{number:02} Ø{diameter} {type} F{feed_rate/60:.1}", "end_mill", 6.0, 3, feed_rate=4800)
    assert t.label == "T03 Ø6 End Mill F80.0"
    assert t.describe().startswith("T03 Ø6 End Mill")
    assert Tool("{nonsense} 6", diameter=6).label == "{nonsense} 6"            # (an unknown variable stays as it is)


def test_the_tool_name_is_written_with_its_variables_put_in(tmp_path):
    p, s, objs = make_project([shapes.rectangle(0, 0, 50, 50)])
    t = add_tp(p, "profile", Tool("Ø{diameter} freze", diameter=4), {}, objs)
    t.name = "Dış {tool_name}"
    assert toolpath_name(p, t, s) == "Dış Ø4 freze"


def test_groups_of_tools_are_made_renamed_and_removed_and_kept_empty(tmp_path):
    lib = ToolLibrary(None, tmp_path / "tools.json")
    lib.add_group("Tarama")
    assert lib.groups()[-1] == "Tarama"
    lib.add(Tool("Surfacing 50", diameter=50, group="Tarama"))
    lib.rename_group("Tarama", "Yüzey")
    assert lib.tools[-1].group == "Yüzey" and "Tarama" not in lib.groups()
    lib.add_group("Boş")
    lib.save()
    back = ToolLibrary.load(tmp_path / "tools.json")
    assert "Boş" in back.groups() and back.groups().index("Yüzey") < back.groups().index("Boş")
    back.remove_group("Yüzey")
    assert "Yüzey" not in back.groups() and back.tools[-1].group == ""


# ------------------------------------------------------------------------------------------------ packages
def package():
    return P.check({"name": "Kontur", "tool": end_mill(4).to_dict(), "items": [
        {"name": "Dış {allowance}", "op": "profile", "params": {"side": "outside", "allowance": 10.0, "cut_depth": 2.0},
         "ask": ["cut_depth", "allowance"]},
        {"name": "İç {allowance}", "op": "profile", "params": {"side": "inside", "allowance": 3.0, "cut_depth": 1.0},
         "ask": ["cut_depth", "allowance"]},
        {"name": "Kenar {n}", "op": "profile", "params": {"side": "outside", "allowance": 12.0, "cut_depth": 3.0}, "ask": []}]})


def two_sheets():
    p, s, objs = make_project([shapes.rectangle(100, 100, 200, 100)], size=(600, 400))
    p.sheets.append(Sheet("Sheet 2", 600, 400))
    return p, objs


def test_a_package_is_kept_and_read_back():
    P.save(package())
    got = P.load()
    assert [x["name"] for x in got] == ["Kontur"] and len(got[0]["items"]) == 3 and got[0]["tool"]["diameter"] == 4
    assert P.delete(got[0]["id"]) and P.load() == []


def test_a_package_makes_its_toolpaths_in_a_numbered_group_of_the_sheet():
    p, objs = two_sheets()
    sh = p.sheets[1]
    sel = Selection("vectors", [objs[0].id], [])
    tps, group = P.build(package(), p, sh, sel, REGISTRY, values={0: {"cut_depth": 1.0}}, names={1: "İçeri {allowance}"})
    assert group == "Kontur S2-01" and all(t.group == group for t in tps)
    assert [toolpath_name(p, t, sh) for t in tps] == ["Dış 10", "İçeri 3", "Kenar 1"]
    assert tps[0].params["cut_depth"] == 1.0 and tps[0].params["allowance"] == 10.0     # (asked: changed for this time)
    assert all(t.tool.diameter == 4 and t.sheet_ids == [sh.id] for t in tps)
    p.toolpaths += tps
    again, group2 = P.build(package(), p, sh, sel, REGISTRY, tool=end_mill(6), skip={2})
    assert group2 == "Kontur S2-02" and len(again) == 2 and again[0].tool.diameter == 6   # (another tool this time)
    assert package()["tool"]["diameter"] == 4                                               # (the package keeps its own)


def test_a_package_can_leave_the_group_out_and_be_on_every_sheet():
    p, objs = two_sheets()
    tps, group = P.build(package(), p, p.sheets[0], Selection("vectors", [objs[0].id], []), REGISTRY,
                         make_group=False, this_sheet=False)
    assert group == "" and all(t.group == "" and t.sheet_ids is None for t in tps)


def test_a_package_is_made_of_toolpaths_with_their_names_and_tools():
    p, s, objs = make_project([shapes.rectangle(0, 0, 50, 50)])
    a = add_tp(p, "profile", end_mill(4), {"side": "outside", "allowance": 10.0, "cut_depth": 2.0}, objs)
    b = add_tp(p, "profile", end_mill(8), {"side": "inside", "allowance": 3.0}, objs)
    a.name, b.name = "Dış", "İç"
    pkg = P.from_toolpaths("X", [a, b], REGISTRY)
    assert [i["name"] for i in pkg["items"]] == ["Dış", "İç"]
    assert pkg["tool"]["diameter"] == 4 and pkg["items"][0]["tool"] is None and pkg["items"][1]["tool"]["diameter"] == 8
    assert pkg["items"][0]["ask"] == ["cut_depth", "allowance"]


def test_the_toolpaths_of_a_package_cut_where_they_say():
    p, objs = two_sheets()
    sh = p.sheets[0]
    tps, _ = P.build(package(), p, sh, Selection("vectors", [objs[0].id], []), REGISTRY)
    p.toolpaths += tps
    res = compute_all(p, REGISTRY)
    xs = [m.x for m in sheet_toolpaths(p, res, sh)[0].moves if hasattr(m, "x")]
    assert min(xs) == pytest.approx(100 - 2 - 10, abs=0.01)                 # (4 mm tool, 10 mm allowance, outside)


# ------------------------------------------------------------------------------------------------ area clearing, big cutter
def clear(shape, strategy, **extra):
    p, s, objs = make_project([shape] if not isinstance(shape, list) else [shape], size=(1100, 700))
    add_tp(p, "pocket", end_mill(50, stepover=40.0, stepdown=1.0), {"strategy": strategy, "cut_depth": 1.0, **extra}, objs)
    tp = sheet_toolpaths(p, compute_all(p, REGISTRY), s)[0]
    return tp, sum(1 for m in tp.moves if isinstance(m, Rapid))


@pytest.mark.parametrize("angle", [0.0, 30.0])
def test_a_zigzag_with_a_big_cutter_is_one_cut_without_lifting_between_the_lines(angle):
    # (the links between the lines run on the edge of the area: they were taken as leaving it, a lift on every line; and on a
    # slanted raster nearest first swept to a corner and came back across the pocket)
    _, rapids = clear(shapes.rectangle(0, 0, 1000, 600), "raster", raster_angle=angle)
    assert rapids == 3                                                       # (to the start, down, up at the end)


@pytest.mark.parametrize("angle", [0.0, 30.0])
def test_a_zigzag_round_an_island_does_one_side_then_the_other(angle):
    # (before: 17 lifts at 0°, 23 at 30° with 2.3 m of rapids; nearest first and rows crossing the island)
    tp, _ = clear([shapes.rectangle(0, 0, 1000, 600), shapes.circle((500, 300), 120)], "raster", raster_angle=angle)
    prev, lifts, travel = None, 0, 0.0
    for m in tp.moves:
        if not hasattr(m, "x"):
            continue
        if isinstance(m, Rapid) and prev and (abs(m.x - prev[0]) > 1e-6 or abs(m.y - prev[1]) > 1e-6):
            lifts += 1
            travel += ((m.x - prev[0]) ** 2 + (m.y - prev[1]) ** 2) ** 0.5
        prev = (m.x, m.y)
    assert lifts <= 10 and travel < 1500


# ------------------------------------------------------------------------------------------------ the desktop
@pytest.fixture
def win():
    pytest.importorskip("PySide6.QtWidgets")
    from PySide6.QtWidgets import QApplication
    from iyicam.gui.app import create_window
    app, w = create_window(["test"], load_plugins=False)
    w.show()
    QApplication.processEvents()
    w.ctrl.settings.auto_recalculate = False
    yield w
    w.ctrl.dirty = False
    w.close()
    QApplication.processEvents()


def rect(ctrl, x=0.0):
    o = VectorObject([shapes.rectangle(x, 0, 50, 50)], ctrl.project.layers[0].id)
    ctrl.add_vectors([o], "Rect")
    return o


def test_redo_works_after_the_edit_menu_was_opened_with_nothing_to_redo(win):
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QApplication
    c = win.ctrl
    win._update_undo()                                     # (what opening the Edit menu does: greyed the items out for good)
    rect(c)
    win.canvas.setFocus()
    QTest.keyClick(win.canvas, Qt.Key_Z, Qt.ControlModifier)
    QApplication.processEvents()
    assert len(c.sheet.vectors) == 0
    QTest.keyClick(win.canvas, Qt.Key_Y, Qt.ControlModifier)
    QApplication.processEvents()
    assert len(c.sheet.vectors) == 1
    c.undo()
    QTest.keyClick(win.canvas, Qt.Key_Z, Qt.ControlModifier | Qt.ShiftModifier)   # (CorelDRAW's redo)
    QApplication.processEvents()
    assert len(c.sheet.vectors) == 1


def test_ctrl_r_repeats_the_last_command_with_its_values(win, monkeypatch):
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QApplication
    from iyicam.gui import dialogs
    c = win.ctrl
    o = rect(c)
    c.select([o.id])
    cmd = REGISTRY.commands["xform.move"]
    monkeypatch.setattr(dialogs.CommandDialog, "exec", lambda self: 1)
    monkeypatch.setattr(dialogs.CommandDialog, "values", lambda self: {**{p.name: p.default for p in cmd.params}, "dx": 10.0})
    win.run_command(cmd)
    assert c.sheet.vectors[0].bbox().xmin == pytest.approx(10)
    win.canvas.setFocus()
    QTest.keyClick(win.canvas, Qt.Key_R, Qt.ControlModifier)
    QApplication.processEvents()
    assert c.sheet.vectors[0].bbox().xmin == pytest.approx(20) and "Move" in win.repeat_action.text()
    win.shortcut_actions["Edit/Duplicate"].trigger()
    n = len(c.sheet.vectors)
    QTest.keyClick(win.canvas, Qt.Key_R, Qt.ControlModifier)
    QApplication.processEvents()
    assert len(c.sheet.vectors) == n + 1


def test_every_function_has_an_editable_shortcut_and_a_taken_key_is_moved(win, monkeypatch):
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QApplication, QMessageBox
    from iyicam.gui.shortcuts import ShortcutsDialog
    assert {"Edit/Duplicate", "Edit/Repeat", "Vectors/Transform/Rotate", "Toolpaths/Recalculate All"} <= set(win.shortcut_actions)
    assert win.shortcut_defaults["Edit/Repeat"] == ["Ctrl+R"]
    assert win.shortcut_defaults["Vectors/Transform/Rotate"] == ["Ctrl+Shift+R"]
    monkeypatch.setattr(QMessageBox, "question", staticmethod(lambda *a, **k: QMessageBox.Yes))
    d = ShortcutsDialog(win.shortcut_actions, win.shortcut_defaults, {}, win)
    d.tree.setCurrentItem(d.items["Edit/Copy"])
    d._set_current(["Ctrl+D"])                                       # (Duplicate's: taken from it)
    assert d.overrides() == {"Edit/Copy": ["Ctrl+D"], "Edit/Duplicate": []}
    win.ctrl.settings.shortcuts = {"Edit/Duplicate": ["Ctrl+K"]}
    win.build_menus()
    rect(win.ctrl)
    win.ctrl.select([o.id for o in win.ctrl.sheet.vectors])
    win.canvas.setFocus()
    QTest.keyClick(win.canvas, Qt.Key_K, Qt.ControlModifier)
    QApplication.processEvents()
    assert len(win.ctrl.sheet.vectors) == 2


def test_a_new_toolpath_asks_for_its_tool_and_its_changes_stay_its_own(win, monkeypatch):
    from iyicam.gui import dialogs
    c = win.ctrl
    lib = c.tools.tools[1]
    asked = []

    def pick(self):
        asked.append(self.windowTitle())
        return 1
    monkeypatch.setattr(dialogs.ToolDatabaseDialog, "exec", pick)
    monkeypatch.setattr(dialogs.ToolDatabaseDialog, "selected_tool", lambda self: lib.copy())
    tool = win.ask_tool(c, "profile")
    assert asked and tool.id == lib.id and tool.feed_rate == lib.feed_rate
    dlg = dialogs.ToolpathDialog(c, "profile", None, win, tool=tool)
    dlg.tool_editor.form.set_values({"feed_rate": 9999.0})
    assert dlg.tool_editor.tool().feed_rate == 9999.0
    dlg._remember()
    assert c.tools.get(lib.id).feed_rate == lib.feed_rate                       # (the tool database: as it was)
    nxt = dialogs.ToolpathDialog(c, "profile", None, win)
    assert nxt.tool_editor.tool().feed_rate == lib.feed_rate                     # (the next toolpath: not 9999)


def test_a_toolpath_does_not_turn_red_when_another_moves_the_start_points_of_its_vectors(win):
    c = win.ctrl
    objs = [rect(c, 120.0 * i) for i in range(3)]

    def tp(name, side, smart):
        t = ToolpathDef(name, "profile", end_mill(6), {"side": side, "cut_depth": 5},
                        Selection("vectors", [o.id for o in objs], []), True, None)
        t.sequence["smart_starts"] = smart
        return t
    a = tp("A", "outside", False)
    c.add_toolpath(a)
    before = [o.contours[0].start for o in objs]
    c.add_toolpath(tp("B", "inside", True))
    assert [o.contours[0].start for o in objs] != before                     # (B moved the start points of the vectors ...)
    assert c.stale_ids == set()                                              # (... and A follows: it is not left red)


def test_the_tool_database_makes_renames_and_removes_groups(win, monkeypatch):
    from PySide6.QtWidgets import QInputDialog, QMessageBox
    from iyicam.gui import dialogs
    c = win.ctrl
    d = dialogs.ToolDatabaseDialog(c, win)
    monkeypatch.setattr(QInputDialog, "getText", staticmethod(lambda *a, **k: ("Tarama", True)))
    d.new_group()
    d.add()
    assert d.current.group == "Tarama" and d.current.name == "New tool"      # (the new tool in the new group, not a copy of another)
    monkeypatch.setattr(QInputDialog, "getText", staticmethod(lambda *a, **k: ("Yüzey", True)))
    d.rename_group()
    assert d.editor.tool().group == "Yüzey"
    d.accept()
    assert "Yüzey" in c.tools.groups() and "Tarama" not in c.tools.groups()
    d = dialogs.ToolDatabaseDialog(c, win)
    monkeypatch.setattr(QMessageBox, "question", staticmethod(lambda *a, **k: QMessageBox.Yes))
    d.current_group = "Yüzey"
    d.delete_group()
    d.accept()
    assert "Yüzey" not in c.tools.groups()


def test_a_package_from_the_menu_makes_its_toolpaths_in_one_step(win, monkeypatch):
    from iyicam.gui import packages as G
    c = win.ctrl
    o = rect(c, 100.0)
    P.save(package())
    pid = P.load()[0]["id"]
    c.select([o.id])

    def run(self):
        self.tool = end_mill(6).to_dict()
        self.boxes[0][3].set_values({"cut_depth": 1.0})
        return 1
    monkeypatch.setattr(G.PackageApplyDialog, "exec", run)
    n = len(c.project.toolpaths)
    win.apply_package(pid)
    made = c.project.toolpaths[n:]
    assert [t.group for t in made] == ["Kontur S1-01"] * 3 and made[0].params["cut_depth"] == 1.0
    assert all(t.tool.diameter == 6 for t in made) and P.load()[0]["tool"]["diameter"] == 4
    c.undo()
    assert len(c.project.toolpaths) == n

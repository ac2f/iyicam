"""Model ▸ Rotate / Mirror (the whole sheet with everything on it) and View ▸ Draw Toolpaths Solid."""
import pytest

from conftest import add_tp, end_mill, make_project
from iyicam.core import shapes
from iyicam.core.geom import BBox
from iyicam.core.sheetturn import turn_sheet

W, H = 600.0, 400.0


def project():
    p, s, objs = make_project([shapes.rectangle(10, 20, 100, 50), shapes.circle((500, 300), 40)], size=(W, H))
    t = add_tp(p, "profile", end_mill(6), {"through": True, "tabs": True, "tab_mode": "manual",
                                           "tab_points": [[60, 20], [540, 300]]}, objs)
    return p, s, objs, t


def bb(o):
    b = o.bbox()
    return (round(b.xmin, 6), round(b.ymin, 6), round(b.xmax, 6), round(b.ymax, 6))


@pytest.mark.parametrize("how, size, rect, tabs", [
    ("left", (H, W), (330, 10, 380, 110), [[380, 60], [100, 540]]),
    ("right", (H, W), (20, 490, 70, 590), [[20, 540], [300, 60]]),
    ("half", (W, H), (490, 330, 590, 380), [[540, 380], [60, 100]]),
    ("mirror_lr", (W, H), (490, 20, 590, 70), [[540, 20], [60, 300]]),
    ("mirror_tb", (W, H), (10, 330, 110, 380), [[60, 380], [540, 100]]),
])
def test_the_sheet_turns_with_everything_on_it(how, size, rect, tabs):
    p, s, (r, c), t = project()
    turn_sheet(p, s, how)
    assert (s.width, s.height) == size
    assert bb(r) == pytest.approx(rect)
    assert [pytest.approx(q) for q in tabs] == t.params["tab_points"]
    b = BBox()
    for o in s.vectors:
        b = b.union(o.bbox())
    assert b.xmin >= 0 and b.ymin >= 0 and b.xmax <= s.width and b.ymax <= s.height     # (nothing leaves the plate)


def test_four_quarter_turns_and_two_mirrors_give_the_sheet_back():
    p, s, (r, c), t = project()
    first = (bb(r), bb(c), [list(x) for x in t.params["tab_points"]])
    for how in ("left", "left", "right", "half", "mirror_lr", "mirror_lr", "mirror_tb", "mirror_tb", "left"):
        turn_sheet(p, s, how)
    assert (s.width, s.height) == (W, H)
    assert bb(r) == pytest.approx(first[0]) and bb(c) == pytest.approx(first[1])
    assert [pytest.approx(q) for q in first[2]] == t.params["tab_points"]


def test_the_outline_of_a_leftover_turns_too():
    p, s, _objs, _t = project()
    s.outline = [[(0, 0), (600, 0), (600, 200), (300, 400), (0, 400)]]
    turn_sheet(p, s, "left")
    assert [pytest.approx(q) for q in [(400, 0), (400, 600), (200, 600), (0, 300), (0, 0)]] == [tuple(q) for q in s.outline[0]]
    turn_sheet(p, s, "mirror_lr")                        # (a mirror turns it round: it still goes the same way)
    from iyicam.core.geom import polygon_area
    assert polygon_area(s.outline[0]) > 0


def test_a_toolpath_of_other_sheets_keeps_its_points():
    p, s, objs, t = project()
    from iyicam.core.document import Sheet
    other = Sheet("B", W, H)
    p.sheets.append(other)
    t.selection.vector_ids = []
    t.selection.mode = "layers"
    t.sheet_ids = [other.id]
    turn_sheet(p, s, "half")
    assert t.params["tab_points"] == [[60, 20], [540, 300]]


def test_the_commands_turn_the_sheet_in_the_engine():
    from iyicam.service import ops
    p, s, _objs, _t = project()
    r = ops.op_command({"project": p.to_dict(), "sheet": s.id, "command": "model.rot90l"})
    sh = r["project"]["sheets"][0]
    assert (sh["width"], sh["height"]) == (H, W) and r["changed"]
    schema = ops.op_schema({})
    ids = {c["id"]: c for c in schema["commands"]}
    for cid in ("model.rot90l", "model.rot90r", "model.rot180", "model.mirror_lr", "model.mirror_tb"):
        assert ids[cid]["menu"] == "Model" and ids[cid]["min_selection"] == 0


def test_the_web_has_the_model_menu_and_the_solid_view():
    import re
    from pathlib import Path
    ui = Path(__file__).resolve().parents[1] / "iyicam" / "webapp" / "ui"
    menus = (ui / "menudef.js").read_text()
    for cid in ("model.rot90l", "model.rot90r", "model.rot180", "model.mirror_lr", "model.mirror_tb"):
        assert f'"cmd": "{cid}"' in menus
    assert '"id": "view.draw_toolpaths_solid"' in menus
    assert re.search(r'\["view.draw_toolpaths_solid", "solidToolpaths"\]', (ui / "actions.js").read_text())


# -- solid ----------------------------------------------------------------------------------------------------------

def test_a_toolpath_solid_is_its_cuts_by_depth():
    from iyicam.cam.job import compute_toolpath
    from iyicam.gui.qtgeom import toolpath_solid
    p, s, objs, t = project()
    t.params.update({"through": False, "cut_depth": 9.0, "stepdown": 3.0, "tabs": False})
    r = compute_toolpath(p, t, s)
    levels = toolpath_solid(r.toolpaths[0])
    assert [round(z, 3) for z, _ in levels] == [-3.0, -6.0, -9.0]         # shallow first
    deepest = levels[-1][1].boundingRect()
    assert deepest.left() == pytest.approx(10 - 3, abs=0.05) and deepest.right() == pytest.approx(540 + 3, abs=0.05)


def test_the_colour_of_a_cut_goes_from_light_to_full():
    from iyicam.gui.qtgeom import solid_color
    shallow, deep = solid_color("#3182bd", 1.0, 18.0), solid_color("#3182bd", 18.0, 18.0)
    assert deep.name() == "#3182bd" and shallow.lightness() > deep.lightness()


def test_the_desktop_draws_toolpaths_solid():
    from PySide6.QtWidgets import QApplication
    from iyicam.gui.app import create_window
    app, w = create_window(["test"], load_plugins=False)
    try:
        w.resize(1000, 700)
        w.show()
        c = w.ctrl
        c.settings.auto_recalculate = False
        p, s, objs, t = project()
        t.params["tabs"] = False
        c._replace(p)
        w.recalculate()
        for _ in range(400):
            QApplication.processEvents()
            if c.results and not c.stale:
                break
        w.canvas.zoom_extents()
        QApplication.processEvents()

        def colour_at(x, y):
            img = w.canvas.grab().toImage()
            q = w.canvas.world_to_screen(x, y)
            return img.pixelColor(int(q.x()), int(q.y()))
        thin = colour_at(60, 18)                     # (2 mm out of the rectangle: in the cut, beside the line)
        w._toggle("solid_toolpaths", True)
        QApplication.processEvents()
        solid = colour_at(60, 18)
        assert solid != thin and solid.saturation() > 80, (thin.name(), solid.name())
        w._toggle("solid_toolpaths", False)
        assert colour_at(60, 18) == thin
    finally:
        for d in w.documents():
            d.dirty = False
        w.close()

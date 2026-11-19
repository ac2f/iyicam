"""A blank project has no sheet until one is added (its size, thickness and Z zero asked for); Z zero on the table is warned
about where the G-code is saved."""
import pytest

from iyicam.core import presets, shapes
from iyicam.core.document import VectorObject, project_from_dict, project_to_dict


def test_a_blank_project_has_no_sheet_and_is_kept_without_one():
    p = presets.create(template=presets.BLANK)
    assert p.sheets == []
    assert project_from_dict(project_to_dict(p)).sheets == []
    assert presets.templates()[0]["id"] == presets.BLANK
    one = presets.create(300, 200, 6, "")
    assert len(one.sheets) == 1 and one.sheets[0].width == 300


pytest.importorskip("PySide6.QtWidgets")


@pytest.fixture
def rwin(tmp_path, monkeypatch):
    from gui_robot import rwin_gen
    yield from rwin_gen(tmp_path, monkeypatch)


def test_nothing_can_be_drawn_before_the_first_sheet_and_the_first_sheet_asks_its_size(rwin, monkeypatch):
    from gui_robot import process
    from iyicam.gui.dialogs import SheetDialog
    win, _ = rwin
    c = win.ctrl
    c.new_project(template=presets.BLANK)
    process()
    assert not c.has_sheet and c.sheet.width == 0
    win.canvas.grab()                                                   # (the message is drawn, nothing breaks)
    said = []
    c.message.connect(said.append)
    c.add_vectors([VectorObject([shapes.rectangle(0, 0, 10, 10)], c.layer_id)])
    assert not c.project.sheets and any("sheet" in m for m in said)
    # the first sheet: cancelled leaves no sheet, accepted gives the sheet with its thickness and Z zero
    monkeypatch.setattr(SheetDialog, "exec", lambda self: 0)
    assert win.sheets_panel.add() is False and not c.has_sheet

    def accept(self):
        w = self.form.widgets
        w["width"].setValue(1250)
        w["height"].setValue(3200)
        w["thickness"].setValue(4)
        w["z_zero"].setCurrentIndex(w["z_zero"].findData("bottom"))
        self.accept()
        return 1
    monkeypatch.setattr(SheetDialog, "exec", accept)
    assert win.sheets_panel.add() is True
    process()
    assert c.has_sheet and (c.sheet.width, c.sheet.height) == (1250, 3200)
    assert c.project.material.thickness == 4 and c.project.material.z_zero == "bottom"
    c.add_vectors([VectorObject([shapes.rectangle(0, 0, 10, 10)], c.layer_id)])
    assert len(c.sheet.vectors) == 1


def test_save_gcode_warns_when_z_zero_is_the_table(rwin):
    from gui_robot import process
    from test_cutedit import parts
    from iyicam.gui.dialogs import ExportDialog, zero_warning
    win, _ = rwin
    c, t, objs = parts(win, n=1, auto=False)
    assert zero_warning(c.project) == ""
    c.project.material.z_zero = "bottom"
    d = ExportDialog(c, win)
    process()
    assert d.zero_warning.isVisibleTo(d) and "table" in d.zero_warning.text()
    assert f"{c.project.material.thickness:g} mm" in d.zero_warning.text()

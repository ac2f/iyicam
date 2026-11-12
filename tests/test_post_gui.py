"""The desktop post editor (a form for every option, the blocks, the file, a preview you can click), the G-code you
can click in *Save G-code*, and the window that says what I J K are."""
import tomllib

import pytest

pytest.importorskip("PySide6.QtWidgets")

from conftest import end_mill  # noqa: E402
from gui_robot import populate, process, rwin_gen  # noqa: E402
from PySide6.QtCore import Qt  # noqa: E402
from PySide6.QtGui import QTextCursor  # noqa: E402

from iyicam.core import paths  # noqa: E402
from iyicam.core.document import Selection, ToolpathDef  # noqa: E402


@pytest.fixture
def rwin(tmp_path, monkeypatch):
    yield from rwin_gen(tmp_path, monkeypatch)
    win_registry_clean()


def win_registry_clean():
    from iyicam.core.registry import REGISTRY
    REGISTRY.remove_source("user-posts")      # (the registry is global: leave nothing of a test in it)


def own_post(dlg, name="Mine"):
    """Select Mach3 in the editor and make a post of the user from it (the robot answers the name)."""
    dlg.list.setCurrentRow([p.id for p in dlg.posts].index("mach3"))
    dlg.new()
    pid = next(p.id for p in dlg.posts if p.id.endswith("_custom"))
    dlg.list.setCurrentRow([p.id for p in dlg.posts].index(pid))
    return pid


def line_with(view, needle):
    for i, ln in enumerate(view.text.toPlainText().split("\n")):
        if needle in ln:
            return i
    raise AssertionError(needle)


def click_line(view, i):
    cur = QTextCursor(view.text.document().findBlockByNumber(i))
    view.text.setTextCursor(cur)
    process()


def test_a_post_that_comes_with_the_program_is_shown_but_not_changed(rwin):
    from iyicam.gui.dialogs import PostEditorDialog
    win, robot = rwin
    dlg = PostEditorDialog(win.ctrl, win, select="mach3")
    assert dlg.current.id == "mach3" and dlg.draft["options"]["decimals"] == 4
    assert not dlg.save_btn.isEnabled() and not dlg.delete_btn.isEnabled() and dlg.edit.isReadOnly()
    assert not dlg.name_edit.isEnabled()
    w = dlg.options.fields["decimals"][1]
    assert w.value() == 4 and not w.isEnabled()                       # (the form shows it, but it cannot be changed)
    assert "cannot be changed" in dlg.status.text()
    assert "G0" in dlg.out.toPlainText()                              # the preview is there at once
    assert robot.problems() == []


def test_changing_the_form_writes_the_file_and_the_preview(rwin):
    from iyicam.gui.dialogs import PostEditorDialog
    win, robot = rwin
    dlg = PostEditorDialog(win.ctrl, win)
    pid = own_post(dlg)
    assert dlg.save_btn.isEnabled() and dlg.options.fields["decimals"][1].isEnabled()
    dlg.options.fields["line_numbers"][1].setChecked(True)            # a person ticks a box
    dlg.options.fields["decimals"][1].setValue(2)
    t = tomllib.loads(dlg.edit.toPlainText())
    assert t["extends"] == "mach3" and t["options"] == {"decimals": 2, "line_numbers": True}      # only what differs
    dlg._settle()
    text = dlg.out.toPlainText()
    assert "N10 " in text and "X100.54" in text and "X100.538" not in text
    assert dlg.options.fields["decimals"][2].isVisible() or not dlg.isVisible()                    # (marked as changed)
    dlg.save()
    process()
    assert REG().posts[pid].options["decimals"] == 2 and REG().posts[pid].options["line_numbers"] is True
    assert (paths.posts_dir() / f"{pid}.toml").exists()
    assert robot.problems() == []


def REG():
    from iyicam.core.registry import REGISTRY
    return REGISTRY


def test_typing_in_the_file_changes_the_form(rwin):
    from iyicam.gui.dialogs import PostEditorDialog
    win, robot = rwin
    dlg = PostEditorDialog(win.ctrl, win)
    own_post(dlg)
    text = dlg.edit.toPlainText().replace("[options]", "[options]\ndecimals = 1\nline_numbers = true", 1) \
        if "[options]" in dlg.edit.toPlainText() else dlg.edit.toPlainText() + "\n[options]\ndecimals = 1\nline_numbers = true\n"
    dlg.edit.setPlainText(text)                                       # (a person types)
    dlg._settle()                                                      # (after the pause)
    assert dlg.options.fields["decimals"][1].value() == 1 and dlg.options.fields["line_numbers"][1].isChecked()
    assert "N10 " in dlg.out.toPlainText()
    # a mistake while typing is told, and nothing is lost
    dlg.edit.setPlainText(text + "\n[options\n")
    dlg._settle()
    assert "TOMLDecodeError" in dlg.status.text() and "cannot be used yet" in dlg.out.toPlainText()
    assert dlg.options.fields["decimals"][1].value() == 1
    assert robot.problems() == []


def test_a_block_is_edited_with_words_and_variables(rwin):
    from iyicam.gui.dialogs import PostEditorDialog
    win, robot = rwin
    dlg = PostEditorDialog(win.ctrl, win)
    own_post(dlg)
    b = dlg.blocks
    names = [b.list.item(i).data(Qt.UserRole) for i in range(b.list.count())]
    b.list.setCurrentRow(names.index("footer"))
    assert "M30" in b.edit.toPlainText() and b.words.count() == 1 and b.vars_box.count() > 10
    b.edit.setPlainText("G0 Z{safe_z}\nM30")
    b.vars_box.activated.emit(b.vars_box.findText("home_x", Qt.MatchContains))
    assert "{home_x}" in b.edit.toPlainText() and b.vars_box.currentIndex() == 0
    t = tomllib.loads(dlg.edit.toPlainText())
    assert "G0 Z{safe_z}" in t["blocks"]["footer"] and "{home_x}" in t["blocks"]["footer"]
    assert b.reset_btn.isEnabled()
    b.reset_btn.click()
    assert "M5" in b.edit.toPlainText() and "blocks" not in tomllib.loads(dlg.edit.toPlainText())    # (back to the parent's)
    # the words of a move block
    b.list.setCurrentRow(names.index("arc"))
    assert [b.words.itemText(i) for i in range(1, b.words.count())][:5] == ["G", "X", "Y", "Z", "F"]
    assert "I" in [b.words.itemText(i) for i in range(1, b.words.count())]
    assert robot.problems() == []


def test_a_mistake_in_the_form_is_said_and_the_file_is_left_alone(rwin):
    from iyicam.gui.dialogs import PostEditorDialog
    win, _ = rwin
    dlg = PostEditorDialog(win.ctrl, win)
    own_post(dlg)
    before = dlg.edit.toPlainText()
    dlg.name_edit.setText("")
    dlg.name_edit.textEdited.emit("")
    assert "name" in dlg.status.text() and dlg.edit.toPlainText() == before
    dlg.name_edit.setText("Fine")
    dlg.name_edit.textEdited.emit("Fine")
    assert dlg.status.text() == "" and 'name = "Fine"' in dlg.edit.toPlainText()
    # the header must say how I J are counted
    dlg.options.fields["ijk"][1].setCurrentIndex(dlg.options.fields["ijk"][1].findData("absolute"))
    assert "G90.1" in dlg.status.text()
    assert dlg.options.fields["ijk"][0].isVisible() or not dlg.isVisible()
    dlg.options.fields["arcs"][1].setCurrentIndex(dlg.options.fields["arcs"][1].findData("none"))
    assert dlg.options.fields["helical"][0].isHidden() and dlg.options.fields["ijk"][0].isHidden()      # (what no longer matters is hidden)


def test_delete_asks_and_removes_only_a_post_of_the_user(rwin):
    from iyicam.gui.dialogs import PostEditorDialog
    win, robot = rwin
    dlg = PostEditorDialog(win.ctrl, win)
    pid = own_post(dlg)
    assert (paths.posts_dir() / f"{pid}.toml").exists() and dlg.delete_btn.isEnabled()
    dlg.delete()
    assert not (paths.posts_dir() / f"{pid}.toml").exists() and pid not in REG().posts
    dlg.list.setCurrentRow([p.id for p in dlg.posts].index("mach3"))
    n = len(REG().posts)
    dlg.delete()                                                       # a built-in post: nothing happens
    assert len(REG().posts) == n and "mach3" in REG().posts
    assert robot.problems() == []


def test_the_preview_can_be_the_current_sheet_and_a_click_explains_a_line(rwin):
    from iyicam.gui.dialogs import PostEditorDialog
    win, robot = rwin
    c = win.ctrl
    objs = populate(win, select=False)
    c.add_toolpath(ToolpathDef("p", "profile", end_mill(6), {"through": True}, Selection("vectors", [objs[0].id])))
    dlg = PostEditorDialog(c, win, select="mach3")
    dlg.source.setCurrentIndex(dlg.source.findData("sheet"))
    assert "Panel" in dlg.out.toPlainText() or "p " in dlg.out.toPlainText() or "G1" in dlg.out.toPlainText()
    dlg.source.setCurrentIndex(dlg.source.findData("sample"))
    i = line_with(dlg.view, "G3 ")
    click_line(dlg.view, i)
    note = dlg.view.note.toPlainText()
    assert "counter-clockwise" in note and "centre of the circle" in note and "I" in note and "radius" in note
    click_line(dlg.view, line_with(dlg.view, "M3"))
    assert "Spindle" in dlg.view.note.toPlainText()
    assert robot.problems() == []


def test_save_gcode_has_the_editor_and_explains_lines(rwin):
    from iyicam.gui.dialogs import ExportDialog, PostEditorDialog
    win, robot = rwin
    c = win.ctrl
    objs = populate(win, select=False)
    c.add_toolpath(ToolpathDef("p", "profile", end_mill(6), {"through": True}, Selection("vectors", [objs[0].id])))
    dlg = ExportDialog(c, win)
    dlg.post.setCurrentIndex(dlg.post.findData("mach3"))
    dlg.preview()
    text = dlg.text.toPlainText()
    assert "G3" in text or "G2" in text
    i = line_with(dlg.view, "G2 ") if "G2 " in text else line_with(dlg.view, "G3 ")
    click_line(dlg.view, i)
    assert "centre of the circle" in dlg.view.note.toPlainText()
    # the editor, opened from here: a post made there is chosen when it closes
    n = dlg.post.count()
    robot.hook = lambda d: d.new() if isinstance(d, PostEditorDialog) else None
    dlg._edit_post()
    robot.hook = None
    assert dlg.post.count() == n + 1 and dlg.post.currentData().endswith("_custom")
    assert robot.problems() == []


def test_the_words_of_gcode_have_a_window(rwin):
    from iyicam.gui.posts import ArcFigure, GlossaryDialog
    win, robot = rwin
    dlg = GlossaryDialog(win)
    text = dlg.view.toPlainText()
    for word in ("centre", "G91.1", "M30", "radius", "Peck depth"):
        assert word in text
    fig = ArcFigure()
    fig.resize(300, 200)
    assert not fig.grab().isNull()                                    # (it paints)
    robot.dialogs.clear()
    helpm = next(a.menu() for a in win.menuBar().actions() if a.text() == "&Help")
    next(a for a in helpm.actions() if "G-code Words" in a.text()).trigger()
    assert "G-code words" in robot.dialogs
    assert robot.problems() == []

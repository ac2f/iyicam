"""Tabs: every tab is a project of its own, and the panels, menus, tools and title follow the open one."""
import pytest

pytest.importorskip("PySide6.QtWidgets")

from gui_robot import populate, process, rwin_gen  # noqa: E402
from PySide6.QtCore import QMimeData, QUrl  # noqa: E402
from PySide6.QtGui import QAction, QDropEvent  # noqa: E402
from PySide6.QtWidgets import QMessageBox  # noqa: E402
from test_memory import Session  # noqa: E402

from iyicam.core import shapes  # noqa: E402
from iyicam.core.document import VectorObject  # noqa: E402


@pytest.fixture
def rwin(tmp_path, monkeypatch):
    yield from rwin_gen(tmp_path, monkeypatch)


def saved_project(win, tmp_path, name, n=1):
    """A project with ``n`` circles saved as ``name.icam`` (in a tab of its own, left open)."""
    robot_files = []
    c = win.ctrl
    objs = [VectorObject([shapes.circle((50 + 30 * i, 50), 10)], c.layer_id, f"{name}{i}") for i in range(n)]
    c.add_vectors(objs, "test")
    f = tmp_path / f"{name}.icam"
    c.save_project(f)
    assert not robot_files
    return f


def test_a_new_project_opens_in_a_new_tab_unless_the_open_one_is_unused(rwin):
    win, robot = rwin
    assert win.tabs.count() == 1 and win.ctrl.pristine
    win.new_project()                                   # the first tab was never used: it is taken
    assert win.tabs.count() == 1
    populate(win)
    first = win.ctrl
    win.new_project()                                   # now it holds work: a new tab
    process()
    assert win.tabs.count() == 2 and win.ctrl is not first
    assert win.ctrl.pristine and win.ctrl.sheet.vectors == []
    assert win.tabs.currentIndex() == 1
    assert robot.problems() == []


def test_the_tabs_do_not_share_anything(rwin):
    win, robot = rwin
    populate(win)
    first = win.ctrl
    n_first = len(first.sheet.vectors)
    first.select([first.sheet.vectors[0].id])
    win.new_project()
    second = win.ctrl
    assert second is not first
    second.add_vectors([VectorObject([shapes.circle((10, 10), 5)], second.layer_id, "only here")], "x")
    second.project.add_layer("Second layer")
    second.set_sheet(second.project.add_sheet("Sheet B").id)
    process()
    # the panels show the tab that is open
    assert any(second.project.layers[-1].name in win.layers_panel.tree.topLevelItem(i).text(0)
               for i in range(win.layers_panel.tree.topLevelItemCount()))
    assert win.sheets_panel.list.count() == 2
    win.tabs.setCurrentIndex(0)
    process()
    assert win.ctrl is first and win.canvas.ctrl is first
    assert len(first.sheet.vectors) == n_first and len(first.project.sheets) == 1
    assert win.sheets_panel.list.count() == 1
    assert [first.sheet.vectors[0].id] == first.selection         # the selection of the tab was kept
    assert win.props_panel.ctrl is first and win.console.ctrl is first
    # undo from the menu acts on the open tab only
    win.undo_action.trigger()
    assert len(first.sheet.vectors) == 0 and n_first > 0
    assert sum(len(sh.vectors) for sh in second.project.sheets) == 1       # (on its first sheet)
    assert robot.problems() == []


def test_menu_actions_work_on_the_tab_that_is_open_not_the_first(rwin):
    win, robot = rwin
    populate(win)
    win.new_project()
    second = win.ctrl
    second.add_vectors([VectorObject([shapes.circle((10, 10), 5)], second.layer_id, "c")], "add circle")
    first = win.documents()[0]
    n1 = len(first.sheet.vectors)
    win.undo_action.trigger()                          # undoes the circle of the second tab
    assert second.sheet.vectors == [] and len(first.sheet.vectors) == n1
    win.redo_action.trigger()
    assert len(second.sheet.vectors) == 1
    win.canvas.scale = 123.0
    for a in win.menuBar().findChildren(QAction):
        if a.text().replace("&", "") == "Zoom Extents":
            a.trigger()
    assert win.canvas.scale != 123.0 and win.tabs.widget(0).scale != 123.0
    assert robot.problems() == []


def test_the_tool_and_the_simulation_switch_follow_the_tab(rwin):
    win, _ = rwin
    populate(win)
    win.new_project()
    win.set_tool("circle")
    assert win.tool_actions["circle"].isChecked() and win.canvas.tool.name == "circle"
    win.tabs.setCurrentIndex(0)
    assert win.tool_actions["select"].isChecked() and win.canvas.tool.name == "select"
    win.canvas.show_sim = True
    win.tabs.setCurrentIndex(1)
    assert not win.sim_action.isChecked() and win.canvas.tool.name == "circle"
    win.tabs.setCurrentIndex(0)
    assert win.sim_action.isChecked()


def test_open_shows_a_file_that_is_open_already_and_takes_an_unused_tab(rwin, tmp_path):
    win, robot = rwin
    f = saved_project(win, tmp_path, "alpha", 3)      # the first tab now is alpha.icam
    g_win_ctrl = win.ctrl
    win.new_project()
    other = win.ctrl
    assert win.tabs.count() == 2
    win.open_project(str(f))                           # already open in tab 1: shown, not opened twice
    assert win.tabs.count() == 2 and win.ctrl is g_win_ctrl
    other_file = tmp_path / "beta.icam"
    other.add_vectors([VectorObject([shapes.circle((10, 10), 5)], other.layer_id, "b")], "b")
    other.save_project(other_file)
    win.new_project()                                  # a third tab, unused
    assert win.tabs.count() == 3
    win.open_project(str(other_file))                  # open in another tab: shown
    assert win.tabs.count() == 3 and win.ctrl is other
    third = win.documents()[2]
    win.activate(third)
    gamma = tmp_path / "gamma.icam"
    from iyicam.core.document import Project
    p = Project()
    p.save(gamma)
    win.open_project(str(gamma))                       # the unused third tab takes it
    assert win.tabs.count() == 3 and win.ctrl is third and win.ctrl.project.path == gamma
    assert robot.problems() == []


def test_a_file_that_cannot_be_opened_leaves_the_tabs_as_they_are(rwin, tmp_path):
    win, robot = rwin
    populate(win)
    bad = tmp_path / "bad.icam"
    bad.write_text("this is not a project")
    n = win.tabs.count()
    win.open_project(str(bad))
    assert win.tabs.count() == n
    assert any("Cannot open" in t for k, t in robot.messages if k == "warning")


def test_tab_titles_show_changes_and_the_name(rwin, tmp_path):
    win, _ = rwin
    f = saved_project(win, tmp_path, "my&part")
    assert win.tabs.tabText(0).replace("&&", "&") == "my&part" and "•" not in win.tabs.tabText(0)
    assert win.tabs.tabToolTip(0) == str(f)
    win.ctrl.add_vectors([VectorObject([shapes.circle((5, 5), 2)], win.ctrl.layer_id, "x")], "more")
    assert "•" in win.tabs.tabText(0)               # unsaved changes
    assert "*" in win.windowTitle() and "my&part" in win.windowTitle()
    win.save_project()
    assert "•" not in win.tabs.tabText(0) and "*" not in win.windowTitle()
    win.new_project()
    assert win.tabs.tabToolTip(1) == "Not saved yet"
    assert win.windowTitle().startswith(win.ctrl.title)


def test_closing_a_tab_asks_when_there_are_changes(rwin, tmp_path, monkeypatch):
    win, robot = rwin
    populate(win)
    win.new_project()
    populate(win)
    win.new_project()
    assert win.tabs.count() == 3
    answers = iter([QMessageBox.Cancel, QMessageBox.Discard])
    monkeypatch.setattr(QMessageBox, "question", staticmethod(lambda *a, **k: next(answers)))
    assert win.close_tab(1) is False                    # cancelled: the tab stays
    assert win.tabs.count() == 3
    assert win.close_tab(1) is True                     # discarded: it goes
    assert win.tabs.count() == 2
    win.close_tab(1)                                    # (the unused one: no question)
    assert win.tabs.count() == 1 and robot.problems() == []


def test_saving_when_closing_a_tab(rwin, tmp_path, monkeypatch):
    win, robot = rwin
    populate(win)
    win.new_project()
    populate(win)
    monkeypatch.setattr(QMessageBox, "question", staticmethod(lambda *a, **k: QMessageBox.Save))
    target = tmp_path / "saved_on_close.icam"
    robot.files.append(str(target))
    assert win.close_tab(1)
    assert target.exists() and win.tabs.count() == 1
    assert win.closed_tabs and win.closed_tabs[0] == str(target)
    win.reopen_closed_tab()                              # Ctrl+Shift+T
    assert win.tabs.count() == 2 and win.ctrl.project.path == target


def test_the_last_tab_is_emptied_not_removed(rwin):
    win, _ = rwin
    populate(win)
    c = win.ctrl
    assert win.close_tab()
    assert win.tabs.count() == 1 and win.ctrl is c and c.pristine and c.sheet.vectors == []


def test_close_other_tabs_and_duplicate(rwin):
    win, robot = rwin
    populate(win)
    win.new_project()
    populate(win)
    win.new_project()
    assert win.tabs.count() == 3
    win.tabs.setCurrentIndex(0)
    n = len(win.ctrl.sheet.vectors)
    win.duplicate_tab(0)
    assert win.tabs.count() == 4 and len(win.ctrl.sheet.vectors) == n and win.ctrl.dirty
    copy = win.ctrl
    assert copy is not win.documents()[0] and copy.project.path is None and "copy" in copy.title
    copy.delete_selection()
    win.close_other_tabs(win.tabs.currentIndex())
    assert win.tabs.count() == 1 and win.ctrl is copy
    assert robot.problems() == []


def test_next_and_previous_tab_and_the_window_menu(rwin):
    win, _ = rwin
    populate(win)
    win.new_project()
    win.new_project() if not win.ctrl.pristine else None
    populate(win)
    win.new_project()
    n = win.tabs.count()
    assert n >= 3
    win.tabs.setCurrentIndex(0)
    win.next_tab(-1)
    assert win.tabs.currentIndex() == n - 1
    win.next_tab(1)
    assert win.tabs.currentIndex() == 0
    win._fill_window_menu()
    actions = [a for a in win.window_menu.actions() if a.isCheckable()]
    assert len(actions) == n and actions[0].isChecked()
    actions[n - 1].trigger()
    assert win.tabs.currentIndex() == n - 1


def test_the_tab_menu_and_the_new_tab_button(rwin):
    win, robot = rwin
    populate(win)
    win.tabs.add_button.click()                          # the + button: the New Project dialog
    assert win.tabs.count() == 2
    win._tab_menu(0, win.mapToGlobal(win.rect().center()))
    win._tab_menu(-1, win.mapToGlobal(win.rect().center()))
    assert robot.problems() == []


def test_units_follow_in_every_tab(rwin):
    win, _ = rwin
    populate(win)
    win.new_project()
    first, second = win.documents()
    seen = []
    first.changed.connect(lambda: seen.append("first"))
    second.set_units("inch")
    assert first.units.name == "inch" and second.units.name == "inch"
    assert "first" in seen
    second.set_units("mm")
    assert first.units.name == "mm"


def test_tools_and_settings_are_shared_between_tabs(rwin):
    win, _ = rwin
    populate(win)
    win.new_project()
    a, b = win.documents()
    assert a.tools is b.tools and a.settings is b.settings


def test_the_console_follows_the_tab(rwin):
    win, _ = rwin
    populate(win)
    first = win.ctrl
    win.new_project()
    second = win.ctrl
    win.console.inp.setText("len(sheet().vectors)")
    win.console.execute()
    assert win.console.out.toPlainText().rstrip().endswith("0")
    win.tabs.setCurrentIndex(0)
    win.console.inp.setText("len(sheet().vectors) == %d" % len(first.sheet.vectors))
    win.console.execute()
    assert win.console.out.toPlainText().rstrip().endswith("True")
    win.console.inp.setText("add(shapes.circle((5, 5), 3))")
    win.console.execute()
    assert len(second.sheet.vectors) == 0 and any(v.name == "script" for v in first.sheet.vectors)


def test_files_dropped_on_the_window(rwin, tmp_path):
    win, robot = rwin
    f = saved_project(win, tmp_path, "dropped")
    g = tmp_path / "second.icam"
    from iyicam.core.document import Project
    Project().save(g)
    win.new_project()
    mime = QMimeData()
    mime.setUrls([QUrl.fromLocalFile(str(f)), QUrl.fromLocalFile(str(g)), QUrl.fromLocalFile(str(tmp_path / "x.txt"))])
    from PySide6.QtCore import QPointF, Qt
    ev = QDropEvent(QPointF(10, 10), Qt.CopyAction, mime, Qt.LeftButton, Qt.NoModifier)
    win.dropEvent(ev)
    process()
    paths = {str(c.project.path) for c in win.documents() if c.project.path}
    assert {str(f), str(g)} <= paths
    assert robot.problems() == []


def test_tabs_come_back_in_the_next_session(tmp_path, monkeypatch):
    monkeypatch.setenv("IYICAM_HOME", str(tmp_path / "home"))
    s = Session(monkeypatch)
    win = s.win
    files = []
    for name in ("one", "two", "three"):
        files.append(saved_project(win, tmp_path, name, n=2))
        win.new_project()
        process()
    win.tabs.setCurrentIndex(1)
    win = s.restart()                                    # the program is closed and started again
    assert win.tabs.count() == 1 and win.ctrl.pristine
    win.restore_tabs()
    assert [str(c.project.path) for c in win.documents()] == [str(f) for f in files]
    assert win.tabs.currentIndex() == 1 and win.ctrl.title == "two"
    assert all(len(c.sheet.vectors) == 2 for c in win.documents())
    # a file that is gone is skipped, and so is a tab that was never saved
    files[0].unlink()
    win = s.restart()
    win.restore_tabs()
    assert [c.title for c in win.documents()] == ["two", "three"]
    assert s.robot.problems() == []


def test_closing_the_window_asks_for_every_changed_tab(tmp_path, monkeypatch):
    monkeypatch.setenv("IYICAM_HOME", str(tmp_path / "home"))
    s = Session(monkeypatch)
    win = s.win
    populate(win)
    win.new_project()
    populate(win)
    asked = []

    def answer(parent, title, text, *a, **k):
        asked.append(text)
        return QMessageBox.Cancel if len(asked) == 2 else QMessageBox.Discard
    monkeypatch.setattr(QMessageBox, "question", staticmethod(answer))
    assert win.close() is False                          # the second question is answered with Cancel
    assert win.isVisible() and len(asked) == 2 and win.tabs.count() == 2
    assert win.ctrl is win.documents()[1]               # (the tab with the question is shown)
    for c in win.documents():
        c.dirty = False
    assert win.close()
    assert not win.isVisible()


def test_projects_that_were_never_saved_are_numbered(rwin):
    win, _ = rwin
    populate(win)
    assert win.ctrl.title == "Untitled"
    win.new_project()
    populate(win)
    win.new_project()
    assert [c.title for c in win.documents()] == ["Untitled", "Untitled 2", "Untitled 3"]
    assert [win.tabs.tabText(i).rstrip(" •") for i in range(3)] == ["Untitled", "Untitled 2", "Untitled 3"]
    win.tabs.setCurrentIndex(1)
    win.ctrl.dirty = False
    win.close_tab(1)
    populate(win)
    win.new_project()                                  # (the number that is free again)
    assert sorted(c.title for c in win.documents()) == ["Untitled", "Untitled 2", "Untitled 3"]
    win.duplicate_tab(0)
    win.duplicate_tab(0)
    titles = [c.title for c in win.documents()]
    assert len(set(titles)) == len(titles) and "Untitled copy" in titles and "Untitled copy 2" in titles

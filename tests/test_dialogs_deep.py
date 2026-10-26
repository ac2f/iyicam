"""The tool database, post-processor editor and plugin manager, used the way a person uses them."""
import json
import textwrap
from pathlib import Path

import pytest

pytest.importorskip("PySide6.QtWidgets")

from conftest import end_mill  # noqa: E402
from gui_robot import populate, process, rwin_gen  # noqa: E402
from PySide6.QtCore import Qt  # noqa: E402

from iyicam.core.document import Selection, ToolpathDef  # noqa: E402


@pytest.fixture
def rwin(tmp_path, monkeypatch):
    yield from rwin_gen(tmp_path, monkeypatch)


def test_tool_database_add_copy_edit_delete_import_export(rwin, tmp_path):
    from iyicam.gui.dialogs import ToolDatabaseDialog
    win, robot = rwin
    c = win.ctrl
    n0 = len(c.tools.tools)
    dlg = ToolDatabaseDialog(c, win)
    dlg.add()
    dlg.editor.form.widgets["name"].setText("My cutter")
    dlg.copy()
    dlg.copy()
    assert len(dlg.lib_tools) == n0 + 3
    dlg.feeds()                                     # the feeds dialog is accepted by the robot
    dlg.delete()
    dlg.delete()
    dlg.delete()                                    # nothing selected: nothing happens
    out = tmp_path / "tools.json"
    robot.files.append(str(out))
    dlg.export()
    data = json.loads(out.read_text())
    assert len(data["tools"]) == len(dlg.lib_tools)
    robot.files.append(str(out))
    dlg.import_()
    assert len(dlg.lib_tools) == 2 * len(data["tools"]) or len(dlg.lib_tools) > len(data["tools"])
    ids = [t.id for t in dlg.lib_tools]
    assert len(ids) == len(set(ids)), "imported tools got the ids of the tools that were there"
    for bad in ("{nope", "[1, 2, 3]", json.dumps({"tools": [1, 2]}), ""):
        f = tmp_path / "bad.json"
        f.write_text(bad)
        robot.files.append(str(f))
        robot.messages.clear()
        dlg.import_()
        assert any("not a tool library" in text or "Import tools" in text for _, text in robot.messages) or bad == "", bad
    robot.errors.clear()
    dlg.accept()
    assert len(c.tools.tools) == len(dlg.lib_tools)
    ToolDatabaseDialog(c, win).accept()             # saved and read again
    assert robot.problems() == [] or all("not a tool library" in p for p in robot.problems())


def test_tool_database_pick_for_a_toolpath(rwin):
    from iyicam.gui.dialogs import ToolDatabaseDialog
    win, robot = rwin
    c = win.ctrl
    dlg = ToolDatabaseDialog(c, win, pick=True)
    t = dlg.selected_tool()
    assert t is not None and t.id == c.tools.tools[0].id
    dlg.tree.setCurrentItem(dlg.tree.topLevelItem(0).child(0))
    assert dlg.selected_tool() is not None
    assert robot.problems() == []


def test_post_editor_new_broken_fixed(rwin):
    from iyicam.core import paths
    from iyicam.gui.dialogs import PostEditorDialog
    win, robot = rwin
    c = win.ctrl
    objs = populate(win, select=False)
    t = ToolpathDef("p", "profile", end_mill(6), {"through": True}, Selection("vectors", [objs[0].id]))
    c.add_toolpath(t)
    dlg = PostEditorDialog(c, win)
    dlg.list.setCurrentRow(0)
    n = len(c.registry.posts)
    dlg.new()                                       # a custom post based on the one selected
    assert len(c.registry.posts) == n + 1
    assert any(f.suffix == ".toml" for f in paths.posts_dir().iterdir())
    custom = next(p for p in c.registry.posts.values() if p.id.endswith("_custom"))
    row = [p.id for p in dlg.posts].index(custom.id)
    dlg.list.setCurrentRow(row)
    assert "G0" in dlg.out.toPlainText() or "G00" in dlg.out.toPlainText()    # the live preview
    good_text = dlg.edit.toPlainText()
    dlg.edit.setPlainText(good_text + "\n[options\nbroken = ")   # a TOML mistake
    dlg.save()
    process()
    assert robot.errors == []
    assert all(p.id != custom.id for p in dlg.posts)                          # it cannot be loaded ...
    assert len(dlg.broken) == 1 and dlg.list.count() == len(dlg.posts) + 1    # ... but it is still in the list
    dlg.list.setCurrentRow(dlg.list.count() - 1)
    assert "cannot be loaded" in dlg.out.toPlainText() and "options" in dlg.edit.toPlainText()
    fixed = good_text.split("[options")[0] + "\n[options]\ndecimals = 2\nline_numbers = true\n"
    dlg.edit.setPlainText(fixed)
    dlg.save()                                                                 # mended in the dialog itself
    process()
    assert dlg.broken == [] and any(p.id == custom.id for p in dlg.posts)
    row = [p.id for p in dlg.posts].index(custom.id)
    dlg.list.setCurrentRow(row)
    text = dlg.out.toPlainText()
    assert "N10" in text or "N1 " in text or "N0010" in text or "N1\n" in text   # the new option is in the preview
    # a post of the wrong kind of options does not take the others down
    (paths.posts_dir() / "wrong.toml").write_text('id = "wrong"\nextends = "mach3"\n[options]\ndecimals = "many"\n')
    dlg.reload()
    assert any(Path(p).name == "wrong.toml" and "decimals" in why for p, why in dlg.broken)   # named, with the reason
    assert all(p.id != "wrong" for p in dlg.posts)
    assert any(p.id == custom.id for p in dlg.posts)
    assert robot.errors == []
    c.registry.remove_source("user-posts")          # the registry is global: leave nothing of the test in it


def test_plugin_manager_install_toggle_reload(rwin, tmp_path):
    from iyicam.gui.dialogs import PluginsDialog
    win, robot = rwin
    c = win.ctrl
    plugin = tmp_path / "hello_plugin.py"
    plugin.write_text(textwrap.dedent('''
        PLUGIN = {"name": "Hello", "version": "1.0", "description": "says hello"}

        def register(api):
            api.add_command("hello.run", "Hello Command", lambda ctx, v: ctx.messages.append("hello"), menu="Plugins")
    '''))
    broken = tmp_path / "broken_plugin.py"
    broken.write_text("this is not python (")
    dlg = PluginsDialog(c, win.pm, win)
    n = dlg.table.rowCount()
    for f in (plugin, broken, plugin):              # the same one twice, and a broken one
        robot.files.append(str(f))
        dlg.install()
        process()
    assert dlg.table.rowCount() >= n + 1
    names = [dlg.table.item(r, 1).text() for r in range(dlg.table.rowCount())]
    assert any("ello" in x for x in names)
    status = {dlg.table.item(r, 1).text(): dlg.table.item(r, 3).text() for r in range(dlg.table.rowCount())}
    assert "error" in status.values()               # the broken one is shown, with its error
    for r in range(dlg.table.rowCount()):           # select each: the details are filled in
        dlg.table.setCurrentCell(r, 1)
    for _ in range(2):                              # switch every plugin off and on again
        for r in range(dlg.table.rowCount()):
            it = dlg.table.item(r, 0)
            it.setCheckState(Qt.Unchecked if it.checkState() == Qt.Checked else Qt.Checked)
            process()
    dlg.reload()
    process()
    # a plugin picked inside the plugins folder itself is not copied onto itself
    from iyicam.core import paths
    inside = next(paths.plugins_dir().glob("hello_plugin.py"))
    robot.files.append(str(inside))
    dlg.install()
    process()
    assert robot.errors == []
    for info in list(win.pm.plugins.values()):      # the registry is global: leave nothing of the test in it
        win.pm.unload(info)
    assert "hello.run" not in c.registry.commands

"""The look: light and dark themes, the system's choice, drawings and icons that follow, and the quick tool bar."""
import pytest

pytest.importorskip("PySide6.QtWidgets")

from gui_robot import populate, process, rwin_gen  # noqa: E402
from PySide6.QtCore import QtMsgType, qInstallMessageHandler  # noqa: E402
from PySide6.QtGui import QColor  # noqa: E402
from PySide6.QtWidgets import QApplication, QToolBar  # noqa: E402
from test_memory import Session  # noqa: E402

from iyicam.gui import theme  # noqa: E402
from iyicam.gui.settings import Settings  # noqa: E402


@pytest.fixture
def rwin(tmp_path, monkeypatch):
    yield from rwin_gen(tmp_path, monkeypatch)
    theme.apply(QApplication.instance(), "light")        # (the other tests run with the usual look)


class Messages:
    """The warnings Qt prints (a style sheet it cannot read says so there)."""

    def __enter__(self):
        self.lines = []
        self._old = qInstallMessageHandler(lambda kind, ctx, msg: self.lines.append((kind, msg)))
        return self

    def __exit__(self, *exc):
        qInstallMessageHandler(self._old)

    def bad(self):
        return [m for k, m in self.lines if k in (QtMsgType.QtWarningMsg, QtMsgType.QtCriticalMsg)
                and "propagateSizeHints" not in m]


@pytest.mark.parametrize("name", ["light", "dark", "system", "nonsense"])
def test_every_theme_applies_without_style_sheet_errors(rwin, name):
    win, robot = rwin
    populate(win)
    theme._applied = None                              # (an unchanged theme is not applied again)
    with Messages() as m:
        theme.apply(QApplication.instance(), name)
        win.apply_theme()
        win.grab()
        process()
    assert m.bad() == []
    assert QApplication.instance().styleSheet() and robot.problems() == []
    assert theme.get().name in ("light", "dark")


def test_the_themes_are_complete_and_different():
    for t in (theme.LIGHT, theme.DARK):
        for field, value in t.__dict__.items():
            if isinstance(value, str) and field not in ("name",):
                assert QColor(value).isValid(), (t.name, field, value)
    assert theme.LIGHT.dark is False and theme.DARK.dark is True
    assert QColor(theme.DARK.canvas).lightness() < QColor(theme.LIGHT.canvas).lightness()
    assert theme.resolve("dark") is theme.DARK and theme.resolve("light") is theme.LIGHT


def test_drawing_colours_can_be_seen_on_either_sheet(rwin):
    win, _ = rwin
    black = QColor("#000000")
    theme.apply(QApplication.instance(), "dark")
    assert theme.adapt(black).lightness() > 100 and theme.adapt(QColor("#ff7f0e")) == QColor("#ff7f0e")
    theme.apply(QApplication.instance(), "light")
    assert theme.adapt(QColor("#ffffff")).lightness() < 200 and theme.adapt(black) == black


def test_the_drawing_and_the_icons_follow_the_theme(rwin):
    win, _ = rwin
    populate(win)
    win.show()
    process()
    seen = {}
    for name in ("light", "dark"):
        win.set_theme(name)
        process()
        img = win.canvas.grab().toImage()
        seen[name] = QColor(img.pixel(img.width() - 4, img.height() - 4))      # outside the sheet
        assert seen[name] == QColor(theme.get().canvas)
    assert seen["light"] != seen["dark"]
    win.set_theme("dark")
    icon_dark = win.quick["new"].icon().pixmap(24, 24).toImage()
    win.set_theme("light")
    icon_light = win.quick["new"].icon().pixmap(24, 24).toImage()
    assert icon_dark != icon_light                                      # the icons are drawn again
    assert win.theme_actions["light"].isChecked() and not win.theme_actions["dark"].isChecked()


def test_the_theme_is_remembered(tmp_path, monkeypatch):
    monkeypatch.setenv("IYICAM_HOME", str(tmp_path / "home"))
    s = Session(monkeypatch)
    s.win.set_theme("dark")
    win = s.restart()
    assert Settings.load().theme == "dark" and theme.get().name == "dark"
    assert win.theme_actions["dark"].isChecked()
    # a damaged setting is ignored
    (tmp_path / "home" / "settings.json").write_text('{"theme": 12}')
    assert Settings.load().theme == "system"
    (tmp_path / "home" / "settings.json").write_text('{"theme": "neon"}')
    assert Settings.load().theme == "system"


def test_preferences_change_the_look_and_the_units(rwin):
    from iyicam.gui.dialogs import PreferencesDialog
    win, robot = rwin

    def pick(dlg):
        w = dlg.form.widgets["theme"]
        w.setCurrentIndex(w.findData("dark"))
    robot.hook = pick
    PreferencesDialog(win.ctrl, win).exec()
    assert theme.get().name == "dark" and win.ctrl.settings.theme == "dark"
    assert robot.problems() == []


def test_the_quick_tool_bar(rwin):
    win, robot = rwin
    populate(win)
    win.show()
    process()
    bar = next(b for b in win.findChildren(QToolBar) if b.windowTitle() == "Main")
    assert win.quick["undo"].isEnabled()               # populate made a change to undo
    win.quick["undo"].trigger()
    process()
    assert not win.quick["undo"].isEnabled() and win.quick["redo"].isEnabled()
    win.quick["redo"].trigger()
    assert win.quick["undo"].isEnabled()
    first = win.ctrl
    win.new_project()                                  # another tab: its own undo history
    assert win.ctrl is not first and not win.quick["undo"].isEnabled()
    win.tabs.setCurrentIndex(0)
    assert win.quick["undo"].isEnabled()
    for a in bar.actions():                            # every button does its job without a fault
        if not a.isSeparator() and a.menu() is None:
            a.trigger()
            process()
    assert win._toolpath_menu.actions() and all(a.text() for a in win._toolpath_menu.actions())
    for a in win._toolpath_menu.actions():
        a.trigger()
        process()
    assert robot.problems() == []


def test_the_program_has_an_icon():
    from iyicam.gui.theme import app_icon
    assert not app_icon().isNull() and not app_icon(64).pixmap(64, 64).isNull()

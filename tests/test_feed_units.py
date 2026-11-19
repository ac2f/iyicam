"""Feed rates can be shown and typed per second: the toolpath keeps mm/min (what the G-code writes)."""
import pytest

pytest.importorskip("PySide6.QtWidgets")

from iyicam.core.params import Param  # noqa: E402
from iyicam.gui.settings import Units  # noqa: E402


def test_units_convert_feed_rates_per_second():
    u = Units("mm", "s")
    assert u.feed_to_display(3000) == pytest.approx(50.0) and u.feed_from_display(50.0) == pytest.approx(3000)
    assert u.feed_suffix == " mm/s" and u.fmt_feed(4800) == "80 mm/s"
    i = Units("inch", "min")
    assert i.feed_suffix == " in/min" and i.fmt_feed(254) == "10 in/min"


def test_a_form_shows_mm_per_second_and_gives_back_mm_per_minute(rwin):
    from iyicam.gui.forms import ParamForm
    f = ParamForm([Param("feed_rate", "Feed rate", "feed", 4800.0, min=1.0), Param("d", "D", "length", 6.0)], None,
                  Units("mm", "s"))
    w = f.widgets["feed_rate"]
    assert w.value() == pytest.approx(80.0) and w.suffix() == " mm/s"
    w.setValue(50.0)
    v = f.values()
    assert v["feed_rate"] == pytest.approx(3000.0) and v["d"] == pytest.approx(6.0)


def test_the_preferences_switch_the_feed_units(rwin):
    from gui_robot import process
    from iyicam.gui.dialogs import PreferencesDialog
    win, _ = rwin
    d = PreferencesDialog(win.ctrl, win)
    box = d.form.widgets["feed_units"]
    box.setCurrentIndex(box.findData("s"))
    d.accept()
    process()
    assert win.ctrl.settings.feed_units == "s" and win.ctrl.units.feed == "s"


@pytest.fixture
def rwin(tmp_path, monkeypatch):
    from gui_robot import rwin_gen
    yield from rwin_gen(tmp_path, monkeypatch)

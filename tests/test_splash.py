"""The start-up window: shown at once, says what is being done, closed when the main window is up."""
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from iyicam.gui import app as A  # noqa: E402
from iyicam.gui import splash as S  # noqa: E402


def test_the_start_up_window_follows_the_start_and_closes():
    seen = []
    orig = S.Splash.step

    def step(self, text):
        seen.append(text)
        orig(self, text)

    S.Splash.step = step
    try:
        _app, win = A.create_window(["iyicam"], load_plugins=False, splash=True)
    finally:
        S.Splash.step = orig
    s = win.splash
    assert s is not None and s.isVisible()
    assert seen[0] == "Loading the interface..." and seen[-1] == "Opening the main window..."
    assert s.done == len(seen)
    win.show()
    s.finish(win)
    assert not s.isVisible()
    win.close()


def test_no_start_up_window_unless_asked():
    _app, win = A.create_window(["iyicam"], load_plugins=False)
    assert win.splash is None
    win.close()

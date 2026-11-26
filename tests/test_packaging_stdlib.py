"""The package takes the whole standard library (a plugin imports what it needs: "No module named 'logging.handlers'"
in the packaged program), without the tests, the IDE and Tk."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "packaging"))

import stdlib_modules  # noqa: E402


def test_what_plugins_import_is_in_and_the_dead_weight_is_out():
    mods = set(stdlib_modules.modules())
    for m in ("logging.handlers", "http.server", "socketserver", "csv", "urllib.request", "email.mime.text",
              "concurrent.futures", "json", "sqlite3"):
        assert m in mods, m
    assert not any(m.split(".")[0] in ("tkinter", "test", "idlelib", "turtledemo") for m in mods)
    assert not any(".test." in m or m.endswith((".test", ".tests")) for m in mods)

"""The self test of the program and the files that package it."""
import ast
import os
import struct
import subprocess
import sys
from pathlib import Path

import pytest

from iyicam import selftest

ROOT = Path(__file__).resolve().parents[1]
PACK = ROOT / "packaging"


def test_the_self_test_passes_and_prints_every_check(capsys):
    assert selftest.run(gui=True) == 0
    out = capsys.readouterr().out
    for name in ("libraries", "built-in features", "project file", "toolpaths and every post-processor",
                 "DXF export and import", "SVG import", "nesting", "window, tabs and themes"):
        assert f"ok    {name}" in out
    assert "Everything works." in out and "FAIL" not in out


def test_the_self_test_without_the_window(capsys):
    assert selftest.run(gui=False) == 0
    assert "window" not in capsys.readouterr().out


def test_a_failing_check_is_reported_and_the_others_still_run(capsys, monkeypatch):
    def broken(self):
        raise RuntimeError("the post-processors are missing")
    monkeypatch.setattr(selftest.SelfTest, "registry", broken)
    assert selftest.run(gui=False) == 1
    cap = capsys.readouterr()
    assert "FAIL  built-in features" in cap.out and "the post-processors are missing" in cap.out
    assert "ok    nesting" in cap.out and "1 of 7 checks failed" in cap.out


def test_the_self_test_leaves_the_settings_of_the_user_alone(tmp_path, monkeypatch):
    home = tmp_path / "mine"
    monkeypatch.setenv("IYICAM_HOME", str(home))
    assert selftest.run(gui=True) == 0
    assert os.environ["IYICAM_HOME"] == str(home) and not home.exists()


def test_the_cli_runs_it(capsys):
    from iyicam import cli
    assert cli.main(["selftest", "--no-gui"]) == 0
    assert "Everything works." in capsys.readouterr().out


def test_the_packaging_files_are_there_and_sound():
    for name in ("iyicam.spec", "build.py", "launcher.py", "make_icons.py", "iyicam.iss", "build_windows.bat",
                 "build_unix.sh", "iyicam.ico", "iyicam.png"):
        assert (PACK / name).is_file(), name
    for name in ("iyicam.spec", "build.py", "launcher.py", "make_icons.py"):
        ast.parse((PACK / name).read_text(encoding="utf-8"))
    spec = (PACK / "iyicam.spec").read_text(encoding="utf-8")
    assert "iyicam-cli" in spec and "collect_data_files(\"iyicam\")" in spec     # the .toml posts go along
    assert (ROOT / ".github" / "workflows" / "build.yml").is_file()
    assert "iyicam.ico" in (PACK / "iyicam.iss").read_text(encoding="utf-8")


def test_the_icon_files_are_a_real_icon():
    ico = (PACK / "iyicam.ico").read_bytes()
    reserved, kind, n = struct.unpack("<HHH", ico[:6])
    assert (reserved, kind) == (0, 1) and n >= 6
    for i in range(n):
        size, offset = struct.unpack("<II", ico[6 + 16 * i + 8:6 + 16 * i + 16])
        assert ico[offset:offset + 8] == b"\x89PNG\r\n\x1a\n" and size > 100
    assert (PACK / "iyicam.png").read_bytes()[:8] == b"\x89PNG\r\n\x1a\n"


def test_the_build_script_explains_itself_and_makes_the_version_resource(tmp_path):
    out = subprocess.run([sys.executable, str(PACK / "build.py"), "--help"], capture_output=True, text=True, timeout=60)
    assert out.returncode == 0 and "--installer" in out.stdout and "32-bit" not in out.stderr
    sys.path.insert(0, str(PACK))
    try:
        import build
    finally:
        sys.path.remove(str(PACK))
    f = build.version_file("1.2.3")
    text = f.read_text(encoding="utf-8")
    assert "filevers=(1, 2, 3, 0)" in text and "'1.2.3'" in text
    assert text.startswith("VSVersionInfo(") and "StringStruct('ProductName', 'iyiCAM')" in text


@pytest.mark.skipif(struct.calcsize("P") != 8, reason="64-bit Python only")
def test_the_build_script_checks_the_libraries_it_needs():
    sys.path.insert(0, str(PACK))
    try:
        import build
        build.check_python()           # (PyInstaller is installed here, or it says so and exits)
    except SystemExit as exc:
        assert exc.code == 1
    finally:
        sys.path.remove(str(PACK))


def test_an_error_nothing_caught_is_logged_and_shown(tmp_path, monkeypatch):
    from iyicam.gui import app
    monkeypatch.setenv("IYICAM_HOME", str(tmp_path / "home"))
    shown = []
    old = sys.excepthook
    try:
        app.install_crash_log(lambda message, log: shown.append((message, str(log))))
        try:
            raise ZeroDivisionError("division by zero")
        except ZeroDivisionError:
            sys.excepthook(*sys.exc_info())
        sys.excepthook(KeyboardInterrupt, KeyboardInterrupt(), None)       # (not an error)
    finally:
        sys.excepthook = old
    assert shown == [("ZeroDivisionError: division by zero", str(tmp_path / "home" / "error.log"))]
    text = (tmp_path / "home" / "error.log").read_text(encoding="utf-8")
    assert "ZeroDivisionError" in text and "test_an_error_nothing_caught" in text

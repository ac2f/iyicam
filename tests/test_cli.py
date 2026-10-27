"""The command line: every command works, and every mistake is an error message, not a traceback."""
import json

import ezdxf
import pytest
from conftest import add_tp, end_mill, make_project

from iyicam import cli
from iyicam.core import shapes


@pytest.fixture
def job(tmp_path):
    p, s, objs = make_project([[shapes.rectangle(10, 10, 100, 60), shapes.circle((60, 40), 15)],
                               [shapes.circle((200, 60), 30)], [shapes.rectangle(250, 20, 80, 40)]], size=(500, 300))
    add_tp(p, "profile", end_mill(6), {"through": True}, objs)
    path = tmp_path / "job.icam"
    p.save(path)
    return path


def run(capsys, *argv):
    code = cli.main(list(argv))
    out = capsys.readouterr()
    return code, out.out, out.err


def test_posts_plugins_and_info(capsys, job):
    code, out, _ = run(capsys, "posts")
    assert code == 0 and "mach3" in out and "ncstudio" in out
    assert run(capsys, "plugins")[0] == 0
    code, out, err = run(capsys, "info", str(job))
    assert code == 0 and "3 vectors" in out and "1 toolpath" in out


@pytest.mark.parametrize("post", ["mach3", "mach4", "fanuc", "linuxcnc", "ncstudio", "syntec"])
def test_gcode_for_every_post(capsys, job, tmp_path, post):
    out = tmp_path / f"out_{post}"
    code, text, err = run(capsys, "gcode", str(job), "--post", post, "-o", str(out))
    assert code == 0, err
    written = text.split()
    assert written and all(open(w, encoding="ascii").read().strip() for w in written)


def test_import_dxf_and_svg_then_gcode_and_nest(capsys, tmp_path):
    doc = ezdxf.new(units=4)   # millimetres
    doc.modelspace().add_circle((50, 50), 20)
    doc.modelspace().add_lwpolyline([(100, 0), (160, 0), (160, 40), (100, 40)], close=True)
    dxf = tmp_path / "a.dxf"
    doc.saveas(dxf)
    out = tmp_path / "a.icam"
    code, text, err = run(capsys, "import", str(dxf), "-o", str(out))
    assert code == 0 and "2 vectors" in text, err
    nested = tmp_path / "n.icam"
    code, text, err = run(capsys, "nest", str(out), "-o", str(nested), "--iterations", "3", "--fill-holes",
                          "--group-inner", "--width", "600", "--height", "400")
    assert code == 0 and "placed 2, unplaced 0" in text, err
    svg = tmp_path / "b.svg"
    svg.write_text('<svg xmlns="http://www.w3.org/2000/svg" width="100mm" height="50mm" viewBox="0 0 100 50">'
                   '<rect x="10" y="5" width="30" height="20"/><text x="1" y="1">hi</text></svg>')
    code, text, err = run(capsys, "import", str(svg), "-o", str(tmp_path / "b.icam"))
    assert code == 0 and "1 vectors" in text and "TEXT" in err      # the text was left out, and it says so


@pytest.mark.parametrize("argv,message", [
    (["info", "nothere.icam"], "was not found"),
    (["gcode", "nothere.icam"], "was not found"),
    (["import", "nothere.dxf", "-o", "x.icam"], "was not found"),
    (["nest", "nothere.icam", "-o", "x.icam"], "was not found"),
])
def test_missing_files_are_reported(capsys, tmp_path, monkeypatch, argv, message):
    monkeypatch.chdir(tmp_path)
    code, out, err = run(capsys, *argv)
    assert code != 0 and message in err and "Traceback" not in err


def test_damaged_files_are_reported(capsys, tmp_path):
    bad = tmp_path / "bad.icam"
    bad.write_text("{this is not json")
    for argv in (["info", str(bad)], ["gcode", str(bad)], ["nest", str(bad), "-o", str(tmp_path / "o.icam")]):
        code, out, err = run(capsys, *argv)
        assert code != 0 and err.startswith("error:"), (argv, err)
    dxf = tmp_path / "bad.dxf"
    dxf.write_text("0\nSECTION\nnot a drawing")
    code, out, err = run(capsys, "import", str(dxf), "-o", str(tmp_path / "x.icam"))
    assert code != 0 and err.startswith("error:")
    odd = tmp_path / "odd.icam"
    odd.write_text(json.dumps({"format": "x", "sheets": "nope"}))
    code, out, err = run(capsys, "info", str(odd))
    assert code != 0 and "error:" in err


def test_bad_arguments_are_reported(capsys, job, tmp_path):
    assert run(capsys, "gcode", str(job), "--post", "nope")[0] == 2
    assert run(capsys, "gcode", str(job), "--sheet", "7")[0] == 2
    assert run(capsys, "gcode", str(job), "--sheet", "zero")[0] == 2
    code, out, err = run(capsys, "nest", str(job), "-o", str(tmp_path / "o.icam"), "--angles", "a,b")
    assert code == 2 and "--angles" in err
    code, out, err = run(capsys, "nest", str(job), "-o", str(tmp_path / "o.icam"), "--rotate-layer", "Nope=none")
    assert code == 2 and "--rotate-layer" in err
    code, out, err = run(capsys, "nest", str(job), "-o", str(tmp_path / "o.icam"), "--leftover", "wide")
    assert code != 0 and "error" in err


def test_a_project_with_nothing_to_do(capsys, tmp_path):
    p, s, _ = make_project([])
    path = tmp_path / "empty.icam"
    p.save(path)
    assert run(capsys, "info", str(path))[0] == 0
    code, out, err = run(capsys, "gcode", str(path), "-o", str(tmp_path / "e.nc"))
    assert code == 1                    # nothing was written: not a success, and not a crash
    code, out, err = run(capsys, "nest", str(path), "-o", str(tmp_path / "n.icam"))
    assert code in (0, 1) and "Traceback" not in err


def test_help_lists_the_commands_without_opening_a_window(capsys, monkeypatch):
    from iyicam import __main__ as entry

    def no_window(*_a, **_k):
        raise AssertionError("the window must not open for --help")
    monkeypatch.setattr("iyicam.gui.app.main", no_window)
    with pytest.raises(SystemExit) as exit_:
        entry.main(["--help"])
    assert exit_.value.code == 0
    out = capsys.readouterr().out
    assert "nest" in out and "gcode" in out

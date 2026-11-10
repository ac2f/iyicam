"""CorelDRAW files: read through a converter program.

No CorelDRAW (and no converter) is needed here: the converter is a small script of ours (``IYICAM_CDR_CONVERTER``) or a
fake ``inkscape`` on the PATH. A real drawing is tried when ``IYICAM_CDR_SAMPLE`` names one (and a converter is installed).
"""
import base64
import os
import stat
import struct
import sys
import textwrap
import time
import zipfile

import pytest

from iyicam.builtins import ensure_registered
from iyicam.core.document import Project
from iyicam.core.registry import REGISTRY
from iyicam.formats import cdr
from iyicam.formats.cdr import CdrError, Converter, import_cdr, sniff
from iyicam.formats.report import ImportReport, run_importer

SVG_100x50 = """<svg xmlns="http://www.w3.org/2000/svg" width="100mm" height="50mm" viewBox="0 0 100 50">
<rect x="10" y="10" width="30" height="20"/><path d="M50 10 L90 10 L90 40 Z"/></svg>"""

FAKE = textwrap.dedent('''
    import os, sys, time
    mode = os.environ.get("FAKE_MODE", "ok")
    src, out = sys.argv[1], sys.argv[2]
    if mode == "sleep":
        time.sleep(60)
    if mode == "fail":
        print("boom: cannot read the file", file=sys.stderr); sys.exit(3)
    if mode == "empty":
        open(out, "w").write('<svg xmlns="http://www.w3.org/2000/svg" width="10mm" height="10mm"/>'); sys.exit(0)
    if mode == "garbage":
        open(out, "w").write("this is not svg"); sys.exit(0)
    open(os.environ.get("FAKE_LOG", os.devnull), "a").write(os.path.basename(src) + "\\n")
    open(out, "w").write(open(os.environ["FAKE_SVG"]).read())
''')


def riff_cdr(version_char=b"D", size=64) -> bytes:
    return b"RIFF" + struct.pack("<I", size) + b"CDR" + version_char + b"\0" * size


@pytest.fixture
def cdr_file(tmp_path):
    f = tmp_path / "drawing.cdr"
    f.write_bytes(riff_cdr())
    return f


@pytest.fixture
def fake(tmp_path, monkeypatch):
    """A converter of our own is all there is (nothing on the PATH)."""
    script = tmp_path / "fake_conv.py"
    script.write_text(FAKE)
    svg = tmp_path / "fake.svg"
    svg.write_text(SVG_100x50)
    empty_bin = tmp_path / "emptybin"
    empty_bin.mkdir()
    monkeypatch.setenv("PATH", str(empty_bin))
    monkeypatch.setenv("IYICAM_CDR_CONVERTER", f'"{sys.executable}" "{script}" {{input}} {{output}}')
    monkeypatch.setenv("FAKE_SVG", str(svg))
    monkeypatch.delenv("FAKE_MODE", raising=False)
    monkeypatch.delenv("IYICAM_CDR_TIMEOUT", raising=False)
    return script


def read(path, **kw):
    p = Project("t")
    rep = ImportReport()
    objs = import_cdr(path, p, p.sheets[0], report=rep, **kw)
    return p, objs, rep


# ---------------------------------------------------------------------------------------------- the file

def test_registered_like_the_other_importers():
    ensure_registered(REGISTRY)
    imp = REGISTRY.importer_for("cdr")
    assert imp is not None and imp.func is import_cdr
    assert REGISTRY.importer_for(".CDR") is imp


@pytest.mark.parametrize("char, version, name", [(b"D", 13, "X3"), (b"7", 7, "7"), (b"A", 10, "10"),
                                                 (b"F", 15, "X5"), (b"I", 18, "X8")])
def test_sniff_riff_versions(tmp_path, char, version, name):
    f = tmp_path / "a.cdr"
    f.write_bytes(riff_cdr(char))
    assert sniff(f) == {"container": "riff", "version": version}
    assert cdr.version_name(version) == name


def test_sniff_zip_packaged_drawing(tmp_path):
    f = tmp_path / "x4.cdr"
    with zipfile.ZipFile(f, "w") as z:
        z.writestr("content/riffData.cdr", riff_cdr(b"E"))
        z.writestr("metadata/metadata.xml", "<x/>")
    assert sniff(f) == {"container": "zip", "version": 14}
    g = tmp_path / "x8.cdr"
    with zipfile.ZipFile(g, "w") as z:
        z.writestr("root.dat", b"\0" * 20)
    assert sniff(g) == {"container": "zip", "version": None}


def test_sniff_says_what_it_is_instead(tmp_path):
    def code(name, data):
        f = tmp_path / name
        f.write_bytes(data)
        with pytest.raises(CdrError) as e:
            sniff(f)
        return e.value

    assert code("empty.cdr", b"").code == "cdr_not_cdr"
    assert code("text.cdr", b"hello world, this is plain text\n").code == "cdr_not_cdr"
    assert code("cmx.cdr", b"RIFF" + struct.pack("<I", 40) + b"CMX1" + b"\0" * 40).code == "cdr_not_cdr"
    assert "damaged" in str(code("bad.cdr", b"PK\x03\x04" + b"\0" * 40))
    f = tmp_path / "whatsapp.cdr.zip"
    with zipfile.ZipFile(f, "w") as z:
        z.writestr("logo.cdr", riff_cdr())
    with pytest.raises(CdrError) as e:
        sniff(f)
    assert e.value.code == "cdr_archive" and e.value.params["inner"] == "logo.cdr"
    z2 = tmp_path / "other.zip"
    with zipfile.ZipFile(z2, "w") as z:
        z.writestr("readme.txt", "x")
    with pytest.raises(CdrError) as e2:
        sniff(z2)
    assert e2.value.code == "cdr_not_cdr"
    with pytest.raises(CdrError) as e3:
        sniff(tmp_path / "missing.cdr")
    assert e3.value.code == "cdr_unreadable"


# ---------------------------------------------------------------------------------------------- cleaning up what a converter wrote

XHTML = """<?xml version="1.0" encoding="UTF-8"?>
<html xmlns="http://www.w3.org/1999/xhtml" xmlns:svg="http://www.w3.org/2000/svg">
<head><title>x</title></head><body>
<svg:svg version="1.1" width="100mm" height="50mm" viewBox="0 0 100 50">
<svg:rect x="10" y="10" width="30" height="20"/><svg:path d="M50 10 L90 10 L90 40 Z"/></svg:svg>
</body></html>"""


def test_unwrap_xhtml_gives_plain_svg_that_imports(tmp_path):
    from iyicam.formats.svg import import_svg
    out = cdr.unwrap_xhtml(XHTML.encode())
    assert "<svg" in out and "svg:" not in out and "<html" not in out
    f = tmp_path / "p.svg"
    f.write_text(out, encoding="utf-8")
    p = Project("t")
    objs = import_svg(f, p, p.sheets[0])
    assert len(objs) == 2
    bb = p.sheets[0].bbox()
    assert (bb.width, bb.height) == pytest.approx((80.0, 30.0))


def test_unwrap_xhtml_survives_a_page_that_is_not_well_formed():
    out = cdr.unwrap_xhtml(b"<html><body><svg:svg width='1mm' height='1mm'><svg:path d='M0 0 L1 1'/></svg:svg><br></body></html>")
    assert out.count("<path") == 1 and "svg:" not in out
    with pytest.raises(cdr._Failed):
        cdr.unwrap_xhtml(b"<html><body>nothing</body></html>")


def test_libreoffice_duplicates_and_bounding_boxes_are_cleaned_away(tmp_path):
    from iyicam.formats.svg import import_svg
    lo = """<svg xmlns="http://www.w3.org/2000/svg" width="100mm" height="50mm" viewBox="0 0 100 50">
    <g class="Slide"><g class="com.sun.star.drawing.CustomShape"><g id="id1">
      <rect class="BoundingBox" stroke="none" fill="none" x="10" y="10" width="30" height="20"/>
      <path fill="rgb(0,0,0)" stroke="none" d="M 10 10 L 40 10 L 40 30 L 10 30 Z"/>
      <path fill="none" stroke="rgb(0,0,0)" d="M 10 10 L 40 10 L 40 30 L 10 30 Z"/>
      <path fill="none" stroke="rgb(0,0,0)" d="M  10 10   L 40 10 L 40 30 L 10 30 Z"/>
    </g></g>
    <g id="id2"><path stroke="rgb(0,0,0)" d="M 50 10 L 90 10"/></g></g></svg>"""
    out = cdr.clean_libreoffice_svg(lo)
    assert out.count("<path") == 2 and "BoundingBox" not in out
    f = tmp_path / "lo.svg"
    f.write_text(out, encoding="utf-8")
    p = Project("t")
    assert len(import_svg(f, p, p.sheets[0])) == 2
    with pytest.raises(cdr._Failed):
        cdr.clean_libreoffice_svg("not xml at all")


# ---------------------------------------------------------------------------------------------- the whole import (a converter of our own)

def test_import_through_a_custom_converter(fake, cdr_file):
    p, objs, rep = read(cdr_file)
    assert len(objs) == 2 and len(p.sheets[0].vectors) == 2
    bb = p.sheets[0].bbox()
    assert (bb.xmin, bb.ymin, bb.width, bb.height) == pytest.approx((10.0, 10.0, 80.0, 30.0))   # the sheet is in mm, y up
    assert rep.via == "CorelDRAW X3 (custom)" and rep.read["RECT"] == 1 and rep.read["PATH"] == 1
    assert rep.clean


def test_through_run_importer_and_scale_and_layer(fake, cdr_file):
    p = Project("t")
    layer = p.add_layer("Cut")
    rep = ImportReport()
    objs = run_importer(REGISTRY.importer_for("cdr").func, str(cdr_file), p, p.sheets[0], rep, layer_id=layer.id, scale=0.5)
    assert {o.layer_id for o in objs} == {layer.id}
    bb = p.sheets[0].bbox()
    assert (bb.width, bb.height) == pytest.approx((40.0, 15.0))
    assert rep.via


def test_the_file_name_does_not_matter_to_the_converter(fake, tmp_path, monkeypatch):
    """A path with spaces and Turkish letters is copied to a plain name first (no converter has to cope with it)."""
    log = tmp_path / "log.txt"
    monkeypatch.setenv("FAKE_LOG", str(log))
    f = tmp_path / "Çizim dosyası (1).cdr"
    f.write_bytes(riff_cdr())
    p, objs, _ = read(f)
    assert len(objs) == 2
    assert log.read_text().strip() == "drawing.cdr"


def test_nothing_is_left_behind_and_the_original_is_untouched(fake, cdr_file, tmp_path):
    import tempfile
    before = set(os.listdir(tempfile.gettempdir()))
    data = cdr_file.read_bytes()
    read(cdr_file)
    assert cdr_file.read_bytes() == data
    assert not [n for n in set(os.listdir(tempfile.gettempdir())) - before if n.startswith("iyicam-cdr-")]


def test_no_converter_says_what_to_install(tmp_path, monkeypatch, cdr_file):
    empty = tmp_path / "emptybin"
    empty.mkdir()
    monkeypatch.setenv("PATH", str(empty))
    monkeypatch.delenv("IYICAM_CDR_CONVERTER", raising=False)
    for var in ("ProgramFiles", "ProgramFiles(x86)", "LOCALAPPDATA"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setattr(cdr.os.path, "isfile", lambda p: False)
    assert cdr.find_converters() == []
    with pytest.raises(CdrError) as e:
        read(cdr_file)
    assert e.value.code == "cdr_no_converter"
    assert "Inkscape" in str(e.value) and "DXF" in str(e.value)
    d = cdr.describe()
    assert d["available"] is False and d["converters"] == [] and "Inkscape" in d["install"]


def test_a_named_converter_that_is_not_there(fake, cdr_file):
    with pytest.raises(CdrError) as e:
        read(cdr_file, converter="libreoffice")
    assert e.value.code == "cdr_no_converter" and "libreoffice" in str(e.value)
    assert len(read(cdr_file, converter="custom")[1]) == 2


@pytest.mark.parametrize("mode, word", [("fail", "boom"), ("empty", "empty"), ("garbage", "could not be read")])
def test_a_converter_that_fails_is_reported_with_its_own_words(fake, cdr_file, monkeypatch, mode, word):
    monkeypatch.setenv("FAKE_MODE", mode)
    with pytest.raises(CdrError) as e:
        read(cdr_file)
    assert e.value.code == "cdr_failed" and word in str(e.value)
    assert e.value.params["reasons"] and e.value.params["reasons"][0].startswith("custom:")


def test_the_next_converter_is_tried_when_one_fails(fake, cdr_file, monkeypatch):
    good = cdr.find_converters()[0]
    monkeypatch.setattr(cdr, "find_converters", lambda: [Converter("custom", "false {input}"), good])
    p, objs, rep = read(cdr_file)
    assert len(objs) == 2 and rep.via.endswith("(custom)")


def test_a_newer_file_is_said_to_be_newer(fake, tmp_path, monkeypatch):
    f = tmp_path / "new.cdr"
    f.write_bytes(riff_cdr(b"S"))          # 'S' = 28
    monkeypatch.setenv("FAKE_MODE", "fail")
    with pytest.raises(CdrError) as e:
        read(f)
    assert e.value.code == "cdr_version" and "Save As" in str(e.value) and e.value.params["version"] == 28


def test_a_file_that_is_not_cdr_never_reaches_a_converter(fake, tmp_path, monkeypatch):
    f = tmp_path / "x.cdr"
    f.write_bytes(b"%PDF-1.4 definitely not a drawing")
    monkeypatch.setenv("FAKE_LOG", str(tmp_path / "log.txt"))
    with pytest.raises(CdrError) as e:
        read(f)
    assert e.value.code == "cdr_not_cdr" and not (tmp_path / "log.txt").exists()


def test_a_slow_converter_is_stopped(fake, cdr_file, monkeypatch):
    monkeypatch.setenv("FAKE_MODE", "sleep")
    t0 = time.monotonic()
    with pytest.raises(CdrError) as e:
        read(cdr_file, timeout=1.5)
    assert e.value.code == "cdr_timeout" and time.monotonic() - t0 < 10


def test_the_time_a_caller_may_ask_for_is_bounded(fake, cdr_file, monkeypatch):
    seen = []
    real = cdr._Budget

    class Spy(real):
        def __init__(self, seconds, cancel=None):
            seen.append(seconds)
            super().__init__(seconds, cancel)
    monkeypatch.setattr(cdr, "_Budget", Spy)
    read(cdr_file, timeout=10 ** 9)
    read(cdr_file, timeout=-5)
    read(cdr_file, timeout="soon")
    assert seen == [cdr.MAX_TIMEOUT, 1.0, cdr.TIMEOUT]


def test_cancel_stops_the_converter(fake, cdr_file, monkeypatch):
    monkeypatch.setenv("FAKE_MODE", "sleep")
    t0 = time.monotonic()
    with pytest.raises(CdrError) as e:
        read(cdr_file, cancel=lambda: time.monotonic() - t0 > 0.4)
    assert e.value.code == "cdr_cancelled" and time.monotonic() - t0 < 10


@pytest.mark.skipif(sys.platform == "win32", reason="needs a shell script on the PATH")
def test_a_stopped_converter_leaves_no_child_behind(fake, cdr_file, tmp_path, monkeypatch):
    pidfile = tmp_path / "child.pid"
    script = tmp_path / "spawner.py"
    script.write_text("import os, subprocess, sys, time\n"
                      f"p = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'])\n"
                      f"open({str(pidfile)!r}, 'w').write(str(p.pid)); time.sleep(60)\n")
    monkeypatch.setenv("IYICAM_CDR_CONVERTER", f'"{sys.executable}" "{script}" {{input}} {{output}}')
    with pytest.raises(CdrError):
        read(cdr_file, timeout=1.5)
    pid = int(pidfile.read_text())
    for _ in range(50):
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            break
        time.sleep(0.1)
    else:
        pytest.fail("the grandchild of the converter is still running")


# ---------------------------------------------------------------------------------------------- Inkscape (a fake one on the PATH)

FAKE_INKSCAPE = textwrap.dedent('''\
    #!{python}
    import os, sys
    args = sys.argv[1:]
    if "--version" in args:
        print("Inkscape 1.2.2 (fake)"); sys.exit(0)
    open(os.environ["FAKE_ARGS"], "a").write(" ".join(args) + "\\n")
    out = [a.split("=", 1)[1] for a in args if a.startswith("--export-filename=")][0]
    if os.environ.get("FAKE_MODE") == "no-actions" and any(a.startswith("--actions") for a in args):
        sys.exit(1)               # (an Inkscape that does not know the action: nothing is written)
    open(out, "w").write(open(os.environ["FAKE_SVG"]).read())
''')


@pytest.fixture
def fake_inkscape(tmp_path, monkeypatch):
    if sys.platform == "win32":
        pytest.skip("shell-script fake")
    binp = tmp_path / "bin"
    binp.mkdir()
    exe = binp / "inkscape"
    exe.write_text(FAKE_INKSCAPE.format(python=sys.executable))
    exe.chmod(exe.stat().st_mode | stat.S_IEXEC)
    svg = tmp_path / "fake.svg"
    svg.write_text(SVG_100x50)
    monkeypatch.setenv("PATH", str(binp))
    monkeypatch.delenv("IYICAM_CDR_CONVERTER", raising=False)
    monkeypatch.delenv("FAKE_MODE", raising=False)
    monkeypatch.setenv("FAKE_SVG", str(svg))
    monkeypatch.setenv("FAKE_ARGS", str(tmp_path / "args.txt"))
    cdr._version_cache.clear()
    return tmp_path / "args.txt"


def test_inkscape_turns_text_into_curves_by_default(fake_inkscape, cdr_file):
    found = cdr.find_converters()
    assert [(c.name, c.version) for c in found] == [("inkscape", "1.2.2")]
    p, objs, rep = read(cdr_file)
    assert len(objs) == 2 and rep.via == "CorelDRAW X3 (inkscape)"
    args = fake_inkscape.read_text().splitlines()
    assert len(args) == 1 and "--actions=select-all:all;object-to-path" in args[0]
    assert "--export-plain-svg" in args[0] and "--export-type=svg" in args[0]
    assert args[0].rstrip().endswith("drawing.cdr")


def test_inkscape_without_text_to_curves_and_with_a_fallback(fake_inkscape, cdr_file, monkeypatch):
    read(cdr_file, text_to_curves=False)
    assert "--actions" not in fake_inkscape.read_text()
    fake_inkscape.unlink()
    monkeypatch.setenv("FAKE_MODE", "no-actions")        # the first way fails, the plain one is tried
    assert len(read(cdr_file)[1]) == 2
    lines = fake_inkscape.read_text().splitlines()
    assert len(lines) == 2 and "--actions" in lines[0] and "--actions" not in lines[1]


def test_inkscape_does_not_touch_the_persons_settings(fake_inkscape, cdr_file, monkeypatch, tmp_path):
    seen = tmp_path / "env.txt"
    exe = tmp_path / "bin" / "inkscape"
    exe.write_text(exe.read_text().replace("open(os.environ[\"FAKE_ARGS\"]",
                                           f"open({str(seen)!r}, 'w').write(os.environ.get('INKSCAPE_PROFILE_DIR', ''))\n"
                                           "open(os.environ[\"FAKE_ARGS\"]"))
    read(cdr_file)
    assert seen.read_text().endswith("inkscape-profile")


def test_an_old_inkscape_is_not_used(tmp_path, monkeypatch):
    if sys.platform == "win32":
        pytest.skip("shell-script fake")
    binp = tmp_path / "bin"
    binp.mkdir()
    exe = binp / "inkscape"
    exe.write_text(f"#!{sys.executable}\nprint('Inkscape 0.92.5 (old)')\n")
    exe.chmod(exe.stat().st_mode | stat.S_IEXEC)
    monkeypatch.setenv("PATH", str(binp))
    monkeypatch.delenv("IYICAM_CDR_CONVERTER", raising=False)
    cdr._version_cache.clear()
    assert [c.name for c in cdr.find_converters() if c.name == "inkscape"] == []


# ---------------------------------------------------------------------------------------------- the service and the command line

def _payload(path, **extra):
    return {"name": "drawing.cdr", "data_b64": base64.b64encode(open(path, "rb").read()).decode(), **extra}


def test_service_import_reads_a_cdr_file(fake, cdr_file):
    from iyicam.service.ops import op_import
    out = op_import(_payload(cdr_file, options={"scale": 0.5, "converter": "custom"}))
    assert out["report"]["via"] == "CorelDRAW X3 (custom)" and len(out["added"]) == 2
    s = out["project"]["sheets"][0]
    assert s["width"] >= 55 and len(s["vectors"]) == 2


def test_service_import_keeps_the_codes_of_the_errors(fake, cdr_file, tmp_path, monkeypatch):
    from iyicam.service.errors import ServiceError
    from iyicam.service.ops import op_import
    monkeypatch.setenv("FAKE_MODE", "fail")
    with pytest.raises(ServiceError) as e:
        op_import(_payload(cdr_file))
    assert e.value.code == "cdr_failed" and "boom" in e.value.message and "CdrError" not in e.value.message
    bad = tmp_path / "not.cdr"
    bad.write_bytes(b"x" * 100)
    with pytest.raises(ServiceError) as e2:
        op_import(_payload(bad))
    assert e2.value.code == "cdr_not_cdr" and e2.value.params["name"] == "drawing.cdr"


def test_service_import_a_remote_caller_cannot_run_a_program_of_its_own(fake, cdr_file, tmp_path):
    """``converter`` only picks among the converters this computer has; there is no way to give a command."""
    from iyicam.service.errors import ServiceError
    from iyicam.service.ops import op_import
    marker = tmp_path / "pwned"
    with pytest.raises(ServiceError) as e:
        op_import(_payload(cdr_file, options={"converter": f"touch {marker}"}))
    assert e.value.code == "cdr_no_converter" and not marker.exists()


def test_schema_tells_whether_corel_files_can_be_read(fake):
    from iyicam.service.ops import op_schema
    sc = op_schema()
    assert sc["cdr"]["available"] is True and sc["cdr"]["converters"][0]["name"] == "custom"
    assert any("cdr" in i["extensions"] for i in sc["importers"])


def test_command_line_import(fake, cdr_file, tmp_path, capsys):
    from iyicam import cli
    out = tmp_path / "job.icam"
    assert cli.main(["import", str(cdr_file), "-o", str(out)]) == 0
    assert Project.load(out).sheets[0].vectors
    assert "read as CorelDRAW X3 (custom)" in capsys.readouterr().err


def test_command_line_import_failure_is_one_line_not_a_traceback(fake, cdr_file, tmp_path, monkeypatch, capsys):
    from iyicam import cli
    monkeypatch.setenv("FAKE_MODE", "fail")
    assert cli.main(["import", str(cdr_file), "-o", str(tmp_path / "job.icam")]) == 1
    err = capsys.readouterr().err
    assert err.startswith("error: ") and "Traceback" not in err and not (tmp_path / "job.icam").exists()


# ---------------------------------------------------------------------------------------------- a real drawing (when there is one)

SAMPLE = os.environ.get("IYICAM_CDR_SAMPLE")


@pytest.mark.skipif(not SAMPLE or not os.path.isfile(SAMPLE or ""), reason="IYICAM_CDR_SAMPLE names no file")
@pytest.mark.parametrize("name", ["inkscape", "cdr2xhtml", "libreoffice"])
def test_a_real_drawing(name):
    """The sample the numbers below come from is a 332 x 401 mm drawing saved by CorelDRAW X3: every converter must
    bring it in at that size (the sheet's lower left is where the drawing's is)."""
    if name not in {c.name for c in cdr.find_converters()}:
        pytest.skip(f"{name} is not installed")
    p, objs, rep = read(SAMPLE, converter=name)
    bb = p.sheets[0].bbox()
    assert (bb.width, bb.height) == pytest.approx((332.4, 401.2), abs=0.5)
    assert len(objs) > 100 and rep.via.endswith(f"({name})")
    if name == "inkscape":
        assert not rep.skipped                       # text became curves
    else:
        assert rep.skipped.get("TEXT") and any("Text was left out" in n for n in rep.notes)


@pytest.mark.skipif(not SAMPLE or not os.path.isfile(SAMPLE or ""), reason="IYICAM_CDR_SAMPLE names no file")
def test_a_real_drawing_in_a_zip_package(tmp_path):
    """CorelDRAW X4 and later keep the drawing in a ZIP package (content/riffData.cdr). The sample is put in one (a real
    X4+ file is not at hand, so this tests the package handling, not the newer format) and read through every converter."""
    packed = tmp_path / "packed.cdr"
    with zipfile.ZipFile(packed, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("content/riffData.cdr", open(SAMPLE, "rb").read())
    assert sniff(packed) == {"container": "zip", "version": 13}
    found = [c.name for c in cdr.find_converters()]
    if not found:
        pytest.skip("no converter is installed")
    for name in found:
        p, objs, rep = read(packed, converter=name)
        bb = p.sheets[0].bbox()
        assert (bb.width, bb.height) == pytest.approx((332.4, 401.2), abs=0.5), name

"""CorelDRAW files newer than X8 (2017 ... 2026).

No file of such a version is at hand, and nobody has published its format: libcdr has no rules past X8.  What is tested is
what this program does with such a file: it is read with the rules of X8 (the files that ``cdr_builder`` makes, written
with the version numbers of the newer programs, in every kind of package), it is said to be unchecked, a block that cannot
be followed stops the reading instead of leaving a part of the drawing out, a package of an unknown layout or compression
is said to be that, and ``iyicam cdr-info`` tells what a file is made of (nothing of the drawing) for the day a real one
does not open.
"""
import json
import struct
import zipfile
from pathlib import Path

import pytest
from cdr_builder import Builder

from iyicam.core.document import Project
from iyicam.formats import cdr, cdr_native as N
from iyicam.formats.cdr import CdrError, import_cdr, sniff
from iyicam.formats.report import ImportReport

NEWER = [1900, 2000, 2100, 2200, 2300, 2400, 2500, 2600, 2700, 2800, 3500]        # 2017 ... 2026, and 'Z'


def T(tx, ty, s=1.0):
    return (s, 0, tx, 0, s, ty)


def make(tmp_path, version, name="d.cdr", compress=True, **kw):
    b = Builder(version, page=(8.0, 6.0), **kw)
    b.add_page([b.rect(2.0, 1.0, (0, 0, 0, 0), 0, T(1.0, 0.5)), b.ellipse(1.0, 1.0, 0, 0, False, T(-1, -1))])
    f = tmp_path / name
    b.save(f, compress)
    return f


def read(path, **kw):
    p = Project("t")
    rep = ImportReport()
    objs = import_cdr(path, p, p.sheets[0], report=rep, converter="builtin", **kw)
    return p, objs, rep


def outlines(drawing):
    return [[(s[0], *[tuple(round(v, 9) for v in pt) for pt in s[1:]]) for c in sh.contours for s in c.segs]
            for sh in drawing.page.shapes]


# ---------------------------------------------------------------------------------------------- reading

@pytest.mark.parametrize("version", NEWER)
@pytest.mark.parametrize("kw", [{}, {"external": True}], ids=["package", "package with other streams"])
def test_a_file_of_a_newer_version_is_read_with_the_rules_of_x8_and_said_to_be_newer(tmp_path, version, kw):
    d = N.read(make(tmp_path, version, **kw))
    x8 = N.read(make(tmp_path, 1800, "x8.cdr", **kw))
    assert d.newer and d.unverified and d.version == version
    assert len(d.page.shapes) == 2 and outlines(d) == outlines(x8)            # (the same drawing, the same outlines)
    assert not N.read(make(tmp_path, 1800, "x8b.cdr")).newer


@pytest.mark.parametrize("compress", [True, False])
def test_the_import_says_what_it_is_and_asks_to_check_it(tmp_path, compress):
    f = make(tmp_path, 2800, compress=compress)
    p, objs, rep = read(f)
    assert len(objs) == 2 and rep.via == "CorelDRAW 2026 (builtin)"
    note = " ".join(rep.notes)
    assert "newer than CorelDRAW X8" in note and "CHECK THE SIZE" in note and "X8" in note
    _, _, x8 = read(make(tmp_path, 1800, "x8.cdr", compress=compress))
    assert not any("newer than" in n for n in x8.notes)


def test_the_size_of_the_drawing_is_the_same_as_the_one_of_x8(tmp_path):
    new, _, _ = read(make(tmp_path, 2800))
    old, _, _ = read(make(tmp_path, 1800, "x8.cdr"))
    a, b = new.sheets[0].bbox(), old.sheets[0].bbox()
    assert (a.xmin, a.ymin, a.xmax, a.ymax) == (b.xmin, b.ymin, b.xmax, b.ymax) and a.xmax - a.xmin > 1.0


def test_a_newer_file_is_a_package_that_can_be_named_differently(tmp_path):
    """A newer CorelDRAW may call the stream something else: any member of ``content`` that is a RIFF drawing is read."""
    src = make(tmp_path, 2800, "src.cdr")
    with zipfile.ZipFile(src) as z:
        riff = z.read("content/riffData.cdr")
    f = tmp_path / "renamed.cdr"
    with zipfile.ZipFile(f, "w") as z:
        z.writestr("content/drawing.bin", riff)
        z.writestr("metadata/metadata.xml", "<x/>")
    assert sniff(f) == {"container": "zip", "version": 28}
    assert len(N.read(f).page.shapes) == 2
    # ... but an archive that merely holds a .cdr is still an archive
    g = tmp_path / "archive.zip"
    with zipfile.ZipFile(g, "w") as z:
        z.writestr("logo.cdr", riff)
    with pytest.raises(CdrError) as e:
        sniff(g)
    assert e.value.code == "cdr_archive"


def test_the_version_of_a_package_with_other_streams_is_known(tmp_path):
    f = make(tmp_path, 2800, external=True, compress=False)
    with zipfile.ZipFile(f) as z:
        assert "content/root.dat" in z.namelist()
    assert sniff(f) == {"container": "zip", "version": 28}              # (it was None before: root.dat was not looked at)


# ---------------------------------------------------------------------------------------------- never a part of a drawing

def _damage_compressed(path: Path) -> None:
    """Break the zlib stream of the first compressed list of the drawing inside the package."""
    with zipfile.ZipFile(path) as z:
        members = {i.filename: z.read(i.filename) for i in z.infolist()}
    name = "content/riffData.cdr" if "content/riffData.cdr" in members else "content/root.dat"
    data = bytearray(members[name])
    at = data.find(b"CPng")
    assert at > 0, "the builder wrote no compressed list"
    start = at + 8                                                          # (past the header: into the zlib stream)
    data[start:start + 24] = b"\xff" * 24
    members[name] = bytes(data)
    with zipfile.ZipFile(path, "w") as z:
        for n, b in members.items():
            z.writestr(n, b)


def test_a_block_that_cannot_be_inflated_stops_the_reading_of_a_newer_file(tmp_path):
    f = make(tmp_path, 2800, compress=True)
    _damage_compressed(f)
    with pytest.raises(N.Unsupported) as e:
        N.read(f)
    assert "inflated" in str(e.value) or "could not be followed" in str(e.value)
    assert "newer than X8" in str(e.value)
    with pytest.raises(CdrError) as e2:
        read(f)
    assert e2.value.code == "cdr_version" and "cdr-info" in str(e2.value)


def test_a_newer_file_that_is_all_zeros_after_its_header_is_refused_with_the_advice(tmp_path):
    f = tmp_path / "empty28.cdr"
    f.write_bytes(b"RIFF" + struct.pack("<I", 60) + b"CDRS" + b"\0" * 60)
    with pytest.raises(CdrError) as e:
        read(f)
    assert e.value.code == "cdr_version" and e.value.params["version"] == 28 and "Save As" in str(e.value)
    assert "2026" in str(e.value)


def test_a_version_character_no_corel_draw_has_used_is_refused(tmp_path):
    f = tmp_path / "weird.cdr"
    f.write_bytes(b"RIFF" + struct.pack("<I", 60) + b"CDRz" + b"\0" * 60)          # 'z' = 4300 in libcdr's formula
    with pytest.raises(N.Unsupported) as e:
        N.read(f)
    assert "not one any CorelDRAW has used" in str(e.value)


# ---------------------------------------------------------------------------------------------- packages of another layout

def test_a_package_of_a_layout_that_is_not_known_is_said_to_be_that(tmp_path):
    f = tmp_path / "future.cdr"
    with zipfile.ZipFile(f, "w") as z:
        z.writestr("content/document.xml", "<doc/>")
        z.writestr("metadata/metadata.xml", "<x/>")
        z.writestr("previews/preview.png", b"\x89PNG")
    with pytest.raises(CdrError) as e:
        sniff(f)
    assert e.value.code == "cdr_package" and "cdr-info" in str(e.value) and "content/document.xml" in e.value.params["entries"]
    plain = tmp_path / "plain.zip"
    with zipfile.ZipFile(plain, "w") as z:
        z.writestr("content/document.xml", "<doc/>")
    with pytest.raises(CdrError) as e2:
        sniff(plain)                                                        # (not named .cdr: just a ZIP file)
    assert e2.value.code == "cdr_not_cdr"


def _with_method(path: Path, method: int) -> None:
    """Say in the ZIP headers that every member is compressed with ``method`` (what a newer CorelDRAW may use and Python's
    zipfile does not know: zstd is 93)."""
    data = bytearray(path.read_bytes())
    i = 0
    while True:
        i = data.find(b"PK\x03\x04", i)
        if i < 0:
            break
        struct.pack_into("<H", data, i + 8, method)
        i += 4
    i = 0
    while True:
        i = data.find(b"PK\x01\x02", i)
        if i < 0:
            break
        struct.pack_into("<H", data, i + 10, method)
        i += 4
    path.write_bytes(bytes(data))


def test_a_compression_that_python_cannot_read_is_said_with_its_name(tmp_path):
    f = make(tmp_path, 2800)
    _with_method(f, 93)
    with pytest.raises(N.Unsupported) as e:
        N.read(f)
    assert "zstd" in str(e.value) and "cannot read" in str(e.value)
    with pytest.raises(CdrError) as e2:
        read(f)
    assert e2.value.code in ("cdr_failed", "cdr_version") and "zstd" in str(e2.value)
    info = N.diagnose(f)
    assert info["container"] == "zip" and info["entries"][0]["compression"] == "zstd" and not info["read"]["ok"]


# ---------------------------------------------------------------------------------------------- iyicam cdr-info

def test_diagnose_tells_what_a_file_is_made_of_and_nothing_of_the_drawing(tmp_path):
    f = make(tmp_path, 2800, external=True, compress=False)
    d = N.diagnose(f)
    assert d["container"] == "zip" and d["version"] == 2800 and d["version_name"] == "2026" and d["newer_than_x8"]
    assert d["form"] == "CDRS" and d["main"] == "content/root.dat" and d["external_streams"] >= 1
    assert d["scan"]["ok"] and d["scan"]["lists"]["page"] >= 1 and d["scan"]["lists"]["obj "] >= 2
    assert d["scan"]["chunks_in_other_streams"] > 0 and 2800 in d["scan"]["file_versions"]
    assert d["read"]["ok"] and d["read"]["shapes"] == 2 and d["read"]["newer"] and d["read"]["pages"] == 1
    text = json.dumps(d)
    assert str(tmp_path) not in text and "d.cdr" not in text                 # (no path, no name of the file)
    names = {e["name"] for e in d["entries"]}
    assert "content/root.dat" in names


def test_diagnose_says_where_it_stops(tmp_path):
    f = make(tmp_path, 2800, compress=True)
    _damage_compressed(f)
    d = N.diagnose(f)
    assert not d["read"]["ok"] and d["scan"]["problem"] and "error" in d["read"]
    nothing = tmp_path / "x.cdr"
    nothing.write_bytes(b"not a corel file at all, just some words")
    d2 = N.diagnose(nothing)
    assert d2["read"]["ok"] is False and d2["container"] is None
    missing = N.diagnose(tmp_path / "missing.cdr")
    assert missing["read"]["ok"] is False and "cannot be opened" in missing["read"]["error"]


def test_the_command_prints_it(tmp_path, capsys):
    from iyicam.cli import main
    f = make(tmp_path, 2800)
    assert main(["cdr-info", str(f)]) == 0
    out = capsys.readouterr().out
    assert "RIFF form" in out and "NEWER than X8" in out and "read        2 shapes" in out and "content/riffData.cdr" in out
    assert main(["cdr-info", "--json", str(f)]) == 0
    data = json.loads(capsys.readouterr().out)
    assert data["read"]["ok"] and data["version"] == 2800 and data["sniff"]["container"] == "zip" and "iyicam" in data
    bad = tmp_path / "bad.cdr"
    bad.write_bytes(b"hello hello hello hello hello")
    assert main(["cdr-info", str(bad)]) == 1
    captured = capsys.readouterr()
    assert "read        NO" in captured.out and "error:" in captured.err


def test_the_command_is_known_to_the_launcher():
    from iyicam.cli import COMMANDS
    assert "cdr-info" in COMMANDS


def test_the_importers_say_newer_files_are_tried():
    assert cdr.describe()["newer_tried"] is True and cdr.describe()["reads"] == "CorelDRAW 5 to X8"

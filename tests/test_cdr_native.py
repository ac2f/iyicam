"""The CorelDRAW reader built into iyiCAM (iyicam/formats/cdr_native.py, a port of libcdr's geometry).

Three kinds of checks:

* numbers worked out by hand on files made by ``cdr_builder`` (nothing else is needed);
* a *differential* test: libcdr's own ``cdr2xhtml`` reads the same files and must give the same outlines, for every
  object kind and every version from 5 to X8 (skipped when the libcdr tools are not installed);
* damaged files: whatever happens to a file, the answer is a drawing or ``Unsupported``, never a crash or a hang.
"""
import os
import random
import shutil
import struct
import subprocess
import sys
import time
import zipfile
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
from cdr_builder import Builder, lst, rotation  # noqa: E402

from iyicam.core.document import Project  # noqa: E402
from iyicam.core.geom import Arc, Bezier, Line, contours_bbox  # noqa: E402
from iyicam.formats import cdr, cdr_native as N  # noqa: E402
from iyicam.formats.report import ImportReport  # noqa: E402

IN = 25.4
VERSIONS = [500, 600, 700, 800, 900, 1000, 1100, 1200, 1300, 1400, 1500, 1600, 1700, 1800]


def T(tx, ty, s=1.0):
    return (s, 0, tx, 0, s, ty)


def make(tmp_path, build, version=1300, name="d.cdr", compress=True, **kw):
    b = Builder(version, page=kw.pop("page", (8.0, 6.0)), **kw)
    build(b)
    f = tmp_path / name
    b.save(f, compress)
    return f


def read(path, **kw):
    p = Project("t")
    rep = ImportReport()
    objs = cdr.import_cdr(path, p, p.sheets[0], report=rep, converter="builtin", **kw)
    return p, objs, rep


# ------------------------------------------------------------------------------------------------ worked out by hand

def test_the_numbers_of_a_version():
    assert [N.cdr_version(c) for c in b"3579ADGI"] == [300, 500, 700, 900, 1000, 1300, 1600, 1800]
    assert N.cdr_version(0x20) == 300 and N.cdr_version(0x2F) == 0 and N.cdr_version(0x3B) == 0
    assert [N.version_name(v) for v in (500, 1000, 1300, 1800)] == ["5", "10", "X3", "X8"]


@pytest.mark.parametrize("version", VERSIONS)
def test_a_rectangle_is_where_it_was_put(tmp_path, version):
    """A 2 x 1 inch rectangle moved by (1, 0.5) on an 8 x 6 inch page whose middle is the origin: 5..7 in across,
    3.5..4.5 in up from the lower left corner of the page."""
    f = make(tmp_path, lambda b: b.add_page([b.rect(2.0, 1.0, (0, 0, 0, 0), 0, T(1.0, 0.5))]), version)
    d = N.read(f)
    assert len(d.pages) == 1 and (d.page.width, d.page.height) == pytest.approx((8 * IN, 6 * IN))
    p, objs, rep = read(f)
    bb = contours_bbox(objs[0].contours)
    assert (bb.xmin, bb.ymin, bb.xmax, bb.ymax) == pytest.approx((5 * IN, 3.5 * IN, 7 * IN, 4.5 * IN), abs=0.01)
    c = objs[0].contours[0]
    assert c.closed and len(c.segs) == 4 and all(isinstance(sg, Line) for sg in c.segs)
    assert c.length() == pytest.approx(6 * IN, abs=0.01)
    assert rep.via == f"CorelDRAW {N.version_name(version)} (builtin)" and rep.read["SHAPE"] == 1


def test_a_circle_stays_a_circle_and_an_ellipse_becomes_curves(tmp_path):
    def build(b):
        b.add_page([b.ellipse(1.0, 1.0, 0, 0, False, T(1.0, 1.0)), b.ellipse(2.0, 1.0, 0, 0, False, T(-2.0, -1.0))])
    p, objs, _ = read(make(tmp_path, build))
    circle = next(o for o in objs if o.contours[0].segs and isinstance(o.contours[0].segs[0], Arc))
    ellipse = next(o for o in objs if o is not circle)
    arcs = circle.contours[0].segs
    assert len(arcs) == 2 and all(isinstance(a, Arc) for a in arcs) and arcs[0].radius == pytest.approx(0.5 * IN, abs=1e-6)
    assert circle.contours[0].closed and circle.contours[0].length() == pytest.approx(IN * 3.141592653589793, abs=1e-4)
    segs = ellipse.contours[0].segs
    assert all(isinstance(sg, Bezier) for sg in segs) and len(segs) >= 8
    bb = contours_bbox(ellipse.contours)
    assert (bb.width, bb.height) == pytest.approx((2 * IN, 1 * IN), abs=1e-3)


def test_rotation_mirror_and_skew_are_applied_in_the_order_of_the_file(tmp_path):
    def build(b):
        b.add_page([b.curve([(0, 0), (1, 0)], [0x00, 0x40], rotation(90)),               # a line turned to point up
                    b.curve([(0, 0), (1, 0)], [0x00, 0x40], (-1, 0, 0, 0, 1, 0), T(0, 1.0)),   # mirrored, then lifted
                    b.curve([(0, 0), (1, 0)], [0x00, 0x40], T(0.5, 0.0), (2, 0, 0, 0, 1, 0))])  # moved, then stretched
    p, objs, _ = read(make(tmp_path, build))
    cx, cy = 4 * IN, 3 * IN                                      # the page's origin, in mm from its corner
    lines = [o.contours[0].segs[0] for o in objs][::-1]          # (written back to front)
    for sg, (a, b) in zip(lines, [((0, 0), (0, 1)), ((0, 1), (-1, 1)), ((1, 0), (3, 0))]):
        got = sorted([sg.a, sg.b])
        want = sorted([(cx + a[0] * IN, cy + a[1] * IN), (cx + b[0] * IN, cy + b[1] * IN)])
        assert [v for pt in got for v in pt] == pytest.approx([v for pt in want for v in pt], abs=1e-6)


def test_open_and_closed_shapes_are_told_apart(tmp_path):
    def build(b):
        b.add_page([b.curve([(0, 0), (1, 0), (1, 1)], [0x00, 0x40, 0x40]),                 # open: two lines
                    b.curve([(0, 0), (1, 0), (1, 1)], [0x00, 0x40, 0x48]),                 # closed by the flag
                    b.curve([(0, 0), (1, 0), (1, 1), (0, 0)], [0x00, 0x40, 0x40, 0x40])])  # closed by coming back
    _, objs, _ = read(make(tmp_path, build))
    assert [o.contours[0].closed for o in objs][::-1] == [False, True, True]
    assert [len(o.contours[0].segs) for o in objs][::-1] == [2, 3, 3]


def test_curves_polygons_and_splines(tmp_path):
    def build(b):
        b.add_page([b.curve([(0, 0), (0.3, 0.8), (0.7, 0.8), (1, 0)], [0x00, 0xC0, 0xC0, 0x80]),
                    b.polygon([(1, 0), (0.3, 0.2)], [0x00, 0x40], 5, 2, 1.0, 1.0, 0.0, 0.0),
                    b.polygon([(1, 0), (0.5, 0.86)], [0x00, 0x40], 6, 2, 0.8, 0.8, 1.0, 1.0),
                    b.spline([(0, 0), (0.4, 1), (1, 1), (1.5, 0), (2, 0.8), (2.5, 0)], [0, 0, 0, 0, 0, 1])])
    _, objs, _ = read(make(tmp_path, build))
    curve, star, hexa, spline = objs[::-1]
    assert len(curve.contours[0].segs) == 1 and isinstance(curve.contours[0].segs[0], Bezier)
    # (a polygon is its one corner turned round the centre, as libcdr does it: five copies, each closed)
    assert len(star.contours) == 5 and all(c.closed and len(c.segs) == 2 for c in star.contours)
    assert len(hexa.contours) >= 2                                                                         # two triangles
    assert all(isinstance(sg, Bezier) for sg in spline.contours[0].segs) and len(spline.contours[0].segs) >= 3


def test_what_is_not_on_the_page_is_not_drawn(tmp_path):
    """A pattern list (vect) and the objects before a page is started are not shapes of the page; text and bitmaps are
    not outlines and are counted, not forgotten."""
    def build(b):
        b.add_page([b.vect(b.rect(5.0, 5.0, (0, 0, 0, 0), 0, T(0, 0))), b.rect(1.0, 1.0, (0, 0, 0, 0), 0, T(0, 0)),
                    b.text(0.1, 0.1), b.text(0.2, 0.2), b.bitmap()])
    _, objs, rep = read(make(tmp_path, build))
    assert len(objs) == 1
    assert rep.skipped == {"TEXT": 2, "IMAGE": 1} and any("Text was left out" in n for n in rep.notes)
    assert any("Bitmaps" in n for n in rep.notes)


def test_a_drawing_of_text_only_says_how_to_make_curves(tmp_path):
    f = make(tmp_path, lambda b: b.add_page([b.text(0, 0), b.text(1, 1)]))
    with pytest.raises(cdr.CdrError) as e:
        read(f)
    assert e.value.code == "cdr_text_only" and e.value.params["texts"] == 2 and "Convert To Curves" in str(e.value)


def test_master_page_pages_and_groups(tmp_path):
    """libcdr draws what is on the master page with the first real page (so does this one, and says so); of several
    pages the first is read, and says so; groups and layers are only containers."""
    def build(b):
        b.add_page([b.rect(1.0, 1.0, (0, 0, 0, 0), 0, T(0, 0))], master=True)
        b.add_page([lst("grp ", b.rect(2.0, 1.0, (0, 0, 0, 0), 0, T(1, 1)), b.ellipse(1.0, 1.0, 0, 0, False, T(3, 3))),
                    lst("layr", b.curve([(0, 0), (1, 1), (2, 0)], [0x00, 0x40, 0x40], T(-2, -2)))])
        b.add_page([b.rect(3.0, 3.0, (0, 0, 0, 0), 0, T(0, 0))])
    f = make(tmp_path, build)
    d = N.read(f)
    assert [len(p.shapes) for p in d.pages] == [4, 1] and d.page.master == 1
    _, objs, rep = read(f)
    assert len(objs) == 4
    assert any("master page" in n for n in rep.notes) and any("2 pages" in n and "first" in n for n in rep.notes)


@pytest.mark.parametrize("version", [700, 1300, 1500])
def test_compressed_or_not_the_same_drawing(tmp_path, version):
    def build(b):
        b.add_page([b.rect(2.0, 1.0, (0, 0, 0, 0), 0, T(1.0, 0.5)), b.curve([(0, 0), (1, 1)], [0x00, 0x40], T(0, 0))])
    a = N.read(make(tmp_path, build, version, "a.cdr", compress=True))
    b = N.read(make(tmp_path, build, version, "b.cdr", compress=False))
    flat = lambda d: [[(s[0], *[tuple(round(v, 9) for v in pt) for pt in s[1:]]) for c in sh.contours for s in c.segs] for sh in d.page.shapes]  # noqa: E731
    assert flat(a) == flat(b) and len(a.page.shapes) == 2


def test_x6_keeps_chunks_in_other_streams_of_the_package(tmp_path):
    def build(b):
        b.add_page([b.rect(2.0, 1.0, (0, 0, 0, 0), 0, T(1.0, 0.5)), b.ellipse(1.0, 1.0, 0, 0, False, T(-1, -1))])
    f = make(tmp_path, build, 1600, external=True)
    with zipfile.ZipFile(f) as z:
        assert "content/root.dat" in z.namelist() and "content/data/a.dat" in z.namelist() and "content/riffData.cdr" not in z.namelist()
    d = N.read(f)
    assert len(d.page.shapes) == 2
    assert cdr.sniff(f)["container"] == "zip"


def test_a_file_of_a_version_not_compared_with_a_real_one_is_marked(tmp_path):
    build = lambda b: b.add_page([b.rect(1.0, 1.0, (0, 0, 0, 0), 0, T(0, 0))])      # noqa: E731
    assert N.read(make(tmp_path, build, 1300, "x3.cdr")).unverified is False
    d = N.read(make(tmp_path, build, 1500, "x5.cdr"))
    assert d.unverified is True
    _, _, rep = read(make(tmp_path, build, 1500, "x5b.cdr"))
    assert any("compared with files of CorelDRAW X3 only" in n for n in rep.notes)
    _, _, rep3 = read(make(tmp_path, build, 1300, "x3b.cdr"))
    assert not any("compared with" in n for n in rep3.notes)


def test_the_order_of_the_readers(tmp_path, monkeypatch):
    """The person's own program first; then the built-in reader for what it was compared on, but the programs that are
    libcdr itself first for the rest (and the built-in one last)."""
    prog = [cdr.Converter("cdr2xhtml", "/x/cdr2xhtml"), cdr.Converter("libreoffice", "/x/soffice")]
    monkeypatch.setattr(cdr, "find_converters", lambda: [cdr.Converter("custom", "mine {input} {output}"), *prog])
    names = lambda v, c=None: [x.name for x in cdr._plan(v, c)]    # noqa: E731
    assert names(1300) == ["custom", "builtin", "cdr2xhtml", "libreoffice"]
    assert names(1500) == ["custom", "cdr2xhtml", "libreoffice", "builtin"]
    assert names(0) == ["custom", "cdr2xhtml", "libreoffice", "builtin"]
    assert names(1300, "builtin") == ["builtin"] and names(1300, "nothing") == []


def test_a_version_the_reader_does_not_know(tmp_path):
    f = tmp_path / "old.cdr"
    f.write_bytes(b"RIFF" + struct.pack("<I", 60) + b"CDR4" + b"\0" * 60)
    with pytest.raises(N.Unsupported) as e:
        N.read(f)
    assert "older" in str(e.value)
    g = tmp_path / "new.cdr"
    g.write_bytes(b"RIFF" + struct.pack("<I", 60) + b"CDRZ" + b"\0" * 60)
    with pytest.raises(N.Unsupported) as e2:
        N.read(g)
    assert "newer" in str(e2.value)
    with pytest.raises(cdr.CdrError) as e3:
        read(g)
    assert e3.value.code == "cdr_version" and "X8" in str(e3.value)


def test_scale_is_about_the_corner_of_the_page(tmp_path):
    f = make(tmp_path, lambda b: b.add_page([b.rect(2.0, 1.0, (0, 0, 0, 0), 0, T(1.0, 0.5))]))
    _, a, _ = read(f)
    _, b, _ = read(f, scale=0.5)
    ba, bb = contours_bbox(a[0].contours), contours_bbox(b[0].contours)
    assert (bb.xmin, bb.ymin, bb.xmax, bb.ymax) == pytest.approx((ba.xmin / 2, ba.ymin / 2, ba.xmax / 2, ba.ymax / 2), abs=1e-9)


# ------------------------------------------------------------------------------------------------ libcdr as the judge

LIBCDR = shutil.which("cdr2xhtml")
needs_libcdr = pytest.mark.skipif(LIBCDR is None, reason="the libcdr tools (cdr2xhtml) are not installed")


def libcdr_shapes(path):
    """What libcdr makes of a file, through the SVG importer: one (contours, closed flags, length, box) per shape."""
    from iyicam.formats.svg import import_svg
    out = subprocess.run([LIBCDR, str(path)], capture_output=True, timeout=60)
    assert out.returncode == 0 and out.stdout.strip(), out.stderr.decode()[-300:]
    svg = Path(str(path) + ".svg")
    svg.write_text(cdr.unwrap_xhtml(out.stdout), encoding="utf-8")
    p = Project("o")
    return [summary(o.contours) for o in import_svg(svg, p, p.sheets[0], keep_layers=False)]


def summary(cs):
    bb = contours_bbox(cs)
    return (len(cs), sum(1 for c in cs if c.closed), sum(c.length() for c in cs), bb.xmin, bb.ymin, bb.xmax, bb.ymax)


def everything(b):
    """Every object kind, with transforms of every kind, on one page."""
    objs = [b.rect(2.0, 1.0, (0, 0, 0, 0), 0, T(1.0, 0.5)),
            b.rect(2.0, 1.2, (0.3, 0.1, 0.2, 0.05) if b.v >= 900 else (0.3, 0, 0, 0), 0, T(-3.0, 0.5))]
    if b.v >= 1500:
        objs += [b.rect(1.5, 1.0, (0.25, 0.25, 0.25, 0.25), corner, T(-3.0 + 2.2 * k, -2.0)) for k, corner in enumerate((1, 2))]
    objs += [b.ellipse(2.0, 1.0, 0, 0, False, rotation(30, 0, 0, (1.0, 1.0))),
             b.ellipse(1.0, 1.0, 0, 0, False, T(2.5, 2.0)),
             b.ellipse(1.5, 1.0, 30, 200, False, T(-2.0, 2.0)),
             b.ellipse(1.5, 1.5, 45, 300, True, T(-0.5, 2.2)),
             b.curve([(0, 0), (1, 0), (1, 1), (0, 1)], [0x00, 0x40, 0x40, 0x48], T(2.5, -2.0)),
             b.curve([(0, 0), (0.3, 0.8), (0.7, 0.8), (1, 0)], [0x00, 0xC0, 0xC0, 0x80], T(3.0, 0.0)),
             b.path([(0, 0), (1, 0), (1, 1), (0, 1), (0, 0)], [0x00, 0x40, 0x40, 0x40, 0x48], (1, 0, -1.0, 0, 1, -1.0)),
             b.curve([(0, 0), (1, 0), (1, 1)], [0x00, 0x40, 0x40], (-1, 0, 0.5, 0, 1, 1.0)),
             b.curve([(0, 0), (0.5, 0), (1, 0.5), (1, 1)], [0x00, 0xC0, 0xC0, 0x80], (0.5, 0.2, 2.0, -0.3, 0.7, 0.1)),
             b.polygon([(1, 0), (0.3, 0.2)], [0x00, 0x40], 5, 2, 1.0, 1.0, -2.8, -1.2),
             b.polygon([(1, 0), (0.5, 0.86)], [0x00, 0x40], 6, 2, 0.8, 0.8, 0.2, -2.2),
             b.spline([(0, 0), (0.4, 1), (1, 1), (1.5, 0), (2, 0.8), (2.5, 0)], [0, 0, 0, 0, 0, 1], T(-2.0, -1.2)),
             b.text(0.1, 0.1, T(0, 0)), b.bitmap(T(0, 0)),
             b.curve([(0, 0), (1, 1)], [0x00, 0x40], rotation(15))]
    b.add_page(objs)


def assert_same(path):
    want = libcdr_shapes(path)
    from iyicam.formats.cdr import _native_objects
    p = Project("n")
    got = [summary(o.contours) for o in _native_objects(N.read(path), p, p.sheets[0], None, 1.0)]
    assert len(want) == len(got) and got
    for i, (a, b) in enumerate(zip(want, got)):
        assert a[:2] == b[:2], f"shape {i}: contours {a[:2]} against {b[:2]}"
        assert max(abs(a[k] - b[k]) for k in range(2, 7)) < 0.005, f"shape {i}: {a} against {b}"


@needs_libcdr
@pytest.mark.parametrize("version", VERSIONS)
@pytest.mark.parametrize("compress", [True, False])
def test_same_outlines_as_libcdr_for_every_object_and_version(tmp_path, version, compress):
    if compress is False and version < 700:
        pytest.skip("the drawing is not compressed before version 7 anyway")
    assert_same(make(tmp_path, everything, version, compress=compress))


@needs_libcdr
@pytest.mark.parametrize("version", [1600, 1700, 1800])
def test_same_outlines_as_libcdr_with_the_streams_of_x6_and_later(tmp_path, version):
    assert_same(make(tmp_path, everything, version, external=True))


@needs_libcdr
@pytest.mark.parametrize("version", [700, 1300, 1500])
def test_same_pages_groups_and_patterns_as_libcdr(tmp_path, version):
    def build(b):
        b.add_page([b.rect(1.0, 1.0, (0, 0, 0, 0), 0, T(0, 0))], master=True)
        b.add_page([lst("grp ", b.rect(2.0, 1.0, (0, 0, 0, 0), 0, T(1, 1)), b.ellipse(1.0, 1.0, 0, 0, False, T(3, 3))),
                    lst("layr", b.curve([(0, 0), (1, 1), (2, 0)], [0x00, 0x40, 0x40], T(-2, -2))),
                    b.vect(b.rect(5.0, 5.0, (0, 0, 0, 0), 0, T(0, 0))), b.rect(0.5, 0.5, (0, 0, 0, 0), 0, T(-1, 2))])
        b.add_page([b.rect(3.0, 3.0, (0, 0, 0, 0), 0, T(0, 0))])
    assert_same(make(tmp_path, build, version))


SAMPLE = os.environ.get("IYICAM_CDR_SAMPLE")


@needs_libcdr
@pytest.mark.skipif(not SAMPLE or not os.path.isfile(SAMPLE or ""), reason="IYICAM_CDR_SAMPLE names no file")
def test_the_real_drawing_is_the_same_as_libcdr_makes_of_it():
    """218 shapes of a drawing saved by CorelDRAW X3: the same contours, the same ends, within micrometres."""
    want = libcdr_shapes(SAMPLE)
    from iyicam.formats.cdr import _native_objects
    p = Project("n")
    d = N.read(SAMPLE)
    got = [summary(o.contours) for o in _native_objects(d, p, p.sheets[0], None, 1.0)]
    assert len(want) == len(got) == 218 and not d.unverified
    assert max(max(abs(a[k] - b[k]) for k in range(2, 7)) for a, b in zip(want, got)) < 0.005
    assert [a[:2] for a in want] == [b[:2] for b in got]
    assert d.skipped["TEXT"] == 56 and (d.page.width, d.page.height) == pytest.approx((420.0, 437.5), abs=0.01)


# ------------------------------------------------------------------------------------------------ damaged files

def test_damaged_files_are_a_drawing_or_unsupported_never_a_crash(tmp_path):
    rnd = random.Random(11)
    seeds = []
    for v in (500, 900, 1300, 1500):
        seeds.append(make(tmp_path, everything, v, f"s{v}.cdr", compress=False).read_bytes())
    seeds.append(make(tmp_path, everything, 1300, "c.cdr", compress=True).read_bytes())
    seeds.append(make(tmp_path, everything, 1600, "x.cdr", external=True).read_bytes())
    target = tmp_path / "fuzz.cdr"
    seen = {"drawing": 0, "unsupported": 0}
    worst = 0.0
    for _ in range(300):
        data = bytearray(rnd.choice(seeds))
        how = rnd.choice(["flip", "flip", "trunc", "zero", "insert", "dup"])
        if how == "flip":
            for _ in range(rnd.randint(1, 12)):
                data[rnd.randrange(len(data))] = rnd.randrange(256)
        elif how == "trunc":
            del data[rnd.randrange(12, len(data)):]
        elif how == "zero":
            k = rnd.randrange(len(data))
            data[k:k + rnd.randint(1, 64)] = bytes(rnd.randint(1, 64))
        elif how == "insert":
            k = rnd.randrange(len(data))
            data[k:k] = os.urandom(rnd.randint(1, 40))
        else:
            k = rnd.randrange(len(data))
            data[k:k] = data[k:k + rnd.randint(4, 200)]
        target.write_bytes(bytes(data))
        t0 = time.time()
        try:
            d = N.read(target)
            assert d.pages and all(p.width == p.width for p in d.pages)
            seen["drawing"] += 1
        except N.Unsupported:
            seen["unsupported"] += 1
        worst = max(worst, time.time() - t0)
    assert seen["drawing"] and seen["unsupported"] and worst < 5


def test_a_bomb_of_a_polygon_or_of_compressed_data_is_refused_quickly(tmp_path):
    def build(b):
        b.add_page([b.polygon([(1, 0), (0.3, 0.2)], [0x00, 0x40], 4_000_000_000, 2, 1.0, 1.0, 0, 0)])
    t0 = time.time()
    with pytest.raises(N.Unsupported) as e:
        N.read(make(tmp_path, build))
    assert "absurd" in str(e.value) and time.time() - t0 < 5
    bomb = tmp_path / "bomb.cdr"
    import zlib
    body = zlib.compress(b"\0" * (N.MAX_INFLATED + 1024 * 1024), 9)                      # (only ~260 KB)
    payload = (b"cmpr" + struct.pack("<I", len(body)) + struct.pack("<III", 0, 0, 0) + b"CPng" + struct.pack("<HH", 1, 4)
               + body + zlib.compress(b""))
    inner = b"LIST" + struct.pack("<I", len(payload)) + payload
    data = b"CDR" + b"D" + inner
    bomb.write_bytes(b"RIFF" + struct.pack("<I", len(data)) + data)
    with pytest.raises(N.Unsupported) as e2:
        N.read(bomb)
    assert "too big" in str(e2.value)


def test_a_file_that_makes_no_sense_in_numbers_is_refused(tmp_path):
    def build(b):
        b.add_page([b.rect(2.0, 1.0, (0, 0, 0, 0), 0, (1e30, 0, 0, 0, 1, 0))])
    with pytest.raises(cdr.CdrError) as e:
        read(make(tmp_path, build))
    assert e.value.code == "cdr_failed" and "make no sense" in str(e.value)


def test_it_does_not_depend_on_the_name_of_the_file_or_leave_anything(tmp_path):
    import tempfile
    before = set(os.listdir(tempfile.gettempdir()))
    f = make(tmp_path, everything, 1300, "Çizim dosyası (1).cdr")
    _, objs, _ = read(f)
    assert len(objs) > 10
    assert set(os.listdir(tempfile.gettempdir())) == before


def test_the_built_in_reader_needs_no_other_program(tmp_path, monkeypatch):
    empty = tmp_path / "emptybin"
    empty.mkdir()
    monkeypatch.setenv("PATH", str(empty))
    monkeypatch.delenv("IYICAM_CDR_CONVERTER", raising=False)
    f = make(tmp_path, everything, 1300)
    p = Project("t")
    rep = ImportReport()
    objs = cdr.import_cdr(f, p, p.sheets[0], report=rep)               # (no converter named: the default way)
    assert len(objs) > 10 and rep.via == "CorelDRAW X3 (builtin)"

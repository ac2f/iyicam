# This Source Code Form is a derivative of libcdr (https://wiki.documentfoundation.org/DLP/Libraries/libcdr, copyright
# the libcdr contributors) and is subject to the terms of the Mozilla Public License, v. 2.0. If a copy of the MPL was not
# distributed with this file, You can obtain one at http://mozilla.org/MPL/2.0/.
"""CorelDRAW (``.cdr``) drawings read directly, with no other program.

This is a port of the *geometry* part of libcdr (the Document Liberation Project's reader of CorelDRAW files, MPL 2.0,
https://wiki.documentfoundation.org/DLP/Libraries/libcdr), the library behind the CorelDRAW import of LibreOffice and
many other programs: the container (RIFF, or the ZIP package of CorelDRAW X4 and later), the compressed blocks, the objects with
their curves, rectangles, ellipses, polygons and splines, the transforms and the pages.  Colours, fills, line styles,
fonts and bitmaps are not read: a cutting job needs the outlines.

What it reads: CorelDRAW 5 to X8 (libcdr's range).  How it was checked: against the output of libcdr itself on a real
drawing saved by CorelDRAW X3 (218 shapes, every outline within 3 micrometres), and, for every object kind and every
version from 5 to X8, on files made by ``tests/cdr_builder.py`` that libcdr reads too (rectangles with rounded corners,
ellipses, arcs, pies, curves, paths, polygons, splines, transforms with mirror and skew, master pages, groups, pattern
lists, compressed lists, the ZIP package, the external streams of X6 and later).  Only the X3 file is a real one, so
``Drawing.unverified`` says when a file is of another version.

What it does not read, and says so (``Drawing.skipped``): text (the letters of a text object are not outlines in the
file: the font is needed), bitmaps.  Anything it cannot follow raises ``Unsupported``: the caller then asks a converter
program.

Coordinates in the result are millimetres, y up, the lower left corner of the page being (0, 0).
"""
from __future__ import annotations

import math
import struct
import zipfile
import zlib
from collections import Counter
from dataclasses import dataclass, field

INCH = 25.4
EPS = 1e-6                        # libcdr's CDR_EPSILON (inches): "the same point"
FIRST_VERSION = 500               # CorelDRAW 5
LAST_VERSION = 1800               # CorelDRAW X8 (libcdr reads no newer)
VERIFIED = (1300,)                # versions compared with libcdr's own output on a real file (X3)

VERSION_NAMES = {500: "5", 600: "6", 700: "7", 800: "8", 900: "9", 1000: "10", 1100: "11", 1200: "12", 1300: "X3",
                 1400: "X4", 1500: "X5", 1600: "X6", 1700: "X7", 1800: "X8"}


class Unsupported(Exception):
    """A file this reader does not follow (the reason is the message)."""


class _Short(Exception):
    """A read past the end of the data."""


def version_name(version: int) -> str:
    return VERSION_NAMES.get(version, str(version // 100))


def cdr_version(ch: int) -> int:
    """libcdr's number for the version character of the RIFF form type (``CDR`` + character)."""
    if ch == 0x20:
        return 300
    if ch < 0x31:
        return 0
    if ch < 0x3A:
        return 100 * (ch - 0x30)
    if ch < 0x41:
        return 0
    return 100 * (ch - 0x37)


# ---------------------------------------------------------------------------------------------------------------
# bytes
# ---------------------------------------------------------------------------------------------------------------

_UNPACK_H, _UNPACK_I, _UNPACK_SH, _UNPACK_SI, _UNPACK_D = (struct.Struct(f).unpack_from for f in ("<H", "<I", "<h", "<i", "<d"))


class _R:
    """A stream of bytes with a position (like libcdr's input streams: seeking is clamped, reading past the end throws)."""
    __slots__ = ("d", "p", "n")

    def __init__(self, data: bytes):
        self.d, self.p, self.n = data, 0, len(data)

    def end(self) -> bool:
        return self.p >= self.n

    def seek(self, pos: int) -> None:
        self.p = max(0, min(self.n, pos))

    def skip(self, n: int) -> None:
        self.seek(self.p + n)

    # (these are called millions of times for a big drawing: each one does its own work, without a helper in between)
    def u8(self):
        p = self.p
        if p >= self.n:
            raise _Short()
        self.p = p + 1
        return self.d[p]

    def u16(self):
        p = self.p
        if p + 2 > self.n:
            raise _Short()
        self.p = p + 2
        return _UNPACK_H(self.d, p)[0]

    def u32(self):
        p = self.p
        if p + 4 > self.n:
            raise _Short()
        self.p = p + 4
        return _UNPACK_I(self.d, p)[0]

    def s16(self):
        p = self.p
        if p + 2 > self.n:
            raise _Short()
        self.p = p + 2
        return _UNPACK_SH(self.d, p)[0]

    def s32(self):
        p = self.p
        if p + 4 > self.n:
            raise _Short()
        self.p = p + 4
        return _UNPACK_SI(self.d, p)[0]

    def f64(self):
        p = self.p
        if p + 8 > self.n:
            raise _Short()
        self.p = p + 8
        return _UNPACK_D(self.d, p)[0]

    def coords(self, count: int, bits: int) -> list[int]:
        """``count`` signed numbers of 16 or 32 bits in one go (a path has thousands)."""
        size = count * (bits // 8)
        p = self.p
        if p + size > self.n:
            raise _Short()
        self.p = p + size
        return list(struct.unpack_from(f"<{count}{'h' if bits == 16 else 'i'}", self.d, p))

    def fixed(self) -> float:
        v = self.u32()
        ip = (v >> 16) & 0xFFFF
        if ip >= 0x8000:
            ip -= 0x10000
        return ip + (v & 0xFFFF) / 0xFFFF

    def take(self, n: int) -> bytes:
        if self.p + n > self.n:
            raise _Short()
        b = self.d[self.p:self.p + n]
        self.p += n
        return b


MAX_INFLATED = 256 * 1024 * 1024     # a compressed block may not grow beyond this (a "zip bomb")
MAX_POLYGON = 10000                  # corners of a polygon (the file says how many; a made up number must not hang us)
MAX_ELEMENTS = 2_000_000             # path elements one polygon may grow to


def _inflate(data: bytes) -> bytes:
    try:
        d = zlib.decompressobj()
        out = d.decompress(data, MAX_INFLATED)
    except zlib.error:
        return b""
    if d.unconsumed_tail:
        raise Unsupported("a compressed block of the file is too big")
    return out


# ---------------------------------------------------------------------------------------------------------------
# transforms and paths (libcdr's CDRTransform / CDRPath)
# ---------------------------------------------------------------------------------------------------------------

class _T:
    """x' = v0 x + v1 y + x0 ; y' = v3 x + v4 y + y0"""
    __slots__ = ("v0", "v1", "x0", "v3", "v4", "y0")

    def __init__(self, v0=1.0, v1=0.0, x0=0.0, v3=0.0, v4=1.0, y0=0.0):
        self.v0, self.v1, self.x0, self.v3, self.v4, self.y0 = v0, v1, x0, v3, v4, y0

    def pt(self, x: float, y: float) -> tuple[float, float]:
        return self.v0 * x + self.v1 * y + self.x0, self.v3 * x + self.v4 * y + self.y0

    def then(self, other: "_T") -> "_T":
        """First this, then ``other``."""
        o = other
        return _T(o.v0 * self.v0 + o.v1 * self.v3, o.v0 * self.v1 + o.v1 * self.v4,
                  o.v0 * self.x0 + o.v1 * self.y0 + o.x0,
                  o.v3 * self.v0 + o.v4 * self.v3, o.v3 * self.v1 + o.v4 * self.v4,
                  o.v3 * self.x0 + o.v4 * self.y0 + o.y0)


_IDENT = _T()


class _Path:
    """A list of elements: ('M', x, y) ('L', x, y) ('C', x1, y1, x2, y2, x, y) ('Q', x1, y1, x, y)
    ('S', points) a spline of 3rd degree, ('E', cx, cy, rx, ry, th1, dth, T) an arc of an axis-aligned ellipse
    (angles as libcdr's ellipse object has them: P(th) = (cx + rx cos th, cy - ry sin th)), ('Z',)."""

    def __init__(self):
        self.el: list[tuple] = []
        self.closed = False

    def copy(self) -> "_Path":
        p = _Path()
        p.el = list(self.el)
        p.closed = self.closed
        return p

    def empty(self) -> bool:
        return not self.el

    def move(self, x, y): self.el.append(("M", x, y))
    def line(self, x, y): self.el.append(("L", x, y))
    def cubic(self, x1, y1, x2, y2, x, y): self.el.append(("C", x1, y1, x2, y2, x, y))
    def quad(self, x1, y1, x, y): self.el.append(("Q", x1, y1, x, y))
    def spline(self, pts): self.el.append(("S", list(pts)))

    def arc(self, cx, cy, rx, ry, th1, dth):
        self.el.append(("E", cx, cy, rx, ry, th1, dth, _IDENT))

    def close(self):
        self.el.append(("Z",))
        self.closed = True

    def append(self, other: "_Path") -> None:
        self.el.extend(other.el)               # (like libcdr, the "closed" mark is not taken over)

    def transform(self, t: _T) -> None:
        out = []
        for e in self.el:
            k = e[0]
            if k in ("M", "L"):
                out.append((k, *t.pt(e[1], e[2])))
            elif k == "C":
                out.append((k, *t.pt(e[1], e[2]), *t.pt(e[3], e[4]), *t.pt(e[5], e[6])))
            elif k == "Q":
                out.append((k, *t.pt(e[1], e[2]), *t.pt(e[3], e[4])))
            elif k == "S":
                out.append((k, [t.pt(x, y) for x, y in e[1]]))
            elif k == "E":
                out.append(e[:7] + (e[7].then(t),))
            else:
                out.append(e)
        self.el = out


def _ellipse_point(e: tuple, th: float) -> tuple[float, float]:
    _, cx, cy, rx, ry, _t1, _dt, t = e
    return t.pt(cx + rx * math.cos(th), cy - ry * math.sin(th))


def _ellipse_end(e: tuple) -> tuple[float, float]:
    return _ellipse_point(e, e[5] + e[6])


def _ellipse_beziers(e: tuple) -> list[tuple]:
    """The arc as cubic Béziers of at most 45 degrees (an affine image of a Bézier is exact: the control points are
    made on the axis-aligned ellipse and mapped)."""
    _, cx, cy, rx, ry, th1, dth, t = e
    n = max(1, int(math.ceil(abs(dth) / (math.pi / 4) - 1e-9)))
    step = dth / n
    k = 4.0 / 3.0 * math.tan(step / 4.0)
    out = []
    for i in range(n):
        a = th1 + i * step
        b = a + step
        pa = (cx + rx * math.cos(a), cy - ry * math.sin(a))
        pb = (cx + rx * math.cos(b), cy - ry * math.sin(b))
        da = (-rx * math.sin(a), -ry * math.cos(a))
        db = (-rx * math.sin(b), -ry * math.cos(b))
        c1 = (pa[0] + k * da[0], pa[1] + k * da[1])
        c2 = (pb[0] - k * db[0], pb[1] - k * db[1])
        out.append(("C", *t.pt(*c1), *t.pt(*c2), *t.pt(*pb)))
    return out


def _is_circle(e: tuple) -> bool:
    """An arc of an ellipse that, after the transforms, is still a circle (a similarity of equal radii)."""
    _, cx, cy, rx, ry, th1, dth, t = e
    if abs(rx - ry) > 1e-9 * max(1.0, abs(rx)):
        return False
    a, b, c, d = t.v0, t.v1, t.v3, t.v4
    s2 = a * a + c * c
    return s2 > 0 and abs(b * b + d * d - s2) <= 1e-9 * s2 and abs(a * b + c * d) <= 1e-9 * s2


def _spline_beziers(points: list[tuple[float, float]]) -> list[tuple]:
    """libcdr's decomposition of a uniform B-spline of degree 3 into Béziers (Piegl and Tiller, The NURBS Book:
    DecomposeCurve)."""
    deg = 3
    npts = len(points)

    def knot(i: int) -> int:
        if i < deg:
            return 0
        if i > npts:
            return npts - deg
        return i - deg

    m = npts + deg + 1
    a, b = deg, deg + 1
    Qw = [points[i] for i in range(deg + 1)]
    Next = [(0.0, 0.0)] * (deg + 1)
    out = []
    while b < m:
        i = b
        while b < m and knot(b + 1) == knot(b):
            b += 1
        mult = b - i + 1
        if mult < deg:
            numer = float(knot(b) - knot(a))
            alphas: dict[int, float] = {}
            j = deg
            while j > mult:
                alphas[j - mult - 1] = numer / float(knot(a + j) - knot(a))
                j -= 1
            r = deg - mult
            for j in range(1, r + 1):
                save = r - j
                s = mult + j
                k = deg
                while k >= s:
                    al = alphas[k - s]
                    Qw[k] = (al * Qw[k][0] + (1.0 - al) * Qw[k - 1][0], al * Qw[k][1] + (1.0 - al) * Qw[k - 1][1])
                    k -= 1
                if b < m:
                    Next[save] = Qw[deg]
        out.append(("C", Qw[1][0], Qw[1][1], Qw[2][0], Qw[2][1], Qw[3][0], Qw[3][1]))
        Qw, Next = Next, Qw
        if b < m:
            for i in range(deg - mult, deg + 1):
                Qw[i] = points[b - deg + i]
            a = b
            b += 1
    return out


def _write_out(path: _Path) -> list[tuple]:
    """libcdr's CDRPath::writeOut: the actions with an end point (x, y at the end of each), Z without a double Z."""
    out: list[tuple] = []
    was_z = True
    for e in path.el:
        k = e[0]
        if k == "Z":
            if not was_z:
                out.append(("Z",))
                was_z = True
            continue
        was_z = False
        if k == "S":
            pts = e[1]
            out.append(("M", pts[0][0], pts[0][1]))
            out.extend(_spline_beziers(pts))
        else:
            out.append(e)
    return out


def _end_point(a: tuple) -> tuple[float, float] | None:
    k = a[0]
    if k in ("M", "L"):
        return a[1], a[2]
    if k == "C":
        return a[5], a[6]
    if k == "Q":
        return a[3], a[4]
    if k == "E":
        return _ellipse_end(a)
    return None


def _close(a: float, b: float) -> bool:
    return abs(a) <= EPS


def _flush_actions(actions: list[tuple], is_closed: bool) -> list[tuple]:
    """The part of libcdr's ``_flushCurrentPath`` that tidies a path before it is written: a move to where the pen is is
    dropped, a move that leads nowhere is dropped, a subpath that ends where it began is closed."""
    tmp: list[tuple] = []
    first = True
    was_move = False
    ix = iy = px = py = 0.0
    for a in actions:
        pt = _end_point(a)
        if pt is not None:
            x, y = pt
            ignore_m = False
            if first:
                ix, iy = x, y
                first = False
                was_move = True
            elif a[0] == "M":
                if _close(px - x, 0) and _close(py - y, 0):
                    ignore_m = True
                else:
                    if tmp:
                        if not was_move:
                            if (_close(ix - px, 0) and _close(iy - py, 0)) or is_closed:
                                tmp.append(("Z",))
                        else:
                            tmp.pop()
                if not ignore_m:
                    ix, iy = x, y
                    was_move = True
            else:
                was_move = False
            if not ignore_m:
                tmp.append(a)
                px, py = x, y
        elif a[0] == "Z":
            if tmp and tmp[-1][0] != "Z":
                tmp.append(a)
    if tmp:
        if not was_move:
            if (_close(ix - px, 0) and _close(iy - py, 0)) or is_closed:
                if tmp[-1][0] != "Z":
                    tmp.append(("Z",))
        else:
            tmp.pop()
    return tmp


# ---------------------------------------------------------------------------------------------------------------
# the result
# ---------------------------------------------------------------------------------------------------------------

@dataclass
class Contour:
    """Segments in millimetres: ('L', a, b), ('B', a, c1, c2, b) or ('A', a, mid, b); ``closed`` when it ends where it began."""
    segs: list[tuple]
    closed: bool


@dataclass
class Shape:
    contours: list[Contour]


@dataclass
class Page:
    width: float                  # mm
    height: float
    shapes: list[Shape] = field(default_factory=list)
    skipped: Counter = field(default_factory=Counter)      # "TEXT" / "IMAGE" -> how many objects were left out
    master: int = 0               # shapes that belong to a master page (libcdr draws them on the next page)


@dataclass
class Drawing:
    version: int
    pages: list[Page]
    unverified: bool = False                                # a version never compared with libcdr's output on a real file

    @property
    def page(self) -> Page:
        return self.pages[0]

    @property
    def skipped(self) -> Counter:
        return self.pages[0].skipped


def _contours_of(actions: list[tuple]) -> list[Contour]:
    """Actions (inches) -> contours (millimetres): the way the SVG importer makes them from the SVG libcdr writes."""
    contours: list[Contour] = []
    segs: list[tuple] = []
    cur = None
    start = None

    def p(pt):
        return (pt[0] * INCH, pt[1] * INCH)

    def flush(closed: bool):
        nonlocal segs
        if segs:
            if closed:
                a0, b = segs[-1], None
                last_end = segs[-1][-1]
                first_start = segs[0][1]
                if math.hypot(last_end[0] - first_start[0], last_end[1] - first_start[1]) > 1e-9:
                    segs.append(("L", last_end, first_start))
                else:
                    segs[-1] = segs[-1][:-1] + (first_start,)
            contours.append(Contour(segs, closed))
        segs = []

    for a in actions:
        k = a[0]
        if k == "M":
            flush(False)
            cur = start = p((a[1], a[2]))
        elif k == "Z":
            flush(True)
            cur = start
        elif k == "L":
            n = p((a[1], a[2]))
            if cur is not None and math.hypot(n[0] - cur[0], n[1] - cur[1]) > 1e-9:
                segs.append(("L", cur, n))
            cur = n
        elif k == "C":
            n = p((a[5], a[6]))
            segs.append(("B", cur, p((a[1], a[2])), p((a[3], a[4])), n))
            cur = n
        elif k == "Q":
            q = p((a[1], a[2]))
            n = p((a[3], a[4]))
            segs.append(("B", cur, (cur[0] + 2 / 3 * (q[0] - cur[0]), cur[1] + 2 / 3 * (q[1] - cur[1])),
                         (n[0] + 2 / 3 * (q[0] - n[0]), n[1] + 2 / 3 * (q[1] - n[1])), n))
            cur = n
        elif k == "E":
            end = p(_ellipse_end(a))
            if _is_circle(a):
                mid = p(_ellipse_point(a, a[5] + a[6] / 2.0))
                segs.append(("A", cur, mid, end))
            else:
                for c in _ellipse_beziers(a):
                    n = p((c[5], c[6]))
                    segs.append(("B", cur, p((c[1], c[2])), p((c[3], c[4])), n))
                    cur = n
            cur = end
    flush(False)
    return [c for c in contours if c.segs]


# ---------------------------------------------------------------------------------------------------------------
# the collector (libcdr's CDRStylesCollector for the pages, CDRContentCollector for the objects)
# ---------------------------------------------------------------------------------------------------------------

class _Pages:
    """First pass: the size of every page."""

    def __init__(self):
        self.pages: list[list[float]] = []                 # [width, height, offx, offy] in inches
        self.page = [8.5, 11.0, -4.25, -5.5]

    def collect_page(self, level): self.pages.append(list(self.page))
    def collect_page_size(self, w, h, ox, oy):
        if not self.pages:
            self.page = [w, h, ox, oy]
        else:
            self.pages[-1] = [w, h, ox, oy]

    def __getattr__(self, name):                           # the rest of the collector interface: nothing to do
        if name.startswith("collect_"):
            return lambda *a, **k: None
        raise AttributeError(name)


class _Content:
    """Second pass: the outlines of every object, placed on its page."""

    def __init__(self, pages: list[list[float]]):
        self.sizes = pages
        self.out_pages: list[Page] = []
        self.pending_skip: Counter = Counter()
        self.pending_master = 0
        self.page_i = 0
        self.page = list(pages[0])
        self.started = False
        self.ignore_page = False
        self.is_page_props = False
        self.obj_level = 0
        self.page_level = 0
        self.vect_level = 0
        self.path = _Path()
        self.trafos: list[_T] = []
        self.kind = ""                                     # "text" / "image" for an object that has no outline
        self.polygon = None
        self.in_polygon = False
        self.spline = None
        self.in_spline = False
        self.shapes: list[Shape] = []

    # -- pages
    def _start_page(self):
        if self.ignore_page:
            return
        self.started = True            # (what a master page before it left over is drawn with it: libcdr does so)

    def _end_page(self):
        if not self.started:
            return
        w, h = self.page[0], self.page[1]
        # (CorelDRAW draws the last object on top; the converters write them back to front: so is this)
        self.out_pages.append(Page(w * INCH, h * INCH, list(reversed(self.shapes)), self.pending_skip, self.pending_master))
        self.started = False
        self.shapes = []
        self.pending_skip = Counter()
        self.pending_master = 0

    def collect_page(self, level):
        self.is_page_props = True
        self.ignore_page = False
        self.page_level = level
        self.page = list(self.sizes[min(self.page_i, len(self.sizes) - 1)])
        self.page_i += 1

    def collect_object(self, level):
        if not self.started and not self.vect_level and not self.ignore_page:
            self._start_page()
        self.obj_level = level
        self.trafos = []
        self.kind = ""

    def collect_group(self, level):
        if not self.started and not self.vect_level and not self.ignore_page:
            self._start_page()

    def collect_vect(self, level):
        self.vect_level = level                            # vector patterns and clip paths are not on the page

    def collect_flags(self, flags, consider):
        if self.is_page_props and not (flags & 0x00FF0000):
            if not self.started:
                self._start_page()
        elif self.is_page_props and consider:
            self.ignore_page = True
        self.is_page_props = False

    def collect_other_list(self): pass
    def collect_path(self, path): self.path.append(path)
    def collect_polygon(self): self.in_polygon = True
    def collect_spline(self): self.in_spline = True
    def collect_polygon_transform(self, n, nxt, rx, ry, cx, cy): self.polygon = (n, nxt, rx, ry, cx, cy)
    def collect_ppdt(self, points, knots): self.spline = (points, knots)
    def collect_page_size(self, *a): pass

    def collect_transform(self, trafos, consider_group):
        if self.obj_level:
            self.trafos = trafos                           # (a group's own transform exists only before CorelDRAW 4)

    def collect_artistic_text(self): self.kind = "text"
    def collect_paragraph_text(self): self.kind = "text"
    def collect_bitmap(self): self.kind = "image"

    def collect_level(self, level):
        if level <= self.obj_level:
            self._flush_path()
            self.obj_level = 0
        if level <= self.vect_level:
            self.vect_level = 0
            self.page = list(self.sizes[self.page_i - 1 if self.page_i else 0])
        if level <= self.page_level:
            self._end_page()
            self.page_level = 0

    # -- the outline of one object
    def _flush_path(self):
        path, kind = self.path, self.kind
        poly, in_poly, spl, in_spl = self.polygon, self.in_polygon, self.spline, self.in_spline
        trafos = self.trafos
        self.path = _Path()
        self.polygon, self.in_polygon, self.spline, self.in_spline = None, False, None, False
        self.trafos, self.kind = [], ""
        if self.vect_level:
            return
        if path.empty() and not (spl and in_spl):
            if kind:
                self.pending_skip["TEXT" if kind == "text" else "IMAGE"] += 1
            return
        if poly and in_poly:
            _polygon_create(poly, path)
        if spl and in_spl:
            _spline_create(spl[0], spl[1], path)
        for t in trafos:
            path.transform(t)
        ox, oy = self.page[2], self.page[3]
        path.transform(_T(1, 0, -ox, 0, 1, -oy))
        actions = _flush_actions(_write_out(path), path.closed)
        if not actions:
            return
        contours = _contours_of(actions)
        if contours:
            self.shapes.append(Shape(contours))
            if self.ignore_page:
                self.pending_master += 1

    def pages(self) -> list[Page]:
        return self.out_pages


def _polygon_create(poly: tuple, path: _Path) -> None:
    """libcdr's CDRPolygon::create: the path of one corner is turned round the centre to make the others."""
    n, nxt, rx, ry, cx, cy = poly
    if n == 0:
        return
    if n > MAX_POLYGON or nxt > MAX_POLYGON or (len(path.el) + 2) * max(n, nxt, 1) > MAX_ELEMENTS:
        raise Unsupported("a polygon with an absurd number of corners")
    tmp = path.copy()
    step = 2 * math.pi / n
    if nxt and n % nxt:
        t = _T(math.cos(nxt * step), math.sin(nxt * step), 0.0, -math.sin(nxt * step), math.cos(nxt * step), 0.0)
        for _ in range(1, n):
            tmp.transform(t)
            path.append(tmp)
    else:
        t = _T(math.cos(nxt * step), math.sin(nxt * step), 0.0, -math.sin(nxt * step), math.cos(nxt * step), 0.0)
        shift = _T(math.cos(step), math.sin(step), 0.0, -math.sin(step), math.cos(step), 0.0)
        for i in range(nxt):
            if i:
                tmp.transform(shift)
                path.append(tmp)
            for _ in range(1, (n // nxt if nxt else 1)):
                tmp.transform(t)
                path.append(tmp)
            path.close()
    path.close()
    path.transform(_T(rx, 0.0, cx, 0.0, ry, cy))


def _spline_create(points: list, knots: list, path: _Path) -> None:
    """libcdr's CDRSplineData::create."""
    if not points or not knots:
        return
    path.move(*points[0])
    tmp = [points[0]]

    def emit():
        if len(tmp) == 2:
            path.line(*tmp[1])
        elif len(tmp) == 3:
            path.quad(tmp[1][0], tmp[1][1], tmp[2][0], tmp[2][1])
        elif len(tmp) > 3:
            path.spline(tmp)

    for i in range(1, min(len(points), len(knots))):
        tmp.append(points[i])
        if knots[i]:
            emit()
            tmp.clear()
            tmp.append(points[i])
    emit()


# ---------------------------------------------------------------------------------------------------------------
# the parser (libcdr's CDRParser, the records that carry geometry)
# ---------------------------------------------------------------------------------------------------------------

LISTS = (b"RIFF", b"LIST")


class _Parser:
    def __init__(self, collector, external: list[bytes | None]):
        self.c = collector
        self.ext = external
        self.fast_levels = all(hasattr(collector, a) for a in ("obj_level", "vect_level", "page_level"))
        self.version = 0
        self.prec = 0                                      # 16 or 32 (bit), 0 until the version is known

    # -- precision dependent numbers
    def _need_prec(self):
        if not self.prec:
            raise Unsupported("the file gives its numbers before its version")

    def coord(self, r: _R) -> float:
        if self.prec == 32:
            return r.s32() / 254000.0
        self._need_prec()
        return r.s16() / 1000.0

    def unsigned(self, r: _R) -> int:
        if self.prec == 32:
            return r.u32()
        self._need_prec()
        return r.u16()

    def angle(self, r: _R) -> float:
        self._need_prec()
        return math.pi * r.s16() / 1800.0 if self.prec == 16 else math.pi * r.s32() / 180000000.0

    def rect_coord(self, r: _R) -> float:
        if self.version < 1500:
            return self.coord(r)
        return r.f64() / 254000.0

    # -- records
    def parse(self, data: bytes) -> bool:
        return self.parse_records(_R(data), [], 0)

    def parse_records(self, r: _R, blocks: list[int], level: int) -> bool:
        self.c.collect_level(level)
        while r.p < r.n:
            if not self.parse_record(r, blocks, level):
                return False
        return True

    def parse_record(self, r: _R, blocks: list[int], level: int) -> bool:
        try:
            c = self.c
            if not self.fast_levels or level <= c.obj_level or level <= c.vect_level or level <= c.page_level:
                c.collect_level(level)                      # (it does nothing else than act on these three levels)
            while not r.end() and r.u8() == 0:
                pass
            if not r.end():
                r.p -= 1
            else:
                return True
            four = r.take(4)
            length = r.u32()
            if len(blocks) > length:
                length = blocks[length]
            position = r.p
            list_type = b""
            if four in LISTS:
                list_type = r.take(4)
                if list_type == b"stlt" and self.version >= 700:
                    four = list_type
                else:
                    self.c.collect_other_list()
            if four in LISTS:
                cmprsize = length - 4
                if list_type == b"cmpr":
                    cmprsize = r.u32()
                    r.skip(12)
                    if r.take(4) != b"CPng":
                        return False
                    if r.u16() != 1:
                        return False
                    if r.u16() != 4:
                        return False
                elif list_type == b"page":
                    self.c.collect_page(level)
                elif list_type == b"obj ":
                    self.c.collect_object(level)
                elif list_type in (b"grp ", b"lnkg"):
                    self.c.collect_group(level)
                elif list_type[:3].upper() == b"CDR":
                    self.version = cdr_version(list_type[3])
                    self.prec = 16 if self.version < 600 else 32
                elif list_type in (b"vect", b"clpt"):
                    self.c.collect_vect(level)
                compressed = list_type == b"cmpr"
                if cmprsize < 0:
                    return False
                if not compressed:
                    sub = _R(r.take(min(cmprsize, r.n - r.p)) if cmprsize else b"")
                    if not self.parse_records(sub, blocks, level + 1):
                        return False
                else:
                    body = _inflate(r.take(min(cmprsize, r.n - r.p)))
                    blocks_len = length + position - r.p
                    block_bytes = _inflate(r.take(min(max(blocks_len, 0), r.n - r.p)))
                    lens = [struct.unpack_from("<I", block_bytes, i)[0] for i in range(0, len(block_bytes) - 3, 4)]
                    if not self.parse_records(_R(body), lens, level + 1):
                        return False
            else:
                self.read_record(four, length, r)
            r.p = max(0, min(r.n, position + length))               # (seek: clamped)
            return True
        except _Short:
            return False

    def redirect(self, r: _R, length: int) -> tuple[_R, int]:
        """CorelDRAW X6 and later may keep the content of a chunk in another stream of the package."""
        if self.version >= 1600 and length == 0x10:
            number = r.u32()
            length = r.u32()
            if number < len(self.ext):
                offset = r.u32()
                data = self.ext[number]
                if data is None:
                    raise Unsupported("a stream of the package is missing")
                rr = _R(data)
                rr.seek(offset)
                if rr.end():
                    raise _Short()
                return rr, length
            if number == 0xFFFFFFFF:
                return r, length
            raise Unsupported("a chunk points to a stream that is not there")
        return r, length

    def read_record(self, four: bytes, length: int, r: _R) -> None:
        if four in (b"loda", b"lobj"):
            self.read_loda(r, length)
        elif four == b"vrsn":
            r2, length = self.redirect(r, length)
            self.version = r2.u16()
            self.prec = 16 if self.version < 600 else 32
        elif four == b"trfd":
            self.read_trfd(r, length)
        elif four == b"flgs":
            r2, length = self.redirect(r, length)
            self.c.collect_flags(r2.u32(), self.version >= 400)
        elif four == b"mcfg":
            self.read_mcfg(r, length)
        elif four == b"ppdt":
            self.read_ppdt(r, length)

    def read_mcfg(self, r: _R, length: int) -> None:
        r, length = self.redirect(r, length)
        if self.version >= 1300:
            r.skip(12)
        elif self.version >= 900:
            r.skip(4)
        elif 600 <= self.version < 700:
            r.skip(0x1C)
        w = self.coord(r)
        h = self.coord(r)
        self.c.collect_page_size(w, h, -w / 2.0, -h / 2.0)

    def read_ppdt(self, r: _R, length: int) -> None:
        r, length = self.redirect(r, length)
        n = r.u16()
        size = 2 * (2 if self.prec == 16 else 4) + 4
        n = min(n, (r.n - r.p) // size)
        r.skip(4)
        pts = [(self.coord(r), self.coord(r)) for _ in range(n)]
        knots = [r.u32() for _ in range(n)]
        self.c.collect_ppdt(pts, knots)

    def read_trfd(self, r: _R, length: int) -> None:
        r, length = self.redirect(r, length)
        start = r.p
        max_len = r.n
        if start >= max_len:
            return
        if length > max_len or max_len - length < start:
            length = max_len - start
        chunk_len = self.unsigned(r)
        n_args = self.unsigned(r)
        start_args = self.unsigned(r)
        if start_args >= length:
            return
        if n_args > (length - start_args) // 4:
            n_args = (length - start_args) // 4
        r.seek(start + start_args)
        offsets = [self.unsigned(r) for _ in range(n_args)]
        trafos: list[_T] = []
        for off in offsets:
            r.seek(start + off)
            if self.version >= 1300:
                r.skip(8)
            kind = r.u16()
            if kind == 0x08:
                if self.version >= 600:
                    r.skip(6)
                if self.version >= 500:
                    div = 1000.0 if self.version < 600 else 254000.0
                    v0, v1, x0 = r.f64(), r.f64(), r.f64() / div
                    v3, v4, y0 = r.f64(), r.f64(), r.f64() / div
                else:
                    v0, v1 = r.fixed(), r.fixed()
                    x0 = r.s32() / 1000.0
                    v3, v4 = r.fixed(), r.fixed()
                    y0 = r.s32() / 1000.0
                trafos.append(_T(v0, v1, x0, v3, v4, y0))
        if trafos:
            self.c.collect_transform(trafos, self.version < 400)

    def read_loda(self, r: _R, length: int) -> None:
        r, length = self.redirect(r, length)
        start = r.p
        max_len = r.n
        if start >= max_len:
            return
        if length > max_len or max_len - length < start:
            length = max_len - start
        chunk_len = self.unsigned(r)
        n_args = self.unsigned(r)
        start_args = self.unsigned(r)
        if start_args >= length:
            return
        start_types = self.unsigned(r)
        if start_types >= length:
            return
        if n_args > (length - start_args) // 4:
            n_args = (length - start_args) // 4
        ctype = self.unsigned(r)
        if ctype == 0x26:
            self.c.collect_spline()
        r.seek(start + start_args)
        offsets = [self.unsigned(r) for _ in range(n_args)]
        old = self.version < 400
        # (libcdr reads the argument types into the slots from the last to the first)
        arg_types = [0] * n_args
        r.seek(start + start_types)
        i = n_args
        while i > 0:
            i -= 1
            arg_types[i] = self.unsigned(r)
        for i in range(n_args):
            r.seek(start + offsets[i])
            t = arg_types[i]
            if t == 0x1E:
                if (not old and ctype == 0x01) or (old and ctype == 0x00):
                    self.read_rectangle(r)
                elif (not old and ctype == 0x02) or (old and ctype == 0x01):
                    self.read_ellipse(r)
                elif (not old and ctype == 0x03) or (old and ctype == 0x02):
                    self.read_line_and_curve(r)
                elif ctype == 0x25:
                    self.read_path(r)
                elif (not old and ctype == 0x04) or (old and ctype == 0x03):
                    self.c.collect_artistic_text()
                elif (not old and ctype == 0x05) or (old and ctype == 0x04):
                    self.c.collect_bitmap()
                elif (not old and ctype == 0x06) or (old and ctype == 0x05):
                    self.c.collect_paragraph_text()
                elif ctype == 0x14:
                    self.read_polygon_coords(r)
            elif t == 0x2AF8:
                self.read_polygon_transform(r)
            elif t == 0x4ABA:
                w = self.coord(r)
                h = self.coord(r)
                self.c.collect_page_size(w, h, -w / 2.0, -h / 2.0)
        r.seek(start + chunk_len)

    # -- shapes
    def read_rectangle(self, r: _R) -> None:
        x0 = self.rect_coord(r)
        y0 = self.rect_coord(r)
        r3 = r2 = r1 = r0 = 0.0
        corner = 0
        sx = sy = 1.0
        if self.version < 1500:
            r3 = self.rect_coord(r)
            r2 = r3 if self.version < 900 else self.rect_coord(r)
            r1 = r3 if self.version < 900 else self.rect_coord(r)
            r0 = r3 if self.version < 900 else self.rect_coord(r)
        else:
            s = r.f64()
            if s != 0:
                sx = s
            s = r.f64()
            if s != 0:
                sy = s
            scale_with = r.u8()
            r.skip(7)
            if scale_with == 0:
                r3 = r.f64()
                corner = r.u8()
                r.skip(15)
                r2 = r.f64()
                r.skip(16)
                r1 = r.f64()
                r.skip(16)
                r0 = r.f64()
                m = min(abs(x0 * sx / 2.0), abs(y0 * sy / 2.0))
                r3 *= m
                r2 *= m
                r1 *= m
                r0 *= m
            else:
                r3 = self.rect_coord(r)
                corner = r.u8()
                r.skip(15)
                r2 = self.rect_coord(r)
                r.skip(16)
                r1 = self.rect_coord(r)
                r.skip(16)
                r0 = self.rect_coord(r)
        p = _Path()
        if r0 > 0.0:
            p.move(0.0, -r0 / sy)
        else:
            p.move(0.0, 0.0)
        if r1 > 0.0:
            p.line(0.0, y0 + r1 / sy)
            if corner == 0:
                p.quad(0.0, y0, r1 / sx, y0)
            elif corner == 1:
                p.quad(r1 / sx, y0 + r1 / sy, r1 / sx, y0)
            elif corner == 2:
                p.line(r1 / sx, y0)
        else:
            p.line(0.0, y0)
        if r2 > 0.0:
            p.line(x0 - r2 / sx, y0)
            if corner == 0:
                p.quad(x0, y0, x0, y0 + r2 / sy)
            elif corner == 1:
                p.quad(x0 - r2 / sx, y0 + r2 / sy, x0, y0 + r2 / sy)
            elif corner == 2:
                p.line(x0, y0 + r2 / sy)
        else:
            p.line(x0, y0)
        if r3 > 0.0:
            p.line(x0, -r3 / sy)
            if corner == 0:
                p.quad(x0, 0.0, x0 - r3 / sx, 0.0)
            elif corner == 1:
                p.quad(x0 - r3 / sx, -r3 / sy, x0 - r3 / sx, 0.0)
            elif corner == 2:
                p.line(x0 - r3 / sx, 0.0)
        else:
            p.line(x0, 0.0)
        if r0 > 0.0:
            p.line(r0 / sx, 0.0)
            if corner == 0:
                p.quad(0.0, 0.0, 0.0, -r0 / sy)
            elif corner == 1:
                p.quad(r0 / sx, -r0 / sy, 0.0, -r0 / sy)
            elif corner == 2:
                p.line(0.0, -r0 / sy)
        else:
            p.line(0.0, 0.0)
        p.close()
        self.c.collect_path(p)

    def read_ellipse(self, r: _R) -> None:
        x = self.coord(r)
        y = self.coord(r)
        a1 = self.angle(r)
        a2 = self.angle(r)
        pie = self.unsigned(r) != 0
        cx, cy = x / 2.0, y / 2.0
        rx, ry = abs(cx), abs(cy)
        a1 = math.fmod(a1, 2 * math.pi)
        if a1 < 0:
            a1 += 2 * math.pi
        a2 = math.fmod(a2, 2 * math.pi)
        if a2 < 0:
            a2 += 2 * math.pi
        p = _Path()
        if a1 != a2:
            if a2 < a1:
                a2 += 2 * math.pi
            x0, y0 = cx + rx * math.cos(a1), cy - ry * math.sin(a1)
            p.move(x0, y0)
            p.arc(cx, cy, rx, ry, a1, a2 - a1)
            if pie:
                p.line(cx, cy)
                p.line(x0, y0)
                p.close()
        else:
            x0, y0 = cx + rx * math.cos(a1), cy - ry * math.sin(a1)
            p.move(x0, y0)
            p.arc(cx, cy, rx, ry, a1, math.pi / 2.0)                       # a quarter ...
            p.arc(cx, cy, rx, ry, a1 + math.pi / 2.0, 3 * math.pi / 2.0)   # ... and the other three
            p.close()
        self.c.collect_path(p)

    def _points(self, r: _R, n: int) -> list[tuple[float, float]]:
        self._need_prec()
        v = r.coords(2 * n, self.prec)
        div = 1000.0 if self.prec == 16 else 254000.0
        return [(v[i] / div, v[i + 1] / div) for i in range(0, 2 * n, 2)]

    @staticmethod
    def _types(r: _R, n: int) -> list[int]:
        if r.p + n > r.n:
            raise _Short()
        out = list(r.d[r.p:r.p + n])
        r.p += n
        return out

    def _output_path(self, points, types) -> None:
        p = _Path()
        tmp: list[tuple[float, float]] = []
        for k, (x, y) in enumerate(points):
            t = types[k]
            is_closed = bool(t & 0x08)
            if not (t & 0x40) and not (t & 0x80):
                if is_closed:
                    p.close()
                tmp.clear()
                p.move(x, y)
            elif (t & 0x40) and not (t & 0x80):
                tmp.clear()
                p.line(x, y)
                if is_closed:
                    p.close()
            elif not (t & 0x40) and (t & 0x80):
                if len(tmp) >= 2:
                    p.cubic(tmp[0][0], tmp[0][1], tmp[1][0], tmp[1][1], x, y)
                else:
                    p.line(x, y)
                if is_closed:
                    p.close()
                tmp.clear()
            else:
                tmp.append((x, y))
        self.c.collect_path(p)

    def _point_size(self) -> int:
        return 2 * (2 if self.prec == 16 else 4) + 1

    def read_line_and_curve(self, r: _R) -> None:
        n = r.u16()
        size = self._point_size()
        r.skip(2)
        n = min(n, (r.n - r.p) // size)
        pts = self._points(r, n)
        types = self._types(r, n)
        self._output_path(pts, types)

    def read_path(self, r: _R) -> None:
        r.skip(4)
        n = r.u16() + r.u16()
        size = self._point_size()
        left = r.n - r.p
        if left < 16:
            n = 0
        elif n > (left - 16) // size:
            n = (left - 16) // size
        r.skip(16)
        pts = self._points(r, n)
        types = self._types(r, n)
        self._output_path(pts, types)

    def read_polygon_coords(self, r: _R) -> None:
        n = r.u16()
        size = self._point_size()
        n = min(n, (r.n - r.p) // size)
        r.skip(2)
        pts = self._points(r, n)
        types = self._types(r, n)
        self._output_path(pts, types)
        self.c.collect_polygon()

    def read_polygon_transform(self, r: _R) -> None:
        if self.version < 1300:
            r.skip(4)
        n = r.u32()
        nxt = r.u32()
        if nxt <= 1:
            nxt = r.u32()
        else:
            r.skip(4)
        if self.version >= 1300:
            r.skip(4)
        rx = r.f64()
        ry = r.f64()
        cx = self.coord(r)
        cy = self.coord(r)
        self.c.collect_polygon_transform(n, nxt, rx, ry, cx, cy)


# ---------------------------------------------------------------------------------------------------------------
# the file
# ---------------------------------------------------------------------------------------------------------------

def _riff_version(data: bytes) -> int:
    if len(data) < 12 or data[:4] != b"RIFF" or data[8:11].upper() != b"CDR":
        return 0
    return cdr_version(data[11])


def _streams(path) -> tuple[bytes, list[bytes | None]]:
    """The RIFF data of the file and the extra streams of a package of CorelDRAW X6 and later."""
    with open(path, "rb") as f:
        data = f.read()
    if _riff_version(data):
        return data, []
    if data[:2] != b"PK":
        raise Unsupported("not a CorelDRAW drawing")
    try:
        z = zipfile.ZipFile(path)
    except zipfile.BadZipFile as exc:
        raise Unsupported("a damaged ZIP file") from exc
    with z:
        names = {n.lower(): n for n in z.namelist()}
        ext: list[bytes | None] = []
        def member(name: str) -> bytes:
            if z.getinfo(name).file_size > MAX_INFLATED:
                raise Unsupported("a part of the package is too big")
            return z.read(name)

        if "content/riffdata.cdr" in names:
            riff = member(names["content/riffdata.cdr"])
        elif "content/root.dat" in names:
            riff = member(names["content/root.dat"])
            if "content/datafilelist.dat" in names:
                listing = z.read(names["content/datafilelist.dat"]).split(b"\n")
                for item in listing:
                    item = item.strip(b"\r")
                    if not item:
                        continue
                    n = names.get("content/data/" + item.decode("latin-1").lower())
                    ext.append(member(n) if n else None)
        else:
            raise Unsupported("a ZIP file without a CorelDRAW drawing in it")
    if not _riff_version(riff):
        raise Unsupported("the drawing in the package is not one this reader knows")
    return riff, ext


def read(path) -> Drawing:
    """Read the first page of a drawing; raises ``Unsupported`` when the file is not one it can follow."""
    try:
        return _read(path)
    except Unsupported:
        raise
    except Exception as exc:        # noqa: BLE001  (whatever a damaged file does to a reader: it is not a crash of ours)
        raise Unsupported(f"the file could not be followed ({type(exc).__name__}: {exc})") from exc


def _read(path) -> Drawing:
    data, ext = _streams(path)
    version = _riff_version(data)
    if version < FIRST_VERSION:
        raise Unsupported(f"CorelDRAW version {version // 100} is older than this reader goes (5)")
    if version > LAST_VERSION:
        raise Unsupported(f"CorelDRAW version {version // 100 - 5} is newer than this reader knows (X8)")
    pages = _Pages()
    first = _Parser(pages, ext)
    if not first.parse(data) or not pages.pages:
        raise Unsupported("the file could not be followed (damaged, or a kind of file this reader does not know)")
    content = _Content(pages.pages)
    second = _Parser(content, ext)
    if not second.parse(data):
        raise Unsupported("the file could not be followed (damaged, or a kind of file this reader does not know)")
    content.collect_level(0)
    out = content.pages()
    if not out:
        raise Unsupported("no page of the drawing could be read")
    return Drawing(version, out, version not in VERIFIED)

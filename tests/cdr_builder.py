"""Makes CorelDRAW files for the tests: a small writer of the RIFF container libcdr reads (CorelDRAW 5 to X6).

There is no CorelDRAW here to save drawings with, so the layout is the one libcdr's reader expects (the file formats of
libcdr: CDRParser.cpp), written by this module.  That is what makes a *differential* test possible: libcdr itself
(``cdr2xhtml``) reads the very same file, and the built-in reader must give the same outlines.
"""
from __future__ import annotations

import io
import math
import struct
import zipfile
import zlib


def form_char(version: int) -> bytes:
    n = version // 100
    return bytes([0x30 + n if n < 10 else 0x37 + n])


class Node:
    """A chunk (``payload``) or a list (``children``) of the RIFF tree."""

    def __init__(self, four: str, payload: bytes | None = None, children: list["Node"] | None = None, kind: str = ""):
        self.four, self.payload, self.children, self.kind = four, payload, children, kind


def chunk(four: str, payload: bytes) -> Node:
    return Node(four, payload=payload)


def lst(kind: str, *children: Node) -> Node:
    return Node("LIST", children=list(children), kind=kind)


class _Raw(Node):
    """A list that is already bytes (the compressed one)."""

    def __init__(self, four: str, body: bytes):
        super().__init__(four, payload=None, children=[])
        self.body = body


def _plain(node: Node) -> bytes:
    if isinstance(node, _Raw):
        return node.four.encode() + struct.pack("<I", len(node.body)) + node.body
    if node.payload is not None:
        body = node.payload
        return node.four.encode() + struct.pack("<I", len(body)) + body + (b"\0" if len(body) % 2 else b"")
    inner = b"".join(_plain(c) for c in node.children)
    body = node.kind.encode() + inner
    return node.four.encode() + struct.pack("<I", len(body)) + body


def _indexed(node: Node, lengths: list[int]) -> bytes:
    """A record of a compressed list: its length field is an index into the table of lengths."""
    if node.payload is not None:
        body = node.payload
        lengths.append(len(body))
        return node.four.encode() + struct.pack("<I", len(lengths) - 1) + body
    slot = len(lengths)
    lengths.append(0)
    inner = b"".join(_indexed(c, lengths) for c in node.children)
    lengths[slot] = 4 + len(inner)
    return node.four.encode() + struct.pack("<I", slot) + node.kind.encode() + inner


def compressed(*children: Node) -> Node:
    """A ``cmpr`` list holding ``children`` (CorelDRAW keeps the drawing in one from version 7)."""
    lengths: list[int] = []
    body = b"".join(_indexed(c, lengths) for c in children)
    comp = zlib.compress(body)
    blocks = zlib.compress(b"".join(struct.pack("<I", n) for n in lengths))
    payload = (struct.pack("<I", len(comp)) + struct.pack("<III", len(body), 0, 0) + b"CPng" + struct.pack("<HH", 1, 4)
               + comp + blocks)
    return _Raw("LIST", b"cmpr" + payload)


class Builder:
    """One drawing: pages of objects.  Lengths are inches, angles degrees."""

    def __init__(self, version: int = 1300, page: tuple[float, float] = (8.0, 6.0), external: bool = False):
        self.v = version
        self.page = page
        self.pages: list[tuple[int, list[Node]]] = []       # (flags, objects)
        self.external = external and version >= 1600         # CorelDRAW X6 and later: the content is in other streams
        self.data = b""

    # -- numbers of the version
    @property
    def p16(self) -> bool:
        return self.v < 600

    def u(self, n: int) -> bytes:
        return struct.pack("<H" if self.p16 else "<I", n)

    def c(self, inches: float) -> bytes:
        return struct.pack("<h", round(inches * 1000)) if self.p16 else struct.pack("<i", round(inches * 254000))

    def a(self, deg: float) -> bytes:
        return struct.pack("<h", round(deg * 10)) if self.p16 else struct.pack("<i", round(deg * 1e6))

    # -- records
    def _chunk(self, four: str, payload: bytes) -> Node:
        """A chunk; with ``external`` the payload goes to a stream of the package and the chunk points at it."""
        if not self.external:
            return chunk(four, payload)
        pointer = struct.pack("<IIII", 0, len(payload), len(self.data), 0)          # stream 0, length, offset (+ padding to 0x10)
        self.data += payload
        return chunk(four, pointer)

    def loda(self, ctype: int, args: list[tuple[int, bytes]]) -> Node:
        n = len(args)
        us = 2 if self.p16 else 4
        start_args = 5 * us
        start_types = start_args + us * n
        data = start_types + us * n
        offsets, blobs, pos = [], b"", data
        for _t, blob in args:
            offsets.append(pos)
            blobs += blob
            pos += len(blob)
        types = [t for t, _b in args][::-1]               # (libcdr reads the types into the slots from the last)
        payload = (self.u(pos) + self.u(n) + self.u(start_args) + self.u(start_types) + self.u(ctype)
                   + b"".join(self.u(o) for o in offsets) + b"".join(self.u(t) for t in types) + blobs)
        return self._chunk("loda", payload)

    def trfd(self, *trafos: tuple[float, float, float, float, float, float]) -> Node:
        """Each as (v0, v1, x0, v3, v4, y0): x' = v0 x + v1 y + x0 (x0, y0 in inches)."""
        us = 2 if self.p16 else 4
        n = len(trafos)
        start_args = 3 * us
        data = start_args + us * n
        blobs, offsets = b"", []
        div = 1000.0 if self.v < 600 else 254000.0
        for v0, v1, x0, v3, v4, y0 in trafos:
            offsets.append(data + len(blobs))
            blob = b"\0" * 8 if self.v >= 1300 else b""
            blob += struct.pack("<H", 0x08)
            if self.v >= 600:
                blob += b"\0" * 6
            blob += struct.pack("<6d", v0, v1, x0 * div, v3, v4, y0 * div)
            blobs += blob
        payload = (self.u(data + len(blobs)) + self.u(n) + self.u(start_args) + b"".join(self.u(o) for o in offsets) + blobs)
        return self._chunk("trfd", payload)

    def obj(self, ctype: int, args: list[tuple[int, bytes]], *trafos, extra: list[Node] | None = None) -> Node:
        kids = [self.loda(ctype, args)]
        if trafos:
            kids.append(self.trfd(*trafos))
        kids += extra or []
        return lst("obj ", *kids)

    # -- objects (the type number of an object changed with version 4: all these versions are 5 and newer)
    def rect(self, w: float, h: float, radii=(0.0, 0.0, 0.0, 0.0), corner: int = 0, *trafos) -> Node:
        """r3, r2, r1, r0 as libcdr names the corners (versions before 9 take only the first)."""
        r3, r2, r1, r0 = radii
        if self.v < 1500:
            blob = self.c(w) + self.c(h) + self.c(r3)
            if self.v >= 900:
                blob += self.c(r2) + self.c(r1) + self.c(r0)
        else:
            d = lambda inches: struct.pack("<d", inches * 254000)  # noqa: E731
            blob = d(w) + d(h) + struct.pack("<dd", 1.0, 1.0) + bytes([1]) + b"\0" * 7
            blob += d(r3) + bytes([corner]) + b"\0" * 15 + d(r2) + b"\0" * 16 + d(r1) + b"\0" * 16 + d(r0)
        return self.obj(0x01, [(0x1E, blob)], *trafos)

    def ellipse(self, w: float, h: float, a1: float = 0.0, a2: float = 0.0, pie: bool = False, *trafos) -> Node:
        blob = self.c(w) + self.c(h) + self.a(a1) + self.a(a2) + self.u(1 if pie else 0)
        return self.obj(0x02, [(0x1E, blob)], *trafos)

    def _points(self, pts, types) -> bytes:
        return b"".join(self.c(x) + self.c(y) for x, y in pts) + bytes(types)

    def curve(self, pts, types, *trafos) -> Node:
        """types: bit 0x08 closes, 0x40 / 0x80: move 00, line 40, curve 80, curve control C0."""
        blob = struct.pack("<H", len(pts)) + b"\0\0" + self._points(pts, types)
        return self.obj(0x03, [(0x1E, blob)], *trafos)

    def path(self, pts, types, *trafos) -> Node:
        blob = b"\0" * 4 + struct.pack("<HH", len(pts), 0) + b"\0" * 16 + self._points(pts, types)
        return self.obj(0x25, [(0x1E, blob)], *trafos)

    def polygon(self, pts, types, n: int, nxt: int, rx: float, ry: float, cx: float, cy: float, *trafos) -> Node:
        coords = struct.pack("<H", len(pts)) + b"\0\0" + self._points(pts, types)
        tr = b"" if self.v >= 1300 else b"\0" * 4
        tr += struct.pack("<II", n, nxt) + b"\0" * 4          # nxt > 1: the next value is skipped
        if self.v >= 1300:
            tr += b"\0" * 4
        tr += struct.pack("<dd", rx, ry) + self.c(cx) + self.c(cy)
        return self.obj(0x14, [(0x1E, coords), (0x2AF8, tr)], *trafos)

    def spline(self, pts, knots, *trafos) -> Node:
        n = len(pts)
        ppdt = struct.pack("<H", n) + b"\0" * 4 + b"".join(self.c(x) + self.c(y) for x, y in pts) + b"".join(struct.pack("<I", k) for k in knots)
        return self.obj(0x26, [(0x1E, b"\0" * 8)], *trafos, extra=[chunk("ppdt", ppdt)])

    def text(self, x: float = 0.0, y: float = 0.0, *trafos) -> Node:
        return self.obj(0x04, [(0x1E, self.c(x) + self.c(y))], *trafos)

    def bitmap(self, *trafos) -> Node:
        return self.obj(0x05, [(0x1E, self.c(0) + self.c(0) + b"\0" * 32)], *trafos)

    def vect(self, *objs: Node) -> Node:
        """Objects of a vector pattern: they are not on the page."""
        return lst("vect", *objs)

    # -- the drawing
    def add_page(self, objects: list[Node], master: bool = False) -> None:
        self.pages.append((0x90010040 if master else 0x90000040, objects))

    def _mcfg(self) -> Node:
        w, h = self.page
        pre = 12 if self.v >= 1300 else 4 if self.v >= 900 else 0x1C if 600 <= self.v < 700 else 0
        return chunk("mcfg", b"\0" * pre + self.c(w) + self.c(h))

    def _body(self, compress: bool) -> list[Node]:
        pages = [lst("page", chunk("flgs", struct.pack("<I", flags)), *objs) for flags, objs in self.pages]
        head = [chunk("vrsn", struct.pack("<H", self.v)), self._mcfg()]
        if compress and self.v >= 700:
            return head + [compressed(*pages)]
        return head + pages

    def riff(self, compress: bool = True) -> bytes:
        inner = b"".join(_plain(n) for n in self._body(compress))
        body = b"CDR" + form_char(self.v) + inner
        return b"RIFF" + struct.pack("<I", len(body)) + body

    def save(self, path, compress: bool = True) -> None:
        data = self.riff(compress)
        if self.v >= 1400:                                   # CorelDRAW X4 and later: a ZIP package
            buf = io.BytesIO()
            with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
                if self.external:
                    z.writestr("content/root.dat", data)
                    z.writestr("content/dataFileList.dat", "a.dat\n")
                    z.writestr("content/data/a.dat", self.data)
                else:
                    z.writestr("content/riffData.cdr", data)
                z.writestr("metadata/metadata.xml", "<metadata/>")
            data = buf.getvalue()
        with open(path, "wb") as f:
            f.write(data)


def rotation(deg: float, cx: float = 0.0, cy: float = 0.0, move=(0.0, 0.0)):
    """A rotation about (cx, cy), then a shift: the 6 numbers of ``trfd`` (inches)."""
    c, s = math.cos(math.radians(deg)), math.sin(math.radians(deg))
    x0 = cx - (c * cx - s * cy) + move[0]
    y0 = cy - (s * cx + c * cy) + move[1]
    return (c, -s, x0, s, c, y0)

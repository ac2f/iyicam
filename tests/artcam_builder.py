"""Small ArtCAM files for the tests, written byte for byte as ArtCAM 2008 and 2018 write theirs (formats/artcam.py says
what each part is): a compound file, a model of a size, and 2D profile toolpaths with their lines, settings and tool.

Nothing of a real drawing is in them: the shapes are rectangles and circles made here.
"""
from __future__ import annotations

import math
import struct

SECTOR = 512
FREE, END, FATSECT = 0xFFFFFFFF, 0xFFFFFFFE, 0xFFFFFFFD
BIG = b"\xa6\x9b\xc4\xc0\x7f\x84\x2e\x41"


# -- a compound file ------------------------------------------------------------------------------------------------

def compound_file(streams: dict[str, bytes]) -> bytes:
    """A compound file of ``{"a/b/c": bytes}`` (every stream in big sectors: the small-stream cutoff is 0)."""
    tree: dict = {}
    for path, data in streams.items():
        node = tree
        parts = path.split("/")
        for p in parts[:-1]:
            node = node.setdefault(p, {})
        node[parts[-1]] = data
    entries: list[list] = []                    # [name, kind, left, right, child, start, size, data]

    def add(name, kind, data=None):
        entries.append([name, kind, FREE, FREE, FREE, END, 0, data])
        return len(entries) - 1

    def build(node: dict, me: int) -> None:
        prev = None
        for name in sorted(node):
            v = node[name]
            i = add(name, 2 if isinstance(v, bytes) else 1, v if isinstance(v, bytes) else None)
            if prev is None:
                entries[me][4] = i
            else:
                entries[prev][3] = i               # (a chain of right siblings: every reader walks it)
            prev = i
            if isinstance(v, dict):
                build(v, i)

    add("Root Entry", 5)
    build(tree, 0)
    data_sectors: list[bytes] = []
    for e in entries:
        if e[1] == 2:
            d = e[7]
            e[5] = len(data_sectors) if d else END
            e[6] = len(d)
            for k in range(0, len(d), SECTOR):
                data_sectors.append(d[k:k + SECTOR].ljust(SECTOR, b"\0"))
    n_dir = math.ceil(len(entries) / 4)
    n_fat = 1
    while n_fat * 128 < n_fat + n_dir + len(data_sectors):
        n_fat += 1
    fat = [FREE] * (n_fat * 128)
    for k in range(n_fat):
        fat[k] = FATSECT
    dir0 = n_fat
    for k in range(n_dir):
        fat[dir0 + k] = dir0 + k + 1 if k < n_dir - 1 else END
    base = n_fat + n_dir
    for e in entries:
        if e[1] == 2 and e[6]:
            n = math.ceil(e[6] / SECTOR)
            e[5] += base
            for k in range(n):
                fat[e[5] + k] = e[5] + k + 1 if k < n - 1 else END
    header = bytearray(SECTOR)
    header[0:8] = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"
    struct.pack_into("<HHHHH", header, 0x18, 0x3E, 3, 0xFFFE, 9, 6)
    struct.pack_into("<IIIIIIIII", header, 0x28, 0, n_fat, dir0, 0, 0, END, 0, END, 0)
    difat = list(range(n_fat)) + [FREE] * (109 - n_fat)
    struct.pack_into("<109I", header, 0x4C, *difat)
    out = bytearray(header)
    out += struct.pack(f"<{len(fat)}I", *fat)
    d = bytearray()
    for name, kind, left, right, child, start, size, _ in entries:
        e = bytearray(128)
        n = name.encode("utf-16-le")[:62]
        e[0:len(n)] = n
        struct.pack_into("<HBB", e, 64, len(n) + 2, kind, 1)
        struct.pack_into("<III", e, 68, left, right, child)
        struct.pack_into("<IQ", e, 116, start, size)
        d += e
    out += d.ljust(n_dir * SECTOR, b"\0")
    for s in data_sectors:
        out += s
    return bytes(out)


# -- the shapes of the line of a toolpath --------------------------------------------------------------------------

def rect_line(x0, y0, w, h, d):
    """The line of an outside profile of a rectangle: its sides ``d`` out, round at the corners (counter-clockwise)."""
    b = math.tan(math.pi / 8)
    P = [(x0, y0 - d), (x0 + w, y0 - d), (x0 + w + d, y0), (x0 + w + d, y0 + h), (x0 + w, y0 + h + d),
         (x0, y0 + h + d), (x0 - d, y0 + h), (x0 - d, y0)]
    spans = []
    for k in range(8):
        a, c = P[k], P[(k + 1) % 8]
        spans.append(("line", a, c) if k % 2 == 0 else ("arc", a, c, b))
    return spans


def circle_line(cx, cy, r, ccw=True):
    b = 1.0 if ccw else -1.0
    return [("arc", (cx + r, cy), (cx - r, cy), b), ("arc", (cx - r, cy), (cx + r, cy), b)]


# -- the streams of a toolpath -------------------------------------------------------------------------------------

def _d(*v) -> bytes:
    return struct.pack(f"<{len(v)}d", *v)


def _i(*v) -> bytes:
    return struct.pack(f"<{len(v)}i", *v)


def _cstr(s: str) -> bytes:
    return b"\xff\xfe\xff" + bytes([len(s)]) + s.encode("utf-16-le")


def data2(contours: list[tuple[int, list]], z_levels: list[float], start_z: float, version: int) -> bytes:
    """``contours``: (vector id, spans)."""
    name = b"tpmOpenContourGroupDrivePicture"
    head = (_i(4 if version == 2008 else 6) + b"\0" * 30 + b"\xff\xff\x01\x00" + bytes([len(name)]) + b"\0" + name
            + _i(1, 0, 0) + _d(0.025, 0.25) + _i(2, 1, 0) + _i(len(z_levels)) + _d(*z_levels) + _i(2, 0, 1))
    out = bytearray(head + _i(len(contours)))
    for vid, spans in contours:
        tol = 1e-5 if version == 2008 else 0.0025
        if version == 2008:
            p0 = spans[0][1]
            out += _i(6, 1, 0) + _d(1e-5, 0.0) + _i(vid, -1, len(spans) + 1, 0, 13) + _d(*p0, *p0) + _i(1)
        else:
            out += _i(6, 1, 1) + _d(1e-5, 0.0) + _i(vid, -1, len(spans))
        for s in spans:
            if s[0] == "line":
                out += _i(0, 0) + _d(*s[1], *s[2])
            else:
                out += _i(1, 2) + _d(*s[1], *s[2], s[3], tol)
    out += b"\0" * 12 + _d(start_z)
    return bytes(out)


def parameters(name: str, tool_name: str, *, allowance, stepdown, diameter, number, spindle, feed, plunge, home,
               safe_z, tolerance, version: int) -> bytes:
    """The ``Parameters`` of a toolpath, laid out as ArtCAM 2008 / 2018 lay it out."""
    p = bytearray(0xE4)
    p[0] = 0x0A if version == 2008 else 0x0B
    p[8:16] = _d(allowance)
    p[0x34:0x3C] = _d(stepdown)
    p[0x3C:0x44] = BIG
    p[0x8A:0x92] = _d(diameter / 2)
    p[0xAA:0xAC] = struct.pack("<H", number)
    if version == 2008:
        p = p[:0xDF] + b"\x01\0\0\0\0\0"
    else:
        t = tool_name.encode("utf-16-le")
        p += _i(len(tool_name)) + t + b"\x01\0\0\0\0\0"
    m2 = len(p) + 24
    p += _d(spindle, feed, plunge) + BIG
    assert len(p) == m2 + 8
    p += b"\x01\0" + _d(*home)
    p += b"\0" * (m2 + 0x2E - len(p)) + _d(safe_z)
    p += b"\0" * (m2 + 0x3C - len(p)) + _d(tolerance, 1.0, 0.5, 0.1, 0.6)
    p += b"\0" * (m2 + 0x64 - len(p))
    a = name.encode("cp1252", "replace") + b"\0"
    p += struct.pack("<H", len(a)) + a + b"\x01\0" * 10 + struct.pack("<H", len(a)) + a + b"\x01\0\0\0" + b"\x8b" * 16
    p += b"\0" * 4 + _i(len(tool_name)) + tool_name.encode("utf-16-le") + _i(len(name)) + name.encode("utf-16-le")
    p += b"\0" * 64
    return bytes(p)


def manager(toolpaths: list[dict], version: int) -> bytes:
    """``OA_TOOLPATH_MANAGER``: for each toolpath its name and heights, its tool and its named settings."""
    out = bytearray(b"\x01\xff\xff\x01\x00\x10\x00COA_ToolpathList\x01" + _i(len(toolpaths)))
    first = True
    for t in toolpaths:
        out += b"\xff\xff\x01\x00\x10\x00OA_ToolpathGroup" if first else b"\x03\x80"
        out += b"\x04" + b"\x11" * 16 + _cstr("2dProfiling/2dProfilingFrame.htm") + _cstr(t["name"])
        out += _d(t["safe_z"], *t["home"], -t["start"], t["allowance"], t["tolerance"]) + b"\0" * 49
        out += b"\xff\xff\x01\x00\x07\x00OA_Tool\x01" if first else b"\x07\x80\x01"
        out += b"\xff\xff\x01\x00\x13\x00tpmDB_SlotDrillTool\x05" if first else b"\x09\x80\x05"
        out += _i(t["number"]) + _cstr(t["tool"]) + _i(1) + _d(t["diameter"], t["stepdown"])
        out += _cstr("End mill can be used for Roughing") + _d(t["stepover"], t["spindle"], t["feed"], t["plunge"])
        out += _i(0, 2) + b"\0" * 40
        nodes = [("Integer", "2DSimulationColor", 0x70A6C2), ("Integer", "StrategyType", 0),
                 ("Double", "MaximumDepth", t["finish"]), ("Bool", "ProfileOutside", t.get("outside", True)),
                 ("Bool", "ClimbMill", t.get("climb", True)), ("Bool", "DoRamping", False),
                 ("Double", "RampMaxZigLength", 10.0), ("String", "SequenceVectorId", ""),
                 ("Bool", "DoLeadInOut", False), ("Double", "LeadInOutDistanceMM", 0.0), ("Double", "LeadInAngle", 45.0)]
        if version == 2018:
            nodes += [("Double", "StartDepth", t["start"]), ("Bool", "ProfileAlong", False), ("Bool", "AddBridges", False),
                      ("Double", "BridgeNumber", 4.0)]
        out += (b"\xff\xff\x01\x00\x10\x00wuNamedValueList\x01" if first else b"\x0c\x80\x01")
        seen: dict[str, bytes] = {}
        for kind, name, _v in nodes:
            cls = f"wuNamedListNode{kind}".encode()
            if first and kind not in seen:
                out += b"\xff\xff\x01\x00" + bytes([len(cls)]) + b"\0" + cls
                seen[kind] = bytes([0x80 + len(seen), 0x80])
            else:
                out += seen.get(kind, bytes([0x8D, 0x80]))
            out += b"\x01" + _cstr(name)
        out += b"\0\0"
        for kind, name, v in reversed(nodes):
            out += b"\x01"
            if kind == "Double":
                out += _d(v)
            elif kind == "String":
                out += _cstr(v)
            else:
                out += _i(int(v))
        out += b"\0" * 24
        first = False
    return bytes(out)


def model_parameters(w: float, h: float) -> bytes:
    return b"\x0a\x04\0\0\x36\x02\x68\x04\x01" + _d(w, h, 0.0, 0.0) + b"\0" * 22


def version_info(version: int) -> bytes:
    text = "ArtCAM 2008.260 Unicode" if version == 2008 else "ArtCAM 2018.0.0 Unicode"
    return _i(9, 1, 1) + b"\xd8\x07\0\0" + _i(len(text)) + text.encode("utf-16-le") + b"\0" * 16


TOOL = dict(tool="End Mill 6 mm", diameter=6.0, number=7, stepdown=4.0, stepover=2.4, spindle=18000.0, feed=50.0,
            plunge=20.0, safe_z=12.0, home=(0.0, 0.0, 40.0), start=0.5, finish=9.5, allowance=-0.5, tolerance=0.025)


def model_streams(toolpaths: list[dict], size=(800.0, 600.0), version=2008, root="") -> dict[str, bytes]:
    """The streams of a model.  A toolpath: ``name``, ``contours`` ([(vector id, spans)]) and what differs from TOOL."""
    s = {root + "ArtCAM/Models/Model1/Parameters": model_parameters(*size),
         root + "ArtCAM/Models/Model1/VectorDocument": bytes((i * 151 + 7) % 256 for i in range(3000)),
         root + "ArtCAM/VersionInfo/VersionA": version_info(version),
         root + "ArtCAM/Reliefs/Relief1": b"\r\n  Relief  RELIEF FILE" + b"\0" * 64}
    keys, full = [], []
    for k, t in enumerate(toolpaths):
        t = {**TOOL, **t}
        full.append(t)
        key = f"Key{k}abcdefghijklmnopqrstuv"[:27]
        keys.append(key)
        start, fin, sd = t["start"], t["finish"], t["stepdown"]
        n = max(1, math.ceil((fin - start) / sd - 1e-9))
        zs = [-(start + (fin - start) * (i + 1) / n) for i in range(n)]
        base = f"{root}ArtCAM/ToolpathData/{key}/"
        s[base + "Data2"] = data2(t["contours"], zs, -start, version)
        s[base + "Parameters"] = parameters(t["name"], t["tool"], allowance=t["allowance"], stepdown=sd,
                                            diameter=t["diameter"], number=t["number"], spindle=t["spindle"],
                                            feed=t["feed"], plunge=t["plunge"], home=t["home"], safe_z=t["safe_z"],
                                            tolerance=t["tolerance"], version=version)
        s[base + "RampData"] = b"\x01\0\0\0\0\0"
    lst = bytearray(b"\x01\x00" + struct.pack("<H", len(keys)))
    for key in keys:
        e = (key + "\0").encode("utf-16-le")
        lst += struct.pack("<H", len(e)) + e
    s[root + "ArtCAM/ToolpathData/#UnicodeToolpathList"] = bytes(lst)
    s[root + "ArtCAM/OA_ToolpathData/OA_TOOLPATH_MANAGER"] = manager(full, version)
    return s


def art_file(toolpaths, size=(800.0, 600.0), version=2008) -> bytes:
    return compound_file(model_streams(toolpaths, size, version))


def project_file(models: list[tuple[str, list, tuple]], version=2018) -> bytes:
    """A ``.3dp`` of models: (name, toolpaths, size)."""
    s: dict[str, bytes] = {"ProjectInfo": _i(2), "Preview": b"\0" * 32}
    for k, (name, tps, size) in enumerate(models):
        mid = f"MODEL{k}XXXXXXXXXXXXXXXXXXXX"[:26]
        root = f"Project/Models/{mid}/"
        s.update(model_streams(tps, size, version, root))
        s[root + "Identity"] = _i(len(name)) + name.encode("utf-16-le") + b"\x22" * 16
    return compound_file(s)

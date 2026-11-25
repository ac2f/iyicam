"""ArtCAM models (``.art``) and projects (``.3dp``): ArtCAM 2008 to 2018.

Both are compound files (``cfb``).  A ``.art`` is one model; a ``.3dp`` holds the models of the project, each one
the same as a ``.art`` (``Project/Models/<id>/ArtCAM/...``).  What iyiCAM takes from a model:

* its size (``Models/Model1/Parameters``) and name;
* every 2D toolpath (``ToolpathData/<id>``): its name, tool, feeds and speeds, heights, depths and the settings of
  the profile (``OA_ToolpathData/OA_TOOLPATH_MANAGER``), and the line the tool runs along (``Data2``: lines and arcs);
* the vectors.  ArtCAM keeps the vectors of a model encrypted (``VectorDocument``); what the toolpaths cut is in the
  clear, though: the line of a profile is the vector offset by the tool radius and the allowance, so the vector is that
  line offset back.  A profile that iyiCAM calculates on it runs where ArtCAM's ran (to a few microns), and the vector is
  the drawing wherever the tool could reach (an inside corner sharper than the tool comes out with the tool's radius).
  Vectors that no toolpath uses are not in the file in a readable form: that is said.

Every model becomes sheets of its size: one for the toolpaths that cut the same vectors (ArtCAM puts the parts of
several sheets in one model, one toolpath for each sheet), with the toolpaths on it.  Reliefs (3D) and the other kinds
of toolpaths are said, never dropped silently: the line of a toolpath of another kind is brought in as a vector.
"""
from __future__ import annotations

import math
import re
import struct
from dataclasses import dataclass, field
from pathlib import Path

from .cfb import CompoundFile, CfbError, is_cfb
from .report import ImportReport

BIG = b"\xa6\x9b\xc4\xc0\x7f\x84\x2e\x41"     # 999999.876: ArtCAM writes it after the stepdown and after the plunge rate
CSTR = b"\xff\xfe\xff"                         # an MFC unicode string follows


class ArtCamError(ValueError):
    pass


# ---------------------------------------------------------------------------------------------------------------
# what is in a model
# ---------------------------------------------------------------------------------------------------------------

@dataclass
class ArtContour:
    segments: list            # ("line", a, b) | ("arc", a, b, bulge): bulge = tan(sweep / 4), + counter-clockwise
    closed: bool
    vector_id: int


@dataclass
class ArtToolpath:
    key: str                  # the storage of it (``ToolpathData/<key>``)
    name: str = ""
    kind: str = ""            # "profile" or the name of the strategy ArtCAM gave it
    contours: list[ArtContour] = field(default_factory=list)
    z_levels: list[float] = field(default_factory=list)   # the passes (negative: below the surface)
    tool: dict = field(default_factory=dict)              # name, type, diameter, number, stepdown, stepover, feeds
    values: dict = field(default_factory=dict)            # the named settings of the strategy (ProfileOutside ...)
    safe_z: float | None = None
    home: tuple[float, float, float] | None = None
    start_depth: float | None = None                      # (positive: below the surface)
    finish_depth: float | None = None
    allowance: float = 0.0
    tolerance: float | None = None
    data_start: float | None = None                       # the start depth the line was calculated with (Data2)
    problems: list[str] = field(default_factory=list)

    @property
    def vector_ids(self) -> set[int]:
        return {c.vector_id for c in self.contours}


@dataclass
class ArtModel:
    name: str
    width: float
    height: float
    origin: tuple[float, float] = (0.0, 0.0)
    version: str = ""
    toolpaths: list[ArtToolpath] = field(default_factory=list)
    has_relief: bool = False


# ---------------------------------------------------------------------------------------------------------------
# bytes
# ---------------------------------------------------------------------------------------------------------------

def _d(b: bytes, i: int) -> float | None:
    if i < 0 or i + 8 > len(b):
        return None
    v = struct.unpack_from("<d", b, i)[0]
    return v if math.isfinite(v) else None


def _i(b: bytes, i: int) -> int | None:
    return struct.unpack_from("<i", b, i)[0] if 0 <= i and i + 4 <= len(b) else None


def _cstring(b: bytes, i: int) -> tuple[str, int] | None:
    """An MFC CString at ``i`` (``ff fe ff`` + length + UTF-16): (text, end) or None."""
    if b[i:i + 3] != CSTR:
        return None
    j = i + 3
    if j >= len(b):
        return None
    n = b[j]
    j += 1
    if n == 0xFF:
        if j + 2 > len(b):
            return None
        n = struct.unpack_from("<H", b, j)[0]
        j += 2
        if n == 0xFFFF:
            if j + 4 > len(b):
                return None
            n = struct.unpack_from("<I", b, j)[0]
            j += 4
    if j + 2 * n > len(b):
        return None
    return b[j:j + 2 * n].decode("utf-16-le", "replace"), j + 2 * n


def _strings(b: bytes) -> list[tuple[int, int, str]]:
    """Every CString in ``b``: (start, end, text)."""
    out, i = [], b.find(CSTR)
    while i >= 0:
        s = _cstring(b, i)
        if s is not None:
            out.append((i, s[1], s[0]))
            i = b.find(CSTR, max(s[1], i + 1))
        else:
            i = b.find(CSTR, i + 1)
    return out


# ---------------------------------------------------------------------------------------------------------------
# the line of a toolpath (Data2)
# ---------------------------------------------------------------------------------------------------------------

def _contours(d: bytes, i: int) -> tuple[list[ArtContour], int]:
    """The contours from ``i`` (their count): raises ``ValueError`` where it does not go on as a contour."""
    n = _i(d, i)
    if n is None or not 0 <= n < 10_000_000:
        raise ValueError("no count")
    i += 4
    out = []
    for _ in range(n):
        if _i(d, i) != 6:
            raise ValueError(f"not a contour at {i}")
        short = _i(d, i + 8)                     # 0: ArtCAM 2008 (the ends of the contour follow), 1: 2018 (not)
        if short not in (0, 1):
            raise ValueError(f"a contour of a kind not known at {i}")
        i += 12 + 16
        vid, _m1, npts = _i(d, i), _i(d, i + 4), _i(d, i + 8)
        if vid is None or npts is None or not 1 <= npts < 50_000_000:
            raise ValueError(f"a contour of {npts} points at {i}")
        i += 12
        if not short:
            i += 8 + 36                          # (two numbers, the first and the last point, and a flag)
        segs = []
        for _s in range(npts if short else npts - 1):     # (2008 counts the points, 2018 the spans)
            k = _i(d, i)
            a = (_d(d, i + 8), _d(d, i + 16))
            b = (_d(d, i + 24), _d(d, i + 32))
            if None in a or None in b:
                raise ValueError(f"a point that is not a number at {i}")
            i += 40
            if k == 0:
                segs.append(("line", a, b))
            elif k == 1:
                bulge = _d(d, i)
                if bulge is None:
                    raise ValueError(f"an arc without its bulge at {i}")
                segs.append(("arc", a, b, bulge))
                i += 16
            else:
                raise ValueError(f"a span of kind {k} at {i - 40}")
        closed = bool(segs) and math.dist(segs[0][1], segs[-1][2]) < 1e-6
        out.append(ArtContour(segs, closed, vid))
    return out, i


def read_data2(d: bytes, tp: ArtToolpath) -> None:
    """The kind, the passes and the line of a toolpath from its ``Data2``."""
    m = re.search(rb"\xff\xff\x01\x00(.)\x00(tpm[A-Za-z0-9_]+)", d[:4096], re.S)
    if not m:
        raise ValueError("the line of the toolpath is not in a known form")
    tp.kind = m.group(2).decode("ascii")
    j = m.end()
    tol = _d(d, j + 12)
    if tol is not None and 0 < tol < 10:
        tp.tolerance = tp.tolerance if tp.tolerance is not None else tol
    nz = _i(d, j + 12 + 16 + 12)
    start = None
    if nz is not None and 0 < nz < 10000:
        zs = [_d(d, j + 44 + 8 * k) for k in range(nz)]
        if all(z is not None and -10000 < z < 10000 for z in zs):
            tp.z_levels = [float(z) for z in zs]
            start = j + 44 + 8 * nz + 12
    tries = ([start] if start is not None else []) + list(range(j, min(len(d) - 4, j + 4096)))
    for p in tries:
        try:
            cs, end = _contours(d, p)
        except (ValueError, struct.error, TypeError):
            continue
        if cs and 0 <= len(d) - end < 256:   # (after the line: 12 bytes and the height of the start)
            tp.contours = cs
            z = _d(d, len(d) - 8) if len(d) - end >= 20 else None
            if z is not None and -10000 < z <= 0:
                tp.data_start = -z
            return
    raise ValueError("the line of the toolpath could not be read")


# ---------------------------------------------------------------------------------------------------------------
# the settings of a toolpath (Parameters, and the toolpath manager of ArtCAM)
# ---------------------------------------------------------------------------------------------------------------

def read_parameters(p: bytes, tp: ArtToolpath) -> None:
    """Name, tool, feeds and heights from the ``Parameters`` of a toolpath (the layout of ArtCAM 2008 to 2018)."""
    marks = [m.start() for m in re.finditer(re.escape(BIG), p)]
    v = _d(p, 8)
    if v is not None and abs(v) < 1000:
        tp.allowance = v
    r = _d(p, 0x8A)
    if r is not None and 0 < r < 500:
        tp.tool.setdefault("diameter", 2 * r)
    num = struct.unpack_from("<H", p, 0xAA)[0] if len(p) > 0xAC else 0
    if 0 < num < 100000:
        tp.tool.setdefault("number", num)
    if len(marks) >= 1:
        sd = _d(p, marks[0] - 8)
        if sd is not None and 0 < sd < 1000:
            tp.tool.setdefault("stepdown", sd)
    if len(marks) >= 2:
        m2 = marks[1]
        sp, fr, pr = _d(p, m2 - 24), _d(p, m2 - 16), _d(p, m2 - 8)
        if sp is not None and 0 < sp < 200000:
            tp.tool.setdefault("spindle", sp)
        if fr is not None and 0 < fr < 1e6:
            tp.tool.setdefault("feed", fr)
        if pr is not None and 0 < pr < 1e6:
            tp.tool.setdefault("plunge", pr)
        hx, hy, hz = _d(p, m2 + 0x0A), _d(p, m2 + 0x12), _d(p, m2 + 0x1A)
        if None not in (hx, hy, hz) and max(abs(hx), abs(hy), abs(hz)) < 1e5:
            tp.home = (hx, hy, hz)
        sz = _d(p, m2 + 0x2E)
        if sz is not None and -1000 < sz < 1000:
            tp.safe_z = sz
        tl = _d(p, m2 + 0x3C)
        if tl is not None and 0 < tl < 10:
            tp.tolerance = tl
        k = m2 + 0x64
        if k + 2 <= len(p):
            n = struct.unpack_from("<H", p, k)[0]
            if 1 <= n <= 256 and k + 2 + n <= len(p):
                tp.name = p[k + 2:k + 2 + n].split(b"\0")[0].decode("cp1252", "replace")
        # the name again in UTF-16 (the letters of every language), after the tool's: [u32 n][n UTF-16 letters]
        texts, j = [], k
        while j + 4 <= len(p):
            n = _i(p, j)
            if n and 0 < n <= 256 and j + 4 + 2 * n <= len(p):
                t = p[j + 4:j + 4 + 2 * n]
                try:
                    s = t.decode("utf-16-le")
                except UnicodeDecodeError:
                    s = ""
                if s and all(ch.isprintable() for ch in s):
                    texts.append(s)
                    j += 4 + 2 * n
                    continue
            j += 1
        if texts:
            if len(texts) >= 2:
                tp.tool.setdefault("name", texts[-2])
            folded = texts[-1].encode("cp1252", "replace").decode("cp1252")
            if not tp.name or folded == tp.name or len(texts[-1]) == len(tp.name):
                tp.name = texts[-1]


def _value_len(name: str) -> int:
    """How long the value of a named setting most likely is (8: a number with a fraction, 4: a whole number or yes/no)."""
    n = name.lower()
    if any(w in n for w in ("depth", "length", "angle", "height", "thickness", "offset", "mm", "distance", "radius",
                            "tolerance", "allowance", "stepover", "stepdown", "width", "diameter")):
        return 8
    return 4


def named_lists(b: bytes) -> list[tuple[int, dict]]:
    """The lists of named settings (``wuNamedValueList``) in a toolpath manager: (where it starts, {name: value}).

    A list is its nodes ([class] 01 [name]) and then ``00 00`` and the values, the last node's first, each ``01`` and the
    value (a number of 4 or 8 bytes, or a string)."""
    out = []
    node_class = re.compile(rb"\xff\xff\x01\x00(.)\x00(wuNamedListNode[A-Za-z]+)", re.S)
    i, n = 0, len(b)
    while i < n - 8:
        names: list[tuple[str, str]] = []            # (name, type: Integer | Double | Bool | String | "")
        j, begin = i, i
        while j < n - 4:
            kind = ""
            m = node_class.match(b, j)
            if m:
                kind = m.group(2).decode()[len("wuNamedListNode"):]
                k = m.end()
            elif b[j + 1] & 0x80 and b[j + 1] != 0xFF:
                k = j + 2
            else:
                break
            if b[k:k + 1] != b"\x01":
                break
            s = _cstring(b, k + 1)
            if s is None or not s[0] or not s[0].replace("_", "").isalnum():
                break
            names.append((s[0], kind))
            j = s[1]
        if len(names) < 2 or b[j:j + 2] != b"\0\0":
            i = begin + 1 if not names else j
            continue
        j += 2
        vals: dict = {}
        ok = True
        for name, kind in reversed(names):
            if b[j:j + 1] != b"\x01":
                ok = False
                break
            j += 1
            s = _cstring(b, j)
            if s is not None and kind in ("String", ""):
                vals[name], j = s[0], s[1]
                continue
            size = 8 if kind == "Double" else 4 if kind in ("Integer", "Bool") else _value_len(name)
            last = name == names[0][0]
            fits = (lambda z: last or b[j + z:j + z + 1] == b"\x01")
            if not kind and not fits(size) and fits(12 - size):
                size = 12 - size
            if size == 8:
                vals[name] = _d(b, j)
            else:
                v = _i(b, j)
                vals[name] = bool(v) if kind == "Bool" or (not kind and v in (0, 1) and _value_len(name) == 4
                                                          and name[:1].isupper() and kind != "Integer"
                                                          and not name.endswith(("Type", "Color", "Number", "Id",
                                                                                 "Method", "Compensation"))) else v
            j += size
        if ok and vals:
            out.append((begin, vals))
            i = j
        else:
            i = begin + 1
    return out


def read_manager(b: bytes, toolpaths: list[ArtToolpath]) -> None:
    """What the toolpath manager adds (ArtCAM 2008 on): the start depth, the side and direction of a profile, the
    tool's name, type and stepover, and the rest of the named settings."""
    by_name: dict[str, list[ArtToolpath]] = {}
    for tp in toolpaths:
        by_name.setdefault(tp.name, []).append(tp)
    strings = _strings(b)
    where: list[tuple[int, ArtToolpath]] = []
    for s0, s1, text in strings:
        for tp in by_name.get(text, []):
            where.append((s0, tp))
            ds = [_d(b, s1 + 8 * k) for k in range(7)]
            if None in ds or tp.start_depth is not None:
                continue
            safe, hx, hy, hz, start, allow, tol = ds
            if (tp.safe_z is None or abs(safe - tp.safe_z) < 1e-6) and -1000 < start <= 0 and abs(allow) < 1000:
                tp.start_depth = -start
                tp.home = tp.home or (hx, hy, hz)
                tp.safe_z = safe if tp.safe_z is None else tp.safe_z
    where.sort(key=lambda x: x[0])

    def owner(pos: int) -> ArtToolpath | None:
        last = None
        for p, tp in where:
            if p > pos:
                break
            last = tp
        return last

    for pos, vals in named_lists(b):
        tp = owner(pos)
        if tp is not None:
            for k, v in vals.items():
                tp.values.setdefault(k, v)
    # the tool: [class] [number] [name] [1] [diameter] [stepdown] [description] [stepover] [spindle] [feed] [plunge] [units]
    tool_class = [(m.start(), m.group(1).decode()) for m in re.finditer(rb"\xff\xff\x01\x00.\x00(tpmDB_[A-Za-z]+)", b, re.S)]
    for s0, s1, text in strings:
        dia, sd = _d(b, s1 + 4), _d(b, s1 + 12)
        if _i(b, s1) is None or dia is None or sd is None or not (0 < dia < 500 and 0 < sd < 1000):
            continue
        desc = _cstring(b, s1 + 20)
        if desc is None:
            continue
        so, sp, fr, pr = (_d(b, desc[1] + 8 * k) for k in range(4))
        if None in (so, sp, fr, pr) or not (0 < sp < 200000 and fr > 0 and pr > 0):
            continue
        tp = owner(s0)
        if tp is None or "stepover" in tp.tool:
            continue
        cls = ""
        for p, c in tool_class:
            if p < s0:
                cls = c
        tp.tool.update({"name": text, "diameter": dia, "stepdown": sd, "stepover": so, "spindle": sp, "feed": fr,
                        "plunge": pr, "class": cls})
    for tp in toolpaths:
        md = tp.values.get("MaximumDepth")
        if isinstance(md, float) and 0 < md < 10000:
            tp.finish_depth = md
        st = tp.values.get("StartDepth")
        if isinstance(st, float) and 0 <= st < 10000:
            tp.start_depth = st


# ---------------------------------------------------------------------------------------------------------------
# a file
# ---------------------------------------------------------------------------------------------------------------

def _model_roots(cf: CompoundFile) -> list[str]:
    roots = []
    for p in cf.paths():
        if p.endswith("ArtCAM/Models/Model1/Parameters"):
            roots.append(p[:-len("ArtCAM/Models/Model1/Parameters")])
    return roots


def _toolpath_keys(cf: CompoundFile, root: str) -> list[str]:
    base = root + "ArtCAM/ToolpathData/"
    keys = [p[len(base):-len("/Data2")] for p in cf.paths() if p.startswith(base) and p.endswith("/Data2")
            and p[len(base):].count("/") == 1]
    order: list[str] = []
    try:
        raw = cf.read(base + "#UnicodeToolpathList")
        n, i = struct.unpack_from("<H", raw, 2)[0], 4
        for _ in range(n):
            ln = struct.unpack_from("<H", raw, i)[0]
            order.append(raw[i + 2:i + 2 + ln].decode("utf-16-le", "replace").rstrip("\0"))
            i += 2 + ln
    except (KeyError, struct.error, ValueError):
        pass
    known = [k for k in order if k in keys]
    return known + [k for k in keys if k not in known]


def _model_name(cf: CompoundFile, root: str, fallback: str) -> str:
    try:
        raw = cf.read(root + "Identity")
        n = struct.unpack_from("<I", raw, 0)[0]
        if 0 < n < 512:
            s = raw[4:4 + 2 * n].decode("utf-16-le").strip()
            if s:
                return s
    except (KeyError, struct.error, UnicodeDecodeError):
        pass
    return fallback


def _version(cf: CompoundFile, root: str) -> str:
    try:
        raw = cf.read(root + "ArtCAM/VersionInfo/VersionA")
    except KeyError:
        return ""
    m = re.search(rb"A\x00r\x00t\x00C\x00A\x00M\x00(?:[ -~]\x00)+", raw)
    return m.group().decode("utf-16-le").strip() if m else ""


def read_file(path: str | Path, data: bytes | None = None) -> list[ArtModel]:
    """The models of an ArtCAM file (``.art``: one, ``.3dp``: those of the project)."""
    data = data if data is not None else Path(path).read_bytes()
    if not is_cfb(data[:8]):
        raise ArtCamError("this is not an ArtCAM file (an ArtCAM model or project is a compound file)")
    try:
        cf = CompoundFile(data)
    except CfbError as exc:
        raise ArtCamError(f"the ArtCAM file is damaged: {exc}") from exc
    roots = _model_roots(cf)
    if not roots:
        raise ArtCamError("there is no ArtCAM model in this file")
    models = []
    for root in roots:
        p = cf.read(root + "ArtCAM/Models/Model1/Parameters")
        w, h, ox, oy = (_d(p, k) for k in (0x09, 0x11, 0x19, 0x21))
        if w is None or h is None or not (0 < w < 1e6 and 0 < h < 1e6):
            raise ArtCamError("the size of the ArtCAM model cannot be read")
        model = ArtModel(_model_name(cf, root, Path(path).stem), w, h, (ox or 0.0, oy or 0.0), _version(cf, root))
        model.has_relief = any(x.startswith(root + "ArtCAM/Reliefs/") for x in cf.paths())
        for key in _toolpath_keys(cf, root):
            tp = ArtToolpath(key)
            base = f"{root}ArtCAM/ToolpathData/{key}/"
            try:
                read_parameters(cf.read(base + "Parameters"), tp)
            except (KeyError, struct.error) as exc:
                tp.problems.append(f"its settings could not be read ({exc})")
            try:
                read_data2(cf.read(base + "Data2"), tp)
            except (KeyError, ValueError, struct.error) as exc:
                tp.problems.append(str(exc))
            model.toolpaths.append(tp)
        try:
            read_manager(cf.read(root + "ArtCAM/OA_ToolpathData/OA_TOOLPATH_MANAGER"), model.toolpaths)
        except KeyError:
            pass
        for tp in model.toolpaths:
            if tp.finish_depth is None and tp.z_levels:
                tp.finish_depth = -min(tp.z_levels)
            if tp.start_depth is None and tp.data_start is not None:
                tp.start_depth = tp.data_start
            if tp.start_depth is None and tp.z_levels:   # (equal passes: one step above the first)
                zs = sorted(tp.z_levels, reverse=True)
                step = (zs[0] - zs[1]) if len(zs) > 1 else 0.0
                tp.start_depth = max(0.0, -(zs[0] + step))
            if not tp.name:
                tp.name = key
            if tp.kind == "tpmOpenContourGroupDrivePicture" and ("ProfileOutside" in tp.values
                                                                 or "ClimbMill" in tp.values or not tp.values):
                tp.kind = "profile"
        models.append(model)
    return models


# ---------------------------------------------------------------------------------------------------------------
# into iyiCAM
# ---------------------------------------------------------------------------------------------------------------

TOOL_TYPES = (("ballnose", "ball_nose"), ("ball", "ball_nose"), ("bullnose", "bull_nose"), ("radius", "bull_nose"),
              ("vbit", "v_bit"), ("conical", "v_bit"), ("engrav", "engraving"), ("slotdrill", "end_mill"),
              ("endmill", "end_mill"), ("drill", "drill"))
PER_MINUTE = 60.0                       # ArtCAM keeps the feeds of a tool in mm/sec (whatever units it shows them in)


def _tool_type(t: dict) -> str:
    for text in (t.get("class", ""), t.get("name", "")):
        k = re.sub(r"[^a-z]", "", text.lower())
        for word, kind in TOOL_TYPES:
            if word in k:
                return kind
    return "end_mill"


def _segments(c: ArtContour):
    from ..core.geom import make_arc
    return [make_arc(s[1], s[2], s[3]) if s[0] == "arc" else make_arc(s[1], s[2], 0.0) for s in c.segments
            if math.dist(s[1], s[2]) > 1e-9]


def _side(tp: ArtToolpath) -> str:
    if tp.values.get("ProfileAlong"):
        return "on"
    out = tp.values.get("ProfileOutside")
    return "inside" if out is False else "outside"


def vectors_of(tp: ArtToolpath) -> list[tuple[set[int], list]]:
    """The vectors the toolpath was made on: its line offset back (a profile), or the line itself; each with the ArtCAM
    vector ids it is made of."""
    from ..core import clip, vectorops
    from ..core.geom import BBox, Contour
    cs = [(c.vector_id, Contour(_segments(c), c.closed)) for c in tp.contours]
    cs = [(v, c) for v, c in cs if c.segs]
    if tp.kind != "profile":
        return [({v}, [c]) for v, c in cs]
    side = _side(tp)
    r = float(tp.tool.get("diameter") or 0.0) / 2.0
    d = r + (tp.allowance or 0.0)
    closed = [(v, c) for v, c in cs if c.closed]
    out = [({v}, [c]) for v, c in cs if not c.closed]      # (an open vector is cut along: the line is the vector)
    if side == "on" or abs(d) < 1e-9:
        return out + [({v}, [c]) for v, c in closed]
    # every part (an outline and the holes in it) on its own: an outside cut runs outside the outline and inside the
    # holes, so the part is the region inside the line, shrunk by d (an inside cut: grown by d)
    flat = [(v, c.flatten(vectorops.FLAT_TOL)) for v, c in closed]
    groups = clip.polytree(clip.normalize([f for _v, f in flat], "evenodd"))
    boxes = [BBox.of_points(outer).inflated(0.01) for outer, _h in groups]
    owners: list[set[int]] = [set() for _ in groups]
    for v, f in flat:                                     # (which ArtCAM vectors each part is made of)
        if not f:
            continue
        q, best, at = f[0], math.inf, None
        for k, (outer, holes) in enumerate(groups):
            if boxes[k].contains(q):
                dd = min(math.dist(q, p) for poly in [outer] + holes for p in poly)
                if dd < best:
                    best, at = dd, k
        if at is not None:
            owners[at].add(v)
    for k, (outer, holes) in enumerate(groups):
        got = vectorops.polys_to_contours(clip.offset([outer] + holes, -d if side == "outside" else d, "round"))
        if got:
            out.append((owners[k], got))
    return out


def fill_project(models: list[ArtModel], project, sheet=None, report: ImportReport | None = None,
                 thickness: float | None = None) -> list:
    """Put the models into ``project``: sheets with the vectors and toolpaths.  ``sheet``: an empty sheet that the
    first one takes the place of (the blank sheet of a new project).  Returns the vectors."""
    from ..core.document import Layer, Sheet, ToolpathDef, VectorObject, Selection
    from ..core.geom import Transform
    from ..core.tooling import Tool
    report = report if report is not None else ImportReport()
    created: list = []
    reuse = sheet if sheet is not None and not sheet.vectors else None
    first_toolpaths = not project.toolpaths
    layer = project.layers[0] if project.layers else None
    if layer is None:
        layer = Layer("Default")
        project.layers.append(layer)
    other_layer = None
    guessed: list[str] = []
    for model in models:
        T = Transform.translate(-model.origin[0], -model.origin[1])
        made_tp: dict = {}
        # sheets: the toolpaths that cut the same vectors together
        groups: list[list[ArtToolpath]] = []
        for tp in model.toolpaths:
            if not tp.contours:
                report.error(f"{model.name} / {tp.name}: {'; '.join(tp.problems) or 'nothing to cut'}")
                continue
            hit = [g for g in groups if any(t.vector_ids & tp.vector_ids for t in g)]
            if not hit:
                groups.append([tp])
                continue
            for g in hit[1:]:
                hit[0].extend(g)
                groups.remove(g)
            hit[0].append(tp)
        if not groups:
            groups = [[]]
        for group in groups:
            name = ", ".join(dict.fromkeys(t.name for t in group)) if group else model.name
            if len(models) > 1 and group:
                name = f"{model.name}: {name}"
            depth = max((t.finish_depth or 0.0 for t in group), default=0.0)
            th = thickness or (depth if depth > 0 else None) or project.material.thickness
            if not thickness and depth > 0:
                guessed.append(f"{name} {depth:g} mm")
            if reuse is not None:
                s, reuse = reuse, None
                s.name, s.width, s.height, s.thickness = name, model.width, model.height, th
            else:
                s = Sheet(name, model.width, model.height, thickness=th)
                project.sheets.append(s)
            made: dict[int, VectorObject] = {}            # ArtCAM vector id -> the vector made of it
            for tp in group:
                ids: list[str] = []
                if tp.kind != "profile" and other_layer is None:
                    other_layer = project.layer_by_name("ArtCAM") or Layer("ArtCAM", "#d62728")
                    if other_layer not in project.layers:
                        project.layers.append(other_layer)
                for vids, cs in vectors_of(tp):
                    if vids and all(v in made for v in vids):   # (a vector another toolpath brought already)
                        ids += [i for i in dict.fromkeys(made[v].id for v in vids) if i not in ids]
                        continue
                    o = VectorObject(cs, (other_layer if tp.kind != "profile" else layer).id, name=tp.name)
                    o.transform(T)
                    s.vectors.append(o)
                    created.append(o)
                    ids.append(o.id)
                    for v in vids:
                        made.setdefault(v, o)
                if tp.kind != "profile":
                    report.skip(f"ArtCAM toolpath of another kind ({tp.kind}): its line is brought in as vectors")
                    continue
                made_tp[tp.key] = _toolpath(tp, s, ids, report, Tool, ToolpathDef, Selection)
                report.read["ArtCAM profile"] += 1
            report.read["vector"] += sum(1 for v in s.vectors)
        project.toolpaths += [made_tp[t.key] for t in model.toolpaths if t.key in made_tp]   # (in ArtCAM's order)
        if model.has_relief:
            report.notes.append(f"{model.name}: the relief (3D) of the model is not read (iyiCAM machines 2D).")
        if first_toolpaths and model.toolpaths:
            tp0 = model.toolpaths[0]
            if tp0.safe_z is not None and tp0.safe_z > 0:
                project.material.safe_z = tp0.safe_z
            if tp0.home is not None:
                project.material.home = (tp0.home[0] - model.origin[0], tp0.home[1] - model.origin[1], tp0.home[2])
            project.material.z_zero = "top"
    if guessed:
        report.notes.append("ArtCAM does not keep the thickness of the material with the toolpaths: each sheet is as thick "
                            "as its deepest cut (" + ", ".join(guessed) + "); change it on the sheet if the material is "
                            "thicker (the depths of the toolpaths are as in ArtCAM, from the top).")
    report.notes.append("ArtCAM keeps the vectors of a model encrypted: the vectors here are those the toolpaths cut, "
                        "taken from the toolpaths (vectors that no toolpath uses are not brought in).")
    report.via = ", ".join(dict.fromkeys(m.version for m in models if m.version))
    if models:
        report.page = (models[0].width, models[0].height)
    return created


def _toolpath(tp: ArtToolpath, sheet, ids: list[str], report: ImportReport, Tool, ToolpathDef, Selection):
    t = tp.tool
    per_min = PER_MINUTE
    dia = float(t.get("diameter") or 6.0)
    tool = Tool(name=t.get("name") or f"End Mill {dia:g} mm", type=_tool_type(t), diameter=dia,
                number=int(t.get("number") or 1), stepdown=float(t.get("stepdown") or dia),
                stepover=float(t.get("stepover") or dia * 0.4),
                feed_rate=round(float(t.get("feed") or 50.0) * per_min, 3),
                plunge_rate=round(float(t.get("plunge") or 20.0) * per_min, 3),
                spindle_speed=int(round(float(t.get("spindle") or 18000))), group="ArtCAM")
    v = tp.values
    params = {"side": _side(tp), "direction": "climb" if v.get("ClimbMill", True) else "conventional",
              "allowance": round(tp.allowance, 6), "start_depth": round(tp.start_depth or 0.0, 6), "through": False,
              "cut_depth": round(tp.finish_depth or 0.0, 6), "stepdown": float(t.get("stepdown") or 0.0),
              "sharp_corners": bool(v.get("SharpOffset", False))}
    if v.get("DoRamping"):
        params["ramp"] = "linear"
        if isinstance(v.get("RampMaxZigLength"), float) and v["RampMaxZigLength"] > 0:
            params["ramp_length"] = v["RampMaxZigLength"]
    if v.get("DoLeadInOut"):
        circ = v.get("DoCircularLeadInOut")
        params["lead"] = "arc" if circ else "line"
        ln = v.get("LeadInOutRadiusMM") if circ else v.get("LeadInOutDistanceMM")
        if isinstance(ln, float) and ln > 0:
            params["lead_length"] = ln
        if isinstance(v.get("LeadInOutoverCutMM"), float) and v["LeadInOutoverCutMM"] > 0:
            params["overlap"] = v["LeadInOutoverCutMM"]
    if v.get("AddBridges"):
        params["tabs"] = True
        n = v.get("BridgeNumber")
        if isinstance(n, (int, float)) and not isinstance(n, bool) and n >= 1:
            params["tab_mode"], params["tab_count"] = "count", int(round(n))
        if isinstance(v.get("BridgeLength"), float) and v["BridgeLength"] > 0:
            params["tab_length"] = v["BridgeLength"]
        if isinstance(v.get("BridgeThickness"), float) and v["BridgeThickness"] > 0:
            params["tab_height"] = v["BridgeThickness"]
        params["tab_3d"] = bool(v.get("BridgeIs3d", False))
    td = ToolpathDef(tp.name, "profile", tool, params, Selection("vectors", list(ids)), sheet_ids=[sheet.id])
    if tp.safe_z is not None and tp.safe_z > 0:
        td.safe_z = tp.safe_z
    return td


def read_project(path: str | Path, report: ImportReport | None = None, data: bytes | None = None):
    """An ArtCAM model or project as a new iyiCAM project."""
    from ..core.document import Project
    models = read_file(path, data)
    p = Project(models[0].name if len(models) == 1 else Path(path).stem)
    fill_project(models, p, p.sheets[0], report)
    return p


def import_artcam(path: str | Path, project, sheet, report: ImportReport | None = None, **_options) -> list:
    """The importer (File > Import, and File > Open: a new project): the models' sheets are added to ``project`` (an
    empty ``sheet`` is used for the first)."""
    models = read_file(path)
    blank = sheet if (sheet is not None and not sheet.vectors and not project.toolpaths) else None
    return fill_project(models, project, blank, report)

"""CorelDRAW (``.cdr``) files, read through a converter program.

CorelDRAW's format is closed and changes with every version; reading it is a project of its own (libcdr, the library
behind LibreOffice's and Inkscape's import, reads CorelDRAW 7 to X8).  iyiCAM does not repeat that work: it asks a
program that already reads it for an SVG, and imports the SVG with its own, exact SVG importer (millimetres to the
micron, arcs, beziers).  The programs, tried in this order, the first that gives drawing wins:

``inkscape``    Inkscape 1.x. Text is turned into curves (``object-to-path``): what a CNC job needs.
``cdr2xhtml``   the libcdr tools (Linux: ``libcdr-tools``). Geometry only, text is left out.
``libreoffice`` LibreOffice. Geometry only, text is left out; its fill-and-outline duplicates are cleaned away.

``IYICAM_CDR_CONVERTER`` names a program of your own: a command with ``{input}`` and ``{output}`` in it (a file of
SVG is expected at ``{output}``; without ``{output}`` the SVG is read from the program's standard output).
``IYICAM_CDR_TIMEOUT`` is how many seconds all of it may take (default 60).

What cannot be read (CorelDRAW 2018 and newer, bitmaps, effects, text without Inkscape) is said, never dropped silently.
"""
from __future__ import annotations

import os
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
import time
import zipfile
import xml.etree.ElementTree as ET
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from ..core.document import Project, Sheet, VectorObject
from .report import ImportReport

SVG_NS = "http://www.w3.org/2000/svg"
TIMEOUT = 60.0                   # seconds for the whole conversion (a relay waits 90)
MAX_TIMEOUT = 600.0
MAX_BYTES = 200 * 1024 * 1024
LAST_READ_VERSION = 18           # CorelDRAW X8: the newest version libcdr reads

VERSION_NAMES = {7: "7", 8: "8", 9: "9", 10: "10", 11: "11", 12: "12", 13: "X3", 14: "X4", 15: "X5", 16: "X6",
                 17: "X7", 18: "X8", 19: "2017", 20: "2018", 21: "2019", 22: "2020", 23: "2021", 24: "2022",
                 25: "2023", 26: "2024"}

INSTALL_HINT = ("Install Inkscape (inkscape.org: best, text becomes curves) or LibreOffice (libreoffice.org), or on Linux "
                "the libcdr tools (apt install libcdr-tools). Or export the drawing from CorelDRAW as DXF or SVG "
                "(File > Export).")


class CdrError(ValueError):
    """A CorelDRAW file that could not be read, with a stable ``code`` and the words for the person."""

    def __init__(self, code: str, message: str, **params):
        super().__init__(message)
        self.code, self.params = code, params


class _Failed(Exception):
    """One converter could not do it (the next one is tried)."""


# ---------------------------------------------------------------------------------------------------------------
# the file
# ---------------------------------------------------------------------------------------------------------------

def _version_of(head: bytes) -> int | None:
    """The version in a RIFF header: ``RIFF`` size ``CDR`` + a character ('7'..'9', then 'A' = 10, 'D' = 13 ...)."""
    if head[:4] != b"RIFF" or head[8:11].upper() != b"CDR":
        return None
    ch = head[11:12].decode("latin-1")
    if ch.isdigit():
        return int(ch)
    if ch.isalpha() and ch.upper() != "X":
        return ord(ch.upper()) - 55
    return None


def version_name(version: int | None) -> str:
    return VERSION_NAMES.get(version, str(version)) if version else "?"


def sniff(path: str | Path) -> dict:
    """What kind of CorelDRAW file this is: ``{"container": "riff"|"zip", "version": 13 or None}``, or a CdrError that
    says what it is instead (an archive, a CMX file, something else)."""
    p = Path(path)
    try:
        size = p.stat().st_size
        with open(p, "rb") as f:
            head = f.read(16)
    except OSError as exc:
        raise CdrError("cdr_unreadable", f"the file cannot be opened: {exc}") from exc
    if size < 16:
        raise CdrError("cdr_not_cdr", "the file is empty or too small to be a CorelDRAW drawing")
    if size > MAX_BYTES:
        raise CdrError("cdr_too_large", f"the file is {size // 1048576} MB; the limit is {MAX_BYTES // 1048576} MB")
    if head[:4] == b"RIFF":
        if head[8:11].upper() == b"CDR":
            return {"container": "riff", "version": _version_of(head)}
        raise CdrError("cdr_not_cdr", f"this is a RIFF file of another kind ({head[8:12].decode('latin-1', 'replace')!r})"
                                      f", not a CorelDRAW drawing (CMX presentation files are not read)")
    if head[:2] == b"PK":                       # CorelDRAW X4 and later keep a drawing in a ZIP package
        try:
            with zipfile.ZipFile(p) as z:
                names = z.namelist()
                riff = next((n for n in names if n.lower().endswith("riffdata.cdr")), None)
                if riff or any(n.lower().endswith("root.dat") for n in names):
                    version = None
                    if riff:
                        with z.open(riff) as inner:
                            version = _version_of(inner.read(16))
                    return {"container": "zip", "version": version}
                inner_cdr = [n for n in names if n.lower().endswith(".cdr")]
        except zipfile.BadZipFile as exc:
            raise CdrError("cdr_not_cdr", "this is a damaged ZIP file, not a CorelDRAW drawing") from exc
        if inner_cdr:
            raise CdrError("cdr_archive", f"this is an archive that holds {inner_cdr[0]}: extract it first",
                           inner=inner_cdr[0])
        raise CdrError("cdr_not_cdr", "this is a ZIP file, not a CorelDRAW drawing")
    raise CdrError("cdr_not_cdr", "this file does not start like a CorelDRAW drawing")


# ---------------------------------------------------------------------------------------------------------------
# the converters
# ---------------------------------------------------------------------------------------------------------------

@dataclass
class Converter:
    name: str                 # inkscape | cdr2xhtml | libreoffice | custom
    exe: str
    version: str = ""

    def describe(self) -> dict:
        return {"name": self.name, "path": self.exe, "version": self.version}


def _program_dirs(*sub: str) -> list[str]:
    out = []
    for var in ("ProgramFiles", "ProgramFiles(x86)", "LOCALAPPDATA"):
        base = os.environ.get(var)
        if base:
            out.append(os.path.join(base, *sub))
            if var == "LOCALAPPDATA":
                out.append(os.path.join(base, "Programs", *sub))
    return out


def _find(names: tuple[str, ...], extra: list[str]) -> str | None:
    for n in names:
        p = shutil.which(n)
        if p:
            return p
    return next((p for p in extra if p and os.path.isfile(p)), None)


_version_cache: dict[str, str] = {}


def _inkscape_version(exe: str) -> str:
    if exe in _version_cache:
        return _version_cache[exe]
    try:
        r = subprocess.run([exe, "--version"], capture_output=True, text=True, timeout=30, stdin=subprocess.DEVNULL,
                           **_no_window())
        m = re.search(r"Inkscape\s+(\d+)\.(\d+)(?:\.(\d+))?", r.stdout + r.stderr)
        _version_cache[exe] = ".".join(g for g in m.groups() if g) if m else ""
    except (OSError, subprocess.SubprocessError):
        _version_cache[exe] = ""
    return _version_cache[exe]


def _no_window() -> dict:
    """A window of a console program must not flash up in front of a program with windows (Windows)."""
    return {"creationflags": subprocess.CREATE_NO_WINDOW} if sys.platform == "win32" else {}


def find_converters() -> list[Converter]:
    """The converter programs found on this computer, in the order they are tried."""
    out: list[Converter] = []
    custom = os.environ.get("IYICAM_CDR_CONVERTER", "").strip()
    if custom:
        out.append(Converter("custom", custom))
    ink = _find(("inkscape.com", "inkscape"), [*_program_dirs("Inkscape", "bin", "inkscape.com"),
                                                "/Applications/Inkscape.app/Contents/MacOS/inkscape"])
    if ink:
        v = _inkscape_version(ink)
        if not v or int(v.split(".")[0]) >= 1:         # 0.92 has other options: not used
            out.append(Converter("inkscape", ink, v))
    x = _find(("cdr2xhtml",), [])
    if x:
        out.append(Converter("cdr2xhtml", x))
    lo = _find(("soffice", "libreoffice", "soffice.exe"), [*_program_dirs("LibreOffice", "program", "soffice.exe"),
                                                           "/Applications/LibreOffice.app/Contents/MacOS/soffice"])
    if lo:
        out.append(Converter("libreoffice", lo))
    return out


def describe() -> dict:
    """For a screen that tells whether CorelDRAW files can be opened here."""
    found = find_converters()
    return {"available": bool(found), "converters": [c.describe() for c in found], "install": INSTALL_HINT,
            "reads": f"CorelDRAW 7 to {VERSION_NAMES[LAST_READ_VERSION]}"}


class _Budget:
    """How long the conversion may still take, and whether somebody has pressed Cancel."""

    def __init__(self, seconds: float, cancel: Callable[[], bool] | None = None):
        self.deadline = time.monotonic() + seconds
        self.cancel = cancel

    def left(self) -> float:
        return self.deadline - time.monotonic()

    def check(self) -> None:
        if self.cancel is not None and self.cancel():
            raise CdrError("cdr_cancelled", "reading the CorelDRAW file was cancelled")
        if self.left() <= 0:
            raise CdrError("cdr_timeout", "reading the CorelDRAW file took too long (the converter was stopped)")


def _kill_tree(proc: subprocess.Popen) -> None:
    """Stop a converter and what it started (LibreOffice's launcher starts the program that does the work)."""
    try:
        if sys.platform == "win32":
            subprocess.run(["taskkill", "/F", "/T", "/PID", str(proc.pid)], capture_output=True, timeout=10,
                           **_no_window())
        else:
            os.killpg(proc.pid, 9)
    except (OSError, subprocess.SubprocessError):
        pass
    try:
        proc.kill()
    except OSError:
        pass
    try:
        proc.wait(timeout=5)
    except subprocess.SubprocessError:
        pass


def _run(args: list[str], budget: _Budget, cwd: Path, env: dict | None = None) -> subprocess.CompletedProcess:
    budget.check()
    extra = _no_window() if sys.platform == "win32" else {"start_new_session": True}   # (its own group: killed as one)
    try:
        proc = subprocess.Popen(args, cwd=str(cwd), env=env, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                stderr=subprocess.PIPE, **extra)
    except OSError as exc:
        raise _Failed(f"cannot be started ({exc})") from exc
    try:
        while True:
            budget.check()
            try:
                out, err = proc.communicate(timeout=max(0.01, min(0.2, budget.left())))
                return subprocess.CompletedProcess(args, proc.returncode, out, err)
            except subprocess.TimeoutExpired:
                continue
    except BaseException:           # (cancelled, out of time, or an error in the program that is waiting)
        _kill_tree(proc)
        raise


def _tail(res: subprocess.CompletedProcess) -> str:
    text = (res.stderr or b"").decode("utf-8", "replace")
    lines = [ln.strip() for ln in text.splitlines() if ln.strip() and "GtkRecentManager" not in ln]
    return lines[-1][:200] if lines else f"exit code {res.returncode}"


def _read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="replace")


def unwrap_xhtml(data: bytes) -> str:
    """``cdr2xhtml`` writes an XHTML page with the SVG inside it (as ``svg:`` elements): the SVG, on its own."""
    try:
        root = ET.fromstring(data)
        svg = next((e for e in root.iter() if e.tag == f"{{{SVG_NS}}}svg"), None)
    except ET.ParseError:
        svg = None
    if svg is None:
        text = data.decode("utf-8", "replace")
        m = re.search(r"<svg:svg\b.*</svg:svg>", text, re.S)
        if not m:
            raise _Failed("did not write a drawing")
        return ('<?xml version="1.0" encoding="UTF-8"?>\n' + m.group(0).replace("<svg:", "<").replace("</svg:", "</")
                .replace("xmlns:svg=", "xmlns="))
    ET.register_namespace("", SVG_NS)
    ET.register_namespace("xlink", "http://www.w3.org/1999/xlink")
    return '<?xml version="1.0" encoding="UTF-8"?>\n' + ET.tostring(svg, encoding="unicode")


def clean_libreoffice_svg(text: str) -> str:
    """LibreOffice writes every shape with an outline and a fill as two paths of the same outline, and gives each shape
    an invisible bounding rectangle: cut as they are, everything would be cut twice or more."""
    try:
        root = ET.fromstring(text.encode("utf-8"))
    except ET.ParseError as exc:
        raise _Failed(f"wrote a file that is not SVG ({exc})") from exc
    ET.register_namespace("", SVG_NS)
    ET.register_namespace("xlink", "http://www.w3.org/1999/xlink")
    path_tag, rect_tag = f"{{{SVG_NS}}}path", f"{{{SVG_NS}}}rect"
    for parent in list(root.iter()):
        seen: set[str] = set()
        for child in list(parent):
            if child.tag == rect_tag and child.get("class") == "BoundingBox":
                parent.remove(child)
            elif child.tag == path_tag:
                key = re.sub(r"\s+", " ", child.get("d", "")).strip()
                if key in seen:
                    parent.remove(child)
                elif key:
                    seen.add(key)
    return '<?xml version="1.0" encoding="UTF-8"?>\n' + ET.tostring(root, encoding="unicode")


def _via_inkscape(conv: Converter, src: Path, tmp: Path, budget: _Budget, text_to_curves: bool) -> str:
    out = tmp / "inkscape.svg"
    env = dict(os.environ, INKSCAPE_PROFILE_DIR=str(tmp / "inkscape-profile"))   # (leave the person's settings alone)
    plain = [conv.exe, "--export-type=svg", "--export-plain-svg", f"--export-filename={out}"]
    attempts = ([[conv.exe, "--actions=select-all:all;object-to-path", *plain[1:]]] if text_to_curves else []) + [plain]
    last = "wrote nothing"
    for args in attempts:
        out.unlink(missing_ok=True)
        res = _run([*args, str(src)], budget, tmp, env)
        if out.is_file() and out.stat().st_size > 0:
            return _read_text(out)
        last = _tail(res)
    raise _Failed(last)


def _via_cdr2xhtml(conv: Converter, src: Path, tmp: Path, budget: _Budget, text_to_curves: bool) -> str:
    res = _run([conv.exe, str(src)], budget, tmp)
    if res.returncode != 0 or not res.stdout.strip():
        raise _Failed(_tail(res))
    return unwrap_xhtml(res.stdout)


def _via_libreoffice(conv: Converter, src: Path, tmp: Path, budget: _Budget, text_to_curves: bool) -> str:
    outdir = tmp / "lo-out"
    outdir.mkdir(exist_ok=True)
    profile = (tmp / "lo-profile").as_uri()                     # its own profile: it does not meet a LibreOffice that is open
    res = _run([conv.exe, f"-env:UserInstallation={profile}", "--headless", "--norestore", "--convert-to", "svg",
                "--outdir", str(outdir), str(src)], budget, tmp)
    out = outdir / (src.stem + ".svg")
    if not out.is_file() or out.stat().st_size == 0:
        raise _Failed(_tail(res))
    return clean_libreoffice_svg(_read_text(out))


def _via_custom(conv: Converter, src: Path, tmp: Path, budget: _Budget, text_to_curves: bool) -> str:
    out = tmp / "custom.svg"
    template = conv.exe
    parts = shlex.split(template, posix=os.name != "nt")
    args = [p.replace("{input}", str(src)).replace("{output}", str(out)) for p in parts]
    res = _run(args, budget, tmp)
    if "{output}" in template:
        if not out.is_file() or out.stat().st_size == 0:
            raise _Failed(_tail(res))
        return _read_text(out)
    if not res.stdout.strip():
        raise _Failed(_tail(res))
    return res.stdout.decode("utf-8", "replace")


_RUNNERS = {"inkscape": _via_inkscape, "cdr2xhtml": _via_cdr2xhtml, "libreoffice": _via_libreoffice, "custom": _via_custom}


# ---------------------------------------------------------------------------------------------------------------
# the importer
# ---------------------------------------------------------------------------------------------------------------

def import_cdr(path: str | Path, project: Project, sheet: Sheet, layer_id: str | None = None, scale: float = 1.0,
               text_to_curves: bool = True, converter: str | None = None, timeout: float | None = None,
               keep_layers: bool = False, cancel: Callable[[], bool] | None = None,
               report: ImportReport | None = None) -> list[VectorObject]:
    """Read a CorelDRAW drawing into ``sheet``; returns the vectors made.

    ``scale``: 1.0 keeps the size the drawing has in CorelDRAW.
    ``text_to_curves``: turn text into curves (only Inkscape can; without it text is left out and reported).
    ``converter``: use only this one (``inkscape`` / ``cdr2xhtml`` / ``libreoffice`` / ``custom``).
    ``timeout``: seconds for all of it. ``cancel``: a function that says True when the person has had enough.
    ``keep_layers``: put the vectors on layers named after the drawing's (CorelDRAW's layers do not survive the
    conversion with every program; by default everything goes on ``layer_id``)."""
    from .svg import import_svg
    info = sniff(path)
    label = f"CorelDRAW {version_name(info['version'])}" if info["version"] else "CorelDRAW"
    if report is None:
        report = ImportReport()
    candidates = [c for c in find_converters() if converter in (None, "", c.name)]
    if not candidates:
        raise CdrError("cdr_no_converter",
                       ("There is no converter named " + converter + " here. " if converter else
                        "A CorelDRAW file is read through a converter program and none was found on this computer. ")
                       + INSTALL_HINT)
    try:
        limit = float(timeout if timeout is not None else os.environ.get("IYICAM_CDR_TIMEOUT") or TIMEOUT)
    except (TypeError, ValueError):
        limit = TIMEOUT
    limit = min(max(limit, 1.0), MAX_TIMEOUT)         # (a remote caller may not ask for hours)
    budget = _Budget(limit, cancel)
    failures: list[str] = []
    with tempfile.TemporaryDirectory(prefix="iyicam-cdr-") as tmpdir:
        tmp = Path(tmpdir)
        src = tmp / "drawing.cdr"            # a plain name: no converter has to cope with odd characters in a path
        shutil.copyfile(path, src)
        for conv in candidates:
            try:
                svg_text = _RUNNERS[conv.name](conv, src, tmp, budget, text_to_curves)
                svg_file = tmp / f"{conv.name}-clean.svg"
                svg_file.write_text(svg_text, encoding="utf-8")
                sub = ImportReport()
                objs = import_svg(svg_file, project, sheet, layer_id, keep_layers=keep_layers, scale=scale, report=sub)
            except _Failed as exc:
                failures.append(f"{conv.name}: {exc}")
                continue
            except CdrError:
                raise
            except Exception as exc:  # noqa: BLE001  (a converter's file that the SVG reader cannot take)
                failures.append(f"{conv.name}: the SVG it wrote could not be read ({type(exc).__name__}: {exc})")
                continue
            if not objs:
                failures.append(f"{conv.name}: the drawing it wrote is empty")
                continue
            report.read.update(sub.read)
            report.skipped.update(sub.skipped)
            report.errors.extend(sub.errors)
            report.notes.extend(sub.notes)
            report.via = f"{label} ({conv.name})"
            if sub.skipped.get("TEXT"):
                report.notes.append("Text was left out: in CorelDRAW turn it into curves (Arrange > Convert To Curves, "
                                    "Ctrl+Q) and save again, or install Inkscape, which converts it while reading.")
            if sub.skipped.get("IMAGE"):
                report.notes.append("Bitmaps in the drawing are not imported.")
            return objs
    newer = info["version"] and info["version"] > LAST_READ_VERSION
    why = "; ".join(failures)
    if newer:
        raise CdrError("cdr_version", f"This file was saved by CorelDRAW {version_name(info['version'])}, newer than "
                       f"the converters read (CorelDRAW 7 to {VERSION_NAMES[LAST_READ_VERSION]}). In CorelDRAW use File > "
                       f"Save As and choose an older version (X8), or export the drawing as DXF or SVG. ({why})",
                       version=info["version"], name=version_name(info["version"]), why=why)
    raise CdrError("cdr_failed", f"None of the converters could read this CorelDRAW file: {why}. If it is a drawing of a "
                                 f"newer or an unusual CorelDRAW, export it from CorelDRAW as DXF or SVG.", reasons=failures, why=why)

"""Making and changing post-processors without writing TOML by hand.

An interface (the page, the desktop program, a script) edits a **draft**: the post as a plain dictionary, with every
option and block as the post has it (``{"id", "name", "description", "extension", "extends", "options", "blocks"}``).
This module

* describes every option, block and variable in a few words, in English and Turkish (:func:`specs`), so a form can be
  built from it and a new option of the engine cannot be forgotten (a test checks that);
* checks a draft (:func:`clean_draft`) and says what is wrong in words;
* saves it as a user post, ``~/.iyicam/posts/<id>.toml``, writing only what differs from the post it is based on
  (``extends``), so a later change of the built-in post still reaches it (:func:`save_user_post`);
* makes the post of a draft without writing anything (:func:`build_post`) and tries it on a small sample job that
  has arcs, a drilling and two tools (:func:`sample_files`), so the result of every change can be seen at once.
"""
from __future__ import annotations

import functools
import math
import os
import re
import string
import threading
import tomllib
import unicodedata
from pathlib import Path

from ..core import paths
from .engine import DEFAULT_BLOCKS, DEFAULT_OPTIONS, PostProcessor, checked_options
from .loader import builtin_definitions, read_definition

LANGS = ("en", "tr")


class PostEditError(ValueError):
    """A draft that cannot be used; the message says what to change."""


# --------------------------------------------------------------------------------------------------
# what every option, block and variable is
# --------------------------------------------------------------------------------------------------

def _t(en: str, tr: str) -> dict:
    return {"en": en, "tr": tr}


GROUPS = [
    ("units", _t("Units and numbers", "Birim ve sayılar")),
    ("arcs", _t("Arcs (I J K, R)", "Yaylar (I J K, R)")),
    ("lines", _t("How lines are written", "Satırların yazılışı")),
    ("machine", _t("Machine", "Makine")),
    ("rotary", _t("Rotary axis", "Döner eksen")),
]


def _o(key, group, kind, en, tr, hen, htr, choices=None, lo=None, hi=None) -> dict:
    d = {"key": key, "group": group, "kind": kind, "label": _t(en, tr), "help": _t(hen, htr),
         "default": DEFAULT_OPTIONS[key]}
    if choices:
        d["choices"] = [[v, _t(a, b)] for v, a, b in choices]
    if lo is not None:
        d["min"] = lo
    if hi is not None:
        d["max"] = hi
    return d


OPTIONS: list[dict] = [
    _o("units", "units", "choice", "Units", "Birim",
       "What the numbers of the G-code are: millimetres (G21) or inches (G20). Coordinates and feeds are converted.",
       "G-code sayılarının birimi: milimetre (G21) ya da inç (G20). Koordinatlar ve hızlar çevrilir.",
       [("mm", "Millimetres", "Milimetre"), ("inch", "Inches", "İnç")]),
    _o("decimals", "units", "int", "Decimals of coordinates", "Koordinatın ondalık sayısı",
       "X10.123 has 3. Fewer is shorter, more is finer; 3 suits millimetres, 4 inches.",
       "X10.123'te 3 var. Az olursa kısa, çok olursa ince olur; milimetre için 3, inç için 4 uygundur.", lo=0, hi=8),
    _o("feed_decimals", "units", "int", "Decimals of feed rates", "Hızın ondalık sayısı",
       "F2000 has 0. (Inches get one more.)", "F2000'de 0 var. (İnçte bir fazla olur.)", lo=0, hi=4),
    _o("trailing_zeros", "units", "bool", "Keep trailing zeros (X10.500)", "Sondaki sıfırlar kalsın (X10.500)",
       "Some controllers want every number with all its decimals.",
       "Bazı kontrolcüler her sayının tüm ondalıklarıyla yazılmasını ister."),
    _o("leading_zero", "units", "bool", "Leading zero (X0.5)", "Baştaki sıfır (X0.5)",
       "Off writes X.5 instead of X0.5.", "Kapalıysa X0.5 yerine X.5 yazılır."),
    _o("integer_point", "units", "bool", "Point after whole numbers (X10.)", "Tam sayılardan sonra nokta (X10.)",
       "Fanuc style: X10. means ten millimetres, X10 may mean ten thousandths on some controllers.",
       "Fanuc usulü: X10. on milimetre demektir; bazı kontrolcülerde X10 binde on olabilir."),
    _o("g_leading_zero", "units", "bool", "G00 / G01 instead of G0 / G1", "G0 / G1 yerine G00 / G01",
       "Some controllers need two digits.", "Bazı kontrolcüler iki hane ister."),
    _o("line_ending", "units", "choice", "Line ending", "Satır sonu",
       "Windows controllers (Mach3, NcStudio) usually want CRLF.",
       "Windows kontrolcüleri (Mach3, NcStudio) genelde CRLF ister.",
       [("lf", "LF (Linux, macOS)", "LF (Linux, macOS)"), ("crlf", "CRLF (Windows)", "CRLF (Windows)")]),

    _o("arcs", "arcs", "choice", "How arcs are written", "Yayların yazılışı",
       "I J: the centre of the arc as a distance from where it starts (most controllers). R: the radius (some "
       "older ones). Lines only: no G2 / G3 at all, the arc becomes many short straight moves.",
       "I J: yayın merkezi, yayın başladığı yerden uzaklık olarak (çoğu kontrolcü). R: yarıçap (bazı eski "
       "kontrolcüler). Yalnız çizgi: hiç G2 / G3 yok, yay çok sayıda kısa düz harekete bölünür.",
       [("ijk", "I J K (centre)", "I J K (merkez)"), ("r", "R (radius)", "R (yarıçap)"),
        ("none", "Short lines only", "Yalnız kısa çizgiler")]),
    _o("ijk", "arcs", "choice", "I J are counted from", "I J şuradan sayılır",
       "Incremental: I and J are how far the centre is from the START of the arc (G91.1, what almost every "
       "controller expects). Absolute: I and J are the centre's own place on the sheet (G90.1).",
       "Artımlı: I ve J, merkezin yayın BAŞLANGICINDAN ne kadar uzakta olduğudur (G91.1, hemen her kontrolcünün "
       "beklediği). Mutlak: I ve J merkezin sacdaki kendi yeridir (G90.1).",
       [("incremental", "The start of the arc (incremental)", "Yayın başlangıcı (artımlı)"),
        ("absolute", "The origin (absolute)", "Sıfır noktası (mutlak)")]),
    _o("arc_max_sweep", "arcs", "float", "Longest arc (degrees)", "En uzun yay (derece)",
       "A larger arc is split in equal parts. 180 keeps every arc to a half circle; R arcs never pass 90.",
       "Daha büyük yay eşit parçalara bölünür. 180, her yayı yarım çemberde tutar; R yayları 90'ı geçmez.",
       lo=10, hi=360),
    _o("arc_min_radius", "arcs", "float", "Smallest arc radius", "En küçük yay yarıçapı",
       "A smaller arc becomes a short line.", "Bundan küçük yay kısa bir çizgi olur.", lo=0, hi=1000),
    _o("arc_max_radius", "arcs", "float", "Largest arc radius", "En büyük yay yarıçapı",
       "A larger arc becomes lines (nearly straight, the numbers would be huge).",
       "Bundan büyük yay çizgilere bölünür (neredeyse düz, sayılar çok büyük olurdu).", lo=1, hi=1e7),
    _o("arc_tolerance", "arcs", "float", "Tolerance of arcs made into lines", "Çizgiye çevrilen yayın toleransı",
       "How far the short lines may leave the true arc.", "Kısa çizgiler gerçek yaydan en çok ne kadar ayrılabilir.",
       lo=0.0001, hi=1),
    _o("helical", "arcs", "bool", "Arcs may go down while turning (helix)", "Yay dönerken inebilsin (helis)",
       "Off: such arcs are written as short lines.", "Kapalıysa böyle yaylar kısa çizgiler olarak yazılır."),
    _o("arc_xy_always", "arcs", "bool", "Always write X and Y on an arc", "Yayda X ve Y hep yazılsın",
       "Safer: some controllers read an arc without X Y as a full circle.",
       "Daha güvenli: bazı kontrolcüler X Y'siz yayı tam daire sayar."),

    _o("modal_coords", "lines", "bool", "Leave out coordinates that did not change", "Değişmeyen koordinatı yazma",
       "G1 X10 Y5 then G1 X20 (Y5 is left out). Shorter files.", "G1 X10 Y5 sonra G1 X20 (Y5 yazılmaz). Daha kısa dosya."),
    _o("modal_motion", "lines", "bool", "Leave out a repeated G0 / G1 / G2 / G3", "Tekrarlanan G0 / G1 / G2 / G3 yazma",
       "The controller keeps the last one.", "Kontrolcü sonuncusunu hatırlar."),
    _o("modal_feed", "lines", "bool", "Write F only when it changes", "F yalnız değişince yazılsın",
       "Off: F on every line that cuts.", "Kapalıysa kesen her satırda F yazılır."),
    _o("rapid_z_first", "lines", "bool", "Rapid: lift first, go down last", "Hızlı harekette: önce kaldır, en son in",
       "A rapid that changes Z and X Y is split so the tool never dives sideways into the material.",
       "Hem Z'yi hem X Y'yi değiştiren hızlı hareket bölünür; kesici malzemeye yandan dalmaz."),
    _o("line_numbers", "lines", "bool", "Number the lines (N10 N20 ...)", "Satırları numarala (N10 N20 ...)",
       "Some controllers need them to restart in the middle.", "Bazı kontrolcüler ortadan devam etmek için ister."),
    _o("line_number_start", "lines", "int", "First line number", "İlk satır numarası", "", "", lo=0, hi=99999),
    _o("line_number_step", "lines", "int", "Step of line numbers", "Satır numarası artışı", "", "", lo=1, hi=1000),
    _o("line_number_max", "lines", "int", "Line numbers go back to the first after", "Şu sayıdan sonra baştan başla",
       "", "", lo=10, hi=99999),
    _o("line_number_space", "lines", "bool", "A space after the line number", "Satır numarasından sonra boşluk",
       "N10 G1 X5 or N10G1X5.", "N10 G1 X5 ya da N10G1X5."),
    _o("comments", "lines", "bool", "Write comments", "Yorumlar yazılsın",
       "Off: the lines of the blocks that start with ( or ; are dropped. Needed by controllers that choke on them.",
       "Kapalıysa blokların ( veya ; ile başlayan satırları atılır. Yorumlara takılan kontrolcüler için."),
    _o("uppercase", "lines", "bool", "Upper case", "Büyük harf", "Everything except comments.",
       "Yorumlar dışında her şey."),

    _o("drill_cycles", "machine", "bool", "Drill with canned cycles (G81 / G83)", "Delmeyi döngüyle yap (G81 / G83)",
       "Off: every hole is written as moves down and up.", "Kapalıysa her delik aşağı-yukarı hareketlerle yazılır."),
    _o("dwell_units", "machine", "choice", "Dwell (P) is in", "Bekleme (P) birimi",
       "Seconds or milliseconds.", "Saniye ya da milisaniye.",
       [("s", "Seconds", "Saniye"), ("ms", "Milliseconds", "Milisaniye")]),
    _o("tool_change_first", "machine", "bool", "Tool change block for the first tool too",
       "İlk takım için de takım değiştirme bloğu",
       "Off for machines that are loaded by hand before the start.", "Başlamadan önce elle takılan makineler için kapatın."),
    _o("max_spindle", "machine", "int", "Highest spindle speed (0 = no limit)", "En yüksek devir (0 = sınır yok)",
       "A faster tool is written at this speed.", "Daha hızlı bir takım bu devirle yazılır.", lo=0, hi=200000),
    _o("split_by_tool", "machine", "bool", "One file per tool", "Her takım için ayrı dosya",
       "For single spindle machines without a tool changer.", "Takım değiştiricisi olmayan tek milli makineler için."),

    _o("rotary_letter", "rotary", "str", "Letter of the rotary axis", "Döner eksenin harfi",
       "The angle of a wrapped job is written with this letter (A).", "Sarılı işin açısı bu harfle yazılır (A)."),
    _o("rotary_feed", "rotary", "choice", "Feed of a wrapped job", "Sarılı işin hızı",
       "Inverse time (G93) keeps the speed on the surface right; degrees: for controllers that count degrees like "
       "millimetres; plain: F as it is.",
       "Ters zaman (G93) yüzeydeki hızı doğru tutar; derece: dereceyi milimetre sayan kontrolcüler için; düz: F olduğu gibi.",
       [("inverse_time", "Inverse time (G93)", "Ters zaman (G93)"), ("degrees", "Degrees", "Derece"),
        ("plain", "Plain", "Düz")]),
]

_MOVE_WORDS = ["G", "X", "Y", "Z", "F"]


def _b(name, group, en, tr, wen, wtr, vars="tool", words=(), extra=()) -> dict:
    return {"name": name, "group": group, "label": _t(en, tr), "when": _t(wen, wtr), "vars": vars,
            "words": list(words), "extra": list(extra), "default": DEFAULT_BLOCKS.get(name, "")}


BLOCK_GROUPS = [
    ("program", _t("Program", "Program")), ("tools", _t("Tools", "Takımlar")),
    ("toolpaths", _t("Toolpaths", "Takım yolları")), ("moves", _t("Moves", "Hareketler")),
    ("drilling", _t("Drilling", "Delme")), ("rotary", _t("Rotary axis", "Döner eksen")),
]

BLOCKS: list[dict] = [
    _b("header", "program", "Start of the file", "Dosyanın başı", "Once, at the very start.",
       "Bir kez, en başta.", "job"),
    _b("footer", "program", "End of the file", "Dosyanın sonu", "Once, at the very end (spindle off, home, M30).",
       "Bir kez, en sonda (iş mili kapat, ev konumu, M30).", "job"),
    _b("first_tool_change", "tools", "First tool", "İlk takım",
       "Before the first toolpath (when empty, the tool change block is used).",
       "İlk takım yolundan önce (boşsa takım değiştirme bloğu kullanılır)."),
    _b("tool_change", "tools", "Tool change", "Takım değiştirme", "When the next toolpath has another tool.",
       "Sıradaki takım yolunun takımı farklıysa."),
    _b("tool_same", "tools", "Same tool again", "Aynı takım yine", "When the next toolpath has the same tool (optional).",
       "Sıradaki takım yolu aynı takımı kullanıyorsa (isteğe bağlı)."),
    _b("toolpath_start", "toolpaths", "Start of a toolpath", "Takım yolunun başı", "Before every toolpath.",
       "Her takım yolundan önce."),
    _b("toolpath_end", "toolpaths", "End of a toolpath", "Takım yolunun sonu", "After every toolpath.",
       "Her takım yolundan sonra."),
    _b("comment", "toolpaths", "Comment of a toolpath", "Takım yolu yorumu", "Notes that toolpaths write (warnings, passes).",
       "Takım yollarının yazdığı notlar (uyarılar, geçişler).", extra=["text"]),
    _b("dwell", "toolpaths", "Pause", "Bekleme", "Where a toolpath waits.", "Bir takım yolu beklediğinde.",
       extra=["dwell"]),
    _b("rapid", "moves", "Rapid move", "Hızlı hareket", "Every G0.", "Her G0.", words=_MOVE_WORDS[:4]),
    _b("linear", "moves", "Cutting line", "Kesme çizgisi", "Every G1.", "Her G1.", words=_MOVE_WORDS),
    _b("arc", "moves", "Arc with I J", "I J'li yay", "Every G2 / G3 when arcs are written with I J K.",
       "Yaylar I J K ile yazılırken her G2 / G3.", words=_MOVE_WORDS + ["I", "J", "K"]),
    _b("arc_r", "moves", "Arc with R", "R'li yay", "Every G2 / G3 when arcs are written with R.",
       "Yaylar R ile yazılırken her G2 / G3.", words=_MOVE_WORDS + ["R"]),
    _b("drill", "drilling", "Drilling cycle", "Delme döngüsü", "A hole (G81).", "Bir delik (G81).",
       words=["X", "Y", "Z", "R", "F"]),
    _b("drill_peck", "drilling", "Peck drilling cycle", "Gagalamalı delme", "A hole with pecks (G83).",
       "Gagalamalı delik (G83).", words=["X", "Y", "Z", "R", "Q", "F"]),
    _b("drill_dwell", "drilling", "Drilling with a pause", "Bekleyen delme", "A hole with a pause at the bottom (G82).",
       "Dipte bekleyen delik (G82).", words=["X", "Y", "Z", "R", "P", "F"]),
    _b("drill_next", "drilling", "Next hole", "Sıradaki delik", "The next hole of the same cycle.",
       "Aynı döngünün sıradaki deliği.", words=["X", "Y"]),
    _b("drill_end", "drilling", "End of the cycle", "Döngünün sonu", "After the last hole (G80).",
       "Son delikten sonra (G80)."),
    _b("inverse_time_on", "rotary", "Inverse time on", "Ters zaman açık", "Before a wrapped job (G93).",
       "Sarılı işten önce (G93).", "job"),
    _b("inverse_time_off", "rotary", "Inverse time off", "Ters zaman kapalı", "After a wrapped job (G94).",
       "Sarılı işten sonra (G94).", "job"),
]

_V = [
    ("file_name", "job", "Name of the file", "Dosyanın adı"), ("project_name", "job", "Name of the project", "Projenin adı"),
    ("sheet_name", "job", "Name of the sheet", "Sacın adı"), ("post_name", "job", "Name of this post", "Bu postun adı"),
    ("date", "job", "Date", "Tarih"), ("time", "job", "Time", "Saat"),
    ("units", "job", "mm or inch", "mm ya da inch"), ("unit_code", "job", "G21 or G20", "G21 ya da G20"),
    ("program_number", "job", "Program number (for O words)", "Program numarası (O kelimesi için)"),
    ("material_thickness", "job", "Thickness of the material", "Malzeme kalınlığı"),
    ("sheet_width", "job", "Width of the sheet", "Sac genişliği"), ("sheet_height", "job", "Height of the sheet", "Sac yüksekliği"),
    ("safe_z", "job", "Safe Z (travel height)", "Güvenli Z (gezinme yüksekliği)"),
    ("home_x", "job", "Home X", "Ev X"), ("home_y", "job", "Home Y", "Ev Y"), ("home_z", "job", "Home Z", "Ev Z"),
    ("xmin", "job", "Smallest X of the job", "İşin en küçük X'i"), ("xmax", "job", "Largest X of the job", "İşin en büyük X'i"),
    ("ymin", "job", "Smallest Y of the job", "İşin en küçük Y'si"), ("ymax", "job", "Largest Y of the job", "İşin en büyük Y'si"),
    ("zmin", "job", "Deepest Z of the job", "İşin en derin Z'si"),
    ("total_time", "job", "Time of the job", "İşin süresi"), ("toolpath_count", "job", "Number of toolpaths", "Takım yolu sayısı"),
    ("tools", "job", "The tools used", "Kullanılan takımlar"),
    ("tool_number", "tool", "Tool number", "Takım numarası"), ("tool_name", "tool", "Tool name", "Takım adı"),
    ("tool_type", "tool", "Tool type", "Takım türü"), ("tool_diameter", "tool", "Tool diameter", "Takım çapı"),
    ("tool_angle", "tool", "Tool angle (V-bits)", "Takım açısı (V uçlar)"),
    ("spindle_speed", "tool", "Spindle speed", "İş mili devri"), ("spindle_dir_code", "tool", "M3 or M4", "M3 ya da M4"),
    ("coolant_code", "tool", "M7 / M8 or nothing", "M7 / M8 ya da boş"), ("coolant_off", "tool", "M9 or nothing", "M9 ya da boş"),
    ("prev_tool_number", "tool", "Number of the tool before", "Önceki takımın numarası"),
    ("toolpath_name", "tool", "Name of the toolpath", "Takım yolunun adı"),
    ("feed_rate", "tool", "Cutting feed of the tool", "Takımın kesme hızı"),
    ("plunge_rate", "tool", "Plunge feed of the tool", "Takımın dalış hızı"),
    ("toolpath_time", "tool", "Time of the toolpath", "Takım yolunun süresi"),
]
VARIABLES: list[dict] = [{"name": n, "group": g, "text": _t(en, tr)} for n, g, en, tr in _V]

WORD_NAMES = {"G", "X", "Y", "Z", "I", "J", "K", "R", "F", "Q", "P", "N"}
KNOWN_NAMES = {v["name"] for v in VARIABLES} | WORD_NAMES | {"dwell", "text", "rotary_diameter"}
BLOCK_NAMES = [b["name"] for b in BLOCKS]


def specs() -> dict:
    """Everything a form needs to edit a post (JSON-able, English and Turkish)."""
    return {"groups": [{"id": i, "label": l} for i, l in GROUPS], "options": OPTIONS,
            "block_groups": [{"id": i, "label": l} for i, l in BLOCK_GROUPS], "blocks": BLOCKS,
            "variables": VARIABLES}


# --------------------------------------------------------------------------------------------------
# the TOML file
# --------------------------------------------------------------------------------------------------

_BARE = re.compile(r"^[A-Za-z0-9_-]+$")
_ID = re.compile(r"[A-Za-z0-9_-]{1,60}")             # (also what a file of the user may have written by hand)
_MAX_ENTRIES = 100


def _escape(text: str) -> str:
    out = []
    for ch in text:
        if ch == "\\":
            out.append("\\\\")
        elif ch == '"':
            out.append('\\"')
        elif ch in "\n\t":
            out.append(ch)
        elif ch == "\r":
            out.append("\\r")
        elif ord(ch) < 32 or ord(ch) == 127:
            out.append(f"\\u{ord(ch):04x}")
        else:
            out.append(ch)
    return "".join(out)


def _string(text: str) -> str:
    if "\n" in text:
        return '"""\n' + _escape(text) + '"""'      # (the newline after the quotes is not part of the text)
    return '"' + _escape(text).replace("\t", "\\t") + '"'


def _value(v) -> str:
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, int):
        return str(v)
    if isinstance(v, float):
        if not math.isfinite(v):
            raise PostEditError("a number is not finite")
        return repr(v)
    return _string(str(v))


def _key(k: str) -> str:
    return k if _BARE.match(k) else '"' + _escape(k) + '"'


def dump_toml(d: dict) -> str:
    """The text of a post file for ``{"id", "extends", "name", "description", "extension", "options", "blocks"}``
    (a dictionary read back with ``tomllib`` is exactly ``d``)."""
    lines = ["# Post-processor. Made with the post editor; the options and blocks written here are what differs from",
             "# the post it extends. Every option and block is described in docs/postprocessors.md.", ""]
    for k in ("id", "extends", "name", "description", "extension"):
        if d.get(k) not in (None, ""):
            lines.append(f"{k} = {_string(str(d[k]))}")
    opts = d.get("options") or {}
    if opts:
        order = [k for k in DEFAULT_OPTIONS if k in opts] + sorted(k for k in opts if k not in DEFAULT_OPTIONS)
        lines += ["", "[options]"] + [f"{_key(k)} = {_value(opts[k])}" for k in order]
    blocks = d.get("blocks") or {}
    if blocks:
        order = [k for k in BLOCK_NAMES if k in blocks] + sorted(k for k in blocks if k not in BLOCK_NAMES)
        lines += ["", "[blocks]"] + [f"{_key(k)} = {_string(blocks[k])}" for k in order]
    return "\n".join(lines) + "\n"


# --------------------------------------------------------------------------------------------------
# a draft
# --------------------------------------------------------------------------------------------------

def slug(name: str) -> str:
    """A file name and id from a name: lower case letters and digits, Turkish letters without their marks."""
    plain = unicodedata.normalize("NFKD", name.replace("ı", "i").replace("İ", "i").lower())
    out = "".join(ch if ch.isascii() and ch.isalnum() else "_" for ch in plain if not unicodedata.combining(ch))
    out = re.sub(r"_+", "_", out).strip("_")
    return (out or "custom")[:40]


def unique_id(name: str, taken) -> str:
    base = slug(name)
    pid, n = base, 2
    while pid in taken:
        pid, n = f"{base}_{n}", n + 1
    return pid


def placeholders(template: str) -> list[str]:
    """The ``{names}`` a block uses.  Raises :class:`PostEditError` for braces that do not pair up."""
    try:
        return [f for _lit, f, _spec, _conv in string.Formatter().parse(template) if f is not None]
    except ValueError as exc:
        raise PostEditError(f"{exc} (a literal brace is written {{{{ or }}}})") from exc


def raw_blocks_of(draft: dict) -> dict:
    b = draft.get("blocks") if isinstance(draft, dict) else None
    return b if isinstance(b, dict) else {}


def check_blocks(blocks: dict) -> tuple[dict, list[str]]:
    """``(blocks, warnings)``: the blocks as text, with a note for each placeholder this program does not know."""
    out, warn = {}, []
    for name, text in blocks.items():
        if not isinstance(name, str) or not isinstance(text, str):
            raise PostEditError("every block is a name and a text")
        if not _ID.fullmatch(name):
            raise PostEditError(f"{name!r} is not a name of a block")
        if len(text) > 8000:
            raise PostEditError(f"block {name} is too long")
        for f in placeholders(text):
            if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", f):
                raise PostEditError(f"block {name}: {{{f}}} is not a name of a variable (only {{name}} is allowed)")
            if f not in KNOWN_NAMES:
                warn.append(f"block {name}: there is no variable {{{f}}}")
        out[name] = text
    return out, warn


def _arc_mode_warnings(ijk: str, header: str | None, arcs: str) -> list[str]:
    """The controller reads I J from where its mode says (G91.1 from the start of the arc, G90.1 as a place):
    the header has to say what the post writes, or every arc goes to the wrong circle."""
    if arcs != "ijk" or header is None:
        return []
    if ijk == "absolute" and "G91.1" in header.upper() and "G90.1" not in header.upper():
        return ["I J are written as places (absolute) but the header says G91.1: change it to G90.1, or the "
                "controller reads them from the start of the arc"]
    if ijk == "incremental" and "G90.1" in header.upper():
        return ["I J are written from the start of the arc (incremental) but the header says G90.1: change it to "
                "G91.1 or leave it out"]
    return []


def _range_check(key: str, value, spec: dict) -> None:
    if spec.get("kind") in ("int", "float") and not isinstance(value, bool):
        lo, hi = spec.get("min"), spec.get("max")
        if (lo is not None and value < lo) or (hi is not None and value > hi):
            raise PostEditError(f"option {key} must be between {lo} and {hi}, not {value}")


def clean_draft(draft: dict) -> tuple[dict, list[str]]:
    """The draft as it can be saved, and what to warn about; :class:`PostEditError` when it cannot be used."""
    if not isinstance(draft, dict):
        raise PostEditError("the post must be an object")
    name = str(draft.get("name") or "").strip()
    if not name or "\n" in name or len(name) > 80:
        raise PostEditError("give the post a name (one line, at most 80 letters)")
    description = str(draft.get("description") or "").strip()
    if "\n" in description or len(description) > 300:
        raise PostEditError("the description is one line of at most 300 letters")
    extension = str(draft.get("extension") or "nc").strip().lstrip(".").lower()
    if not re.fullmatch(r"[a-z0-9]{1,8}", extension):
        raise PostEditError("the extension is letters and digits, like nc or tap")
    raw = draft.get("options") or {}
    if not isinstance(raw, dict):
        raise PostEditError("options must be an object")
    try:
        options = checked_options(raw)
    except ValueError as exc:
        raise PostEditError(str(exc)) from exc
    if len(options) > _MAX_ENTRIES or len(raw_blocks_of(draft)) > _MAX_ENTRIES:
        raise PostEditError("too many options or blocks")
    for k, v in options.items():
        if not isinstance(k, str) or not _ID.fullmatch(k):
            raise PostEditError(f"{k!r} is not a name of an option")
        if k not in DEFAULT_OPTIONS and not isinstance(v, (bool, int, float, str)):
            raise PostEditError(f"option {k} must be a number, a switch or text")
    by_key = {o["key"]: o for o in OPTIONS}
    for k, v in options.items():
        if k in by_key:
            _range_check(k, v, by_key[k])
        if isinstance(v, float) and not math.isfinite(v):
            raise PostEditError(f"option {k} must be a finite number")
    if options.get("rotary_letter") is not None and not re.fullmatch(r"[A-Za-z]", str(options["rotary_letter"])):
        raise PostEditError("the rotary axis letter is one letter")
    raw_blocks = draft.get("blocks") or {}
    if not isinstance(raw_blocks, dict):
        raise PostEditError("blocks must be an object")
    blocks, warnings = check_blocks(raw_blocks)
    warnings += _arc_mode_warnings(options.get("ijk", DEFAULT_OPTIONS["ijk"]), blocks.get("header"),
                                   options.get("arcs", DEFAULT_OPTIONS["arcs"]))
    clean = {"id": str(draft.get("id") or "").strip(), "name": name, "description": description,
             "extension": extension, "extends": str(draft.get("extends") or "").strip(),
             "options": options, "blocks": blocks}
    if clean["id"] and not _ID.fullmatch(clean["id"]):
        raise PostEditError("the id is letters, digits, - and _ (at most 60)")
    return clean, warnings


def build_post(draft: dict) -> PostProcessor:
    """The post a draft describes, to try it (nothing is written; ``extends`` is not needed: a draft holds all)."""
    clean, _ = clean_draft(draft)
    d = {"id": clean["id"] or "draft", "name": clean["name"], "description": clean["description"],
         "extension": clean["extension"], "options": clean["options"], "blocks": clean["blocks"]}
    try:
        return PostProcessor(d)
    except ValueError as exc:
        raise PostEditError(str(exc)) from exc


def draft_of(post: PostProcessor, extends: str | None = None) -> dict:
    """A post as a draft: every option and block as the post has it.  ``extends`` is what the file says it is based
    on (read from the file when not given)."""
    ext = extends
    if ext is None and post.path is not None:
        try:
            ext = read_definition(Path(post.path)).get("extends")
        except Exception:  # noqa: BLE001  (a damaged file: it has no parent to name)
            ext = None
    return {"id": post.id, "name": post.name, "description": post.description, "extension": post.extension,
            "extends": ext or "", "options": dict(post.options), "blocks": {k: v for k, v in post.blocks.items()}}


@functools.cache
def builtin_ids() -> frozenset:
    """The ids of the posts that come with the program."""
    return frozenset(builtin_definitions())


def is_user_post(post) -> bool:
    p = getattr(post, "path", None)
    if p is None:
        return False
    try:
        return paths.posts_dir().resolve() in Path(p).resolve().parents
    except OSError:
        return False


def info(post: PostProcessor) -> dict:
    """What an interface shows of a post: the draft and whether it can be changed."""
    d = draft_of(post)
    d["blocks"] = {k: d["blocks"].get(k, "") for k in BLOCK_NAMES}
    user = is_user_post(post)
    return {**d, "builtin": post.id in builtin_ids(), "editable": user}


# --------------------------------------------------------------------------------------------------
# writing and deleting
# --------------------------------------------------------------------------------------------------

_save_lock = threading.Lock()


def parent_of(extends: str, posts: dict, own_id: str = "") -> dict | None:
    """The options and blocks of the post a post is based on, or ``None``.  Only a post that is a file can be
    extended (the loader finds the parent among the files): a post of a plugin cannot, so such a post is saved
    with everything that differs from the defaults instead."""
    base = posts.get(extends) if extends else None
    if base is not None and base.id != own_id and (base.id in builtin_ids() or is_user_post(base)):
        return {"options": dict(base.options), "blocks": dict(base.blocks)}
    return None


def own_definition(clean: dict, parent: dict | None) -> dict:
    """What the file of a post says: its name and what differs from its parent (``parent`` holds the parent's
    ``options`` and ``blocks`` as it has them, or ``None`` for a post that extends nothing)."""
    base_opts = dict(parent["options"]) if parent else dict(DEFAULT_OPTIONS)
    base_blocks = dict(parent["blocks"]) if parent else dict(DEFAULT_BLOCKS)
    options = {k: v for k, v in clean["options"].items() if base_opts.get(k, object()) != v}
    blocks = {k: v for k, v in clean["blocks"].items() if base_blocks.get(k, "") != v}
    out = {"id": clean["id"], "name": clean["name"], "description": clean["description"],
           "extension": clean["extension"], "options": options, "blocks": blocks}
    if parent and clean["extends"]:
        out["extends"] = clean["extends"]
    return out


def save_user_post(draft: dict, posts: dict, directory: Path | None = None) -> tuple[Path, list[str]]:
    """Write a draft as a user post; returns ``(file, warnings)``.

    ``posts`` is what is registered now (``{id: PostProcessor}``): the post being changed and the one it extends are
    looked up there.  The id is made from the name when the draft has none.  A built-in post is never overwritten."""
    clean, warnings = clean_draft(draft)
    directory = Path(directory) if directory else paths.posts_dir()
    builtin = builtin_ids()
    with _save_lock:
        pid = clean["id"]
        existing = posts.get(pid) if pid else None
        if not pid:
            pid = unique_id(clean["name"], set(posts) | set(builtin))
        elif pid in builtin or (existing is not None and not is_user_post(existing)):
            raise PostEditError(f"{pid} is a post that comes with the program: save it under another name")
        clean["id"] = pid
        if clean["extends"] == pid:
            raise PostEditError("a post cannot be based on itself")
        parent = parent_of(clean["extends"], posts, pid)
        if parent is None:
            clean["extends"] = ""
        if existing is not None and existing.path:
            target = Path(existing.path)
            keep = read_definition(target) if target.exists() else {}
            for k, v in (keep.get("options") or {}).items():          # (what this editor does not know is kept)
                if k not in DEFAULT_OPTIONS and k not in clean["options"]:
                    clean["options"][k] = v
        else:
            target = directory / f"{pid}.toml"
            if target.exists():
                raise PostEditError(f"{target.name} is in the posts folder already but cannot be loaded: mend or "
                                    "delete it first")
        out = own_definition(clean, parent)
        text = dump_toml(out)
        try:
            back = tomllib.loads(text)
        except tomllib.TOMLDecodeError as exc:        # (never leave a file that cannot be read)
            raise PostEditError(f"the post cannot be written as a file: {exc}") from exc
        if back.get("options", {}) != out["options"] or back.get("blocks", {}) != out["blocks"]:
            raise PostEditError("the post cannot be written as a file without changing it")
        directory.mkdir(parents=True, exist_ok=True)
        tmp = target.with_name(target.name + ".tmp")
        tmp.write_text(text, encoding="utf-8", newline="\n")
        os.replace(tmp, target)
    return target, warnings


def delete_user_post(post) -> Path:
    """Remove the file of a user post (a built-in post or one of a plugin cannot be deleted)."""
    if not is_user_post(post):
        raise PostEditError("only the posts you made can be deleted")
    p = Path(post.path)
    with _save_lock:
        p.unlink(missing_ok=True)
    return p


# --------------------------------------------------------------------------------------------------
# a sample job to look at
# --------------------------------------------------------------------------------------------------

_sample_lock = threading.Lock()
_sample = None


def _sample_job():
    """A small project that shows what a post writes: a rounded plate with a hole to drill, a round pocket and
    the outline (arcs, a helix, two tools), calculated once."""
    from ..cam.job import compute_all, sheet_toolpaths
    from ..core import shapes
    from ..core.document import Project, Selection, ToolpathDef, VectorObject
    from ..core.tooling import Tool
    p = Project("Sample")
    p.material.thickness = 12.0
    s = p.sheets[0]
    s.width, s.height = 150.0, 100.0
    objs = [VectorObject([c], p.layers[0].id) for c in (shapes.rectangle(10, 10, 130, 80, corner_radius=8),
                                                           shapes.circle((40, 50), 5),
                                                           shapes.circle((100, 50), 15))]
    s.vectors.extend(objs)

    def add(name, op, tool, params, obj):
        p.toolpaths.append(ToolpathDef(name, op, tool, params, Selection("vectors", [obj.id])))

    drill = Tool(name="Drill 5", type="drill", diameter=5.0, number=3, feed_rate=600, plunge_rate=300,
                 spindle_speed=8000)
    mill = Tool(name="End mill 6", type="end_mill", diameter=6.0, number=1, stepdown=3.0, stepover=2.4,
                feed_rate=3000, plunge_rate=1000, spindle_speed=18000)
    add("Hole", "drill", drill, {"cut_depth": 8, "peck": 2}, objs[1])
    add("Pocket", "pocket", mill, {"cut_depth": 4, "entry": "helix"}, objs[2])
    add("Outline", "profile", mill, {"through": True, "lead": "arc"}, objs[0])
    res = compute_all(p)
    return p, s, sheet_toolpaths(p, res, s)


def sample_files(post: PostProcessor) -> list[tuple[str, str]]:
    """``[(file suffix, G-code)]`` of the sample job written by ``post``."""
    global _sample
    from .export import post_toolpaths
    with _sample_lock:
        if _sample is None:
            _sample = _sample_job()
        project, sheet, tps = _sample
        return post_toolpaths(project, sheet, tps, post, "sample")

"""The names of the G-code files: a few prefixes, each with variables, the way the blocks of a post are written.

``{project_name}``, ``{sheet_name}``, ``{date}`` ... are put in where they are written; the parts (prefixes) are joined by a
separator (``_``). Used by the Save G-code window of the desktop program, the page (Output tab) and the engine
(``gcode`` takes ``name_parts``), so a file is called the same wherever it is saved."""
from __future__ import annotations

import datetime as _dt
import re

DEFAULT_PARTS = ["{project_name}"]
DEFAULT_SEP = "_"
MAX_PARTS = 8

_V = [
    ("project_name", "Name of the project", "Projenin adı"), ("sheet_name", "Name of the sheet", "Sacın adı"),
    ("sheet_number", "Number of the sheet (1, 2 ...)", "Sacın numarası (1, 2 ...)"),
    ("sheet_count", "How many sheets", "Kaç sac var"), ("post_name", "Name of the post", "Postun adı"),
    ("group", "Group of toolpaths saved (or empty)", "Kaydedilen takım yolu grubu (ya da boş)"),
    ("toolpath_count", "Number of toolpaths in the file", "Dosyadaki takım yolu sayısı"),
    ("tool_number", "Number of the first tool", "İlk takımın numarası"), ("tool_name", "Name of the first tool", "İlk takımın adı"),
    ("tool_diameter", "Diameter of the first tool", "İlk takımın çapı"),
    ("toolpath_name", "Name of the first toolpath", "İlk takım yolunun adı"),
    ("safe_z", "Safe Z", "Güvenli Z"), ("cut_depth", "Cut depth of the first toolpath", "İlk takım yolunun kesim derinliği"),
    ("sheet_group", "Group of the sheet (or empty)", "Sacın grubu (ya da boş)"),
    ("revision", "Revision number (counts up by itself, or typed)", "Revizyon numarası (kendiliğinden artar, elle de yazılır)"),
    ("material_thickness", "Thickness of the material", "Malzeme kalınlığı"),
    ("sheet_width", "Width of the sheet", "Sac genişliği"), ("sheet_height", "Height of the sheet", "Sac yüksekliği"),
    ("date", "Date (2026-11-18)", "Tarih (2026-11-18)"), ("time", "Time (0930)", "Saat (0930)"),
]
VARIABLES: list[dict] = [{"name": n, "text": {"en": en, "tr": tr}} for n, en, tr in _V]
NAMES = {v["name"] for v in VARIABLES}
_FIELD = re.compile(r"\{([A-Za-z_][A-Za-z0-9_]*)\}")
_BAD = re.compile(r"[^\w\-. ()+,=#@]+", re.UNICODE)


class NamingError(ValueError):
    pass


def unknown(parts: list[str]) -> list[str]:
    """The variables written in the parts that do not exist."""
    return sorted({m for p in parts for m in _FIELD.findall(p) if m not in NAMES})


def check(parts, sep: str = DEFAULT_SEP) -> tuple[list[str], str]:
    """``(parts, separator)`` cleaned, or :class:`NamingError` with what is wrong."""
    if parts is None:
        parts = DEFAULT_PARTS
    if not isinstance(parts, (list, tuple)) or not all(isinstance(p, str) for p in parts):
        raise NamingError("the parts of the name must be a list of texts")
    parts = [p.strip() for p in parts if p.strip()][:MAX_PARTS] or list(DEFAULT_PARTS)
    if not isinstance(sep, str) or len(sep) > 4 or _BAD.search(sep.replace(" ", "")):
        raise NamingError("the separator is at most 4 characters, none of them a path character")
    bad = unknown(parts)
    if bad:
        raise NamingError("unknown variable(s): " + ", ".join("{" + b + "}" for b in bad))
    return parts, sep


def values(project_name: str = "", sheet_name: str = "", sheet_number: int = 1, sheet_count: int = 1, post_name: str = "",
           group: str = "", toolpath_count: int = 0, tool_number=None, tool_name: str = "", thickness=None,
           width=None, height=None, now: _dt.datetime | None = None, tool_diameter=None, toolpath_name: str = "",
           safe_z=None, cut_depth=None, sheet_group: str = "", revision=None) -> dict:
    """The value of every variable (as text)."""
    now = now or _dt.datetime.now()

    def num(v):
        return "" if v is None else f"{v:g}"
    return {"project_name": project_name, "sheet_name": sheet_name, "sheet_number": str(sheet_number),
            "sheet_count": str(sheet_count), "post_name": post_name, "group": group, "toolpath_count": str(toolpath_count),
            "tool_number": "" if tool_number is None else str(tool_number), "tool_name": tool_name,
            "material_thickness": num(thickness), "sheet_width": num(width), "sheet_height": num(height),
            "date": now.strftime("%Y-%m-%d"), "time": now.strftime("%H%M"), "tool_diameter": num(tool_diameter),
            "toolpath_name": toolpath_name, "safe_z": num(safe_z), "cut_depth": num(cut_depth), "sheet_group": sheet_group,
            "revision": "" if revision is None else str(int(revision))}


def clean(text: str) -> str:
    """A text that can be (a part of) a file name: letters, digits and a few signs, the rest becomes ``_``."""
    out = _BAD.sub("_", text).strip(" ._")
    return re.sub(r"_{2,}", "_", out)


def render(parts, vals: dict, sep: str = DEFAULT_SEP) -> str:
    """The name: every part with its variables put in, empty parts left out, the rest joined by ``sep``; at most 120
    characters, never empty."""
    parts, sep = check(parts, sep)
    done = []
    for p in parts:
        t = clean(_FIELD.sub(lambda m: str(vals.get(m.group(1), "")), p))
        if t:
            done.append(t)
    return (sep.join(done) or "job")[:120]


def uses(parts, *names: str) -> bool:
    """Whether a variable of ``names`` is written in the parts."""
    return any(m in names for p in parts or [] for m in _FIELD.findall(p))

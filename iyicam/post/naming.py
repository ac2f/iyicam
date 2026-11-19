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
    ("tool_number", "Number(s) of the tools in the file (1+2)", "Dosyadaki takımların numaraları (1+2)"),
    ("tool_name", "Name(s) of the tools in the file", "Dosyadaki takımların adları"),
    ("tool_diameter", "Diameter(s) of the tools in the file", "Dosyadaki takımların çapları"),
    ("toolpath_name", "Name(s) of the toolpaths in the file (A+B)", "Dosyadaki takım yollarının adları (A+B)"),
    ("safe_z", "Safe Z (the highest of the file)", "Güvenli Z (dosyadaki en yüksek)"),
    ("cut_depth", "Cut depth(s) of the toolpaths in the file", "Dosyadaki takım yollarının kesim derinlikleri"),
    ("sheet_group", "Group of the sheet (or empty)", "Sacın grubu (ya da boş)"),
    ("revision", "Revision number (counts up by itself, or typed)", "Revizyon numarası (kendiliğinden artar, elle de yazılır)"),
    ("material_thickness", "Thickness of the material", "Malzeme kalınlığı"),
    ("sheet_width", "Width of the sheet", "Sac genişliği"), ("sheet_height", "Height of the sheet", "Sac yüksekliği"),
    ("date", "Date (2026-11-18)", "Tarih (2026-11-18)"), ("time", "Time (0930)", "Saat (0930)"),
]
VARIABLES: list[dict] = [{"name": n, "text": {"en": en, "tr": tr}} for n, en, tr in _V]
NAMES = {v["name"] for v in VARIABLES}
_FIELD = re.compile(r"\{([A-Za-z_][A-Za-z0-9_]*)\}")
# a field: a variable or a sum of variables and numbers, with a format after the last ":" (``{revision:03}``: three digits with
# zeros in front, ``{tool_diameter:.1}``: one decimal, ``{sheet_number + 100}``, ``{sheet_height/10:.0}``)
_ANY = re.compile(r"\{([^{}]*)\}")
_SPEC = re.compile(r"^(0?)(\d{1,2})?(?:\.(\d))?$")
_IDENT = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
JOIN = "+"
_BAD = re.compile(r"[^\w\-. ()+,=#@]+", re.UNICODE)


_RESERVED = {"CON", "PRN", "AUX", "NUL"} | {f"COM{i}" for i in range(1, 10)} | {f"LPT{i}" for i in range(1, 10)}


class NamingError(ValueError):
    pass


def _split(field: str) -> tuple[str, str | None]:
    """``(expression, format)`` of what is between the braces."""
    if ":" in field:
        expr, spec = field.rsplit(":", 1)
        if _SPEC.match(spec.strip()) and spec.strip():
            return expr.strip(), spec.strip()
    return field.strip(), None


def _names_in(part: str) -> list[str]:
    return [n for f in _ANY.findall(part) for n in _IDENT.findall(_split(f)[0])]


def unknown(parts: list[str]) -> list[str]:
    """The variables written in the parts that do not exist."""
    return sorted({m for p in parts for m in _names_in(p) if m not in NAMES})


def _formatted(value: float, spec: str | None) -> str:
    m = _SPEC.match(spec or "")
    if not m:
        return f"{value:g}" if value != int(value) else str(int(value))
    zero, width, dec = m.group(1), int(m.group(2) or 0), m.group(3)
    text = f"{value:.{int(dec)}f}" if dec is not None else (str(int(round(value))) if abs(value - round(value)) < 1e-9 else f"{value:g}")
    if width:
        neg = text.startswith("-")
        body = text[1:] if neg else text
        body = body.rjust(width - (1 if neg else 0), "0")            # (zeros in front: 1 -> 001)
        text = ("-" if neg else "") + body
    return text


def field_text(field: str, vals: dict) -> str:
    """What one ``{...}`` of a prefix becomes."""
    from ..core import calc
    expr, spec = _split(field)
    if _IDENT.fullmatch(expr):
        raw = str(vals.get(expr, ""))
        if spec is None:
            return raw
        try:
            return _formatted(float(raw.replace(",", ".")), spec)
        except ValueError:
            m = _SPEC.match(spec)                                    # (a text: only the width is kept, zeros in front)
            return raw.rjust(int(m.group(2) or 0), "0") if m and raw else raw
    nums = {}
    for k, v in vals.items():
        try:
            nums[k] = float(str(v).replace(",", "."))
        except ValueError:
            pass
    v = calc.evaluate(expr, nums)
    return "" if v is None else _formatted(v, spec)


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


def _revision_text(revision) -> str:
    try:
        return "" if revision is None else str(int(revision))
    except (TypeError, ValueError, OverflowError):
        return ""


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
            "revision": _revision_text(revision)}


def clean(text: str) -> str:
    """A text that can be (a part of) a file name: letters, digits and a few signs, the rest becomes ``_``."""
    out = _BAD.sub("_", text).strip(" ._")
    out = re.sub(r"_{2,}", "_", out)
    if out.split(".")[0].upper() in _RESERVED:            # (CON, NUL ... are not file names on Windows)
        out = "_" + out
    return out


def render(parts, vals: dict, sep: str = DEFAULT_SEP) -> str:
    """The name: every part with its variables put in, empty parts left out, the rest joined by ``sep``; at most 120
    characters, never empty."""
    parts, sep = check(parts, sep)
    done = []
    for p in parts:
        t = clean(_ANY.sub(lambda m: field_text(m.group(1), vals), p))
        if t:
            done.append(t)
    return (sep.join(done) or "job")[:120]


def uses(parts, *names: str) -> bool:
    """Whether a variable of ``names`` is written in the parts."""
    return any(m in names for p in parts or [] for m in _names_in(p))


def joined(items) -> str:
    """Several values of one variable (the toolpaths of a file): each once, in order, joined by ``+``."""
    out = []
    for v in items:
        t = "" if v is None else (f"{v:g}" if isinstance(v, float) else str(v))
        if t and t not in out:
            out.append(t)
    return JOIN.join(out)


def values_for(project, sheet, index: int, count: int, post_name: str, group: str, tps, revision=None,
               now: _dt.datetime | None = None) -> dict:
    """The variables for a file holding the calculated toolpaths ``tps`` (every toolpath of the file counts: names, tools
    and depths are joined with ``+``, the safe Z is the highest)."""
    defs = [project.toolpath(t.def_id) for t in tps]
    safe = [d.safe_z if d is not None and d.safe_z is not None else project.material.safe_z for d in defs] or [project.material.safe_z]
    v = values(project.name, sheet.name, index, count, post_name, group, len(tps), None, "", project.sheet_thickness(sheet),
               sheet.width, sheet.height, now=now, sheet_group=sheet.group, revision=revision, safe_z=max(safe))
    v.update({"tool_number": joined(t.tool.number for t in tps), "tool_name": joined(t.tool.name for t in tps),
              "tool_diameter": joined(float(t.tool.diameter) for t in tps),
              "toolpath_name": joined(d.name if d is not None else t.name for d, t in zip(defs, tps)),
              "cut_depth": joined(float(d.params["cut_depth"]) for d in defs
                                  if d is not None and isinstance(d.params.get("cut_depth"), (int, float)))})
    return v

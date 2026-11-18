"""Presets of toolpaths: what a toolpath is set to, kept under a name, to make the next one with it.

The person chooses what a preset keeps (the name, the tool, each setting, the heights, the cut order ...) and what it leaves to
the toolpath it is put on. A number can be kept as a value or as a sum of the job (``material_thickness + 0.2``,
``tool_diameter * 0.4``): it is worked out when the preset is put on a toolpath, so a preset follows the material. A text (the
name) can have the same variables in braces: ``Cut {material_thickness} mm``.

The presets are kept in ``toolpath_presets.json`` in the user folder; the desktop program and the engine (the page) share them."""
from __future__ import annotations

import json
import os
import re
import uuid
from pathlib import Path

from ..core import calc, paths
from ..core.document import Tool, ToolpathDef, _jsonable, _unjson

FILE_NAME = "toolpath_presets.json"
#: what a sum of a preset may use
VARIABLES = [
    ("material_thickness", "Thickness of the material (of the sheet)", "Malzeme (tabaka) kalınlığı"),
    ("sheet_width", "Width of the sheet", "Tabaka genişliği"), ("sheet_height", "Height of the sheet", "Tabaka yüksekliği"),
    ("safe_z", "Safe Z of the project", "Projenin güvenli Z'si"), ("clearance", "Rapid-down clearance", "Hızlı iniş payı"),
    ("tool_diameter", "Diameter of the tool", "Takımın çapı"), ("tool_radius", "Radius of the tool", "Takımın yarıçapı"),
]
SEQUENCE_KEYS = ("strategy", "grouping", "direction", "smart_starts", "start_margin", "corner_distance", "band")
_FIELD = re.compile(r"\{([A-Za-z_][A-Za-z0-9_]*)\}")


class PresetError(ValueError):
    pass


def file() -> Path:
    return paths.user_dir() / FILE_NAME


def load() -> list[dict]:
    try:
        data = json.loads(file().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    items = data.get("presets") if isinstance(data, dict) else None
    return [p for p in items if isinstance(p, dict) and isinstance(p.get("name"), str) and isinstance(p.get("fields"), dict)] \
        if isinstance(items, list) else []


def _write(items: list[dict]) -> None:
    f = file()
    f.parent.mkdir(parents=True, exist_ok=True)
    tmp = f.with_name(f.name + ".tmp")
    tmp.write_text(json.dumps({"format": "iyicam-toolpath-presets", "presets": items}, indent=1), encoding="utf-8")
    os.replace(tmp, f)


def save(preset: dict) -> dict:
    """Keep a preset (one with the same id or name is replaced). Returns it as kept."""
    p = check(preset)
    items = [x for x in load() if x.get("id") != p["id"] and x.get("name") != p["name"]]
    items.append(p)
    _write(items)
    return p


def delete(pid: str) -> bool:
    items = load()
    keep = [x for x in items if x.get("id") != pid]
    if len(keep) == len(items):
        return False
    _write(keep)
    return True


def fields(tdef: ToolpathDef, op_params) -> list[dict]:
    """What a preset can keep of this toolpath: ``key``, ``label``, ``value`` (as it can be written down), ``number`` (whether it
    can be a sum)."""
    out = [{"key": "name", "label": "Name", "value": tdef.name, "number": False},
           {"key": "tool", "label": f"Tool ({tdef.tool.describe()})", "value": tdef.tool.to_dict(), "number": False}]
    for p in op_params:
        v = tdef.params.get(p.name, p.default)
        num = p.kind in ("float", "length", "angle", "feed", "percent", "int")
        out.append({"key": f"params.{p.name}", "label": p.label, "value": _jsonable(v), "number": num})
    out += [{"key": "safe_z", "label": "Safe Z of the toolpath", "value": tdef.safe_z, "number": True},
            {"key": "clearance", "label": "Rapid-down clearance of the toolpath", "value": tdef.clearance, "number": True},
            {"key": "group", "label": "Group", "value": tdef.group, "number": False},
            {"key": "sheet_group", "label": "Sheet group", "value": tdef.sheet_group, "number": False}]
    for k in SEQUENCE_KEYS:
        if k in tdef.sequence:
            v = tdef.sequence[k]
            out.append({"key": f"sequence.{k}", "label": f"Cut order: {k.replace('_', ' ')}", "value": _jsonable(v),
                        "number": isinstance(v, (int, float)) and not isinstance(v, bool)})
    return out


def check(preset: dict) -> dict:
    """A preset as it is kept: a name, the toolpath type, and the fields kept (``{"value": ...}`` or ``{"expr": "a sum"}``)."""
    if not isinstance(preset, dict):
        raise PresetError("a preset is an object")
    name = str(preset.get("name") or "").strip()
    if not name or len(name) > 120:
        raise PresetError("a preset needs a name (at most 120 characters)")
    op = str(preset.get("op") or "")
    flds = preset.get("fields")
    if not isinstance(flds, dict) or not flds:
        raise PresetError("a preset keeps at least one thing")
    known = {v[0] for v in VARIABLES}
    out = {}
    for k, f in flds.items():
        if not isinstance(k, str) or not re.fullmatch(r"(name|tool|safe_z|clearance|group|sheet_group|params\.\w+|sequence\.\w+)", k):
            raise PresetError(f"{k!r} is not something a preset can keep")
        if not isinstance(f, dict) or ("value" not in f and "expr" not in f):
            raise PresetError(f"{k}: a value or a sum")
        if "expr" in f:
            expr = str(f["expr"])
            names = calc.identifiers(expr)
            bad = [n for n in names if n not in known]
            if bad:
                raise PresetError(f"{k}: {', '.join(bad)} is not a variable ({', '.join(sorted(known))})")
            if calc.evaluate(expr, {n: 1.0 for n in known}) is None:
                raise PresetError(f"{k}: '{expr}' is not a sum")
            out[k] = {"expr": expr}
        else:
            out[k] = {"value": f["value"]}
    return {"id": str(preset.get("id") or uuid.uuid4().hex[:12]), "name": name, "op": op, "fields": out}


def variables(project, sheet=None, tool: Tool | None = None) -> dict:
    sheet = sheet or project.sheets[0]
    m = project.material
    d = tool.diameter if tool is not None else 0.0
    return {"material_thickness": project.sheet_thickness(sheet), "sheet_width": sheet.width, "sheet_height": sheet.height,
            "safe_z": m.safe_z, "clearance": m.clearance, "tool_diameter": d, "tool_radius": d / 2}


def _text(v, vals: dict) -> str:
    return _FIELD.sub(lambda m: f"{vals[m.group(1)]:g}" if m.group(1) in vals else m.group(0), str(v))


def apply(preset: dict, tdef: ToolpathDef, project, sheet=None, op_params=None) -> list[str]:
    """Put what the preset keeps on the toolpath (in place); the sums are worked out with this project (and sheet). Returns notes
    about what could not be put (a setting the toolpath type does not have ...)."""
    notes = []
    flds = preset.get("fields") or {}
    params = {p.name: p for p in (op_params or [])}
    if "tool" in flds and "value" in flds["tool"]:
        try:
            tdef.tool = Tool.from_dict(flds["tool"]["value"])
        except (TypeError, ValueError, KeyError):
            notes.append("the tool of the preset could not be read")
    vals = variables(project, sheet, tdef.tool)
    for k, f in flds.items():
        if k == "tool":
            continue
        if "expr" in f:
            v = calc.evaluate(f["expr"], vals)
            if v is None:
                notes.append(f"{k}: '{f['expr']}' could not be worked out")
                continue
        else:
            v = f["value"]
        if k == "name":
            tdef.name = _text(v, vals)
        elif k in ("group", "sheet_group"):
            setattr(tdef, k, str(v or ""))
        elif k in ("safe_z", "clearance"):
            setattr(tdef, k, None if v is None else float(v))
        elif k.startswith("params."):
            name = k[7:]
            if params and name not in params:
                notes.append(f"{name}: this toolpath type has no such setting")
                continue
            v = _unjson(v)
            tdef.params[name] = params[name].coerce(v) if name in params and v is not None else v
        elif k.startswith("sequence."):
            tdef.sequence[k[9:]] = v
    return notes

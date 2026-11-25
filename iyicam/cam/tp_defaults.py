"""The person's own defaults of new toolpaths, kept in the user folder (``toolpath_defaults.json``): how far a through cut
goes below the bottom of the material (0.2 mm unless set) and how far a drill through it goes (1 mm unless set). 0 is a
default like any other: a cut that stops at the bottom.

The desktop's toolpath window and the schema the web app builds its forms from take them, so a new toolpath starts with
them; a toolpath that exists keeps its own."""
from __future__ import annotations

import json

from ..core import paths

FILE_NAME = "toolpath_defaults.json"
#: what can be set: key, label, default (mm)
KEYS = [("through_overcut", "Through cuts go below the bottom by", 0.2),
        ("drill_overcut", "Drilling through goes below the bottom by", 1.0)]


def file():
    return paths.user_dir() / FILE_NAME


def load() -> dict:
    out = {k: d for k, _, d in KEYS}
    try:
        data = json.loads(file().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return out
    for k, _, _ in KEYS:
        v = data.get(k) if isinstance(data, dict) else None
        if isinstance(v, (int, float)) and not isinstance(v, bool) and 0 <= v <= 100:
            out[k] = float(v)
    return out


def save(values: dict) -> dict:
    """Keep the defaults given (those not given stay); answers all of them. A value below 0 or not a number is refused."""
    cur = load()
    for k, _, _ in KEYS:
        if k in values:
            v = values[k]
            if not isinstance(v, (int, float)) or isinstance(v, bool) or not 0 <= v <= 100:
                raise ValueError(f"{k}: a length from 0 to 100 mm")
            cur[k] = float(v)
    f = file()
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text(json.dumps(cur, indent=2), encoding="utf-8")
    return cur


def apply(op_id: str, params: list, values: dict | None = None) -> list:
    """``params`` (of a toolpath type) with the person's defaults put in as their defaults (in place; returned)."""
    vals = values if values is not None else load()
    for p in params:
        if p.name == "overcut":
            p.default = vals["drill_overcut"] if op_id == "drill" else vals["through_overcut"]
    return params

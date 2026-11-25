"""Declarative parameter schemas.

Toolpath strategies, vector commands and plugins describe their settings with
:class:`Param` objects; the GUI builds its forms from them automatically.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, Iterable

KINDS = ("float", "length", "int", "bool", "choice", "angle", "str", "text",
         "tool", "feed", "percent", "point", "font")


@dataclass
class Param:
    name: str
    label: str
    kind: str = "float"
    default: Any = None
    min: float | None = None
    max: float | None = None
    step: float | None = None
    choices: list[tuple[str, str]] | None = None  # (value, label)
    group: str = ""
    help: str = ""
    visible: dict[str, Any] | None = None  # {"other_param": value | [values]}
    decimals: int = 3
    advanced: bool | None = None  # None: decided by :func:`is_advanced` (what is rarely changed is kept out of sight)
    remember: bool = True  # False: a new toolpath / dialog starts with the default, not with the value chosen last

    def __post_init__(self) -> None:
        if self.kind not in KINDS:
            raise ValueError(f"unknown parameter kind {self.kind!r}")
        if self.kind == "choice" and not self.choices:
            raise ValueError(f"choice parameter {self.name!r} needs choices")

    def coerce(self, v: Any) -> Any:
        if v is None:
            return None
        k = self.kind
        if k in ("float", "length", "angle", "feed", "percent", "int") and not isinstance(v, bool):
            try:
                finite = math.isfinite(float(v))
            except (TypeError, ValueError):
                finite = True                                # (a text: the conversion below says what is wrong with it)
            if not finite:                                   # (NaN and infinity are not numbers a setting can have)
                return self.default
        if k in ("float", "length", "angle", "feed", "percent"):
            v = float(v)
        elif k == "int":
            v = int(round(float(v)))
        elif k == "bool":
            v = v if isinstance(v, bool) else (bool(v) if isinstance(v, (int, float)) else str(v).lower() in ("1", "true", "yes", "on"))
        elif k == "choice":
            v = str(v)
            if v not in [c[0] for c in self.choices or []]:
                v = self.choices[0][0]
            return v
        elif k == "point":
            v = (float(v[0]), float(v[1]))
            return v
        elif k in ("str", "text", "font"):
            return str(v)
        else:
            return v
        if isinstance(v, (int, float)) and not isinstance(v, bool):
            if self.min is not None and v < self.min:
                v = type(v)(math.ceil(self.min) if isinstance(v, int) else self.min)
            if self.max is not None and v > self.max:
                v = type(v)(math.floor(self.max) if isinstance(v, int) else self.max)
        return v


#: settings that most people never change: forms show them behind "More options"
ADVANCED_GROUPS = {"Ramp & Leads", "Advanced", "Flat areas", "Ramps", "Entry"}
ADVANCED_NAMES = {
    "start_depth", "stepdown", "direction", "allowance", "sharp_corners", "finish_pass", "start_mode",
    "tab_mode", "tab_3d", "tab_min_length", "tab_spacing", "rest_from", "order", "raster_angle", "profile_pass",
    "finish_wall", "match_tolerance", "tip_compensation", "line_width", "resolution", "smoothing", "retract", "dwell",
    "flat_depth", "glue_gap", "margin", "mirror", "ramp_units", "ramp_start", "ramp_end", "profile", "depth_mode",
}


def is_advanced(p: "Param") -> bool:
    """A setting that is rarely changed (so a form keeps it behind its "More options" button)."""
    if p.advanced is not None:
        return p.advanced
    return p.group in ADVANCED_GROUPS or p.name in ADVANCED_NAMES


@dataclass
class ParamSet:
    params: list[Param] = field(default_factory=list)

    def __iter__(self):
        return iter(self.params)

    def get(self, name: str) -> Param | None:
        for p in self.params:
            if p.name == name:
                return p
        return None


def resolve(params: Iterable[Param], values: dict[str, Any] | None) -> dict[str, Any]:
    """Merge ``values`` over the defaults and coerce every value to its kind."""
    values = values or {}
    out: dict[str, Any] = {}
    for p in params:
        v = values.get(p.name, p.default)
        out[p.name] = p.coerce(v) if v is not None else None
    # keep unknown keys (forward compatibility, plugin extras)
    for k, v in values.items():
        out.setdefault(k, v)
    return out


def is_visible(p: Param, values: dict[str, Any]) -> bool:
    if not p.visible:
        return True
    for key, want in p.visible.items():
        have = values.get(key)
        if isinstance(want, (list, tuple, set)):
            if have not in want:
                return False
        elif have != want:
            return False
    return True

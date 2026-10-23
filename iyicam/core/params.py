"""Declarative parameter schemas.

Toolpath strategies, vector commands and plugins describe their settings with
:class:`Param` objects; the GUI builds its forms from them automatically.
"""
from __future__ import annotations

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

    def __post_init__(self) -> None:
        if self.kind not in KINDS:
            raise ValueError(f"unknown parameter kind {self.kind!r}")
        if self.kind == "choice" and not self.choices:
            raise ValueError(f"choice parameter {self.name!r} needs choices")

    def coerce(self, v: Any) -> Any:
        if v is None:
            return None
        k = self.kind
        if k in ("float", "length", "angle", "feed", "percent"):
            v = float(v)
        elif k == "int":
            v = int(round(float(v)))
        elif k == "bool":
            v = v if isinstance(v, bool) else str(v).lower() in ("1", "true", "yes", "on")
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
                v = type(v)(self.min)
            if self.max is not None and v > self.max:
                v = type(v)(self.max)
        return v


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

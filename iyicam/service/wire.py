"""Plain data helpers of the service: parameter schemas, base64, JSON clean-up."""
from __future__ import annotations

import base64
import math
from typing import Any

from ..core.params import Param
from ..core.tooling import Tool
from .errors import ServiceError


def b64e(data: bytes) -> str:
    return base64.b64encode(data).decode("ascii")


def b64d(text: Any) -> bytes:
    try:
        return base64.b64decode(text or "", validate=False)
    except (ValueError, TypeError) as exc:
        raise ServiceError("bad_request", f"the data is not valid base64: {exc}") from exc


def jsonable(v: Any) -> Any:
    """Plain JSON values: tools become dicts, tuples lists, NaN / infinity ``None`` (JSON has no such numbers)."""
    if isinstance(v, Tool):
        return v.to_dict()
    if isinstance(v, bool) or v is None or isinstance(v, (str, int)):
        return v
    if isinstance(v, float):
        return v if math.isfinite(v) else None
    if isinstance(v, dict):
        return {str(k): jsonable(x) for k, x in v.items()}
    if isinstance(v, (list, tuple, set, frozenset)):
        return [jsonable(x) for x in v]
    return str(v)


def param_to_dict(p: Param) -> dict:
    return {"name": p.name, "label": p.label, "kind": p.kind, "default": jsonable(p.default),
            "min": p.min, "max": p.max, "step": p.step,
            "choices": [[str(v), str(l)] for v, l in p.choices] if p.choices else None,
            "group": p.group, "help": p.help, "visible": jsonable(p.visible), "decimals": p.decimals}


def tool_values(params: list[Param], values: dict | None) -> dict:
    """Parameters of kind ``tool`` arrive as dicts in JSON; the commands want :class:`Tool` objects."""
    out = dict(values or {})
    for p in params:
        v = out.get(p.name)
        if p.kind == "tool" and isinstance(v, dict):
            out[p.name] = Tool.from_dict(v.get("__tool__", v))
    return out

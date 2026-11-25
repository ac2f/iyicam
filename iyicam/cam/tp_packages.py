"""Packages of toolpaths: several toolpaths kept together under a name, to make them all at once on the vectors that are
selected (for one job: an outside cut 10 mm off at 2 mm deep, an inside cut 3 mm in at 1 mm deep, ... all with a 4 mm tool).

A package is put on the selected vectors or on **layers** (every vector of them, on every sheet or on the open one): then each
of its toolpaths machines the layers chosen, or the layers of its own (by name: an outside cut on ``Outer``, an inside cut on
``Inner``).

A package has

* a **tool** that its toolpaths use (each one may have its own instead); when the package is put on a drawing another tool can
  be chosen for that time, the package keeps its own;
* its **toolpaths** (``items``): a name, a type (``profile`` ...), the settings, and which settings are **asked** when it is put
  on a drawing (the depth, the allowance ...: changed for that time, as the tool);
* whether it makes a **group** of toolpaths, and the name of that group.

Names may have ``{variables}``: those of the package are put in when the toolpaths are made (``{package}``, ``{sheet_number}``,
``{sheet_name}``, ``{sheet_group}``, ``{n}``: the next number of the group, ``{group}``), those of a toolpath stay and follow it as in every toolpath
name (``{tool_diameter}``, ``{cut_depth}``, ``{allowance}`` ...). A group named ``{package} S{sheet_number}-{n:02}`` is
``Contour S3-01`` on sheet 3, then ``Contour S3-02`` the next time.

The packages are kept in ``toolpath_packages.json`` in the user folder.
"""
from __future__ import annotations

import copy
import json
import os
import re
import uuid
from pathlib import Path

from ..core import paths
from ..core.document import Selection, Tool, ToolpathDef, _jsonable, _unjson

FILE_NAME = "toolpath_packages.json"
#: the variables of a package, for the name of its group and of its toolpaths
VARIABLES: list[tuple[str, str, str]] = [
    ("package", "Name of the package", "Paketin adı"),
    ("sheet_number", "Number of the sheet (1, 2 ...)", "Tabakanın numarası (1, 2 ...)"),
    ("sheet_name", "Name of the sheet", "Tabakanın adı"),
    ("n", "The next number: one more each time the package makes a group of that name", "Sıradaki numara: paket o adla her "
                                                                                       "grup yaptığında bir artar"),
    ("group", "Name of the group the package made", "Paketin yaptığı grubun adı"),
    ("sheet_group", "Group of the sheet (or empty)", "Tabakanın grubu (ya da boş)"),
]
#: the settings asked by default when a package is put on a drawing (those a toolpath type has)
DEFAULT_ASK = ("cut_depth", "allowance")
DEFAULT_GROUP = "{package} S{sheet_number}-{n:02}"


class PackageError(ValueError):
    pass


# ------------------------------------------------------------------------------------------------ kept in a file
def file() -> Path:
    return paths.user_dir() / FILE_NAME


def load() -> list[dict]:
    try:
        data = json.loads(file().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    items = data.get("packages") if isinstance(data, dict) else None
    out = []
    for p in items if isinstance(items, list) else []:
        try:
            out.append(check(p))
        except PackageError:
            continue
    return out


def _write(items: list[dict]) -> None:
    f = file()
    f.parent.mkdir(parents=True, exist_ok=True)
    tmp = f.with_name(f.name + ".tmp")
    tmp.write_text(json.dumps({"format": "iyicam-toolpath-packages", "packages": items}, indent=1, ensure_ascii=False),
                   encoding="utf-8")
    os.replace(tmp, f)


def save(pkg: dict) -> dict:
    """Keep a package (one with the same id is replaced, in its place). Returns it as kept."""
    p = check(pkg)
    items = load()
    for i, x in enumerate(items):
        if x["id"] == p["id"]:
            items[i] = p
            break
    else:
        items.append(p)
    _write(items)
    return p


def delete(pid: str) -> bool:
    items = load()
    keep = [x for x in items if x["id"] != pid]
    if len(keep) == len(items):
        return False
    _write(keep)
    return True


def get(pid: str) -> dict | None:
    return next((p for p in load() if p["id"] == pid), None)


# ------------------------------------------------------------------------------------------------ what a package is
def check(pkg: dict) -> dict:
    """A package as it is kept (missing parts filled in); PackageError when it cannot be one."""
    if not isinstance(pkg, dict):
        raise PackageError("a package is an object")
    name = str(pkg.get("name") or "").strip()
    if not name or len(name) > 120:
        raise PackageError("a package needs a name (at most 120 characters)")
    tool = pkg.get("tool")
    if tool is not None:
        try:
            tool = Tool.from_dict(tool).to_dict()
        except (TypeError, ValueError, KeyError) as exc:
            raise PackageError(f"the tool of the package cannot be read: {exc}") from exc
    items = []
    for i, it in enumerate(pkg.get("items") or []):
        if not isinstance(it, dict) or not str(it.get("op") or ""):
            raise PackageError(f"toolpath {i + 1} of the package has no type")
        own = it.get("tool")
        if own is not None:
            try:
                own = Tool.from_dict(own).to_dict()
            except (TypeError, ValueError, KeyError) as exc:
                raise PackageError(f"the tool of toolpath {i + 1} cannot be read: {exc}") from exc
        params = it.get("params") if isinstance(it.get("params"), dict) else {}
        ask = [str(a) for a in (it.get("ask") or []) if isinstance(a, str)]
        layers = [str(x) for x in (it.get("layers") or []) if isinstance(x, str) and x]
        items.append({"name": str(it.get("name") or it["op"]), "op": str(it["op"]), "params": params, "tool": own,
                      "ask": ask, "layers": layers, "safe_z": it.get("safe_z"), "clearance": it.get("clearance"),
                      "sequence": it.get("sequence") if isinstance(it.get("sequence"), dict) else {}})
    if tool is None and any(it["tool"] is None for it in items):
        raise PackageError("a toolpath of the package has no tool: give the package a tool")
    return {"id": str(pkg.get("id") or uuid.uuid4().hex[:12]), "name": name, "tool": tool, "items": items,
            "make_group": bool(pkg.get("make_group", True)), "group_name": str(pkg.get("group_name") or DEFAULT_GROUP),
            "this_sheet": bool(pkg.get("this_sheet", True))}


def new(name: str, tool: Tool | None = None) -> dict:
    return check({"name": name, "tool": tool.to_dict() if tool is not None else None, "items": []})


def item_from_toolpath(tdef: ToolpathDef, package_tool: dict | None, op_params=None, project=None) -> dict:
    """A toolpath as an item of a package: its name, type, settings and (when it differs from the package's) its tool; the depth
    and the allowance are asked when the package is put on a drawing (when the type has them)."""
    own = None
    if package_tool is None or Tool.from_dict(package_tool).to_dict() | {"id": ""} != tdef.tool.to_dict() | {"id": ""}:
        own = tdef.tool.to_dict()
    names = {p.name for p in op_params} if op_params is not None else set(tdef.params)
    seq = {k: _jsonable(v) for k, v in tdef.sequence.items() if k not in ("order", "auto_order")}
    layers = []
    if project is not None and tdef.selection.mode == "layers":             # (the layers it cut: by name, for the package on layers)
        names_of = {l.id: l.name for l in project.layers}
        layers = [names_of[i] for i in tdef.selection.layer_ids if i in names_of]
    return {"name": tdef.name, "op": tdef.op, "params": {k: _jsonable(v) for k, v in tdef.params.items()}, "tool": own,
            "ask": [a for a in DEFAULT_ASK if a in names], "safe_z": tdef.safe_z, "clearance": tdef.clearance,
            "sequence": seq, "layers": layers}


def from_toolpaths(name: str, tdefs: list[ToolpathDef], registry=None, project=None) -> dict:
    """A package made of toolpaths: the tool of the first is the package's; the others keep theirs when it is another."""
    if not tdefs:
        raise PackageError("choose the toolpaths of the package")
    tool = tdefs[0].tool.to_dict()
    items = []
    for t in tdefs:
        op = registry.operations.get(t.op) if registry is not None else None
        items.append(item_from_toolpath(t, tool, op.params() if op is not None else None, project))
    return check({"name": name, "tool": tool, "items": items})


# ------------------------------------------------------------------------------------------------ putting it on a drawing
def sheet_number(project, sheet) -> int:
    return next((i for i, s in enumerate(project.sheets) if s.id == sheet.id), 0) + 1


def variables(pkg: dict, project, sheet, n: int = 1, group: str = "") -> dict:
    return {"package": pkg["name"], "sheet_number": str(sheet_number(project, sheet)), "sheet_name": sheet.name,
            "n": str(n), "group": group, "sheet_group": getattr(sheet, "group", "") or ""}


def group_name(pkg: dict, project, sheet, template: str | None = None) -> tuple[str, int]:
    """The name of the group the package makes on this sheet now, and its number ``{n}``: the first number whose name no
    toolpath group of the project has yet (``Contour S1-01``, then ``-02`` ...)."""
    from ..post.naming import fill
    template = template if template is not None else pkg["group_name"]
    used = {t.group for t in project.toolpaths if t.group}
    name = ""
    for n in range(1, 10000):
        name = fill(template, variables(pkg, project, sheet, n))
        if name not in used or "{n" not in template:
            return name, n
    return name, 1


def toolpath_name(template: str, pkg: dict, project, sheet, n: int = 1, group: str = "") -> str:
    """The name of a toolpath of the package: its package variables put in (those of the toolpath stay: they follow it)."""
    from ..post.naming import fill
    vals = variables(pkg, project, sheet, n, group)
    vals.pop("sheet_group")              # (a toolpath variable too: it stays in the name and follows the sheet the toolpath is on)
    return fill(template, vals)


def build(pkg: dict, project, sheet, selection: Selection, registry, *, tool: Tool | None = None,
          values: dict[int, dict] | None = None, names: dict[int, str] | None = None, skip: set[int] | None = None,
          make_group: bool | None = None, group_template: str | None = None, this_sheet: bool | None = None,
          item_tools: dict[int, Tool] | None = None,
          item_layers: dict[int, list[str]] | None = None) -> tuple[list[ToolpathDef], str]:
    """The toolpaths of the package on ``selection`` (new definitions; nothing is added to the project) and the name of the group
    they are in ("" for none).

    ``tool`` is the tool of the package for this time; ``values`` the asked settings of each item (by its index), ``names`` the
    names typed for this time, ``skip`` the items left out, ``item_tools`` a tool for this time of an item that has its own.

    On layers (``selection`` of mode ``layers``) a toolpath machines the layers of ``item_layers`` (ids, chosen for this time),
    else the layers of its own (``layers``: names; those the project has), else the layers of ``selection``."""
    values, names, skip, item_tools = values or {}, names or {}, skip or set(), item_tools or {}
    item_layers = item_layers or {}
    by_name = {l.name: l.id for l in project.layers}
    make_group = pkg["make_group"] if make_group is None else make_group
    this_sheet = pkg["this_sheet"] if this_sheet is None else this_sheet
    group, n = group_name(pkg, project, sheet, group_template) if make_group else ("", 1)
    base_tool = tool if tool is not None else (Tool.from_dict(pkg["tool"]) if pkg["tool"] is not None else None)
    out = []
    for i, it in enumerate(pkg["items"]):
        if i in skip:
            continue
        op = registry.operations.get(it["op"])
        if op is None:
            raise PackageError(f"the toolpath type '{it['op']}' is not available (a plugin that is off?)")
        if i in item_tools:
            t_tool = item_tools[i].copy()
        elif it["tool"] is not None:
            t_tool = Tool.from_dict(it["tool"])
        else:
            t_tool = base_tool.copy()
        known = {p.name: p for p in op.params()}
        params = {}
        for k, v in it["params"].items():
            v = _unjson(v)
            params[k] = known[k].coerce(v) if k in known and v is not None else v
        for k, v in (values.get(i) or {}).items():
            params[k] = known[k].coerce(v) if k in known and v is not None else v
        if op.id == "profile" and "cut_depth" not in params and not params.get("through"):
            params["cut_depth"] = project.sheet_thickness(sheet)
        name = toolpath_name(names.get(i, it["name"]), pkg, project, sheet, n, group)
        sel = copy.deepcopy(selection)
        if selection.mode == "layers":
            own = item_layers.get(i) or [by_name[n] for n in it.get("layers") or [] if n in by_name]
            if own:
                sel = Selection("layers", [], list(own))
            if not sel.layer_ids:
                raise PackageError(f"'{it['name']}': choose the layers it machines")
        t = ToolpathDef(name, op.id, t_tool, params, sel, True,
                        [sheet.id] if this_sheet else None, safe_z=it.get("safe_z"), clearance=it.get("clearance"))
        t.sequence.update(copy.deepcopy(it.get("sequence") or {}))
        t.group = group
        out.append(t)
    return out, group


def asked_params(item: dict, registry) -> list:
    """The settings of an item asked when the package is put on a drawing (``Param`` of its type, its value as default)."""
    op = registry.operations.get(item["op"])
    if op is None:
        return []
    out = []
    for p in op.params():
        if p.name in item["ask"]:
            q = copy.copy(p)
            if p.name in item["params"]:
                v = _unjson(item["params"][p.name])
                try:
                    q.default = p.coerce(v) if v is not None else None
                except (TypeError, ValueError):
                    pass
            q.visible = None                       # (asked: shown whatever the other settings are)
            out.append(q)
    return out


_SAFE = re.compile(r"[^\w .\-]+")


def export_name(pkg: dict) -> str:
    return (_SAFE.sub("_", pkg["name"]).strip() or "package") + ".icampkg"

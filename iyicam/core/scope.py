"""Isolated groups of sheets: a part of the project that keeps to itself.

A group of sheets that is *isolated* is like a project of its own inside the project:

* its **layers** are its own (``Layer.sheet_group``): on its sheets only they are there, and they are not on any other sheet;
* its **toolpaths** are its own (``ToolpathDef.sheet_group``): calculated, listed and drawn on its sheets only; the toolpaths
  of the rest of the project are not calculated on them;
* **nesting** that starts on one of its sheets nests onto its sheets and puts the new sheets in it; nesting elsewhere does
  not touch it;
* **Save G-code** of "every sheet" from one of its sheets writes its sheets; from elsewhere, the sheets that are not isolated.

A single sheet is isolated as a group of its own (named after it).  Every sheet of an isolated group has
``Sheet.isolated`` set; a sheet put into the group becomes isolated, one taken out of it is not any more.  Whatever is
done, no vector is left on a layer that is not there on its sheet (``fix_layers``): it goes to the layer of the same name
of its side (made when there is none).
"""
from __future__ import annotations

import copy

from .document import Layer, Project, Sheet, new_id


def scope_of(sheet: Sheet | None) -> str:
    """The isolated group ``sheet`` is in ("" = the rest of the project)."""
    return sheet.group if sheet is not None and sheet.isolated and sheet.group else ""


def isolated_groups(project: Project) -> list[str]:
    return list(dict.fromkeys(s.group for s in project.sheets if s.isolated and s.group))


def is_isolated(project: Project, group: str) -> bool:
    return bool(group) and any(s.group == group and s.isolated for s in project.sheets)


def sheets_of_scope(project: Project, scope: str) -> list[Sheet]:
    """The sheets of a side: those of the isolated group ``scope``, or ("") every sheet that is not isolated."""
    if scope:
        return [s for s in project.sheets if s.group == scope and s.isolated]
    return [s for s in project.sheets if not scope_of(s)]


def sheets_with(project: Project, sheet: Sheet | None) -> list[Sheet]:
    """The sheets on the same side as ``sheet`` ("every sheet" for whatever is done from it)."""
    return sheets_of_scope(project, scope_of(sheet))


def groups_of_scope(project: Project, scope: str) -> list[str]:
    """The groups of sheets one may nest onto, choose ... from a side: the isolated group itself, or the groups that are not
    isolated."""
    if scope:
        return [scope]
    return list(dict.fromkeys(s.group for s in project.sheets if s.group and not s.isolated))


def toolpath_in_scope(project: Project, tdef, scope: str) -> bool:
    """Whether a toolpath belongs to a side (the toolpaths of an isolated group, or those of the rest)."""
    if scope:
        return tdef.sheet_group == scope
    return not is_isolated(project, tdef.sheet_group)


def layer_in_scope(project: Project, layer: Layer, scope: str) -> bool:
    if scope:
        return layer.sheet_group == scope
    return not is_isolated(project, layer.sheet_group)


def new_item_scope(sheet: Sheet | None) -> str:
    """The ``sheet_group`` a layer or a toolpath made while ``sheet`` is open gets: its isolated group, else none."""
    return scope_of(sheet)


# ---------------------------------------------------------------------------------------------------------------------
# keeping the layers right
# ---------------------------------------------------------------------------------------------------------------------

def layer_for(project: Project, like: Layer, sheet: Sheet) -> Layer:
    """The layer of ``sheet``'s side that a vector on ``like`` goes to: one of the same name there, else a copy of it."""
    scope = scope_of(sheet)
    for l in project.layers:
        if l.name == like.name and l.applies(sheet) and (l.sheet_group == scope if scope else not l.sheet_group
                                                         and l.sheet_ids is None):
            return l
    for l in project.layers:
        if l.name == like.name and l.applies(sheet):
            return l
    new = Layer(like.name, like.color, like.visible, like.locked, new_id(), None, scope, like.group)
    i = project.layers.index(like) + 1 if like in project.layers else len(project.layers)
    project.layers.insert(i, new)
    return new


def fix_layers(project: Project, sheets: list[Sheet] | None = None) -> dict[str, str]:
    """Every vector of ``sheets`` (every sheet) on a layer that is not there on its sheet goes to the layer of its side
    (``layer_for``).  Returns ``{old layer id: new layer id}`` of what was moved."""
    moved: dict[str, str] = {}
    for s in sheets if sheets is not None else project.sheets:
        for v in s.vectors:
            l = project.layer(v.layer_id)
            if l is None:
                continue
            if not l.applies(s):
                to = layer_for(project, l, s)
                moved[l.id] = to.id
                v.layer_id = to.id
    return moved


# ---------------------------------------------------------------------------------------------------------------------
# groups, isolation
# ---------------------------------------------------------------------------------------------------------------------

def unique_group_name(project: Project, base: str) -> str:
    names = {s.group for s in project.sheets if s.group}
    base = (base or "Group").strip() or "Group"
    if base not in names:
        return base
    k = 2
    while f"{base} {k}" in names:
        k += 1
    return f"{base} {k}"


def set_group(project: Project, ids, name: str) -> list[Sheet]:
    """Put the sheets ``ids`` in the group ``name`` ("" takes them out of their group).  A sheet that goes into an isolated
    group is isolated with it; one that leaves an isolated group is not any more.  Their vectors keep their layers right
    (``fix_layers``).  Returns the sheets that changed."""
    name = (name or "").strip()
    ids = set(ids)
    iso = is_isolated(project, name)
    changed = []
    for s in project.sheets:
        if s.id in ids and (s.group != name or s.isolated != iso):
            s.group, s.isolated = name, iso and bool(name)
            changed.append(s)
    if changed:
        fix_layers(project, changed)
    return changed


def rename_group(project: Project, old: str, new: str) -> None:
    """A group's new name, on its sheets and on the layers and toolpaths kept to it."""
    new = (new or "").strip()
    if not new or new == old:
        return
    if any(s.group == new for s in project.sheets):       # (two groups are not merged by a new name: one may be isolated)
        raise ValueError(f"there is a group of sheets named {new} already")
    for s in project.sheets:
        if s.group == old:
            s.group = new
    for l in project.layers:
        if l.sheet_group == old:
            l.sheet_group = new
    for t in project.toolpaths:
        if t.sheet_group == old:
            t.sheet_group = new


def isolate(project: Project, ids) -> str:
    """Isolate the sheets ``ids``: their group (the group they are all in, else a new group of them named after the first
    one) becomes a part of the project of its own.  What is on them comes along: the layers their vectors are on are
    copied to the group (the vectors go to the copies), and the toolpaths of the rest of the project that were calculated
    on them become the group's (moved when they were calculated only there, copied when on other sheets too, each with the
    vectors of its side).  Returns the name of the group."""
    from ..cam.job import sheets_for
    ids = list(dict.fromkeys(ids))
    sheets = [s for s in project.sheets if s.id in set(ids)]
    if not sheets:
        return ""
    groups = {s.group for s in sheets}
    if len(groups) == 1 and next(iter(groups)):
        name = next(iter(groups))
        sheets = [s for s in project.sheets if s.group == name]       # (the whole group: it is isolated as one)
    else:
        name = unique_group_name(project, sheets[0].name)
    mine = {s.id for s in sheets}
    # the toolpaths that are calculated on them now (before anything changes)
    def cuts_on(t) -> list[str]:                         # (a toolpath of chosen vectors is where they are)
        on = sheets_for(project, t)
        if t.selection.mode != "layers":
            ids = set(t.selection.vector_ids)
            on = [s for s in on if any(v.id in ids for v in s.vectors)]
        return [s.id for s in on]
    before = {t.id: cuts_on(t) for t in project.toolpaths}
    for s in sheets:
        s.group, s.isolated = name, True
    moved = fix_layers(project, sheets)                    # (the layers come along as the group's copies)
    if not any(l.sheet_group == name for l in project.layers) and project.layers:
        layer_for(project, project.layers[0], sheets[0])     # (a group with nothing on it yet has a layer to draw on)
    vec_ids = {v.id for s in sheets for v in s.vectors}
    new_toolpaths = []
    for t in list(project.toolpaths):
        if t.sheet_group == name:
            continue
        on = before.get(t.id, [])
        if not any(sid in mine for sid in on):
            continue
        only_here = all(sid in mine for sid in on)
        target = t if only_here else copy.deepcopy(t)
        if not only_here:
            target.id = new_id()
            new_toolpaths.append((t, target))
        target.sheet_group, target.sheet_ids = name, None
        sel = target.selection
        if sel.mode == "layers":
            sel.layer_ids = [moved.get(l, l) for l in sel.layer_ids]
        elif not only_here:
            sel.vector_ids = [v for v in sel.vector_ids if v in vec_ids]
            t.selection.vector_ids = [v for v in t.selection.vector_ids if v not in vec_ids]
    for orig, cp in new_toolpaths:
        project.toolpaths.insert(project.toolpaths.index(orig) + 1, cp)
    return name


def unisolate(project: Project, group: str) -> list[Sheet]:
    """The group is part of the project again: what is the rest's is on its sheets too.  Its own layers and toolpaths stay
    its own (kept to the group)."""
    out = []
    for s in project.sheets:
        if s.group == group and s.isolated:
            s.isolated = False
            out.append(s)
    return out


def group_new_sheets(project: Project, made: list[Sheet], group, current: Sheet | None,
                     used: list[Sheet] | None = None) -> str:
    """The group the sheets a nesting made go into (asked after the nesting): ``group`` a name (a new or an existing
    group), ``""`` none, ``None`` the default: the group of the sheet the nesting was started on.  On an isolated group the
    new sheets are always its own (nothing leaves it).  Returns the group they are in.

    ``used``: the sheets that were there already and got parts (the sheet the nesting started on): a group chosen takes
    them too, so the whole layout is in it (they stayed out, the group had only the new ones). "No group" leaves them in
    their groups; one in an isolated group stays there."""
    if not made:
        return ""
    side = scope_of(current)
    if side:
        name = side
    elif group is None:
        name = current.group if current is not None else ""
    else:
        name = str(group).strip()
        if is_isolated(project, name):                     # (an isolated group takes nothing from the rest)
            name = ""
    set_group(project, [s.id for s in made], name)
    if name:
        keep = {s.id for s in made}
        join = [s.id for s in (used or []) if s.id not in keep and not scope_of(s) and s.group != name]
        if join:
            set_group(project, join, name)
    return name

"""The warning about vectors that are not closed (their last node is not on their first).

An open vector is nested as a thin line (other parts may go inside a frame whose corner does not quite meet), and a
pocket, a V-carving or a profile inside / outside leaves it out or cuts it on the line. Before nesting and before such a
toolpath the window says how many there are and how wide the widest gap is, and offers to close them (a line from the
end back to the start, as Vectors ▸ Close Vectors does), to select them (to look at them), or to go on.
"""
from __future__ import annotations

import math

from PySide6.QtWidgets import QCheckBox, QMessageBox

from ..core import vectorops
from ..core.geom import Line

MEMORY = "warn:open_vectors"          # (settings memory: {"off": true} when the warning is not wanted any more)


def open_contours(objects) -> list[tuple[object, int, float]]:
    """``(vector, contour index, gap mm)`` of every open contour of these vectors."""
    out = []
    for o in objects:
        for i, c in enumerate(o.contours):
            if not c.closed and c.segs:
                out.append((o, i, math.dist(c.start, c.end)))
    return out


def closable(c) -> bool:
    """A single straight line is not closed (a line back over itself has no inside)."""
    return not (len(c.segs) == 1 and isinstance(c.segs[0], Line))


def nearly_closed(c, gap: float) -> bool:
    """The ends a hair apart for the size of the vector: it was meant to be closed (a drawing exported with a gap)."""
    bb = c.bbox()
    return gap <= max(0.5, 0.02 * math.hypot(bb.width, bb.height))


def needs_closed(op, values: dict) -> bool:
    """Whether a toolpath of this kind with these settings works on the inside of closed vectors."""
    from ..cam.cutcheck import needs_closed as engine_needs_closed
    return engine_needs_closed(op, values)


def ask(parent, ctrl, objects, what: str) -> bool:
    """The warning for these vectors before ``what`` ("nesting", "this pocket toolpath" ...). True: go on (closed if
    asked); False: stop (also when the open vectors were selected to look at them)."""
    found = open_contours(objects)
    if not found:
        return True
    memo = ctrl.settings.recall(MEMORY)
    if memo.get("off"):
        return True
    vectors = list({o.id: o for o, _, _ in found}.values())
    near = [g for o, i, g in found if nearly_closed(o.contours[i], g)]
    lines = len(found) - len(near)
    kinds = []
    if near:
        kinds.append(f"{len(near)} nearly closed (their ends {max(near):.3g} mm apart at most: meant to be closed)")
    if lines:
        kinds.append(f"{lines} open line(s) (the widest gap {max(g for o, i, g in found if not nearly_closed(o.contours[i], g)):.3g} mm)")
    effect = ("they are nested as thin lines: other parts may be placed inside a shape whose ends do not meet"
              if what == "nesting" else
              "the inside of an open vector is not known: it is left out or cut on the line")
    text = (f"{len(found)} contour(s) of {len(vectors)} vector(s) are not closed, their last node is not on their first: "
            + "; ".join(kinds) + f".\n\nFor {what} {effect}.")
    box = QMessageBox(QMessageBox.Warning, "Open vectors", text, QMessageBox.NoButton, parent)
    close_b = box.addButton("Close and continue", QMessageBox.AcceptRole)
    select_b = box.addButton("Select them", QMessageBox.ActionRole)
    go_b = box.addButton("Continue anyway", QMessageBox.DestructiveRole)
    box.addButton(QMessageBox.Cancel)
    box.setDefaultButton(close_b)
    box.setInformativeText("Close: a line is drawn from the end of each back to its start (Vectors ▸ Close Vectors); "
                           "a single straight line is left as it is.")
    again = QCheckBox("Do not warn me about open vectors again")
    box.setCheckBox(again)
    box.exec()
    clicked = box.clickedButton()
    if again.isChecked() and clicked in (close_b, go_b):
        ctrl.settings.remember(MEMORY, {"off": True})
    if clicked is close_b:
        close(ctrl, found)
        return True
    if clicked is select_b:
        ctrl.select([o.id for o in vectors])
        return False
    return clicked is go_b


def close(ctrl, found) -> int:
    """Close these open contours (one undo step); how many were closed."""
    todo = [(o, i) for o, i, _ in found if closable(o.contours[i])]
    if not todo:
        return 0
    by_vector: dict = {}
    for o, i in todo:
        by_vector.setdefault(id(o), (o, set()))[1].add(i)
    with ctrl.edit("Close open vectors"):
        for o, idx in by_vector.values():
            # (a gap of a hundredth or less is snapped shut, a wider one gets a line back to the start)
            o.contours = [vectorops.close(c, max(1e-3, min(0.01, math.dist(c.start, c.end)))) if i in idx else c
                          for i, c in enumerate(o.contours)]
    return len(todo)


def objects_of(ctrl, selection, sheets=None) -> list:
    """The vectors a toolpath's selection takes (its vectors, or every vector on its layers)."""
    sheets = sheets or ctrl.project.sheets
    if selection.mode == "layers":
        layers = set(selection.layer_ids)
        return [o for s in sheets for o in s.vectors if o.layer_id in layers]
    ids = set(selection.vector_ids)
    return [o for s in sheets for o in s.vectors if o.id in ids]


def refuse(parent, ctrl, problems) -> None:
    """Vectors that cannot be cut (on one another, parts closer together than the tool needs, a hole the tool does not
    fit in): the toolpath is not made. "Select them" selects the vectors to fix."""
    box = QMessageBox(QMessageBox.Critical, "Cannot be cut", problems.text().replace("; ", ".\n\n") + ".",
                      QMessageBox.NoButton, parent)
    box.setInformativeText("The toolpath is not made. Move the parts apart, weld the vectors that overlap or delete "
                           "the copy, or take a smaller tool.")
    select_b = box.addButton("Select them", QMessageBox.ActionRole)
    box.addButton(QMessageBox.Close)
    box.exec()
    if box.clickedButton() is select_b:
        ctrl.select(problems.vector_ids())


def cuttable(parent, ctrl, tdef) -> bool:
    """Whether the vectors of a new / changed toolpath can be cut with its tool; if not the window says why."""
    from ..cam.cutcheck import check_toolpath
    op = ctrl.registry.operations.get(tdef.op)
    if op is None:
        return True
    problems = check_toolpath(op, op(tdef.params).values, objects_of(ctrl, tdef.selection), tdef.tool.diameter)
    if problems:
        refuse(parent, ctrl, problems)
        return False
    return True

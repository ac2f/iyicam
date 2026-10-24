"""Document controller: the single source of truth shared by canvas, panels and dialogs."""
from __future__ import annotations

import time
from contextlib import contextmanager
from pathlib import Path

from PySide6.QtCore import QObject, QTimer, Signal

from ..cam.job import ComputeResult, compute_all, sheet_toolpaths, sheets_for
from ..core import paths
from ..core.commands import Command, CommandContext, CommandError, run_command
from ..core.document import Project, Sheet, ToolpathDef, VectorObject
from ..core.registry import REGISTRY, Registry
from ..core.tooling import ToolLibrary
from ..core.undo import UndoStack
from .settings import Settings, Units


class Controller(QObject):
    changed = Signal()            # document content changed (vectors, layers, sheets, material)
    selection_changed = Signal()
    sheet_changed = Signal()      # active sheet switched
    toolpaths_changed = Signal()  # toolpath definitions or results changed
    project_replaced = Signal()   # new / opened project
    message = Signal(str)

    AUTO_LIMIT_S = 1.5  # stop auto recalculation when a full calculation is slower than this

    def __init__(self, registry: Registry = REGISTRY, settings: Settings | None = None):
        super().__init__()
        self.registry = registry
        self.settings = settings or Settings()
        self.units = Units(self.settings.units)
        self.tools = ToolLibrary.load_or_default(paths.tools_file())
        self.undo_stack = UndoStack()
        self.project = Project()
        self.sheet_id = self.project.sheets[0].id
        self.layer_id = self.project.layers[0].id
        self.selection: list[str] = []
        self.results: dict[tuple[str, str], ComputeResult] = {}
        self.backplot: list = []  # toolpaths read from an existing G-code file (display / simulation only)
        self.stale = False
        self.dirty = False
        self.last_compute_s = 0.0
        self._recalc = QTimer(self)
        self._recalc.setSingleShot(True)
        self._recalc.setInterval(250)
        self._recalc.timeout.connect(self._auto_recalculate)

    # -- convenience ------------------------------------------------------------------
    @property
    def sheet(self) -> Sheet:
        s = self.project.sheet(self.sheet_id)
        if s is None:
            s = self.project.sheets[0]
            self.sheet_id = s.id
        return s

    @property
    def layer(self):
        l = self.project.layer(self.layer_id)
        if l is None:
            l = self.project.layers[0]
            self.layer_id = l.id
        return l

    def selected_objects(self) -> list[VectorObject]:
        s = self.sheet
        return [v for v in (s.vector(i) for i in self.selection) if v is not None]

    def editable(self, obj: VectorObject) -> bool:
        layer = self.project.layer(obj.layer_id)
        return layer is None or (layer.visible and not layer.locked)

    # -- project lifecycle -----------------------------------------------------------------
    def _replace(self, project: Project) -> None:
        self.project = project
        self.sheet_id = project.sheets[0].id
        self.layer_id = project.layers[0].id
        self.selection = []
        self.results = {}
        self.stale = bool(project.toolpaths)
        self.undo_stack.clear()
        self.dirty = False
        self.project_replaced.emit()
        self.sheet_changed.emit()
        self.changed.emit()
        self.selection_changed.emit()
        self.toolpaths_changed.emit()
        if project.toolpaths and self.settings.auto_recalculate:
            self.recalculate()

    def new_project(self, width: float = 600.0, height: float = 400.0, thickness: float = 18.0) -> None:
        p = Project()
        p.sheets[0].width, p.sheets[0].height = width, height
        p.material.thickness = thickness
        self._replace(p)

    def open_project(self, path: str | Path) -> None:
        p = Project.load(path)
        self.registry.emit("project_loaded", p)
        self._replace(p)
        self.settings.add_recent(str(path))

    def save_project(self, path: str | Path | None = None) -> Path:
        path = Path(path or self.project.path)
        if path.suffix.lower() != ".icam":
            path = path.with_suffix(".icam")
        self.project.save(path)
        self.registry.emit("project_saved", self.project)
        self.dirty = False
        self.settings.add_recent(str(path))
        self.changed.emit()
        return path

    # -- editing ------------------------------------------------------------------------------
    @contextmanager
    def edit(self, label: str, geometry: bool = True):
        """Wrap a modification: undo checkpoint before, notifications after."""
        self.undo_stack.checkpoint(self.project, label)
        try:
            yield self.project
        except Exception:
            self.undo_stack.rollback(self.project)
            raise
        self.dirty = True
        self._after_change(geometry)

    def _after_change(self, geometry: bool = True) -> None:
        self.selection = [i for i in self.selection if self.sheet.vector(i) is not None]
        self.changed.emit()
        self.selection_changed.emit()
        if geometry and self.project.toolpaths:
            self.stale = True
            self.toolpaths_changed.emit()
            if self.settings.auto_recalculate and self.last_compute_s < self.AUTO_LIMIT_S:
                self._recalc.start()

    def notify(self, geometry: bool = True) -> None:
        """Announce changes made without :meth:`edit` (call ``checkpoint`` first)."""
        self.dirty = True
        self._after_change(geometry)

    def checkpoint(self, label: str) -> None:
        self.undo_stack.checkpoint(self.project, label)

    def undo(self) -> None:
        if self.undo_stack.undo(self.project):
            self._after_undo()

    def redo(self) -> None:
        if self.undo_stack.redo(self.project):
            self._after_undo()

    def _after_undo(self) -> None:
        if self.project.sheet(self.sheet_id) is None:
            self.sheet_id = self.project.sheets[0].id
            self.sheet_changed.emit()
        self.dirty = True
        self._after_change(True)
        self.toolpaths_changed.emit()

    # -- selection --------------------------------------------------------------------------------
    def select(self, ids, mode: str = "replace") -> None:
        ids = [i for i in ids if self.sheet.vector(i) is not None]
        if mode == "replace":
            new = list(dict.fromkeys(ids))
        elif mode == "add":
            new = list(dict.fromkeys(self.selection + ids))
        elif mode == "toggle":
            cur = list(self.selection)
            for i in ids:
                if i in cur:
                    cur.remove(i)
                else:
                    cur.append(i)
            new = cur
        else:  # remove
            new = [i for i in self.selection if i not in ids]
        if new != self.selection:
            self.selection = new
            self.selection_changed.emit()
            self.registry.emit("selection_changed", self.project, list(new))

    def set_sheet(self, sid: str) -> None:
        if sid != self.sheet_id and self.project.sheet(sid) is not None:
            self.sheet_id = sid
            self.selection = []
            self.sheet_changed.emit()
            self.selection_changed.emit()
            self.changed.emit()

    def set_layer(self, lid: str) -> None:
        self.layer_id = lid
        self.changed.emit()

    # -- vectors -----------------------------------------------------------------------------------
    def add_vectors(self, objs: list[VectorObject], label: str = "Add vectors", select: bool = True) -> None:
        with self.edit(label):
            self.sheet.vectors.extend(objs)
        if select:
            self.select([o.id for o in objs])

    def delete_selection(self) -> None:
        ids = [o.id for o in self.selected_objects() if self.editable(o)]
        if not ids:
            return
        with self.edit("Delete"):
            self.project.remove_vectors(ids)
        self.select([])

    def run_command(self, cmd: Command, values: dict | None = None) -> bool:
        ctx = CommandContext(self.project, self.sheet, [o.id for o in self.selected_objects()
                                                        if self.editable(o)], self.layer_id)
        self.undo_stack.checkpoint(self.project, cmd.label.rstrip("."))
        try:
            run_command(cmd, ctx, values)
        except CommandError as exc:
            self.undo_stack.discard_last()
            self.message.emit(str(exc))
            return False
        except Exception:
            self.undo_stack.rollback(self.project)
            raise
        for m in ctx.messages:
            self.message.emit(m)
        self.dirty = True
        if ctx.new_selection is not None:
            self.selection = [i for i in ctx.new_selection if self.sheet.vector(i) is not None]
        self._after_change(True)
        return True

    # -- toolpaths -----------------------------------------------------------------------------------
    def add_toolpath(self, tdef: ToolpathDef) -> None:
        with self.edit(f"Add toolpath {tdef.name}", geometry=False):
            self.project.toolpaths.append(tdef)
        self.recalculate({tdef.id})

    def replace_toolpath(self, tdef: ToolpathDef) -> None:
        with self.edit(f"Edit toolpath {tdef.name}", geometry=False):
            self.project.toolpaths = [tdef if t.id == tdef.id else t for t in self.project.toolpaths]
        self.recalculate({tdef.id})

    def remove_toolpaths(self, ids: set[str]) -> None:
        with self.edit("Delete toolpath", geometry=False):
            self.project.toolpaths = [t for t in self.project.toolpaths if t.id not in ids]
        self.results = {k: v for k, v in self.results.items() if k[0] not in ids}
        self.toolpaths_changed.emit()

    def move_toolpath(self, tid: str, delta: int) -> None:
        tps = self.project.toolpaths
        i = next((k for k, t in enumerate(tps) if t.id == tid), None)
        if i is None or not 0 <= i + delta < len(tps):
            return
        with self.edit("Reorder toolpaths", geometry=False):
            tps[i], tps[i + delta] = tps[i + delta], tps[i]
        self.toolpaths_changed.emit()

    def recalculate(self, only: set[str] | None = None, force_smart: set[str] | None = None,
                    apply_starts: bool = True) -> dict:
        from PySide6.QtCore import Qt
        from PySide6.QtWidgets import QApplication
        t0 = time.perf_counter()
        QApplication.setOverrideCursor(Qt.WaitCursor)
        try:
            res = compute_all(self.project, self.registry, only, self.results, force_smart)
        finally:
            QApplication.restoreOverrideCursor()
        if only is None:
            self.results = res
            self.stale = False
            self.last_compute_s = time.perf_counter() - t0
        else:
            self.results = {k: v for k, v in self.results.items() if k[0] not in only}
            self.results.update(res)
        self._store_orders(res)
        if apply_starts and self._apply_start_updates(res, force_smart or set()):
            # the vectors now carry the start points; toolpaths sharing them follow
            return self.recalculate(only, apply_starts=False)
        self.toolpaths_changed.emit()
        return res

    # -- cut order and start points -------------------------------------------------------------
    def _store_orders(self, res: dict) -> None:
        """Remember the calculated order: it is what a frozen order or a manual edit starts from."""
        for t in self.project.toolpaths:
            orders = [res[(t.id, s.id)].order for s in sheets_for(self.project, t)
                      if (t.id, s.id) in res and res[(t.id, s.id)].order is not None]
            if orders:
                t.sequence["order"] = [k for o in orders for k in o]

    def _apply_start_updates(self, res: dict, forced: set[str]) -> bool:
        """Write smart start points into the vectors (a new node where needed)."""
        from ..core import vectorops
        from ..core.geom import dist
        updates = {}
        for (tid, _), r in res.items():
            t = self.project.toolpath(tid)
            if t is not None and r.start_updates and (t.sequence.get("smart_starts") or tid in forced):
                updates.update(r.start_updates)
        if not updates:
            return False
        if forced:
            self.undo_stack.checkpoint(self.project, "Optimise start points")
        changed = 0
        for key, q in updates.items():
            vid, ci = key.rsplit(":", 1)
            o = self.find_vector(vid)
            ci = int(ci)
            if o is None or ci >= len(o.contours) or ci in o.locked_starts:
                continue
            c = vectorops.set_start_point(o.contours[ci], q)
            if dist(c.start, o.contours[ci].start) > 1e-3:
                o.contours[ci] = c
                changed += 1
        if not changed:
            if forced:
                self.undo_stack.discard_last()
            return False
        self.dirty = True
        self.changed.emit()
        if forced:
            self.message.emit(f"Moved {changed} start point(s)")
        return True

    def find_vector(self, vid: str) -> VectorObject | None:
        for s in self.project.sheets:
            o = s.vector(vid)
            if o is not None:
                return o
        return None

    def set_sequence(self, tid: str, **changes) -> None:
        """Change cut order / start point settings of a toolpath and recalculate it."""
        t = self.project.toolpath(tid)
        if t is None:
            return
        with self.edit("Cut order settings", geometry=False):
            t.sequence.update(changes)
        self.recalculate({tid})

    def reorder_now(self, tid: str) -> None:
        """Order the cuts with the toolpath's strategy once (a frozen order stays frozen)."""
        self.set_sequence(tid, order=[])

    def optimise_starts(self, tids: set[str]) -> None:
        """Move the start points of the toolpaths' vectors to the best places, once."""
        self.recalculate(set(tids), force_smart=set(tids))

    def cut_order(self, tid: str, sheet: Sheet | None = None) -> list[str]:
        """Cut keys of a toolpath on a sheet, in machining order."""
        t = self.project.toolpath(tid)
        s = sheet or self.sheet
        if t is None:
            return []
        on_sheet = {f"{o.id}:{i}" for o in self.project.vectors_for(t, s) for i in range(len(o.contours))}
        return [k for k in t.sequence.get("order", []) if k in on_sheet]

    def set_manual_order(self, tid: str, index: int, picked: list[str]) -> None:
        """Cut ``picked`` from position ``index`` (0 based) of this sheet's order on; freezes the order."""
        from ..cam.sequence import manual_from
        t = self.project.toolpath(tid)
        if t is None:
            return
        mine = self.cut_order(tid)
        new_mine = manual_from(mine, index, picked)
        it = iter(new_mine)
        mine_set = set(mine)
        order = [next(it) if k in mine_set else k for k in t.sequence.get("order", [])]
        self.set_sequence(tid, order=order, auto_order=False)

    def set_start_point(self, vid: str, ci: int, point, lock: bool = True) -> None:
        """Start a vector contour at ``point`` (a new node where there is none) and keep it there."""
        from ..core import vectorops
        o = self.find_vector(vid)
        if o is None or not 0 <= ci < len(o.contours) or not o.contours[ci].closed:
            return
        with self.edit("Set start point"):
            o.contours[ci] = vectorops.set_start_point(o.contours[ci], point)
            if lock and ci not in o.locked_starts:
                o.locked_starts.append(ci)

    def unlock_starts(self, ids: list[str]) -> None:
        objs = [o for o in (self.find_vector(i) for i in ids) if o is not None and o.locked_starts]
        if not objs:
            return
        with self.edit("Unlock start points"):
            for o in objs:
                o.locked_starts = []

    def _auto_recalculate(self) -> None:
        if self.stale:
            self.recalculate()

    def sheet_toolpaths(self, sheet: Sheet | None = None, enabled_only: bool = True):
        return sheet_toolpaths(self.project, self.results, sheet or self.sheet, enabled_only)

    def results_for(self, tdef: ToolpathDef) -> list[ComputeResult]:
        return [self.results[(tdef.id, s.id)] for s in sheets_for(self.project, tdef)
                if (tdef.id, s.id) in self.results]

    # -- misc -------------------------------------------------------------------------------------------
    def set_units(self, name: str) -> None:
        self.settings.units = name
        self.units = Units(name)
        self.changed.emit()

    def save_tools(self) -> None:
        self.tools.save(paths.tools_file())

"""Document controller: the single source of truth shared by canvas, panels and dialogs."""
from __future__ import annotations

import time
from contextlib import contextmanager
from pathlib import Path

from PySide6.QtCore import QObject, QTimer, Signal

from ..cam.job import ComputeCancelled, ComputeResult, compute_all, sheet_toolpaths, sheets_for
from ..core import clip, paths, placement, vectorops
from ..core.commands import Command, CommandContext, CommandError, run_command
from ..core.document import XY_ORIGINS, Project, Sheet, ToolpathDef, VectorObject
from ..core.geom import Pt, Transform
from ..core.registry import REGISTRY, Registry
from ..core.stock import StockItem, normalized
from ..core.tooling import ToolLibrary
from ..core.undo import UndoStack
from ..nesting.stock import leftover_of_sheet, stock_from_vectors
from .busy import run_busy
from .cutcontrol import CutControl, CutPreview
from .settings import Settings, Units


class Controller(CutControl, QObject):
    changed = Signal()            # document content changed (vectors, layers, sheets, material)
    selection_changed = Signal()
    sheet_changed = Signal()      # active sheet switched
    toolpaths_changed = Signal()  # toolpath definitions or results changed
    project_replaced = Signal()   # new / opened project
    message = Signal(str)
    command_result = Signal(object)   # a command handed back a report to show: (command, result dict)
    units_changed = Signal()      # the display units of every document changed (they are a setting of the window)
    cut_view_changed = Signal()   # the cut that is looked at, or the preview of a cut order, changed (redraw)

    AUTO_LIMIT_S = 1.5  # stop auto recalculation when a full calculation is slower than this
    last_result: dict | None = None   # what the last command that ran handed back (``CommandContext.result()``)

    def __init__(self, registry: Registry = REGISTRY, settings: Settings | None = None,
                 tools: ToolLibrary | None = None):
        """One document. The settings and the tool library are shared by the documents of a window (the tabs):
        pass the ones of the first."""
        super().__init__()
        self.registry = registry
        self.settings = settings or Settings()
        self._units = Units(self.settings.units, self.settings.feed_units)
        self.tools = tools if tools is not None else ToolLibrary.load_or_default(paths.tools_file())
        self.undo_stack = UndoStack()
        self.project = Project()
        self.sheet_id = self.project.sheets[0].id
        self.layer_id = self.project.layers[0].id
        self.selection: list[str] = []
        self.results: dict[tuple[str, str], ComputeResult] = {}
        self.backplot: list = []  # toolpaths read from an existing G-code file (display / simulation only)
        self.stale = False
        self._sigs: dict[str, str] = {}         # toolpath id -> what it depended on when it was calculated
        self.stale_ids: set[str] = set()        # the toolpaths whose own vectors or settings changed since
        self.dirty = False
        self.last_compute_s = 0.0
        self.busy_parent = None       # the window the "please wait" window of a long calculation belongs to
        self.cut_preview: CutPreview | None = None    # a toolpath calculated with other cut order settings (shown only)
        self.focus_cut: str | None = None             # the cut (vector contour key) the cut order panel points at
        self._computing = False
        self._compute_again = False
        self._recalc = QTimer(self)
        self._recalc.setSingleShot(True)
        self._recalc.setInterval(250)
        self._recalc.timeout.connect(self._auto_recalculate)

    def _busy_window(self):
        """The window the progress box belongs to, unless that is gone (a timer can fire while a window closes)."""
        w = self.busy_parent
        try:
            from shiboken6 import isValid
            return w if w is not None and isValid(w) else None
        except ImportError:
            return w

    # -- convenience ------------------------------------------------------------------
    @property
    def sheet(self) -> Sheet:
        s = self.project.sheet(self.sheet_id) if self.sheet_id is not None else None
        if s is None:
            if not self.project.sheets:            # (a blank project: no sheet yet; an empty one that is no part of it)
                if getattr(self, "_no_sheet", None) is None:
                    self._no_sheet = Sheet("(no sheet)", 0.0, 0.0)
                self._no_sheet.vectors = []
                return self._no_sheet
            s = self.project.sheets[0]
            self.sheet_id = s.id
        return s

    @property
    def has_sheet(self) -> bool:
        """Whether the project has a sheet to draw on (a blank one has none until one is added)."""
        return bool(self.project.sheets)

    @property
    def layer(self):
        l = self.project.layer(self.layer_id)
        if l is None or not l.applies(self.sheet):         # (a layer that is not on this sheet is not the current one)
            l = next(iter(self.project.layers_for(self.sheet)), None) or self.project.layers[0]
            self.layer_id = l.id
        return l

    @property
    def units(self) -> Units:
        if self._units.name != self.settings.units or self._units.feed != self.settings.feed_units:
            self._units = Units(self.settings.units, self.settings.feed_units)   # (changed through the settings, perhaps in another tab)
        return self._units

    @property
    def pristine(self) -> bool:
        """A new, untouched document: opening a file may take its place (a tab that was never used)."""
        p = self.project
        return (p.path is None and not self.dirty and not p.toolpaths and not self.undo_stack.can_undo
                and not any(s.vectors for s in p.sheets))

    @property
    def title(self) -> str:
        p = self.project
        return p.path.stem if p.path else p.name

    def selected_objects(self) -> list[VectorObject]:
        s = self.sheet
        return s.pick(self.selection)

    def editable(self, obj: VectorObject) -> bool:
        layer = self.project.layer(obj.layer_id)
        return layer is None or (layer.visible and not layer.locked and layer.applies(self.sheet))

    # -- project lifecycle -----------------------------------------------------------------
    def _replace(self, project: Project) -> None:
        self.cut_preview = None
        self.focus_cut = None
        self.project = project
        self.sheet_id = project.sheets[0].id if project.sheets else None
        self.layer_id = project.layers[0].id
        self.selection = []
        self.results = {}
        self._sigs = {}
        self.stale = bool(project.toolpaths)
        self.stale_ids = {t.id for t in project.toolpaths}
        self.undo_stack.clear()
        self.dirty = False
        self.project_replaced.emit()
        self.sheet_changed.emit()
        self.changed.emit()
        self.selection_changed.emit()
        self.toolpaths_changed.emit()
        kept = self._kept_results(project)
        if kept is not None:                       # (the toolpaths as they were calculated and saved: not calculated again)
            self.results, self.stale = kept, False
            self._mark_calculated({t.id for t in project.toolpaths})
            self.toolpaths_changed.emit()
        elif project.toolpaths and (self.settings.auto_recalculate or self.settings.calculate_on_open):
            self.recalculate()

    def new_project(self, width: float = 600.0, height: float = 400.0, thickness: float = 18.0, template: str = "",
                    values: dict | None = None) -> None:
        """A new project: blank with this size, or started by the template (a plugin's way to start a project)."""
        from ..core import presets
        p = presets.create(width, height, thickness, template, values, self.registry)
        self._apply_material_defaults(p)
        self._replace(p)

    def sheet_sizes(self) -> list[tuple[str, float, float]]:
        """The sheet sizes on offer: the person's list, the built-in ones, and what plugins add."""
        from ..core import presets
        return presets.sizes(self.settings.sheet_presets, self.registry)

    MATERIAL_MEMORY = ("z_zero", "xy_origin", "safe_z", "clearance", "home_x", "home_y", "home_z", "rapid_rate")

    def _apply_material_defaults(self, p: Project) -> None:
        """A new project starts with the material setup (heights, zero, origin, home) that was set last."""
        v = self.settings.recall("material")
        m = p.material
        try:
            if v.get("z_zero") in ("top", "bottom"):
                m.z_zero = v["z_zero"]
            if v.get("xy_origin") in XY_ORIGINS:
                m.xy_origin = v["xy_origin"]
            for name, attr, low in (("safe_z", "safe_z", 0.1), ("clearance", "clearance", 0.0),
                                    ("rapid_rate", "rapid_rate", 1.0)):
                if isinstance(v.get(name), (int, float)) and not isinstance(v.get(name), bool) and v[name] >= low:
                    setattr(m, attr, float(v[name]))
            home = [v.get("home_x"), v.get("home_y"), v.get("home_z")]
            if all(isinstance(x, (int, float)) and not isinstance(x, bool) for x in home):
                m.home = tuple(float(x) for x in home)
        except (TypeError, ValueError):
            pass

    def open_project(self, path: str | Path) -> None:
        p = Project.load(path)
        self.registry.emit("project_loaded", p)
        self._replace(p)
        self.settings.add_recent(str(path))

    @staticmethod
    def _kept_results(project: Project) -> dict | None:
        """The calculated toolpaths kept in the file the project came from, when they are of the project as it is."""
        from ..cam import store
        d = getattr(project, "calc_cache", None)
        if not d or not project.toolpaths:
            return None
        try:
            if not store.usable(project, d):
                return None
            return store.results_from_dict(d)
        except (KeyError, TypeError, ValueError, IndexError):          # (a file of another version: calculated as usual)
            return None

    def save_project(self, path: str | Path | None = None) -> Path:
        path = Path(path or self.project.path)
        if path.suffix.lower() != ".icam":
            path = path.with_suffix(".icam")
        calc = None
        if self.results and not self.stale and self.project.toolpaths and not self._computing:
            from ..cam import store           # (what is calculated is kept in the file, as it is)
            calc = store.results_to_dict(self.project, self.results)
        self.project.save(path, calc)
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
        _ = self.layer   # a current layer that is gone (undone, deleted) is replaced by the first one
        if self.cut_preview is not None:      # (a preview is of the document as it was)
            self.cut_preview = None
            self.cut_view_changed.emit()
        self.selection = [v.id for v in self.sheet.pick(self.selection)]
        self.changed.emit()
        self.selection_changed.emit()
        if geometry and self.project.toolpaths:
            self.refresh_stale()          # (only the toolpaths whose own vectors changed are old)
            self.toolpaths_changed.emit()
            if self.settings.auto_recalculate and self.last_compute_s < self.AUTO_LIMIT_S:
                self._recalc.start()

    def notify(self, geometry: bool = True) -> None:
        """Announce changes made without :meth:`edit` (call ``checkpoint`` first)."""
        self.dirty = True
        self._after_change(geometry)

    def checkpoint(self, label: str) -> None:
        self.undo_stack.checkpoint(self.project, label)

    def _keep_revisions(self):
        """The revision numbers of the G-code files are not part of what undo takes back: a number that was used is not used again."""
        import copy
        return copy.deepcopy(self.project.metadata.get("revisions"))

    def undo(self) -> None:
        kept = self._keep_revisions()
        if self.undo_stack.undo(self.project):
            if kept is not None:
                self.project.metadata["revisions"] = kept
            self._after_undo()

    def redo(self) -> None:
        kept = self._keep_revisions()
        if self.undo_stack.redo(self.project):
            if kept is not None:
                self.project.metadata["revisions"] = kept
            self._after_undo()

    def _after_undo(self) -> None:
        if self.project.sheet(self.sheet_id) is None:
            self.sheet_id = self.project.sheets[0].id if self.project.sheets else None
            self.sheet_changed.emit()
        self.dirty = True
        self._after_change(True)
        self.toolpaths_changed.emit()

    # -- selection --------------------------------------------------------------------------------
    def select(self, ids, mode: str = "replace") -> None:
        ids = [v.id for v in self.sheet.pick(ids)]
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

    def add_first_sheet(self, width: float, height: float, thickness: float | None = None, name: str = "") -> Sheet:
        """A sheet of this size (and its own thickness) added and opened."""
        with self.edit("Add sheet", geometry=False):
            s = self.project.add_sheet(name or None, width, height)
            if thickness is not None and abs(thickness - self.project.material.thickness) > 1e-9:
                if not any(x is not s for x in self.project.sheets):
                    self.project.material.thickness = thickness      # (the first sheet: the project's thickness)
                else:
                    s.thickness = thickness
        self.sheet_id = None
        self.set_sheet(s.id)
        return s

    def set_sheet(self, sid: str) -> None:
        if sid != self.sheet_id and self.project.sheet(sid) is not None:
            self.sheet_id = sid
            self.selection = []
            self.clear_cut_preview()                    # (a preview is of the sheet it was made for)
            self.set_focus_cut(None)
            self.sheet_changed.emit()
            self.selection_changed.emit()
            self.changed.emit()

    def set_layer(self, lid: str) -> None:
        self.layer_id = lid
        self.changed.emit()

    # -- vectors -----------------------------------------------------------------------------------
    def add_vectors(self, objs: list[VectorObject], label: str = "Add vectors", select: bool = True) -> None:
        objs = [o for o in objs if o.prune()]   # a vector of nothing is not added
        if not objs:
            return
        if not self.project.sheets:             # (a blank project: there is nothing to put them on yet)
            self.message.emit("Add a sheet first (Sheets panel: +): a blank project has no sheet to draw on")
            return
        with self.edit(label):
            self.sheet.vectors.extend(objs)
        if select:
            self.select([o.id for o in objs])

    # -- stock of plates (leftovers and sheet sizes that jobs are nested onto) --------------------------
    def set_stock(self, items: list[StockItem], label: str = "Edit stock") -> None:
        with self.edit(label, geometry=False):
            self.project.stock = list(items)

    def add_stock(self, items: list[StockItem], label: str = "Add to stock") -> None:
        with self.edit(label, geometry=False):
            self.project.stock.extend(items)

    def stock_from_selection(self, quantity: int = 1, leftover: bool = True) -> list[StockItem]:
        """Plates drawn as closed vectors: each outline is a plate, closed vectors inside it are its holes."""
        items = stock_from_vectors(self.selected_objects(), quantity, leftover)
        if items:
            self.add_stock(items, "Add selected vectors to stock")
        return items

    def leftover_to_stock(self, sheet: Sheet | None = None, clearance: float = 3.0, min_width: float = 30.0,
                          min_area: float = 2500.0) -> list[StockItem]:
        """What is left of a sheet once its parts are cut out, added to the stock as plates."""
        items = leftover_of_sheet(sheet or self.sheet, clearance, min_width, min_area)
        if items:
            self.add_stock(items, "Add leftover to stock")
        return items

    @staticmethod
    def _apply_outline(s: Sheet, outline: list[list[Pt]] | None) -> None:
        if not outline:
            s.outline = None
            return
        outline = clip.normalize(outline)
        w, h, norm = normalized(outline)
        T = Transform.translate(-min(x for poly in outline for x, _ in poly),
                                -min(y for poly in outline for _, y in poly))
        for v in s.vectors:
            v.transform(T)
        s.width, s.height, s.outline = w, h, norm

    def set_sheet_outline(self, outline: list[list[Pt]] | None, sheet: Sheet | None = None) -> None:
        """Make the sheet a plate of any shape (``None``: a plain rectangle again).

        The outline is given in sheet coordinates; the sheet is moved so the outline starts at the
        origin, with everything on it, and the sheet size becomes the size of the outline."""
        with self.edit("Sheet outline"):
            self._apply_outline(sheet or self.sheet, outline)

    def set_sheet_outline_from_selection(self) -> bool:
        """The selected closed vectors become the outline of the sheet (and are used up doing so)."""
        objs = [o for o in self.selected_objects() if any(c.closed for c in o.contours)]
        polys = clip.normalize([p for o in objs for p in vectorops.region([c for c in o.contours if c.closed])])
        if not polys:
            return False
        ids = {o.id for o in objs}
        with self.edit("Sheet outline"):
            self.sheet.vectors = [v for v in self.sheet.vectors if v.id not in ids]
            self._apply_outline(self.sheet, polys)
        self.select([])
        return True

    def delete_selection(self) -> None:
        ids = [o.id for o in self.selected_objects() if self.editable(o)]
        if not ids:
            return
        with self.edit("Delete"):
            self.project.remove_vectors(ids)
        self.select([])

    MAX_COPIES = 20000    # vectors one transformation may make: more is a mistake that would only hang the program

    def transform_selection(self, T: Transform, copies: int = 0, label: str = "Transform") -> bool:
        """Apply ``T`` to the selected vectors (one undo step). With ``copies`` > 0 the vectors stay as they are and
        that many copies are made, the first one moved by ``T``, the second by ``T`` twice, and so on; the last
        copies are selected, so that applying it again goes on from there. Whether anything was done."""
        objs = [o for o in self.selected_objects() if self.editable(o)]
        if not objs:
            self.message.emit("Select the vectors first")
            return False
        if abs(T.det) < 1e-12:
            self.message.emit("That would flatten the vectors to nothing")
            return False
        if placement.is_identity(T):          # (copies on the very spot of the vectors would only be a pile)
            return False
        if copies <= 0:
            with self.edit(label):
                for o in objs:
                    o.transform(T)
            return True
        if copies * len(objs) > self.MAX_COPIES:
            self.message.emit(f"That would make {copies * len(objs)} vectors; at most {self.MAX_COPIES} are made at once")
            return False
        last: list[VectorObject] = []
        with self.edit(label):
            step = Transform.identity()
            for k in range(copies):
                step = T @ step                   # (from the original each time: no error builds up)
                made = []
                for o in objs:
                    n = o.copy()
                    n.transform(step)
                    if n.prune():
                        self.sheet.vectors.append(n)
                        made.append(n)
                last = made or last
        self.select([n.id for n in last])
        return True

    def run_command(self, cmd: Command, values: dict | None = None, show_report: bool = True) -> bool:
        """Run a vector command as one undo step.  What it handed back is in :attr:`last_result`; a report in it is shown in a
        window (``show_report=False``: not, for a script that reads :attr:`last_result` itself)."""
        ctx = CommandContext(self.project, self.sheet, [o.id for o in self.selected_objects()
                                                        if self.editable(o)], self.layer_id)
        self.undo_stack.checkpoint(self.project, cmd.label.rstrip("."))
        try:
            run_command(cmd, ctx, values)
        except CommandError as exc:
            if self.undo_stack.unchanged(self.project):
                self.undo_stack.discard_last()
            else:   # it had already changed something when it found it cannot go on: back to how it was
                self.undo_stack.rollback(self.project)
                self._after_change(True)
            self.message.emit(str(exc))
            return False
        except Exception:
            self.undo_stack.rollback(self.project)
            raise
        result = ctx.result()
        self.last_result = result                                   # (what the command handed back: the console returns it)
        # (the status bar keeps one text: every message in it, not only the last one that overwrote the others)
        said = ctx.messages + ["⚠ " + w for w in ctx.warnings]
        if said:
            self.message.emit(" · ".join(said))
        self.dirty = True
        if ctx.new_selection is not None:
            self.selection = [v.id for v in self.sheet.pick(ctx.new_selection)]
        self._after_change(True)
        if result["report"] and show_report:
            self.command_result.emit((cmd, result))                 # (after the drawing is up to date: the window opens over it)
        return True

    # -- toolpaths -----------------------------------------------------------------------------------
    def add_toolpath(self, tdef: ToolpathDef) -> None:
        if self.settings.strategy_directions and "directions" not in tdef.sequence:
            tdef.sequence["directions"] = dict(self.settings.strategy_directions)
        for key, value in self.settings.sequence_defaults.items():
            if key in self.REMEMBERED_SEQUENCE:    # (the strategy is not: every new toolpath starts with the default one)
                tdef.sequence.setdefault(key, value)   # new toolpaths are ordered the way the last one was
        with self.edit(f"Add toolpath {tdef.name}", geometry=False):
            self.project.toolpaths.append(tdef)
        self.recalculate({tdef.id})

    def replace_toolpath(self, tdef: ToolpathDef) -> None:
        with self.edit(f"Edit toolpath {tdef.name}", geometry=False):
            self.project.toolpaths = [tdef if t.id == tdef.id else t for t in self.project.toolpaths]
        self.recalculate({tdef.id})

    #: what the toolpath window edits (the rest of a toolpath: its order, group, whether it is on, is not touched by it)
    EDITED_BY_WINDOW = ("name", "tool", "params", "selection", "sheet_ids", "sheet_group", "safe_z", "clearance")

    def apply_toolpath_edit(self, edited: ToolpathDef) -> bool:
        """The toolpath window was accepted: its fields are put on the toolpath as it is *now* (what changed meanwhile, like the
        cut order or the group, stays). False when the toolpath is gone."""
        cur = self.project.toolpath(edited.id)
        if cur is None:
            return False
        with self.edit(f"Edit toolpath {edited.name}", geometry=False):
            for f in self.EDITED_BY_WINDOW:
                setattr(cur, f, getattr(edited, f))
        self.recalculate({cur.id})
        return True

    def remove_toolpaths(self, ids: set[str]) -> None:
        with self.edit("Delete toolpath", geometry=False):
            self.project.toolpaths = [t for t in self.project.toolpaths if t.id not in ids]
        self.results = {k: v for k, v in self.results.items() if k[0] not in ids}
        if not self.project.toolpaths:
            self.stale = False                           # (nothing is left that could be old)
        self.toolpaths_changed.emit()

    # -- groups of toolpaths -----------------------------------------------------------------------------
    def groups(self) -> list[str]:
        """The names of the groups, in the order their first toolpath comes."""
        return list(dict.fromkeys(t.group for t in self.project.toolpaths if t.group))

    def group_ids(self, name: str) -> list[str]:
        return [t.id for t in self.project.toolpaths if t.group == name]

    def set_group(self, ids, name: str) -> int:
        """Put the toolpaths in the group ``name`` ("" takes them out of their group). How many changed."""
        name = (name or "").strip()
        ids = set(ids)
        todo = [t for t in self.project.toolpaths if t.id in ids and t.group != name]
        if not todo:
            return 0
        with self.edit("Group toolpaths" if name else "Ungroup toolpaths", geometry=False):
            for t in todo:
                t.group = name
        self.toolpaths_changed.emit()
        return len(todo)

    def rename_group(self, old: str, new: str) -> int:
        return self.set_group(self.group_ids(old), new) if new.strip() else 0

    def enable_only(self, ids, enabled: bool = True) -> None:
        """Switch the toolpaths ``ids`` on (``enabled``) and every other off: only they are saved and shown."""
        ids = set(ids)
        with self.edit("Switch toolpaths", geometry=False):
            for t in self.project.toolpaths:
                t.enabled = (t.id in ids) == enabled
        self.toolpaths_changed.emit()

    def move_sheet(self, sid: str, delta: int) -> bool:
        """Move a sheet up (-1) or down (1) in the list: the order of the sheets is the order of their files and of their numbers."""
        sh = self.project.sheets
        i = next((k for k, s in enumerate(sh) if s.id == sid), None)
        if i is None or not 0 <= i + delta < len(sh):
            return False
        with self.edit("Reorder sheets", geometry=False):
            sh[i], sh[i + delta] = sh[i + delta], sh[i]
        self.sheet_changed.emit()
        return True

    # -- groups of sheets --------------------------------------------------------------------------------
    def sheet_groups(self) -> list[str]:
        """The names of the groups of sheets, in the order their first sheet comes."""
        return list(dict.fromkeys(s.group for s in self.project.sheets if s.group))

    def sheets_in(self, name: str) -> list[Sheet]:
        return [s for s in self.project.sheets if s.group == name]

    def set_sheet_group(self, ids, name: str) -> int:
        """Put the sheets in the group ``name`` ("" takes them out of their group). How many changed. A toolpath of a group that
        is left without a sheet calculates nothing until sheets are put in it again."""
        name = (name or "").strip()
        ids = set(ids)
        todo = [s for s in self.project.sheets if s.id in ids and s.group != name]
        if not todo:
            return 0
        with self.edit("Group sheets" if name else "Ungroup sheets", geometry=False):
            for s in todo:
                s.group = name
        self.refresh_stale()
        self.toolpaths_changed.emit()
        self.sheet_changed.emit()
        self.calc_if_auto()
        return len(todo)

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
        """Calculate the toolpaths. A calculation that takes more than a moment runs in a thread: the program
        stays alive and a window with a Cancel button appears (nothing is changed when it is cancelled)."""
        if self._computing:   # asked from inside the event loop of a calculation that is running
            self._compute_again = True
            return {}
        t0 = time.perf_counter()
        events: list = []
        state: dict = {}

        def progress(k, n, name):
            state["k"], state["n"], state["label"] = k, n, f"Calculating {name} ({k + 1} of {n})..."

        self._computing = True
        try:
            res = run_busy(lambda: compute_all(self.project, self.registry, only, self.results, force_smart,
                                               cancel=lambda: state["cancel"].is_set(), progress=progress,
                                               events=events),
                           self._busy_window(), "Calculating toolpaths...", ComputeCancelled, state=state)
        except ComputeCancelled:
            self.stale = True
            self.message.emit("Calculation cancelled: Toolpaths > Recalculate All (F5) calculates again")
            self.toolpaths_changed.emit()
            return {}
        finally:
            self._computing = False
        for hook, args in events:   # the hooks of plugins run here, not in the thread
            self.registry.emit(hook, *args)
        if only is None:
            self.results = res
            self.stale = False
            self.last_compute_s = time.perf_counter() - t0
        else:
            self.results = {k: v for k, v in self.results.items() if k[0] not in only}
            self.results.update(res)
        self._mark_calculated({t.id for t in self.project.toolpaths} if only is None else set(only))
        self._store_orders(res)
        if apply_starts and self._apply_start_updates(res, force_smart or set()):
            # the vectors now carry the start points; toolpaths sharing them follow
            return self.recalculate(only, apply_starts=False)
        self.toolpaths_changed.emit()
        if self._compute_again:   # something asked for a calculation while this one ran
            self._compute_again = False
            return self.recalculate()
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
        """Change cut order / start point settings of a toolpath and recalculate it (this is a command: the
        panel calls it when the person presses Apply)."""
        t = self.project.toolpath(tid)
        if t is None:
            return
        with self.edit("Cut order settings", geometry=False):
            t.sequence.update(changes)
        self.recalculate({tid})

    def reset_order(self, tid: str, **changes) -> None:
        """Forget the order the toolpath has (a hand-made one too) and order it again from nothing with its
        settings (``changes`` are applied with it)."""
        self.set_sequence(tid, order=[], auto_order=True, **changes)

    def calc_if_auto(self, only: set[str] | None = None) -> None:
        """What an edit does about the toolpaths: they are calculated again only when the setting says so
        (otherwise they are marked as old and the person calculates when ready: F5)."""
        if self.stale and self.settings.auto_recalculate:
            self.recalculate(only)

    # cut order and start point settings that new toolpaths start with (what was chosen last)
    REMEMBERED_SEQUENCE = ("grouping", "direction", "smart_starts", "start_margin", "corner_distance")

    def remember_cut_order(self, **changes) -> None:
        """Cut order / start point settings chosen by the user: new toolpaths start with them."""
        keep = {k: v for k, v in changes.items() if k in self.REMEMBERED_SEQUENCE and v is not None}
        if keep:
            self.settings.sequence_defaults.update(keep)
            self.settings.save()

    def set_inner_first(self, on: bool) -> int:
        """Cut the inner pieces (göbek) of all parts first (``on``) or part by part, on every toolpath that
        cuts vector by vector and on the toolpaths made later. Returns how many toolpaths changed."""
        from ..cam.profile import ProfileOp
        grouping = "depth" if on else "part"
        self.remember_cut_order(grouping=grouping)
        ids = set()
        todo = [t for t in self.project.toolpaths
                if (op := self.registry.operations.get(t.op)) is not None and issubclass(op, ProfileOp)
                and t.sequence.get("grouping", "depth") != grouping]
        if todo:                                          # (no undo step, and nothing made dirty, when nothing changes)
            with self.edit("Cut order: inner pieces first" if on else "Cut order: part by part", geometry=False):
                for t in todo:
                    t.sequence["grouping"] = grouping
                    ids.add(t.id)
        if ids:
            self.recalculate(ids)
        return len(ids)

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

    def sheet_cut_keys(self, tid: str, sheet: Sheet | None = None) -> list[str]:
        """The cuts of a toolpath on a sheet in the order they are made; cuts the stored order does not have yet (a
        project opened with kept toolpaths, vectors added since) follow in the order the calculation made them."""
        t = self.project.toolpath(tid)
        s = sheet or self.sheet
        if t is None:
            return []
        mine = self.cut_order(tid, s)
        known = set(mine)
        res = self.results.get((tid, s.id))
        made = list(res.order or []) if res is not None else []
        keys = [f"{o.id}:{i}" for o in self.project.vectors_for(t, s) for i, c in enumerate(o.contours) if c.segs]
        valid = set(keys)
        extra = [k for k in dict.fromkeys(made) if k in valid and k not in known]
        known |= set(extra)
        return mine + extra + [k for k in keys if k not in known]

    def set_sheet_order(self, tid: str, new_mine: list[str], sheet: Sheet | None = None) -> None:
        """Cut this sheet's cuts of a toolpath in the order ``new_mine`` (the other sheets keep theirs); freezes the order."""
        t = self.project.toolpath(tid)
        if t is None or not new_mine:
            return
        want = set(new_mine)
        it = iter(new_mine)
        order = [next(it) if k in want else k for k in t.sequence.get("order", [])]
        order += list(it)                       # (cuts the stored order did not have)
        if order == list(t.sequence.get("order", [])) and t.sequence.get("auto_order") is False:
            return
        self.set_sequence(tid, order=order, auto_order=False)

    def set_manual_order(self, tid: str, index: int, picked: list[str]) -> None:
        """Cut ``picked`` from position ``index`` (0 based) of this sheet's order on; freezes the order."""
        from ..cam.sequence import manual_from
        if self.project.toolpath(tid) is not None:
            self.set_sheet_order(tid, manual_from(self.sheet_cut_keys(tid), index, picked))

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
            old = self.stale_ids & {t.id for t in self.project.toolpaths}
            self.recalculate(old if old and self.results and len(old) < len(self.project.toolpaths) else None)

    # -- which toolpaths are old ---------------------------------------------------------------------
    def _signature(self, t) -> str:
        from ..cam.store import toolpath_signature
        try:
            return toolpath_signature(self.project, t)
        except Exception:      # noqa: BLE001  (a toolpath that cannot be described is taken as changed)
            return ""

    def _mark_calculated(self, ids: set[str]) -> None:
        """The toolpaths ``ids`` are as they were calculated now."""
        for t in self.project.toolpaths:
            if t.id in ids:
                self._sigs[t.id] = self._signature(t)
        self.refresh_stale()

    def refresh_stale(self) -> set[str]:
        """Which toolpaths changed (their own vectors, sheets or settings) since they were calculated; ``stale`` is whether
        any did."""
        tps = self.project.toolpaths
        self._sigs = {k: v for k, v in self._sigs.items() if any(t.id == k for t in tps)}
        self.stale_ids = {t.id for t in tps if not self._sigs.get(t.id) or self._sigs[t.id] != self._signature(t)}
        self.stale = bool(self.stale_ids)
        return self.stale_ids

    def sheet_toolpaths(self, sheet: Sheet | None = None, enabled_only: bool = True):
        return sheet_toolpaths(self.project, self.results, sheet or self.sheet, enabled_only)

    def results_for(self, tdef: ToolpathDef) -> list[ComputeResult]:
        return [self.results[(tdef.id, s.id)] for s in sheets_for(self.project, tdef)
                if (tdef.id, s.id) in self.results]

    # -- misc -------------------------------------------------------------------------------------------
    def set_units(self, name: str) -> None:
        self.settings.units = name
        self.changed.emit()
        self.units_changed.emit()

    def save_tools(self) -> None:
        self.tools.save(paths.tools_file())

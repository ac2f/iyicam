"""Main application window."""
from __future__ import annotations

import html
import json
import traceback
from pathlib import Path

from PySide6.QtCore import QByteArray, QRectF, QSize, Qt, QUrl
from PySide6.QtGui import QAction, QActionGroup, QDesktopServices, QGuiApplication, QImage, QKeySequence
from PySide6.QtWidgets import (QApplication, QDialog, QDockWidget, QLabel, QMainWindow, QMenu,
                               QMessageBox, QProgressDialog, QTabWidget, QToolBar, QToolButton, QVBoxLayout)

from .. import __version__
from ..cam.moves import PathStats, format_time
from ..core.document import VectorObject, new_id, toolpath_from_dict, toolpath_to_dict
from ..core.geom import BBox, Transform
from ..core.params import Param
from ..formats.report import ImportReport, run_importer
from . import menus, theme
from .canvas import Canvas
from .console import Console
from .controller import Controller
from .dialogs import (CommandDialog, ExportDialog, ExportOptionsDialog, ImportDialog, LeftoverDialog, MaterialDialog,
                      NestingDialog, PluginsDialog, PostEditorDialog, PreferencesDialog, StockDialog, ToolDatabaseDialog,
                      ToolpathDialog, buttons)
from ..nesting.parts import find_duplicates, squeeze_lines
from .files import open_file, save_file
from .forms import ParamForm
from .icons import icon
from .panels import LayersPanel, PropertiesPanel, SheetsPanel, ToolpathsPanel
from .tabs import DocumentTabs
from .transform_panel import PAGES as TRANSFORM_PAGES, TransformPanel
from .tools import TOOLS

TOOL_KEYS = {"select": "S", "nodes": "N", "polyline": "P", "line": "L", "rectangle": "R", "circle": "C",
             "ellipse": "E", "polygon": "G", "star": "", "arc": "A", "text": "T", "measure": "M", "trim": "X",
             "start": "B"}
TOOL_LABELS = {"select": "Select / Transform", "nodes": "Edit Nodes", "polyline": "Polyline / Bezier",
               "line": "Line", "rectangle": "Rectangle", "circle": "Circle", "ellipse": "Ellipse",
               "polygon": "Polygon", "star": "Star", "arc": "Arc (3 points)", "text": "Text", "measure": "Measure",
               "trim": "Trim Vectors", "start": "Set Start Point"}


class MainWindow(QMainWindow):
    last_import_report: ImportReport | None = None

    def __init__(self, ctrl: Controller, plugin_manager=None):
        """``ctrl`` is the document of the first tab; every tab has a controller and a drawing of its own, and the
        panels show the one of the tab that is open. ``self.ctrl`` and ``self.canvas`` are those."""
        super().__init__()
        self.pm = plugin_manager
        self.setWindowTitle("iyiCAM")
        self.resize(1400, 900)
        self.setAcceptDrops(True)
        self.closed_tabs: list[str] = []       # paths of the projects closed (the last one first): Ctrl+Shift+T
        self._themed: list[tuple[QAction, str]] = []     # tool bar actions and the icon each wears (follows the theme)
        self.tabs = DocumentTabs()
        self.tabs.tabCloseRequested.connect(self.close_tab)
        self.tabs.new_requested.connect(self.new_project)
        self.tabs.menu_requested.connect(self._tab_menu)
        self.setCentralWidget(self.tabs)
        self._attach(ctrl)
        self._build_docks(ctrl)
        self._build_toolbar()
        self._build_status()
        self.build_menus()
        self.tabs.currentChanged.connect(self._show_document)
        self._show_document()
        try:        # (a theme that follows the system changes with it)
            QGuiApplication.styleHints().colorSchemeChanged.connect(self._system_scheme_changed)
        except AttributeError:
            pass
        if ctrl.settings.window_state:
            try:
                self.restoreState(QByteArray.fromBase64(ctrl.settings.window_state.encode()))
            except Exception:
                pass
        if ctrl.settings.window_geometry:
            try:  # the size and place of the window, as it was closed (after the docks, which would change it)
                self.restoreGeometry(QByteArray.fromBase64(ctrl.settings.window_geometry.encode()))
            except Exception:
                pass
        for _, ext in list(ctrl.registry.gui_extensions):
            self._safe(ext, self)
        ctrl.registry.emit("gui_ready", self)

    # -- documents (tabs) ------------------------------------------------------------------------
    @property
    def canvas(self) -> Canvas:
        """The drawing of the tab that is open."""
        return self.tabs.currentWidget()

    @property
    def ctrl(self) -> Controller:
        """The document of the tab that is open."""
        return self.tabs.currentWidget().ctrl

    def documents(self) -> list[Controller]:
        return [cv.ctrl for cv in self.tabs.documents()]

    def _attach(self, ctrl: Controller) -> Canvas:
        """Put a document in a new tab (and make it the one that is open)."""
        canvas = Canvas(ctrl)
        canvas.context_menu_hook = self._canvas_menu
        ctrl.busy_parent = self
        ctrl.message.connect(lambda m: self.statusBar().showMessage(m, 6000))
        ctrl.changed.connect(lambda c=ctrl: self._doc_changed(c))
        ctrl.project_replaced.connect(lambda c=ctrl: self._doc_changed(c))
        ctrl.units_changed.connect(lambda c=ctrl: self._units_changed(c))
        # (only the drawing that is shown tells the window about its tool, prompt and mouse)
        canvas.tool_changed.connect(lambda name, cv=canvas: cv is self.canvas and self._tool_changed(name))
        canvas.prompt.connect(lambda text, cv=canvas: cv is self.canvas and self.prompt.setText(text))
        canvas.cursor_moved.connect(lambda x, y, cv=canvas: cv is self.canvas and self._coords(x, y))
        i = self.tabs.addTab(canvas, "")
        self._label(i)
        self.tabs.setCurrentIndex(i)
        return canvas

    def _index_of(self, ctrl: Controller) -> int:
        return next((i for i, cv in enumerate(self.tabs.documents()) if cv.ctrl is ctrl), -1)

    def _label(self, i: int) -> None:
        ctrl = self.tabs.widget(i).ctrl
        name = ctrl.title.replace("&", "&&")
        self.tabs.setTabText(i, f"{name} \u2022" if ctrl.dirty else name)
        self.tabs.setTabToolTip(i, str(ctrl.project.path) if ctrl.project.path else "Not saved yet")

    def _doc_changed(self, ctrl: Controller) -> None:
        i = self._index_of(ctrl)
        if i >= 0:
            self._label(i)
        if ctrl is self.ctrl:
            self._update_title()
            self._update_actions()

    def _update_actions(self) -> None:
        u = self.ctrl.undo_stack
        if hasattr(self, "quick"):
            self.quick["undo"].setEnabled(u.can_undo)
            self.quick["redo"].setEnabled(u.can_redo)

    # -- look ---------------------------------------------------------------------------------------
    def set_theme(self, name: str) -> None:
        """Light, dark or the system's look for the whole program (kept in the settings)."""
        s = self.ctrl.settings
        s.theme = name if name in theme.THEMES else "system"
        theme.apply(QApplication.instance(), s.theme)
        self.apply_theme()
        try:
            s.save()
        except OSError:
            pass

    def apply_theme(self) -> None:
        """The icons and the drawings take the colours of the theme that is applied."""
        for action, name in self._themed:
            action.setIcon(icon(name))
        for cv in self.tabs.documents():
            cv._tp_cache = None
            cv.update()
        for name, a in getattr(self, "theme_actions", {}).items():
            a.setChecked(name == self.ctrl.settings.theme)

    def _system_scheme_changed(self, *_) -> None:
        if self.ctrl.settings.theme == "system":
            self.set_theme("system")

    def _units_changed(self, source: Controller) -> None:
        for c in self.documents():
            if c is not source:
                c.changed.emit()

    def _show_document(self, *_) -> None:
        """The tab that is open changed: the panels, the tool and the title follow it."""
        c, cv = self.ctrl, self.canvas
        for panel in (self.layers_panel, self.sheets_panel, self.props_panel, self.transform_panel, self.toolpaths_panel,
                      self.console):
            panel.bind(c)
        cv._tp_cache = None                    # (settings may have changed while another tab was open)
        cv.update()
        self._tool_changed(cv.tool.name)
        self.prompt.setText(cv.tool.prompt)
        self.sim_action.setChecked(cv.show_sim)
        self._update_title()
        self._update_actions()
        cv.setFocus()

    def _new_tab(self) -> Controller:
        """A new tab with an empty project (the settings and the tools are shared with the others)."""
        first = self.ctrl
        c = Controller(first.registry, first.settings, first.tools)
        self._attach(c)
        return c

    def _blank_values(self) -> tuple[float, float, float]:
        p = [Param("width", "Width (X)", "length", 600.0, min=1.0), Param("height", "Height (Y)", "length", 400.0, min=1.0),
             Param("thickness", "Thickness", "length", 18.0, min=0.01)]
        v = {**{q.name: q.default for q in p}, **self.ctrl.settings.recall("new_project", p)}
        return v["width"], v["height"], v["thickness"]

    def _name_new(self, ctrl: Controller, base: str = "Untitled") -> None:
        """A project that was never saved is told from the others by a number: Untitled, Untitled 2, ..."""
        taken = {c.title for c in self.documents() if c is not ctrl}
        name, n = base, 1
        while name in taken:
            n += 1
            name = f"{base} {n}"
        ctrl.project.name = name
        self._doc_changed(ctrl)

    def activate(self, ctrl: Controller) -> None:
        i = self._index_of(ctrl)
        if i >= 0:
            self.tabs.setCurrentIndex(i)

    def tab_of_path(self, path) -> Controller | None:
        """The open document of this file, if there is one."""
        try:
            want = Path(path).resolve()
        except OSError:
            return None
        for c in self.documents():
            if c.project.path is not None:
                try:
                    if c.project.path.resolve() == want:
                        return c
                except OSError:
                    continue
        return None

    def close_tab(self, index: int | None = None) -> bool:
        """Close a tab (the open one without ``index``), asking first when it has changes. The last tab is
        emptied instead: there is always a project to draw on."""
        if index is None:
            index = self.tabs.currentIndex()
        if not 0 <= index < self.tabs.count():
            return False
        ctrl = self.tabs.widget(index).ctrl
        self.tabs.setCurrentIndex(index)
        if not self.confirm_discard(ctrl):
            return False
        if ctrl.project.path is not None:
            self.closed_tabs.insert(0, str(ctrl.project.path))
            del self.closed_tabs[20:]
        if self.tabs.count() == 1:
            ctrl.new_project(*self._blank_values())
            self._name_new(ctrl)
            return True
        canvas = self.tabs.widget(index)
        ctrl._recalc.stop()
        self.tabs.removeTab(index)
        canvas.setParent(None)
        canvas.deleteLater()
        return True

    def close_other_tabs(self, keep: int) -> None:
        keep_ctrl = self.tabs.widget(keep).ctrl
        for c in [c for c in self.documents() if c is not keep_ctrl]:
            i = self._index_of(c)
            if i < 0 or not self.close_tab(i):
                break
        self.activate(keep_ctrl)

    def reopen_closed_tab(self) -> None:
        while self.closed_tabs:
            path = self.closed_tabs.pop(0)
            if Path(path).is_file():
                self.open_project(path)
                return

    def duplicate_tab(self, index: int) -> None:
        """A copy of the project of a tab in a new tab (to try another layout or settings)."""
        from ..core.document import project_from_dict
        src = self.tabs.widget(index).ctrl
        c = self._new_tab()
        c.project = project_from_dict(src.project.to_dict())
        c._replace(c.project)
        c.dirty = True
        self._name_new(c, f"{src.title} copy")

    def next_tab(self, step: int = 1) -> None:
        n = self.tabs.count()
        if n > 1:
            self.tabs.setCurrentIndex((self.tabs.currentIndex() + step) % n)

    def _tab_menu(self, index: int, pos) -> None:
        if index < 0:
            return
        m = QMenu(self)
        ctrl = self.tabs.widget(index).ctrl
        self._act(m, "Close", lambda: self.close_tab(index))
        a = self._act(m, "Close Other Tabs", lambda: self.close_other_tabs(index))
        a.setEnabled(self.tabs.count() > 1)
        m.addSeparator()
        self._act(m, "Duplicate as New Tab", lambda: self.duplicate_tab(index))
        a = self._act(m, "Copy File Path", lambda: QApplication.clipboard().setText(str(ctrl.project.path)))
        a.setEnabled(ctrl.project.path is not None)
        menus.show_menu(m, pos)

    def dragEnterEvent(self, ev) -> None:
        if ev.mimeData().hasUrls():
            ev.acceptProposedAction()

    def dropEvent(self, ev) -> None:
        """Files dropped on the window: projects open in tabs, drawings are imported into the open one."""
        for url in ev.mimeData().urls():
            f = url.toLocalFile()
            if f.lower().endswith(".icam"):
                self._safe(self.open_project, f)
            elif f and self.ctrl.registry.importer_for(Path(f).suffix) is not None:
                self._safe(self.import_vectors, f)
        ev.acceptProposedAction()

    # -- layout ------------------------------------------------------------------------------------
    def _build_docks(self, c: Controller) -> None:
        self.layers_panel = LayersPanel(c)
        self.sheets_panel = SheetsPanel(c)
        tabs = QTabWidget()
        tabs.addTab(self.layers_panel, "Layers")
        tabs.addTab(self.sheets_panel, "Sheets")
        self.project_dock = self._dock("Project", tabs, Qt.LeftDockWidgetArea)
        self.props_panel = PropertiesPanel(c)
        self.props_dock = self._dock("Selection", self.props_panel, Qt.LeftDockWidgetArea)
        self.transform_panel = TransformPanel(c)
        self.transform_dock = self._dock("Transform", self.transform_panel, Qt.LeftDockWidgetArea)
        self.tabifyDockWidget(self.props_dock, self.transform_dock)
        self.transform_dock.raise_()
        self.toolpaths_panel = ToolpathsPanel(c, self)
        self.toolpaths_dock = self._dock("Toolpaths", self.toolpaths_panel, Qt.RightDockWidgetArea)
        self.console = Console(c, self)
        self.console_dock = self._dock("Python Console", self.console, Qt.BottomDockWidgetArea)
        self.console_dock.hide()
        self.resizeDocks([self.project_dock, self.toolpaths_dock], [270, 300], Qt.Horizontal)

    def _dock(self, title: str, widget, area) -> QDockWidget:
        d = QDockWidget(title, self)
        d.setObjectName(title.replace(" ", "_"))
        d.setWidget(widget)
        self.addDockWidget(area, d)
        return d

    def _build_toolbar(self) -> None:
        top = QToolBar("Main")
        top.setObjectName("MainToolbar")
        top.setMovable(False)
        top.setIconSize(QSize(22, 22))
        self.addToolBar(Qt.TopToolBarArea, top)
        self.quick: dict[str, QAction] = {}

        def quick(name: str, text: str, slot, tip: str = "") -> QAction:
            a = QAction(icon(name), text, self)
            a.setToolTip(f"{text} ({tip})" if tip else text)
            a.triggered.connect(lambda checked=False: self._safe(slot))
            top.addAction(a)
            self._themed.append((a, name))
            self.quick[name] = a
            return a

        quick("new", "New project", self.new_project, "Ctrl+N")
        quick("open", "Open project", self.open_project, "Ctrl+O")
        quick("save", "Save project", self.save_project, "Ctrl+S")
        top.addSeparator()
        quick("undo", "Undo", lambda: self.ctrl.undo(), "Ctrl+Z")
        quick("redo", "Redo", lambda: self.ctrl.redo(), "Ctrl+Y")
        top.addSeparator()
        quick("fit", "Zoom to the whole sheet", lambda: self.canvas.zoom_extents(), "F")
        top.addSeparator()
        quick("nest", "Nest parts", self.nest, "Ctrl+Shift+N")
        tp = quick("toolpath", "New toolpath", lambda: None)
        self._toolpath_menu = QMenu(self)
        tp.setMenu(self._toolpath_menu)
        quick("recalculate", "Recalculate toolpaths", self.recalculate, "F5")
        quick("simulate", "Simulate", self.simulate, "F6")
        quick("gcode", "Save G-code", self.export_gcode, "Ctrl+Shift+E")
        button = top.widgetForAction(tp)
        if isinstance(button, QToolButton):
            button.setPopupMode(QToolButton.InstantPopup)

        tb = QToolBar("Drawing")
        tb.setObjectName("DrawingToolbar")
        tb.setOrientation(Qt.Vertical)
        self.addToolBar(Qt.LeftToolBarArea, tb)
        self.tool_group = QActionGroup(self)
        self.tool_actions: dict[str, QAction] = {}
        for name, cls in TOOLS.items():
            a = QAction(icon(name), TOOL_LABELS.get(name, name.title()), self)
            self._themed.append((a, name))
            a.setCheckable(True)
            key = TOOL_KEYS.get(name)
            if key:
                a.setShortcut(QKeySequence(key))
                a.setToolTip(f"{a.text()} ({key})")
            a.triggered.connect(lambda checked=False, n=name: self.set_tool(n))
            self.tool_group.addAction(a)
            tb.addAction(a)
            self.tool_actions[name] = a
            if name in ("nodes", "text"):
                tb.addSeparator()
        self.tool_actions["select"].setChecked(True)

    def _build_status(self) -> None:
        self.coords = QLabel()
        self.coords.setMinimumWidth(220)
        self.prompt = QLabel()
        self.statusBar().addWidget(self.prompt, 1)
        self.statusBar().addPermanentWidget(self.coords)

    def _coords(self, x: float, y: float) -> None:
        u = self.ctrl.units
        self.coords.setText(f"X {u.to_display(x):.{u.decimals}f}   Y {u.to_display(y):.{u.decimals}f}{u.suffix}")

    def _tool_changed(self, name: str) -> None:
        a = self.tool_actions.get(name)
        if a is not None and not a.isChecked():
            a.setChecked(True)

    def set_tool(self, name: str) -> None:
        self.canvas.set_tool(TOOLS[name](self.canvas))

    # -- menus --------------------------------------------------------------------------------------
    def _act(self, menu: QMenu, text: str, slot, shortcut=None, checkable=False, checked=False) -> QAction:
        a = menu.addAction(text)
        a.triggered.connect(lambda checked_=False: self._safe(slot) if not checkable else self._safe(slot, checked_))
        if shortcut:
            a.setShortcut(QKeySequence(shortcut))
        if checkable:
            a.setCheckable(True)
            a.setChecked(checked)
        return a

    def build_menus(self) -> None:
        mb = self.menuBar()
        mb.clear()
        c = self.ctrl            # (only for what every tab shares: the settings and the registry)
        s = c.settings
        m = mb.addMenu("&File")
        self._act(m, "&New...", self.new_project, QKeySequence.New)
        self._act(m, "&Open...", self.open_project, QKeySequence.Open)
        self.recent_menu = m.addMenu("Open &Recent")
        self.recent_menu.aboutToShow.connect(self._fill_recent)
        self._act(m, "Re&open Closed Tab", self.reopen_closed_tab, "Ctrl+Shift+T")
        m.addSeparator()
        self._act(m, "&Save", self.save_project, QKeySequence.Save)
        self._act(m, "Save &As...", self.save_project_as, QKeySequence.SaveAs)
        self._act(m, "Save A&ll", self.save_all)
        self._act(m, "&Close Tab", self.close_tab, QKeySequence.Close)
        m.addSeparator()
        self._act(m, "&Import Vectors...", self.import_vectors, "Ctrl+I")
        self._act(m, "&Export Vectors...", self.export_vectors)
        m.addSeparator()
        self._act(m, "Save &G-code...", self.export_gcode, "Ctrl+Shift+E")
        self._act(m, "Open G-code (&Backplot)...", self.open_gcode)
        self._act(m, "Job &Report...", self.job_report)
        self._act(m, "Export &Image...", self.export_image)
        m.addSeparator()
        self._act(m, "&Quit", self.close, QKeySequence.Quit)

        m = mb.addMenu("&Edit")
        self.undo_action = self._act(m, "&Undo", lambda: self.ctrl.undo(), QKeySequence.Undo)
        self.redo_action = self._act(m, "&Redo", lambda: self.ctrl.redo(), QKeySequence.Redo)
        m.aboutToShow.connect(self._update_undo)
        m.addSeparator()
        self._act(m, "&Delete", self.delete, QKeySequence.Delete)
        self._act(m, "D&uplicate", self.duplicate, "Ctrl+D")
        self._act(m, "Cu&t", self.cut, QKeySequence.Cut)
        self._act(m, "&Copy", self.copy, QKeySequence.Copy)
        self._act(m, "&Paste", self.paste, QKeySequence.Paste)
        m.addSeparator()
        self._command_menus(m, "Edit")
        m.addSeparator()
        self._act(m, "&Preferences...", lambda: PreferencesDialog(self.ctrl, self).exec())

        m = mb.addMenu("&View")
        self._act(m, "Zoom &Extents", lambda: self.canvas.zoom_extents(), "F")
        self._act(m, "Zoom &Selection", lambda: self.canvas.zoom_selection(), "Shift+F")
        m.addSeparator()
        self._act(m, "Show &Toolpaths", lambda v: self._toggle("show_toolpaths", v), None, True, s.show_toolpaths)
        self._act(m, "Show Tool &Width", lambda v: self._toggle("show_tool_width", v), None, True, s.show_tool_width)
        self._act(m, "Show &Directions", lambda v: self._toggle("show_directions", v), None, True, s.show_directions)
        self._act(m, "Show Cut &Order", lambda v: self._toggle("show_cut_order", v), None, True, s.show_cut_order)
        self.sim_action = self._act(m, "Show Si&mulation", self._toggle_sim, None, True, self.canvas.show_sim)
        m.addSeparator()
        self._act(m, "Clear &Guides", lambda: self.canvas.clear_guides())
        m.addSeparator()
        self._act(m, "Snap to &Grid", lambda v: self._toggle("snap_grid", v), "F7", True, s.snap_grid)
        self._act(m, "Snap to &Vectors", lambda v: self._toggle("snap_objects", v), "F8", True, s.snap_objects)
        m.addSeparator()
        for d in (self.project_dock, self.props_dock, self.transform_dock, self.toolpaths_dock, self.console_dock):
            m.addAction(d.toggleViewAction())

        tm = m.addMenu("&Appearance")
        group = QActionGroup(self)
        self.theme_actions = {}
        for name, label in (("system", "Follow the &System"), ("light", "&Light"), ("dark", "&Dark")):
            a = self._act(tm, label, lambda n=name: self.set_theme(n), None, True, s.theme == name)
            group.addAction(a)
            self.theme_actions[name] = a

        m = mb.addMenu("&Draw")
        for name, a in self.tool_actions.items():
            m.addAction(a)

        m = mb.addMenu("&Vectors")
        self._act(m, "Trace &Bitmap...", self.trace_bitmap)
        self._act(m, "Text on &Curve...", self.text_on_curve)
        sp = m.addMenu("&Start Points")
        self._act(sp, "Smart &Start Points...", self.start_points)
        sp.addAction(self.tool_actions["start"])
        self._act(sp, "&Release Start Points of the Selection", self.release_starts)
        m.addSeparator()
        subs = self._command_menus(m, "Vectors")
        if "/Transform" in subs:
            self._transform_panel_actions(subs["/Transform"])

        m = mb.addMenu("&Toolpaths")
        new = m.addMenu("&New Toolpath")
        for op in c.registry.operations.values():
            self._act(new, op.label + "...", lambda o=op.id: self.new_toolpath(o))
        self._act(m, "&Recalculate All", self.recalculate, "F5")
        self._act(m, "Cut &Order...", self.cut_order, "Ctrl+Shift+O")
        self.inner_first_action = self._act(m, "Cut Inner Pieces (&Göbek) First", self.toggle_inner_first, None, True,
                                            s.sequence_defaults.get("grouping", "depth") == "depth")
        self.inner_first_action.setToolTip("Cut the holes and inner pieces of all parts first, then the outlines, "
                                           "each in the order of the cut order strategy")
        m.addSeparator()
        self._act(m, "&Material Setup...", lambda: MaterialDialog(self.ctrl, self).exec(), "Ctrl+Shift+M")
        self._act(m, "&Tool Database...", lambda: ToolDatabaseDialog(self.ctrl, self).exec())
        m.addSeparator()
        self._act(m, "&Simulate", self.simulate, "F6")
        self._act(m, "Clear Simulation", self.clear_simulation)
        self._act(m, "Clear G-code Backplot", self.clear_backplot)
        m.addSeparator()
        self._act(m, "Save Toolpath &Template...", self.save_toolpath_template)
        self._act(m, "Load Toolpath Template...", self.load_toolpath_template)
        m.addSeparator()
        self._act(m, "Save &G-code...", self.export_gcode)
        self._act(m, "&Post-processors...", lambda: PostEditorDialog(self.ctrl, self).exec())

        m = mb.addMenu("&Nesting")
        self._act(m, "&Nest Parts...", self.nest, "Ctrl+Shift+N")
        m.addSeparator()
        self._act(m, "&Stock of Plates...", lambda: StockDialog(self.ctrl, self).exec())
        self._act(m, "Add Selected Vectors to Stock", self.stock_from_selection)
        self._act(m, "Add &Leftover of Current Sheet to Stock...", self.leftover_to_stock)
        m.addSeparator()
        self._act(m, "Set Sheet &Outline from Selected Vectors", self.sheet_outline_from_selection)
        self._act(m, "&Clear Sheet Outline", lambda: self.ctrl.set_sheet_outline(None))

        m = mb.addMenu("&Plugins")
        if self.pm is not None:
            self._act(m, "Plugin &Manager...", lambda: PluginsDialog(self.ctrl, self.pm, self).exec())
        self._act(m, "Python &Console", lambda: self.console_dock.setVisible(not self.console_dock.isVisible()),
                  "Ctrl+`")
        m.addSeparator()
        self._command_menus(m, "Plugins")
        for top in sorted({cmd.menu.split("/")[0] for cmd in c.registry.commands.values()}
                          - {"Edit", "Vectors", "Plugins"}):
            self._command_menus(mb.addMenu(top), top)

        self.window_menu = mb.addMenu("&Window")
        self.window_menu.aboutToShow.connect(self._fill_window_menu)

        self._fill_toolpath_menu()

        m = mb.addMenu("&Help")
        self._act(m, "&Keyboard Shortcuts", self.shortcuts_help)
        self._act(m, "&About iyiCAM", self.about)

    def _fill_toolpath_menu(self) -> None:
        self._toolpath_menu.clear()
        for op in self.ctrl.registry.operations.values():
            self._act(self._toolpath_menu, op.label + "...", lambda o=op.id: self.new_toolpath(o))

    def _fill_window_menu(self) -> None:
        m = self.window_menu
        m.clear()
        self._act(m, "&Next Tab", lambda: self.next_tab(1), "Ctrl+PgDown")
        self._act(m, "&Previous Tab", lambda: self.next_tab(-1), "Ctrl+PgUp")
        m.addSeparator()
        for i, cv in enumerate(self.tabs.documents()):
            title = cv.ctrl.title.replace("&", "&&")
            a = self._act(m, f"&{i + 1}  {title}{' *' if cv.ctrl.dirty else ''}",
                          lambda i_=i: self.tabs.setCurrentIndex(i_))
            a.setCheckable(True)
            a.setChecked(i == self.tabs.currentIndex())

    PAGE_KEYS = ("Alt+F7", "Alt+F8", "Alt+F9", "Alt+F10", "Alt+F11")

    def _transform_panel_actions(self, menu: QMenu) -> None:
        """The pages of the transform panel at the top of the menu (Alt+F7 ... Alt+F11 open them)."""
        first = menu.actions()[0] if menu.actions() else None
        for (key, title), shortcut in zip(TRANSFORM_PAGES, self.PAGE_KEYS):
            a = QAction(f"{title} Panel", menu)
            a.setShortcut(QKeySequence(shortcut))
            a.triggered.connect(lambda checked=False, k=key: self._safe(self.show_transform, k))
            menu.insertAction(first, a)
        menu.insertSeparator(first)

    def show_transform(self, page: str = "position") -> None:
        """Open the transform panel (on a page)."""
        self.transform_dock.show()
        self.transform_dock.raise_()
        self.transform_panel.show_page(page)

    def _command_menus(self, menu: QMenu, top: str) -> dict[str, QMenu]:
        """Add the commands of the menu ``top`` (in sub menus as they say); the sub menus by path."""
        subs: dict[str, QMenu] = {}
        for cmd in self.ctrl.registry.commands.values():
            parts = cmd.menu.split("/")
            if parts[0] != top:
                continue
            target = menu
            path = ""
            for name in parts[1:]:
                path += "/" + name
                if path not in subs:
                    subs[path] = target.addMenu(name)
                target = subs[path]
            a = self._act(target, cmd.label, lambda c_=cmd: self.run_command(c_), cmd.shortcut)
            a.setToolTip(cmd.tooltip)
        return subs

    def rebuild_plugin_ui(self) -> None:
        self.build_menus()
        self.ctrl.toolpaths_changed.emit()

    def _update_undo(self) -> None:
        u = self.ctrl.undo_stack
        self.undo_action.setEnabled(u.can_undo)
        self.redo_action.setEnabled(u.can_redo)
        self.undo_action.setText(f"&Undo {u.undo_label}".strip())
        self.redo_action.setText(f"&Redo {u.redo_label}".strip())

    def _toggle(self, key: str, value: bool) -> None:
        setattr(self.ctrl.settings, key, value)
        for cv in self.tabs.documents():
            cv._tp_cache = None
            cv.update()

    def _toggle_sim(self, value: bool) -> None:
        self.canvas.show_sim = value
        self.canvas.update()

    def _fill_recent(self) -> None:
        self.recent_menu.clear()
        for f in self.ctrl.settings.recent_files:
            self._act(self.recent_menu, f, lambda f_=f: self.open_project(f_))

    def _item(self, menu: QMenu, text: str, hint: str, slot, enabled: bool = True) -> QAction:
        """A menu entry that shows the key of the action it repeats (the key itself is set on the menus above)."""
        a = self._act(menu, f"{text}\t{hint}" if hint else text, slot)
        a.setEnabled(enabled)
        return a

    def _command_item(self, menu: QMenu, cid: str, selected: int) -> None:
        cmd = self.ctrl.registry.commands.get(cid)
        if cmd is not None:
            self._item(menu, cmd.label, cmd.shortcut or "", lambda c_=cmd: self.run_command(c_),
                       selected >= cmd.min_selection)

    def _canvas_menu(self, gpos, on_selection: bool = True) -> None:
        m = QMenu(self)
        c = self.ctrl
        n = len(c.selected_objects())
        if n and on_selection:
            self._item(m, "Cut", "Ctrl+X", self.cut)
            self._item(m, "Copy", "Ctrl+C", self.copy)
            self._item(m, "Paste", "Ctrl+V", self.paste, self._clipboard_has_vectors())
            self._item(m, "Duplicate", "Ctrl+D", self.duplicate)
            self._item(m, "Delete", "Del", self.delete)
            m.addSeparator()
            tm = m.addMenu("Transform")
            for cid in ("xform.move", "xform.rotate", "xform.scale", "xform.size", "xform.mirror", "xform.slant"):
                self._command_item(tm, cid, n)
            tm.addSeparator()
            for cid in ("xform.rot90l", "xform.rot90r", "xform.rot180", "xform.flip_h", "xform.flip_v"):
                self._command_item(tm, cid, n)
            tm.addSeparator()
            for (key, title), shortcut in zip(TRANSFORM_PAGES, self.PAGE_KEYS):
                self._item(tm, f"{title} Panel", shortcut, lambda k=key: self.show_transform(k))
            am = m.addMenu("Arrange")
            for cid in ("arrange.align", "arrange.center", "arrange.distribute", "arrange.array", "arrange.circular",
                        "arrange.along"):
                self._command_item(am, cid, n)
            m.addSeparator()
            self._act(m, "Edit Nodes", lambda: self.set_tool("nodes"))
            for cid in ("edit.offset", "edit.join", "edit.weld", "edit.subtract", "corner.fillet", "corner.dogbone",
                        "edit.reverse", "edit.group", "edit.ungroup"):
                self._command_item(m, cid, n)
            m.addSeparator()
            self._command_item(m, "select.all", n)
            self._command_item(m, "select.invert", n)
            m.addSeparator()
            tp = m.addMenu("New Toolpath")
            for op in c.registry.operations.values():
                self._act(tp, op.label + "...", lambda o=op.id: self.new_toolpath(o))
        else:
            self._item(m, "Paste", "Ctrl+V", self.paste, self._clipboard_has_vectors())
            self._command_item(m, "select.all", n)
            m.addSeparator()
            self._act(m, "Zoom Extents", self.canvas.zoom_extents)
        menus.show_menu(m, gpos)
        m.deleteLater()

    # -- helpers ------------------------------------------------------------------------------------
    def _safe(self, fn, *args):
        try:
            return fn(*args)
        except Exception as exc:
            traceback.print_exc()
            QMessageBox.critical(self, "iyiCAM", f"{type(exc).__name__}: {exc}")
            return None

    def _update_title(self) -> None:
        p = self.ctrl.project
        name = p.path.name if p.path else p.name
        self.setWindowTitle(f"{name}{' *' if self.ctrl.dirty else ''} — iyiCAM {__version__}")

    def confirm_discard(self, ctrl: Controller | None = None) -> bool:
        """Whether the project of a tab (the open one) may go: asks to save when it has changes."""
        ctrl = ctrl or self.ctrl
        if not ctrl.dirty:
            return True
        self.activate(ctrl)
        r = QMessageBox.question(self, "iyiCAM", f"Save changes to \u201c{ctrl.title}\u201d?",
                                 QMessageBox.Save | QMessageBox.Discard | QMessageBox.Cancel)
        if r == QMessageBox.Save:
            return self.save_project()
        return r == QMessageBox.Discard

    # -- file ---------------------------------------------------------------------------------------
    def new_project(self) -> None:
        """A new project in a new tab (in the open one when that has never been used)."""
        dlg = QDialog(self)
        dlg.setWindowTitle("New Project")
        lay = QVBoxLayout(dlg)
        params = [Param("width", "Width (X)", "length", 600.0, min=1.0),
                  Param("height", "Height (Y)", "length", 400.0, min=1.0),
                  Param("thickness", "Thickness", "length", 18.0, min=0.01)]
        s = self.ctrl.settings
        form = ParamForm(params, s.recall("new_project", params), self.ctrl.units)
        lay.addWidget(form)
        lay.addWidget(buttons(dlg, "Create"))
        if dlg.exec():
            v = form.values()
            s.remember("new_project", v, params)
            target = self.ctrl if self.ctrl.pristine else self._new_tab()
            target.new_project(v["width"], v["height"], v["thickness"])
            self._name_new(target)

    def open_project(self, path: str | None = None) -> None:
        """Open a project in a new tab (in the open one when that has never been used); one that is open
        already is shown instead."""
        if not path:
            path = open_file(self, self.ctrl.settings, "project", "Open project",
                             "iyiCAM project (*.icam);;All files (*)")
        if not path:
            return
        same = self.tab_of_path(path)
        if same is not None:
            self.activate(same)
            return
        fresh = self.ctrl.pristine
        c = self.ctrl if fresh else Controller(self.ctrl.registry, self.ctrl.settings, self.ctrl.tools)
        c.busy_parent = self
        try:
            c.open_project(path)
        except (OSError, ValueError, KeyError, TypeError) as exc:
            QMessageBox.warning(self, "Open project", f"Cannot open {path}:\n{exc}")
            return
        if not fresh:
            self._attach(c)
        self._doc_changed(c)

    def restore_tabs(self) -> None:
        """Open the projects that were open when the program was closed."""
        s = self.ctrl.settings
        saved = [f for f in s.open_tabs if Path(f).is_file()]
        for f in saved:
            self.open_project(f)
        if saved:
            self.tabs.setCurrentIndex(min(max(0, s.active_tab), self.tabs.count() - 1))

    def save_project(self, ctrl: Controller | None = None) -> bool:
        ctrl = ctrl or self.ctrl
        if ctrl.project.path is None:
            return self.save_project_as(ctrl)
        ctrl.save_project()
        self.statusBar().showMessage(f"Saved {ctrl.project.path}", 4000)
        return True

    def save_project_as(self, ctrl: Controller | None = None) -> bool:
        ctrl = ctrl or self.ctrl
        fn, _ = save_file(self, ctrl.settings, "project", "Save project", f"{ctrl.project.name}.icam",
                          "iyiCAM project (*.icam)")
        if not fn:
            return False
        ctrl.save_project(fn)
        self._doc_changed(ctrl)
        return True

    def save_all(self) -> None:
        for c in self.documents():
            if c.dirty:
                self.activate(c)
                if not self.save_project(c):
                    break

    def import_vectors(self, path: str | None = None, options: dict | None = None) -> list[VectorObject]:
        reg = self.ctrl.registry
        if not path:
            exts = sorted({e for imp in reg.importers.values() for e in imp.extensions})
            filt = "Vector files (" + " ".join(f"*.{e}" for e in exts) + ");;" + ";;".join(
                f"{imp.label} (" + " ".join(f"*.{e}" for e in imp.extensions) + ")" for imp in reg.importers.values())
            path = open_file(self, self.ctrl.settings, "import", "Import vectors", filt)
            if not path:
                return []
        imp = reg.importer_for(Path(path).suffix)
        if imp is None:
            QMessageBox.warning(self, "Import", f"No importer for {Path(path).suffix}")
            return []
        if options is None:
            dlg = ImportDialog(self.ctrl, path, self)
            if not dlg.exec():
                return []
            options = dlg.values()
        kw = {}
        ext = Path(path).suffix.lower().lstrip(".")
        if ext == "dxf":
            u = options.get("units", "auto")
            kw = {"join_tolerance": options.get("join", 0.05), "keep_layers": options.get("keep_layers", True),
                  "unit_scale": None if u == "auto" else float(u)}
        elif ext == "svg":
            kw = {"keep_layers": options.get("keep_layers", True), "scale": options.get("scale", 100.0) / 100.0}
        elif ext in ("plt", "hpgl", "hpg"):
            kw = {"join_tolerance": options.get("join", 0.05), "scale": options.get("scale", 100.0) / 100.0}
        c = self.ctrl
        report = ImportReport()
        with c.edit(f"Import {Path(path).name}"):
            objs = run_importer(imp.func, path, c.project, c.sheet, report, layer_id=c.layer_id, **kw)
            if options.get("center") and objs:
                bb = BBox()
                for o in objs:
                    bb = bb.union(o.bbox())
                T = Transform.translate(c.sheet.width / 2 - bb.center[0], c.sheet.height / 2 - bb.center[1])
                for o in objs:
                    o.transform(T)
        c.select([o.id for o in objs])
        self.canvas.zoom_extents()
        self.statusBar().showMessage(f"Imported {len(objs)} vector(s) from {Path(path).name}", 5000)
        if report.summary():
            QMessageBox.warning(self, "Import", f"{Path(path).name}: {len(objs)} vector(s) imported.\n\n"
                                                f"{report.summary()}")
        self.last_import_report = report
        return objs

    def trace_bitmap(self, path: str | None = None, options: dict | None = None) -> list[VectorObject]:
        """Turn a picture (logo, scanned drawing) into vectors."""
        import numpy as np
        from ..core.trace import centerline, trace
        from .qtgeom import image_to_gray
        if not path:
            path = open_file(self, self.ctrl.settings, "image", "Trace bitmap",
                             "Images (*.png *.jpg *.jpeg *.bmp *.gif *.tif *.tiff)")
            if not path:
                return []
        try:
            gray = image_to_gray(path)
        except ValueError as exc:
            QMessageBox.warning(self, "Trace bitmap", str(exc))
            return []
        h, w = gray.shape
        if options is None:
            params = [Param("width", "Width of the result", "length", min(self.ctrl.sheet.width * 0.8, 200.0),
                            min=0.01),
                      Param("threshold", "Threshold (0-255)", "int", 128, min=1, max=254,
                            help="Pixels darker than this become the shape"),
                      Param("mode", "Result", "choice", "outline",
                            choices=[("outline", "Outlines of the dark areas"),
                                     ("centerline", "Centre lines (single lines along strokes)")]),
                      Param("invert", "Trace light areas instead", "bool", False),
                      Param("smoothing", "Smoothing (pixels)", "float", 0.6, min=0.05, max=10.0),
                      Param("min_area", "Ignore specks smaller than (pixels)", "float", 8.0, min=0.0),
                      Param("center", "Centre on the sheet", "bool", True)]
            dlg = QDialog(self)
            dlg.setWindowTitle(f"Trace {Path(path).name} ({w} x {h} px)")
            lay = QVBoxLayout(dlg)
            memo = [q for q in params if q.name != "width"]   # the width follows the sheet, not the last trace
            form = ParamForm(params, self.ctrl.settings.recall("trace", memo), self.ctrl.units)
            lay.addWidget(form)
            lay.addWidget(buttons(dlg, "Trace"))
            if not dlg.exec():
                return []
            options = form.values()
            self.ctrl.settings.remember("trace", options, memo)
        px = options.get("width", 100.0) / max(1, w)
        if options.get("mode") == "centerline":
            contours = centerline(np.asarray(gray), options.get("threshold", 128), options.get("invert", False),
                                  px, options.get("smoothing", 0.6), max(4.0, options.get("min_area", 8.0) ** 0.5))
        else:
            contours = trace(np.asarray(gray), options.get("threshold", 128), options.get("invert", False), px,
                             options.get("min_area", 8.0), options.get("smoothing", 0.6))
        if not contours:
            QMessageBox.information(self, "Trace bitmap", "Nothing was found at this threshold.")
            return []
        c = self.ctrl
        obj = VectorObject(contours, c.layer_id, Path(path).stem)
        if options.get("center", True):
            bb = obj.bbox()
            obj.transform(Transform.translate(c.sheet.width / 2 - bb.center[0], c.sheet.height / 2 - bb.center[1]))
        c.add_vectors([obj], "Trace bitmap")
        self.statusBar().showMessage(f"Traced {len(contours)} contour(s)", 5000)
        return [obj]

    def text_on_curve(self, values: dict | None = None) -> VectorObject | None:
        """Lay text along the selected vector."""
        from .qtgeom import text_on_curve
        sel = self.ctrl.selected_objects()
        if len(sel) != 1 or not sel[0].contours:
            QMessageBox.information(self, "Text on curve", "Select one vector to put the text on.")
            return None
        if values is None:
            params = [Param("text", "Text", "str", "iyiCAM"), Param("font", "Font", "font", "DejaVu Sans"),
                      Param("height", "Capital height", "length", 20.0, min=0.1),
                      Param("spacing", "Extra letter spacing", "length", 0.0),
                      Param("offset", "Distance from the vector", "length", 0.0,
                            help="Positive moves the text to the left of the vector direction"),
                      Param("align", "Position", "choice", "center",
                            choices=[("start", "From the start"), ("center", "Centred"), ("end", "To the end")]),
                      Param("reverse", "Run along the reversed vector", "bool", False),
                      Param("bold", "Bold", "bool", False), Param("italic", "Italic", "bool", False)]
            dlg = QDialog(self)
            dlg.setWindowTitle("Text on Curve")
            lay = QVBoxLayout(dlg)
            form = ParamForm(params, self.ctrl.settings.recall("text_on_curve", params), self.ctrl.units)
            lay.addWidget(form)
            lay.addWidget(buttons(dlg, "Create"))
            if not dlg.exec():
                return None
            values = form.values()
            self.ctrl.settings.remember("text_on_curve", values, params)
        curve = sel[0].contours[0]
        if values.get("reverse"):
            curve = curve.reversed()
        cs = text_on_curve(values["text"], values["font"], values["height"], curve, values.get("bold", False),
                           values.get("italic", False), values.get("spacing", 0.0), values.get("offset", 0.0),
                           values.get("align", "center"))
        if not cs:
            return None
        obj = VectorObject(cs, self.ctrl.layer_id, values["text"][:24])
        self.ctrl.add_vectors([obj], "Text on curve")
        return obj

    def export_vectors(self, path: str | None = None, options: dict | None = None) -> None:
        reg = self.ctrl.registry
        filt = ";;".join(f"{e.label} (*.{e.extension})" for e in reg.exporters.values())
        fn, chosen = (path, "") if path else save_file(self, self.ctrl.settings, "export", "Export vectors",
                                                       self.ctrl.project.name, filt)
        if not fn:
            return
        ext = Path(fn).suffix.lower().lstrip(".")
        exp = reg.exporters.get(ext)
        if exp is None:
            exp = next((e for e in reg.exporters.values() if f"*.{e.extension}" in chosen), None)
            if exp is None:
                return
            fn += "." + exp.extension
        if options is None:
            options = {}
            if exp.params:
                dlg = ExportOptionsDialog(self.ctrl, exp, self)
                if not dlg.exec():
                    return
                options = dlg.values()
        objs = self.ctrl.selected_objects() or None
        try:
            exp.func(fn, self.ctrl.project, self.ctrl.sheet, objs, **options)
        except Exception as exc:  # noqa: BLE001  (a folder that cannot be written to, a plugin's exporter)
            QMessageBox.warning(self, "Export", f"{Path(fn).name} could not be written:\n\n{type(exc).__name__}: {exc}")
            return
        self.statusBar().showMessage(f"Exported {fn}", 5000)

    # -- editing ------------------------------------------------------------------------------------
    def run_command(self, cmd) -> None:
        values = None
        if cmd.params:
            dlg = CommandDialog(self.ctrl, cmd, self)
            if not dlg.exec():
                return
            values = dlg.values()
        self.ctrl.run_command(cmd, values)

    def delete(self) -> None:
        """Delete key: the active tool decides (node editing deletes nodes, not vectors)."""
        if hasattr(self.canvas.tool, "delete_nodes"):
            self.canvas.tool.delete_nodes()
        else:
            self.ctrl.delete_selection()
        self.canvas.update()

    def duplicate(self) -> None:
        objs = self.ctrl.selected_objects()
        if objs:
            g = self.ctrl.settings.grid or 10.0
            copies = [o.copy() for o in objs]
            for o in copies:
                o.transform(Transform.translate(g, -g))
            self.ctrl.add_vectors(copies, "Duplicate")

    def copy(self) -> None:
        self._to_clipboard(self.ctrl.selected_objects())

    def cut(self) -> None:
        """Ctrl+X: the selected vectors go to the clipboard and out of the drawing (the ones on a locked or hidden
        layer stay: they cannot be changed)."""
        objs = [o for o in self.ctrl.selected_objects() if self.ctrl.editable(o)]
        if not objs:
            if self.ctrl.selection:
                self.statusBar().showMessage("The selected vectors are on a locked or hidden layer", 5000)
            return
        self._to_clipboard(objs)
        self.ctrl.delete_selection()
        self.canvas.update()

    @staticmethod
    def _to_clipboard(objs) -> None:
        from ..core.document import vector_to_dict
        if objs:
            QApplication.clipboard().setText(json.dumps({"iyicam-vectors": [vector_to_dict(o) for o in objs]}))

    @staticmethod
    def _clipboard_has_vectors() -> bool:
        return QApplication.clipboard().text().startswith('{"iyicam-vectors"')

    def paste(self) -> None:
        from ..core.document import vector_from_dict
        try:
            data = json.loads(QApplication.clipboard().text())["iyicam-vectors"]
        except (ValueError, KeyError, TypeError):
            return
        objs = []
        for d in data:
            o = vector_from_dict(d)
            o.id = new_id()
            if self.ctrl.project.layer(o.layer_id) is None:
                o.layer_id = self.ctrl.layer_id
            objs.append(o)
        if objs:
            self.ctrl.add_vectors(objs, "Paste")

    # -- toolpaths ----------------------------------------------------------------------------------
    def new_toolpath(self, op_id: str) -> None:
        dlg = ToolpathDialog(self.ctrl, op_id, None, self)
        if dlg.exec():
            t = dlg.toolpath()  # once: every call makes a new definition with a new id
            self.ctrl.add_toolpath(t)
            self._report(t.id)

    def edit_toolpath(self, tid: str) -> None:
        t = self.ctrl.project.toolpath(tid)
        if t is None:
            return
        if t.op not in self.ctrl.registry.operations:
            QMessageBox.warning(self, "Toolpath", f"The toolpath type '{t.op}' is not available (missing plugin?).")
            return
        dlg = ToolpathDialog(self.ctrl, t.op, t, self)
        if dlg.exec():
            self.ctrl.replace_toolpath(dlg.toolpath())
            self._report(tid)

    def _report(self, tid: str) -> None:
        t = self.ctrl.project.toolpath(tid)
        if t is None:
            return
        res = self.ctrl.results_for(t)
        errors = [r.error for r in res if r.error]
        warns = list(dict.fromkeys(w for r in res for w in r.warnings if "Traceback" not in w))
        n = sum(len(r.toolpaths) for r in res)
        if errors:
            QMessageBox.warning(self, "Toolpath", "\n".join(errors))
        elif warns:
            self.statusBar().showMessage(f"{t.name}: " + "; ".join(warns), 10000)
        elif n == 0:
            self.statusBar().showMessage(f"{t.name}: nothing to machine on this sheet", 6000)

    def cut_order(self, tid: str | None = None) -> None:
        from .sequence_dialogs import CutOrderDialog, sequenced_toolpaths
        if not sequenced_toolpaths(self.ctrl):
            QMessageBox.information(self, "Cut order", "Add a profile or engraving toolpath first.")
            return
        if not self.ctrl.settings.show_cut_order:
            self._toggle("show_cut_order", True)
        CutOrderDialog(self.ctrl, tid, self).exec()

    def toggle_inner_first(self, on: bool) -> None:
        n = self.ctrl.set_inner_first(on)
        self.statusBar().showMessage(
            ("Inner pieces (göbek) are cut first" if on else "Cut part by part")
            + (f" on {n} toolpath(s)" if n else ""), 5000)

    def start_points(self, tid: str | None = None) -> None:
        from .sequence_dialogs import StartPointsDialog, sequenced_toolpaths
        if not sequenced_toolpaths(self.ctrl):
            QMessageBox.information(self, "Start points", "Add a profile or engraving toolpath first.")
            return
        StartPointsDialog(self.ctrl, tid, self).exec()

    def release_starts(self) -> None:
        self.ctrl.unlock_starts(list(self.ctrl.selection))

    def edit_tabs(self, tid: str) -> None:
        from .tools import TabTool
        self.canvas.set_tool(TabTool(self.canvas, tid))

    def recalculate(self) -> None:
        self.ctrl.recalculate()

    def save_toolpath_template(self) -> None:
        tps = self.ctrl.project.toolpaths
        if not tps:
            return
        fn, _ = save_file(self, self.ctrl.settings, "template", "Save toolpath template", "toolpaths.icamtpl",
                          "Toolpath template (*.icamtpl)")
        if fn:
            layers = {l.id: l.name for l in self.ctrl.project.layers}
            data = []
            for t in tps:
                d = toolpath_to_dict(t)
                d["selection"]["layer_names"] = [layers.get(i, "") for i in t.selection.layer_ids]
                d["selection"]["vectors"] = []
                data.append(d)
            Path(fn).write_text(json.dumps({"format": "iyicam-toolpaths", "toolpaths": data}, indent=1),
                                encoding="utf-8")

    def load_toolpath_template(self) -> None:
        fn = open_file(self, self.ctrl.settings, "template", "Load toolpath template",
                       "Toolpath template (*.icamtpl)")
        if not fn:
            return
        data = json.loads(Path(fn).read_text(encoding="utf-8"))
        p = self.ctrl.project
        with self.ctrl.edit("Load toolpath template", geometry=False):
            for d in data.get("toolpaths", []):
                names = d.get("selection", {}).get("layer_names", [])
                t = toolpath_from_dict(d)
                t.id = new_id()
                t.sheet_ids = None
                t.selection.layer_ids = [p.ensure_layer(n).id for n in names if n]
                t.selection.mode = "layers"
                p.toolpaths.append(t)
        self.ctrl.recalculate()

    def export_gcode(self) -> None:
        if self.ctrl.stale:
            self.ctrl.recalculate()
        if not self.ctrl.project.toolpaths:
            QMessageBox.information(self, "Save G-code", "Create a toolpath first (Toolpaths ▸ New Toolpath).")
            return
        ExportDialog(self.ctrl, self).exec()

    def simulate(self) -> None:
        from ..cam.simulate import shade, simulate
        c = self.ctrl
        if c.stale:
            c.recalculate()
        tps = c.sheet_toolpaths() + list(c.backplot)
        if not tps:
            QMessageBox.information(self, "Simulate", "There are no calculated toolpaths on this sheet.")
            return
        sh = c.sheet
        prog = QProgressDialog("Simulating material removal...", "Cancel", 0, 100, self)
        prog.setWindowModality(Qt.WindowModal)
        prog.setMinimumDuration(400)

        def progress(k, n):
            prog.setValue(int(100 * k / max(1, n)))
            QApplication.processEvents()

        hm = simulate(tps, sh.width, sh.height, c.project.sheet_thickness(sh), c.settings.sim_resolution,
                      progress, prog.wasCanceled, sh.outline)
        prog.close()
        rgb = shade(hm)
        h, w, _ = rgb.shape
        img = QImage(rgb.tobytes(), w, h, 3 * w, QImage.Format_RGB888).copy()
        self.canvas.sim_image = img
        self.canvas.sim_rect = QRectF(hm.x0 - hm.res / 2, hm.y0 - hm.res / 2, w * hm.res, h * hm.res)
        self.canvas.show_sim = True
        self.sim_action.setChecked(True)
        self.canvas.update()
        st = PathStats()
        for tp in tps:
            st = st.merge(tp.stats(c.project.material.rapid_rate))
        self.statusBar().showMessage(f"Simulation done (resolution {hm.res:.2f} mm). Estimated machining time "
                                     f"{format_time(st.time_s)}", 8000)

    def open_gcode(self, path: str | None = None) -> list:
        """Show an existing G-code file over the sheet (uses the material setup to map coordinates)."""
        from ..cam.gcode_reader import ReadOptions, read_gcode
        from ..post.engine import ORIGINS
        if not path:
            path = open_file(self, self.ctrl.settings, "gcode", "Open G-code",
                             "G-code (*.nc *.tap *.ngc *.gcode *.cnc *.txt *.u00 *.eia);;All files (*)")
            if not path:
                return []
        c = self.ctrl
        m, sh = c.project.material, c.sheet
        fx, fy = ORIGINS.get(m.xy_origin, (0.0, 0.0))
        tools = {t.number: t for t in c.tools.tools}
        opt = ReadOptions(fx * sh.width, fy * sh.height,
                          c.project.sheet_thickness(sh) if m.z_zero == "bottom" else 0.0, tools)
        try:
            text = Path(path).read_text(encoding="latin-1", errors="replace")
        except OSError as exc:
            QMessageBox.warning(self, "Open G-code", str(exc))
            return []
        c.backplot = read_gcode(text, opt)
        c.toolpaths_changed.emit()
        st = PathStats()
        for tp in c.backplot:
            st = st.merge(tp.stats(m.rapid_rate))
        self.statusBar().showMessage(f"{Path(path).name}: {len(c.backplot)} tool(s), cut length "
                                     f"{c.units.fmt(st.feed_length)}, estimated {format_time(st.time_s)}", 10000)
        return c.backplot

    def clear_backplot(self) -> None:
        self.ctrl.backplot = []
        self.ctrl.toolpaths_changed.emit()

    def clear_simulation(self) -> None:
        self.canvas.sim_image = None
        self.canvas.show_sim = False
        self.sim_action.setChecked(False)
        self.canvas.update()

    def nest(self) -> None:
        c = self.ctrl
        pool = [o for o in (c.selected_objects() or list(c.sheet.vectors)) if c.editable(o)]  # locked layers stay put
        if not pool:
            QMessageBox.information(self, "Nesting", "There are no vectors to nest (vectors on locked layers "
                                                     "are not moved).")
            return
        extra = [o.id for g in find_duplicates(pool) for o in g[1:]]
        if extra:  # the same shape twice in the same place would be nested as two parts: it looks like a copy
            r = QMessageBox.question(
                self, "Nesting", f"{len(extra)} vector(s) lie exactly on top of another vector (the same shape in the "
                "same place). Nesting would spread them out as separate parts.\n\nDelete the duplicates first?",
                QMessageBox.Yes | QMessageBox.No | QMessageBox.Cancel)
            if r == QMessageBox.Cancel:
                return
            if r == QMessageBox.Yes:
                with c.edit("Delete duplicates"):
                    c.project.remove_vectors(extra)
        dlg = NestingDialog(c, self)
        if dlg.exec():
            out = dlg.run()
            if out is None:  # cancelled: nothing changed
                return
            r = out.result
            lines = [f"Placed {out.placed} part(s) on {r.sheets} plate(s):"]
            for sh, u in zip(out.sheets, r.utilization):
                lines.append(f"  {sh.name}: {u * 100:.1f}% used")
            if out.squeezed:
                lines += ["", "Squeezed in:"] + squeeze_lines(out.parts, out.squeezed)
            if out.unplaced:
                lines.append(f"\n{out.unplaced} part(s) did not fit.")
            QMessageBox.information(self, "Nesting", "\n".join(lines))
            self.canvas.zoom_extents()

    def stock_from_selection(self) -> None:
        items = self.ctrl.stock_from_selection()
        if items:
            QMessageBox.information(self, "Stock", f"Added {len(items)} plate(s) to the stock.")
        else:
            QMessageBox.information(self, "Stock", "Select closed vectors that outline the plates first.")

    def leftover_to_stock(self) -> None:
        dlg = LeftoverDialog(self.ctrl, self)
        if dlg.exec():
            items = self.ctrl.leftover_to_stock(self.ctrl.sheet, *dlg.values())
            msg = (f"Added {len(items)} plate(s) to the stock: " + ", ".join(
                f"{it.width:.0f} x {it.height:.0f}" for it in items)) if items else \
                "Nothing worth keeping is left on this sheet."
            QMessageBox.information(self, "Stock", msg)

    def sheet_outline_from_selection(self) -> None:
        if not self.ctrl.set_sheet_outline_from_selection():
            QMessageBox.information(self, "Sheet outline", "Select the closed vectors that outline the plate "
                                    "(and its holes) first.")
        else:
            self.canvas.zoom_extents()

    def export_image(self) -> None:
        fn, _ = save_file(self, self.ctrl.settings, "export", "Export image", f"{self.ctrl.project.name}.png",
                          "PNG image (*.png);;JPEG image (*.jpg)")
        if fn:
            self.canvas.grab().save(fn)

    def job_report(self) -> None:
        c = self.ctrl
        p = c.project
        if c.stale:
            c.recalculate()
        fn, _ = save_file(self, self.ctrl.settings, "export", "Job report", f"{p.name}_report.html", "HTML (*.html)")
        if not fn:
            return
        Path(fn).write_text(job_report_html(c), encoding="utf-8")
        QDesktopServices.openUrl(QUrl.fromLocalFile(fn))

    # -- help -----------------------------------------------------------------------------------------
    def shortcuts_help(self) -> None:
        rows = [f"<tr><td><b>{TOOL_KEYS[n]}</b></td><td>{TOOL_LABELS[n]}</td></tr>" for n in TOOL_KEYS if TOOL_KEYS[n]]
        extra = [("F", "Zoom extents"), ("Shift+F", "Zoom selection"), ("Mouse wheel", "Zoom at cursor"),
                 ("Middle drag / Space+drag", "Pan"), ("Esc", "Back to the select tool"), ("Del", "Delete"),
                 ("Arrows", "Nudge (Shift x10, Ctrl x0.1)"), ("F5", "Recalculate toolpaths"), ("F6", "Simulate"),
                 ("F7 / F8", "Snap to grid / vectors"), ("Ctrl+D", "Duplicate"), ("Ctrl+J", "Join vectors"),
                 ("Ctrl+N / Ctrl+O", "New project / open project (each in a tab)"),
                 ("Ctrl+W", "Close tab"), ("Ctrl+Shift+T", "Reopen closed tab"),
                 ("Ctrl+PgUp / Ctrl+PgDown", "Previous / next tab"), ("Middle-click a tab", "Close it"),
                 ("Ctrl+Shift+O", "Offset"), ("Ctrl+G / Ctrl+Shift+G", "Group / ungroup"),
                 ("Ctrl+X / Ctrl+C / Ctrl+V", "Cut / copy / paste vectors (also between tabs)"),
                 ("Ctrl+drag a vector", "Copy it while moving (Ctrl+click toggles the selection)"),
                 ("Right-click a vector", "Cut, copy, paste, transform, arrange, edit nodes, new toolpath"),
                 ("Alt+F7 / F8 / F9 / F10 / F11", "Transform panel: position / rotate / scale and mirror / size / slant"),
                 ("Ctrl+M / Ctrl+R", "Move... / Rotate... by numbers")]
        rows += [f"<tr><td><b>{k}</b></td><td>{v}</td></tr>" for k, v in extra]
        QMessageBox.information(self, "Keyboard shortcuts", "<table>" + "".join(rows) + "</table>")

    def about(self) -> None:
        QMessageBox.about(self, "About iyiCAM",
                          f"<h3>iyiCAM {__version__}</h3><p>Modular 2D / 2.5D CAM for CNC routers.</p>"
                          "<p>Vector layers, sheets, toolpaths (profile, pocket, drill, engrave, V-carve), "
                          "true shape nesting, template post-processors and a Python plugin API.</p>")

    def closeEvent(self, ev) -> None:
        keep = self.tabs.currentIndex()
        for c in self.documents():
            if not self.confirm_discard(c):
                ev.ignore()
                self.activate(c)
                return
        for c in self.documents():
            c._recalc.stop()                   # (no calculation may start in a window that is closing)
        s = self.ctrl.settings
        files = [(i, str(c.project.path)) for i, c in enumerate(self.documents()) if c.project.path is not None]
        s.open_tabs = [f for _, f in files]
        s.active_tab = next((n for n, (i, _) in enumerate(files) if i == keep), 0)
        s.window_state = bytes(self.saveState().toBase64()).decode()
        s.window_geometry = bytes(self.saveGeometry().toBase64()).decode()
        try:
            s.save()
        except OSError:
            pass
        ev.accept()


def job_report_html(ctrl) -> str:
    p = ctrl.project
    u = ctrl.units
    m = p.material
    e = html.escape
    out = [f"<html><head><meta charset='utf-8'><title>{e(p.name)}</title>"
           "<style>body{font-family:sans-serif;margin:24px}table{border-collapse:collapse}"
           "td,th{border:1px solid #bbb;padding:4px 8px;text-align:left}th{background:#eee}</style></head><body>",
           f"<h1>{e(p.name)}</h1>",
           f"<p>Material thickness {u.fmt(m.thickness)}, Z zero at the {m.z_zero}, XY origin "
           f"{m.xy_origin.replace('_', ' ')}, safe Z {u.fmt(m.safe_z)}.</p>"]
    for s in p.sheets:
        tps = ctrl.sheet_toolpaths(s)
        out.append(f"<h2>{e(s.name)} — {u.fmt(s.width)} x {u.fmt(s.height)} x "
                   f"{u.fmt(p.sheet_thickness(s))}</h2>")
        if not tps:
            out.append("<p>No toolpaths.</p>")
            continue
        out.append("<table><tr><th>#</th><th>Toolpath</th><th>Tool</th><th>Spindle</th><th>Feed / plunge</th>"
                   "<th>Depth</th><th>Cut length</th><th>Time</th></tr>")
        total = PathStats()
        for i, tp in enumerate(tps, 1):
            st = tp.stats(m.rapid_rate)
            total = total.merge(st)
            t = tp.tool
            out.append(f"<tr><td>{i}</td><td>{e(tp.name)}</td><td>T{t.number} {e(t.describe())}</td>"
                       f"<td>{t.spindle_speed}</td><td>{t.feed_rate:g} / {t.plunge_rate:g}</td>"
                       f"<td>{u.fmt(-st.zmin)}</td><td>{u.fmt(st.feed_length)}</td><td>{format_time(st.time_s)}</td></tr>")
        out.append(f"<tr><th colspan=6>Total</th><th>{u.fmt(total.feed_length)}</th>"
                   f"<th>{format_time(total.time_s)}</th></tr></table>")
    out.append("</body></html>")
    return "\n".join(out)

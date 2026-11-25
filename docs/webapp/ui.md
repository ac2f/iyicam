# The interface of the web app

The window is the desktop program's (`iyicam/gui`), rebuilt in the browser as plain ES modules: no framework, no npm
dependency, no build step. The page is `iyicam/webapp/index.html` (+ `app.css`); the code is `iyicam/webapp/ui/`. The engine
(`engine/engine.js`) and the files API (`engine/files.js`) are the engineer's; the interface only calls them.

## Layout of `ui/`

| Module | What it is |
|---|---|
| `main.js` | starts: sign-in (`/api/session`, `/api/login`), engine, schema; builds the window; document tabs; status bar |
| `state.js` | `app` (docs, tool, clipboard, schema, tools), `Doc` (a tab: the project as plain data, snapshots for Undo / Redo, selection, results), settings, units, a tiny event bus (`on`, `emit`) |
| `engine.js` | `startEngine`, `run(op, payload, {label, cancel})` with the busy bar, `report(error)`; `files()` loads `engine/files.js` |
| `actions.js` | everything the menu items, tool bars, shortcuts and panels do (`registry`: item id -> `{run, enabled, checked}`); the drawing tools; the keyboard |
| `menudef.js` | the desktop's menus (generated once from `desktop_reference/ui_structure.json`): ids, shortcuts, `cmd` = a command of the schema |
| `menus.js`, `toolbars.js`, `docks.js`, `panels.js` | menu bar, quick and drawing tool bars, the dock groups (tabs, close, sizes, drawers on a phone), the panels |
| `canvas.js` | the drawing: rulers, sheet and grid, vectors, toolpaths, simulation picture, selection with handles, guides, snapping, pointer / touch handling |
| `geom.js` | geometry of vectors (flatten, hit, bbox, affine, shapes); ported from the old page |
| `forms.js` | `ParamForm`: a Param list as a form (the desktop's `forms.py`) |
| `dialog.js`, `dialogs/*.js` | modal and modeless windows; Toolpath window (`toolpath.js`), Tool Database and tool editor (`tools.js`), Material, New, Sheet, Nesting, Preferences, Export, Save G-code, shortcuts, About (`misc.js`), command forms (`command.js`, `common.js`) |
| `files.js` | files of this device (picker, download, File System Access API) and of the agent (file browser) |
| `theme.js`, `icons.js`, `dom.js` | light / dark, the tool bar icons, element helpers |

## How things work

* **Project and Undo.** A tab keeps the project as the `.icam` JSON. A vector is never changed in place: a changed vector is a
  new object, so the drawing keeps its polylines / `Path2D` with the object (`geom.js`). `doc.edit(label, fn)` snapshots the
  project (JSON string) before `fn`; `doc.replace(label, project)` is for what the engine answers (`command`, `import`, `nest`).
  Undo / Redo restore snapshots with the selection and the open sheet. Toolpath results are not part of the snapshot: after an
  edit `doc.stale` is true, the toolpaths are drawn faded until F5 (or Preferences: recalculate after every edit).
* **Menus.** An item whose id has no entry in `actions.registry` is greyed with the tooltip "Not on the web yet". Vectors /
  Create, Edit, Corners, Transform, Arrange and Edit / Select are the schema's commands, found by menu and label; selecting fewer
  vectors than `min_selection` says so instead of calling the engine. Shortcuts come from `menudef.js` (alternatives
  "Ctrl+Y, Ctrl+Shift+Z" both work); single letters pick drawing tools; arrows nudge (step follows the zoom, Shift x10, Ctrl /10).
* **Forms.** `ParamForm` reads the schema's params: kinds `length feed angle int float percent bool choice str text tool point`,
  groups as boxes, `visible` conditions, advanced params behind "More options (n)". Lengths show in the display unit and are
  kept in mm; numbers accept sums (`120/3+2`, a small parser: the server's CSP allows no `eval`).
* **Toolpath window.** Modeless beside the drawing on a wide screen (the toolpath follows the selection on the drawing), full
  screen on a phone. Presets use `tp_presets`, `tp_preset_apply`, `tp_preset_save`, `tp_preset_delete`.
* **Files.** Open / Save / Import / Export / Save G-code offer "This device" (picker / download; Save writes back through the
  File System Access API where the browser has it) and, when the server says an agent (or `--local`) is there, the roots of the
  agent through a file browser. `.icam` is written as the JSON of the project.
* **Phone and tablet.** Under 820 px the docks become drawers (buttons at the ends of the quick bar), dialogs are bottom sheets,
  the menu bar scrolls sideways. One finger acts as the mouse; two fingers pinch to zoom and pan (`canvas.js`); handles and
  hit tolerances are bigger for touch.

## Drawing and vectors (`feat_draw.js`, `panels_draw.js`, `dialogs/draw.js`, `service/ops_draw.py`)

The work is the engine's (JSON ops in `iyicam/service/ops_draw.py`; the browser calls them with `run("<name>", payload)`); the page does
what the pointer does: finds what is under it, draws what is going on and asks for the change.

| Item | Engine op | What the page does |
|---|---|---|
| Edit Nodes (N) | `vector_edit` (move nodes / bezier handles, insert, delete, break, convert span, reverse, open / close, set start) | picks a handle, node or span of the selected vectors; while a node is dragged the latest position is asked for, one call at a time (the newest wins), and the drag is one step of Undo; double click inserts a node, Del deletes the selected nodes, right click is the desktop's menu |
| Star | (the shape is `G.star`, the same as the engine's `shapes.star`) | drag from the centre to a tip; `+` / `-` change the points (also the sides of the polygon) |
| Text (T) | `text`, `fonts` | a form (text, font, capital height, spacing, bold, italic, alignment); the letters come from the engine (see below) |
| Trim Vectors (X) | `trim` | hover shows the vector, a click removes the piece between two crossings |
| Set Start Point (B) | `start_probe` (where the start would go: corner, middle, arc extreme, else nearest point; the marks it clings to), `start_points` (`set`) | click or drag along a vector, Alt: free, `]` / `[` step to the next / previous corner, right click gives the start back to smart start points; start points are marked (a square: set by hand) |
| Vectors ▸ Start Points | `start_points` (`place`, `step`, `opposite`, `smart`, `release`) | the corners and middles of the sides of the selected closed vectors, next / previous corner, opposite point, Smart (best place: calculated with the toolpath that machines the selection, else the first profile), Release; the message of the desktop's status bar. "Smart Start Points..." opens the Cut Order panel |
| Vectors ▸ Text on Curve | `text_on_curve` | the desktop's form; one vector selected |
| Vectors ▸ Trace Bitmap | `trace_bitmap` | the browser decodes the picture (any format it reads; at most 2400 px on a side) to grey values and sends them; the desktop's form (width, threshold, outlines or centre lines, ...) |
| Vector Library (dock, `Vectors` menu) and Keep Selection in the Library | `veclib` (tree, groups, items, insert, preview, import, export) | the tree with groups stacked in groups (drag a drawing or group onto a group; double click puts a drawing in the sheet, in the middle of the view), search, picture of the drawing, rename / delete / move up and down / to the top level, import and export (`.icamlib`); the library is `vector_library.json` of the user folder (kept in IndexedDB like the tool database) |
| Nesting ▸ Set / Clear Sheet Outline | `sheet_outline` | the sheet takes the shape of the selected closed vectors (they are used up, closed ones inside are holes); the plate is shaded outside and its outline traced |

**Text and fonts.** The desktop takes letters from Qt and the fonts of the computer; the browser has neither, so
`iyicam/core/textgeom.py` reads font files with `fontTools` (pure Python, a dependency of `ezdxf`) and lays the text out as the desktop
does (capital height, spacing, lines, alignment; closed contours, outer ones counter-clockwise, holes clockwise). The fonts that come
with the engine are in `iyicam/core/fonts/`: DejaVu Sans, Serif and Sans Mono, regular and bold (subsets: Latin with the Turkish
letters, Greek, Cyrillic, common signs; licence in the folder); italic is the font slanted 12 degrees. A font file of the person
(`.ttf` / `.otf`, "Use a font file...") can be given instead. Kerning is not applied. `pyproject.toml` needs `"iyicam.core" = ["fonts/*"]`
in `package-data` for an installed (not editable) package.

**Where the logic is.** Start places, stepping and releasing are `iyicam/core/startplace.py` (what `gui/cutcontrol.py` does without
Qt); the node, trace, trim, library and outline functions are the ones the desktop uses (`vectorops`, `trace`, `intersect`, `veclib`,
`stock.normalized`).

## Not on the web yet (greyed or a note in the panel)

Open Recent works for files of the agent only, Repeat works for commands only. (The job features below are done.)

## The job features (`feat_job.js`, `panels_job.js`, `dialogs/job.js`, `service/ops_job.py`)

The rule: the work is the engine's, the page only asks. Each feature has an op in `iyicam/service/ops_job.py` (JSON in, JSON out;
`ops.py` takes them in) and, where the desktop did the work in `gui/*.py`, a function of the engine package that the desktop's code
could use too (`cam/cutedit.py`, `cam/jobreport.py`). The user folder (packages, posts, plugins, tools) is kept in IndexedDB by the bridge.

| Item | Page | Engine |
|---|---|---|
| File ▸ Open G-code (Backplot), Toolpaths ▸ Clear G-code Backplot | purple lines over the sheet (`app.overlays`); the Simulation panel plays it after the toolpaths | `backplot` (`cam/gcode_reader.py`) |
| File ▸ Job Report | a window with the report, "Open in a tab" / "Save…" | `job_report` (`cam/jobreport.py`) |
| File ▸ Quit | asks about every tab with changes, signs out (`/api/logout`) and says the tab can be closed | |
| Edit ▸ Keyboard Shortcuts | a list with a search, press the keys; conflicts ask "Take it from there?". The changes (only what differs from the program's keys) are in `localStorage` (`iyicam.shortcuts`) and read by `actions.js` `buildShortcuts` / `keysOf`; the menus show them (`shortcutText`); Help ▸ Keyboard Shortcuts lists the current keys | |
| View ▸ Python Console, Plugins ▸ Python Console (Ctrl+`) | a pane under the drawing; one line at a time runs **in the engine in the browser**, variables stay from line to line. Names as on the desktop: `project`, `sheet()`, `selected()`, `ctrl` (small), `add()`, `run(command_id, **values)`, `shapes`, `vectorops`, `geom`, `REGISTRY`, `report_text`. A changed project comes back as one Undo step | `console_run`, `console_reset` |
| Toolpaths ▸ Toolpath Packages, Save Selected Toolpaths as a Package, Use a Package | the packages window (list, tool, group name with `{…}` variables, toolpaths with their own tool, layers and asked settings) and the window that puts one on the selection or on layers, with the names it will make shown live | `packages`, `package_save`, `package_from_toolpaths`, `package_items`, `package_info`, `package_apply`, `package_import`, `package_export` (`cam/tp_packages.py`) |
| Save / Load Toolpath Template | a file `.icamtpl` (layers by name) | `template_save`, `template_load` |
| Toolpaths ▸ Post-processors | list, settings (a form from `specs`), blocks of text, a live preview of a sample part where a line can be clicked to read what it says; New from selected (your own post), Save, Delete | `posts`, `post_save`, `post_delete`, `post_preview`, `gcode_explain` |
| Nesting ▸ Stock of Plates, Add Selected Vectors to Stock, Add Leftover of Current Sheet to Stock | the stock table (name, size, quantity, leftover, thickness), add a plate of a shape, from vectors, leftover of the sheet, load / save; kept in the project (`project.stock`), one Undo step | `stock_plate`, `stock_from_vectors`, `stock_leftover`, `stock_export`, `stock_import` |
| Plugins ▸ Plugin Manager | list, on / off, reload, remove, install a `.py` / `.zip`; the schema is read again, so commands, toolpath types and posts of a plugin appear (its commands in the Plugins menu) | `plugins`, `plugin_install`, `plugin_set` |
| Help ▸ G-code Words | the glossary (English / Turkish) and a line explained | `posts` (glossary), `gcode_explain` |
| Cut Order panel | as the desktop: strategy, smart order, big parts last, inner pieces, row size, direction (per strategy, "All…"), smart start points; settings **wait** for Apply (or Preview, pink on the drawing, with what it changes), "Calculate while I change settings"; the cuts list (select a row: the vector is selected and drawn yellow; Up / Down, drag a row, "Click to number…" on the drawing with Enter to apply); the start point of the selected cut (◀ ▶ along the cut, the 8 places, Pick…, Smart, Release) | `cut_order` (list / preview), `cut_edit` (`cam/cutedit.py`) |
| Simulation panel | Play / Pause, ⏮ ⏭, speed 1x–1000x, the slider, the tool and what it has cut painted as it goes; "Shaded result (3D)…" is the old picture (F6 still makes that one) | `sim_timeline` (`cam/sim2d.py`) |

The Use a Package submenu and the plugin commands of the Plugins menu are filled when a menu opens (`app.dynamicMenus`, read by `menus.js`).

## Differences from the desktop

* No floating docks; docks close and reopen from View. The Window menu lists the tabs.
* Resize handles scale uniformly (corners) or along one side (edges); a stretch of arcs is done by the engine's `Scale` command.
* The tool database is kept in the user folder through the `tools_save` operation when the engine has it, else in this browser.
* The rapid / cut colours follow the desktop (`TP_COLORS`); the simulation is the engine's PNG laid on the sheet.

## Testing (Playwright)

`tests/webapp/ui_scripts/`: `lib.js` (start the page, sign in), `check_ui.js` (select, move, undo, shortcuts, menus),
`check_flows.js` (toolpath, G-code, simulate, nest, import, new, agent files), `check_panels.js` (layers, sheets, Selection and Transform panels, handles, docks, drawing tools, snapping, units), `check_job.js` (the job features above), `check_draw.js` (Edit Nodes, Star, Text, Trim, Set Start Point and the Start Points menu, Text on Curve, Trace Bitmap, Vector Library, sheet outline), `check_touch.js` (phone: drawers, pinch, two-finger pan), `shot_main.js`,
`shot_dialogs.js`, `shot_phone.js` (screenshots in `/tmp/iyicam-ui-shots`). They need `iyicam serve --local --root DIR` and its key:
`KEY=... NODE_PATH=$(npm root -g) node check_ui.js`. `window.__iyicam = {app, A}` is exposed for them.

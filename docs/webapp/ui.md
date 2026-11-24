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
| `devfake.js` | **temporary** stand-in used only when `engine/engine.js` is not served (a plain static server); delete it with the `fake` branch of `engine.js` |

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

## Not on the web yet (greyed or a note in the panel)

Edit Nodes, Star / Text / Trim / Set Start Point tools (Star and others are available as commands under Vectors), Open Recent
works for files of the agent only, Open G-code (Backplot), Job Report, Quit, Repeat works for commands only, Keyboard Shortcuts
editor (the list is in Help), Vector Library and Keep in the Library, Trace Bitmap, Text on Curve, Start Points submenu,
Toolpath Packages and templates, Cut Order editing (the panel lists the order), Simulation playback (the picture is shown),
Stock of plates / leftovers, Set / Clear Sheet Outline, Plugin Manager, Python Console, Post-processors, G-code Words.

## Differences from the desktop

* No floating docks; docks close and reopen from View. The Window menu lists the tabs.
* Resize handles scale uniformly (corners) or along one side (edges); a stretch of arcs is done by the engine's `Scale` command.
* The tool database is kept in the user folder through the `tools_save` operation when the engine has it, else in this browser.
* The rapid / cut colours follow the desktop (`TP_COLORS`); the simulation is the engine's PNG laid on the sheet.

## Testing (Playwright)

`tests/webapp/ui_scripts/`: `lib.js` (start the page, sign in), `check_ui.js` (select, move, undo, shortcuts, menus),
`check_flows.js` (toolpath, G-code, simulate, nest, import, new, layers / sheets, agent files), `shot_main.js`,
`shot_dialogs.js`, `shot_phone.js` (screenshots in `/tmp/iyicam-ui-shots`). They need `iyicam serve --local --root DIR` and its key:
`KEY=... NODE_PATH=$(npm root -g) node check_ui.js`. `window.__iyicam = {app, A}` is exposed for them.

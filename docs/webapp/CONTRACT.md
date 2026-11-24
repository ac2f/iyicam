# iyiCAM web app — the contract between the designer, the engineer and the tester

The old web page (`iyicam/web/`) and the old service in which the server or an agent did the CAM work are replaced.

## The idea

* **The browser does all the CAM work.** The iyiCAM Python package runs in the browser in a Web Worker with
  [Pyodide 0.27.7](https://pyodide.org) (Python 3.12; `numpy` and `pyclipper` are Pyodide packages, `ezdxf` and
  `svgelements` are installed with `micropip`). It was tried: the same project gives the same toolpaths as on the desktop,
  at about half the speed. The worker calls the JSON operations of `iyicam/service/ops.py` (`op_schema`, `op_import`,
  `op_command`, `op_compute`, `op_gcode`, `op_simulate`, `op_nest`, ...): JSON in, JSON out, as they are.
* **The agent only shares files.** `iyicam agent` runs on the workshop PC and lists, reads and writes files in the folders it
  is given (its roots). It does no CAM work.
* **The server only passes files between the agent and the person.** `iyicam serve` serves the static app and the Python
  package for the worker, signs people in with the key, and relays file requests to the agent (or, with `--local`, serves
  the roots of the computer it runs on itself). It does no CAM work.
* **The interface is the desktop program's**: the same menus (with the same shortcuts), tool bars, docks, panels, dialogs
  and status bar, the drawing in the middle. Reference: `docs/webapp/desktop_reference/` (`main_window.png`, the dialogs and
  panels as PNG, `ui_structure.json`: every menu item with its shortcut, the tool bars, the docks). The desktop code
  (`iyicam/gui/*.py`) is the reference for what every item does.

## Files and who owns them (only the owner edits them; ask the coordinator for a change in another's)

| Path | Owner |
|---|---|
| `iyicam/webapp/index.html`, `iyicam/webapp/app.css`, `iyicam/webapp/ui/**` | designer |
| `iyicam/webapp/engine/**` (worker, engine API, files API), `iyicam/webapp/bridge.py` | engineer |
| `iyicam/share/**` (server, agent, relay), `iyicam/cli.py` (`serve`, `agent`), removing `iyicam/web/` and the old server / hub / agent / jobs / sessions modules that only they used | engineer |
| `tests/webapp/**`, the old tests of what is removed (removed or rewritten) | tester |
| `docs/webapp/**` other than this file, `docs/web.md` | engineer (docs of the architecture) and designer (docs of the interface) |
| `iyicam/service/ops.py` and everything under `iyicam/core`, `iyicam/cam`, `iyicam/post`, `iyicam/nesting` | nobody: changes go through the coordinator |

Keep `iyicam/service/ops.py`, `wire.py`, `errors.py`, `preview.py`, `png.py` (the worker uses them) and whatever of
`files.py` / `keys.py` the new server reuses. The desktop program (`iyicam/gui`) is not touched.

## The engine API (JavaScript, `iyicam/webapp/engine/engine.js`, an ES module)

```js
import { Engine } from "./engine/engine.js";
const engine = await Engine.start({ onStatus: text => ... });   // loads Pyodide and the iyicam package in a Web Worker
const schema = await engine.call("schema", {});                 // any op of iyicam/service/ops.py, by its name without op_
const r = await engine.call("compute", { project }, { onProgress: (k, n, label) => ..., signal });  // AbortSignal cancels
```

* `engine.call(op, payload, opts)` resolves with what `op_<op>(payload)` returns (JSON) and rejects with
  `EngineError {code, message, details}` for a `ServiceError` (its `code` and fields) or any other exception
  (`code: "internal"`, the traceback in `details`).
* Bytes travel as base64 strings, as `ops.py` already takes and gives them (`data_b64`).
* Progress: the long ops (`compute`, `gcode`, `simulate`, `nest`) report `(k, n, label)`; cancelling raises inside them
  (the `JobControl` of `ops.py` or a callback the bridge gives).
* The user folder of iyiCAM (`IYICAM_HOME`: tool library, posts, presets, toolpath packages, vector library, settings) is a
  folder of the Pyodide file system kept in IndexedDB (IDBFS), synced after every op that writes there.
* `engine.version`, `engine.ready` (a promise). The Python package comes from the server (`/app/py/iyicam.zip`, built from the
  installed iyicam without `gui`, `web`, `webapp`, `share`); Pyodide from `https://cdn.jsdelivr.net/pyodide/v0.27.7/full/`
  unless the server is started with a folder of its own (`--pyodide DIR`).

## The files API (JavaScript, `iyicam/webapp/engine/files.js`)

```js
import { Files } from "./engine/files.js";
await Files.status();                 // {signed_in, agent: {connected, name} | null, roots: [{name, path}]}
await Files.list(path);               // [{name, path, dir, size, mtime}]  (path "" = the roots)
await Files.read(path);               // Uint8Array
await Files.write(path, bytes, { overwrite: false });   // {path, size}; FileExistsError-like rejection when it exists
```

HTTP behind it (the engineer decides the details and documents them in `docs/webapp/server.md`): `POST /api/login {key}`,
`GET /api/session`, `GET /api/files/status`, `GET /api/files/list?path=`, `GET /api/files/read?path=`,
`PUT /api/files/write?path=&overwrite=`. Without an agent and without `--local` the file API says so (`agent: null`): the app
still opens and saves files of the device itself (file picker, download).

## What the app does in the first round

1. Start: sign in with the key (as the old page did), the engine loads with a visible progress, the schema is read.
2. **The window**: menu bar (File, Edit, View, Draw, Vectors, Toolpaths, Nesting, Plugins, Window, Help) with every item of
   `ui_structure.json` and its shortcut; an item not done yet is shown greyed with the tooltip "Not on the web yet". The quick
   tool bar on top, the drawing tool bar on the left, the docks (Project: Layers / Sheets / Vector Library; Selection /
   Transform; Toolpaths / Cut Order / Simulation), the status bar, the document tabs. Docks can be closed and shown again from
   View; their sizes are kept (localStorage).
3. **The drawing**: rulers, the sheet with its grid, vectors in their layer colours, the toolpaths drawn over them (as the
   desktop canvas), zoom at the cursor (wheel), pan (middle drag / space+drag), Zoom Extents (F), select (click, shift,
   window and crossing), move by dragging, arrow keys nudge, delete. Touch: pinch to zoom, two fingers to pan.
4. **File**: New, Open (a file of the device, or of the agent through a file browser of the roots), Save / Save As (`.icam`
   download or to the agent), Import Vectors (every importer of the schema, `.cdr` too), Export Vectors, Save G-code (a post,
   the files written by `op_gcode`, downloaded or saved to the agent). Recent files.
5. **Edit**: Undo / Redo (Ctrl+Z, Ctrl+Y and Ctrl+Shift+Z; the app keeps project snapshots), Delete, Duplicate, Cut / Copy /
   Paste, and every command of the schema in its menu (`op_command`), a form built from its params as the desktop builds it.
6. **Toolpaths**: New Toolpath (every operation of the schema), the toolpath window as on the desktop (Settings / Tool /
   Vectors tabs, its params, the tool from the tool database), edit, delete, enable, reorder, groups, Recalculate (F5,
   `op_compute`), Material Setup, Tool Database (kept in the user folder), Simulate (F6, `op_simulate`), Save G-code.
7. **Nesting**: Nest Parts (`op_nest`) with its settings.
8. Layers and sheets panels as on the desktop (add, rename, colour, show, lock; sheets with groups stacked).

Later rounds: cut order panel, packages of toolpaths, vector library, keyboard shortcut editor, the rest of the menus.

## How the work is done

* Do not commit or push; the coordinator does. Do not touch files you do not own.
* Python: `/tmp/claude-0/-home-user/5e728309-d616-5344-bea7-183da02f0b49/scratchpad/venv/bin/python` (PySide6, pytest,
  pytest-xdist; iyicam installed editable from `/home/user/iyicam`). Tests: `QT_QPA_PLATFORM=offscreen <python> -m pytest -q
  -p no:cacheprovider -n 4 tests`. One test (`test_snap.py::test_the_status_bar_says_what_the_pointer_clung_to`) fails only
  in parallel runs, before any change: ignore it.
* Node 22; Playwright: `NODE_PATH=$(npm root -g) node script.js` (`require("playwright")`, Chromium needs no options).
  Pyodide for Node: `/tmp/claude-0/-home-user/5e728309-d616-5344-bea7-183da02f0b49/scratchpad/pyo/node_modules/pyodide`.
  The CDN `cdn.jsdelivr.net` is reachable from here.
* Fixtures from the real engine: `docs/webapp/fixtures/schema.json` (the schema), `project.json` (a project of 19 parts and
  two toolpaths), `compute_result.json` (what `op_compute` returns for it).
* No build step: plain ES modules, served as they are; no framework, no npm dependency in the app (the old page did the
  same). Phone and desktop browsers (Chrome, Edge, Firefox, Safari).
* Write in the style of the code base (comments that say why; names of the domain).

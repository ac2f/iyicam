# The engine and the files API are ready

Proven by `tests/webapp/test_engine_parity.py` (Chromium + Pyodide against `iyicam serve`): the browser engine gives the same
schema, the same toolpaths and the same G-code text (apart from the `(DATE ...)` line) as the native `ops.op_*`.

## Start the dev server

```
python -m iyicam serve --local --root ~/some/folder --port 8300    # the app is at http://localhost:8300/app/
python -m iyicam serve --show-key                                  # the key to sign in with
```

* `/` redirects to `/app/`; `/app/` serves `iyicam/webapp/` as it is (every request is revalidated, so edits show at once).
* `--local` shares the folders of `--root` (the roots) with the files API; without it the files come from an agent
  (`python -m iyicam agent --server http://localhost:8300 --key KEY --root FOLDER`). With neither, `Files.status()` says
  `agent: null` and the app should fall back to the file picker / download.
* The pages are served **cross-origin isolated** (COOP `same-origin` + COEP `require-corp`): that is what makes cancelling a
  calculation instant. Consequence for the interface: everything loaded from another origin must be sent with CORS or CORP
  (jsDelivr, Google Fonts, PyPI are; for fonts and stylesheets from elsewhere add `crossorigin`). Best: self-host fonts/icons.
  CSP: scripts only from `self` and `cdn.jsdelivr.net`; styles may be inline; images `self`/`data:`/`blob:`; no `eval`.
* Not served from this server (e.g. `python -m http.server`) the engine still loads (it falls back to the CDN and
  `/app/py/iyicam.zip`), but without the headers cancelling restarts the worker.

## Engine (`iyicam/webapp/engine/engine.js`)

```js
import { Engine, EngineError } from "./engine/engine.js";
const engine = await Engine.start({ onStatus: (text, fraction) => {/* "Starting Python", 0.15 ... "Ready", 1 */} });
const schema = await engine.call("schema", {});
const ac = new AbortController();
const r = await engine.call("compute", { project }, { onProgress: (k, n, label) => {}, signal: ac.signal });
try { await engine.call("validate", {}); } catch (e) { e instanceof EngineError; e.code; e.message; e.details; }
```

* Any `op_<name>` of `iyicam/service/ops.py` by its name (`schema`, `import`, `command`, `compute`, `gcode`, `simulate`,
  `nest`, `new_project`, `posts`, `tp_presets`, ...). Calls run one at a time, in order.
* `EngineError.code`: the `ServiceError` code (`bad_request`, `unknown_operation`, ...), `cancelled`, or `internal` (the
  Python traceback is in `details`); `engine_failed` / `engine_crashed` when the worker itself fails.
* `signal.abort()` rejects with `code: "cancelled"` (about 30 ms for `compute`, the worker stays loaded). Not isolated: the
  call is rejected at once and the worker restarts (`engine.ready` is then a new promise; calls wait for it).
* `engine.version` (iyiCAM), `engine.pyodideVersion`, `engine.ready`, `engine.persistent` (the user folder -- tools, posts,
  presets -- is kept in IndexedDB; false in a private window), `engine.cancelMode` (`"flag"` | `"restart"`), `engine.close()`.
* First load about 4-6 s on a fast line (Pyodide 2 s, packages 1 s, importing iyiCAM 1.5 s), faster when the browser has the files
  cached. Show the `onStatus` text; the schema can be read as soon as `Engine.start` resolves.
* Bytes are base64 (`data_b64`) as in `ops.py`; a `gcode` answer has `files: [{name, mime, text}]`.

## Files (`iyicam/webapp/engine/files.js`)

```js
import { Files, FilesError } from "./engine/files.js";
await Files.login(key, { remember: true });   // POST /api/login; the cookie does the rest. Files.logout(), Files.session()
await Files.status();                         // {signed_in, agent: {connected, name, local} | null, roots: [{name, path}]}
await Files.list("");                         // the roots; Files.list("jobs/2024") -> [{name, path, dir, size, mtime}], folders first
await Files.stat(path);  await Files.read(path);                       // Uint8Array
await Files.write(path, bytesOrString, { overwrite: false });          // {path, size, mtime}
```

Paths are virtual: `<root name>/sub/file.icam` (never a disk path). Errors are `FilesError {code, message, status}`:
`not_logged_in`, `agent_offline`, `no_files`, `path_not_allowed`, `not_found`, `file_exists`, `file_too_large`,
`unsupported_file`, `bad_path`. Only the kinds of file the app uses are listed and read (`.icam .dxf .svg .plt .hpgl .cdr .png .json`
and G-code extensions); nothing hidden; a program or script is never written.

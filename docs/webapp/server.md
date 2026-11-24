# The server, the agent and the engine of the web app

Code: `iyicam/share/` (`server.py`, `agent.py`, `relay.py`, `rootfs.py`, `pack.py`, `sessions.py`), `iyicam/webapp/engine/`
(`engine.js`, `worker.js`, `files.js`), `iyicam/webapp/bridge.py`. How to use it from the interface: `ENGINE_READY.md`.

## The engine

`Engine.start()` starts `worker.js` (a module worker). All downloads begin at once: the Pyodide runtime (`pyodide.mjs`, the
wasm), `/app/py/iyicam.zip`, `/app/bridge.py`, and (self-hosted) the wheels. Then: Pyodide starts; `numpy`, `pyclipper`, `micropip`
load while the user folder is read from IndexedDB; `micropip.install(["ezdxf", "svgelements"])`; the zip is unpacked to
`/lib`; `bridge.init()` imports `iyicam.service.ops`. About 4-6 s from empty caches (headless Chromium here), less when the
browser has the files; every request is `no-cache`-revalidated (ETag), so an unchanged file costs a 304.

`IYICAM_HOME` is `/home/pyodide/.iyicam`, an IDBFS mount. After an operation the bridge compares the folder's file list (name, size,
time) with what it was before and the worker syncs to IndexedDB (before it answers) when it changed: tools, posts, presets, plugins
survive a reload. In a private window IndexedDB can be missing: the engine still runs and `engine.persistent` is false.

**Cancelling.** `compute`, `gcode`, `simulate`, `nest` are synchronous Python in the worker, which cannot hear a message meanwhile.
Chosen: a `SharedArrayBuffer` flag (needs cross-origin isolation, hence the COOP/COEP headers). `signal.abort()` does
`Atomics.store(flag, 1)`; the bridge's `Control.cancelled()` (the `ctl` that `ops.py` expects) reads it, `ops.py` raises
`JobCancelled` and the call rejects with `EngineError("cancelled")` in tens of milliseconds, the worker stays loaded. Fallback
(not isolated: another static host, an old browser): the worker is terminated and restarted (`engine.cancelMode === "restart"`;
the call is rejected at once, `engine.ready` is a new promise, queued calls wait). Calls run one at a time.

Progress: `ctl.progress(k, n, label)` posts `{k, n, label}` to the page, at most every 50 ms (always the last).

Errors: `{ok:false, error:{code, message, details}}` from the bridge becomes `EngineError`; a `ServiceError` keeps its code and
`params` (as `details`), anything else is `internal` with the traceback. `unknown_operation` for a name without `op_<name>`.

Not available in the browser: the LibreOffice converter for `.cdr` (the built-in reader covers CorelDRAW 5 to X8; the schema's
`cdr.converters` lists only it).

## HTTP

Everything answers with `Cross-Origin-Opener-Policy: same-origin`, `Cross-Origin-Embedder-Policy: require-corp`,
`Cross-Origin-Resource-Policy: same-origin`, `X-Content-Type-Options: nosniff` and a CSP. Errors are
`{"error": code, "message": text, ...}` with a fitting status.

| | |
|---|---|
| `GET /` | 302 to `/app/` |
| `GET /app/...` | `iyicam/webapp/` as it is (no hidden names, nothing outside it); ETag, gzip |
| `GET /app/config.json` | `{pyodide, zip, bridge, wheels}`: where the worker finds Pyodide (`https://cdn.jsdelivr.net/pyodide/v0.27.7/full/` or `/app/pyodide/`) |
| `GET /app/py/iyicam.zip` | the engine's package, from the installed files (zip kept until a file's size or time changes) |
| `GET /app/pyodide/...` | the `--pyodide DIR` folder |
| `POST /api/login` `{key, remember?}` | sets the session cookie (12 hours, `remember`: 90 days renewed on use); 401 `bad_key`, 429 `throttled` (`retry_after`) |
| `POST /api/logout` | |
| `GET /api/session` | `{signed_in, mode: "agent" or "local"}` |
| `GET /api/files/status` | `{signed_in, agent: {connected, name, local} \| null, roots: [{name, path}]}` (nothing for a stranger) |
| `GET /api/files/list?path=` | `{path, entries: [{name, path, dir, size, mtime}], truncated}`; empty path: the roots |
| `GET /api/files/stat?path=` | one entry |
| `GET /api/files/read?path=` | the bytes (`application/octet-stream`) |
| `PUT /api/files/write?path=&overwrite=0\|1` | body = the bytes; `{path, size, mtime}`; 409 `file_exists` |

File errors: `bad_path` 400, `path_not_allowed` 403, `not_found` 404, `not_a_file`/`not_a_folder` 400, `file_exists` 409,
`file_too_large` 413, `unsupported_file` 415, `not_logged_in` 401, `no_files` 503 (no agent, no `--local`), `agent_offline` 503,
`agent_timeout` 504, `too_busy` 503. Every request that changes something checks `Origin` against `Host`.

## The agent protocol (the agent calls the server; `Authorization: Bearer KEY`, `X-Agent-Id`)

* `POST /agent/poll` `{id, name, roots: [names]}`: held up to 25 s; answers `{job: null}` or `{job: {id, op, params, body_size}}`
  (`op`: `list`, `stat`, `read`, `write`). The poll is also the "I am here": an agent not heard of for 35 s is not connected; the
  server remembers its name and root names for `status`. One agent per server: another id gets 409 `agent_taken`.
* `GET /agent/body/<job>`: the bytes of a write.
* `POST /agent/reply/<job>` with `X-Status` and the bytes (a JSON object for list/stat/write and for errors, the file for read).
* `POST /agent/bye`: leaving.

The agent does up to four jobs at the same time, retries with back-off (max 30 s), stops with exit status 2 when the key is refused,
and checks every path itself (the server is not trusted to have checked).

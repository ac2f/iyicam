# iyiCAM as a service (`iyicam.service`)

The engine behind a plain JSON interface: no window, no `Controller`, no files the caller has to name. It is what the
mobile page of the [CNC-Assistant](https://github.com/ac2f/Cnc-Assistant) project talks to (through its agent), and
it is just as useful from a script or another program.

Every operation takes the **whole project as data** (the `.icam` JSON) and returns data. Nothing is remembered between
calls: the project lives where it is edited, this runs where the heavy work and the files are. What the GUI's
`Controller` does after a calculation (keep the cut order, move smart start points into the vectors, calculate again)
is done the same way here, so the G-code equals the desktop program's.

```python
from iyicam.service import ServiceHost

host = ServiceHost()                                   # one per process
schema = host.handle("schema")                         # forms, posts, file formats, tools
out = host.handle("import", {"name": "a.dxf", "data_b64": "..."})      # -> {"project": {...}, "report": {...}}
job = host.handle("job.start", {"op": "compute", "payload": {"project": out["project"]}})
host.handle("job.status", {"id": job["id"]})           # {"state": "running", "k": 3, "n": 10, "label": "..."}
host.handle("job.result", {"id": job["id"]})
```

`host.handle(op, payload)` never raises: a failure is `{"error": code, "message": "...", "params": {...}}`
(`host.call` raises `ServiceError` instead). Codes are stable; the message is English.

Install: `pip install iyicam` (no Qt needed: the service uses only the core dependencies). User posts, tools and
plugins are read from `~/.iyicam` (`IYICAM_HOME`); `IYICAM_SERVICE_PLUGINS=0` leaves plugins out.

## Operations

| operation | payload | answer |
|---|---|---|
| `ping` | | `{ok, api, iyicam, plugin_errors}` — `api` is the version of this interface (`API_VERSION`) |
| `schema` | | `operations` (toolpath types with their parameters), `commands`, `posts`, `importers`, `exporters`, `tools`, `tool_types`, `sequence` (cut order options), `origins`, `layer_colors`, `cdr` (can CorelDRAW files be read here: `{available, converters, install, reads}`) |
| `validate` | `project` | `{ok, project, counts}` — the project as this program would write it, or `invalid_project` |
| `import` | `name`, `data_b64`, `thickness?`, `project?`, `sheet?`, `options?` | `{project, report, added}`; with `project` the drawing is added to it. `report.via` says how a `.cdr` file was read, e.g. `CorelDRAW X3 (builtin)`; its errors keep their own codes (`cdr_not_cdr`, `cdr_version`, `cdr_text_only`, `cdr_failed`, `cdr_timeout`, ...), see [cdr.md](cdr.md) |
| `export` | `project`, `format` (`dxf` `svg` `icam`, …), `sheet?` (id or `"all"`), `selection?`, `options?`, `file_name?` | `{files: [{name, mime, data_b64}]}` |
| `command` | `project`, `command` (an id from `schema`), `sheet?`, `selection?`, `values?`, `active_layer?` | `{ok, project, selection, messages, changed}`; `command_failed` leaves nothing changed |
| `compute` | `project`, `sheets?`, `toolpaths?`, `optimise_starts?`, `preview?`, `preview_step?` | `{results, patch, totals, seconds}` — see below |
| `gcode` | `project`, `post`, `sheets?`, `file_name?`, `program_number?` | `{files: [{name, mime, text, sheet}], errors, warnings, ok}` |
| `simulate` | `project`, `sheet?`, `cells?`, `resolution?` | `{png_b64, width_px, height_px, resolution, extent, sheet, time_s}` |
| `nest` | `project`, `objects?`, `quantities?`, `settings` (the options of `iyicam nest`) | `{project, placed, unplaced, plates, sheets, squeezed, suggestion}` |

`compute` returns, for every toolpath and sheet, its `warnings`, `error`, `stats` (time, lengths, depth, extent) and
(unless `preview: false`) a `preview`: polylines of the cutting moves with their deepest Z (`cuts`), the moves in the
air (`rapids`) and the holes (`drills`), thinned to a size a phone can draw. `patch` is what the calculation changed in
the project itself: `vectors` (the vectors whose start points were moved, whole) and `sequences` (the cut order of each
toolpath). Give `toolpaths` to calculate only some; the others are then not seen by them (a warning about a cut inside
a part another toolpath cut free needs them).

## Jobs

`compute`, `gcode`, `simulate` and `nest` (and `command`, `import`, `export`) can run as jobs, for a caller that cannot
wait (a request/response channel with a time limit, a phone that may be put to sleep):

| | |
|---|---|
| `job.start` `{op, payload}` | `{id, state: "queued"}` (`too_busy` when 16 jobs are already waiting or running) |
| `job.status` `{id}` | `{state: queued│running│done│error│cancelled, k, n, label, elapsed, error}` |
| `job.cancel` `{id}` | cooperative: the calculation looks at the flag and stops |
| `job.result` `{id}` | the answer of the operation, or its error; can be fetched again until the job is forgotten (30 min) |

A job keeps no document, only its progress and result. Without `job.start`, a long operation called directly runs in
the calling thread.

## Tested against the program itself

`tests/test_service.py` checks that the answers equal the program's: G-code equal to `iyicam gcode`, a layout equal to
`iyicam nest`, every command equal to running it directly, every answer strict JSON (no NaN, no objects), and that a
wrong request is an error code and never an exception.

## Numbers for a browser implementation

A browser can edit the project itself (move, turn, scale, align, arrays, …) and use this only for what needs the
engine. To keep the two the same, `tools/make_js_golden.py` runs the real commands on a project of every kind of shape
and writes inputs and results to a JSON file; the JavaScript tests of the other project run the same inputs and compare.
Make it again whenever one of the commands in its `LOCAL` list changes:

```bash
python tools/make_js_golden.py -o ../Cnc-Assistant/tests/js/fixtures/golden.json
```

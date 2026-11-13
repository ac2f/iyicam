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
| `gcode` | `project`, `post` (or `post_draft`: a post not saved, see `post_save`), `sheets?`, `file_name?`, `program_number?` | `{files: [{name, mime, text, sheet}], errors, warnings, ok}` |
| `posts` | | `{posts, errors, specs, glossary}` — every post as a *draft* (`id, name, description, extension, extends, options, blocks`, with every option and block as the post has it, and `builtin`, `editable`), the files of the user's posts that cannot be loaded, a description of every option / block / variable in English and Turkish (`specs`, for building a form) and the glossary of G-code words (`glossary.en`, `glossary.tr`) |
| `post_save` | `post` (a draft; no `id`: one is made of the name) | `{saved, file, warnings, posts, errors, specs, glossary}` — writes `~/.iyicam/posts/<id>.toml` with only what differs from the post it `extends`; `invalid_post` says what is wrong (a built-in post is never overwritten) |
| `post_delete` | `id` | `{deleted, posts, …}` — only posts of the user |
| `post_preview` | `post` (a draft) | `{files: [{name, text}], warnings}` — the G-code of a small sample job (arcs, a drilling, two tools) written with the draft; `invalid_post` or `post_failed` (an unknown `{variable}`) with the reason |
| `gcode_explain` | `lines` (the lines of a file up to the one asked, at most 5000), `index?`, `lang?` (`en` `tr`), `post?` | `{summary, words: [{word, text}], arc, at, index}` — what the line says; for an arc the centre and the radius as a place on the sheet |
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
| `job.start` `{op, payload, keep?, wait?}` | `{id, state: "queued"}` (`too_busy` when 16 jobs are already waiting or running); `keep` makes it a **kept job**, see below |
| `job.status` `{id}` | `{state: queued│running│done│error│cancelled, k, n, label, elapsed, error}` |
| `job.cancel` `{id}` | cooperative: the calculation looks at the flag and stops |
| `job.result` `{id, device?}` | the answer of the operation, or its error; can be fetched again until the job is forgotten (30 min; a kept job: see below). `device` notes who was given it |
| `jobs` `{device?}` | the **kept jobs**, newest first: `[{id, kind, state, k, n, label, created, finished, title, device_label, mine, delivered, deliveries, size, has_result, error}]` and `recent` (3) |
| `job.dismiss` `{id}` | take a kept job off the list (one that is running is cancelled first) |

A job keeps no document, only its progress and result. Without `job.start`, a long operation called directly runs in
the calling thread.

### Kept jobs (the last three)

A job runs at the engine, in its own thread, whatever its caller does: a phone that loses its connection, a browser that is
closed or goes to sleep does not stop it. A job started with `keep: {title, device, device_label}` (the page does it for
nesting: `device` is an id the page made for itself and keeps in its browser, `device_label` a name like `Android · Chrome`)
is one of the **last three jobs** of the engine:

* it stays, with its result, until three newer ones push it out (a job that is not over is never pushed out) or for a week;
  `jobs` lists them, from any device, and `job.result` gives the result again whenever it is asked for;
* the engine notes which devices were given the result (`delivered` is for the `device` asked with, `mine` says that device
  started the job): a device that comes back finds what it has not received and can **ask its person before it takes it**;
  a job that was over within the call that started it counts as given to the device that started it;
* they are written to `~/.iyicam/jobs` (`index.json` and one gzip file per result, mode 600; a result larger than 256 MB stays
  in memory only), so **restarting the engine does not lose a finished job**; one that was still running then is `error` with
  `interrupted`. An unreadable index means no kept jobs, nothing more. With a hub the files are those of the agent's computer.

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

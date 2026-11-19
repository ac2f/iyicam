# Writing plugins

A plugin is a Python file (`my_plugin.py`) or package (`my_plugin/__init__.py`)
placed in `~/.iyicam/plugins/`, or an installed package that exposes the
`iyicam.plugins` entry point group:

```toml
# pyproject.toml of a third party package
[project.entry-points."iyicam.plugins"]
my_plugin = "my_package.my_plugin"
```

The module defines a `PLUGIN` dictionary and a `register(api)` function;
`unregister(api)` is optional (everything a plugin registers is removed
automatically when it is disabled).

```python
PLUGIN = {"name": "Hello", "version": "1.0", "author": "me", "description": "Adds a square"}

def register(api):
    def run(ctx, values):
        ctx.add(api.shapes.rectangle(0, 0, values["size"], values["size"]), name="Square")

    api.add_command("hello.square", "Hello Square...", run, menu="Plugins/Hello",
                    params=[api.Param("size", "Size", "length", 50.0, min=1.0)])
```

Plugins are managed in **Plugins ▸ Plugin Manager** (enable / disable, reload,
install, error details). A plugin that raises while loading is reported there
and everything it registered so far is rolled back.

The built-in features of iyiCAM are registered through this same API in
`iyicam/builtins/__init__.py`, which is a good reference. More examples live in
`examples/plugins/`:

| File | Shows |
|------|-------|
| `surfacing.py` | a new toolpath strategy (spoilboard surfacing) |
| `gcode_stats.py` | a G-code filter |
| `box_maker.py` | a vector command with parameters (finger jointed boxes) |

## The `api` object

| Method | Purpose |
|--------|---------|
| `api.add_operation(cls)` | register a toolpath strategy (subclass of `api.Operation`) |
| `api.add_command(id, label, run, menu=..., params=[...], min_selection=0, shortcut=None, tooltip="")` | add a command to the menus; `run(ctx, values)` |
| `api.add_postprocessor(post)` | a `PostProcessor` instance, a definition `dict` or the path of a `.toml` file |
| `api.add_importer(("ext",), label, func)` | `func(path, project, sheet, layer_id=None, **options) -> [VectorObject]` |
| `api.add_exporter("ext", label, func, params=None)` | `func(path, project, sheet, objects_or_None, **options)`; the `params` (a list of `Param`) are asked for in a dialog before the file is written (remembered), and passed on as the options |
| `api.add_gcode_filter(func)` | `func(text, job, post) -> text`, runs on every generated file |
| `api.on(hook, func)` | event hooks, see below |
| `api.add_gui_extension(func)` | `func(main_window)` once the GUI is ready (add menus, docks, ...) |

The main window has one tab per open project. `main_window.ctrl` and `main_window.canvas` are the document and the
drawing of the tab that is **open now**, so read them when the user does something (in a menu action, a hook)
and do not keep them; `main_window.documents()` lists the controllers of all tabs and `main_window.tabs` is the
tab widget. The hooks `project_loaded` and `project_saved` are called for the project of the tab concerned.

Building blocks re-exported on `api`: `Param`, `Command`, `CommandContext`,
`CommandError`, `Operation`, `OpContext`, `Toolpath`, `MoveBuilder`,
`PostProcessor`, `depth_params`, `depth_levels`, `final_depth`, `stepdown` and
the modules `geom`, `shapes`, `vectorops`, `clip`, `moves`.

### Hooks

| Hook | Arguments |
|------|-----------|
| `project_loaded` | `project` |
| `project_saved` | `project` |
| `selection_changed` | `project, ids` |
| `toolpath_computed` | `project, toolpath_def, sheet, result` |
| `before_post` | `job, post` |
| `after_post` | `job, post, files` |
| `gui_ready` | `main_window` |

## Parameters (`api.Param`)

Commands and toolpaths describe their settings with `Param` objects; the GUI
builds the dialogs automatically.

```python
api.Param(name, label, kind="float", default=None, min=None, max=None, step=None,
          choices=None, group="", help="", visible=None, decimals=3)
```

| kind | value | widget |
|------|-------|--------|
| `float`, `angle`, `percent` | float | spin box |
| `length`, `feed` | float in mm, mm/min | spin box shown in the user's units |
| `int` | int | spin box |
| `bool` | bool | check box |
| `choice` | str, `choices=[(value, label), ...]` | combo box |
| `str`, `text`, `font` | str | line edit, text box, font picker |
| `tool` | `Tool` copy or `None` | tool library picker |
| `point` | `(x, y)` | two spin boxes |

`group` puts parameters into boxes, `visible={"other": value}` (or a list of
values) shows a parameter only when another one matches. A `length` with
default `None` is shown as an optional field ("auto").

## Commands

`run(ctx, values)` receives a `CommandContext`:

| Member | |
|--------|---|
| `ctx.project`, `ctx.sheet` | the document and the active sheet |
| `ctx.selected` | selected `VectorObject`s (only editable ones) |
| `ctx.active_layer_id` | |
| `ctx.add(contours, layer_id=None, name="", select=True)` | create a vector object |
| `ctx.add_object(obj)`, `ctx.remove(ids)`, `ctx.replace(obj, contours)` | |
| `ctx.select(ids)`, `ctx.message(text)` | a line for the status bar (desktop) or the toast (page) |
| `ctx.section(title, rows)`, `ctx.warn(text)`, `ctx.title`, `ctx.data` | a **report** (blocks of `(label, value)` rows), a warning, a title and numbers to hand back |

Raise `api.CommandError("...")` to show a message to the user. Every command
run from the GUI is one undo step.

### Giving an answer back

A command answers like a function does: **what `run` returns is read**, and
`ctx.message` is only one way to say something.

```python
def measure(ctx, values):
    total = sum(c.length() for o in ctx.selected for c in o.contours)
    return {"title": "Length",                                   # the title of the window
            "messages": [f"Total {total:.1f} mm"],               # the status bar / toast (a plain str, or a list of str, works too)
            "report": [{"title": "TOTAL", "rows": [["Length", f"{total:.1f} mm"]]}],   # a window to read and copy
            "warnings": ["2 open outlines"],                     # after the messages, with a ⚠
            "data": {"total": total}}                            # numbers for scripts and the page's own code
```

| `run` returns | the program |
|---------------|-------------|
| `None` | says what `ctx.message` said, as before |
| a `str` or a list of `str` | says them |
| a `dict` | `title`, `messages` (or `headline`), `report` (or `sections`), `warnings`, `data`; any other key is data as well |
| anything else | `data["value"]` |

* **A report opens a window** (desktop: a dialog with the text to select and *Copy*; page: a sheet with the rows and *Copy the
  report*). A command that returns no `report` says its lines in the status bar / toast only. Return a `report` when the
  answer is something to *read* (a measurement, a count, a list), not for every run.
* **All the messages are shown**: the desktop's status bar holds them in one line (the last no longer overwrites the others) and
  the page's toast shows the first three.
* **Scripts get the answer back**: in the console, `run("my.command", size=40)` returns
  `{"title", "messages", "report", "warnings", "data"}` (`False` if the command failed) and `print(report_text(result))` reads
  a report; `Controller.last_result` holds it too. On the page it is `result` of the service's `command` call
  ([service.md](service.md)).

## Toolpath strategies

```python
class Spiral(api.Operation):
    id = "my.spiral"                 # stored in project files
    label = "Spiral"
    description = "Shown at the top of the dialog"
    tool_types = ("end_mill",)       # None = any tool

    @classmethod
    def params(cls):
        return api.depth_params(2.0) + [api.Param("turns", "Turns", "int", 5, min=1)]

    def compute(self, ctx):          # ctx: api.OpContext
        tool = ctx.tool
        mb = api.MoveBuilder(ctx.safe_z, ctx.clearance, tool.feed_rate, tool.plunge_rate)
        depth = -api.final_depth(self, ctx)
        for c in ctx.contours:       # all contours of the selected vectors on this sheet
            mb.travel(*c.start, depth)               # retract, rapid, plunge
            for seg in c.segs:
                mb.segment(seg, depth)               # G1 / G2 / G3
        return [self.make_toolpath(ctx, mb.finish())]
```

`OpContext` offers `project`, `sheet`, `tdef` (the stored toolpath definition),
`objects`, `contours`, `tool`, `material`, `thickness` (of this sheet),
`safe_z`, `clearance`, `tolerance` and `warn(text)`.

Moves are millimetres in sheet coordinates with Z = 0 at the material top:
`Rapid`, `Feed`, `ArcMove` (centre, direction, optional helix), `DrillCycle`,
`Dwell`, `Comment`. Never emit an `ArcMove` whose start and end coincide (split
full circles in two). A strategy may return several toolpaths (e.g. V-carving
plus flat area clearing with another tool, using a parameter of kind `tool`).

## G-code filters

```python
def register(api):
    def stamp(text, job, post):
        return f"(made for {job.project.name})\n" + text
    api.add_gcode_filter(stamp)
```

## Plugins on the web page and the phone

The page (also on a phone) has a **Plugins** card on the **Project** tab: the plugins of the engine with what each one added
(commands, toolpath types, importers and exporters, posts, G-code filters), a switch to turn one **on or off**, **Reload**,
**Remove**, and **Install a plugin (.py / .zip)**. A plugin is not run in the browser: it runs on the engine, the server of
`iyicam serve` or the PC of the agent ([web.md](web.md)), and what it registers is offered by the page at once:

| What the plugin adds | Where the page shows it |
|---|---|
| a vector command (`api.add_command`) | the ⋯ button of the Drawing tab, with its parameters in a form |
| a toolpath strategy (`api.add_operation`) | the Toolpaths tab, in the list of toolpath types |
| an importer / exporter | opening a file on the Project tab / the Output tab |
| a post (`api.add_postprocessor`) | the post list on the Output tab |
| a G-code filter, a hook | runs on the engine when G-code is made |
| `api.add_gui_extension` | nothing: windows and menus exist only in the desktop program |

**Installing.** Choose a `.py` file or a `.zip` (a package folder with an `__init__.py`, the package itself, or one `.py`). The
page asks you to tick that you trust it, because **a plugin is a program that runs with the rights of the user the engine runs
as**. Before anything is written the engine looks at it: the file name (letters, digits, `_`), the size (2 MB a file, 8 MB a zip,
24 MB unpacked, 300 entries), the paths of a zip (no `..`, no absolute path, no link, only Python files, text, data and pictures)
and that the Python compiles; nothing is run until it is loaded, and a plugin that raises while loading is rolled back and its
error shown on its row. Installing a name again replaces the plugin. What is switched off stays off (`~/.iyicam/plugins.json`).

**Turning it off.** The owner of an engine can forbid installing and removing from the page: `iyicam serve --no-plugin-install`,
`iyicam agent --no-plugin-install` or `IYICAM_PLUGIN_INSTALL=off` (switching on and off still works); `--no-plugins` /
`IYICAM_SERVICE_PLUGINS=0` leaves plugins out altogether. A hub's own engine, which answers when an agent is away, never installs
one (`needs_agent`): plugins belong to the computer of the agent.

**For programs** (the same operations the page calls, [service.md](service.md)): `plugins` (the list), `plugin_install`
(`name`, `data_b64`), `plugin_set` (`key`, `action`: `enable`, `disable`, `reload`, `remove`). Tests: `tests/test_plugins_web.py`.

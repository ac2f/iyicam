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
| `api.add_exporter("ext", label, func)` | `func(path, project, sheet, objects_or_None)` |
| `api.add_gcode_filter(func)` | `func(text, job, post) -> text`, runs on every generated file |
| `api.on(hook, func)` | event hooks, see below |
| `api.add_gui_extension(func)` | `func(main_window)` once the GUI is ready (add menus, docks, ...) |

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
| `ctx.select(ids)`, `ctx.message(text)` | |

Raise `api.CommandError("...")` to show a message to the user. Every command
run from the GUI is one undo step.

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

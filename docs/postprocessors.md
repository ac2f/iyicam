# Post-processors

Post-processors turn calculated toolpaths into controller specific G-code. They
are TOML files: the built-in ones are in `iyicam/post/builtin/`, your own go to
`~/.iyicam/posts/`. **Toolpaths ▸ Post-processors...** creates a custom post from
any existing one and shows a live preview of the current sheet while you edit.

```toml
id = "my_mach3"            # unique id (defaults to the file name)
extends = "mach3"          # inherit every option and block not given here
name = "Mach3 (my machine)"
description = "Mach3 with a 5 second spindle warm-up"
extension = "tap"

[options]
decimals = 3
line_numbers = true

[blocks]
tool_change = """
M5
G0 Z{safe_z}
T{tool_number} M6
S{spindle_speed} {spindle_dir_code}
G4 P5
"""
```

## Options

An option of the wrong kind (`decimals = "many"`, `units = "furlong"`, a number where a switch is
expected) makes the post unusable: it is left out of the list, the other posts load as usual, and
**Toolpaths ▸ Post-processors...** shows the file with the name of the option so it can be mended there.

| Option | Default | Meaning |
|--------|---------|---------|
| `units` | `"mm"` | `mm` or `inch` (coordinates and feeds are converted) |
| `line_ending` | `"lf"` | `lf` or `crlf` |
| `decimals` | `3` | decimals of coordinates |
| `feed_decimals` | `0` | decimals of feed rates (one more in inches) |
| `trailing_zeros` | `false` | `X10.500` instead of `X10.5` |
| `leading_zero` | `true` | `X0.5` instead of `X.5` |
| `integer_point` | `false` | `X10.` instead of `X10` (Fanuc style) |
| `g_leading_zero` | `false` | `G01` instead of `G1` |
| `arcs` | `"ijk"` | `ijk`, `r` or `none` (arcs written as short lines) |
| `ijk` | `"incremental"` | `incremental` (centre relative to the start) or `absolute` |
| `arc_max_sweep` | `180` | larger arcs are split (degrees); with `arcs = "r"` at most 90 |
| `arc_min_radius`, `arc_max_radius` | `0.01`, `5000` | outside this range arcs become lines |
| `arc_tolerance` | `0.005` | chord tolerance when arcs are turned into lines |
| `helical` | `true` | allow G2/G3 with a Z change |
| `arc_xy_always` | `true` | always write X and Y on arc lines |
| `modal_coords` | `true` | omit unchanged X / Y / Z words |
| `modal_motion` | `true` | omit repeated G0 / G1 / G2 / G3 |
| `modal_feed` | `true` | write F only when it changes |
| `rapid_z_first` | `true` | split diagonal rapids: lift first, descend last |
| `line_numbers` | `false` | number the lines |
| `line_number_start`, `line_number_step`, `line_number_max` | `10`, `10`, `99999` | |
| `line_number_space` | `true` | `N10 G1...` or `N10G1...` |
| `comments` | `true` | write comment lines (lines starting with `(` or `;` in blocks are dropped when false) |
| `uppercase` | `true` | upper-case everything except comments |
| `drill_cycles` | `false` | use the `drill` / `drill_peck` canned cycle blocks |
| `dwell_units` | `"s"` | `s` or `ms` for `{dwell}` and the `P` word of cycles |
| `tool_change_first` | `true` | output the tool change block for the first tool |
| `max_spindle` | `0` | clamp the spindle speed (0 = no limit) |
| `split_by_tool` | `false` | write one file per tool (single spindle machines) |
| `rotary_letter` | `"A"` | angle word of wrapped (rotary) jobs |
| `rotary_feed` | `"inverse_time"` | feed of wrapped moves: `inverse_time` (G93, F = 1 / minutes on every line), `degrees` (F corrected for controllers that count degrees like millimetres) or `plain` |

## Blocks

| Block | When |
|-------|------|
| `header` | start of the file |
| `first_tool_change` | first toolpath (falls back to `tool_change` when empty) |
| `tool_change` | when the tool number changes |
| `tool_same` | next toolpath with the same tool (optional) |
| `toolpath_start`, `toolpath_end` | around every toolpath |
| `rapid`, `linear`, `arc`, `arc_r` | moves (`arc_r` is used when `arcs = "r"`) |
| `drill`, `drill_peck`, `drill_dwell`, `drill_next`, `drill_end` | canned cycles (`G81`, `G83`, `G82` with the dwell, next hole, `G80`); a dwell the chosen block has no `{P}` for is written as moves with `G4` |
| `dwell` | pauses |
| `comment` | comments coming from toolpaths (`{text}`) |
| `footer` | end of the file |
| `inverse_time_on`, `inverse_time_off` | around wrapped jobs with `rotary_feed = "inverse_time"` (`G93` / `G94`) |

Lines are formatted with Python `str.format` placeholders. Spaces are
collapsed and empty lines dropped, so `"{G} {X} {Y} {Z} {F}"` and
`"{G}{X}{Y}{Z}{F}"` (compact style) both work.

### Words (move blocks)

`{G}` motion code, `{X} {Y} {Z}` coordinates, `{I} {J} {K}` arc centre,
`{R}` arc radius (negative above 180 degrees), `{F}` feed, `{Q}` peck depth,
`{P}` dwell. A word expands to letter + number, or to nothing when it is modal
and unchanged.

### Variables (all blocks)

| Variable | |
|----------|---|
| `{file_name}` `{project_name}` `{sheet_name}` `{post_name}` | |
| `{date}` `{time}` | |
| `{units}` `{unit_code}` | `mm` / `inch`, `G21` / `G20` |
| `{program_number}` | for `O` words |
| `{material_thickness}` `{sheet_width}` `{sheet_height}` | |
| `{safe_z}` `{home_x}` `{home_y}` `{home_z}` | in output coordinates |
| `{xmin}` `{xmax}` `{ymin}` `{ymax}` `{zmin}` | extents of the job |
| `{total_time}` `{toolpath_count}` `{tools}` | |

Tool and toolpath blocks additionally have `{tool_number}`, `{tool_name}`,
`{tool_type}`, `{tool_diameter}`, `{tool_angle}`, `{spindle_speed}`,
`{spindle_dir_code}` (`M3` / `M4`), `{coolant_code}` (`M7` / `M8` / empty),
`{coolant_off}`, `{prev_tool_number}`, `{toolpath_name}`, `{feed_rate}`,
`{plunge_rate}`, `{toolpath_time}`. `{dwell}` is available in `dwell`,
`{text}` in `comment`. An unknown placeholder raises an error that names it.

## Coordinates

Toolpaths are calculated in sheet coordinates with Z = 0 at the material top.
When writing G-code the post subtracts the XY origin chosen in **Material
Setup** (bottom left, centre, ...), adds the material thickness when Z zero is
at the bottom and converts to inches when `units = "inch"`.

## Rotary (wrapped) jobs

With **Material Setup ▸ Wrap around a rotary axis** the sheet is the unrolled
cylinder: X runs along the axis and Y around it, so the sheet height becomes the
circumference. The post then writes the angle (`A`, see `rotary_letter`) in
degrees instead of Y, turns arcs into short lines (a wrapped arc is not a
circle) and keeps the surface speed at the toolpath feed rate (see
`rotary_feed`). Z zero is the cylinder surface or, with *Bottom*, the axis.
`Y{home_y}`, `Y{ymin}` and `Y{ymax}` in blocks become `A...`, so the built-in
footers turn the part back instead of moving the Y axis.

## Python posts

For logic templates cannot express, subclass the engine in a plugin:

```python
def register(api):
    class MyPost(api.PostProcessor):
        def finalize(self, lines, job):
            lines = super().finalize(lines, job)
            return [l for l in lines if not l.startswith("M9")]

    from iyicam.post.loader import builtin_definitions, merge
    base = builtin_definitions()["mach3"]
    api.add_postprocessor(MyPost(merge(base, {"id": "mach3_nocool", "name": "Mach3 without M9"})))
```

Useful hooks to override: `job_vars`, `tool_vars`, `block`, `rapid`, `linear`,
`arc`, `drill`, `fmt`, `finalize`.

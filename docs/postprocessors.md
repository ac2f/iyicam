# Post-processors

Post-processors turn calculated toolpaths into controller specific G-code. They
are TOML files: the built-in ones are in `iyicam/post/builtin/`, your own go to
`~/.iyicam/posts/`. **Toolpaths ▸ Post-processors...** (and the *Edit post...* button of **Save G-code**, which brings you back with the new post chosen)
creates a custom post from any existing one and shows a live preview while you edit; the page (`iyicam serve`) has the
same editor on its Output tab. You do not have to write TOML: every option is a field with a sentence of help and every
block a text box with buttons for the variables ([the editor](#the-post-editor)). The file it writes holds only what
differs from the post it is based on.

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

## The post editor

In the desktop window the **Settings** tab is a field for every option (a sentence of help under it, a ● for what
differs from the post it is based on), **Blocks of text** has a text for every block with lists of words and variables to
put at the cursor, and **File (TOML)** is the file itself: the form and the file are one thing seen two ways (change
either, the other follows). The preview below is made again after every change, on a sample part or on the current
sheet, and **clicking a line of it says what the line means**; **What are I J K?** opens the glossary.

Both editors (the desktop window and the page) work on the same description of the options and blocks
(`iyicam/post/editing.py`), and say what is wrong in words before a post is saved:

* an option of the wrong kind or outside its range (`decimals` must be between 0 and 8);
* a `{placeholder}` that is not a name (`{0}`, `{x.y}`), a brace that does not close (write `{{` and `}}` for a literal
  brace), or a variable this program does not know (a warning: the post is saved, the preview then names it);
* **I J that contradict the header**: with `ijk = "absolute"` the controller must be told, `G90.1` in the header (the
  usual `G91.1` reads I J from the start of the arc, so every arc would go to the wrong circle).

The preview is a small sample job (a plate with rounded corners, a hole to drill and a round pocket, two tools), so a
post can be tried before there is a project with toolpaths.

## What are I, J, K and R?

An arc line like `G2 X20 Y0 I5 J0 F800` says: cut a circle arc clockwise (`G3`: counter-clockwise) **to** X20 Y0 from
where the tool is. **I** and **J** (and **K**, for the Z direction, which flat arcs never need) give the **centre** of
the circle as the distance from the **start** of the arc: `I5 J0` is "the centre is 5 to the right of here". They are
not a place on the sheet. With the tool at (10, 0) the centre is at (15, 0) and the radius is 5. With `ijk =
"absolute"` (`G90.1`) I and J are the centre's own coordinates. `R` gives the radius instead (a minus sign: more than
half a circle), and `arcs = "none"` writes short straight lines, no arcs at all. Tap a line of G-code in the page or the
desktop's *Save G-code* window to have it explained, and see `iyicam/post/guide.py` for the words.

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

## The name of the files (Save G-code)

The Save G-code window of the desktop program, the Output tab of the page and the engine (`gcode` takes `name_parts`
and `name_sep`) name the files by **prefixes**: a list of texts, each with `{variables}` that the program fills in
(`{project_name}`, `{sheet_name}`, `{sheet_number}`, `{group}`, `{tool_number}`, `{tool_name}`, `{date}`, `{time}`,
`{post_name}`, `{material_thickness}`, `{sheet_width}`, `{sheet_height}`, `{toolpath_count}`, `{sheet_count}`), joined by
a separator (`_`). A variable is added from the list or the buttons, as in the blocks of a post; the window shows the
names the files will get, and says which variable it does not know. What was written is remembered. With several sheets
a number is added when the name has no `{sheet_number}` or `{sheet_name}`, so no file overwrites another. In the desktop
window a name typed over the proposed one in the file dialog is used as it is, as before.

**A file with several toolpaths**: every toolpath in it counts. `{toolpath_name}`, `{tool_number}`, `{tool_name}`,
`{tool_diameter}` and `{cut_depth}` are the values of all of them, each once, joined with `+` (`Outline+Holes`, `T1+2`);
`{safe_z}` is the highest and `{toolpath_count}` how many there are. With *One file per tool* every file is named by its own
toolpaths (`{toolpath_name}_T{tool_number}` gives `Outline_T1` and `Holes_T2`); when two files would get the same name the
post's `_1_T1`, `_2_T2` ... is put after it.

**A name made of the toolpaths: `{segments}`.** It writes a part for every toolpath of the file, in the order they are cut,
joined by `-` (*between* the parts). The part is a template, `{side}_{name}{depth}[a{allowance}][s{step}]z{safe_z}[m{count}]` at
first: `side` is `i` inside, `o` outside, `a` along the line (`p` pocket, `d` drill, `v` v-carve, `e` engrave), `name` the name of the
toolpath **without its digits** (`oyma01` → `oyma`: the numbers of a part start with its depth), `depth` the depth (a cut through:
the thickness), `allowance` the allowance when it is not 0 (`a0.2`, `a-0.1`), `step` the depth of a pass (only when there is more than
one), `safe_z`, `count` how many toolpaths are stacked; also `final_depth`, `stepdown`, `passes`, `tool_diameter`, `tool_number`,
`group`, `feed_rate`. A piece in `[brackets]` is left out when a variable in it has no value.

**Group names by order** (on by default; `stack` in the engine): toolpaths that come **one after the other** with the same settings
(name without digits, side, depths, allowance, safe Z, tool) are **stacked** into one part with their number (`m3`); one that differs in
anything (another allowance, another depth) is a part of its own, so the name says what is cut. The order is the order of the file:
change it in the Save G-code window (▲ ▼ or drag) and the name follows. With *Cut the toolpaths of a group one after the other* (on) the
toolpaths of a group are cut together, where the first of them is. Example: a 4 mm tool, `oyma01`, `oyma02`, `oyma03` (inside, 2 mm in
1 mm passes), `kesim` outside and `kesim` along, through 4 mm, safe Z 10, revision 1: `{revision:03}d{tool_diameter}-{segments}` gives
`001d4-i_oyma2s1z10m3-o_kesim4z10-a_kesim4z10`; with the second *oyma* at allowance 0.2 it is
`001d4-i_oyma2s1z10-i_oyma2a0.2s1z10-i_oyma2s1z10-...`. The page (Output tab) and the engine (`segment_template`, `segment_sep`,
`together`, `stack`) do the same.

**Sums and number formats in the prefixes**: between the braces there can be a sum of variables and numbers
(`{sheet_number + 100}`, `{sheet_width / 10}`) and, after a last `:`, how the number is written:
- `{revision:03}` (or `{revision:3}`): at least three digits, zeros in front: 1 → `001`, 12 → `012`;
- `{tool_diameter:.1}`: one decimal (6 → `6.0`); `{sheet_height/2:05.1}`: both;
- a text variable with a width is filled with zeros in front as well.
The desktop window, the page and the engine write the same name.

The order of the blocks in a file is always: start of the file, first tool / tool change, start of a toolpath, its
moves, end of a toolpath (and so on for every toolpath), end of the file.

## Arcs: straight lines by default (safe to pause)

By default (*Arcs as short straight lines (safe)*, `safe_arcs`, on) no G2 / G3 is written: every arc is written as short
straight G1 lines that stay within the arc tolerance (`arc_tolerance`, 0.005 mm) of it, the way ArtCAM's simple files are.
A G2 / G3 can be read by a controller in another way than it was meant: Mach3, paused in the middle of an arc chain and
started again, or with its IJ mode put back to *absolute* by a reset, takes the centre of the arc from somewhere else and cuts
a growing circle (a spiral) that is in no preview. With straight lines there is nothing to read another way.

With the option off, arcs are G2 / G3 as *Arcs* says, and every arc line also carries its IJ mode (`G91.1 G3 X.. Y.. I.. J..`,
`arc_mode_each`, on), so that a controller that lost the mode still puts the centre in the right place.

## The first move of a file

A job that goes to the home Z at the start of the file (a line like `G0 Z{home_z}` in the *Start of the file* block) and then
starts: the first rapid of the file now travels over at the home Z (when that is higher than the toolpath's safe Z) and goes
down to the safe Z last, instead of diving to it on the way. The option *First move: travel at the home Z, go down last*
(`start_from_home`, on by default, with *Rapid: lift first, go down last* on) switches it off.

The height it travels at is the Z the start of the file (or the first tool block) sent the tool to (`G0 Z..`, `G43 H.. Z..`,
`G53 Z..`); when they do not move Z, the home Z of Material Setup; never lower than the toolpath's own safe Z. From there the
tool goes **straight down to where the cut begins** (the plunge clearance): it does not stop at the safe Z on the way, so a
file starts with exactly what the post says and then `G0 X.. Y.. Z<travel>` and `Z<clearance>`. The option *First move: stop at
the safe Z on the way down* (`first_stop_safe_z`, off by default) puts that stop back.

## The first tool

The *First tool* block is written before the first toolpath. When it is empty the *Tool change* block is used **without its
spindle stop lines** (`M5` / `M05`): the spindle does not turn yet at the start of a file, so a file does not begin with an M5.
A *First tool* block of your own is written as it is; to write nothing there at all, turn off *Tool change block for the first
tool too* (then the spindle has to be started in the *Start of the file* block).

## Sums in the blocks (a post that follows the job)

Inside the braces of a block a sum of variables and numbers is worked out: `G0 Y{sheet_height + 50}` ends 50 mm above the
sheet (whatever its length is), `G0 X{sheet_width/2}` goes to the middle, `{(home_x + 10) * 2}`. Numbers, variables, `+ - * / ^` and
brackets are understood; the answer is written like a coordinate (the decimals of the post). The numbers are in the units of
the file, as the variables are (in an inch post write `{sheet_height + 50/25.4}`). A sum that cannot be read is refused when the
post is saved, and a name that is not a variable (or has no number, like `{X}`) is warned about.

## Groups of sheets

Sheets can be put in groups (the *Group* of the sheet's properties, or the right-click menu of the Sheets panel; the page: the
*Sheet group* field of a sheet on the Project tab). A group is nested onto together (*Place on: the sheets of the group*, `place_on:
"group"` with `group` in the engine) and a toolpath can be made for a group only (*Only on the sheets of the group* in its dialog,
`sheet_group` in the project): it is calculated on the sheets of the group, also on those put in it later.

## Saving G-code: folder, revision, names

In the Save G-code window (desktop) and on the Output tab (page): the **folder** is chosen first (and remembered), then **Save**
writes: nothing is written before. The files are named by the prefixes (variables: `{toolpath_name}`, `{safe_z}`, `{cut_depth}`,
`{tool_diameter}`, `{sheet_group}`, `{revision}` and the others above) and can go in a **folder inside** it with variables of
its own (`Rev{revision}`). The **revision** counts up by itself after every save (it can be typed; it is of the project or of
the sheet group, each group counting for itself) and is kept in the project file. The window shows what was saved last
and stays open for the next revision. What was calculated is saved as it is: the desktop asks when the drawing changed since
(save as calculated, or calculate again), the engine writes the toolpaths of the last calculation when the project is the same.

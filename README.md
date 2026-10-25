# iyiCAM

Modular 2D / 2.5D CAM for CNC routers, written in Python with a PySide6 (Qt)
user interface. It covers the 2D side of ArtCAM — vector layers, sheets,
material setup, profile / pocket / drill / engrave / V-carve toolpaths, true
shape nesting and template post-processors — and everything is extensible
with Python plugins.

> **Türkçe özet:** iyiCAM, CNC router'lar için ArtCAM'in 2D tarafına alternatif, modüler bir CAM
> yazılımıdır. Vektör katmanları, sheet'ler, malzeme/derinlik ayarı, kontür (profil), cep, delme,
> gravür, V-oyma ve kakma (inlay) takım yolları, döner eksen (A) sarma, döşeme (tiling), MDF kapak
> sihirbazı, tek çizgi (orta hat) yazı ve izleme, her biri kendi kesim yönüyle kesim sırası
> stratejileri ve akıllı başlangıç noktaları, köprüler (tab), rampa ve giriş/çıkış yayları, gerçek
> şekil nesting, NcStudio / Mach3 / Mach4 / Fanuc / LinuxCNC / Syntec postprocessor'ları, takım
> veritabanı ve kesme hızı hesaplayıcı, malzeme kaldırma simülasyonu, G-kod görüntüleme, bitmap'ten
> vektöre izleme, vektör doktoru, kırpma aracı ve Python eklenti sistemi içerir. Arayüz
> İngilizcedir. Kurulum ve kullanım aşağıdadır; eklenti yazımı için [docs/plugins.md](docs/plugins.md),
> postprocessor düzenleme için [docs/postprocessors.md](docs/postprocessors.md), kesim sırası ve
> akıllı başlangıç noktaları için [docs/cut_order.md](docs/cut_order.md) dosyalarına bakın.

## Features

**Design**
- Vectors made of lines, arcs and cubic beziers; open or closed; compound vectors (text, groups)
- Vector layers (colour, show/hide, lock, active layer) and multiple **sheets** per project
- Drawing tools: polyline / bezier (click or drag), line, rectangle, circle, ellipse, polygon,
  star, 3-point arc, text (any installed font), measure, **trim** (cut away the piece of a vector
  between crossings)
- Rulers and draggable guide lines; snapping to nodes, midpoints, arc centres, sheet corners,
  guides and the grid
- Selection with move / scale / rotate handles, window and crossing selection, nudging
- **Node editing**: drag nodes and bezier handles, insert / delete nodes, convert spans
  (line / arc / bezier), set the start point, break, close, reverse
- Vector commands: offset (round / sharp), weld, subtract, intersect, join with tolerance,
  close, group / ungroup / explode, fillet, chamfer, **dog-bone / T-bone** corners, smooth
  (fit arcs), simplify, convert to polyline, delete duplicates, move / position / rotate /
  scale / set size / mirror, align, distribute, rectangular and circular arrays, **copy along a
  vector**, **text on a curve**, bounding box, select open / similar / invert
- **Vector Doctor**: finds open, nearly closed, tiny, duplicate and self-crossing vectors and fixes
  what it can
- **Door / panel wizard**: cabinet (MDF) doors with stacked square / rounded / cove /
  chamfered / arched panels, decorative groove lines and, when tools are chosen, the
  pocket, panel edge, groove and cut-out toolpaths
- **Bitmap tracing**: turn logos and scanned drawings (PNG, JPG, BMP, ...) into outline vectors
  or into **centre lines**
- **Centre line (single line)**: single strokes along the middle of closed shapes, e.g. any
  installed font turned into single line text for fast engraving
- DXF import (lines, arcs, circles, polylines with bulges, splines, ellipses, blocks, OCS,
  units, gap joining) and export; SVG import (transforms, units, Inkscape layers) and export;
  HPGL (`.plt`) import from cutting plotters

**Machining**
- Material setup: thickness, Z zero top / bottom, XY origin (9 positions), safe Z,
  rapid-down clearance, home position, per-sheet thickness override
- Tool database: end mills, ball nose, bull nose, V-bits, engraving tools, drills with
  feeds, speeds, stepdown / stepover, tool numbers, coolant; import / export; chip load based
  **feeds & speeds calculator** with material presets
- Toolpaths select vectors explicitly **or by layer** (layer based toolpaths work on every
  sheet — ideal together with nesting), can be saved and loaded as templates
- **Profile**: outside / inside / on, climb / conventional, allowance, sharp corners,
  finish pass, flat or ramped (3D) **tabs** placed automatically or **clicked on the drawing**,
  linear or spiral **ramping**, arc / line **lead in/out**, overlap, holes before outlines, user
  controlled start points
- **Pocket**: offset or raster strategy, islands, wall allowance and finish pass,
  automatic clean-up of missed material, plunge / zig-zag ramp / helix entry, **rest machining**
  after a larger tool
- **Drilling**: centres, circles only or circles matching the drill, peck, dwell,
  drill point compensation; canned cycles (G81/G83) when the post supports them
- **Engrave**: follow vectors at a depth or at a depth derived from a V-bit line width
- **V-carve**: exact inscribed-circle carving, flat depth with automatic flat area clearing
- **Inlay**: V-carve inlay pocket and the matching mirrored plug with a glue gap
  (fit checked by simulating both halves)
- **Fluting**: follow vectors with linear or S-curve depth ramps at both ends
- **Rotary axis wrapping**: draw on the unrolled cylinder, get A-axis G-code with inverse time
  (G93) or corrected feed rates
- **Cut order**: shortest travel, rows / columns / diagonals from any corner, zig-zag sweeps,
  smallest parts first, centre out or edges in; automatic or frozen, picked on the drawing from any
  position; every strategy with its own cutting direction (climb, conventional, clockwise,
  counter-clockwise or mirrored with the sweep). Inside-first and loose-scrap rules guarantee
  nothing is cut once it is free ([docs/cut_order.md](docs/cut_order.md))
- **Smart start points**: a cut closes where it starts, so the start is put where the part stays
  held best to the last moment (ahead of the sweep, away from neighbours already cut), with the well
  held sides cut last, for the actual cutting direction; long narrow parts and letters close just
  above the bottom right corner (mirrored for other sweeps); never on a corner, never plunging into a
  cut; a new node on the vector; hand-set starts are kept; moving one recalculates only that cut and
  the ones after it
- **Tiling**: machine sheets longer than the machine in bands, one G-code file per tile
- Time estimates, warnings, height-map **material removal simulation**, HTML job report
- **G-code backplot**: open any G-code file, see it over the sheet and simulate it

**Nesting** – true shape nesting with no-fit polygons: rotations (none / 180 / 90 / 45 /
15 degree steps), part spacing, border gap, quantities, several sheets, holes and
engravings travel with their part (a part drawn inside another part's hole is recognised as
a separate part), randomised optimisation.

**Post-processors** – template based, defined in TOML, with inheritance (`extends`).
Included: NcStudio (Weihong, single spindle — one file per tool), NcStudio ATC, Mach3,
Mach4, Fanuc / ISO, LinuxCNC, Syntec. Options cover units, number formats, IJK or R arcs,
arc splitting, helical arcs, modal suppression, line numbers, comments, canned cycles,
CRLF line endings and more. A built-in editor creates custom posts with a live preview.

**Extensibility** – plugins add toolpath strategies, vector commands, post-processors,
importers, exporters, G-code filters, event hooks and GUI extensions. The built-in
features are registered through the very same API. A Python console and a command line
interface allow scripting and batch work.

## Installation

Python 3.11 or newer is required.

```bash
git clone https://github.com/ac2f/iyicam.git
cd iyicam
pip install -e ".[gui]"      # core + PySide6 user interface
iyicam                      # or: python -m iyicam
```

On Linux the Qt platform libraries may need to be installed (for example
`libegl1 libxkbcommon0 libfontconfig1` on Debian / Ubuntu).

## Quick start

1. **File ▸ New** — set the sheet size and material thickness.
2. Draw with the tools on the left or **File ▸ Import Vectors** (DXF / SVG).
3. **Toolpaths ▸ Material Setup** — Z zero, XY origin, safe Z.
4. Select vectors and create a toolpath (**Toolpaths ▸ New Toolpath** or the *New* button in
   the Toolpaths panel). Pick the tool, set depths, tabs, ramps...
5. **Simulate** (F6) and check the warnings in the Toolpaths panel.
6. **Save G-code** (Ctrl+Shift+E), choose the post-processor for your controller.

For production work put the vectors on layers (e.g. *Cut*, *Pocket*, *Drill*), create
layer based toolpaths and use **Nesting ▸ Nest Parts** — the toolpaths are calculated on
every sheet the nesting creates.

Useful keys: `S` select, `N` nodes, `P` polyline, `L` line, `R` rectangle, `C` circle,
`E` ellipse, `G` polygon, `A` arc, `T` text, `M` measure, `X` trim, `F` zoom extents, wheel to zoom,
middle button or Space+drag to pan, `Esc` back to selection, `F5` recalculate, `F6` simulate,
`F7`/`F8` grid / vector snapping. **Help ▸ Keyboard Shortcuts** lists them all.

## Command line

```bash
python -m iyicam posts                                    # list post-processors
python -m iyicam import drawing.dxf -o job.icam --thickness 18
python -m iyicam nest job.icam -o nested.icam --width 2800 --height 2100 --spacing 8
python -m iyicam info nested.icam                         # sheets, times, warnings
python -m iyicam gcode nested.icam --post ncstudio --sheet all -o out/job.nc
```

## Files and folders

| What | Where |
|------|-------|
| Projects | `*.icam` (JSON) |
| Toolpath templates | `*.icamtpl` |
| Tool library | `~/.iyicam/tools.json` |
| User post-processors | `~/.iyicam/posts/*.toml` |
| Plugins | `~/.iyicam/plugins/` (`*.py` or packages) |
| Settings | `~/.iyicam/settings.json` |

Set the `IYICAM_HOME` environment variable to use another folder.

## Architecture

```
iyicam/
  core/        geometry kernel (geom, clip, arcfit), shapes, vector operations, document
               model, parameters, commands, registry, tool library, undo
  cam/         moves, toolpath strategies (profile, pocket, drill, engrave, vcarve, inlay),
               cut order (sequence) and smart start points,
               ordering, job calculation, simulation
  post/        post-processor engine, TOML loader, built-in posts, G-code export
  nesting/     no-fit polygon nesting and part grouping
  formats/     DXF and SVG import / export
  plugins/     plugin API and manager
  builtins/    built-in features, registered like a plugin
  gui/         PySide6 user interface (controller, canvas, tools, panels, dialogs)
  cli.py       command line interface
examples/plugins/   example plugins (surfacing toolpath, G-code statistics, box maker)
docs/               plugin and post-processor documentation
tests/              pytest suite (geometry, toolpaths, posts, nesting, formats, plugins, GUI)
```

Internally everything is millimetres with Y up and Z = 0 at the material top; posts apply
the origin, Z zero and unit conversions when writing G-code.

## Development

```bash
pip install -e ".[dev]"
QT_QPA_PLATFORM=offscreen pytest
```

`tests/test_gcode_integrity.py` posts jobs with many combinations of settings through
the real post-processors and checks the G-code text itself against the drawing:
pass depths and step (against a model of the material left), final depth, rapids,
feed rates, tool clearance, cutting direction, cut-out, tool changes and the
program frame. It also damages good G-code to prove each check notices.

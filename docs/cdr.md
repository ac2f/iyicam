# CorelDRAW (`.cdr`) files

iyiCAM opens `.cdr` drawings: **File > Import**, drag and drop onto the window, `python -m iyicam import logo.cdr -o job.icam`, and
the mobile page of CNC-Assistant. The size is kept exactly (millimetres, to the micron): a drawing of 332.4 x 401.2 mm in
CorelDRAW is 332.4 x 401.2 mm in the project.

## How it works

CorelDRAW's file format is closed and changes with every version. iyiCAM does not read it itself: it asks a program that does
to write an SVG, and reads that SVG with its own importer (exact units, arcs, curves). The programs are tried in this order;
the first one that gives a drawing wins:

| program | text | notes |
|---|---|---|
| **Inkscape** 1.x | **becomes curves** | the best choice for CNC work: the letters are cut as outlines |
| `cdr2xhtml` of the libcdr tools (`apt install libcdr-tools`) | left out, reported | the fastest; needs no program with windows |
| **LibreOffice** | left out, reported | its double outlines (fill and stroke) are cleaned away |
| your own, `IYICAM_CDR_CONVERTER` | | see below |

Nothing has to be installed for the rest of iyiCAM; only `.cdr` files need one of these. Without any, the import says so and
what to install. On the computer that runs the CNC-Assistant agent the program has to be installed **on that computer**
(the page only shows the result).

What CorelDRAW versions are read is up to the converters (libcdr: CorelDRAW 7 to X8). A file saved by a newer CorelDRAW may be
refused: in CorelDRAW use *File > Save As* and pick **X8** (or older), or export the drawing as **DXF** or **SVG**.

## What comes in, and what does not

Outlines, curves, arcs, rectangles, ellipses and (with Inkscape) text as curves. **Not imported, and said so after the
import:** bitmaps; text, unless Inkscape reads the file (in CorelDRAW: select all, *Arrange > Convert To Curves*, Ctrl+Q,
and save again); and whatever a converter cannot write as outlines (which effects that is depends on the program and
the file: the number of vectors a program brings is not the same, so look at the result). Fills are not cut: what is
imported is the outline of every shape.

CorelDRAW "layers" are not carried over: everything goes on the current layer.

## Options

| | |
|---|---|
| Scale | 100 % keeps the size of the drawing |
| Turn text into curves | only when Inkscape is there (the question is not asked without it) |
| Centre on the sheet | as for the other formats |

In code: `import_cdr(path, project, sheet, layer_id=None, scale=1.0, text_to_curves=True, converter=None, timeout=None,
keep_layers=False, cancel=None, report=None)` in `iyicam/formats/cdr.py`; `converter` picks one of `inkscape`, `cdr2xhtml`,
`libreoffice`, `custom`. Errors are `CdrError` with a `code` (`cdr_no_converter`, `cdr_not_cdr`, `cdr_archive`, `cdr_version`,
`cdr_failed`, `cdr_timeout`, `cdr_cancelled`, ...) and words for the person; the service passes the code through.

## Settings

| environment variable | |
|---|---|
| `IYICAM_CDR_CONVERTER` | a command of your own, tried first. `{input}` and `{output}` stand for the files: `mytool --svg {input} {output}`. Without `{output}` the SVG is read from the program's standard output |
| `IYICAM_CDR_TIMEOUT` | seconds the whole conversion may take (default 60, at most 600). The relay of the mobile page waits 90 s |

## Notes

* A `.cdr` taken from WhatsApp is often called `name.cdr.zip` although it is a CorelDRAW file, not an archive. Rename it
  (the `filename` tray program, ac2f/filename, does it) or iyiCAM will say that it is a ZIP file. A real archive that
  holds a `.cdr` is recognised, and the name of the file inside is given.
* The file is copied to a temporary folder under a plain name for the converter and removed afterwards. Inkscape is started
  with its own settings folder, so the one of the person is not touched. A converter that hangs is stopped with everything
  it started when the time is up or Cancel is pressed (the window of the program stays alive while it reads).
* A caller of the service cannot name a program: `converter` only chooses among the ones found on the computer.
* Tests: `tests/test_cdr.py` (converters of its own, errors, time limit, Cancel, no child left behind, the service, the
  command line). With `IYICAM_CDR_SAMPLE=/path/to/drawing.cdr` it also reads a real drawing (332.4 x 401.2 mm in the sample
  it was made with) through every converter that is installed.

# CorelDRAW (`.cdr`) files

iyiCAM opens `.cdr` drawings **by itself: nothing has to be installed** (no Inkscape, no LibreOffice, no CorelDRAW).
**File > Import**, drag and drop onto the window, `python -m iyicam import logo.cdr -o job.icam`, and the mobile page of
CNC-Assistant all use it. The size is kept exactly (to the micron): a drawing of 332.4 x 401.2 mm in CorelDRAW is
332.4 x 401.2 mm in the project.

## What comes in, and what does not

Outlines of every kind: curves, lines, rectangles (rounded corners too), ellipses, arcs, pies, polygons, splines, with all
their transforms (rotation, mirror, skew, scale). Circles stay circles (arcs) so that holes can be drilled.

**Not imported, and said so after the import:**

* **Text.** In a CorelDRAW file a text object holds the letters and the name of a font, not outlines: they exist only
  where the font is installed, and the layout of a paragraph is CorelDRAW's. The import counts the text objects it left out
  and says what to do: in CorelDRAW select everything, *Arrange > Convert To Curves* (Ctrl+Q), save again. A drawing that
  is *only* text is refused with the same words.
* Bitmaps (counted and said).
* Fills, colours and line styles: a cutting job needs the outlines; the layers are not carried over, everything goes on the
  current layer.
* Of a drawing with several pages the **first page** is read (the report says how many there are). What is on the
  *master page* is read with it, as libcdr does (the report says how many shapes that are).

## How it works, and how far to trust it

`iyicam/formats/cdr_native.py` is a Python port of the geometry part of
[libcdr](https://wiki.documentfoundation.org/DLP/Libraries/libcdr) (Document Liberation Project, MPL 2.0), the library
behind the CorelDRAW import of LibreOffice and many other programs: the container (RIFF, or the ZIP package of
CorelDRAW X4 and later, with the extra streams of X6 and later), the compressed blocks, the objects and their transforms
and the pages. It reads **CorelDRAW 5 to X8**, and **tries newer ones** (2017 ... 2026), see
[the next section](#corel-draw-2017-and-newer-up-to-2026).

What it was compared with, because a drawing that is a little wrong cuts a wrong part:

* **A real drawing saved by CorelDRAW X3** (218 shapes, 332 x 401 mm): the outlines are the same as libcdr makes of it, shape
  by shape, within 3 micrometres. (It is the only real file at hand: the sample is not in the repository.)
* **Files written by `tests/cdr_builder.py`** for every version from 5 to X8, that libcdr reads too: every object kind and
  every transform; master pages, groups, pattern lists, compressed lists, the package, the extra streams. The test
  `tests/test_cdr_native.py` has libcdr's own `cdr2xhtml` read the very same files and compares shape by shape (it is
  skipped where the libcdr tools are not installed).
* 1500 damaged files (bytes changed, cut off, inserted): always a drawing or a refusal, never a crash or a hang; a polygon or
  a compressed block that claims an absurd size is refused at once.

So the built-in reader agrees with libcdr everywhere it was tried; only X3 was tried on a **real** file. For a file of
another version (up to X8) the import adds a note: *"read by the built-in reader, which has been compared with files of CorelDRAW X3
only: check the size of the result against the drawing in CorelDRAW before cutting"*. (Where `cdr2xhtml` or LibreOffice is on
the computer, those are tried first for such a file, and the built-in reader last.)

## CorelDRAW 2017 and newer (up to 2026)

**Read this before cutting a part from such a file.** The format of CorelDRAW is not published, and libcdr (and so
LibreOffice, Inkscape and every program that reads CorelDRAW files) has rules for it only up to **X8**. No drawing saved by
a newer CorelDRAW was at hand when this was written, so nobody has checked what iyiCAM does with one.

What iyiCAM does with a file of CorelDRAW 2017 or newer (version 19 and up, 2026 is 28):

* It is **tried** instead of refused: the file is read with the rules of X8, as libcdr does (libcdr has no upper limit
  either), in every kind of package (`content/riffData.cdr`, `content/root.dat` with the extra streams, or a stream of
  another name in the `content` folder). The import says **in its notes that the file is newer than X8, that the result has
  never been compared with a drawing of this version, and that its size and outlines must be checked in CorelDRAW before
  cutting.**
* **A part of a drawing is never left out silently:** for these files a compressed block that cannot be inflated, a chunk that
  points to a stream that is not there and a header that is not the one libcdr knows stop the reading (the older versions
  keep their way). The answer is then the refusal below, not a drawing with something missing.
* If it cannot be followed you get the advice to save the drawing as X8 (*File > Save As*) or to export DXF / SVG, and
  the words to ask for support: **`iyicam cdr-info drawing.cdr`** says what the file is made of (the members of the
  package and how they are compressed, the version, how many records of which kind, **where the reader stops**) and holds
  **nothing of the drawing** (no coordinates, names or text), so it can be sent. `--json` gives it as JSON.
* A package of a layout this program does not know (a `.cdr` with a `content` folder and no drawing stream it recognises) is
  said to be that, with the names of its members; a member compressed in a way Python cannot read (zstd, deflate64) is named.

What was **tested**: files of every kind that `tests/cdr_builder.py` writes, with the version numbers of 2017 to 2026 (and a
file damaged in a compressed block), in every package; the answers are the same as for X8, the notes are there, nothing is
left out silently. What **cannot be tested** without a real file: whether CorelDRAW 2026 writes its objects the way X8 does.
If you have a drawing saved by CorelDRAW 2017 or newer, `iyicam cdr-info` on it, and the same drawing exported as DXF or SVG
from CorelDRAW, is what it takes to check the reader against it (the sample is used by the tests when `IYICAM_CDR_SAMPLE`
names it).

## Other readers (optional)

They are only used when they are on the computer, for a file the built-in reader is not the first choice for or cannot
follow:

| reader | |
|---|---|
| `IYICAM_CDR_CONVERTER` | a command of your own, tried first. `{input}` and `{output}` stand for the files: `mytool --svg {input} {output}`. Without `{output}` the SVG is read from the program's standard output |
| `cdr2xhtml` of the libcdr tools (`apt install libcdr-tools`) | libcdr itself |
| LibreOffice | its double outlines (fill and stroke) are cleaned away |

`IYICAM_CDR_TIMEOUT` is how many seconds those programs may take (default 60, at most 600; the relay of the mobile page
waits 90). They run in a temporary folder, are stopped with everything they started when the time is up or Cancel is
pressed, and the one of the person is never touched. A caller of the service cannot name a program: `converter` only
chooses among the readers of the computer (`builtin`, `cdr2xhtml`, `libreoffice`, `custom`).

## In code

`import_cdr(path, project, sheet, layer_id=None, scale=1.0, converter=None, timeout=None, cancel=None, report=None)` in
`iyicam/formats/cdr.py`: `scale` 1.0 keeps the size, the vectors go on `layer_id`. Errors are `CdrError` with a `code`
(`cdr_not_cdr`, `cdr_archive`, `cdr_version`, `cdr_text_only`, `cdr_failed`, `cdr_no_converter`, `cdr_timeout`, `cdr_cancelled`,
...) and words for the person; the service passes the code through. `report.via` says how the file was read
(`CorelDRAW X3 (builtin)`). The reader itself: `cdr_native.read(path)` gives pages of shapes in millimetres, y up.

## Notes

* A `.cdr` taken from WhatsApp is often called `name.cdr.zip` although it is a CorelDRAW file, not an archive. Rename it (the
  `filename` tray program, ac2f/filename, does it) or iyiCAM will say that it is a ZIP file. A real archive that holds a
  `.cdr` is recognised, and the name of the file inside is given.
* In the window the file is read away from the drawing, in a thread with Cancel, and put in as one undo step.
* `cdr_native.py` is derived from libcdr and is under the Mozilla Public License 2.0 (its header says so); the rest of iyiCAM
  is not affected.

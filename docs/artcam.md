# ArtCAM files

iyiCAM opens ArtCAM **models (`.art`)** and **projects (`.3dp`)** of ArtCAM 2008 to 2018: File ▸ Open (a project of
its own, in a new tab) or File ▸ Import (its sheets are added to the project that is open), on the desktop and in the
web app alike. Nothing has to be installed and ArtCAM need not be on the computer.

## What comes in

| ArtCAM | iyiCAM |
|---|---|
| the model's size (e.g. 1510 × 3010) | a sheet of that size for each toolpath; toolpaths that cut the same vectors share one sheet. The sheet is named after its toolpaths (`k88881`) |
| a `.3dp` with several models | the sheets of every model, named `model: toolpath` |
| 2D profile (outside / inside / on) | a Profile toolpath on that sheet only, named as in ArtCAM |
| tool | its name, type (end mill, ball nose, V-bit ...), diameter, number, stepdown, stepover; the feed and plunge rates (ArtCAM keeps them in mm/sec: 80 → 4800 mm/min), spindle speed |
| start depth, finish depth, stepdown | start depth and cut depth (not "through"); the passes come out where ArtCAM's were (0.973 → 11.265 in steps of at most 8: −6.119, −11.265) |
| allowance, climb / conventional, sharp corners, ramps, leads, bridges | the same settings of the profile |
| safe Z, home | the toolpath's safe Z; the home position of the project (Z zero on the top) |

## The vectors

ArtCAM keeps the vectors of a model **encrypted**; they cannot be read. What every toolpath cut is in the file in the
clear, though: the line the centre of the tool ran along. For a profile that line is the vector offset by the tool
radius and the allowance (outside: 2 − 0.591 = 1.409 mm out), so iyiCAM offsets it back and has the vector again. A
profile iyiCAM calculates on it runs on ArtCAM's line (checked on real files: half the points within 5 µm, all within
0.1 mm, and the same number of cuts); the vector is the drawing wherever the tool could reach (an inside corner sharper
than the tool comes out rounded with the radius the tool cut it with).

So:

* vectors that **no toolpath uses** are not brought in (it is said);
* a toolpath of **another kind** (area clearance, V-carve, 3D) is not converted: the line it ran along comes in as
  vectors on the layer *ArtCAM*, and that is said;
* **reliefs** (3D) are not read;
* ArtCAM keeps **no thickness of the material** with the toolpaths: each sheet is as thick as its deepest cut. The depths
  are measured from the top as in ArtCAM, so the G-code is the same; change the thickness on the sheet when it matters
  (Z zero at the bottom, simulation).

What could not be read is listed after the file is opened.

## The file, for those who want to know

Both are Microsoft compound files (`formats/cfb.py`). A `.3dp` holds its models under `Project/Models/<id>/`, each the
same as a `.art`. In a model (`formats/artcam.py`):

* `ArtCAM/Models/Model1/Parameters`: the size (two doubles at byte 9);
* `ArtCAM/ToolpathData/#UnicodeToolpathList`: the toolpaths, in order;
* `ArtCAM/ToolpathData/<id>/Parameters`: name, tool, feeds and heights;
* `ArtCAM/ToolpathData/<id>/Data2`: the kind of the line, the Z of every pass and the contours (lines and arcs, an
  arc by its bulge); ArtCAM 2018 writes a shorter header for every contour than 2008;
* `ArtCAM/OA_ToolpathData/OA_TOOLPATH_MANAGER`: an MFC archive with the start depth, the tool and a list of named
  settings (`ProfileOutside`, `ClimbMill`, `MaximumDepth`, `StartDepth` ...: the names first, then the values, the last
  one's first);
* `ArtCAM/Models/Model1/VectorDocument`: the vectors, encrypted.

The tests make ArtCAM files of both versions byte for byte (`tests/artcam_builder.py`); with
`IYICAM_ARTCAM_SAMPLES=<folder>` they also open every real `.art` / `.3dp` in that folder and calculate its toolpaths.

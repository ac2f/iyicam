# Nesting on leftovers and plates of any shape

Nesting places the parts of a job on plates so that little material is wasted. A
plate is not only a new rectangular sheet: it can be a **leftover** of any shape
(an L, a plate with a bite or a hole, a trapezoid), the **free area** of a sheet
that already carries parts, or a new sheet, in any number of sizes and quantities.

## Plates

| Plate | What it is | Where it comes from |
|---|---|---|
| New sheet | a rectangle of the *New sheet width / height*, as many as needed | the nesting dialog |
| Stock plate | a rectangle or **any shape**, with a quantity (0 = unlimited) | **Nesting ▸ Stock of Plates...** |
| Free area of a sheet | the sheet's material around the vectors that stay on it | *Place on* in the nesting dialog |

Plates are filled one after the other, **leftovers first**, smallest free area
first, then the other stock plates, then new sheets. Each plate takes the biggest
parts that still fit before the next plate is started, so a small leftover is
filled with what suits it and new sheets are only opened when the leftovers are
full. Parts that fit on no plate are reported and stay where they are.

## The stock

**Nesting ▸ Stock of Plates...** keeps the plates of the project (saved with it):

* **Add plate...** a rectangle: name, size, quantity, leftover or not.
* **From selected vectors** draw the outline of a leftover (and the holes or
  damaged places in it as closed vectors inside) and select them: each outline
  becomes a plate of that shape. This is also how a scanned or measured leftover
  is entered.
* **Leftover of sheet...** after a sheet is cut, the material left on it becomes
  plates for the next job: choose the width of the cut round the parts, the narrowest
  strip and the smallest piece worth keeping. Several pieces become several plates.
  (Also available directly as **Nesting ▸ Add Leftover of Current Sheet to Stock...**
  and **Add Selected Vectors to Stock**.)
* **Load... / Save...** stock files (JSON) are shared between projects and shops.

Per plate: *quantity* (0 = as many as needed), *leftover* (used before new sheets)
and an own *thickness* (the sheet gets it, and so its toolpath depth and G-code).

## Nesting

**Nesting ▸ Nest Parts...** (Ctrl+Shift+N). Select the vectors to nest (or none for
everything on the sheet) and set the quantities of the parts. Besides the usual
settings (border gap, gap between parts, rotation, gravity, attempts, tolerance):

* **Place on** the current sheet first, all sheets first, or the stock and new
  sheets only. A sheet that already carries other vectors is used around them: the
  vectors that stay are obstacles that keep the gap between parts. A blank current
  sheet takes the *new sheet* size, as before.
* **Plates of the stock** tick the plates to use and change their quantities for
  this run.
* **Start new sheets when needed** off: only the leftovers and stock plates are
  used, whatever does not fit is reported.
* **Take the plates used out of the stock** counts the quantity of used plates down;
  a used up leftover leaves the stock. Undo restores it together with the sheets.

Every plate used becomes a **sheet** of its own size, outline and thickness, named
after the plate (*Leftover 1*, *Offcut 2*, ...). The border gap is kept to the edge of
the plate, to its holes and cut-outs; the gap between parts is kept between parts and
to what is fixed on a plate.

## Speed, progress and cancelling

Nesting runs in the background: the program stays alive, a progress window counts the
parts placed, and **Cancel** works at once (within a fraction of a second) and changes
nothing. What keeps it fast:

* **Outline points per part** (default 40, 0 = no limit): a part whose outline has more
  points is simplified further before it is nested. Outlines from DXF files, text or traced
  curves can have hundreds of points, and the work grows with the square of that (25 organic
  parts: about 190 seconds without a limit, 2 seconds with 40 points, the same number of
  sheets and the same usage). The price is that such parts end up up to a millimetre or two
  further from their neighbours. Plain parts (rectangles, rounded corners, L shapes) are not
  touched. *Outline tolerance* is the least that is simplified.
* Parts with the same outline (copies drawn separately, anywhere on the sheet) share their
  work; outlines that look the same when turned (a rectangle at 0° and 180°, a square at every
  quarter turn) are tried once.
* Only the neighbours that really get in the way of a part are looked at in detail.

Copies lying on top of each other are looked at once: identical outlines (a doubled drawing) count as one,
and a pile of more than about a hundred different shapes on one spot is refused with a message ("shapes lie on
top of each other"), because putting such a pile together takes minutes and cannot be interrupted.

If nesting is still slow: fewer angles (90° steps instead of 15°), fewer *Optimisation
attempts*, fewer outline points. On the command line: `--max-points N`.

## Using the least material

Nesting places the parts one by one at the best place of the feasible area. What "best" is and in which
order the parts come decides how much of the sheet is used, so several layouts are made and the best one
is kept: **Optimisation attempts** (default 8; 1 is one quick pass).

* The first attempt puts the biggest parts first towards the side chosen in *Pack towards*. The following ones
  try the other directions (left, bottom, smallest area, shortest layout along X or Y) and other orders of
  the parts (by area, longest side, height, width). After those the best order is shuffled a little.
* A layout is better when it places more parts, then uses fewer (cheaper) sheets, then uses less of the last
  sheet (the rectangle round the parts on it), then reaches less far.
* The best layout is polished: the parts that set its extent are taken out and put back where they fit best,
  while the others stay; a move is kept when the rectangle round everything gets smaller.
* The work is shared between the attempts (the no-fit polygons are made once), so ten attempts cost a lot
  less than ten times one. The result is the same every time for the same settings.

**Time limit** (seconds, 0 = none; `--time-limit` on the command line): the search stops when the time is
up and keeps the best layout found so far, which is never worse than the first pass. The first pass is
always finished.

On a 2440 x 1220 sheet with 22 mixed parts (rounded rectangles, L, T and U shapes, circles, arrows) one pass
used 89 % of the sheet, the search 81-84 %; 24 rectangles of many sizes needed 2 sheets in one pass and 1 with
the search.

## Inner pieces (göbek) and holes

* **Inner pieces stay with their part** (*Parts* in the dialog, `--group-inner`): whatever lies inside a part,
  the pieces in its holes too, moves with it. Off (the default): a piece in a hole is a part of its own and is
  nested separately, as before.
* **Place smaller parts in the holes of parts** (`--fill-holes`): the holes of a part (closed vectors inside
  it that hold nothing that stays, and holes inside the part's own vector, such as the counter of a letter) can
  take other parts. A part nests in a hole, around a part, in a hole of a hole... the parts keep the gap to
  the edge of the hole. Do not use it when the inner closed vectors are only drawn (engraved) and not cut
  through: their material is not removed. The cut order cuts a part nested in a hole before the hole.

## Rotation

How parts may turn is set in three steps, the most specific wins:

1. **Rotation** in the nesting dialog, for every part: *No rotation*, *0 / 180°*,
   90, 45 or 15° steps, **Steps of my own...** (any step in degrees, e.g. 22.5) or
   **List of angles...** (any angles, e.g. `0, 22.5, 90`).
2. **Rotation by layer**: every layer of the parts being nested has its own rule.
3. **Rotation of a part**: *Like its layer* (the default) or its own rule. Select
   several rows of the parts table and use **Apply** to set them together (the
   selected vectors, or everything on a layer).

The rules:

| Rule | Angles the part may take |
|---|---|
| As set above | the angles of the Rotation setting |
| Never rotate | 0° only (for materials with a grain, patterns, ...) |
| 0 / 180° only | 0° and 180° |
| Tilt up to ±X° | -X° ... +X° in steps of *Step of limited rotation* |
| Tilt up to ±X° or flipped 180° | the same and turned by 180° |
| Steps of X° | every X° round the circle |

The rules are saved with the project and stay when a nesting is undone. A part that
may not rotate (or only a little) is never tilted by squeezing either.

## Squeezing parts in

Sometimes a part does not fit although it would with a little give: tilted a few
degrees off its angle, or a few millimetres closer to the plate's edge than the
border gap. **Squeeze parts in** (off by default) looks for exactly that:

* **Tilt by up to** X° (in steps of *Tilt step*; 0 = never tilt) and
  **Closer to the edge by up to** Y mm (what a part may take from the border gap;
  0 = never).
* **Suggest it, I decide**: the layout is made as usual; when squeezing parts in
  would place parts that do not fit or save a plate, a window lists what would be
  done ("Part 3 x2: tilted 6°", "Part 5: 1 mm closer to the edge") and you choose
  **Squeeze them in** or **Keep it as it is**. **Squeeze in automatically** takes the
  better layout without asking. Nothing is offered when it would not help.
* The gentlest squeeze is used for every part: the smallest tilt first, then
  coming closer to the edge in four steps, then both. Parts that fit as they are
  stay as they are. The result lists every squeezed part and by how much.

## Sheets of any shape

A sheet made from a leftover has an **outline** (material only inside it), shown on
the drawing with the area outside shaded, and listed with *(shape)* in the sheets
panel. The outline can also be set by hand: draw the plate as closed vectors
(holes inside it), select them and use **Nesting ▸ Set Sheet Outline from Selected
Vectors** (the sheet is moved so the outline starts at X0 Y0, with everything on it);
**Clear Sheet Outline** (or the sheet properties) makes it a rectangle again.

The rest of the program knows the plate is not a rectangle:

* **Cut order.** The edge of the plate holds like a sheet's edge; beyond it there
  is nothing, so a piece of scrap enclosed by cuts and an outline's cut-out is
  recognised as loose, and a cut next to the cut-out is not.
* **Smart start points** do not count on material beyond the outline.
* **Warnings** report a toolpath that cuts outside the outline.
* **Simulation** leaves no material outside the outline.
* **G-code** takes its origin from the bounding box of the plate, like any sheet.

## Command line

```
python -m iyicam nest job.icam -o nested.icam --leftover 900x600:2 --leftover 500x500
python -m iyicam nest job.icam -o nested.icam --stock leftovers.json --no-new-sheets
```

`--leftover WxH[:N]` adds rectangular leftovers (N of them), `--stock FILE` a stock
file, `--no-project-stock` ignores the stock saved in the project, `--no-new-sheets`
uses only those plates, `--keep-stock` keeps the used plates in the project's stock.

```
python -m iyicam nest job.icam -o nested.icam --iterations 20 --gravity reach_x --fill-holes
python -m iyicam nest job.icam -o nested.icam --angles 0,22.5,90 --rotate-layer Grain=none
python -m iyicam nest job.icam -o nested.icam --squeeze-angle 5 --squeeze-margin 3 --apply-squeeze
```

`--time-limit SECONDS` stops the search for a better layout after that long, `--group-inner` keeps
inner pieces with their outer part (see above), `--fill-holes` puts small parts into the holes of big ones.

`--angles LIST` any angles instead of `--rotation` (the step), `--rotate-layer
LAYER=RULE` (`none`, `flip`, `limit:X`, `limit-flip:X`, `step:X`, `default`; repeat
for more layers), `--tilt-step` the step of `limit:X`. `--squeeze-angle`,
`--squeeze-margin` and `--squeeze-step` set the limits of squeezing; a better layout is
printed as a suggestion, `--apply-squeeze` takes it.

## Stock file

```json
{"format": "iyicam-stock", "version": 1,
 "items": [{"name": "Offcut", "width": 700, "height": 300, "outline": null,
            "quantity": 2, "leftover": true, "thickness": null},
           {"name": "L leftover", "width": 500, "height": 400,
            "outline": [[[0, 0], [500, 0], [500, 200], [250, 200], [250, 400], [0, 400]]],
            "quantity": 1, "leftover": true, "thickness": 12.0}]}
```

`outline` lists polygons in the plate's own coordinates (origin at the lower left of
its bounding box); several polygons are outer boundaries and holes (even-odd).

## How it works

Parts are inflated by half the gap and placed one by one on a plate at the best vertex
of the feasible area: the area where the part lies inside the plate, outside the
no-fit polygons of the parts already placed. For a plate of any shape the part must
not touch the plate's boundary and must have its reference point in the material:
the boundary of the plate swept by the part is taken away from the plate shifted by
that reference point. The feasible area of such a plate has zero width slivers where a part
only touches the boundary with that point (lying outside the plate or in a hole), so the
position chosen is checked against the plate once more and the next best one is taken when it
fails. Plates, shapes and rotations are cached, so several plates of one kind cost no more than
one.

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
python -m iyicam nest job.icam -o nested.icam --angles 0,22.5,90 --rotate-layer Grain=none
python -m iyicam nest job.icam -o nested.icam --squeeze-angle 5 --squeeze-margin 3 --apply-squeeze
```

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

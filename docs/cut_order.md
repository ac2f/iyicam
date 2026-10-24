# Cut order and start points

Profile and engraving toolpaths cut vector by vector. Two independent systems
decide **in which order** the vectors are cut and **where** each closed cut
starts. Each one can work automatically or be run once on demand.

| | Cut order (G-code sequence) | Start points (vector editing) |
|---|---|---|
| Menu | **Toolpaths ▸ Cut Order...** (Ctrl+Shift+O) | **Vectors ▸ Start Points ▸ Smart Start Points...** |
| Automatic | *Re-order automatically on every calculation* | *Move the vectors' start points automatically* |
| Once | **Re-order now** | **Optimise now** (undoable) |
| By hand | **Pick order on the drawing** from any position, or move a row up / down | **Set Start Point** tool (**B**): click anywhere on a vector |

Both are also in the context menu of the toolpath list. **View ▸ Show Cut
Order** numbers the cuts on the drawing at the point where the tool goes down.

## Rules that always hold

Whatever strategy or manual order is chosen:

1. **Inside first.** Everything inside a closed vector is cut before that
   vector: holes before their outline, a part nested in a hole before the hole,
   engravings before the part around them. A manual order that breaks this rule
   is corrected and the toolpath shows a warning.
2. **No cut in loose scrap.** When parts are so close that their cuts overlap,
   several cuts can together close a ring of scrap. All cut areas are removed
   from the sheet at once; any piece of material that no longer reaches the
   sheet frame is an *island*. Whatever lies in an island (a small part nested
   between bigger ones) is cut before the last cut around it.
3. **Parts closer than the tool** cannot be separated without cutting into the
   neighbour; that is reported, no order can fix it.
4. A toolpath that machines inside parts an **earlier toolpath** has already cut
   free (e.g. drilling after the cut-out) is reported with the toolpath to move.

## Strategies

| Strategy | |
|---|---|
| Shortest travel | the nearest vector next |
| Rows (4 corners) | rows from the chosen corner, every row in the same direction |
| Bottom → top / Top → bottom | zig-zag rows |
| Left → right / Right → left | zig-zag columns |
| Columns (4 corners) | columns from the chosen corner |
| Diagonal (4 corners) | sweeps diagonally away from the corner |
| Smallest parts first | small parts lose hold-down first: cut them while the sheet is intact |
| From the centre outwards / from the edges inwards | |

*Inner cuts* decides how inner cuts are grouped: **all inner cuts first, then the
outlines** (the sheet stays whole the longest) or **part by part**. Rows and
columns are found automatically (a new row starts where the next part is more
than half a part size further on) or use a fixed **row / column size**.

With automatic ordering off, the order is frozen: new vectors are added at the
end, deleted ones leave, and only the rules above can move a cut. Picking the
order on the drawing or moving a row freezes the order.

## Smart start points

A closed cut ends where it starts: the stretch just before the start point is
cut last and is all that holds the part (or the scrap of a hole) at the end. The
start point of a vector therefore marks **where its cut closes**, and smart start
points put it where the part stays held best until the very last moment:

* **Hold direction.** The sheet is held most firmly where the sweep has not
  been yet: bottom left → top right is held from the **right and top**, bottom
  right → top left from the left and top, top left → bottom right from the right
  and bottom, top right → bottom left from the left and bottom (zig-zag sweeps:
  ahead of the rows / columns; centre outwards / edges inwards: away from /
  towards the centre; other strategies: as bottom left → top right).
* **What is really there.** Beyond the width of the cut the material is probed
  at every point: sides facing neighbours that are already cut, or the sheet edge,
  hold little; sides facing uncut parts or the solid sheet hold well.
* **Cut last what holds best.** Over the whole cut, the well held sides are left
  for the end, and the final stretch lies on the best held side.
* **Cutting direction counts.** With clockwise outlines (climb milling) a
  rectangle in a bottom left → top right sweep closes on its right side, a corner
  distance above the bottom right corner (the cut runs along the bottom, up the
  left, along the top and finishes down the right side); when its right side faces
  a neighbour that is already cut, it closes at the top right instead. The mirror
  images follow for the other sweeps. **Cut Order ▸ Outline direction** keeps the
  toolpath's climb / conventional setting, mirrors it with the sweep (clockwise for
  bottom left → top right and top right → bottom left, counter-clockwise for the
  other two) or sets clockwise / counter-clockwise; holes keep the same milling as
  their outline.
* **Long narrow parts and letters** (strips with a length of 2.5 times their
  width or more, shapes far from convex such as letters, L and T shapes) always
  close a corner distance before their bottom right corner in a bottom left → top
  right sweep with clockwise cutting: on the right side, just above the corner.
  Other sweeps and directions use the mirrored corner.
* **Never on the corner, never into a cut.** The close and the plunge keep the
  corner distance (default: the larger of 2 mm and the tool radius) from corners,
  including the small arcs an offset path rolls around a sharp corner, and the
  plunge keeps the clearance from everything machined before.
* **Ramps and overlap.** Passes without a lead-in go on where the previous one
  ended, so with a ramp or an overlap the tool goes down earlier along the path
  and the last pass still closes at the start point.

The chosen point becomes a **new node** of the vector, wherever it is, and is
the vector's start from then on. A start point that is about as good as the best
one stays where it is, so start points do not wander between calculations.

Start points set with the **Set Start Point** tool are kept by the automatic
mode (shown as squares while the tool is active); right click one, or use
**Release Start Points of the Selection**, to hand it back.

## What a start point change recalculates

Orders are stored by vector (vector id and contour number), not by position, so
moving a start point never changes the order. Each cut is cached by everything it
depends on: after a start point change only that cut is calculated again, and with
smart start points also the cuts after it (their best start can depend on where
the tool now arrives from). Everything before it stays exactly as it was.

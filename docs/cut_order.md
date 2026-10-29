# Cut order and start points

Profile and engraving toolpaths cut vector by vector. Two independent systems
decide **in which order** the vectors are cut and **where** each closed cut
starts. Each one can work automatically or be run once on demand.

Everything is in one place, the **Cut Order panel** (**Toolpaths ▸ Cut Order...**,
Ctrl+Alt+O; the tab next to *Toolpaths* on the right), and everything it does is
shown on the drawing as it happens.

## Seeing it on the drawing

The panel's **Show on the drawing** options (also in the View menu) draw, for every
cut of the toolpath:

| | |
|---|---|
| **Numbers** | the place in the order, at the point where the tool goes down; blue for the first cut, through green and yellow to red for the last |
| **Route** | the rapid moves between the cuts as arrows, so you can see the sweep (or the zig-zag) |
| **Direction arrows** | arrow heads along every cut pointing the way the tool goes: **orange = clockwise, green = counter-clockwise** (open vectors: blue, along the line) |
| **Start points** | where every cut closes: **green circle** a good start point, **violet square** set by hand, **orange triangle** too close to a corner or to a cut area, **grey circle** simply where the vector was drawn (smart start points are off) |

Select a row of the list and that cut is lit up on the drawing (and its vector is
selected); click a vector on the drawing and the list points at its cut. The text below the
list says why the start point is where it is: which side of the part the cut closes on,
how firmly the last stretch is held, how far it is from the nearest corner and how much
material is left between the plunge and the areas cut before.

## Preview before applying

Changing the strategy, the inner pieces grouping, the row size or the cutting direction
does not change the toolpath at first (**Preview a change first**, on by default and
remembered). The toolpath is calculated with the new settings and drawn in **pink**, the
list shows the new order, and the preview bar says what it changes:

* how many cuts would be cut at another place of the order,
* how far the tool travels between the cuts now and with the preview (`2169 mm → 3681 mm (+70 %)`),
* how many cuts would be cut the other way round,
* how many start points would move (a dotted line runs from every start point to the place
  it would go to).

**Apply** makes it so (undoable), **Cancel** drops it. **Preview** next to *Re-order now*
and next to *Optimise now* show what those one-time actions would do. Any change of the
drawing or closing the panel drops a preview. Switch *Preview a change first* off to apply
changes at once.

| | Cut order (G-code sequence) | Start points (vector editing) |
|---|---|---|
| Automatic | **Smart order**: re-order automatically on every calculation | **Smart start points**: move the vectors' start points automatically |
| Once | **Re-order now** (preview: **Preview**) | **Optimise now** (preview: **Preview**; undoable) |
| By hand | move a row **Up / Down** or drag it; **Pick...** the cuts on the drawing from any position | the **Start point of the selected cut** box, or the **Start Point tool** (**B**) |

Both are also in the context menu of the toolpath list.

## Changing where a cut starts without changing its place in the order

Select the cut in the list; the **Start point of the selected cut** box moves *that* start
point and nothing else (the order is kept by vector, not by place):

* **◀ ▶** move it along the cut by the step next to them (▶ goes the way the tool goes),
* the eight **arrows** put it at a corner (↖ ↗ ↙ ↘: the extreme point of the vector that
  way) or the middle of a side (↑ ← → ↓),
* **Pick...** lets you click on the vector, or drag the marker along it, on the drawing,
* **Smart** lets smart start points choose it, **Release** gives a hand-set start back.

A start point set by hand is kept by smart start points; it shows as a violet square.
Other ways to the same: the **Start Point tool (B)** (click on a vector, or drag the
marker of a start point along its vector; right click a marker to release it), the
**Start Point** menu of the right-click menu of a vector (here, a corner, smart, release),
and **Set as start point** / **Set start point here** of the node tool's right-click menu.

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
| **Smart: shortest route** | the nearest vector next, then the route is shortened by 2-opt (reverse stretches of the route while that makes it shorter); never longer than nearest next, inner pieces first as always. The preview says what it saves |
| Shortest travel | the nearest vector next |
| Rows (4 corners) | rows from the chosen corner, every row in the same direction |
| Bottom → top / Top → bottom | zig-zag rows |
| Left → right / Right → left | zig-zag columns |
| Columns (4 corners) | columns from the chosen corner |
| Diagonal (4 corners) | sweeps diagonally away from the corner |
| Smallest parts first | small parts lose hold-down first: cut them while the sheet is intact |
| From the centre outwards / from the edges inwards | |

*Inner pieces (göbek)* decides how the inner cuts (holes, the hub of a ring, parts
nested in a hole) are grouped with the outlines:

* **Inner pieces of all parts first, then the outlines** (the default): every inner
  piece of every part is cut first, in the order of the strategy, and the outlines
  follow, again in the order of the strategy. The sheet stays whole the longest.
  Pieces inside pieces go from the inside out.
* **Part by part**: a part's inner pieces, then its outline, then the next part.

**Toolpaths ▸ Cut Inner Pieces (Göbek) First** switches between the two for every
toolpath at once. What was chosen last (the strategy too) is remembered and is used
by the toolpaths made later, also in the next session.

Rows and columns are found automatically (a new row starts where the next part is more
than half a part size further on) or use a fixed **row / column size**.

### Cutting direction per strategy

Every strategy has its own cutting direction (**Cut Order ▸ Direction (this
strategy)**; **All strategies...** sets them all at once and can keep them for new
toolpaths). Switching the strategy switches the direction with it, e.g. climb
milling for bottom left → top right and conventional for top right → bottom left.

| Direction | Outside cuts | Their holes | Inside cuts |
|---|---|---|---|
| As the toolpath says | the toolpath's climb / conventional | same milling as the outline | the toolpath's setting |
| Climb milling | clockwise | counter-clockwise | counter-clockwise |
| Conventional milling | counter-clockwise | clockwise | clockwise |
| Outlines clockwise | clockwise | counter-clockwise (same milling) | clockwise |
| Outlines counter-clockwise | counter-clockwise | clockwise (same milling) | counter-clockwise |
| Mirrored with the sweep | clockwise for bottom left → top right and top right → bottom left, otherwise counter-clockwise | same milling as the outline | as the outlines |

Directions are for a clockwise turning spindle (M3). Vectors cut on the line have
no side: clockwise / counter-clockwise turns them, climb / conventional leaves
them as drawn. Smart start points always plan with the direction that is cut.

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
  images follow for the other sweeps. The direction is part of the cut order, see
  below.
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

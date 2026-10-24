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

A start point is where the tool goes down (at the beginning of its lead-in when
there is one) and where the cut finally closes. Smart start points put it

* in solid material, at least the *clearance* away from everything machined
  before (earlier cuts of this toolpath, their full width, and earlier toolpaths
  of the sheet), so the tool never plunges beside a slot or into a sliver,
* away from corners (also the small arcs an offset path rolls around a sharp
  corner of the vector), preferring the calm middle of straight stretches and
  gentle curves,
* not far from where the tool already is.

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

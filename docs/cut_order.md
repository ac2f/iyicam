# Cut order and start points

Profile and engraving toolpaths cut vector by vector. Two independent systems
decide **in which order** the vectors are cut and **where** each closed cut
starts. Each one can work automatically or be run once on demand.

Everything is in one place, the **Cut Order panel** (**Toolpaths ▸ Cut Order...**,
Ctrl+Alt+O; the tab next to *Toolpaths* on the right), and everything it does is
shown on the drawing as it happens.

The page (`iyicam serve`) has the same on its **Preview** tab: the numbers, the arrows along the cuts, the start points, a playback of
the cuts, the list of the cuts in order, moving a cut up or down, to the first or last place, turning the order round, **tapping the
parts on the drawing in the order they are to be cut**, a strategy and the cutting direction, and the change calculated and drawn in
pink with what it does to the travel before it is applied ([docs/web.md](web.md#toolpath-preview-cut-order-and-editing-the-preview-tab)).

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

### Setting start points by hand, on the drawing

Press **B** (or *Draw ▸ Set Start Point*) and every closed vector shows its start point.
Then:

* **Click on a vector, or inside it**, and its cut starts there: where the line is
  nearest to the click, and for a click inside a closed vector the nearest point of the
  smallest vector around the click (so a click in a hole is about the hole).
* **Corners and middles cling.** Near a corner, the middle of a side or the extreme point
  of an arc (the north and south of a circle) the start goes exactly there, and no extra
  node is made. A ring and its name (*Corner*, *Middle*, *Quadrant*, *On the vector*)
  show **before the click** where it will go, and small marks show the places that cling
  on the vector under the pointer. The reach is the one of the snap settings (*Edit ▸
  Preferences*, in pixels); **Alt** or switching *Vectors* snapping off (F8) sets it
  free: the point of the line nearest to the pointer, with a node made there.
* **Press and keep the button down** and the start slides along the vector, wherever the
  pointer goes; let go where it should stay (**Esc** lets go of it without a change).
  Grabbing the marker of a start point does not move it until you move the pointer.
* **`]` and `[`** (or the arrow keys, in the tool) put the start at the **next / previous
  corner**: of the vector under the pointer, else of the one changed last; with another
  tool, of the selected vectors. Four steps go all the way round.
* **Right click** a start point to give it back to smart start points.

For many vectors at once select them and use *Vectors ▸ Start Points* or the **Start Point**
menu of the right-click menu: a corner (↙ ↘ ↖ ↗ of every vector), the middle of a side,
the next / previous corner (`]` `[`), the opposite point (half way round), smart, release.
All of them are **one undo step and one calculation**, however many vectors there are.

Also: the **Start Point** menu entry *Here* (the corner or point nearest to the click) and
**Set as start point** / **Set start point here** of the node tool's right-click menu.

## Rules that always hold

Whatever strategy or manual order is chosen:

1. **Inside first.** Everything inside a closed vector is cut before that
   vector: holes before their outline, a part nested in a hole before the hole,
   engravings before the part around them. A manual order that breaks this rule
   is corrected and the toolpath shows a warning.
2. **No cut in loose scrap.** The order is replayed cut by cut (any size of sheet: up to 5000 cuts): a cut that
   would run in material the cuts before it have already set loose is brought in front of the cut
   that closes the scrap round it. Also, when parts are so close that their cuts overlap,
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
than half a part size further on) or use a fixed **row / column size**. A part is where the sweep
first reaches it: its corner on the side the sweep starts from (the bottom edge for rows from the
bottom), so parts standing on one line are one row however tall they are.

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
start point of a vector therefore marks **where its cut closes**. Smart start
points put it in the **closing zone** and where the sheet beyond it is still there:

* **The closing zone.** For a sweep from **bottom left to top right** and clockwise cutting
  (climb milling, the default) every part closes on its **right side, 5 % of the side above
  the bottom right corner** (at least the corner distance, never on the corner; at most 100 mm).
  The cut runs along the bottom, up the left, along the top and finishes down the right
  side. The other sweeps are the same turned and mirrored: bottom right → top left closes
  at the bottom left, top left → bottom right at the top right, top right → bottom left at the
  top left. Counter-clockwise outlines take the mirrored corner (their last stretch is then
  still the side facing the sheet that is yet to be cut). A **hole** always takes the corner
  of the sweep itself (bottom right for bottom left → top right), whatever its direction.
  Round and irregular parts (letters, circles, L and T shapes) use the extreme point towards
  that corner the same way, so every part of a sign closes in the same place, however it is
  nested.
* **Held by sheet that is still there.** Beyond the cut width the material is probed where the
  cut closes and along the stretch cut last (the two rings next to the cut): they must not lie
  in an area an earlier cut has already removed. A side that faces a neighbour already cut,
  or a ring of loose scrap, holds nothing; the sheet edge holds a little (its rim is still held).
  When the zone itself is not held, the nearest place along the loop that is, is taken, and
  when nowhere is, the best held one. So **the start of the 3rd cut is never in the area the
  2nd cut took away**: every cut uses the fixed part of the sheet.
* **Room round the plunge.** The tool plunges clear of everything machined before
  (*start margin*, 1 mm), and where an earlier cut is close (rows 8 mm apart) the start
  moves up the side until there are about 5 mm of material between the tool and that cut.
* **Cutting direction counts.** The corner is the one a quarter turn from the hold direction,
  turned with the cutting direction (see above). The direction is part of the cut order,
  see below.
* **Never on the corner, never into a cut.** The close and the plunge keep the
  corner distance (default: the larger of 2 mm and the tool radius) from corners,
  including the small arcs an offset path rolls around a sharp corner, and the
  plunge keeps the clearance from everything machined before.
* **Ramps and overlap.** Passes without a lead-in go on where the previous one
  ended, so with a ramp or an overlap the tool goes down earlier along the path
  and the last pass still closes at the start point.
* **Scrap the outside cut cannot enter.** A part with a counter or a slot narrower than the
  tool (a letter whose counter is joined to the outside by a thin channel) has a second loop of
  scrap in the same vector; it is cut **before** the outline, while the part is whole, and gets
  its own smart start point.

The chosen point becomes a **new node** of the vector, wherever it is, and is
the vector's start from then on. A start point that is about as good as the best
one stays where it is, so start points do not wander between calculations.

Start points set with the **Set Start Point** tool are kept by the automatic
mode (shown as squares while the tool is active); right click one, or use
**Release Start Points of the Selection**, to hand it back.

### Order and start points work together

With smart start points on, an automatic order is made to suit the closing zones:

* **Sweeps** (rows, columns, diagonal, zig-zag): after the strategy has made its order, every cut is brought
  **in front of the neighbours that lie next to its closing zone**: if the part's right side, just above
  its bottom right corner, faces a part the sweep would cut first, the part is cut first. It is pulled
  forward to just before that neighbour (with its own inner pieces and with the cuts that in turn want to
  come before it), so the sweep stays as it was and only the neighbours trade places. A cut is only pulled
  past its own neighbourhood in the sweep, never from one row to another.
* **Route strategies** (shortest travel, smart route): the route is made with the same wish: a neighbour that
  would take the sheet away from the closing zone of a cut waits until that cut is made, as long as there is
  another cut to go to.
* **The hold side.** A part is held by the sheet on the side the sweep has not reached (up and to the right for
  bottom left → top right), so no cut may be made after a neighbour that stands on that side of it. Every outline
  is looked at on its own: along the part of it that faces the hold side (the outward normal points towards it)
  rays go up to 30 mm beyond the cut area, and the neighbours they run into are the ones standing on its hold side.
  The mm of outline that face a neighbour are counted both ways; when a part faces a neighbour clearly more than the
  neighbour faces it (at least 4 mm and 15 % more) the part is cut before it. This is done for all neighbours, not
  only those next to the place where the part closes, and wherever they are in the order: a part that stands under or
  to the left of another is cut first even when the row would have put it second. Neighbours that face each other
  about equally interlock (a small letter in the notch of a big one) and are left to the closing zone; when
  wishes form a ring (a before b before c before a) the weakest one gives way. Pulls that would leave a cut in loose
  scrap are tried again one at a time, with the cuts that have to come before the pulled one coming along. The
  rays start at the lowest left point of the outline, so moving a start point never changes a wish.
* **Support map.** After that, every outline is looked at on its own: where along it could the cut close with the
  sheet beyond it (two rings, 1.5 and 5 mm beyond the cut width, at the close and along the 6 tool radii cut last)
  still there, given the cuts that come before it, and the plunging tool keeping the start margin from the cut
  areas before it? The map samples the loop and measures the stretch cut last exactly the way the start point
  choice does, so what it calls held is what the start point choice will find held. A cut that has no such place
  in its closing zone (the zone itself, or up to 8 mm / 8 % of the part along the side from it) is brought in
  front of the fewest cuts that would give it one; when that cannot be had in the zone it is asked anywhere along
  the cut. Pulls that would leave a cut in loose scrap are given up one by one (the harmless ones stay), and a
  last pass brings the cuts that would then be loose in front of what closes them, kept when the cuts then close
  nearer to their zones in all (a cut that cannot be held counts for a lot, so a big letter's close is worth
  more than a small piece's).
* Cuts that want to come before each other keep the order of the strategy.
* **Every order is replayed** (up to 5000 cuts on a sheet): a pull that would leave a cut in loose scrap is
  given up (the one that closes the scrap first, then the next one, until nothing is loose), and
  a cut that the strategy's own order leaves in loose scrap goes in front of the cut that closes it. The replay keeps the cut
  areas as connected *pieces* and a new cut joins only the pieces it touches, so a step costs what lies near the cut: a sheet
  of 493 cuts (a Japanese-letter sign) is replayed in 0.1 s instead of 9 s. (Above 400 cuts the replay used to be given up and
  only neighbours traded places: that sheet had 145 cuts with a neighbour cut first on their hold side or in scrap; now none.)
* **The support is kept whatever the settings are.** The hold side and the loose scrap are looked after by every automatic
  order, with smart start points on or off (off: the start points are the vectors' own, so only *where a cut closes* is not
  asked for). The page makes a new toolpath with smart start points on.
* Strategies with a purpose of their own (**smallest first**, **from the centre outwards**,
  **from the edges inwards**) and orders fixed by hand are left as they are; the one-time **Optimise now** moves start points
  only; their start points still go to the best held place there is.

Where a wish cannot be fulfilled (a part packed between big letters that are cut first) the start goes to the
nearest place along the part that is held, and the cut explanation says how far from the zone it closes.

Measured on the 543 letters of a real sign nested on three 156 x 305 cm sheets (free, gravity to the left,
bottom left to top right, 6 mm end mill, smart start points), each cut judged on its own: no cut is made in loose
scrap, none is cut before something inside it, none closes with a cut area of an earlier cut facing the stretch
cut last, no zone is more than 8 mm / 8 % of the part away except one (10 mm), and for one cut of 543 an earlier
neighbour stands on its hold side because the neighbours form a ring (each wants to be first, 11 mm of outline).
The same judging on 21 layouts (free with every gravity, turned, rows and columns from every corner, wide) of 91
cuts with the strategies rows, diagonal, columns, shortest travel and smart route found nothing, except in a
layout with the parts only 2 mm apart (closer than the tool: the cut areas overlap) and for 5 cuts of one mixed
layout (a zone 28 mm away). The parts that do not fit on a sheet at all (the page of the drawing is bigger than
one sheet) were left out. Nesting the sheets and calculating all 543 cuts took about two minutes.

## What a start point change recalculates

Orders are stored by vector (vector id and contour number), not by position, so
moving a start point never changes the order. Each cut is cached by everything it
depends on: after a start point change only that cut is calculated again, and with
smart start points also the cuts after it (their best start can depend on where
the tool now arrives from). Everything before it stays exactly as it was.

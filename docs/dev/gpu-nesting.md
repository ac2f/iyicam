# GPU nesting (experimental): the brief for the work

This is the hand-over of a task begun in an earlier session, for a session on a computer with an NVIDIA graphics
card. Read it whole before changing anything; then measure first (step 1).

## What is asked

1. A **new nesting method on the GPU (NVIDIA, CUDA)**, many times faster on big jobs.
2. It is **experimental**: off unless switched on in **Settings ▸ Experimental ▸ "Nesting on the graphics card
   (NVIDIA)"**. Switched off — or no NVIDIA card, no CUDA, CuPy missing, out of GPU memory, any error — the nesting is
   the one there is now, unchanged (the processors). Nothing else changes for someone who does not switch it on.
3. The layouts must be **as good**: no part outside its plate, no two parts closer than the gap, holes and inner
   pieces as now. A faster layout that is worse is not a gain. Compare with `tools/bench_nesting.py` (step 1).
4. Everything else as it is: the nesting runs in the background (`NestJob`), Cancel works at once, progress shows, the
   result goes into its project, undo works.

## What there is now (read these)

* `iyicam/nesting/nest.py` — the nester (`nest()` → `_nest()`; `Nester`). Free-form layout by **no-fit polygons
  (NFP)**: for each part, angle by angle, the feasible area of its reference point is the plate's inner fit polygon
  minus the NFPs of the parts already placed (`_best_at_angle`), done with Clipper in whole numbers
  (`iyicam/core/clip.py`, `SCALE` = 10000 per mm). The best point of that area by the gravity (`_keys`) is taken.
* `iyicam/nesting/parallel.py` — every processor: NFPs of all pairs at once (`Nester.precompute`), the plain orders
  laid out at once, the random variations a round at a time, one polishing search per helper. Helpers get the nester
  once (a pickle file). Priority: `NestSettings.priority` (low / normal / high).
* `iyicam/gui/dialogs.py` — `NestingDialog` (`prepare` / `finish`, `start` → `NestJob` in the background), the
  settings window `PreferencesDialog`; `iyicam/gui/settings.py` — `Settings` (where a new flag is kept).
* `docs/nesting.md` — what the user reads (section "Every processor").
* Tests: `tests/test_nesting*.py`, `tests/test_prop_nesting.py` (Hypothesis, checked by `tests/nest_check.py`: every
  part on its plate, gaps kept), `tests/test_nesting_parallel.py`, `tests/test_nesting_background.py`.

### Where the time goes (measured, 113 parts of 60 kinds, 30 attempts, one processor: 79 s)

| | |
|---|---|
| `clip.Execute` — Clipper unions / differences (the feasible area: plate minus NFPs) | 41 s |
| NFPs of pairs (`nfp_of`, Minkowski sums of convex pieces, `convex_minkowski`) | ~31 s of it, made once and reused |
| `_best_at_angle` in Python (loops, `np.lexsort` of the vertices) | 5 s |

On 4 processors with the helpers: 26 s, the same layout. Why the GPU cannot simply run *this* code: it is hundreds of
thousands of small, branching polygon operations, each depending on the one before; sending each to the card costs more
than doing it. There is no general polygon-boolean library on CUDA for Python.

## The proposed method (raster screening + exact check)

The well-known way to use a GPU for nesting is on a **grid**:

1. The plate as a bitmap (pixel ``p`` mm, e.g. 0.5 — a setting; finer is slower and better), occupied by what is placed
   (each placed part grown by the gap, rasterised).
2. Each part at each angle as a bitmap (its outline grown by half the gap, rasterised once).
3. Where the part can go = where its bitmap does not meet the occupied plate: a **2-D correlation** (FFT on the card,
   `cupy.fft` / `cupyx.scipy.signal.fftconvolve`, or a direct correlation for small parts). Every position of every
   angle at once.
4. The best positions by the same gravity keys (`_keys`) — a few candidates, on the card.
5. **Each candidate checked exactly** with the existing geometry (Clipper: inside the plate, no overlap with the parts
   placed — `_overlaps`, `_inside`), and the first one that passes is taken. If none passes (the raster was too coarse
   there), that part goes the exact way (`_best_at_angle`) as now. So the layout is always valid; only how fast a
   good position is found changes.

Keep the rest of the nester (orders, attempts, polishing, plates, holes and inner pieces, squeezing) as it is: the GPU
part replaces the position search of one part (`_best_at_angle` / `_try_sheet`) behind a switch. Holes of placed parts
must stay free on the bitmap (parts may go in them, as now: `room_terms`).

Measure before building it all: a prototype of steps 1–3 alone on the bench job tells whether it is worth it (the time
of a correlation of a 4880 x 2440 bitmap with a part's bitmap, times the positions tried).

## How to do it (in this order)

1. **Measure now**: `python tools/bench_nesting.py --json cpu.json` (and `--processes 0` for one processor). Note the
   GPU (`nvidia-smi`), the CUDA version, `pip install cupy-cuda12x` (or the one for the CUDA there). Keep the numbers.
2. A prototype of the raster search (a script, not in the program) on the bench job: time per part on the card, and
   how often the exact check refuses the best raster candidate. Decide with the user whether it is worth going on.
3. In the program, behind the switch:
   * `Settings.experimental_gpu_nesting: bool = False` (+ a page "Experimental" in `PreferencesDialog`, with a line on
     what it does and that it needs an NVIDIA card; greyed with the reason when no card / no CuPy is found);
   * `NestSettings.gpu: bool = False`, set from the setting by `NestingDialog.prepare` (and `--gpu` in `iyicam-cli
     nest`);
   * a module `iyicam/nesting/gpu.py`: `available() -> str | None` (why not, or None), the raster search; imported only
     when switched on (CuPy is not a dependency of the program: an extra, `pip install iyicam[gpu]`);
   * in `Nester`: when `st.gpu` and `gpu.available()` is None, the position search goes through it; any exception →
     a warning once and the exact way for the rest of the nesting.
4. Tests (they must pass without a GPU too): the switch off changes nothing (same layout as before); with a fake
   "card" (the raster search on NumPy instead of CuPy, the same code) the layouts pass `nest_check.verify`; a GPU
   failure falls back. The Hypothesis test of nesting run with the raster search on NumPy.
5. `tools/bench_nesting.py --gpu` and the numbers into `docs/nesting.md` (a section "Experimental: on the graphics
   card"), honest: how much faster, on which card, and whether the layouts were as good.

Acceptance: switched off, all tests pass and the layouts are the same as now; switched on, on the bench job, the same
number of plates (or fewer) and a utilisation within a fraction of a percent, in clearly less time; every layout valid.

## Step 1: measured (2026-11-27)

The machine: Intel Core i3-6098P (2 cores, 4 threads), 16 GB, Windows 10; **NVIDIA GeForce GTX 960, 2 GB**
(compute capability 5.2, Maxwell), driver 560.94, CUDA 12.6 (driver). About 1.6 GB of the card is free with the
desktop open: a whole plate at 0.5 mm (4880 x 2440 float32, 48 MB) fits many times, but not hundreds of them.

The environment: a virtual environment `.venv/` in the project (ignored by git; not `.venv-build/`, which is the
package's and must not get CuPy): `python -m venv .venv`, then
`.venv\Scripts\python -m pip install -e ".[dev]" pytest-xdist hypothesis "cupy-cuda12x[ctk]"`. The `[ctk]` extra brings
the CUDA libraries (runtime 12.9, NVRTC, cuFFT) as wheels, so no CUDA Toolkit is needed; they run on the 12.6 driver
(CUDA minor-version compatibility). CuPy 14.2.0; set `CUPY_CACHE_DIR=.venv\cupy-cache` to keep its compiled kernels in
the project. CuPy warns "CUDA path could not be detected": harmless with the wheels.

A first probe: one FFT correlation of a plate bitmap 4880 x 2440 with a part bitmap 400 x 400
(`cupyx.scipy.signal.fftconvolve`, mode "valid") takes **34 ms** on this card.

The bench job (`tools/bench_nesting.py`, 60 kinds, 113 parts, 30 attempts, seed as default), results in
`docs/dev/bench/cpu.json` and `cpu1.json`:

| | 4 helper processes | one processor (`--processes 0`) |
|---|---|---|
| no-fit polygons | 25.8 s (6924, all pairs, on the helpers) | (made as they are needed, in "the rest") |
| layouts (18 + 3 x 4 at once) | 18.7 + 3.4 + 3.3 + 3.3 s | |
| polishing (4 at once) | 8.8 s | |
| the rest | 0.1 s | 83.4 s |
| **in all** | **63.4 s** | **85.6 s** |
| plates, placed | 3, 113 of 113 | 3, 113 of 113 |
| utilisation | 57.1 %, 56.2 %, 16.7 % | the same |

The layout is the same both ways. On this processor (2 real cores) the helpers gain only 1.35 x. A profile of one
process (8 attempts) says where the time goes: making no-fit polygons 55 % (Minkowski sums of convex pieces 21 %, their
union in Clipper 28 %, splitting outlines into convex pieces 11 %), the feasible areas in `_best_at_angle` most of the
rest. One process makes nearly every pair's NFP too (6583 of 6924 in 8 attempts): they are needed, not a waste of the
helpers. Once they are made, a layout takes about 5 s; the first one 80 s. So the GPU has to beat about 85 s for one
process, or 63 s against all four.
A 34 ms correlation per part per angle is too slow done naively (113 parts x several angles x 30 attempts is ~10^4
searches: minutes); the prototype (step 2) must batch the angles and parts, correlate only the plate's window that can
hold the part, or keep a running occupancy to make each search cheaper.

## Rules of this project (the user's)

* Answer the user in **Turkish**. The code, comments, commit messages and documents are in English, in the style there
  is (plain words, what it does and why; see any module).
* Work on **master** only, commit and push when done (`git push origin master`). Commit messages end with the
  `Co-Authored-By` line the session gives.
* Run the whole test suite before pushing: `python -m pytest tests -q -n 4` (pytest-xdist; Qt tests need
  `QT_QPA_PLATFORM=offscreen` on a machine without a screen). Three timing tests can fail under a heavy parallel load
  and pass alone (`test_snap::test_the_status_bar_says_what_the_pointer_clung_to`, `test_thin`, `test_engine_parity`).
* The Windows package is built with `packaging/build.py` (PyInstaller, `packaging/iyicam.spec`); a new optional
  library (CuPy) must not be needed to build or to run without the GPU.

## Steps 2 to 5: what was done (2026-11-27)

**On the processors** (the layouts are the same, checked by the layout digest that `tools/bench_nesting.py` now prints):
`convex_parts` tests every corner against an ear at once (NumPy, the same integer test): 1.55 x on that part. The convex
pieces of an outline are made when first asked for (`_Variant.pieces`), so the card's way never makes them. A batched
NumPy Minkowski sum of all piece pairs was tried and taken out again: point for point the same, but no faster (the pieces
are small, NumPy's cost per call ate the gain); the union in Clipper is the larger part anyway.

**On the card** (`iyicam/nesting/gpu.py`, `NestSettings.gpu`, Settings > Preferences > Experimental, `iyicam-cli nest
--gpu`, `tools/bench_nesting.py --gpu / --gpu-numpy / --pixel`): the raster search as proposed, with what the prototype
showed it needs to be fast:

* every attempt side by side, a part of each at every step: one search launch for all attempts and angles, one launch
  per slide move, five launches to stamp (no CuPy array operations in the loop: they cost 0.9 ms a stamp);
* the search goes through 8 x 8 cells in the gravity's order, a warp per 32 cells: rectangles inside the part (the
  biggest ones from sampled rows and columns) against a summed area table of 8 x 8 blocks reject 97 % of the cells at
  one look; the runs of the rest are read coarse to fine (the first row, the middle, the quarters ...): 7 a position
  instead of 45 from the middle out; 32-bit index arithmetic (64-bit division on the card was most of the time);
* a search starts where the same part at the same angle found its place last time on that plate (a plate only fills
  up, so what was taken stays taken): the result is the same, less is looked at;
* the slide: rays from the corners of each outline to the edges of the other (float32, relative to the moving part, 2
  microns kept off the contact), all parts near the swept box of all attempts in one launch;
* the best layout is checked exactly (`gpu.valid`, Clipper); if few parts are on its last plate, they are put on the
  plates before it with `_try_sheet` (no-fit polygons, as the usual way) where they fit (`gpu.repair`).

Tests (`tests/test_nesting_gpu.py`, 15, about 20 s without a card): the switch off leaves `gpu.py` unloaded and the notes
empty; the card's way on NumPy is valid (`nest_check.verify`), holes are used, every gravity it does; what it does not do
and a failure go the usual way with a note; a layout that fails the exact check is not taken; cancel works; a Hypothesis
test of its layouts; with a card, the kernels give the same positions and tables as NumPy, and a layout on the card.

Numbers (this machine, `docs/dev/bench/*.json`): 113 parts, 30 attempts: the processors 63 s, the card 18 s (128
layouts); 3.5 x. See the table in `docs/nesting.md` ("Experimental: on the graphics card"): on two jobs of three the same
number of plates and a utilisation a few points lower on the full plates, on the third a fourth plate (2.7 %). **The
acceptance (as many plates or fewer, utilisation within a fraction of a percent) is not met yet**: it stays off.

## Step 6: faster and better than the processors (2026-11-28)

What changed (`iyicam/nesting/gpu.py`), each measured on seed 3 (122 parts) and kept only where it paid:

* **A bug**: with *Along X*, a part wider than the layout so far was searched from a negative column (`lim < 0`) and on the
  card could land off the plate; the exact check at the end hid it (2 of 16 plain layouts were invalid). Fixed; a test
  now checks every layout the card makes, not only the one taken.
* **Rows as distances**: per pixel, how far the next taken pixel along its row is (uint16) instead of row prefix sums: a run
  is one read, and a stamp changes only the pixels from the taken one before it to its end (row update 2.9 s -> 0.55 s).
* **Supercells** of 64 x 64 positions rejected at one look; no rectangle test per position (the runs are cheaper there);
  8 rectangles a part; the run that met something at the lane's last position is read first (search 6.9 s -> 3.8 s).
* **Exact stamps**: a part is marked where it really lies, not rounded to the grid and grown by 1.42 px: its outline grown
  by the pixel square (Minkowski sum, once per part and angle) filled row by row on the card. A part's pixels are the ones
  it covers some of, the same for the search: the room the bitmaps add is at most a pixel, the same everywhere. NumPy
  fills them the same to the bit (float32, no fused multiply-add on the card).
* **Two streams**: the attempts in two groups by turns, the jobs up and the results down through page-locked memory; the
  Windows driver keeps queued work back until something waits, so each launch is followed by a query that sends it.
* **Plate after plate** (`search_plates`): what decides the plates a layout takes is how full the plates before the last
  are. The next plate of the best few layouts is filled in many ways (plain orders of the parts left, then changes of the
  fullest), the fullest few go on; where the parts left nearly fit on one plate and a plate fewer is in reach, rounds more
  there. Two such searches of their own (own random changes), the best of them kept: one search goes where its first
  changes lead it. Tried and left out: a coarse plate (fully taken 8 x 8 blocks) before the search (it rejects only
  what the rectangles already do), filling the gaps of the plates before the last with its parts (a plate's pass already
  tried every part), re-making the best layouts from a plate on (worse for the same work), more rounds per plate (fuller
  first plates, worse layouts).
* The card's memory is given back after a nesting (CuPy keeps it otherwise: the next nesting in the same program ran out
  and Windows paged it, 4 times slower); the attempts at a time follow the free memory; the exact repair at the end stops
  after 5 s.

Six jobs (`tools/bench_nesting.py --seed N`, 113 to 127 parts, 30 attempts; the processors: all 4, the card: GTX 960):

| seed | the processors | the card | |
|---|---|---|---|
| 1 | 63 s: 57.1 / 56.2 / 16.7 % | 19 s: 55.7 / 58.1 / 16.1 % | better |
| 2 | 70 s: 62.9 / 54.5 / 24.1 % | 17 s: 63.1 / 54.4 / 23.9 % | better |
| 3 | 105 s (other work ran): 54.2 / 61.6 / 46.1 % | 35 s: 52.2 / 64.1 / 45.7 % | better |
| 4 | 74 s: 53.2 / 62.5 / 24.1 % | 17 s: 53.4 / 64.1 / 22.2 % | better |
| 5 | 86 s: 56.1 / 55.0 / 21.2 % | 20 s: 52.8 / 56.6 / 23.0 % | worse |
| 6 | 84 s: 56.0 / 58.2 / 33.1 % | 18 s: 60.7 / 55.4 / 31.3 % | better |

Every job 3 plates both ways; the card 3.8 x faster in all, its last plate smaller on 5 of 6 (better by
`Nester.quality`: as many plates, the plates before the last fuller). With 3 searches (`attempts` 384) the same 5 of 6,
2.8 x. One search alone (128): 3 of 6, 6 x.

Then (the same layouts, to the bit): the slide gathers first the corners and edges in the band across the move that both
parts share (slide kernel about 1.6 x faster), and late on a plate (after a part that did not fit) the next 4 parts of an
attempt are looked for in the same step, the first that fits placed as one at a time would have found it (fewer steps:
20 s -> 17.6 s on seed 1; 8 or 16 ahead were slower). The times in the table are with these.

A pixel of 0.7 mm (`--pixel 0.7`): the same 5 of 6, the last plate smaller still on four of them (seed 5: 22.1 % against
21.2 %), 3.1 x instead of 3.8 x. More searches do not help seed 5 (3: 23.0 %, 5: 22.7 %).

What is left: the search kernel, the Python of a step (half the time). Seed 5 is still the processors'.

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

"""Nesting on the graphics card (experimental): the free layout of parts on rectangular plates, many attempts at once.

Switched on with ``NestSettings.gpu`` (Settings > Experimental). It needs an NVIDIA card and CuPy
(``pip install cupy-cuda12x``, or ``pip install iyicam[gpu]``); without them, or for what it does not do (plates that are
not rectangles, parts fixed on a plate, rows / columns, squeezing in, the gravities ``compact`` and ``diag``), the nesting
is the usual one on the processors, unchanged. The same code runs on NumPy (``backend("numpy")``): slower, for the tests.

How a part is placed, the no-fit polygons left out:

1. The plate is a bitmap (``pixel`` mm). A part's pixels are the ones it covers some of: the pixels whose centre lies in its
   outline (grown by half the gap) grown by the pixel square. Every part at every angle is such a bitmap at the positions
   of the grid, as runs of pixels per row; per pixel of the plate, how far the next taken one along its row is: a run is
   free where that is at least its length (one read).
2. Where the part can go is where none of its runs has anything under it: every pixel position of every angle at once on
   the card. The positions are looked at in the order of the gravity, in supercells of 64 x 64 and cells of 8 x 8 that a
   few big rectangles inside the part reject at one look on a coarse summed area table of the plate where they lie on
   something taken; the first free position is the best. A free position is free: no pixel is covered by both parts.
3. The part is then slid the way the gravity pulls it (left, down, left, down, or down first), each time as far as it
   goes until it touches a part or the plate's edge, worked out exactly from the outlines (rays from the corners of one to
   the edges of the other): the gaps the pixels leave are closed, the parts lie against one another as they do with the
   no-fit polygons.
4. The part is marked on the plate where it ended, not on the grid: its grown outline filled row by row on the card (the
   same pixels on NumPy, to the bit), so the parts placed keep the same room, a pixel at most, wherever they lie.

Many attempts run side by side, a part of each at every step, in two groups by turns on two streams (the processor gets
one group's next step ready while the card works on the other's). First the plain orders of the nester, then plate after
plate (:func:`search_plates`): the next plate of the best few layouts filled in many ways, the fullest going on. The best
layout by :meth:`Nester.quality` is checked exactly at the end (every part inside its plate, no two closer than the gap:
Clipper, as :func:`Nester._overlaps`); one that fails is not taken (the next best is, or the nesting goes the usual way).
"""
from __future__ import annotations

import contextlib
import math
import random
import time
from typing import Callable

import numpy as np
import pyclipper

from ..core import clip

BLOCK = 8          # pixels per side of a block of the coarse table (TS in the kernels)
CELL = 8           # positions per side of a cell of the search (the kernels' 8, a supercell 8 x 8 cells)
GRID = 4           # thread blocks of 256 per search job
JB = 20            # ints per search job (the kernel's JB)
INF = 2 ** 31 - 1
SLIDES = 4         # slides of a part at most, the two ways the gravity pulls by turns
BACKOFF = 20       # units (2 microns) kept off a contact after a slide: the rays are worked out in float32
SEARCH = 128       # layouts per search of the plates (see nest_on_gpu)
GRAVITIES = ("left", "left_far", "bottom", "bottom_far", "reach_x", "reach_y")
_why: str | None | bool = False


def _flush(stream) -> None:
    """The work queued on a stream sent to the card now (the Windows driver keeps it back until something waits)."""
    q = getattr(stream, "done", None)
    del q


def available() -> str | None:
    """Why the graphics card cannot be used, or ``None`` when it can (CuPy there, an NVIDIA card, a kernel runs)."""
    global _why
    if _why is not False:
        return _why
    try:
        import cupy as cp
    except Exception as e:  # noqa: BLE001
        _why = f"CuPy is not installed ({type(e).__name__}): pip install cupy-cuda12x"
        return _why
    try:
        if cp.cuda.runtime.getDeviceCount() < 1:
            _why = "there is no NVIDIA graphics card"
            return _why
        a = cp.arange(4, dtype=cp.int32)
        if int((a * 2).sum()) != 12:
            _why = "the graphics card gave a wrong answer"
            return _why
        _kernels(cp)
        _why = None
    except Exception as e:  # noqa: BLE001
        _why = f"the graphics card cannot be used: {e}"
    return _why


def device_name() -> str:
    try:
        import cupy as cp
        return cp.cuda.runtime.getDeviceProperties(0)["name"].decode(errors="replace")
    except Exception:  # noqa: BLE001
        return ""


# -- kernels -----------------------------------------------------------------------------------------------------------

_SRC = r"""
// One job: the best free position of one part at one angle on one plate. A position k is (column, row) in the order of
// the gravity: column-major (left: the first columns first) or row-major (bottom). The smallest free k wins.
//
// The positions are taken 8 x 8 at a time (a cell), the cells in the gravity's order: a core rectangle of the part that
// lies on an occupied block of the coarse table at every position of the cell rejects all 64 at one look (most of a full
// plate goes that way); the others are looked at one by one, in order, until one is free. A cell that begins past the
// best found so far is not looked at, and the threads of a job sweep the cells in order, so the work after the best is a
// wave at most.
#define TS 3                  // the coarse table's blocks: 8 x 8 pixels
#define TBK (1 << TS)
#define TBM (TBK - 1)
__device__ __forceinline__ const void* ptr_at(const int* q) {
    return (const void*)(((unsigned long long)(unsigned)q[1] << 32) | (unsigned)q[0]);
}

__device__ __forceinline__ int box_sum(const int* T, int TB1, int bx0, int by0, int bx1, int by1) {
    return T[by1 * TB1 + bx1] - T[by0 * TB1 + bx1] - T[by1 * TB1 + bx0] + T[by0 * TB1 + bx0];
}

#define JB 20
extern "C" __global__ void search(int* jb, int njobs, int job0, const int* runs, const int* cores) {
    // a warp takes a supercell (8 x 8 cells) at a time: a core on an occupied block at all its 4096 positions rejects it
    // at one look (most of a full plate); else its cells, 32 at a time, each lane the look at one; the cells that are
    // not rejected are then looked at in order by the whole warp, two positions a lane
    int job = job0 + blockIdx.y;
    const int* q = jb + JB * job;
    const unsigned short* NX = (const unsigned short*)ptr_at(q);
    const int* T = (const int*)ptr_at(q + 2);
    q += 4;
    int* best = jb + JB * njobs;
    int W = q[0], nc = q[1], nr = q[2], r0 = q[3], nruns = q[4], c0 = q[5], ncores = q[6], rowmajor = q[7];
    int TB1 = q[8], x0 = q[9], kst = q[10], y0 = q[11];
    int ncx = (nc + 7) >> 3, ncy = (nr + 7) >> 3;
    int nsx = (ncx + 7) >> 3, nsy = (ncy + 7) >> 3;
    int lane = threadIdx.x & 31;
    int warp = (blockIdx.x * blockDim.x + threadIdx.x) >> 5;
    int warps = (gridDim.x * blockDim.x) >> 5;
    int sinner = rowmajor ? nsx : nsy;                // supercells along the fast direction
    int scells = nsx * nsy;
    int span = rowmajor ? nc : nr;
    volatile int* bp = (volatile int*)(best + job);
    // (positions before ``kst`` are known to be taken: the same part at the same angle found nothing free there before,
    // and a plate only fills up)
    int first = ((rowmajor ? kst / nc : kst / nr) >> 6) * sinner;
    for (int s = first + warp; s < scells; s += warps) {
        int outer = s / sinner, sin_ = s - outer * sinner;
        if ((outer << 6) * span > *bp)
            return;                                             // (every supercell from here on begins past the best)
        int SI = rowmajor ? sin_ : outer, SJ = rowmajor ? outer : sin_;
        int si0 = SI << 6, sj0 = SJ << 6;
        if ((rowmajor ? sj0 * nc + si0 : si0 * nr + sj0) >= *bp)
            continue;
        bool gone = false;
        if (lane < ncores) {
            const int* cr = cores + 4 * (c0 + lane);
            int X0 = x0 + si0, X1 = x0 + min(si0 + 64, nc) - 1, Y0 = y0 + sj0, Y1 = y0 + min(sj0 + 64, nr) - 1;
            int bx0 = (X1 + cr[1] + TBM) >> TS, bx1 = (X0 + cr[3]) >> TS;
            int by0 = (Y1 + cr[0] + TBM) >> TS, by1 = (Y0 + cr[2]) >> TS;
            gone = bx1 > bx0 && by1 > by0 && box_sum(T, TB1, bx0, by0, bx1, by1) != 0;
        }
        if (__any_sync(0xffffffffu, gone))
            continue;
        for (int h = 0; h < 2; ++h) {
            // the cells of the supercell in the gravity's order: 4 lines of 8 along the fast direction per half
            int a = (lane >> 3) + 4 * h, b = lane & 7;
            int I = (SI << 3) + (rowmajor ? b : a), J = (SJ << 3) + (rowmajor ? a : b);
            bool alive = I < ncx && J < ncy;
            if (alive) {
                int kmin = rowmajor ? (J << 3) * nc + (I << 3) : (I << 3) * nr + (J << 3);
                alive = kmin < *bp;
                int i0 = I << 3, j0 = J << 3;
                int X0 = x0 + i0, X1 = x0 + min(i0 + 8, nc) - 1, Y0 = y0 + j0, Y1 = y0 + min(j0 + 8, nr) - 1;
                for (int c = 0; c < ncores && alive; ++c) {
                    const int* cr = cores + 4 * (c0 + c);
                    int bx0 = (X1 + cr[1] + TBM) >> TS, bx1 = (X0 + cr[3]) >> TS;
                    int by0 = (Y1 + cr[0] + TBM) >> TS, by1 = (Y0 + cr[2]) >> TS;
                    if (bx1 > bx0 && by1 > by0 && box_sum(T, TB1, bx0, by0, bx1, by1) != 0)
                        alive = false;
                }
            }
            unsigned live = __ballot_sync(0xffffffffu, alive);
            int hint = -1;           // (the run that met something at this lane's position before: next door, it mostly does too)
            while (live) {
                int src = __ffs(live) - 1;
                live &= live - 1;
                int CI = __shfl_sync(0xffffffffu, I, src), CJ = __shfl_sync(0xffffffffu, J, src);
                int i0 = CI << 3, j0 = CJ << 3;
                if ((rowmajor ? j0 * nc + i0 : i0 * nr + j0) >= *bp) break;
                int ni = min(8, nc - i0), nj = min(8, nr - j0);
                int mine = 0x7fffffff;
                for (int hh = 0; hh < 2; ++hh) {
                    int u = lane + 32 * hh;
                    int di = rowmajor ? (u & 7) : (u >> 3), dj = rowmajor ? (u >> 3) : (u & 7);
                    if (di >= ni || dj >= nj) continue;
                    int i = i0 + di, j = j0 + dj;
                    int k = rowmajor ? j * nc + i : i * nr + j;
                    if (k >= *bp) break;
                    if (k < kst) continue;
                    int x = x0 + i, y = y0 + j;
                    bool hit = false;
                    if (!hit && hint >= 0) {
                        const int* ru = runs + 3 * (r0 + hint);
                        hit = NX[(long long)(ru[0] + y) * W + ru[1] + x] < ru[2] - ru[1];
                    }
                    for (int t = 0; t < nruns && !hit; ++t) {
                        const int* ru = runs + 3 * (r0 + t);
                        hit = NX[(long long)(ru[0] + y) * W + ru[1] + x] < ru[2] - ru[1];
                        if (hit) hint = t;
                        if ((t & 7) == 7 && k >= *bp) break;
                    }
                    if (!hit && k < *bp) {
                        mine = k;
                        break;
                    }
                }
                if (mine != 0x7fffffff)
                    atomicMin(best + job, mine);
            }
        }
    }
}

// Stamping, every plate of a step at once: a part filled in where it lies (stamp_poly), the distances of the rows it
// touched made again (row_next), the blocks it touched counted again (coarse), the summed area table of the blocks from there on
// (table_rows, table_cols). ``si`` per job: W, H, Wb, Hb, mw, i, j, a0, b0, a1, b1 (the window on the plate).
#define SJ 26
#define SQ 10
#define PTR(q, k) ptr_at((q) + 2 * (k))
#define MAXC 128
extern "C" __global__ void stamp_poly(const int* si, const float* GE) {
    // a row of the plate a block: the pixels whose centre is inside the part's outline grown by the pixel square (the
    // pixels the part covers some of), the part where it really lies (fx, fy: its reference in pixels of the plate).
    // The crossings of the outline with the row's centre line, sorted; between two, inside where the winding is not 0.
    __shared__ float xs[MAXC];
    __shared__ int ds[MAXC];
    __shared__ int cnt, lo, hi;
    int job = blockIdx.y;
    const int* q = si + SJ * job;
    unsigned char* occ = (unsigned char*)PTR(q, 0);
    q += SQ;
    int W = q[0], a0 = q[7], b0 = q[8], a1 = q[9], b1 = q[10], g0 = q[11], gn = q[12];
    float fx = __int_as_float(q[13]), fy = __int_as_float(q[14]);
    int r = b0 + blockIdx.x;
    if (r >= b1) return;
    if (threadIdx.x == 0) { cnt = 0; lo = 0x7fffffff; hi = -0x7fffffff; }
    __syncthreads();
    float yc = (float)r + 0.5f;
    for (int e = threadIdx.x; e < gn; e += blockDim.x) {
        const float* E = GE + 4 * (g0 + e);
        float y0 = E[1] + fy, y1 = E[3] + fy;
        if ((y0 <= yc) != (y1 <= yc)) {
            float xa = E[0] + fx, xb = E[2] + fx;
            float x = xa + (yc - y0) * (xb - xa) / (y1 - y0);
            int k = atomicAdd(&cnt, 1);
            if (k < MAXC) { xs[k] = x; ds[k] = y1 > y0 ? 1 : -1; }
            atomicMin(&lo, (int)floorf(x));
            atomicMax(&hi, (int)ceilf(x));
        }
    }
    __syncthreads();
    int n = cnt;
    if (n == 0) return;
    unsigned char* row = occ + (long long)r * W;
    if (n > MAXC) {                     // (too many to sort here: all of it between the first and the last, more than it covers)
        for (int c = max(a0, lo - 1) + threadIdx.x; c < min(a1, hi + 2); c += blockDim.x) row[c] = 1;
        return;
    }
    if (threadIdx.x == 0) {
        for (int i = 1; i < n; ++i) {
            float v = xs[i];
            int d = ds[i], j = i - 1;
            while (j >= 0 && xs[j] > v) { xs[j + 1] = xs[j]; ds[j + 1] = ds[j]; --j; }
            xs[j + 1] = v;
            ds[j + 1] = d;
        }
        int w = 0;
        for (int i = 0; i < n; ++i) { w += ds[i]; ds[i] = w; }
    }
    __syncthreads();
    for (int i = 0; i + 1 < n; ++i) {
        if (ds[i] == 0) continue;
        int c0 = max(a0, (int)ceilf(xs[i] - 0.5f)), c1 = min(a1, (int)ceilf(xs[i + 1] - 0.5f));
        for (int c = c0 + threadIdx.x; c < c1; c += blockDim.x) row[c] = 1;
    }
}

extern "C" __global__ void row_next(const int* si) {
    // per pixel of a row: how far the next taken pixel is (0: taken); only the pixels from the taken one before the window
    // (a0, a1) up to its end change: the first taken pixel at or after each (a scan of the window from the right, the
    // pixel at a1 tells what lies past it)
    __shared__ int f[256];
    __shared__ int lo;
    int job = blockIdx.y;
    const int* q = si + SJ * job;
    const unsigned char* occ0 = (const unsigned char*)PTR(q, 0);
    unsigned short* N0 = (unsigned short*)PTR(q, 1);
    q += SQ;
    int W = q[0], a0 = q[7], b0 = q[8], a1 = q[9], b1 = q[10];
    int r = b0 + blockIdx.x;
    if (r >= b1) return;
    const unsigned char* occ = occ0 + (long long)r * W;
    unsigned short* N = N0 + (long long)r * W;
    int t = threadIdx.x, nt = blockDim.x;
    int per = (a1 - a0 + nt - 1) / nt;
    int c0 = min(a1, a0 + t * per), c1 = min(a1, c0 + per);
    int mine = 0x7fffffff;
    for (int c = c0; c < c1; ++c)
        if (occ[c]) { mine = c; break; }
    f[t] = mine;
    __syncthreads();
    for (int off = 1; off < nt; off <<= 1) {             // (the least of the threads from this one on)
        int v = t + off < nt ? f[t + off] : 0x7fffffff;
        __syncthreads();
        f[t] = min(f[t], v);
        __syncthreads();
    }
    int beyond = a1 < W ? a1 + N[a1] : W;
    int cur = min(t + 1 < nt ? f[t + 1] : 0x7fffffff, beyond);
    for (int c = c1 - 1; c >= c0; --c) {
        if (occ[c]) cur = c;
        N[c] = (unsigned short)(cur - c);
    }
    int first = min(f[0], beyond);
    if (t == 0) lo = -1;
    __syncthreads();
    for (int base = a0 - 1; base >= 0; base -= nt) {     // the taken pixel before the window
        int c = base - t;
        if (c >= 0 && occ[c]) atomicMax(&lo, c);
        __syncthreads();
        int stop = lo >= 0;
        __syncthreads();
        if (stop) break;
    }
    for (int c = lo + 1 + t; c < a0; c += nt)
        N[c] = (unsigned short)(first - c);
}

extern "C" __global__ void coarse(const int* si) {
    int job = blockIdx.y;
    const int* q = si + SJ * job;
    const unsigned char* occ = (const unsigned char*)PTR(q, 0);
    int* C = (int*)PTR(q, 2);
    q += SQ;
    int W = q[0], Wb = q[2], a0 = q[7], b0 = q[8], a1 = q[9], b1 = q[10];
    int cb0 = a0 >> TS, cb1 = (a1 + TBM) >> TS, rb0 = b0 >> TS, rb1 = (b1 + TBM) >> TS;
    int w = cb1 - cb0, n = w * (rb1 - rb0);
    for (int k = blockIdx.x * blockDim.x + threadIdx.x; k < n; k += gridDim.x * blockDim.x) {
        int br = rb0 + k / w, bc = cb0 + k % w;
        int s = 0;
        for (int y = 0; y < TBK; ++y) {
            const unsigned char* row = occ + (long long)(br * TBK + y) * W + bc * TBK;
            for (int x = 0; x < TBK; ++x) s += row[x];
        }
        C[br * Wb + bc] = s;
    }
}

extern "C" __global__ void table_rows(const int* si) {
    int job = blockIdx.y;
    const int* q = si + SJ * job;
    const int* C = (const int*)PTR(q, 2);
    int* T = (int*)PTR(q, 3);
    q += SQ;
    int Wb = q[2], Hb = q[3], rb0 = q[8] >> TS;
    int r = rb0 + blockIdx.x * blockDim.x + threadIdx.x;
    if (r >= Hb) return;
    int s = 0;
    int* row = T + (long long)(r + 1) * (Wb + 1);
    row[0] = 0;
    for (int c = 0; c < Wb; ++c) {
        s += C[r * Wb + c];
        row[c + 1] = s;
    }
}

extern "C" __global__ void table_cols(const int* si) {
    int job = blockIdx.y;
    const int* q = si + SJ * job;
    int* T = (int*)PTR(q, 3);
    q += SQ;
    int Wb = q[2], Hb = q[3], rb0 = q[8] >> TS;
    int c = blockIdx.x * blockDim.x + threadIdx.x;
    if (c > Wb) return;
    for (int r = rb0 + 1; r <= Hb; ++r)
        T[(long long)r * (Wb + 1) + c] += T[(long long)(r - 1) * (Wb + 1) + c];
}

// One task: how far a moving part can go along an axis before it meets one other part: rays from the corners of each to
// the edges of the other. Coordinates are relative to the moving part (float), the result the least over the tasks of a
// job (float bits: non-negative floats compare as integers).
extern "C" __global__ void slide(int* tb, int ntasks, const float* E) {
    int task = blockIdx.x;
    const int* q = tb + 8 * task;
    int* out = tb + 8 * ntasks;
    int job = q[0], m0 = q[1], nm = q[2], o0 = q[3], no = q[4], dir = q[5];
    float ox = __int_as_float(q[6]), oy = __int_as_float(q[7]);
    // dir: 0 -x, 1 -y, 2 +x, 3 +y; along = the coordinate that moves, sg = its sign
    int ax = dir & 1;
    float sg = dir >= 2 ? 1.f : -1.f;
    float tmin = __int_as_float(out[job]);
    long long n1 = (long long)nm * no, n = 2 * n1;
    for (long long k = threadIdx.x; k < n; k += blockDim.x) {
        bool first = k < n1;
        long long kk = first ? k : k - n1;
        int iv, ie;
        float vx, vy, px, py, qx, qy, s;
        if (first) {          // a corner of the moving part to an edge of the other, along the move
            iv = m0 + (int)(kk / no); ie = o0 + (int)(kk % no);
            vx = E[4 * iv]; vy = E[4 * iv + 1];
            px = E[4 * ie] + ox; py = E[4 * ie + 1] + oy; qx = E[4 * ie + 2] + ox; qy = E[4 * ie + 3] + oy;
            s = sg;
        } else {              // a corner of the other to an edge of the moving part, against the move
            iv = o0 + (int)(kk / nm); ie = m0 + (int)(kk % nm);
            vx = E[4 * iv] + ox; vy = E[4 * iv + 1] + oy;
            px = E[4 * ie]; py = E[4 * ie + 1]; qx = E[4 * ie + 2]; qy = E[4 * ie + 3];
            s = -sg;
        }
        float va = ax ? vy : vx, vb = ax ? vx : vy;     // along the move, across it
        float pa = ax ? py : px, pb = ax ? px : py, qa = ax ? qy : qx, qb = ax ? qx : qy;
        if (pb == qb) continue;                         // an edge along the move: slid along, not met
        if ((pb - vb) * (qb - vb) > 0.f) continue;      // the ray passes it
        float ca = pa + (vb - pb) * (qa - pa) / (qb - pb);
        float t = s * (ca - va);
        if (t < -2.f) continue;                         // behind
        t = fmaxf(t, 0.f);
        if (t < tmin) {
            atomicMin(out + job, __float_as_int(t));
            tmin = t;
        }
    }
}
"""

_compiled: dict = {}


def _kernels(cp):
    if "search" not in _compiled:
        mod = cp.RawModule(code=_SRC, options=("--fmad=false",))   # (the stamps as NumPy makes them, to the bit)
        _compiled["search"] = mod.get_function("search")
        for name in ("slide", "stamp_poly", "row_next", "coarse", "table_rows", "table_cols"):
            _compiled[name] = mod.get_function(name)
    return _compiled


class _Cupy:
    name = "cupy"

    def __init__(self):
        import cupy as cp
        self.xp = cp
        self.k = _kernels(cp)
        self._stages: dict = {}

    def asarray(self, a):
        return self.xp.asarray(a)

    # (the jobs go up and the results come down through page-locked memory, one buffer per stream and use: the copies are
    # queued with the kernels and the processor does not wait for them)
    def _stage(self, key, nbytes: int):
        cp = self.xp
        key = (cp.cuda.get_current_stream().ptr, key)
        st = self._stages.get(key)
        if st is not None and st[4] is not None:
            st[4].synchronize()            # (the copy from it before is done: it can be written again)
        if st is None or st[0] < nbytes:
            size = max(nbytes, 2 * st[0] if st else 1 << 16)
            mem = cp.cuda.alloc_pinned_memory(size)
            st = [size, mem, np.frombuffer(mem, np.uint8, size), cp.empty(size, cp.uint8), None]
            self._stages[key] = st
        return st

    def _up(self, key, a: np.ndarray):
        """``a`` (int32) on the card: a copy queued on the current stream."""
        cp = self.xp
        b = np.ascontiguousarray(a, np.int32).reshape(-1).view(np.uint8)
        st = self._stage(key, b.nbytes)
        host, dev = st[2], st[3]
        host[:b.nbytes] = b
        cp.cuda.runtime.memcpyAsync(dev.data.ptr, host.ctypes.data, b.nbytes, cp.cuda.runtime.memcpyHostToDevice,
                                    cp.cuda.get_current_stream().ptr)
        if st[4] is None:
            st[4] = cp.cuda.Event(block=False, disable_timing=True)
        st[4].record()
        return dev[:b.nbytes].view(cp.int32)

    def _down(self, key, d, count: int):
        """The first ``count`` int32 of ``d`` brought back (queued): a function that waits for them."""
        cp = self.xp
        st = cp.cuda.get_current_stream()
        nb = 4 * count
        host = self._stage(key, nb)[2]
        cp.cuda.runtime.memcpyAsync(host.ctypes.data, d.data.ptr, nb, cp.cuda.runtime.memcpyDeviceToHost, st.ptr)

        def wait():
            st.synchronize()
            return host[:nb].view(np.int32).copy()
        return wait

    def get(self, a):
        return a.get()

    def search(self, plates, jobs, runs, cores):
        """``jobs``: (plate, mask, x0, nc, nr, rowmajor, kst) -> the best position k of each (INF: none)."""
        return self.search_start(jobs, runs, cores)()

    def search_start(self, jobs, runs, cores):
        """The search started on the current stream: a function that waits for its result."""
        cp = self.xp
        n = len(jobs)
        if n == 0:
            return lambda: []
        buf = np.zeros((JB + 1) * n, np.int32)
        jb = buf[:JB * n].reshape(n, JB)
        ptrs = jb[:, :4].view(np.uint64)
        for j, (pl, mk, x0, nc, nr, rm, kst) in enumerate(jobs):
            ptrs[j] = (pl.N.data.ptr, pl.T.data.ptr)
            jb[j, 4:16] = (pl.W, nc, nr, mk.r0, mk.nruns, mk.c0, mk.ncores, rm, pl.Wb + 1, x0, kst, 0)
        buf[JB * n:] = INF
        g = self._up("search", buf)
        self.k["search"]((GRID, n), (256,), (g, np.int32(n), np.int32(0), runs, cores))
        wait = self._down("found", g[JB * n:], n)
        return lambda: wait().tolist()

    def stamp(self, jobs, GE=None):
        """``jobs``: (plate, mask, fx, fy): the parts marked on their plates where they lie (their reference at fx, fy
        pixels of the plate), all at once; ``GE``: the grown outlines (see :class:`_Mask`)."""
        rows = []
        for pl, sm, fx, fy in jobs:
            a0, b0, a1, b1 = sm.window(pl, fx, fy)
            if a1 > a0 and b1 > b0:
                rows.append((pl, sm, fx, fy, a0, b0, a1, b1))
        if not rows:
            return
        n = len(rows)
        si = np.zeros((n, 26), np.int32)                 # (26: the pointers stay 8-byte aligned)
        ptrs = si[:, :10].view(np.uint64)
        for k, (pl, sm, fx, fy, a0, b0, a1, b1) in enumerate(rows):
            ptrs[k] = (pl.occ.data.ptr, pl.N.data.ptr, pl.C.data.ptr, pl.T.data.ptr, 0)
            si[k, 10:23] = (pl.W, pl.H, pl.Wb, pl.Hb, 0, 0, 0, a0, b0, a1, b1, sm.g0, sm.gn)
            si[k, 23:25] = np.array([fx, fy], np.float32).view(np.int32)
        Q = self._up("stamp", si)
        win = int(max((r[6] - r[4]) * (r[7] - r[5]) for r in rows))
        hmax = int(max(r[7] - r[5] for r in rows))
        hb = int(max(r[0].Hb for r in rows))
        wb = int(max(r[0].Wb for r in rows))
        k = self.k
        k["stamp_poly"]((hmax, n), (256,), (Q, GE))
        k["row_next"]((hmax, n), (256,), (Q,))
        k["coarse"]((-(-(win // (BLOCK * BLOCK) + 64) // 256), n), (256,), (Q,))
        k["table_rows"]((-(-hb // 128), n), (128,), (Q,))
        k["table_cols"]((-(-(wb + 1) // 128), n), (128,), (Q,))

    def slide(self, tasks, start, E):
        """``tasks``: rows of (job, m0, nm, o0, no, dir, ox, oy); ``start``: the distance to the plate's edge per job -> the
        distance each job's part can go."""
        return self.slide_start(tasks, start, E)()

    def slide_start(self, tasks, start, E):
        cp = self.xp
        if not len(tasks):
            return lambda: [float(v) for v in start]
        n = len(tasks)
        buf = np.zeros(8 * n + len(start), np.int32)
        tb = buf[:8 * n].reshape(n, 8)
        tb[:, :6] = tasks[:, :6]
        tb[:, 6:8] = tasks[:, 6:8].astype(np.float32).view(np.int32)
        buf[8 * n:] = np.asarray(start, np.float32).view(np.int32)
        g = self._up("slide", buf)
        self.k["slide"]((n,), (256,), (g, np.int32(n), E))
        wait = self._down("slid", g[8 * n:], len(start))
        return lambda: wait().view(np.float32).tolist()


class _Numpy:
    """The same work on NumPy, a position chunk at a time in the gravity's order (for the tests and as a reference)."""
    name = "numpy"

    def __init__(self):
        self.xp = np

    def asarray(self, a):
        return np.asarray(a)

    def get(self, a):
        return np.asarray(a)

    def search_start(self, jobs, runs, cores):
        out = self.search(None, jobs, runs, cores)
        return lambda: out

    def slide_start(self, tasks, start, E):
        out = self.slide(tasks, start, E)
        return lambda: out

    def search(self, plates, jobs, runs, cores):
        out = []
        for pl, mk, x0, nc, nr, rm, kst in jobs:
            total = nc * nr
            found = INF
            R = runs[mk.r0:mk.r0 + mk.nruns].tolist()
            C = cores[mk.c0:mk.c0 + mk.ncores].tolist()
            T, NX = pl.T, pl.N
            for k0 in range(kst, total, 1 << 16):
                # the cores of a whole chunk of positions at once, then the runs of the ones left, a few at a time in order
                k = np.arange(k0, min(total, k0 + (1 << 16)))
                x = x0 + (k % nc if rm else k // nr)
                y = k // nc if rm else k % nr
                ok = np.ones(len(k), bool)
                for r0_, c0_, r1_, c1_ in C:
                    bx0, bx1 = (x + c0_ + BLOCK - 1) // BLOCK, (x + c1_) // BLOCK
                    by0, by1 = (y + r0_ + BLOCK - 1) // BLOCK, (y + r1_) // BLOCK
                    use = (bx1 > bx0) & (by1 > by0)
                    ok &= ~(use & ((T[by1, bx1] - T[by0, bx1] - T[by1, bx0] + T[by0, bx0]) != 0))
                live = np.nonzero(ok)[0]
                for s0 in range(0, len(live), 512):
                    sel = live[s0:s0 + 512]
                    xs, ys = x[sel], y[sel]
                    good = np.ones(len(sel), bool)
                    for r, a, b in R:
                        good &= NX[r + ys, a + xs] >= b - a
                        if not good.any():
                            break
                    if good.any():
                        found = int(k[sel[np.argmax(good)]])
                        break
                if found < INF:
                    break
            out.append(found)
        return out

    def stamp(self, jobs, GE=None):
        for pl, sm, fx, fy in jobs:
            pl.stamp_at(sm, fx, fy, GE)

    def slide(self, tasks, start, E):
        out = list(np.asarray(start, np.float64))
        for job, m0, nm, o0, no, d, ox, oy in tasks.tolist():
            job, m0, nm, o0, no, d = int(job), int(m0), int(nm), int(o0), int(no), int(d)
            ax = d & 1
            sg = 1.0 if d >= 2 else -1.0
            M = E[m0:m0 + nm].astype(np.float64)
            O = E[o0:o0 + no].astype(np.float64) + (ox, oy, ox, oy)
            for V, Ed, s in ((M[:, :2], O, sg), (O[:, :2], M, -sg)):
                va, vb = (V[:, 1], V[:, 0]) if ax else (V[:, 0], V[:, 1])
                pa, pb = (Ed[:, 1], Ed[:, 0]) if ax else (Ed[:, 0], Ed[:, 1])
                qa, qb = (Ed[:, 3], Ed[:, 2]) if ax else (Ed[:, 2], Ed[:, 3])
                vb_, va_ = vb[:, None], va[:, None]
                cross = (pb != qb)[None, :] & ((pb[None, :] - vb_) * (qb[None, :] - vb_) <= 0)
                with np.errstate(divide="ignore", invalid="ignore"):
                    ca = pa[None, :] + (vb_ - pb[None, :]) * (qa - pa)[None, :] / (qb - pb)[None, :]
                t = s * (ca - va_)
                ok = cross & (t >= -2)
                if ok.any():
                    out[job] = min(out[job], max(0.0, float(t[ok].min())))
        return out


def backend(name: str = "cupy"):
    return _Cupy() if name == "cupy" else _Numpy()


# -- bitmaps -----------------------------------------------------------------------------------------------------------

def raster(solid: list, ox: int, oy: int, w: int, h: int, px: int, grow: float) -> np.ndarray:
    """``h`` x ``w`` booleans: the pixels within ``grow`` pixels of the solid (outlines counter-clockwise, holes
    clockwise, non-zero), pixel (0, 0) at (ox, oy) units. 0.7072 takes every pixel the solid touches."""
    off = pyclipper.PyclipperOffset()
    off.ArcTolerance = max(1.0, px / 20)
    try:
        off.AddPaths(solid, pyclipper.JT_ROUND, pyclipper.ET_CLOSEDPOLYGON)
    except pyclipper.ClipperException:
        return np.zeros((h, w), bool)
    grown = off.Execute(int(px * grow) + 2)
    D = np.zeros((h, w + 1), np.int32)
    rows, cols, dirs = [], [], []
    for path in grown:
        P = np.asarray(path, np.float64)
        if len(P) < 3:
            continue
        Q = np.roll(P, -1, axis=0)
        y0, y1, x0, x1 = P[:, 1], Q[:, 1], P[:, 0], Q[:, 0]
        keep = y0 != y1
        y0, y1, x0, x1 = y0[keep], y1[keep], x0[keep], x1[keep]
        d = np.where(y1 > y0, 1, -1)
        lo, hi = np.minimum(y0, y1), np.maximum(y0, y1)
        r0 = np.maximum(0, np.ceil((lo - oy) / px - 0.5)).astype(np.int64)            # rows whose centre is in [lo, hi)
        r1 = np.minimum(h - 1, np.ceil((hi - oy) / px - 0.5).astype(np.int64) - 1)
        cnt = np.maximum(0, r1 - r0 + 1)
        if not cnt.sum():
            continue
        e = np.repeat(np.arange(len(cnt)), cnt)
        r = r0[e] + (np.arange(cnt.sum()) - np.repeat(np.cumsum(cnt) - cnt, cnt))
        yc = oy + (r + 0.5) * px
        xc = x0[e] + (yc - y0[e]) * (x1[e] - x0[e]) / (y1[e] - y0[e])
        rows.append(r)
        cols.append(np.clip(np.ceil((xc - ox) / px - 0.5).astype(np.int64), 0, w))     # the first centre right of it
        dirs.append(d[e])
    if rows:
        np.add.at(D, (np.concatenate(rows), np.concatenate(cols)), np.concatenate(dirs))
    return np.cumsum(D, axis=1)[:, :w] != 0


def _cores(m: np.ndarray, most: int = 8) -> list[tuple[int, int, int, int]]:
    """A few big rectangles inside the bitmap ``(row0, col0, row1, col1)``, ends excluded: for rows (and columns) taken
    every few, the longest run grown up and down while it stays inside; the biggest ones, not much inside one another."""
    cand = []
    for axis in (0, 1):
        mm = m if axis == 0 else m.T
        n = mm.shape[0]
        if n == 0:
            continue
        cs = np.cumsum(mm, axis=1, dtype=np.int32)
        cs = np.concatenate((np.zeros((n, 1), np.int32), cs), axis=1)
        for r in sorted(set(np.linspace(0, n - 1, min(n, 24)).astype(int).tolist()) | {n // 2}):
            row = np.concatenate(([0], mm[r].astype(np.int8), [0]))
            d = np.diff(row)
            st, en = np.nonzero(d == 1)[0], np.nonzero(d == -1)[0]
            if not len(st):
                continue
            k = int(np.argmax(en - st))
            a, b = int(st[k]), int(en[k])
            full = (cs[:, b] - cs[:, a]) == (b - a)
            lo = hi = r
            while lo > 0 and full[lo - 1]:
                lo -= 1
            while hi + 1 < n and full[hi + 1]:
                hi += 1
            rect = (lo, a, hi + 1, b) if axis == 0 else (a, lo, b, hi + 1)
            cand.append(((rect[2] - rect[0]) * (rect[3] - rect[1]), rect))
    out: list = []
    for area, r in sorted(cand, reverse=True):
        if area <= 0 or len(out) >= most:
            break
        if any(r[0] >= o[0] and r[1] >= o[1] and r[2] <= o[2] and r[3] <= o[3] for o in out):
            continue
        out.append(r)
    return out


def _runs(m: np.ndarray) -> list[tuple[int, int, int]]:
    """The runs of a bitmap, ``(row, first, end)``, coarse to fine: the first row, the middle, the quarters, the eighths
    ...: wherever a part meets another, a few runs find it (from the middle out took 45 on average at the edge of the
    parts placed)."""
    h, w = m.shape
    pad = np.zeros((h, w + 2), np.int8)
    pad[:, 1:-1] = m
    dd = np.diff(pad, axis=1)
    rs, a_ = np.nonzero(dd == 1)                    # (row-major: the starts and the ends of a row pair up)
    _, b_ = np.nonzero(dd == -1)
    runs = list(zip(rs.tolist(), a_.tolist(), b_.tolist()))

    def spread(r):
        tz = (r & -r).bit_length() if r else 64
        return (-tz, r)
    runs.sort(key=lambda t: spread(t[0]))
    return runs


def touched(solid: list, px: int) -> list:
    """The outline grown by the pixel square (half a pixel each way, and a hair more): a pixel's centre is inside it when
    the solid covers some of the pixel."""
    h = px // 2 + 2
    sq = [(-h, -h), (h, -h), (h, h), (-h, h)]
    c = pyclipper.Pyclipper()
    try:
        c.AddPaths(solid, pyclipper.PT_SUBJECT, True)
        for path in solid:
            if len(path) >= 2:
                c.AddPaths(pyclipper.MinkowskiSum(sq, path, True), pyclipper.PT_CLIP, True)
        return c.Execute(pyclipper.CT_UNION, pyclipper.PFT_NONZERO, pyclipper.PFT_NONZERO)
    except pyclipper.ClipperException:
        return []


class _Mask:
    """A part at an angle as a bitmap: where its pixel (0, 0) lies from the part's reference (``ox``, ``oy``, units), its
    size in pixels, its runs and its cores (in the backend's arrays: ``r0``, ``nruns``, ``c0``, ``ncores``). Its pixels
    are the ones it covers some of (``grown``: its outline grown by the pixel square); a part placed is marked where it
    lies the same way, from those edges (``g0``, ``gn`` in the backend's array, pixels from the reference; ``gbox``)."""

    def __init__(self, v, px: int):
        bx0, by0, bx1, by1 = v.bbox
        g = 2 * px
        self.ox, self.oy = bx0 - g, by0 - g
        self.w = (bx1 - bx0 + 2 * g) // px + 1
        self.h = (by1 - by0 + 2 * g) // px + 1
        self.grown = touched(v.solid, px)
        m = raster(self.grown, self.ox, self.oy, self.w, self.h, px, 0.0)
        segs = [np.concatenate((P, np.roll(P, -1, axis=0)), axis=1)
                for P in (np.asarray(path, np.float64) / px for path in self.grown) if len(P) >= 3]
        self.edges = np.concatenate(segs) if segs else np.zeros((0, 4))
        pts = self.edges[:, :2] if len(self.edges) else np.zeros((1, 2))
        self.gbox = (float(pts[:, 0].min()), float(pts[:, 1].min()), float(pts[:, 0].max()), float(pts[:, 1].max()))
        self.g0 = self.gn = 0
        runs = _runs(m)
        self.runs = runs
        self.cores = _cores(m)
        self.r0 = self.nruns = self.c0 = self.ncores = 0

    def window(self, pl, fx: float, fy: float):
        """The pixels of plate ``pl`` that its stamp at ``(fx, fy)`` can mark: ``(a0, b0, a1, b1)``, ends excluded."""
        x0, y0, x1, y1 = self.gbox
        return (max(0, int(math.floor(fx + x0)) - 1), max(0, int(math.floor(fy + y0)) - 1),
                min(pl.W, int(math.ceil(fx + x1)) + 2), min(pl.H, int(math.ceil(fy + y1)) + 2))


class _Plate:
    """A plate on the card: its bitmap (``occ``), per pixel how far the next taken pixel along its row is (``N``, 0: taken;
    a run of a part is free where it is at least the run's length), the coarse table of the occupied pixels in blocks of
    BLOCK x BLOCK (``T``, summed area)."""

    def __init__(self, be, rect, px: int):
        xp = be.xp
        self.be = be
        self.x0, self.y0, x1, y1 = rect
        self.px = px
        self.Wr = max(0, (x1 - self.x0) // px)                  # the pixels wholly on the plate
        self.Hr = max(0, (y1 - self.y0) // px)
        self.W = -(-max(1, self.Wr) // CELL) * CELL
        self.H = -(-max(1, self.Hr) // CELL) * CELL
        self.Wb, self.Hb = self.W // BLOCK, self.H // BLOCK
        self.occ = xp.zeros((self.H, self.W), xp.uint8)
        self.N = xp.zeros((self.H, self.W), xp.uint16)
        self.T = xp.zeros((self.Hb + 1, self.Wb + 1), xp.int32)
        self.reset()

    def reset(self):
        """Empty: only what is off the plate taken."""
        xp = self.be.xp
        self.lb: dict = {}             # (class, angle, row-major) -> the first position that can still be free
        self.occ[:] = 0
        self.occ[:, self.Wr:] = 1
        self.occ[self.Hr:, :] = 1
        self.N[:self.Hr] = xp.maximum(0, self.Wr - xp.arange(self.W)).astype(xp.uint16)[None, :]
        self.N[self.Hr:] = 0
        self._blocks(0, self.H)

    def arrays(self) -> list:
        return [self.occ, self.N, self.C, self.T]

    def restore(self, empty: list):
        """Empty again, from the arrays of an empty plate of the same size (copied on the card)."""
        self.lb = {}
        for a, b in zip(self.arrays(), empty):
            a[...] = b

    def _rows(self, j0: int, j1: int):
        o = np.asarray(self.occ[j0:j1])
        idx = np.where(o != 0, np.arange(self.W)[None, :], self.W)
        nxt = np.minimum.accumulate(idx[:, ::-1], axis=1)[:, ::-1]
        self.N[j0:j1] = (nxt - np.arange(self.W)[None, :]).astype(np.uint16)
        self._blocks(j0, j1)

    def _blocks(self, j0: int, j1: int):
        xp = self.be.xp
        b0, b1 = j0 // BLOCK, -(-j1 // BLOCK)
        blk = self.occ[b0 * BLOCK:b1 * BLOCK].reshape(b1 - b0, BLOCK, self.Wb, BLOCK).sum(axis=(1, 3), dtype=xp.int32)
        if not hasattr(self, "C"):
            self.C = xp.zeros((self.Hb, self.Wb), xp.int32)
        self.C[b0:b1] = blk
        self.T[1:, 1:] = xp.cumsum(xp.cumsum(self.C, axis=0, dtype=xp.int32), axis=1, dtype=xp.int32)

    def stamp_at(self, sm, fx: float, fy: float, GE):
        """The kernel stamp_poly on NumPy (float32 the same way: the same pixels)."""
        a0, b0, a1, b1 = sm.window(self, fx, fy)
        if a1 <= a0 or b1 <= b0:
            return
        E = np.asarray(GE[sm.g0:sm.g0 + sm.gn], np.float32)
        f32 = np.float32
        fx, fy = f32(fx), f32(fy)
        y0, y1 = E[:, 1] + fy, E[:, 3] + fy
        xa, xb = E[:, 0] + fx, E[:, 2] + fx
        for r in range(b0, b1):
            yc = f32(r) + f32(0.5)
            m = (y0 <= yc) != (y1 <= yc)
            if not m.any():
                continue
            x = xa[m] + (yc - y0[m]) * (xb[m] - xa[m]) / (y1[m] - y0[m])
            d = np.where(y1[m] > y0[m], 1, -1)
            row = self.occ[r]
            if len(x) > 128:
                c0, c1 = max(a0, int(np.floor(x).min()) - 1), min(a1, int(np.ceil(x).max()) + 2)
                if c1 > c0:
                    row[c0:c1] = 1
                continue
            o = np.argsort(x, kind="stable")
            x, w = x[o], np.cumsum(d[o])
            for i in range(len(x) - 1):
                c0, c1 = max(a0, int(np.ceil(x[i] - f32(0.5)))), min(a1, int(np.ceil(x[i + 1] - f32(0.5))))
                if w[i] and c1 > c0:
                    row[c0:c1] = 1
        self._rows(b0, b1)


# -- the attempts -------------------------------------------------------------------------------------------------------

def supported(n) -> str | None:
    """Why this nesting cannot be done on the card (it then goes the usual way), or ``None``."""
    st = n.st
    if st.arrange != "free":
        return "rows and columns are laid out the usual way"
    if st.squeeze_angle > 0 or st.squeeze_margin > 0:
        return "squeezing in is done the usual way"
    if st.min_leftover > 0:
        return "the least leftover is kept the usual way"
    if any(b.rect is None for b in n.boxes if b.usable):
        return "a plate that is not a rectangle (or has parts on it) is filled the usual way"
    if st.gravity_only and st.gravity not in GRAVITIES:
        return f"the gravity {st.gravity} is the usual way's"
    return None


class _Attempt:
    """One layout being made: the run_order / _fill of the nester, a part at a time.

    ``base``: the plates of another layout that this one goes on from (``order``: the parts not on them); ``plates``: how
    many plates more are filled, the parts left over then stay in ``remaining`` (``None``: all of them, as the nester)."""

    def __init__(self, n, order, grav, base=None, plates=None):
        self.n, self.order, self.grav = n, list(order), grav
        self.group = 0                   # (which of the groups made by turns it is in: its plates are that group's)
        self.made: dict[int, int] = {}
        if base is None:
            self.res = n_result()
            self.remaining = [it for it in order if n.angles[it[0]]]
            self.res.unplaced = [it for it in order if not n.angles[it[0]]]
        else:
            self.res = copy_result(base)
            self.remaining = list(order)
            for b in self.res.stock:
                self.made[b] = self.made.get(b, 0) + 1
        self.until = None if plates is None else self.res.sheets + plates
        self.pool = n._plates()
        self.plate = None
        self.done = False
        self._next_plate()

    def _next_plate(self):
        n = self.n
        while True:
            self.pool = [b for b in self.pool if b.c.quantity <= 0 or self.made.get(b.index, 0) < b.c.quantity]
            if self.remaining and self.pool and self.until is not None and self.res.sheets >= self.until:
                self.done = True               # (as many plates as asked: the parts left stay in ``remaining``)
                self.plate = None
                return
            if not self.remaining or not self.pool or self.res.sheets >= n.st.max_sheets:
                self.res.unplaced += self.remaining
                self.remaining = []
                self.done = True
                self.plate = None
                return
            self.box = self.pool[0]
            self.plate = None              # (made when the first part is placed: the slot is lent then)
            self.placed: list[tuple] = []
            self.items: list[tuple] = []
            # per part placed: its bounding box (units), its edges (first, count) and where it is
            self.info = np.zeros((64, 8), np.float64)
            self.used = None
            self.free = self.box.free
            self.dead: set = set()
            self.left: list = []
            self.k = 0
            return

    def end_pass(self, give_back):
        """The pass over the parts left is over: the plate is done (or nothing fits on such a plate)."""
        n = self.n
        if self.placed:
            S = clip.SCALE
            self.res.used.append(tuple(v / S for v in self.used))
            self.res.layout.append(self.items)
            self.res.stock.append(self.box.index)
            self.res.units.append(self.made.get(self.box.index, 0))
            self.made[self.box.index] = self.made.get(self.box.index, 0) + 1
            self.res.sheets += 1
            a = sum(n.areas[it[0]] for it in self.items)
            self.res.utilization.append(a / self.box.free if self.box.free > 0 else 0.0)
            self.remaining = self.left
        else:
            self.pool.pop(0)
        if self.plate is not None:
            give_back(self.plate)
            self.plate = None
        self._next_plate()

    def next_item(self):
        """The next part to place on this plate, or ``None`` when the pass is over."""
        n = self.n
        while self.k < len(self.remaining):
            item = self.remaining[self.k]
            self.k += 1
            si = item[0]
            if n.areas[si] > self.free + 1e-6 or n.skey[si] in self.dead:
                self.left.append(item)
                continue
            return item
        return None


def n_result():
    from .nest import NestResult
    return NestResult()


def copy_result(r):
    """A NestResult with its own lists (the plates of ``r`` that another layout goes on from)."""
    out = n_result()
    out.placements, out.sheets, out.unplaced = list(r.placements), r.sheets, list(r.unplaced)
    out.utilization, out.stock, out.units, out.used = list(r.utilization), list(r.stock), list(r.units), list(r.used)
    out.layout = [list(x) for x in r.layout]
    return out


class GpuNester:
    """The layouts of a :class:`Nester` on the card (or on NumPy)."""

    def __init__(self, n, be=None, pixel: float = 0.0, slots: int = 32):
        self.n = n
        self.be = be or backend("cupy")
        boxes = [b for b in n.boxes if b.usable]
        side = max(max(b.rect[2] - b.rect[0], b.rect[3] - b.rect[1]) for b in boxes) / clip.SCALE
        if pixel <= 0:   # about 2500 pixels along the plate: 1 mm on a 2440 plate, finer on a small one
            pixel = min(2.0, max(0.25, side / 2500.0))
        self.pixel = pixel
        self.px = int(round(pixel * clip.SCALE))
        self.slots = max(1, slots)
        if self.be.name == "cupy":
            # (as many attempts at a time as the card's memory takes: a plate is its bitmap, its distances and tables,
            # and the attempts of both groups hold one each, with as many again kept for the next ones)
            W = max(b.rect[2] - b.rect[0] for b in boxes) // self.px + 2 * CELL
            H = max(b.rect[3] - b.rect[1] for b in boxes) // self.px + 2 * CELL
            per = W * H * 3.2
            try:
                free = self.be.xp.cuda.runtime.memGetInfo()[0] + self.be.xp.get_default_memory_pool().free_bytes()
            except Exception:  # noqa: BLE001
                free = 1 << 30
            self.slots = max(2, min(self.slots, int(0.6 * free / (2 * per))))
        self.masks: dict = {}
        self._runs: list = []
        self._cores: list = []
        self._edges: list = []           # per (class, angle): (first, count) in E
        self._eidx: dict = {}
        self._E: list = []
        self._GE: list = []               # the masks' grown outlines (pixels, from the reference), for the stamps
        self._free: dict = {}             # plates made and not in use, per (group, box)
        self._streams: list = []
        self._empty: dict = {}            # per box: the arrays of an empty plate
        self.stats = {"searches": 0, "slides": 0, "steps": 0, "attempts": 0}

    # (the masks and the outlines of every part at every angle, made once)
    def prepare(self, check: Callable[[], None] = lambda: None):
        n = self.n
        for si in range(len(n.shapes)):
            c = n.cls[si]
            for ang in n.angles[si]:
                if (c, ang) in self.masks:
                    continue
                check()
                v = n._variant(c, ang)
                mk = _Mask(v, self.px)
                mk.r0, mk.nruns = len(self._runs), len(mk.runs)
                self._runs += mk.runs
                mk.c0, mk.ncores = len(self._cores), len(mk.cores)
                self._cores += mk.cores
                mk.g0, mk.gn = sum(len(e) for e in self._GE), len(mk.edges)
                self._GE.append(mk.edges)
                self.masks[(c, ang)] = mk
                segs = []
                for p in v.solid:
                    P = np.asarray(p, np.float64)
                    Q = np.roll(P, -1, axis=0)
                    segs.append(np.concatenate((P, Q), axis=1))
                seg = np.concatenate(segs) if segs else np.zeros((0, 4))
                self._eidx[(c, ang)] = (sum(len(e) for e in self._E), len(seg))
                self._E.append(seg)
        xp = self.be.xp
        self.runs = xp.asarray(np.array(self._runs or [(0, 0, 0)], np.int32).reshape(-1, 3))
        self.cores = xp.asarray(np.array(self._cores or [(0, 0, 0, 0)], np.int32).reshape(-1, 4))
        E = np.concatenate(self._E) if self._E else np.zeros((1, 4))
        self.E = xp.asarray(E.astype(np.float32)) if self.be.name == "cupy" else E
        GE = np.concatenate(self._GE) if self._GE else np.zeros((1, 4))
        self.GE = xp.asarray(GE.astype(np.float32)) if self.be.name == "cupy" else GE.astype(np.float32)

    def _plate(self, box, group: int = 0):
        free = self._free.setdefault((group, box.index), [])
        if free:
            pl = free.pop()
            pl.restore(self._empty[box.index])
            return pl
        pl = _Plate(self.be, box.rect, self.px)
        pl.box, pl.group = box, group
        if box.index not in self._empty:
            self._empty[box.index] = [a.copy() for a in pl.arrays()]
        return pl

    def release(self):
        """The card's memory given back (the plates, and what CuPy keeps for the next arrays): a nesting after this one
        in the same program gets all of it; on Windows a card short of memory pages it out, many times slower."""
        self._free.clear()
        self._empty.clear()
        self._streams.clear()
        self.masks.clear()
        self.runs = self.cores = self.E = self.GE = None
        if self.be.name == "cupy":
            cp = self.be.xp
            cp.cuda.Device().synchronize()
            self.be._stages.clear()
            cp.get_default_memory_pool().free_all_blocks()
            cp.get_default_pinned_memory_pool().free_all_blocks()

    def _give_back(self, pl):
        self._free.setdefault((pl.group, pl.box.index), []).append(pl)

    # -- one step of every attempt ---------------------------------------------------------------------------------
    def _search(self, reqs):
        """``reqs``: (attempt, item) -> per request the best ``(key, angle, x, y)`` or ``None``."""
        return self._search_end(reqs, self._search_start(reqs))

    def _search_start(self, reqs):
        n = self.n
        px = self.px
        jobs, meta = [], []
        for q, (at, item) in enumerate(reqs):
            si = item[0]
            c = n.cls[si]
            if at.plate is None:
                at.plate = self._plate(at.box, at.group)
            pl = at.plate
            g = at.grav
            for ang in n.angles[si]:
                mk = self.masks[(c, ang)]
                nc, nr = pl.Wr - mk.w + 1, pl.Hr - mk.h + 1
                if nc <= 0 or nr <= 0:
                    continue
                v = n._variant(c, ang)
                if g in ("reach_x", "reach_y") and at.used is not None:
                    # the positions that do not make the layout longer first (lowest, then leftmost), then the others
                    ax = 0 if g == "reach_x" else 1
                    far = at.used[2 + ax]
                    lim = (far - v.bbox[2 + ax] + (mk.ox if ax == 0 else mk.oy) - (pl.x0 if ax == 0 else pl.y0)) // px
                    lim = int(max(-1, min(lim, (nc if ax == 0 else nr) - 1)))   # (-1: every position is past it)
                    if lim >= 0:
                        if ax == 0:
                            jobs.append((pl, mk, 0, lim + 1, nr, 1, pl.lb.get((c, ang, "in", lim), 0)))
                        else:
                            jobs.append((pl, mk, 0, nc, lim + 1, 0, pl.lb.get((c, ang, "in", lim), 0)))
                        meta.append((q, ang, mk, 0, lim + 1, nr, 1, 0) if ax == 0 else (q, ang, mk, 0, nc, lim + 1, 0, 0))
                    if lim + 1 < (nc if ax == 0 else nr):
                        if ax == 0:
                            got = pl.lb.get((c, ang, "past"))       # (column, row) of the last one found past it
                            kst = 0
                            if got is not None and got[0] >= INF:
                                kst = INF
                            elif got is not None and got[0] >= lim + 1:
                                kst = min(INF, (got[0] - lim - 1) * nr + got[1])
                            jobs.append((pl, mk, lim + 1, nc - lim - 1, nr, 0, kst))
                            meta.append((q, ang, mk, lim + 1, nc - lim - 1, nr, 0, 1))
                        else:
                            jobs.append((pl, mk, 0, nc, nr, 1, 0))  # (rows past the limit: row-major from the start)
                            meta.append((q, ang, mk, 0, nc, nr, 1, 1))
                    continue
                rm = 1 if g in ("bottom", "bottom_far", "reach_y") else 0
                jobs.append((pl, mk, 0, nc, nr, rm, pl.lb.get((c, ang, rm), 0)))
                meta.append((q, ang, mk, 0, nc, nr, rm, 0))
        self.stats["searches"] += len(jobs)
        return jobs, meta, self.be.search_start(jobs, self.runs, self.cores)

    def _search_end(self, reqs, started):
        n = self.n
        px = self.px
        jobs, meta, wait = started
        found = wait()
        best: list = [None] * len(reqs)
        for (q, ang, mk, x0, nc, nr, rm, phase), (job, k) in zip(meta, zip(jobs, found)):
            at, item = reqs[q]
            pl = at.plate
            c = n.cls[item[0]]
            if x0 == 0 and nc == pl.Wr - mk.w + 1 and nr == pl.Hr - mk.h + 1:
                pl.lb[(c, ang, rm)] = k        # (a search of the whole plate: what is before it stays taken)
            elif phase == 0:
                pl.lb[(c, ang, "in", nc - 1 if rm else nr - 1)] = k    # (reach, inside the far edge: this limit only)
            elif x0 > 0:
                pl.lb[(c, ang, "past")] = (INF, 0) if k >= INF else (x0 + k // nr, k % nr)
            if k >= INF:
                continue
            i = x0 + (k % nc if rm else k // nr)
            j = k // nc if rm else k % nr
            c = n.cls[item[0]]
            x = pl.x0 + i * px - mk.ox
            y = pl.y0 + j * px - mk.oy
            key = self._key(at, c, ang, x, y)
            if best[q] is None or key < best[q][0]:
                best[q] = (key, ang, x, y)
        return best

    def _key(self, at, c, ang, x, y):
        """The gravity's score of a position (less is better), as :meth:`Nester._keys`."""
        bx0, by0, bx1, by1 = self.n._variant(c, ang).bbox
        g = at.grav
        if g == "bottom":
            return (y + by0, x + bx0)
        if g == "bottom_far":
            return (y + by1, x + bx0)
        if g == "left_far":
            return (x + bx1, y + by0)
        if g == "reach_x":
            far = at.used[2] if at.used is not None else x + bx1
            return (max(far, x + bx1), (y + by0) + 1e-6 * (x + bx0))
        if g == "reach_y":
            far = at.used[3] if at.used is not None else y + by1
            return (max(far, y + by1), (x + bx0) + 1e-6 * (y + by0))
        return (x + bx0, y + by0)

    def _slides(self, moves):
        """``moves``: (attempt, class, angle, x, y, dir) -> how far each can go (units): the parts placed on its plate
        that lie in the box it sweeps on the way to the plate's edge, each a task of the kernel."""
        n = self.n
        m = len(moves)
        M = np.array([(x + v.bbox[0], y + v.bbox[1], x + v.bbox[2], y + v.bbox[3], x, y, d) + at.box.rect + self._eidx[(c, a)]
                      for at, c, a, x, y, d in moves for v in (n._variant(c, a),)], np.float64)
        d = M[:, 6].astype(np.int64)
        wall = np.choose(d, (M[:, 0] - M[:, 7], M[:, 1] - M[:, 8], M[:, 9] - M[:, 2], M[:, 10] - M[:, 3]))
        start = np.maximum(0.0, wall)
        sweep = M[:, :4].copy()                  # (the box the part sweeps)
        go = wall > 0
        for k, col in ((0, 0), (1, 1), (2, 2), (3, 3)):
            sel = go & (d == k)
            sweep[sel, col] += (-wall if k < 2 else wall)[sel]
        counts = np.array([len(at.placed) if g_ else 0 for (at, *_), g_ in zip(moves, go.tolist())], np.int64)
        self.stats["slides"] += m
        if not counts.sum():
            return self.be.slide_start(np.zeros((0, 8)), start, self.E)
        P = np.concatenate([at.info[:k] for (at, *_), k in zip(moves, counts.tolist()) if k])
        own = np.repeat(np.arange(m), counts)
        S = sweep[own]
        near = (P[:, 2] >= S[:, 0]) & (P[:, 0] <= S[:, 2]) & (P[:, 3] >= S[:, 1]) & (P[:, 1] <= S[:, 3])
        P, own = P[near], own[near]
        tasks = np.empty((len(own), 8), np.float64)
        tasks[:, 0] = own
        tasks[:, 1:3] = M[own, 11:13]
        tasks[:, 3:5] = P[:, 4:6]
        tasks[:, 5] = d[own]
        tasks[:, 6] = P[:, 6] - M[own, 4]
        tasks[:, 7] = P[:, 7] - M[own, 5]
        return self.be.slide_start(tasks, start, self.E)

    def run(self, plan, check: Callable[[], None], progress: Callable[[int], None] | None = None) -> list:
        """The layouts of ``plan`` (``(order, gravity)`` each), side by side: the NestResults in the same order."""
        return [a.res for a in self.fill(plan, check, progress)]

    def fill(self, plan, check: Callable[[], None], progress: Callable[[int], None] | None = None) -> list:
        """The attempts of ``plan`` made side by side: ``(order, gravity)`` or ``(order, gravity, base, plates)`` each
        (see :class:`_Attempt`). On the card in two groups by turns, each on a stream of its own: while the card works
        on the one, the next step of the other is got ready."""
        n = self.n
        ats = [_Attempt(n, *p) for p in plan]
        self.stats["attempts"] += len(ats)
        groups = 2 if self.be.name == "cupy" and len(ats) >= 8 else 1
        if self.be.name == "cupy":
            while len(self._streams) < groups:
                self._streams.append(self.be.xp.cuda.Stream(non_blocking=True))
        count = [0] * groups

        def told():
            if progress is not None:
                progress(sum(count))
        gens = []
        for k in range(groups):
            part = ats[k::groups]
            for at in part:
                at.group = k
            ctx = self._streams[k] if self.be.name == "cupy" else contextlib.nullcontext()
            gens.append(self._steps(part, ctx, check, count, k, told))
        try:
            while gens:
                for gen in list(gens):
                    try:
                        next(gen)
                    except StopIteration:
                        gens.remove(gen)
        finally:
            if self.be.name == "cupy":
                for st in self._streams:
                    st.synchronize()
        for at in ats:
            if at.plate is not None:
                self._give_back(at.plate)
                at.plate = None
        return ats

    def _steps(self, ats, ctx, check, count, k, told):
        """The steps of a group of attempts, a part each at a time; it stops (yields) where it waits for the card."""
        n = self.n
        active = [a for a in ats if not a.done]
        while active:
            check()
            self.stats["steps"] += 1
            reqs = []
            for at in active:
                item = at.next_item()
                while item is None and not at.done:
                    at.end_pass(self._give_back)
                    item = None if at.done else at.next_item()
                if item is not None:
                    reqs.append((at, item))
            if not reqs:
                active = [a for a in active if not a.done]
                continue
            with ctx:
                started = self._search_start(reqs)
            _flush(ctx)
            yield
            with ctx:
                found = self._search_end(reqs, started)
            # the parts that fit: slid the way the gravity pulls them, a move of all of them at a time
            go = []
            for (at, item), b in zip(reqs, found):
                if b is None:
                    at.dead.add(n.skey[item[0]])
                    at.left.append(item)
                    continue
                _, ang, x, y = b
                go.append([at, item, n.cls[item[0]], ang, x, y])
            if go:
                # (a part that a slide did not move after the first one is in its corner: the slide before it, the other
                # way, already went as far as it could)
                moving = list(go)
                for step in range(SLIDES):
                    moves = []
                    for g in moving:
                        at = g[0]
                        first_down = at.grav.startswith("bottom") or at.grav == "reach_y"
                        d = (step + first_down) & 1
                        moves.append((at, g[2], g[3], g[4], g[5], d))
                    if not moves:
                        break
                    with ctx:
                        wait = self._slides(moves)
                    _flush(ctx)
                    yield
                    with ctx:
                        dist = wait()
                    still = []
                    for g, (_, _, _, _, _, d), t in zip(moving, moves, dist):
                        s = int(t) - BACKOFF
                        if s > 0:
                            if d == 0:
                                g[4] -= s
                            else:
                                g[5] -= s
                            still.append(g)
                        elif step == 0:
                            still.append(g)
                    moving = still
                marks = []
                for at, item, c, ang, x, y in go:
                    marks.append(self._place(at, item, c, ang, x, y))
                    count[k] += 1
                with ctx:
                    self.be.stamp(marks, self.GE)
                _flush(ctx)
            told()
            active = [a for a in active if not a.done]

    def _place(self, at, item, c, ang, x, y):
        n = self.n
        si, ci = item
        v = n._variant(c, ang)
        sm = self.masks[(c, ang)]
        pl = at.plate
        at.placed.append((c, ang, x, y))
        at.items.append((si, ci, c, ang, x, y))
        nb = (x + v.bbox[0], y + v.bbox[1], x + v.bbox[2], y + v.bbox[3])
        k = len(at.placed) - 1
        if k >= len(at.info):
            at.info = np.concatenate((at.info, np.zeros_like(at.info)))
        at.info[k] = nb + self._eidx[(c, ang)] + (x, y)
        at.free -= n.areas[si]
        u = at.used
        at.used = nb if u is None else (min(u[0], nb[0]), min(u[1], nb[1]), max(u[2], nb[2]), max(u[3], nb[3]))
        at.res.placements.append(n._placement(si, ci, at.res.sheets, ang, x, y))
        return pl, sm, (x - pl.x0) / self.px, (y - pl.y0) / self.px


def valid(n, res) -> bool:
    """Whether a layout keeps every part inside its plate and every two the gap apart (exactly, with Clipper)."""
    for k, items in enumerate(res.layout):
        box = n.boxes[res.stock[k]]
        r = box.rect
        bbs = []
        for si, ci, c, ang, x, y in items:
            b = n._variant(c, ang).bbox
            nb = (x + b[0], y + b[1], x + b[2], y + b[3])
            if r is None or nb[0] < r[0] or nb[1] < r[1] or nb[2] > r[2] or nb[3] > r[3]:
                return False
            bbs.append(nb)
        order = sorted(range(len(items)), key=lambda i: bbs[i][0])
        for a_, i in enumerate(order):
            for j in order[a_ + 1:]:
                if bbs[j][0] >= bbs[i][2]:
                    break
                if bbs[j][1] >= bbs[i][3] or bbs[i][1] >= bbs[j][3]:
                    continue
                _, _, c1, a1, x1, y1 = items[i]
                _, _, c2, a2, x2, y2 = items[j]
                if n._overlaps((c1, a1, x1, y1), c2, a2, x2, y2):
                    return False
    return True


def plan_of(n, count: int):
    """The plain attempts of the nester (its orders and gravities) that the card does: compact / diag left out."""
    out = []
    for order, g in n.candidates(max(count, 1) + 8):
        if g in GRAVITIES:
            out.append((order, g))
    return out[:max(1, count)]


def rebuild(n, layout: list, stock: list, unplaced: list):
    """A NestResult from what is on every plate (``layout``: per plate its items; ``stock``: per plate its box)."""
    from .nest import NestResult
    res = NestResult()
    made: dict[int, int] = {}
    for items, b in zip(layout, stock):
        if not items:
            continue
        k = res.sheets
        box = n.boxes[b]
        res.layout.append(list(items))
        res.stock.append(b)
        res.units.append(made.get(b, 0))
        made[b] = made.get(b, 0) + 1
        bb = n._bbox_of(items)
        res.used.append(tuple(v / clip.SCALE for v in bb))
        res.utilization.append(sum(n.areas[it[0]] for it in items) / box.free if box.free > 0 else 0.0)
        for si, ci, c, ang, x, y in items:
            res.placements.append(n._placement(si, ci, k, ang, x, y))
        res.sheets += 1
    res.unplaced = list(unplaced)
    return res


def repair(n, res, grav: str, check: Callable[[], None], most: int = 8, seconds: float = 5.0):
    """The parts of the last plate put on the plates before it the exact way (no-fit polygons, as the usual nesting
    does), when there are few of them: the bitmaps keep the parts a pixel or two further apart than they need, and the
    exact search finds the narrow places they leave. A plate that is emptied so is saved. Returns the better layout (or
    ``res`` as it was). It stops after ``seconds`` (the no-fit polygons are made on this processor alone)."""
    if res.sheets < 2 or len(res.layout[-1]) > most:
        return res
    layout = [list(items) for items in res.layout]
    until = time.perf_counter() + seconds
    saved = n.gravity
    n.gravity = grav
    try:
        last = layout[-1]
        for it in sorted(last, key=lambda it: -n.areas[it[0]]):
            si, ci = it[0], it[1]
            for k in range(len(layout) - 1):
                check()
                if time.perf_counter() > until:
                    break
                box = n.boxes[res.stock[k]]
                if n.areas[si] > box.free - sum(n.areas[q[0]] for q in layout[k]) + 1e-6:
                    continue
                placed = [(c, a, x, y) for _, _, c, a, x, y in layout[k]]
                cand = n._try_sheet(box, placed, si, n._bbox_of(layout[k]))
                if cand is not None:
                    _, a, x, y = cand
                    layout[k].append((si, ci, n.cls[si], a, x, y))
                    last.remove(it)
                    break
    finally:
        n.gravity = saved
    if last and len(last) == len(res.layout[-1]):
        return res
    new = rebuild(n, layout, res.stock, res.unplaced)
    return new if n.quality(new) < n.quality(res) else res


def _mutate(order: list, rng: random.Random) -> list:
    """A few changes of an order: a part brought to the front, a stretch shuffled, neighbours swapped, or many swaps."""
    cand = list(order)
    if len(cand) < 2:
        return cand
    r = rng.random()
    if r < 0.2:
        a = rng.randrange(1, len(cand))
        cand.insert(0, cand.pop(a))
        return cand
    if r < 0.35:                       # (a stretch of the order shuffled: a bigger jump)
        a = rng.randrange(len(cand))
        b = min(len(cand), a + rng.randint(4, 12))
        part = cand[a:b]
        rng.shuffle(part)
        cand[a:b] = part
        return cand
    for _ in range(max(1, len(cand) // 4) if r < 0.55 else rng.randint(1, 3)):
        a = rng.randrange(len(cand))
        b = rng.randrange(len(cand)) if rng.random() < 0.3 else min(len(cand) - 1, a + rng.randint(1, 3))
        cand[a], cand[b] = cand[b], cand[a]
    return cand


def _prefix(n, res, k: int):
    """The first ``k`` plates of a layout, and the parts not on them in the order they were placed."""
    out = rebuild(n, res.layout[:k], res.stock[:k], res.unplaced)
    return out, [(it[0], it[1]) for items in res.layout[k:] for it in items]


def search_plates(n, g, run, keep, done: list, budget: int, rng: random.Random, deadline, check,
                  beam: int = 4, rounds: int = 3, closing: int = 8) -> int:
    """Plate after plate: the next plate of the best few layouts so far filled in many ways (the plain orders of the
    parts left, then changes of the orders that filled it most), the fullest ones going on (the least area of parts left
    over): how many plates a layout takes is decided by how full the plates before the last one are. The last plate is
    filled the same way, and the whole layouts that come out of it are handed to ``keep``. Where the parts left nearly
    fit on the plate being filled and a plate fewer than the best so far is in reach, up to ``closing`` rounds more are
    made there until one takes them all. Then again from the first plate, wider, while the budget lasts: each plate
    begins from the same plates of the best whole layouts so far too.

    ``run(batch)`` makes attempts (see :meth:`GpuNester.fill`); ``done``: the whole layouts made so far, ``(order,
    gravity, result)``; ``budget``: how many plates may be filled. Returns how many were."""
    hows = ("area", "side", "height", "width", "perimeter")
    gravs = list(dict.fromkeys(gr for _, gr, _ in done)) or ["left"]
    orders = {h: n.order_by(h) for h in hows}
    plate = max(b.free for b in n.boxes if b.usable)
    spent = 0

    def rest_area(rest):
        return sum(n.areas[it[0]] for it in rest)

    def fewest():
        return min((r.sheets for _, _, r in done if not r.unplaced), default=10 ** 9)

    def entry(pool, res, rest, o, gr, base):
        q = n.quality(res) if not rest else ()
        pool.append((rest_area(rest), q, len(pool), res, rest, o, gr, base))

    def made(batch, pool):
        nonlocal spent
        spent += len(batch)
        for (o, gr, base, _), at in zip(batch, run(batch)):
            if not at.remaining:
                keep(o, gr, at.res)
            entry(pool, at.res, at.remaining, o, gr, base)

    def late():
        return spent >= budget or (deadline is not None and time.perf_counter() >= deadline)

    def seeds(level, pool):
        """The plate ``level`` of the best whole layouts so far, as they filled it."""
        best = sorted(done, key=lambda d: n.quality(d[2]))[:2 * g.slots]
        seen = set()
        for o, gr, res in best:
            if res.unplaced or res.sheets <= level:
                continue
            key = tuple(sorted((it[0], it[1]) for items in res.layout[:level + 1] for it in items))
            if key in seen:
                continue
            seen.add(key)
            base, order = _prefix(n, res, level) if level else (None, [(it[0], it[1]) for items in res.layout
                                                                       for it in items])
            st, rest = _prefix(n, res, level + 1)
            entry(pool, st, rest, order, gr, base)

    passes = 0
    while not late():
        passes += 1
        width = beam + 2 * (passes - 1)
        before = fewest(), min(n.quality(r) for _, _, r in done)
        states: list = [None]
        level = 0
        while states and not late():
            check()
            pool: list = []
            seeds(level, pool)
            if level > 0:
                lists = []
                for base, rest in states:
                    rem, seen, mine = set(rest), set(), []
                    for h in hows:
                        for gr in gravs:
                            o = [it for it in orders[h] if it in rem]
                            if (tuple(o), gr) not in seen:
                                seen.add((tuple(o), gr))
                                mine.append((o, gr, base, 1))
                    lists.append(mine)
                # (as many as there are slots: the states' plain orders by turns)
                batch = [m[k] for k in range(max(map(len, lists))) for m in lists if k < len(m)][:g.slots]
                made(batch, pool)
            for r_ in range(rounds + closing):
                if r_ >= rounds:
                    # (a plate fewer within reach: the fullest fill leaves little over, and none took everything yet)
                    left = min((p[0] for p in pool if p[4]), default=0.0)
                    won = any(not p[4] and p[3].sheets <= level + 1 for p in pool)
                    if won or not left or left > 0.15 * plate or level + 1 >= fewest() or                             (deadline is not None and time.perf_counter() >= deadline):
                        break
                elif late() or not pool:
                    break
                check()
                pool.sort(key=lambda p: (p[0], p[1], p[2]))
                batch = []
                for _ in range(g.slots):
                    p = pool[min(len(pool) - 1, int(rng.expovariate(1.0 / max(1, passes))))]
                    if len(p[5]) >= 2:
                        batch.append((_mutate(p[5], rng), p[6] if rng.random() < 0.7 else rng.choice(gravs), p[7], 1))
                if not batch:
                    break
                made(batch, pool)
            # the fullest few go on, one of each set of parts left; none that cannot take fewer plates than the best (or
            # as few, with fuller plates before the last)
            pool.sort(key=lambda p: (p[0], p[1], p[2]))
            limit = fewest()
            states, seen = [], set()
            for p in pool:
                if p[4] and p[3].sheets + 1 <= limit and frozenset(p[4]) not in seen:
                    seen.add(frozenset(p[4]))
                    states.append((p[3], p[4]))
                    if len(states) >= width:
                        break
            level += 1
        if passes >= 3 and (fewest(), min(n.quality(r) for _, _, r in done)) >= before:
            break                          # (a whole pass brought nothing: as good as it gets this way)
    return spent


def nest_on_gpu(n, settings, check: Callable[[], None], progress: Callable[[int, int], None] | None = None,
                be=None, attempts: int | None = None):
    """The best layout made on the card, checked exactly, or ``None`` (nothing valid: the usual way then).

    ``attempts``: how many layouts (by default 4 x ``settings.iterations``, at least 256 on the card): the plain orders of
    the nester side by side, then the plates filled one after another the best ways found (:func:`search_plates`), as
    many at a time as there are slots; a plate filled counts as a part of a layout. A search for every SEARCH of them,
    each of its own (its own random changes, from the plain layouts): one search goes where its first changes lead it,
    the best of a few is better than one search as long."""
    t0 = time.perf_counter()
    g = GpuNester(n, be, settings.gpu_pixel)
    try:
        return _nest_on_gpu(n, settings, check, progress, g, attempts, t0)
    finally:
        g.release()


def _nest_on_gpu(n, settings, check, progress, g, attempts, t0):
    g.prepare(check)
    tries = max(1, settings.iterations)
    total = attempts if attempts is not None else (
        1 if tries <= 1 else max(SEARCH * 2 if g.be.name == "cupy" else 0, 4 * tries))
    plan = plan_of(n, min(total, g.slots))
    parts = max(1, len(plan[0][0]) if plan else 1)
    est = total * parts
    placed = [0]

    def report(k):
        if progress is not None:
            progress(min(est, k), est)

    def run(batch):
        ats = g.fill(batch, check, lambda k: report(placed[0] + k))
        placed[0] += sum(len(a.res.placements) - (len(b[2].placements) if len(b) > 2 and b[2] is not None else 0)
                         for a, b in zip(ats, batch))
        return ats

    done: list = []

    def keep(o, gr, res):
        done.append((o, gr, res))

    for (o, gr), at in zip(plan, run(plan)):
        keep(o, gr, at.res)
    made = len(plan)
    deadline = None if settings.time_limit <= 0 else t0 + settings.time_limit
    rng = random.Random(settings.seed)
    if made < total and parts >= 2:
        sheets = min(r.sheets for _, _, r in done) or 1
        budget = (total - made) * max(1, sheets)
        # (searches of their own, each from the plain layouts with its own random changes: the best of them is kept;
        # one search goes where its first changes lead it)
        searches = max(1, total // SEARCH)
        plain = list(done)
        for r_ in range(searches):
            mine = list(plain)

            def keep2(o, gr, res, mine=mine):
                mine.append((o, gr, res))
                done.append((o, gr, res))
            filled = search_plates(n, g, run, keep2, mine, budget // searches, random.Random(settings.seed * 7919 + r_),
                                   deadline, check)
            made += -(-filled // max(1, sheets))
    scored = sorted(((n.quality(r), i, r, o, gr) for i, (o, gr, r) in enumerate(done)), key=lambda s: (s[0], s[1]))
    for q, _, r, o, gr in scored:
        if valid(n, r):
            fixed = repair(n, r, gr, check)
            if fixed is not r and valid(n, fixed):
                r = fixed
                g.stats["repaired"] = 1
            r.notes = [f"nested on the graphics card ({device_name() or g.be.name}): {len(done)} layouts, "
                       f"{g.stats['attempts']} plates or layouts filled, pixel {g.pixel:g} mm, "
                       f"{time.perf_counter() - t0:.1f} s"
                       + ("; the last plate finished the exact way" if g.stats.get("repaired") else "")]
            return r, o, gr, g.stats
    return None

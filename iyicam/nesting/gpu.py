"""Nesting on the graphics card (experimental): the free layout of parts on rectangular plates, many attempts at once.

Switched on with ``NestSettings.gpu`` (Settings > Experimental). It needs an NVIDIA card and CuPy
(``pip install cupy-cuda12x``, or ``pip install iyicam[gpu]``); without them, or for what it does not do (plates that are
not rectangles, parts fixed on a plate, rows / columns, squeezing in, the gravities ``compact`` and ``diag``), the nesting
is the usual one on the processors, unchanged. The same code runs on NumPy (``backend("numpy")``): slower, for the tests.

How a part is placed, the no-fit polygons left out:

1. The plate is a bitmap (``pixel`` mm), every pixel a part placed touches set. Every part at every angle is a bitmap of
   the pixels its outline (grown by half the gap) touches, as runs of pixels per row.
2. Where the part can go is where none of its runs has anything under it: every pixel position of every angle at once on
   the card (a row prefix sum of the plate makes a run one subtraction; a coarse summed area table rejects most occupied
   positions at one look). The positions are looked at in the order of the gravity, and the first free one is the best.
   The bitmaps keep on the safe side, so a free position is free: the parts do not touch there.
3. The part is then slid the way the gravity pulls it (left, down, left, down, or down first), each time as far as it
   goes until it touches a part or the plate's edge, worked out exactly from the outlines (rays from the corners of one to
   the edges of the other): the gaps the pixels leave are closed, the parts lie against one another as they do with the
   no-fit polygons.
4. The plate's bitmap gets the part where it ended (its bitmap grown by half a pixel more, so a part moved off the pixel
   grid is covered).

Many attempts (orders of the parts and gravities, then random changes of the best order) run side by side, a part of
each at every step. The best layout by :meth:`Nester.quality` is checked exactly at the end (every part inside its plate,
no two closer than the gap: Clipper, as :func:`Nester._overlaps`); one that fails is not taken (the next best is, or the
nesting goes the usual way).
"""
from __future__ import annotations

import math
import random
import time
from typing import Callable

import numpy as np
import pyclipper

from ..core import clip

BLOCK = 8          # pixels per side of a block of the coarse table
STAMP_GROW = 1.42
GRID = 4           # thread blocks of 256 per search job
INF = 2 ** 31 - 1
BACKOFF = 20       # units (2 microns) kept off a contact after a slide: the rays are worked out in float32
GRAVITIES = ("left", "left_far", "bottom", "bottom_far", "reach_x", "reach_y")
_why: str | None | bool = False


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
__device__ __forceinline__ const void* ptr_at(const int* q) {
    return (const void*)(((unsigned long long)(unsigned)q[1] << 32) | (unsigned)q[0]);
}

__device__ __forceinline__ int box_sum(const int* T, int TB1, int bx0, int by0, int bx1, int by1) {
    return T[by1 * TB1 + bx1] - T[by0 * TB1 + bx1] - T[by1 * TB1 + bx0] + T[by0 * TB1 + bx0];
}

extern "C" __global__ void search(int* jb, int njobs, const int* runs, const int* cores) {
    // a warp takes 32 cells at a time, each lane the look at one; the cells that are not rejected are then looked at in
    // order by the whole warp, two positions a lane
    int job = blockIdx.y;
    const int* q = jb + 16 * job;
    const int* S = (const int*)ptr_at(q);
    const int* T = (const int*)ptr_at(q + 2);
    q += 4;
    int* best = jb + 16 * njobs;
    int W1 = q[0], nc = q[1], nr = q[2], r0 = q[3], nruns = q[4], c0 = q[5], ncores = q[6], rowmajor = q[7];
    int TB1 = q[8], x0 = q[9], kst = q[10];
    int ncx = (nc + 7) >> 3, ncy = (nr + 7) >> 3;
    int cells = ncx * ncy;
    int lane = threadIdx.x & 31;
    int warp = (blockIdx.x * blockDim.x + threadIdx.x) >> 5;
    int warps = (gridDim.x * blockDim.x) >> 5;
    int inner = rowmajor ? ncx : ncy;                 // cells along the fast direction
    int span = rowmajor ? nc : nr;
    volatile int* bp = (volatile int*)(best + job);
    // (positions before ``kst`` are known to be taken: the same part at the same angle found nothing free there before,
    // and a plate only fills up)
    int first = ((rowmajor ? kst / nc : kst / nr) >> 3) * inner;
    for (int base = first + warp * 32; base < cells; base += warps * 32) {
        int K = base + lane;
        bool alive = false;
        int I = 0, J = 0;
        if (K < cells) {
            int outer = K / inner, in = K - outer * inner;
            I = rowmajor ? in : outer;
            J = rowmajor ? outer : in;
            int kmin = rowmajor ? (J << 3) * nc + (I << 3) : (I << 3) * nr + (J << 3);
            alive = kmin < *bp;
            int i0 = I << 3, j0 = J << 3;
            int X0 = x0 + i0, X1 = x0 + min(i0 + 8, nc) - 1, Y0 = j0, Y1 = min(j0 + 8, nr) - 1;
            for (int c = 0; c < ncores && alive; ++c) {
                const int* cr = cores + 4 * (c0 + c);
                int bx0 = (X1 + cr[1] + 7) >> 3, bx1 = (X0 + cr[3]) >> 3;
                int by0 = (Y1 + cr[0] + 7) >> 3, by1 = (Y0 + cr[2]) >> 3;
                if (bx1 > bx0 && by1 > by0 && box_sum(T, TB1, bx0, by0, bx1, by1) != 0)
                    alive = false;
            }
        }
        if (((base / inner) << 3) * span > *bp)
            return;                                             // (every cell from here on begins past the best)
        unsigned live = __ballot_sync(0xffffffffu, alive);
        while (live) {
            int src = __ffs(live) - 1;
            live &= live - 1;
            int CI = __shfl_sync(0xffffffffu, I, src), CJ = __shfl_sync(0xffffffffu, J, src);
            int i0 = CI << 3, j0 = CJ << 3;
            if ((rowmajor ? j0 * nc + i0 : i0 * nr + j0) >= *bp) break;
            int ni = min(8, nc - i0), nj = min(8, nr - j0);
            int mine = 0x7fffffff;
            for (int h = 0; h < 2; ++h) {
                int u = lane + 32 * h;
                int di = rowmajor ? (u & 7) : (u >> 3), dj = rowmajor ? (u >> 3) : (u & 7);
                if (di >= ni || dj >= nj) continue;
                int i = i0 + di, j = j0 + dj;
                int k = rowmajor ? j * nc + i : i * nr + j;
                if (k >= *bp) break;
                if (k < kst) continue;
                int x = x0 + i, y = j;
                bool hit = false;
                for (int c = 0; c < ncores && !hit; ++c) {
                    const int* cr = cores + 4 * (c0 + c);
                    int bx0 = (x + cr[1] + 7) >> 3, bx1 = (x + cr[3]) >> 3;
                    int by0 = (y + cr[0] + 7) >> 3, by1 = (y + cr[2]) >> 3;
                    if (bx1 > bx0 && by1 > by0)
                        hit = box_sum(T, TB1, bx0, by0, bx1, by1) != 0;
                }
                for (int t = 0; t < nruns && !hit; ++t) {
                    const int* ru = runs + 3 * (r0 + t);
                    const int* row = S + (long long)(ru[0] + y) * W1;
                    hit = row[ru[2] + x] != row[ru[1] + x];
                    if ((t & 7) == 7 && k >= *bp) break;
                }
                if (!hit && k < *bp) {
                    mine = k;
                    break;
                }
            }
            if (mine != 0x7fffffff)
                atomicMin(best + job, mine);
            // (a free position in this cell: the cells after it in this warp's batch can still hold a smaller one
            // only if they begin before it, which the k >= best tests take care of)
        }
    }
}

// Stamping, every plate of a step at once: a part's bitmap ORed in (stamp_or), the prefix sums of the rows it touched
// made again (row_scan), the blocks it touched counted again (coarse), the summed area table of the blocks from there on
// (table_rows, table_cols). ``si`` per job: W, H, Wb, Hb, mw, i, j, a0, b0, a1, b1 (the window on the plate).
#define SJ 22
#define PTR(q, k) ptr_at((q) + 2 * (k))
extern "C" __global__ void stamp_or(const int* si) {
    int job = blockIdx.y;
    const int* q = si + SJ * job;
    unsigned char* occ = (unsigned char*)PTR(q, 0);
    const unsigned char* mask = (const unsigned char*)PTR(q, 4);
    q += 10;
    int W = q[0], mw = q[4], i = q[5], j = q[6], a0 = q[7], b0 = q[8], a1 = q[9], b1 = q[10];
    int w = a1 - a0, n = w * (b1 - b0);
    for (int k = blockIdx.x * blockDim.x + threadIdx.x; k < n; k += gridDim.x * blockDim.x) {
        int r = b0 + k / w, c = a0 + k % w;
        if (mask[(r - j) * mw + (c - i)]) occ[(long long)r * W + c] = 1;
    }
}

extern "C" __global__ void row_scan(const int* si) {
    __shared__ int part[256];
    int job = blockIdx.y;
    const int* q = si + SJ * job;
    const unsigned char* occ0 = (const unsigned char*)PTR(q, 0);
    int* S0 = (int*)PTR(q, 1);
    q += 10;
    int W = q[0], b0 = q[8], b1 = q[10];
    int r = b0 + blockIdx.x;
    if (r >= b1) return;
    const unsigned char* occ = occ0 + (long long)r * W;
    int* S = S0 + (long long)r * (W + 1);
    int per = (W + blockDim.x - 1) / blockDim.x;
    int c0 = threadIdx.x * per, c1 = min(W, c0 + per);
    int sum = 0;
    for (int c = c0; c < c1; ++c) sum += occ[c];
    part[threadIdx.x] = sum;
    __syncthreads();
    for (int off = 1; off < blockDim.x; off <<= 1) {       // (inclusive scan of the parts)
        int v = threadIdx.x >= off ? part[threadIdx.x - off] : 0;
        __syncthreads();
        part[threadIdx.x] += v;
        __syncthreads();
    }
    int run = part[threadIdx.x] - sum;
    if (threadIdx.x == 0) S[0] = 0;
    for (int c = c0; c < c1; ++c) {
        run += occ[c];
        S[c + 1] = run;
    }
}

extern "C" __global__ void coarse(const int* si) {
    int job = blockIdx.y;
    const int* q = si + SJ * job;
    const unsigned char* occ = (const unsigned char*)PTR(q, 0);
    int* C = (int*)PTR(q, 2);
    q += 10;
    int W = q[0], Wb = q[2], a0 = q[7], b0 = q[8], a1 = q[9], b1 = q[10];
    int cb0 = a0 >> 3, cb1 = (a1 + 7) >> 3, rb0 = b0 >> 3, rb1 = (b1 + 7) >> 3;
    int w = cb1 - cb0, n = w * (rb1 - rb0);
    for (int k = blockIdx.x * blockDim.x + threadIdx.x; k < n; k += gridDim.x * blockDim.x) {
        int br = rb0 + k / w, bc = cb0 + k % w;
        int s = 0;
        for (int y = 0; y < 8; ++y) {
            const unsigned char* row = occ + (long long)(br * 8 + y) * W + bc * 8;
            for (int x = 0; x < 8; ++x) s += row[x];
        }
        C[br * Wb + bc] = s;
    }
}

extern "C" __global__ void table_rows(const int* si) {
    int job = blockIdx.y;
    const int* q = si + SJ * job;
    const int* C = (const int*)PTR(q, 2);
    int* T = (int*)PTR(q, 3);
    q += 10;
    int Wb = q[2], Hb = q[3], rb0 = q[8] >> 3;
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
    q += 10;
    int Wb = q[2], Hb = q[3], rb0 = q[8] >> 3;
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
        mod = cp.RawModule(code=_SRC)
        _compiled["search"] = mod.get_function("search")
        for name in ("slide", "stamp_or", "row_scan", "coarse", "table_rows", "table_cols"):
            _compiled[name] = mod.get_function(name)
    return _compiled


class _Cupy:
    name = "cupy"

    def __init__(self):
        import cupy as cp
        self.xp = cp
        self.k = _kernels(cp)

    def asarray(self, a):
        return self.xp.asarray(a)

    def get(self, a):
        return a.get()

    def search(self, plates, jobs, runs, cores):
        """``jobs``: (plate, mask, x0, nc, nr, rowmajor) -> the best position k of each (INF: none)."""
        cp = self.xp
        n = len(jobs)
        if n == 0:
            return []
        buf = np.zeros(17 * n, np.int32)
        jb = buf[:16 * n].reshape(n, 16)
        ptrs = jb[:, :4].view(np.uint64)
        for j, (pl, mk, x0, nc, nr, rm, kst) in enumerate(jobs):
            ptrs[j] = (pl.S.data.ptr, pl.T.data.ptr)
            jb[j, 4:15] = (pl.W + 1, nc, nr, mk.r0, mk.nruns, mk.c0, mk.ncores, rm, pl.Wb + 1, x0, kst)
        buf[16 * n:] = INF
        g = cp.asarray(buf)
        self.k["search"]((GRID, n), (256,), (g, np.int32(n), runs, cores))
        return g[16 * n:].get().tolist()

    def stamp(self, jobs):
        """``jobs``: (plate, mask, i, j): the parts marked on their plates, all at once."""
        cp = self.xp
        rows = []
        for pl, sm, i, j in jobs:
            a0, b0 = max(0, i), max(0, j)
            a1, b1 = min(pl.W, i + sm.w), min(pl.H, j + sm.h)
            if a1 > a0 and b1 > b0:
                rows.append((pl, sm, i, j, a0, b0, a1, b1))
        if not rows:
            return
        n = len(rows)
        si = np.zeros((n, 22), np.int32)                 # (22: the pointers stay 8-byte aligned)
        ptrs = si[:, :10].view(np.uint64)
        for k, (pl, sm, i, j, a0, b0, a1, b1) in enumerate(rows):
            ptrs[k] = (pl.occ.data.ptr, pl.S.data.ptr, pl.C.data.ptr, pl.T.data.ptr, sm.gstamp.data.ptr)
            si[k, 10:21] = (pl.W, pl.H, pl.Wb, pl.Hb, sm.w, i, j, a0, b0, a1, b1)
        Q = cp.asarray(si)
        win = int(max((r[6] - r[4]) * (r[7] - r[5]) for r in rows))
        hmax = int(max(r[7] - r[5] for r in rows))
        hb = int(max(r[0].Hb for r in rows))
        wb = int(max(r[0].Wb for r in rows))
        k = self.k
        k["stamp_or"]((min(64, -(-win // 256)), n), (256,), (Q,))
        k["row_scan"]((hmax, n), (256,), (Q,))
        k["coarse"]((-(-(win // 64 + 64) // 256), n), (256,), (Q,))
        k["table_rows"]((-(-hb // 128), n), (128,), (Q,))
        k["table_cols"]((-(-(wb + 1) // 128), n), (128,), (Q,))

    def slide(self, tasks, start, E):
        """``tasks``: (job, m0, nm, o0, no, dir, ox, oy); ``start``: the distance to the plate's edge per job -> the
        distance each job's part can go."""
        cp = self.xp
        if not tasks:
            return [float(v) for v in start]
        n = len(tasks)
        buf = np.zeros(8 * n + len(start), np.int32)
        tb = buf[:8 * n].reshape(n, 8)
        tb[:, :6] = [t[:6] for t in tasks]
        tb[:, 6:8] = np.array([t[6:] for t in tasks], np.float32).view(np.int32)
        buf[8 * n:] = np.asarray(start, np.float32).view(np.int32)
        g = cp.asarray(buf)
        self.k["slide"]((n,), (256,), (g, np.int32(n), E))
        return g[8 * n:].get().view(np.float32).tolist()


class _Numpy:
    """The same work on NumPy, a position chunk at a time in the gravity's order (for the tests and as a reference)."""
    name = "numpy"

    def __init__(self):
        self.xp = np

    def asarray(self, a):
        return np.asarray(a)

    def get(self, a):
        return np.asarray(a)

    def search(self, plates, jobs, runs, cores):
        out = []
        for pl, mk, x0, nc, nr, rm, kst in jobs:
            total = nc * nr
            found = INF
            R = runs[mk.r0:mk.r0 + mk.nruns].tolist()
            C = cores[mk.c0:mk.c0 + mk.ncores].tolist()
            T, S = pl.T, pl.S
            for k0 in range(kst, total, 1 << 16):
                # the cores of a whole chunk of positions at once, then the runs of the ones left, a few at a time in order
                k = np.arange(k0, min(total, k0 + (1 << 16)))
                x = x0 + (k % nc if rm else k // nr)
                y = k // nc if rm else k % nr
                ok = np.ones(len(k), bool)
                for r0_, c0_, r1_, c1_ in C:
                    bx0, bx1 = (x + c0_ + 7) >> 3, (x + c1_) >> 3
                    by0, by1 = (y + r0_ + 7) >> 3, (y + r1_) >> 3
                    use = (bx1 > bx0) & (by1 > by0)
                    ok &= ~(use & ((T[by1, bx1] - T[by0, bx1] - T[by1, bx0] + T[by0, bx0]) != 0))
                live = np.nonzero(ok)[0]
                for s0 in range(0, len(live), 512):
                    sel = live[s0:s0 + 512]
                    xs, ys = x[sel], y[sel]
                    good = np.ones(len(sel), bool)
                    for r, a, b in R:
                        good &= S[r + ys, b + xs] == S[r + ys, a + xs]
                        if not good.any():
                            break
                    if good.any():
                        found = int(k[sel[np.argmax(good)]])
                        break
                if found < INF:
                    break
            out.append(found)
        return out

    def stamp(self, jobs):
        for pl, sm, i, j in jobs:
            pl.stamp(sm, i, j)

    def slide(self, tasks, start, E):
        out = list(np.asarray(start, np.float64))
        for job, m0, nm, o0, no, d, ox, oy in tasks:
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


def _cores(m: np.ndarray, most: int = 4) -> list[tuple[int, int, int, int]]:
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


class _Mask:
    """A part at an angle as a bitmap: where its pixel (0, 0) lies from the part's reference (``ox``, ``oy``, units), its
    size in pixels, its runs and its cores (in the backend's arrays: ``r0``, ``nruns``, ``c0``, ``ncores``), and the
    bitmap that marks it on a plate (``stamp``, half a pixel grown more)."""

    def __init__(self, v, px: int):
        bx0, by0, bx1, by1 = v.bbox
        g = 2 * px
        self.ox, self.oy = bx0 - g, by0 - g
        self.w = (bx1 - bx0 + 2 * g) // px + 1
        self.h = (by1 - by0 + 2 * g) // px + 1
        m = raster(v.solid, self.ox, self.oy, self.w, self.h, px, 0.7072)
        self.stamp = raster(v.solid, self.ox, self.oy, self.w, self.h, px, STAMP_GROW)
        pad = np.zeros((self.h, self.w + 2), np.int8)
        pad[:, 1:-1] = m
        dd = np.diff(pad, axis=1)
        rs, a_ = np.nonzero(dd == 1)                    # (row-major: the starts and the ends of a row pair up)
        _, b_ = np.nonzero(dd == -1)
        runs = list(zip(rs.tolist(), a_.tolist(), b_.tolist()))
        # coarse to fine: the first row, the middle, the quarters, the eighths ...: wherever a part meets another, a few runs
        # find it (from the middle out took 45 on average at the edge of the parts placed)
        def spread(r):
            tz = (r & -r).bit_length() if r else 64
            return (-tz, r)
        runs.sort(key=lambda t: spread(t[0]))
        self.runs = runs
        self.cores = _cores(m)
        self.r0 = self.nruns = self.c0 = self.ncores = 0


class _Plate:
    """A plate on the card: its bitmap (``occ``), the prefix sums of every row (``S``), the coarse table of the occupied
    pixels in blocks of BLOCK x BLOCK (``T``, summed area)."""

    def __init__(self, be, rect, px: int):
        xp = be.xp
        self.be = be
        self.x0, self.y0, x1, y1 = rect
        self.px = px
        self.Wr = max(0, (x1 - self.x0) // px)                  # the pixels wholly on the plate
        self.Hr = max(0, (y1 - self.y0) // px)
        self.W = -(-max(1, self.Wr) // BLOCK) * BLOCK
        self.H = -(-max(1, self.Hr) // BLOCK) * BLOCK
        self.Wb, self.Hb = self.W // BLOCK, self.H // BLOCK
        self.occ = xp.zeros((self.H, self.W), xp.uint8)
        self.occ[:, self.Wr:] = 1                               # (off the plate: taken)
        self.occ[self.Hr:, :] = 1
        self.S = xp.zeros((self.H, self.W + 1), xp.int32)
        self.T = xp.zeros((self.Hb + 1, self.Wb + 1), xp.int32)
        self.lb: dict = {}             # (class, angle, row-major) -> the first position that can still be free
        self._rows(0, self.H)

    def _rows(self, j0: int, j1: int):
        xp = self.be.xp
        self.S[j0:j1, 1:] = xp.cumsum(self.occ[j0:j1], axis=1, dtype=xp.int32)
        b0, b1 = j0 // BLOCK, -(-j1 // BLOCK)
        blk = self.occ[b0 * BLOCK:b1 * BLOCK].reshape(b1 - b0, BLOCK, self.Wb, BLOCK).sum(axis=(1, 3), dtype=xp.int32)
        if not hasattr(self, "C"):
            self.C = xp.zeros((self.Hb, self.Wb), xp.int32)
        self.C[b0:b1] = blk
        self.T[1:, 1:] = xp.cumsum(xp.cumsum(self.C, axis=0, dtype=xp.int32), axis=1, dtype=xp.int32)

    def stamp(self, sm, i: int, j: int):
        a0, b0 = max(0, i), max(0, j)
        a1, b1 = min(self.W, i + sm.w), min(self.H, j + sm.h)
        if a1 <= a0 or b1 <= b0:
            return
        self.occ[b0:b1, a0:a1] |= sm.gstamp[b0 - j:b1 - j, a0 - i:a1 - i]
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
    """One layout being made: the run_order / _fill of the nester, a part at a time."""

    def __init__(self, n, order, grav):
        self.n, self.order, self.grav = n, list(order), grav
        self.res = n_result()
        self.remaining = [it for it in order if n.angles[it[0]]]
        self.res.unplaced = [it for it in order if not n.angles[it[0]]]
        self.pool = n._plates()
        self.made: dict[int, int] = {}
        self.plate = None
        self.done = False
        self._next_plate()

    def _next_plate(self):
        n = self.n
        while True:
            self.pool = [b for b in self.pool if b.c.quantity <= 0 or self.made.get(b.index, 0) < b.c.quantity]
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
            self.bbs: list[tuple] = []     # per part placed: its bounding box (units)
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
        self.masks: dict = {}
        self._runs: list = []
        self._cores: list = []
        self._edges: list = []           # per (class, angle): (first, count) in E
        self._eidx: dict = {}
        self._E: list = []
        self._free: dict = {}             # plates made and not in use, per box
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
                mk.gstamp = self.be.asarray(mk.stamp.astype(np.uint8))
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

    def _plate(self, box):
        free = self._free.setdefault(box.index, [])
        if free:
            pl = free.pop()
            pl.lb = {}
            pl.occ[:] = 0
            pl.occ[:, pl.Wr:] = 1
            pl.occ[pl.Hr:, :] = 1
            pl._rows(0, pl.H)
            return pl
        pl = _Plate(self.be, box.rect, self.px)
        pl.box = box
        return pl

    def _give_back(self, pl):
        self._free.setdefault(pl.box.index, []).append(pl)

    # -- one step of every attempt ---------------------------------------------------------------------------------
    def _search(self, reqs):
        """``reqs``: (attempt, item) -> per request the best ``(key, angle, x, y)`` or ``None``."""
        n = self.n
        px = self.px
        jobs, meta = [], []
        for q, (at, item) in enumerate(reqs):
            si = item[0]
            c = n.cls[si]
            if at.plate is None:
                at.plate = self._plate(at.box)
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
                    lim = int(min(lim, (nc if ax == 0 else nr) - 1))
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
        found = self.be.search(None, jobs, self.runs, self.cores)
        self.stats["searches"] += len(jobs)
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
        """``moves``: (attempt, class, angle, x, y, dir) -> how far each can go (units)."""
        n = self.n
        tasks, start = [], []
        for j, (at, c, ang, x, y, d) in enumerate(moves):
            v = n._variant(c, ang)
            bx0, by0, bx1, by1 = v.bbox
            r = at.box.rect
            wall = (x + bx0 - r[0], y + by0 - r[1], r[2] - x - bx1, r[3] - y - by1)[d]
            start.append(float(max(0, wall)))
            if wall <= 0 or not at.placed:
                continue
            sx0, sy0, sx1, sy1 = x + bx0, y + by0, x + bx1, y + by1      # the box the part sweeps
            if d == 0:
                sx0 -= wall
            elif d == 1:
                sy0 -= wall
            elif d == 2:
                sx1 += wall
            else:
                sy1 += wall
            B = at.bbarr
            near = np.nonzero((B[:, 2] >= sx0) & (B[:, 0] <= sx1) & (B[:, 3] >= sy0) & (B[:, 1] <= sy1))[0]
            m0, nm = self._eidx[(c, ang)]
            for k in near.tolist():
                pc, pa, qx, qy = at.placed[k]
                o0, no = self._eidx[(pc, pa)]
                tasks.append((j, m0, nm, o0, no, d, float(qx - x), float(qy - y)))
        self.stats["slides"] += len(moves)
        return self.be.slide(tasks, start, self.E)

    def run(self, plan, check: Callable[[], None], progress: Callable[[int], None] | None = None) -> list:
        """The layouts of ``plan`` (``(order, gravity)`` each), side by side: the NestResults in the same order."""
        n = self.n
        ats = [_Attempt(n, o, g) for o, g in plan]
        self.stats["attempts"] += len(ats)
        active = [a for a in ats if not a.done]
        placed_count = 0
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
            found = self._search(reqs)
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
                for step in range(4):
                    moves = []
                    for g in go:
                        at = g[0]
                        first_down = at.grav.startswith("bottom") or at.grav == "reach_y"
                        d = (1, 0, 1, 0)[step] if first_down else (0, 1, 0, 1)[step]
                        moves.append((at, g[2], g[3], g[4], g[5], d))
                    dist = self._slides(moves)
                    for g, (_, _, _, _, _, d), t in zip(go, moves, dist):
                        s = int(t) - BACKOFF
                        if s > 0:
                            if d == 0:
                                g[4] -= s
                            else:
                                g[5] -= s
                marks = []
                for at, item, c, ang, x, y in go:
                    marks.append(self._place(at, item, c, ang, x, y))
                    placed_count += 1
                self.be.stamp(marks)
            if progress is not None:
                progress(placed_count)
            active = [a for a in active if not a.done]
        for at in ats:
            if at.plate is not None:
                self._give_back(at.plate)
                at.plate = None
        return [a.res for a in ats]

    def _place(self, at, item, c, ang, x, y):
        n = self.n
        si, ci = item
        v = n._variant(c, ang)
        sm = self.masks[(c, ang)]
        pl = at.plate
        at.placed.append((c, ang, x, y))
        at.items.append((si, ci, c, ang, x, y))
        nb = (x + v.bbox[0], y + v.bbox[1], x + v.bbox[2], y + v.bbox[3])
        at.bbs.append(nb)
        at.bbarr = np.array(at.bbs, np.int64)
        at.free -= n.areas[si]
        u = at.used
        at.used = nb if u is None else (min(u[0], nb[0]), min(u[1], nb[1]), max(u[2], nb[2]), max(u[3], nb[3]))
        at.res.placements.append(n._placement(si, ci, at.res.sheets, ang, x, y))
        return pl, sm, round((x + sm.ox - pl.x0) / self.px), round((y + sm.oy - pl.y0) / self.px)


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


def repair(n, res, grav: str, check: Callable[[], None], most: int = 8):
    """The parts of the last plate put on the plates before it the exact way (no-fit polygons, as the usual nesting
    does), when there are few of them: the bitmaps keep the parts a pixel or two further apart than they need, and the
    exact search finds the narrow places they leave. A plate that is emptied so is saved. Returns the better layout (or
    ``res`` as it was)."""
    if res.sheets < 2 or len(res.layout[-1]) > most:
        return res
    layout = [list(items) for items in res.layout]
    saved = n.gravity
    n.gravity = grav
    try:
        last = layout[-1]
        for it in sorted(last, key=lambda it: -n.areas[it[0]]):
            si, ci = it[0], it[1]
            for k in range(len(layout) - 1):
                check()
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


def nest_on_gpu(n, settings, check: Callable[[], None], progress: Callable[[int, int], None] | None = None,
                be=None, attempts: int | None = None):
    """The best layout made on the card, checked exactly, or ``None`` (nothing valid: the usual way then).

    ``attempts``: how many layouts (by default 4 x ``settings.iterations``, at least the plain ones): the plain orders of
    the nester side by side, then rounds of random changes of the best order, as many at a time as there are slots."""
    t0 = time.perf_counter()
    g = GpuNester(n, be, settings.gpu_pixel)
    g.prepare(check)
    tries = max(1, settings.iterations)
    # (the card makes many more layouts than the processors in the same time: at least 128, 4 for every attempt asked)
    total = attempts if attempts is not None else (
        1 if tries <= 1 else max(128 if g.be.name == "cupy" else 0, 4 * tries))
    plan = plan_of(n, min(total, g.slots))
    count = sum(len(o) for o, _ in plan) or 1
    est = total * max(1, len(plan[0][0]) if plan else 1)

    def report(k):
        if progress is not None:
            progress(min(est, k), est)
    done = [0]
    results = g.run(plan, check, lambda k: report(done[0] + k))
    done[0] += count
    scored = [(n.quality(r), i, r, o, gr) for i, (r, (o, gr)) in enumerate(zip(results, plan))]
    rng = random.Random(settings.seed)
    made = len(plan)
    deadline = None if settings.time_limit <= 0 else t0 + settings.time_limit
    while made < total and (deadline is None or time.perf_counter() < deadline):
        check()
        scored.sort(key=lambda s: (s[0], s[1]))
        if len(scored[0][3]) < 2:
            break
        batch = []
        for _ in range(min(g.slots, total - made)):
            # (changes of the best order mostly, of the next best ones now and then: a search that does not get stuck)
            _, _, _, order, grav = scored[min(len(scored) - 1, int(rng.expovariate(1.5)))]
            cand = list(order)
            for _ in range(max(1, len(cand) // 4) if rng.random() < 0.5 else rng.randint(1, 3)):
                a = rng.randrange(len(cand))
                b = rng.randrange(len(cand)) if rng.random() < 0.3 else min(len(cand) - 1, a + rng.randint(1, 3))
                cand[a], cand[b] = cand[b], cand[a]
            gr = grav if rng.random() < 0.7 else rng.choice([p[1] for p in plan])
            batch.append((cand, gr))
        outs = g.run(batch, check, lambda k: report(done[0] + k))
        done[0] += sum(len(o) for o, _ in batch)
        for (o, gr), r in zip(batch, outs):
            scored.append((n.quality(r), made, r, o, gr))
            made += 1
    scored.sort(key=lambda s: (s[0], s[1]))
    for q, _, r, o, gr in scored:
        if valid(n, r):
            fixed = repair(n, r, gr, check)
            if fixed is not r and valid(n, fixed):
                r = fixed
                g.stats["repaired"] = 1
            r.notes = [f"nested on the graphics card ({device_name() or g.be.name}): {made} layouts, "
                       f"pixel {g.pixel:g} mm, {time.perf_counter() - t0:.1f} s"
                       + ("; the last plate finished the exact way" if g.stats.get("repaired") else "")]
            return r, o, gr, g.stats
    return None

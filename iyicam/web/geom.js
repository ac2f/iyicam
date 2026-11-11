/* iyiCAM page: the geometry of vectors, as plain functions on the JSON of the project (no page, no server).
 *
 * A vector is {id, layer, name, contours: [...], locked_starts}; a contour is {closed, start: [x, y], segs: [...]}
 * and a segment is ["L", x, y] (a line to x, y), ["A", x, y, bulge] (an arc to x, y; bulge = tan(sweep / 4), positive
 * counter-clockwise) or ["C", x1, y1, x2, y2, x, y] (a cubic bezier).  A closed contour ends where it starts.  Y is up.
 * Nothing here changes what it is given: a changed vector or contour is a new object (so a cache that is keyed by the
 * object stays right).  The numbers are the program's own (the same arcs and beziers), see docs/web.md. */
(function (root) {
  "use strict";
  const G = {};
  const hyp = Math.hypot, EPS = 1e-9;

  G.newId = () => Array.from(crypto.getRandomValues(new Uint8Array(6)), b => b.toString(16).padStart(2, "0")).join("");
  const copyC = c => ({ closed: c.closed, start: c.start.slice(), segs: c.segs.map(s => s.slice()) });
  G.copyContour = copyC;

  // ------------------------------------------------------------------------------------ segments
  G.segEnd = s => (s[0] === "C" ? [s[5], s[6]] : [s[1], s[2]]);

  /** centre, radius, start angle and sweep of the arc from (x0, y0) to (x1, y1) with this bulge (null for a line) */
  function arcGeom(x0, y0, x1, y1, bulge) {
    const th = 4 * Math.atan(bulge), dx = x1 - x0, dy = y1 - y0, c = hyp(dx, dy);
    if (c < 1e-12 || Math.abs(th) < 1e-9) return null;
    const hh = (c / 2) / Math.tan(th / 2);
    const cx = (x0 + x1) / 2 - dy / c * hh, cy = (y0 + y1) / 2 + dx / c * hh;
    return { cx, cy, r: hyp(x0 - cx, y0 - cy), a0: Math.atan2(y0 - cy, x0 - cx), th };
  }
  G.arcGeom = arcGeom;

  function arcPoints(x0, y0, x1, y1, bulge) {
    const g = arcGeom(x0, y0, x1, y1, bulge);
    if (!g) return [[x1, y1]];
    const n = Math.max(2, Math.ceil(Math.abs(g.th) / 0.15)), out = [];
    for (let k = 1; k < n; k++) out.push([g.cx + g.r * Math.cos(g.a0 + g.th * k / n), g.cy + g.r * Math.sin(g.a0 + g.th * k / n)]);
    out.push([x1, y1]);
    return out;
  }

  /** the points of a contour as a polyline (arcs and beziers in small steps) */
  G.flatten = c => {
    const pts = [c.start.slice()];
    let [x, y] = c.start;
    for (const s of c.segs) {
      if (s[0] === "L") { pts.push([s[1], s[2]]); x = s[1]; y = s[2]; }
      else if (s[0] === "A") { pts.push(...arcPoints(x, y, s[1], s[2], s[3])); x = s[1]; y = s[2]; }
      else {
        for (let k = 1; k <= 12; k++) {
          const u = k / 12, m = 1 - u;
          pts.push([m * m * m * x + 3 * m * m * u * s[1] + 3 * m * u * u * s[3] + u * u * u * s[5],
                    m * m * m * y + 3 * m * m * u * s[2] + 3 * m * u * u * s[4] + u * u * u * s[6]]);
        }
        x = s[5]; y = s[6];
      }
    }
    return pts;
  };

  /** the point at u (0 to 1) along the segment that starts at p0 */
  function evalSeg(p0, s, u) {
    if (s[0] === "L") return [p0[0] + (s[1] - p0[0]) * u, p0[1] + (s[2] - p0[1]) * u];
    if (s[0] === "A") {
      const g = arcGeom(p0[0], p0[1], s[1], s[2], s[3]);
      if (!g) return [p0[0] + (s[1] - p0[0]) * u, p0[1] + (s[2] - p0[1]) * u];
      return [g.cx + g.r * Math.cos(g.a0 + g.th * u), g.cy + g.r * Math.sin(g.a0 + g.th * u)];
    }
    const m = 1 - u;
    return [m * m * m * p0[0] + 3 * m * m * u * s[1] + 3 * m * u * u * s[3] + u * u * u * s[5],
            m * m * m * p0[1] + 3 * m * m * u * s[2] + 3 * m * u * u * s[4] + u * u * u * s[6]];
  }
  G.evalSeg = evalSeg;

  /** the segment cut in two at u: [first, second] */
  function splitSeg(p0, s, u) {
    if (s[0] === "L") return [["L", p0[0] + (s[1] - p0[0]) * u, p0[1] + (s[2] - p0[1]) * u], s.slice()];
    if (s[0] === "A") {
      const g = arcGeom(p0[0], p0[1], s[1], s[2], s[3]);
      const q = evalSeg(p0, s, u);
      if (!g) return [["L", q[0], q[1]], ["L", s[1], s[2]]];
      return [["A", q[0], q[1], Math.tan(g.th * u / 4)], ["A", s[1], s[2], Math.tan(g.th * (1 - u) / 4)]];
    }
    const lerp = (a, b) => [a[0] + (b[0] - a[0]) * u, a[1] + (b[1] - a[1]) * u];
    const P = [p0, [s[1], s[2]], [s[3], s[4]], [s[5], s[6]]];
    const a = lerp(P[0], P[1]), b = lerp(P[1], P[2]), c = lerp(P[2], P[3]), d = lerp(a, b), e = lerp(b, c), f = lerp(d, e);
    return [["C", a[0], a[1], d[0], d[1], f[0], f[1]], ["C", e[0], e[1], c[0], c[1], P[3][0], P[3][1]]];
  }
  G.splitSeg = splitSeg;

  // ------------------------------------------------------------------------------------ nodes
  /** the nodes of a contour: a closed one has as many as segments (the start is not repeated at the end) */
  G.nodes = c => {
    const pts = [c.start.slice()];
    c.segs.forEach((s, i) => { if (!(c.closed && i === c.segs.length - 1)) pts.push(G.segEnd(s)); });
    return pts;
  };

  /** where the segment i starts */
  const segStart = (c, i) => (i === 0 ? c.start : G.segEnd(c.segs[i - 1]));
  G.segStart = segStart;

  /** the nearest point of a contour to (x, y): {i (segment), u (0 to 1 along it), d, pt} */
  G.nearest = (c, x, y) => {
    let best = null, p0 = c.start;
    c.segs.forEach((s, i) => {
      let lo = 0, hi = 1, bu = 0, bd = Infinity;
      const N = s[0] === "L" ? 1 : 48;
      for (let k = 0; k <= N; k++) {
        const q = evalSeg(p0, s, k / N), d = hyp(q[0] - x, q[1] - y);
        if (d < bd) { bd = d; bu = k / N; }
      }
      if (s[0] === "L") {
        const dx = s[1] - p0[0], dy = s[2] - p0[1], l2 = dx * dx + dy * dy;
        bu = l2 < 1e-18 ? 0 : clampN(((x - p0[0]) * dx + (y - p0[1]) * dy) / l2, 0, 1);
      } else {
        lo = Math.max(0, bu - 1 / N); hi = Math.min(1, bu + 1 / N);
        for (let k = 0; k < 24; k++) {               // (a ternary search between the two neighbours of the best sample)
          const m1 = lo + (hi - lo) / 3, m2 = hi - (hi - lo) / 3;
          const d1 = hyp(...sub(evalSeg(p0, s, m1), [x, y])), d2 = hyp(...sub(evalSeg(p0, s, m2), [x, y]));
          if (d1 < d2) hi = m2; else lo = m1;
        }
        bu = (lo + hi) / 2;
      }
      const q = evalSeg(p0, s, bu), d = hyp(q[0] - x, q[1] - y);
      if (!best || d < best.d) best = { i, u: bu, d, pt: q };
      p0 = G.segEnd(s);
    });
    return best;
  };
  const sub = (a, b) => [a[0] - b[0], a[1] - b[1]];
  const clampN = (v, a, b) => Math.min(b, Math.max(a, v));

  // ------------------------------------------------------------------------------------ vectors
  G.vectorOf = (contours, layer, name = "") => ({ id: G.newId(), layer, name, contours, locked_starts: [] });
  const withContours = (v, contours) => ({ ...v, contours, locked_starts: (v.locked_starts || []).slice() });

  /** an affine map [a, b, c, d, e, f]: x' = a x + c y + e, y' = b x + d y + f.  Arcs stay arcs under a map that keeps
   *  the shape (a move, a turn, a flip, an equal scale); under another one ``null`` is answered (use the service). */
  G.affine = (v, m) => {
    const [a, b, c, d, e, f] = m, det = a * d - b * c;
    const similar = Math.abs(a * a + b * b - (c * c + d * d)) < 1e-9 * (1 + a * a + b * b) && Math.abs(a * c + b * d) < 1e-9 * (1 + a * a + b * b);
    const P = (x, y) => [a * x + c * y + e, b * x + d * y + f];
    const hasArc = v.contours.some(ct => ct.segs.some(s => s[0] === "A"));
    if (hasArc && !similar) return null;
    const flip = det < 0 ? -1 : 1;
    return withContours(v, v.contours.map(ct => ({
      closed: ct.closed, start: P(ct.start[0], ct.start[1]),
      segs: ct.segs.map(s => {
        if (s[0] === "L") return ["L", ...P(s[1], s[2])];
        if (s[0] === "A") return ["A", ...P(s[1], s[2]), s[3] * flip];
        return ["C", ...P(s[1], s[2]), ...P(s[3], s[4]), ...P(s[5], s[6])];
      }) })));
  };
  G.translate = (v, dx, dy) => G.affine(v, [1, 0, 0, 1, dx, dy]);
  G.rotateMap = (deg, cx, cy) => {
    const r = deg * Math.PI / 180, co = Math.cos(r), si = Math.sin(r);
    return [co, si, -si, co, cx - co * cx + si * cy, cy - si * cx - co * cy];
  };
  G.scaleMap = (k, cx, cy) => [k, 0, 0, k, cx - k * cx, cy - k * cy];
  G.flipHMap = cx => [-1, 0, 0, 1, 2 * cx, 0];          // left / right about the vertical line x = cx
  G.flipVMap = cy => [1, 0, 0, -1, 0, 2 * cy];          // top / bottom about the horizontal line y = cy

  /** the exact box of a contour: the ends of its segments and the extremes of its arcs and beziers */
  G.contourBBox = c => {
    let x0 = c.start[0], x1 = x0, y0 = c.start[1], y1 = y0;
    const add = (x, y) => { if (x < x0) x0 = x; if (x > x1) x1 = x; if (y < y0) y0 = y; if (y > y1) y1 = y; };
    let p = c.start;
    for (const s of c.segs) {
      const e = G.segEnd(s);
      add(e[0], e[1]);
      if (s[0] === "A") {
        const a = arcGeom(p[0], p[1], s[1], s[2], s[3]);
        if (a) for (let k = 0; k < 4; k++) {
          const ang = k * Math.PI / 2, d = a.th > 0 ? mod(ang - a.a0) : mod(a.a0 - ang);
          if (d <= Math.abs(a.th)) add(a.cx + a.r * Math.cos(ang), a.cy + a.r * Math.sin(ang));
        }
      } else if (s[0] === "C") {
        for (const ax of [0, 1]) {
          const p0 = p[ax], p1 = s[1 + ax], p2 = s[3 + ax], p3 = s[5 + ax];
          const A = (p1 - p0) - 2 * (p2 - p1) + (p3 - p2), Bq = 2 * ((p2 - p1) - (p1 - p0)), C = p1 - p0;
          const roots = [];
          if (Math.abs(A) < 1e-12) { if (Math.abs(Bq) > 1e-12) roots.push(-C / Bq); }
          else { const dsc = Bq * Bq - 4 * A * C; if (dsc >= 0) { const q = Math.sqrt(dsc); roots.push((-Bq + q) / (2 * A), (-Bq - q) / (2 * A)); } }
          for (const u of roots) if (u > 0 && u < 1) { const q = evalSeg(p, s, u); add(q[0], q[1]); }
        }
      }
      p = e;
    }
    return { x0, y0, x1, y1 };
  };
  const mod = a => ((a % (2 * Math.PI)) + 2 * Math.PI) % (2 * Math.PI);
  /** the exact box of some vectors (``flatOf`` is not needed any more; it is accepted so that old calls work) */
  G.bbox = vectors => {
    let x0 = Infinity, y0 = Infinity, x1 = -Infinity, y1 = -Infinity;
    for (const v of vectors) for (const c of v.contours) {
      const b = G.contourBBox(c);
      if (b.x0 < x0) x0 = b.x0; if (b.x1 > x1) x1 = b.x1; if (b.y0 < y0) y0 = b.y0; if (b.y1 > y1) y1 = b.y1;
    }
    return x0 <= x1 ? { x0, y0, x1, y1 } : null;
  };

  const segDist = (px, py, ax, ay, bx, by) => {
    const dx = bx - ax, dy = by - ay, l2 = dx * dx + dy * dy;
    const t = l2 < 1e-18 ? 0 : clampN(((px - ax) * dx + (py - ay) * dy) / l2, 0, 1);
    return hyp(px - ax - t * dx, py - ay - t * dy);
  };
  /** the distance of a point to the line of a vector */
  G.distance = (flat, x, y) => {
    let best = Infinity;
    for (const c of flat) {
      const p = c.pts;
      for (let i = 0; i + 1 < p.length; i++) best = Math.min(best, segDist(x, y, p[i][0], p[i][1], p[i + 1][0], p[i + 1][1]));
      if (c.closed && p.length > 1) best = Math.min(best, segDist(x, y, p[p.length - 1][0], p[p.length - 1][1], p[0][0], p[0][1]));
    }
    return best;
  };
  /** the id of the vector whose line is nearest to the point and nearer than ``tol`` (the later one when they tie), or null */
  G.hit = (vectors, flatOf, x, y, tol, ok = () => true) => {
    let best = null, bd = tol;
    for (let i = vectors.length - 1; i >= 0; i--) {
      const v = vectors[i];
      const b = G.vbox(v);                                    // (the box first: only the few vectors near the point are looked at closely)
      if (!b || x < b.x0 - tol || x > b.x1 + tol || y < b.y0 - tol || y > b.y1 + tol) continue;
      if (!ok(v)) continue;
      const d = G.distance(flatOf(v), x, y);
      if (d < bd) { bd = d; best = v.id; }
    }
    return best;
  };
  const boxes = new WeakMap();
  /** the exact box of one vector, made once (a vector object is never changed: a changed vector is a new object) */
  G.vbox = v => {
    let b = boxes.get(v);
    if (b === undefined) { b = G.bbox([v]); boxes.set(v, b); }
    return b;
  };
  /** whether a vector is taken by a window (wholly inside) or a crossing (touching) of a rectangle */
  G.vInRect = (v, flatOf, r, crossing) => {
    const b = G.vbox(v);
    if (!b) return false;
    if (!crossing) return b.x0 >= r.x0 && b.x1 <= r.x1 && b.y0 >= r.y0 && b.y1 <= r.y1;
    if (b.x1 < r.x0 || b.x0 > r.x1 || b.y1 < r.y0 || b.y0 > r.y1) return false;
    return G.inRect(flatOf(v), r, true);
  };
  /** a window takes what lies wholly inside; a crossing also what only touches it */
  G.inRect = (flat, r, crossing) => {
    let x0 = Infinity, y0 = Infinity, x1 = -Infinity, y1 = -Infinity;
    for (const c of flat) for (const [x, y] of c.pts) { x0 = Math.min(x0, x); x1 = Math.max(x1, x); y0 = Math.min(y0, y); y1 = Math.max(y1, y); }
    if (x0 > x1) return false;
    if (!crossing) return x0 >= r.x0 && x1 <= r.x1 && y0 >= r.y0 && y1 <= r.y1;
    if (x1 < r.x0 || x0 > r.x1 || y1 < r.y0 || y0 > r.y1) return false;
    for (const c of flat) {                                   // (a point inside, or a piece that crosses an edge)
      const p = c.pts;
      for (let i = 0; i < p.length; i++) if (p[i][0] >= r.x0 && p[i][0] <= r.x1 && p[i][1] >= r.y0 && p[i][1] <= r.y1) return true;
      const edges = [[r.x0, r.y0, r.x1, r.y0], [r.x1, r.y0, r.x1, r.y1], [r.x1, r.y1, r.x0, r.y1], [r.x0, r.y1, r.x0, r.y0]];
      const n = c.closed ? p.length : p.length - 1;
      for (let i = 0; i < n; i++) {
        const a = p[i], b = p[(i + 1) % p.length];
        for (const e of edges) if (cross(a, b, [e[0], e[1]], [e[2], e[3]])) return true;
      }
    }
    return false;
  };
  const cross = (a, b, c, d) => {
    const o = (p, q, r) => (q[0] - p[0]) * (r[1] - p[1]) - (q[1] - p[1]) * (r[0] - p[0]);
    return o(a, b, c) * o(a, b, d) < 0 && o(c, d, a) * o(c, d, b) < 0;
  };

  // ------------------------------------------------------------------------------------ shapes
  const KAPPA = 0.5522847498307936;
  G.polyline = (pts, closed) => {
    pts = pts.map(p => p.slice());
    if (closed && pts.length > 1 && hyp(pts[0][0] - pts[pts.length - 1][0], pts[0][1] - pts[pts.length - 1][1]) < 1e-9) pts.pop();
    const segs = pts.slice(1).map(p => ["L", p[0], p[1]]);
    if (closed && pts.length > 2) segs.push(["L", pts[0][0], pts[0][1]]);
    return { closed: !!(closed && pts.length > 2), start: pts[0], segs };
  };
  G.rectangle = (x0, y0, x1, y1) => G.polyline([[Math.min(x0, x1), Math.min(y0, y1)], [Math.max(x0, x1), Math.min(y0, y1)],
    [Math.max(x0, x1), Math.max(y0, y1)], [Math.min(x0, x1), Math.max(y0, y1)]], true);
  G.circle = (cx, cy, r) => ({ closed: true, start: [cx + r, cy], segs: [["A", cx - r, cy, 1], ["A", cx + r, cy, 1]] });
  G.ellipse = (cx, cy, rx, ry) => {
    const k = KAPPA;
    return { closed: true, start: [cx + rx, cy], segs: [
      ["C", cx + rx, cy + k * ry, cx + k * rx, cy + ry, cx, cy + ry], ["C", cx - k * rx, cy + ry, cx - rx, cy + k * ry, cx - rx, cy],
      ["C", cx - rx, cy - k * ry, cx - k * rx, cy - ry, cx, cy - ry], ["C", cx + k * rx, cy - ry, cx + rx, cy - k * ry, cx + rx, cy]] };
  };
  G.polygon = (cx, cy, r, sides, rotationDeg = 0) => {
    sides = Math.max(3, Math.round(sides));
    const base = rotationDeg * Math.PI / 180 + Math.PI / 2;
    return G.polyline(Array.from({ length: sides }, (_, i) => [cx + r * Math.cos(base + 2 * Math.PI * i / sides), cy + r * Math.sin(base + 2 * Math.PI * i / sides)]), true);
  };

  // ------------------------------------------------------------------------------------ node editing (one contour)
  /** a node moved by (dx, dy), with the handles of the beziers that belong to it */
  G.moveNode = (c, k, dx, dy) => {
    const o = copyC(c), n = o.segs.length;
    const shiftEnd = i => {
      const s = o.segs[i];
      if (s[0] === "C") { s[3] += dx; s[4] += dy; s[5] += dx; s[6] += dy; } else { s[1] += dx; s[2] += dy; }
    };
    const shiftStart = i => { const s = o.segs[i]; if (s && s[0] === "C") { s[1] += dx; s[2] += dy; } };
    if (k === 0) {
      o.start[0] += dx; o.start[1] += dy;
      shiftStart(0);
      if (o.closed) shiftEnd(n - 1);
    } else {
      shiftEnd(k - 1);
      shiftStart(k);
    }
    return o;
  };
  /** the handle ``which`` (1: the first one of the bezier segment i, 2: the second) moved */
  G.moveHandle = (c, i, which, dx, dy) => {
    const o = copyC(c), s = o.segs[i];
    if (which === 1) { s[1] += dx; s[2] += dy; } else { s[3] += dx; s[4] += dy; }
    return o;
  };
  /** the segment i cut at u: a node more */
  G.insertNode = (c, i, u) => {
    const o = copyC(c), parts = splitSeg(segStart(c, i), c.segs[i], u);
    o.segs.splice(i, 1, parts[0], parts[1]);
    return o;
  };
  /** the node k left out: its two segments become one line.  null when too little would be left. */
  G.deleteNode = (c, k) => {
    const o = copyC(c), n = o.segs.length;
    if (c.closed ? n <= 3 : n < 1) return null;
    if (c.closed) {
      if (k === 0) { o.start = G.segEnd(o.segs[0]).slice(); o.segs.shift(); const p = o.segs[o.segs.length - 1]; o.segs[o.segs.length - 1] = ["L", o.start[0], o.start[1]]; return o; }
      const a = segStart(c, k - 1), b = G.segEnd(c.segs[k]);
      o.segs.splice(k - 1, 2, ["L", b[0], b[1]]);
      void a;
      return o;
    }
    if (k === 0) { o.start = G.segEnd(o.segs[0]).slice(); o.segs.shift(); return o; }
    if (k === n) { o.segs.pop(); return o; }
    const b = G.segEnd(c.segs[k]);
    o.segs.splice(k - 1, 2, ["L", b[0], b[1]]);
    return o;
  };
  /** a closed contour that starts at node k (the segments in the same order, from there) */
  G.setStart = (c, k) => {
    if (!c.closed || k <= 0) return copyC(c);
    const o = copyC(c);
    o.start = G.nodes(c)[k].slice();
    o.segs = o.segs.slice(k).concat(o.segs.slice(0, k));
    return o;
  };
  /** the contour opened at node k: one open contour for a closed one, two for an open one (the second may be missing) */
  G.breakAt = (c, k) => {
    if (c.closed) { const o = G.setStart(c, k); o.closed = false; return [o]; }
    const a = copyC(c), n = c.segs.length;
    if (k <= 0 || k >= n) return [a];
    const b = { closed: false, start: G.nodes(c)[k].slice(), segs: c.segs.slice(k).map(s => s.slice()) };
    a.segs = a.segs.slice(0, k);
    return [a, b];
  };
  /** an open contour closed with a line from its end back to its start */
  G.closeContour = c => {
    const o = copyC(c);
    if (o.closed || !o.segs.length) return o;
    const e = G.segEnd(o.segs[o.segs.length - 1]);
    if (hyp(e[0] - o.start[0], e[1] - o.start[1]) > 1e-9) o.segs.push(["L", o.start[0], o.start[1]]);
    o.closed = true;
    return o;
  };
  /** the segment i as another kind: "L" (a line to its end), "C" (a bezier along the same line) */
  G.convertSpan = (c, i, kind) => {
    const o = copyC(c), s = o.segs[i], p0 = segStart(c, i), e = G.segEnd(s);
    if (kind === "L") { o.segs[i] = ["L", e[0], e[1]]; return o; }
    if (kind === "C" && s[0] !== "C") {
      const mid = u => [p0[0] + (e[0] - p0[0]) * u, p0[1] + (e[1] - p0[1]) * u];
      const a = mid(1 / 3), b = mid(2 / 3);
      o.segs[i] = ["C", a[0], a[1], b[0], b[1], e[0], e[1]];
    }
    return o;
  };

  // ------------------------------------------------------------------------------------ snapping
  /** the points a drawing or a moved selection may cling to: the sheet (corners, middles of the sides, centre) and the
   *  nodes of the other vectors (at most ``cap``) */
  G.snapPoints = (sheet, nodesOf, skip, cap = 4000) => {
    const W = sheet.width, H = sheet.height, out = [];
    for (const x of [0, W / 2, W]) for (const y of [0, H / 2, H]) out.push({ p: [x, y], kind: "sheet" });
    for (const v of sheet.vectors) {
      if (skip && skip.has(v.id)) continue;
      for (const c of v.contours) for (const p of nodesOf(c)) { if (out.length >= cap) return out; out.push({ p, kind: "node" }); }
    }
    return out;
  };
  /** the nearest snap point within ``tol`` of a point: {p, kind} or null */
  G.snap = (pt, cands, tol) => {
    let best = null, bd = tol;
    for (const c of cands) { const d = hyp(c.p[0] - pt[0], c.p[1] - pt[1]); if (d < bd) { bd = d; best = c; } }
    return best;
  };

  root.IyiGeom = G;
})(typeof window !== "undefined" ? window : globalThis);

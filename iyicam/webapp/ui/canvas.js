/* iyiCAM web: the drawing in the middle, as the desktop's canvas: rulers, the sheet with its grid, the vectors in their layer
 * colours, the toolpaths over them, the selection with its handles, guides, snapping, the drawing tools.
 *
 * Sheet coordinates are millimetres with Y up; the view is {s, ox, oy}: screen x = ox + s·X, screen y = oy − s·Y (CSS pixels,
 * measured from the top left of the canvas, whose first 22 pixels are the rulers).  The view of each tab is kept in the tab.
 * One finger acts as the mouse; two fingers pinch to zoom and pan; the wheel zooms at the cursor; the middle button (or
 * Space and the left one) pans.  What a gesture changes in the project is handed to `canvas.api` (set by actions.js). */
import { h } from "./dom.js";
import G from "./geom.js";
import { app, emit, on, settings, units } from "./state.js";

const R = 22;                                   // the rulers
const TP_COLORS = ["#e6550d", "#3182bd", "#31a354", "#756bb1", "#de2d26", "#636363", "#fd8d3c", "#6baed6"];
/** How wide a tool cuts ``depth`` below its tip (a V-bit or an engraving tool: wider the deeper it goes). */
export function toolWidthAt(t, depth) {
  const D = t.diameter || 1;
  if (t.type === "v_bit" || t.type === "engraving") return Math.min(D, (t.tip_diameter || 0) + 2 * Math.max(0, depth) * Math.tan(Math.max(1e-3, Math.min(179.9, t.angle || 90)) * Math.PI / 360));
  if (t.type === "ball_nose") { const R = D / 2, h = Math.max(0, depth); return h >= R ? D : 2 * Math.sqrt(Math.max(0, R * R - (R - h) ** 2)); }
  return D;
}
/** The colour of a cut ``depth`` deep: the toolpath's colour, light near the surface, full at ``deepest``. */
export function solidColor(hex, depth, deepest) {
  const n = parseInt(hex.slice(1), 16), t = deepest > 0 ? Math.max(0, Math.min(1, depth / deepest)) : 0, k = 0.62 * (1 - t);
  const ch = v => Math.round(v + (255 - v) * k);
  return `rgb(${ch((n >> 16) & 255)},${ch((n >> 8) & 255)},${ch(n & 255)})`;
}
const NICE = [1, 2, 5];

const PALETTE = {
  light: { canvas: "#e4e7ee", sheet: "#fffdf8", shadow: "#cdd1da", edge: "#8a8f9c", ruler: "#f2f4f8", tick: "#8d95a6", rtext: "#5b6477",
    grid: [0, 0, 0], text: "#1c2333", selection: "#e31a1c", hfill: "#ffffff", hedge: "#1f78b4", dark: false },
  dark: { canvas: "#1b1e23", sheet: "#2a2f38", shadow: "#20242b", edge: "#6c7485", ruler: "#23272e", tick: "#5f6778", rtext: "#959eb0",
    grid: [255, 255, 255], text: "#d9dee7", selection: "#f08c8c", hfill: "#2e343e", hedge: "#8fb0f5", dark: true },
};
export const isDark = () => document.documentElement.dataset.resolved === "dark";

/** a layer colour that shows on a dark sheet too (as the desktop's theme.adapt) */
function adapt(hex, dark) {
  if (!dark) return hex;
  const n = parseInt(hex.slice(1), 16), r = n >> 16, g = (n >> 8) & 255, b = n & 255;
  const l = (0.299 * r + 0.587 * g + 0.114 * b) / 255;
  if (l > 0.5) return hex;
  const k = 0.55;
  return `rgb(${Math.round(r + (255 - r) * k)},${Math.round(g + (255 - g) * k)},${Math.round(b + (255 - b) * k)})`;
}

export class DrawingView {
  constructor(host) {
    this.host = host;
    this.canvas = h("canvas", { class: "drawing", tabIndex: 0 });
    host.append(this.canvas);
    this.ctx = this.canvas.getContext("2d");
    this.api = {};
    this.pointers = new Map();
    this.drag = null;
    this.hover = null;                 // the cursor on the sheet
    this.space = false;
    this.snapDirty = true;
    this.snapCand = [];
    this.marker = null;                // where the last snap clung
    this.draft = null;                 // the shape being drawn: {tool, pts, cur}
    this.frame = 0;
    this._wire();
    new ResizeObserver(() => this._resize()).observe(host);
    for (const e of ["project", "selection", "results", "view", "settings", "docs", "theme"]) on(e, () => { if (e === "project") this.snapDirty = true; this.redraw(); });
  }

  get doc() { return app.cur; }
  get size() { return { w: this.canvas.clientWidth, h: this.canvas.clientHeight }; }
  redraw() { if (!this.frame) this.frame = requestAnimationFrame(() => { this.frame = 0; this.draw(); }); }
  _resize() {
    const dpr = window.devicePixelRatio || 1, { w, h: hh } = this.size;
    if (!w || !hh) return;
    const first = !this._sized;
    this.canvas.width = Math.round(w * dpr); this.canvas.height = Math.round(hh * dpr);
    this._sized = true;
    if (first && this.doc && !this.doc.view) this.zoomExtents();
    this.redraw();
  }

  // ------------------------------------------------------------------------------------------ the view
  w2s(x, y) { const v = this.view; return [v.ox + v.s * x, v.oy - v.s * y]; }
  s2w(x, y) { const v = this.view; return [(x - v.ox) / v.s, (v.oy - y) / v.s]; }
  get view() {
    const d = this.doc;
    if (!d) return { s: 1, ox: R, oy: this.size.h - R };
    if (!d.view) d.view = { s: 0.5, ox: R + 20, oy: this.size.h - R - 20 };
    return d.view;
  }
  fit(box, margin = 28) {
    const { w, h: hh } = this.size, d = this.doc;
    if (!d || !w) return;
    const bw = Math.max(box.x1 - box.x0, 1e-6), bh = Math.max(box.y1 - box.y0, 1e-6);
    const aw = Math.max(w - R - 2 * margin, 20), ah = Math.max(hh - R - 2 * margin, 20);
    const s = Math.min(aw / bw, ah / bh);
    d.view = { s, ox: R + (w - R - s * bw) / 2 - s * box.x0, oy: R + (hh - R + s * bh) / 2 + s * box.y0 };
    this.redraw();
  }
  zoomExtents() { const sh = this.doc && this.doc.sheet; if (sh) this.fit({ x0: 0, y0: 0, x1: sh.width, y1: sh.height }); }
  zoomSelection() {
    const sel = this.doc && this.doc.selected();
    const b = sel && sel.length ? G.bbox(sel) : null;
    if (b) this.fit(b, 60); else this.zoomExtents();
  }
  zoomAt(factor, sx, sy) {
    const d = this.doc;
    if (!d) return;
    const v = this.view, s = Math.min(Math.max(v.s * factor, 0.005), 400), k = s / v.s;
    v.ox = sx - (sx - v.ox) * k; v.oy = sy - (sy - v.oy) * k; v.s = s;
    this.redraw();
  }

  // ------------------------------------------------------------------------------------------ snapping
  _snapCandidates() {
    if (!this.snapDirty) return this.snapCand;
    const d = this.doc;
    const out = [];
    if (d) {                                                    // (the sheet's corners, middles and centre, and the nodes of the vectors)
      const W = d.sheet.width, H = d.sheet.height;
      for (const x of [0, W / 2, W]) for (const y of [0, H / 2, H]) out.push({ p: [x, y], kind: "sheet" });
      for (const v of d.sheet.vectors) for (const c of v.contours) for (const p of G.nodes(c)) { if (out.length > 8000) break; out.push({ p, kind: "node", v: v.id }); }
    }
    this.snapCand = out;
    this.snapDirty = false;
    return this.snapCand;
  }
  /** the point a drawing or a moved selection clings to (null: nowhere); `skip`: ids of vectors that are being moved */
  snap(pt, skip) {
    const d = this.doc, tol = settings.snapReach / this.view.s;
    let best = null, bd = tol, msg = "";
    if (!d) return null;
    if (settings.snapVectors) {
      for (const c of this._snapCandidates()) {
        if (skip && c.v && skip.has(c.v)) continue;
        const dd = Math.hypot(c.p[0] - pt[0], c.p[1] - pt[1]);
        if (dd < bd) { bd = dd; best = { p: c.p.slice(), kind: c.kind }; msg = c.kind === "sheet" ? "sheet" : "node"; }
      }
    }
    if (!best && settings.snapEdges) {
      const vs = G.hitAll(d.sheet.vectors, G.flatOf, pt[0], pt[1], tol, v => !(skip && skip.has(v.id)));
      const v = vs.length && d.sheet.vectors.find(x => x.id === vs[0]);
      if (v) { for (const c of v.contours) { const q = G.nearest(c, pt[0], pt[1]); if (q && q.d < bd) { bd = q.d; best = { p: q.pt, kind: "edge" }; msg = "edge"; } } }
    }
    if (!best && settings.snapGrid) {
      const g = Math.max(settings.grid, 1e-6);
      const q = [Math.round(pt[0] / g) * g, Math.round(pt[1] / g) * g];
      if (Math.hypot(q[0] - pt[0], q[1] - pt[1]) < Math.max(tol, g * 0.5)) { best = { p: q, kind: "grid" }; msg = "grid"; }
    }
    let level = null;
    if (settings.snapLevel) {                          // (level with a node: the same X or the same Y)
      const out = best ? best.p.slice() : pt.slice();
      let bx = tol, by = tol, lx = null, ly = null;
      for (const c of this._snapCandidates()) {
        if (skip && c.v && skip.has(c.v)) continue;
        if (Math.abs(c.p[0] - out[0]) < bx) { bx = Math.abs(c.p[0] - out[0]); lx = c.p; }
        if (Math.abs(c.p[1] - out[1]) < by) { by = Math.abs(c.p[1] - out[1]); ly = c.p; }
      }
      for (const g of d.guides) { if (g.axis === "x" && Math.abs(g.pos - out[0]) < bx) { bx = 0; lx = [g.pos, out[1]]; } if (g.axis === "y" && Math.abs(g.pos - out[1]) < by) { by = 0; ly = [out[0], g.pos]; } }
      if (!best && (lx || ly)) { best = { p: out, kind: "level" }; msg = "level"; }
      if (lx && !(best && best.kind === "node" && Math.abs(best.p[0] - lx[0]) > 1e-9)) { best.p[0] = lx[0]; level = level || {}; level.x = lx; }
      if (ly && !(best && best.kind === "node" && Math.abs(best.p[1] - ly[1]) > 1e-9)) { best.p[1] = ly[1]; level = level || {}; level.y = ly; }
    }
    this.marker = best ? { ...best, level } : null;
    app.snapMsg = msg ? "Snap: " + msg : "";
    return best ? best.p : null;
  }
  snapped(pt, skip) { return this.snap(pt, skip) || pt; }

  // ------------------------------------------------------------------------------------------ drawing
  draw() {
    const ctx = this.ctx, { w, h: hh } = this.size, dpr = window.devicePixelRatio || 1;
    if (!w || !hh) return;
    const dark = isDark(), P = dark ? PALETTE.dark : PALETTE.light;
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.fillStyle = P.canvas; ctx.fillRect(0, 0, w, hh);
    const d = this.doc;
    if (!d) { this._empty(ctx, w, hh, P); return; }
    const v = this.view, sh = d.sheet, s = v.s;
    ctx.save();
    ctx.beginPath(); ctx.rect(R, R, w - R, hh - R); ctx.clip();
    const [x0, y0] = this.w2s(0, sh.height), [x1, y1] = this.w2s(sh.width, 0);
    ctx.fillStyle = P.shadow; ctx.fillRect(x0 + 3, y0 + 3, x1 - x0, y1 - y0);
    ctx.fillStyle = P.sheet; ctx.fillRect(x0, y0, x1 - x0, y1 - y0);
    this._grid(ctx, P, sh, x0, y0, x1, y1);
    if (settings.showSim && d.sim && d.sim.img && d.sim.sheet === sh.id) {
      const e = d.sim.extent, [ax, ay] = this.w2s(e[0], e[3]), [bx, by] = this.w2s(e[2], e[1]);
      ctx.imageSmoothingEnabled = s < 3;
      ctx.drawImage(d.sim.img, ax, ay, bx - ax, by - ay);
    }
    ctx.strokeStyle = P.edge; ctx.lineWidth = 1.2; ctx.strokeRect(x0, y0, x1 - x0, y1 - y0);

    const layers = new Map(d.project.layers.map(l => [l.id, l]));
    if (settings.showStarts) this._starts(ctx, sh, layers, s);
    ctx.save();
    ctx.translate(v.ox, v.oy); ctx.scale(s, -s);
    ctx.lineJoin = "round"; ctx.lineCap = "round";
    const selected = new Set(d.selection);
    const dragging = this.drag && this.drag.kind === "move" && this.drag.moved;
    if (settings.showToolpaths && settings.solidToolpaths) this._solid(ctx, d, sh);   // (under the vectors, as in ArtCAM)
    // vectors, a layer's colour each
    const byColor = new Map();
    for (const vec of sh.vectors) {
      if (selected.has(vec.id)) continue;
      const l = layers.get(vec.layer);
      if (l && !l.visible) continue;
      const col = adapt(l ? l.color : "#000000", dark);
      let p = byColor.get(col);
      if (!p) { p = []; byColor.set(col, p); }
      p.push([vec, l && l.locked]);
    }
    ctx.lineWidth = 1.3 / s;
    for (const [col, list] of byColor) {
      ctx.strokeStyle = col;
      for (const [vec, locked] of list) { ctx.globalAlpha = locked ? 0.55 : 1; ctx.stroke(G.pathOf(vec)); }
    }
    ctx.globalAlpha = 1;
    if (settings.showToolpaths) this._toolpaths(ctx, d, sh, s, dark, layers);
    for (const f of app.overlays) f(ctx, this, "world");          // (what features draw over the drawing: a backplot, nodes being edited)
    // the selection (moved or transformed while it is dragged)
    ctx.save();
    if (this.drag && this.drag.matrix) ctx.transform(...this.drag.matrix);
    else if (dragging) ctx.translate(this.drag.dx, this.drag.dy);
    ctx.strokeStyle = P.selection; ctx.lineWidth = 2 / s; ctx.setLineDash([6 / s, 4 / s]);
    for (const vec of sh.vectors) if (selected.has(vec.id)) { const l = layers.get(vec.layer); if (!l || l.visible) ctx.stroke(G.pathOf(vec)); }
    ctx.restore();
    ctx.setLineDash([]);
    ctx.restore();                                           // (back to screen pixels)

    this._guides(ctx, d, w, hh);
    this._selectionBox(ctx, P, d, selected);
    this._draft(ctx, P, s);
    this._marquee(ctx);
    for (const f of app.overlays) f(ctx, this, "screen");
    if (this.marker) this._markerDraw(ctx);
    // the origin: red along X, green along Y
    const [ox, oy] = this.w2s(0, 0);
    ctx.lineWidth = 2;
    ctx.strokeStyle = "#c00000"; ctx.beginPath(); ctx.moveTo(ox, oy); ctx.lineTo(ox + 30, oy); ctx.stroke();
    ctx.strokeStyle = "#008000"; ctx.beginPath(); ctx.moveTo(ox, oy); ctx.lineTo(ox, oy - 30); ctx.stroke();
    ctx.restore();
    this._rulers(ctx, P, w, hh);
  }

  _empty(ctx, w, hh, P) {
    ctx.fillStyle = P.rtext; ctx.font = "14px system-ui, sans-serif"; ctx.textAlign = "center";
    ctx.fillText("No project open — File ▸ New or File ▸ Open", w / 2, hh / 2);
  }

  _grid(ctx, P, sh, x0, y0, x1, y1) {
    const s = this.view.s;
    let step = Math.max(settings.grid, 0.01);
    while (step * s < 8) step *= step * s * 5 < 8 ? 10 : 5;
    const [g0, g1, g2] = P.grid, cl = (a, b, lo, hi) => [Math.max(a, lo), Math.min(b, hi)];
    const draw = (major) => {
      ctx.strokeStyle = `rgba(${g0},${g1},${g2},${major ? 0.18 : 0.08})`; ctx.lineWidth = 1; ctx.beginPath();
      const every = major ? 5 : 1;
      for (let i = 0; i * step <= sh.width + 1e-9; i++) {
        if ((i % 5 === 0) !== major && major) continue;
        if (!major && i % 5 === 0) continue;
        const [x] = this.w2s(i * step, 0); const xx = Math.round(x) + 0.5;
        if (xx < R || xx > this.size.w) continue;
        ctx.moveTo(xx, y0); ctx.lineTo(xx, y1);
      }
      for (let j = 0; j * step <= sh.height + 1e-9; j++) {
        if ((j % 5 === 0) !== major) continue;
        const [, y] = this.w2s(0, j * step); const yy = Math.round(y) + 0.5;
        if (yy < R || yy > this.size.h) continue;
        ctx.moveTo(x0, yy); ctx.lineTo(x1, yy);
      }
      ctx.stroke();
      void every; void cl;
    };
    draw(false); draw(true);
  }

  /** The toolpaths drawn solid, as ArtCAM does: every cut as wide as the tool at its depth, the shallow ones first and
   * light, the deepest last in the full colour of the toolpath: what shows is the shape the tool leaves. */
  _solid(ctx, d, sh) {
    const res = d.results;
    if (!res) return;
    const tpIndex = new Map(d.project.toolpaths.map((t, i) => [t.id, i]));
    const thick = sh.thickness || (d.project.material && d.project.material.thickness) || 0;
    const layers = [];
    let deepest = thick;
    for (const r of res.results) {
      if (r.sheet !== sh.id || r.enabled === false || !r.preview || d.hiddenToolpaths.has(r.toolpath)) continue;
      const idx = tpIndex.get(r.toolpath);
      if (idx === undefined) continue;
      const tp = d.project.toolpaths[idx], color = TP_COLORS[idx % TP_COLORS.length];
      for (const cut of r.preview.cuts) {
        if (!(cut.z < 0)) continue;
        const path = new Path2D();
        for (let k = 0; k + 1 < cut.p.length; k += 2) (k ? path.lineTo(cut.p[k], cut.p[k + 1]) : path.moveTo(cut.p[k], cut.p[k + 1]));
        layers.push({ z: cut.z, path, color, tool: tp.tool });
        deepest = Math.max(deepest, -cut.z);
      }
      for (const dr of r.preview.drills || []) {
        const path = new Path2D(); path.moveTo(dr[0], dr[1]); path.lineTo(dr[0], dr[1]);
        layers.push({ z: dr[2], path, color, tool: tp.tool });
      }
    }
    if (!layers.length) return;
    layers.sort((a, b) => b.z - a.z);
    const full = thick || deepest;
    ctx.save(); ctx.lineCap = "round"; ctx.lineJoin = "round";
    for (const l of layers) {
      const depth = -l.z, t = l.tool || {};
      ctx.lineWidth = Math.max(1e-3, toolWidthAt(t, depth));
      ctx.strokeStyle = solidColor(l.color, depth, full);
      ctx.stroke(l.path);
    }
    ctx.restore();
  }

  _toolpaths(ctx, d, sh, s, dark, layers) {
    const res = d.results;
    if (!res) return;
    const dim = d.stale ? 0.45 : 1;
    const tpIndex = new Map(d.project.toolpaths.map((t, i) => [t.id, i]));
    const vmap = settings.showCutOrder || settings.showStarts ? new Map(sh.vectors.map(x => [x.id, x])) : null;
    for (const r of res.results) {
      if (r.sheet !== sh.id || r.enabled === false || !r.preview || d.hiddenToolpaths.has(r.toolpath)) continue;
      const idx = tpIndex.get(r.toolpath);
      if (idx === undefined) continue;
      const tp = d.project.toolpaths[idx], color = TP_COLORS[idx % TP_COLORS.length];
      let c = r._cache;
      if (!c) {
        const feed = new Path2D();
        for (const cut of r.preview.cuts) for (let k = 0; k + 1 < cut.p.length; k += 2) (k ? feed.lineTo(cut.p[k], cut.p[k + 1]) : feed.moveTo(cut.p[k], cut.p[k + 1]));
        const rapid = new Path2D(), rp = r.preview.rapids || [];
        for (let k = 0; k + 3 < rp.length; k += 4) { rapid.moveTo(rp[k], rp[k + 1]); rapid.lineTo(rp[k + 2], rp[k + 3]); }
        c = { feed, rapid };
        Object.defineProperty(r, "_cache", { value: c, enumerable: false });
      }
      ctx.globalAlpha = dim;
      if (settings.showToolWidth && tp.tool) {
        const wd = tp.tool.type === "v_bit" || tp.tool.type === "engraving"
          ? Math.max(tp.tool.tip_diameter || 0.2, 2 * Math.tan((tp.tool.angle || 60) * Math.PI / 360) * Math.min(-(r.stats.zmin || 0), 20)) : tp.tool.diameter;
        ctx.strokeStyle = color; ctx.globalAlpha = dim * 0.22; ctx.lineWidth = wd; ctx.stroke(c.feed); ctx.globalAlpha = dim;
      }
      if (settings.solidToolpaths) {                            // (the solid shows the cuts: the moves in the air over it)
        if (settings.showRoute) { ctx.strokeStyle = "rgba(220,30,30,0.55)"; ctx.lineWidth = 0.8 / s; ctx.setLineDash([5 / s, 4 / s]); ctx.stroke(c.rapid); ctx.setLineDash([]); }
        if (settings.showDirections) this._arrows(ctx, r, s, color);
        ctx.globalAlpha = 1;
        continue;
      }
      ctx.strokeStyle = color; ctx.lineWidth = 1 / s; ctx.stroke(c.feed);
      if (settings.showRoute) { ctx.strokeStyle = "rgba(220,30,30,0.55)"; ctx.lineWidth = 0.8 / s; ctx.setLineDash([5 / s, 4 / s]); ctx.stroke(c.rapid); ctx.setLineDash([]); }
      ctx.strokeStyle = color; ctx.lineWidth = 1 / s;
      for (const dr of r.preview.drills || []) {
        const rr = 5 / s;
        ctx.beginPath(); ctx.arc(dr[0], dr[1], rr, 0, 7); ctx.moveTo(dr[0] - rr, dr[1]); ctx.lineTo(dr[0] + rr, dr[1]); ctx.moveTo(dr[0], dr[1] - rr); ctx.lineTo(dr[0], dr[1] + rr); ctx.stroke();
      }
      if (settings.showDirections) this._arrows(ctx, r, s, color);
      ctx.globalAlpha = 1;
    }
    if (settings.showCutOrder && vmap) {                       // (numbers where each cut starts: in screen pixels)
      ctx.save(); ctx.setTransform(window.devicePixelRatio || 1, 0, 0, window.devicePixelRatio || 1, 0, 0);
      ctx.font = "bold 10px system-ui, sans-serif"; ctx.textAlign = "center"; ctx.textBaseline = "middle";
      for (const r of res.results) {
        if (r.sheet !== sh.id || r.enabled === false || !r.order || d.hiddenToolpaths.has(r.toolpath)) continue;
        r.order.forEach((key, i) => {
          const [vid, ci] = key.split(":"), vec = vmap.get(vid), ct = vec && vec.contours[+ci];
          if (!ct) return;
          const [x, y] = this.w2s(ct.start[0], ct.start[1]);
          ctx.fillStyle = `hsl(${240 - 240 * (r.order.length > 1 ? i / (r.order.length - 1) : 0)} 75% 42%)`;
          ctx.beginPath(); ctx.arc(x, y, 8, 0, 7); ctx.fill();
          ctx.fillStyle = "#fff"; ctx.fillText(String(i + 1), x, y + 0.5);
        });
      }
      ctx.restore();
    }
  }

  _arrows(ctx, r, s, color) {
    const every = 60 / s, a = 4.5 / s;
    ctx.fillStyle = color; ctx.beginPath();
    for (const cut of r.preview.cuts) {
      const p = cut.p; let acc = every / 2;
      for (let i = 0; i + 3 < p.length; i += 2) {
        const dx = p[i + 2] - p[i], dy = p[i + 3] - p[i + 1], dd = Math.hypot(dx, dy);
        if (dd < 1e-9) continue;
        let at = acc;
        const th = Math.atan2(dy, dx);
        while (at < dd) {
          const x = p[i] + dx * at / dd, y = p[i + 1] + dy * at / dd;
          ctx.moveTo(x + a * Math.cos(th), y + a * Math.sin(th));
          ctx.lineTo(x + a * Math.cos(th + 2.5), y + a * Math.sin(th + 2.5));
          ctx.lineTo(x + a * Math.cos(th - 2.5), y + a * Math.sin(th - 2.5));
          ctx.closePath();
          at += every;
        }
        acc = at - dd;
      }
    }
    ctx.fill();
  }

  _starts(ctx, sh, layers) {
    ctx.save();
    ctx.fillStyle = "#1a9641"; ctx.strokeStyle = "#fff"; ctx.lineWidth = 1.2;
    for (const vec of sh.vectors) {
      const l = layers.get(vec.layer);
      if (l && !l.visible) continue;
      for (const c of vec.contours) { const [x, y] = this.w2s(c.start[0], c.start[1]); ctx.beginPath(); ctx.arc(x, y, 3.5, 0, 7); ctx.fill(); ctx.stroke(); }
    }
    ctx.restore();
  }

  _guides(ctx, d, w, hh) {
    ctx.save(); ctx.strokeStyle = "#0097a7"; ctx.lineWidth = 1; ctx.setLineDash([5, 4]);
    for (const g of d.guides) {
      ctx.beginPath();
      if (g.axis === "x") { const [x] = this.w2s(g.pos, 0); ctx.moveTo(x, R); ctx.lineTo(x, hh); } else { const [, y] = this.w2s(0, g.pos); ctx.moveTo(R, y); ctx.lineTo(w, y); }
      ctx.stroke();
    }
    ctx.restore();
  }

  /** the box round the selection with its handles (corners and sides scale, the ring above turns) */
  handles() {
    const d = this.doc;
    if (!d || app.tool !== "select" || !settings.resizeHandles) return null;
    const sel = d.selected();
    const b = sel.length ? G.bbox(sel) : null;
    if (!b) return null;
    const m = this.drag && this.drag.matrix;
    const pts = [[b.x0, b.y1], [(b.x0 + b.x1) / 2, b.y1], [b.x1, b.y1], [b.x1, (b.y0 + b.y1) / 2], [b.x1, b.y0], [(b.x0 + b.x1) / 2, b.y0], [b.x0, b.y0], [b.x0, (b.y0 + b.y1) / 2]];
    void m;
    return { box: b, pts, rot: [(b.x0 + b.x1) / 2, b.y1] };
  }
  _selectionBox(ctx, P, d, selected) {
    if (!selected.size) return;
    const hd = this.handles();
    if (!hd) return;
    let b = hd.box;
    if (this.drag && this.drag.kind === "move" && this.drag.moved) b = { x0: b.x0 + this.drag.dx, x1: b.x1 + this.drag.dx, y0: b.y0 + this.drag.dy, y1: b.y1 + this.drag.dy };
    const [ax, ay] = this.w2s(b.x0, b.y1), [bx, by] = this.w2s(b.x1, b.y0);
    ctx.save();
    ctx.strokeStyle = P.hedge; ctx.lineWidth = 1; ctx.setLineDash([2, 3]); ctx.strokeRect(ax, ay, bx - ax, by - ay); ctx.setLineDash([]);
    if (this.drag && this.drag.matrix) { ctx.restore(); return; }
    const pts = [[ax, ay], [(ax + bx) / 2, ay], [bx, ay], [bx, (ay + by) / 2], [bx, by], [(ax + bx) / 2, by], [ax, by], [ax, (ay + by) / 2]];
    ctx.fillStyle = P.hfill; ctx.strokeStyle = P.hedge; ctx.lineWidth = 1.2;
    for (const [x, y] of pts) { ctx.fillRect(x - 4, y - 4, 8, 8); ctx.strokeRect(x - 4, y - 4, 8, 8); }
    const rx = (ax + bx) / 2, ry = ay - 24;
    ctx.beginPath(); ctx.moveTo(rx, ay); ctx.lineTo(rx, ry + 4); ctx.setLineDash([2, 2]); ctx.stroke(); ctx.setLineDash([]);
    ctx.beginPath(); ctx.arc(rx, ry, 4.5, 0, 7); ctx.fill(); ctx.stroke();
    ctx.restore();
  }
  _handleAt(sx, sy) {
    const hd = this.handles();
    if (!hd) return null;
    const touch = this._touchy ? 8 : 0;
    const [ax, ay] = this.w2s(hd.box.x0, hd.box.y1), [bx, by] = this.w2s(hd.box.x1, hd.box.y0);
    const pts = [[ax, ay], [(ax + bx) / 2, ay], [bx, ay], [bx, (ay + by) / 2], [bx, by], [(ax + bx) / 2, by], [ax, by], [ax, (ay + by) / 2]];
    if (Math.hypot(sx - (ax + bx) / 2, sy - (ay - 24)) < 8 + touch) return { kind: "rotate" };
    for (let i = 0; i < 8; i++) if (Math.abs(sx - pts[i][0]) < 7 + touch && Math.abs(sy - pts[i][1]) < 7 + touch) return { kind: "scale", i, box: hd.box };
    return null;
  }

  _draft(ctx, P, s) {
    const dr = this.draft;
    if (!dr || !dr.shape) return;
    ctx.save();
    ctx.translate(this.view.ox, this.view.oy); ctx.scale(s, -s);
    ctx.strokeStyle = "#ec4899"; ctx.lineWidth = 1.6 / s; ctx.setLineDash([5 / s, 4 / s]);
    for (const c of dr.shape) {
      ctx.beginPath();
      G.flatten(c).forEach(([x, y], i) => (i ? ctx.lineTo(x, y) : ctx.moveTo(x, y)));
      if (c.closed) ctx.closePath();
      ctx.stroke();
    }
    ctx.restore();
  }
  _marquee(ctx) {
    const dg = this.drag;
    if (!dg || dg.kind !== "marquee" || !dg.moved) return;
    const crossing = dg.cur[0] < dg.start[0];
    ctx.save();
    ctx.fillStyle = crossing ? "rgba(46,160,67,0.12)" : "rgba(47,109,246,0.12)";
    ctx.strokeStyle = crossing ? "#2ea043" : "#2f6df6"; ctx.setLineDash(crossing ? [5, 3] : []); ctx.lineWidth = 1;
    const x = Math.min(dg.start[0], dg.cur[0]), y = Math.min(dg.start[1], dg.cur[1]), ww = Math.abs(dg.cur[0] - dg.start[0]), hh = Math.abs(dg.cur[1] - dg.start[1]);
    ctx.fillRect(x, y, ww, hh); ctx.strokeRect(x, y, ww, hh);
    ctx.restore();
  }
  _markerDraw(ctx) {
    const m = this.marker;
    const [x, y] = this.w2s(m.p[0], m.p[1]);
    ctx.save();
    ctx.strokeStyle = "#ec4899"; ctx.lineWidth = 1.2;
    if (m.level) {
      ctx.setLineDash([3, 3]); ctx.beginPath();
      if (m.level.x) { const [, y2] = this.w2s(m.level.x[0], m.level.x[1]); ctx.moveTo(x, y); ctx.lineTo(x, y2); }
      if (m.level.y) { const [x2] = this.w2s(m.level.y[0], m.level.y[1]); ctx.moveTo(x, y); ctx.lineTo(x2, y); }
      ctx.stroke(); ctx.setLineDash([]);
    }
    ctx.beginPath();
    if (m.kind === "node" || m.kind === "sheet") ctx.rect(x - 5, y - 5, 10, 10); else ctx.arc(x, y, 5, 0, 7);
    ctx.stroke();
    ctx.restore();
  }

  _rulers(ctx, P, w, hh) {
    ctx.fillStyle = P.ruler; ctx.fillRect(0, 0, w, R); ctx.fillRect(0, 0, R, hh);
    ctx.fillStyle = P.canvas; ctx.fillRect(0, 0, R, R);
    ctx.strokeStyle = P.tick; ctx.fillStyle = P.rtext; ctx.lineWidth = 1; ctx.font = "10px system-ui, sans-serif";
    const d = this.doc;
    if (!d) return;
    const v = this.view, f = units.factor;
    const target = 60 / (v.s / f);                                  // display units per 60px
    const mag = 10 ** Math.floor(Math.log10(target));
    const step = (NICE.map(n => n * mag).concat([10 * mag]).find(x => x >= target) || 10 * mag) * f;   // in mm
    const minor = step / 5;
    ctx.textAlign = "left"; ctx.textBaseline = "alphabetic";
    // top ruler
    const xa = (R - v.ox) / v.s, xb = (w - v.ox) / v.s;
    for (let i = Math.floor(xa / minor); i * minor <= xb; i++) {
      const x = Math.round(v.ox + v.s * i * minor) + 0.5, major = i % 5 === 0;
      if (x < R) continue;
      ctx.beginPath(); ctx.moveTo(x, R); ctx.lineTo(x, major ? R - 10 : R - 5); ctx.stroke();
      if (major) ctx.fillText(String(+(i * minor / f).toFixed(4)), x + 3, 10);
    }
    const ya = (v.oy - hh) / v.s, yb = (v.oy - R) / v.s;
    for (let i = Math.floor(ya / minor); i * minor <= yb; i++) {
      const y = Math.round(v.oy - v.s * i * minor) + 0.5, major = i % 5 === 0;
      if (y < R) continue;
      ctx.beginPath(); ctx.moveTo(R, y); ctx.lineTo(major ? R - 10 : R - 5, y); ctx.stroke();
      if (major) { ctx.save(); ctx.translate(10, y - 3); ctx.rotate(-Math.PI / 2); ctx.fillText(String(+(i * minor / f).toFixed(4)), 0, 0); ctx.restore(); }
    }
    ctx.strokeStyle = P.edge; ctx.beginPath(); ctx.moveTo(R + 0.5, R); ctx.lineTo(R + 0.5, hh); ctx.moveTo(R, R + 0.5); ctx.lineTo(w, R + 0.5); ctx.stroke();
    if (this.hover) {                                                // (where the cursor is, on both rulers)
      const [sx, sy] = this.w2s(this.hover[0], this.hover[1]);
      ctx.strokeStyle = "#d84315"; ctx.beginPath(); ctx.moveTo(sx + 0.5, 0); ctx.lineTo(sx + 0.5, R); ctx.moveTo(0, sy + 0.5); ctx.lineTo(R, sy + 0.5); ctx.stroke();
    }
  }

  // ------------------------------------------------------------------------------------------ pointer
  _wire() {
    const c = this.canvas;
    c.addEventListener("pointerdown", e => this._down(e));
    c.addEventListener("pointermove", e => this._move(e));
    c.addEventListener("pointerup", e => this._up(e));
    c.addEventListener("pointercancel", e => this._up(e, true));
    c.addEventListener("pointerleave", () => { if (!this.drag) { this.hover = null; app.cursor = null; this.marker = null; this.redraw(); emit("cursor"); } });
    c.addEventListener("wheel", e => {
      e.preventDefault();
      const r = c.getBoundingClientRect();
      const k = Math.exp(-e.deltaY * (e.ctrlKey ? 0.01 : 0.0016) * (e.deltaMode === 1 ? 16 : 1));
      this.zoomAt(k, e.clientX - r.left, e.clientY - r.top);
    }, { passive: false });
    c.addEventListener("dblclick", e => this.api.dblclick && this.api.dblclick(this._pt(e), e));
    c.addEventListener("contextmenu", e => { e.preventDefault(); if (this.draft && this.draft.tool === "polyline") this.api.finishPolyline && this.api.finishPolyline(); });
  }
  _pt(e) { const r = this.canvas.getBoundingClientRect(); return { sx: e.clientX - r.left, sy: e.clientY - r.top }; }
  _world(e) { const { sx, sy } = this._pt(e); return this.s2w(sx, sy); }

  _down(e) {
    this.canvas.focus({ preventScroll: true });
    this.canvas.setPointerCapture(e.pointerId);
    this._touchy = e.pointerType !== "mouse";
    const { sx, sy } = this._pt(e);
    this.pointers.set(e.pointerId, { sx, sy });
    if (!this.doc) return;
    if (this.pointers.size === 2) { this._startPinch(); return; }
    if (this.pointers.size > 2) return;
    const tool = app.tool;
    // rulers: a guide is pulled from them
    if (e.button === 0 && (sx < R || sy < R) && !(sx < R && sy < R)) {
      const axis = sy < R ? "y" : "x", d = this.doc;
      let g = d.guides.find(q => (q.axis === axis) && (axis === "y" ? Math.abs(this.w2s(0, q.pos)[1] - sy) < 5 : Math.abs(this.w2s(q.pos, 0)[0] - sx) < 5));
      if (!g) { g = { axis, pos: 0 }; d.guides.push(g); }
      const w = this.s2w(sx, sy);
      g.pos = axis === "y" ? w[1] : w[0];
      this.drag = { kind: "guide", g, axis };
      this.redraw();
      return;
    }
    if (e.button === 1 || (e.button === 0 && this.space)) { this.drag = { kind: "pan", last: [sx, sy] }; this.canvas.style.cursor = "grabbing"; return; }
    if (e.button !== 0) return;
    const w = this.s2w(sx, sy);
    this.hover = w;
    if (tool === "select") return this._downSelect(e, sx, sy, w);
    this.api.toolDown && this.api.toolDown(tool, w, e, this);
  }

  _downSelect(e, sx, sy, w) {
    const d = this.doc;
    const hnd = this._handleAt(sx, sy);
    if (hnd && d.selection.length) {
      const sel = d.selected();
      this.drag = { kind: "handle", h: hnd, start: w, box: G.bbox(sel), matrix: null, sel };
      return;
    }
    const tol = (this._touchy ? 14 : 6) / this.view.s;
    const layers = new Map(d.project.layers.map(l => [l.id, l]));
    const ok = v => { const l = layers.get(v.layer); return !l || (l.visible && !l.locked); };
    let id;
    if (e.altKey) { const all = G.hitAll(d.sheet.vectors, G.flatOf, w[0], w[1], tol, ok); id = G.nextAfter(all, d.selection.length === 1 ? d.selection[0] : null); }
    else id = G.hit(d.sheet.vectors, G.flatOf, w[0], w[1], tol, ok);
    if (id) {
      const already = d.selection.includes(id);
      if (e.shiftKey) { this.api.select(already ? d.selection.filter(x => x !== id) : [...d.selection, id]); return; }
      if (e.ctrlKey || e.metaKey) { if (already) { this.api.select(d.selection.filter(x => x !== id)); return; } this.api.select([...d.selection, id]); }
      else if (!already) this.api.select([id]);
      this.drag = { kind: "move", start: w, startScreen: [sx, sy], dx: 0, dy: 0, moved: false, copy: e.ctrlKey || e.metaKey, skip: new Set(this.doc.selection.concat(id)) };
      return;
    }
    this.drag = { kind: "marquee", start: [sx, sy], cur: [sx, sy], moved: false, add: e.shiftKey || e.ctrlKey || e.metaKey };
  }

  _startPinch() {
    const [a, b] = [...this.pointers.values()];
    this.drag = null; this.draft = null;
    const v = this.view;
    this.pinch = { d0: Math.hypot(a.sx - b.sx, a.sy - b.sy) || 1, mid0: [(a.sx + b.sx) / 2, (a.sy + b.sy) / 2], view0: { ...v } };
    this.pinch.w0 = this.s2w(this.pinch.mid0[0], this.pinch.mid0[1]);
    this.redraw();
  }

  _move(e) {
    const { sx, sy } = this._pt(e);
    const p = this.pointers.get(e.pointerId);
    if (p) { p.sx = sx; p.sy = sy; }
    if (this.pinch && this.pointers.size >= 2) {
      const [a, b] = [...this.pointers.values()], v = this.view;
      const dist = Math.hypot(a.sx - b.sx, a.sy - b.sy), mid = [(a.sx + b.sx) / 2, (a.sy + b.sy) / 2];
      v.s = Math.min(Math.max(this.pinch.view0.s * dist / this.pinch.d0, 0.005), 400);
      v.ox = mid[0] - v.s * this.pinch.w0[0]; v.oy = mid[1] + v.s * this.pinch.w0[1];
      this.redraw();
      return;
    }
    if (!this.doc) return;
    const w = this.s2w(sx, sy);
    this.hover = w; app.cursor = w;
    if (!this.drag) this._hoverCursor(sx, sy, w, e);
    emit("cursor");
    const dg = this.drag;
    if (!dg) {
      if (app.tool !== "select") { this.snap(w); this.api.toolHover && this.api.toolHover(app.tool, this.marker ? this.marker.p : w, e, this); this.redraw(); }
      else if (this.marker) { this.marker = null; this.redraw(); }
      else this.redraw();
      return;
    }
    if (dg.kind === "pan") { const v = this.view; v.ox += sx - dg.last[0]; v.oy += sy - dg.last[1]; dg.last = [sx, sy]; this.redraw(); }
    else if (dg.kind === "guide") { dg.g.pos = dg.axis === "y" ? w[1] : w[0]; this.redraw(); }
    else if (dg.kind === "marquee") { dg.cur = [sx, sy]; if (Math.hypot(sx - dg.start[0], sy - dg.start[1]) > 3) dg.moved = true; this.redraw(); }
    else if (dg.kind === "move") {
      if (!dg.moved && Math.hypot(sx - dg.startScreen[0], sy - dg.startScreen[1]) > 4) dg.moved = true;
      if (dg.moved) {
        let dx = w[0] - dg.start[0], dy = w[1] - dg.start[1];
        const sel = this.doc.selected(), b = sel.length ? G.bbox(sel) : null;
        if (b) {                                                   // (the box of the selection clings: its corners, middle ...)
          const probes = [[b.x0, b.y0], [b.x1, b.y0], [b.x0, b.y1], [b.x1, b.y1], [(b.x0 + b.x1) / 2, (b.y0 + b.y1) / 2]];
          let best = null;
          for (const q of probes) { const t = this.snap([q[0] + dx, q[1] + dy], dg.skip); if (t) { const dd = Math.hypot(t[0] - q[0] - dx, t[1] - q[1] - dy); if (!best || dd < best.dd) best = { dd, ax: t[0] - q[0], ay: t[1] - q[1], m: this.marker }; } }
          if (best) { dx = best.ax; dy = best.ay; this.marker = best.m; } else this.marker = null;
        }
        if (e.shiftKey) [dx, dy] = G.axisLock(dx, dy);
        dg.dx = dx; dg.dy = dy;
        app.message = `Move ${units.fmt(dx)}, ${units.fmt(dy)}` + (dg.copy ? "  (copy)" : "");
        emit("message");
        this.redraw();
      }
    } else if (dg.kind === "handle") this._dragHandle(dg, w, e);
    else if (this.api.toolMove) this.api.toolMove(app.tool, w, e, this);
  }

  _dragHandle(dg, w, e) {
    const b = dg.box;
    if (dg.h.kind === "rotate") {
      const cx = (b.x0 + b.x1) / 2, cy = (b.y0 + b.y1) / 2;
      let ang = (Math.atan2(w[1] - cy, w[0] - cx) - Math.PI / 2) * 180 / Math.PI;
      if (e.ctrlKey || e.shiftKey || this._touchy) ang = Math.round(ang / 15) * 15;
      dg.angle = ang;
      dg.matrix = G.rotateMap(ang, cx, cy);
      app.message = `Rotate ${ang.toFixed(1)}°`;
    } else {
      const i = dg.h.i, corner = i % 2 === 0;
      const pts = [[b.x0, b.y1], [(b.x0 + b.x1) / 2, b.y1], [b.x1, b.y1], [b.x1, (b.y0 + b.y1) / 2], [b.x1, b.y0], [(b.x0 + b.x1) / 2, b.y0], [b.x0, b.y0], [b.x0, (b.y0 + b.y1) / 2]];
      const grab = pts[i], anchor = pts[(i + 4) % 8];
      let sx = 1, sy = 1;
      const wd = grab[0] - anchor[0], ht = grab[1] - anchor[1];
      if (corner) {
        const len2 = wd * wd + ht * ht;
        const k = len2 > 1e-12 ? ((w[0] - anchor[0]) * wd + (w[1] - anchor[1]) * ht) / len2 : 1;
        sx = sy = Math.max(k, 0.001);
      } else if (Math.abs(wd) > 1e-9) sx = Math.max((w[0] - anchor[0]) / wd, 0.001);
      else if (Math.abs(ht) > 1e-9) sy = Math.max((w[1] - anchor[1]) / ht, 0.001);
      dg.sx = sx; dg.sy = sy; dg.anchor = anchor;
      dg.matrix = [sx, 0, 0, sy, anchor[0] - sx * anchor[0], anchor[1] - sy * anchor[1]];
      app.message = `Scale ${(sx * 100).toFixed(1)} % × ${(sy * 100).toFixed(1)} %`;
    }
    emit("message");
    this.redraw();
  }

  _hoverCursor(sx, sy, w, e) {
    let cur = "default";
    if (this.space) cur = "grab";
    else if ((sx < R || sy < R)) cur = "crosshair";
    else if (app.tool !== "select") cur = "crosshair";
    else {
      const hd = this._handleAt(sx, sy);
      if (hd) cur = hd.kind === "rotate" ? "alias" : ["nwse-resize", "ns-resize", "nesw-resize", "ew-resize", "nwse-resize", "ns-resize", "nesw-resize", "ew-resize"][hd.i];
    }
    this.canvas.style.cursor = cur;
    void w; void e;
  }

  _up(e, cancelled) {
    const had = this.pointers.has(e.pointerId);
    this.pointers.delete(e.pointerId);
    if (this.pinch) { if (this.pointers.size < 2) this.pinch = null; this.drag = null; return; }
    if (!had) return;
    const dg = this.drag;
    this.canvas.style.cursor = "";
    if (!dg) { if (!cancelled && this.doc && app.tool !== "select" && this.api.toolUp) this.api.toolUp(app.tool, this._world(e), e, this); return; }
    this.drag = null;
    if (cancelled) { this.redraw(); return; }
    const { sx, sy } = this._pt(e), w = this.s2w(sx, sy);
    if (dg.kind === "guide") {
      const d = this.doc;
      if (sx < R || sy < R) d.guides.splice(d.guides.indexOf(dg.g), 1);   // (dragged back onto the ruler: gone)
    } else if (dg.kind === "marquee") {
      if (dg.moved) {
        const a = this.s2w(dg.start[0], dg.start[1]), b = this.s2w(sx, sy);
        const r = { x0: Math.min(a[0], b[0]), x1: Math.max(a[0], b[0]), y0: Math.min(a[1], b[1]), y1: Math.max(a[1], b[1]) };
        const crossing = sx < dg.start[0];
        const layers = new Map(this.doc.project.layers.map(l => [l.id, l]));
        const ids = this.doc.sheet.vectors.filter(v => { const l = layers.get(v.layer); return (!l || (l.visible && !l.locked)) && G.vInRect(v, G.flatOf, r, crossing); }).map(v => v.id);
        this.api.select(dg.add ? [...new Set([...this.doc.selection, ...ids])] : ids);
      } else if (!dg.add) this.api.select([]);
    } else if (dg.kind === "move") {
      this.marker = null;
      if (dg.moved && (Math.abs(dg.dx) > 1e-9 || Math.abs(dg.dy) > 1e-9)) this.api.moveSelection(dg.dx, dg.dy, dg.copy);
    } else if (dg.kind === "handle") {
      this.marker = null;
      if (dg.matrix) this.api.transformSelection(dg.matrix, dg.h.kind === "rotate" ? "Rotate" : "Scale", dg);
    } else if (this.api.toolUp) this.api.toolUp(app.tool, w, e, this);
    this.redraw();
  }
}

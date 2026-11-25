/* iyiCAM web: drawing and vectors: Edit Nodes, Star, Text, Trim, start points, Text on Curve, Trace Bitmap, Vector Library, sheet outline.
 * `install()` runs once the window is built: it registers the menu items it makes work (`A.register(id, {run, enabled, checked})`),
 * drawing tools (`app.extraTools[name] = {down, move, hover, up}`) and what it draws over the drawing (`app.overlays`).
 *
 * The work is the engine's (iyicam/service/ops_draw.py: `vector_edit`, `start_points`, `start_probe`, `trim`, `text`, `text_on_curve`,
 * `trace_bitmap`, `veclib`, `sheet_outline`).  What is here is what the pointer does: finding what is under it, drawing what is
 * going on, and asking the engine for the change (while a node is dragged the latest position is asked for, one call at a time). */
import G from "./geom.js";
import { h } from "./dom.js";
import { app, on, settings, setSetting, units } from "./state.js";
import { run, report } from "./engine.js";
import { alertBox } from "./dialog.js";
import { openFile, toB64 } from "./files.js";
import * as A from "./actions.js";
import { textDialog, textOnCurveDialog, traceDialog } from "./dialogs/draw.js";
import { libraryApi } from "./panels_draw.js";

const doc = () => app.cur;
const view = () => app.ui.canvas;
const hasDoc = () => !!doc();
const hasSel = () => !!doc() && doc().selection.length > 0;
const dist = (a, b) => Math.hypot(a[0] - b[0], a[1] - b[1]);

/** one engine call at a time with the newest request: a pointer that moves faster than the engine answers asks only for the latest place */
class Latest {
  constructor(fn) { this.fn = fn; this.busy = null; this.next = null; }
  push(arg) { this.next = arg; if (!this.busy) this.busy = this._loop(); return this.busy; }
  async _loop() {
    try { while (this.next) { const a = this.next; this.next = null; try { await this.fn(a); } catch (e) { this.error = e; } } } finally { this.busy = null; }
  }
  idle() { return this.busy || Promise.resolve(); }
}

const layerMap = d => new Map(d.project.layers.map(l => [l.id, l]));
const editable = (vec, layers) => { const l = layers.get(vec.layer); return !l || (l.visible && !l.locked); };

const HINTS = {
  nodes: "Node editing: drag nodes and bezier handles, double-click a span to insert a node, Del deletes selected nodes, right-click for span/node options, Esc to finish.",
  star: () => `Star (${settings.starPoints || 5} points, +/- to change): drag from the centre to a point.`,
  text: "Text: click where the text baseline should start.",
  trim: "Trim: click the part of a vector to remove; it is cut where it crosses other vectors.",
  start: "Start point: click on a vector, or inside it, to start its cut there; drag to slide it along the vector (corners and middles cling, Alt: free). ] and [ step to the next / previous corner, right click releases it to smart start points, Esc when done.",
};
const hint = t => { const x = HINTS[t]; return typeof x === "function" ? x() : x; };
function useTool(t) { A.setTool(t); A.status(hint(t)); }
const toolItem = t => ({ run: () => useTool(t), checked: () => app.tool === t, enabled: hasDoc });

/** a small menu at the pointer: items [{label, run}] and "-" */
function popup(e, items) {
  document.querySelectorAll(".popmenu.draw").forEach(m => m.remove());
  const m = h("div", { class: "popmenu draw" }, items.map(it => (it === "-" ? h("div", { class: "psep" }) : h("div", { class: "item", onclick: () => { m.remove(); it.run(); } }, it.label))));
  document.body.append(m);
  Object.assign(m.style, { left: Math.max(4, Math.min(e.clientX, innerWidth - m.offsetWidth - 4)) + "px", top: Math.max(4, Math.min(e.clientY, innerHeight - m.offsetHeight - 4)) + "px" });
  const away = ev => { if (m.contains(ev.target)) return; m.remove(); document.removeEventListener("pointerdown", away, true); };
  setTimeout(() => document.addEventListener("pointerdown", away, true), 0);     // (a press on the menu itself must reach its item)
}

/** replace vectors of the open sheet by the engine's answer (`vectors`) and drop `removed` ones; one step of Undo */
function applyVectors(label, r) {
  const d = doc();
  d.edit(label, p => {
    const sh = p.sheets.find(s => s.id === d.sheetId), by = new Map(r.vectors.map(v => [v.id, v])), gone = new Set(r.removed || []);
    sh.vectors = sh.vectors.filter(v => !gone.has(v.id)).map(v => by.get(v.id) || v);
    d.selection = d.selection.filter(i => !gone.has(i));
  });
}
const vectorsOf = ids => doc().sheet.vectors.filter(v => ids.has(v.id));

// ============================================================================================ Edit Nodes
const NT = { sel: new Set(), drag: null, span: null };
const nkey = (vid, ci, ni) => `${vid}|${ci}|${ni}`;
const nparse = k => { const [v, c, n] = k.split("|"); return [v, +c, +n]; };
const nodeTargets = () => { const d = doc(), layers = layerMap(d); return d.selected().filter(v => editable(v, layers)); };

/** what is under the pointer: a bezier handle, a node, or a span of a selected vector */
function pickNode(sx, sy) {
  const v = view(), tol = v._touchy ? 12 : 7;
  let best = null;
  const near = (x, y) => { const [qx, qy] = v.w2s(x, y); return Math.hypot(qx - sx, qy - sy); };
  for (const vec of nodeTargets()) vec.contours.forEach((c, ci) => {
    c.segs.forEach((s, si) => {
      if (s[0] !== "C") return;
      for (const [which, x, y] of [[1, s[1], s[2]], [2, s[3], s[4]]]) { const dd = near(x, y); if (dd <= tol && (!best || dd < best.d)) best = { d: dd, kind: "ctrl", vid: vec.id, ci, si, which }; }
    });
    G.nodes(c).forEach((pt, ni) => { const dd = near(pt[0], pt[1]); if (dd <= tol && (!best || dd < best.d || (best.kind === "ctrl" && dd < best.d + 2))) best = { d: dd, kind: "node", vid: vec.id, ci, ni }; });
  });
  if (best) return best;
  const w = v.s2w(sx, sy), tolw = 6 / v.view.s;
  for (const vec of nodeTargets()) for (let ci = 0; ci < vec.contours.length; ci++) {
    const q = G.nearest(vec.contours[ci], w[0], w[1]);
    if (q && q.d <= tolw) return { kind: "span", vid: vec.id, ci, si: q.i, t: q.u };
  }
  return null;
}

const nodeTool = {
  down(w, e, v) {
    if (e.button !== 0) return;
    const { sx, sy } = v._pt(e), d = doc(), hit = pickNode(sx, sy);
    if (!hit) {
      NT.sel.clear(); NT.span = null;
      const layers = layerMap(d), id = G.hit(d.sheet.vectors, G.flatOf, w[0], w[1], 6 / v.view.s, vec => editable(vec, layers));
      if (id) A.select(e.shiftKey ? [...new Set([...d.selection, id])] : [id]);
      v.redraw(); return;
    }
    if (hit.kind === "span") { NT.span = hit; NT.sel.clear(); v.redraw(); return; }
    NT.span = null;
    if (hit.kind === "node") {
      const k = nkey(hit.vid, hit.ci, hit.ni);
      if (e.shiftKey) { if (NT.sel.has(k)) NT.sel.delete(k); else NT.sel.add(k); }
      else if (!NT.sel.has(k)) NT.sel = new Set([k]);
      if (!NT.sel.has(k)) { v.redraw(); return; }
    }
    const ids = hit.kind === "node" ? new Set([...NT.sel].map(k => nparse(k)[0])) : new Set([hit.vid]);
    const orig = new Map(vectorsOf(ids).map(x => [x.id, x]));
    const base = hit.kind === "node" ? G.nodes(orig.get(hit.vid).contours[hit.ci])[hit.ni] : null;
    NT.drag = { hit, ids, orig, base, snap: d.snap(), sel0: d.selection.slice(), moved: false,
      latest: new Latest(async payload => {
        const r = await app.engine.call("vector_edit", payload);
        const by = new Map(r.vectors.map(x => [x.id, x]));
        d.sheet.vectors = d.sheet.vectors.map(x => by.get(x.id) || x);        // (the project shows it at once; Undo gets the snapshot taken at the press)
        v.redraw();
      }) };
    v.drag = { kind: "draw" };
    v.redraw();
  },
  move(w, e, v) {
    const nd = NT.drag;
    if (!nd) return;
    const t = v.snapped(w, nd.ids), vecs = [...nd.orig.values()];
    nd.moved = true;
    if (nd.hit.kind === "ctrl") nd.latest.push({ vectors: vecs, action: "move_control", vector: nd.hit.vid, contour: nd.hit.ci, seg: nd.hit.si, which: nd.hit.which, to: t });
    else {
      nd.latest.push({ vectors: vecs, action: "move_nodes", nodes: [...NT.sel].map(nparse), dx: t[0] - nd.base[0], dy: t[1] - nd.base[1] });
      A.status(`Move ${units.fmt(t[0] - nd.base[0])}, ${units.fmt(t[1] - nd.base[1])}`);
    }
  },
  hover(p, e, v) { v.marker = null; },
  async up(w, e, v) {
    const nd = NT.drag, d = doc();
    NT.drag = null;
    if (!nd || !d) return;
    if (!nd.moved) return;
    await nd.latest.idle();
    if (nd.latest.error) {                                                    // (the engine refused: the vectors go back)
      d.sheet.vectors = d.sheet.vectors.map(x => nd.orig.get(x.id) || x);
      report(nd.latest.error, "Edit nodes");
    } else { d.checkpoint("Edit nodes", nd.snap, nd.sel0); d.changed(); }
    v.redraw();
  },
};

async function editVectors(label, payload) {
  const d = doc();
  try {
    const r = await run("vector_edit", payload, { label });
    applyVectors(label, r);
    return r;
  } catch (e) { report(e, label); return null; }
}
async function deleteNodes() {
  const keys = [...NT.sel].map(nparse);
  if (!keys.length) return;
  const r = await editVectors("Delete nodes", { vectors: vectorsOf(new Set(keys.map(k => k[0]))), action: "delete_nodes", nodes: keys });
  if (r) NT.sel.clear();
  view().redraw();
}
function nodeMenu(e, hit) {
  const d = doc(), vec = d.sheet.vectors.find(x => x.id === hit.vid);
  if (!vec) return;
  const c = vec.contours[hit.ci], me = (label, extra) => () => editVectors(label, { vectors: [vec], vector: hit.vid, contour: hit.ci, ...extra }).then(() => { NT.sel.clear(); NT.span = null; });
  const items = [];
  if (hit.kind === "node") {
    const seg = hit.ni < c.segs.length ? hit.ni : c.segs.length - 1, t = hit.ni < c.segs.length ? 0 : 1;
    if (c.closed) items.push({ label: "Set as start point", run: me("Set start point", { action: "set_start", point: G.nodes(c)[hit.ni] }) });
    items.push({ label: "Delete node", run: () => { NT.sel = new Set([nkey(hit.vid, hit.ci, hit.ni)]); deleteNodes(); } });
    items.push({ label: "Break at node", run: me("Break vector", { action: "break", seg, t }) });
  } else {
    items.push({ label: "Insert node here", run: me("Insert node", { action: "insert", seg: hit.si, t: hit.t }) });
    if (c.closed) items.push({ label: "Set start point here", run: me("Set start point", { action: "set_start", point: G.evalSeg(G.segStart(c, hit.si), c.segs[hit.si], hit.t) }) });
    items.push({ label: "Break here", run: me("Break vector", { action: "break", seg: hit.si, t: hit.t }) }, "-");
    for (const [kind, label] of [["line", "Convert span to line"], ["arc", "Convert span to arc"], ["bezier", "Convert span to bezier"]]) items.push({ label, run: me("Convert span", { action: "convert", seg: hit.si, kind }) });
  }
  items.push("-", { label: "Reverse direction", run: me("Reverse", { action: "reverse" }) },
    c.closed ? { label: "Open (break at start)", run: me("Open vector", { action: "open" }) } : { label: "Close", run: me("Close vector", { action: "close" }) });
  popup(e, items);
}

function drawNodes(ctx, v) {
  const d = doc(), R = 4;
  ctx.save();
  if (NT.span) {                                                               // the span under the pointer
    const vec = d.sheet.vectors.find(x => x.id === NT.span.vid), c = vec && vec.contours[NT.span.ci], s = c && c.segs[NT.span.si];
    if (s) {
      const p0 = G.segStart(c, NT.span.si);
      ctx.strokeStyle = "#ff7f00"; ctx.lineWidth = 3.5; ctx.beginPath();
      for (let k = 0; k <= 24; k++) { const q = G.evalSeg(p0, s, k / 24), [x, y] = v.w2s(q[0], q[1]); if (k) ctx.lineTo(x, y); else ctx.moveTo(x, y); }
      ctx.stroke();
    }
  }
  for (const vec of nodeTargets()) vec.contours.forEach((c, ci) => {
    ctx.strokeStyle = "#888888"; ctx.lineWidth = 1; ctx.fillStyle = "#ffffff";
    let p0 = c.start;
    for (const s of c.segs) {
      if (s[0] === "C") {
        const a = v.w2s(p0[0], p0[1]), c1 = v.w2s(s[1], s[2]), c2 = v.w2s(s[3], s[4]), b = v.w2s(s[5], s[6]);
        ctx.beginPath(); ctx.moveTo(...a); ctx.lineTo(...c1); ctx.moveTo(...b); ctx.lineTo(...c2); ctx.stroke();
        for (const q of [c1, c2]) { ctx.beginPath(); ctx.arc(q[0], q[1], R - 1, 0, 7); ctx.fill(); ctx.stroke(); }
      }
      p0 = G.segEnd(s);
    }
    ctx.strokeStyle = "#1f3b73";
    G.nodes(c).forEach((pt, ni) => {
      const [x, y] = v.w2s(pt[0], pt[1]), sz = R + (ni === 0 ? 1.5 : 0);
      ctx.fillStyle = NT.sel.has(nkey(vec.id, ci, ni)) ? "#e31a1c" : ni === 0 ? "#1a9641" : "#ffffff";
      ctx.fillRect(x - sz, y - sz, 2 * sz, 2 * sz); ctx.strokeRect(x - sz, y - sz, 2 * sz, 2 * sz);
    });
  });
  ctx.restore();
}

// ============================================================================================ Star
function starShape(a, b, e) {
  const { b: q } = G.dragShape("polygon", a, b, { ctrl: e && (e.ctrlKey || e.metaKey) });
  const r = Math.hypot(q[0] - a[0], q[1] - a[1]);
  return r < 1e-9 ? null : [G.star(a[0], a[1], r, r * 0.45, settings.starPoints || 5, Math.atan2(q[1] - a[1], q[0] - a[0]) * 180 / Math.PI - 90)];
}
const starTool = {
  down(w, e, v) {
    const p = v.snapped(w), dr = v.draft;
    if (dr && dr.mode === "wait") {                                            // (the second click of a click-click star)
      const shape = starShape(dr.a, p, e);
      v.draft = null;
      if (shape) A.addContours(shape, "Draw star");
      v.redraw(); return;
    }
    v.draft = { tool: "star", a: p, mode: "drag", shape: null, s0: [e.clientX, e.clientY] };
    v.drag = { kind: "draw" };
  },
  move(w, e, v) {
    const dr = v.draft;
    if (!dr || dr.mode !== "drag") return;
    dr.shape = starShape(dr.a, v.snapped(w), e);
    if (dr.shape) A.status(`Radius ${units.fmt(Math.hypot(...[0, 1].map(i => v.snapped(w)[i] - dr.a[i])))}`);
    v.redraw();
  },
  hover(p, e, v) {
    const dr = v.draft;
    if (dr && dr.mode === "wait") dr.shape = starShape(dr.a, p, e);
  },
  up(w, e, v) {
    const dr = v.draft;
    if (!dr || dr.mode !== "drag") return;
    if (Math.hypot(e.clientX - dr.s0[0], e.clientY - dr.s0[1]) <= 5) { dr.mode = "wait"; dr.shape = null; return; }
    const shape = starShape(dr.a, v.snapped(w), e);
    v.draft = null;
    if (shape) A.addContours(shape, "Draw star");
    v.redraw();
  },
};
/** + / - change the number of points of the star (and the sides of the polygon), as the desktop's */
function pointsKey(e) {
  if (app.tool !== "star" && app.tool !== "polygon") return false;
  const up = e.key === "+" || e.key === "=", down = e.key === "-" || e.key === "_";
  if (!up && !down) return false;
  const star = app.tool === "star", key = star ? "starPoints" : "polySides", cur = settings[key] || (star ? 5 : 6);
  setSetting(key, Math.max(3, Math.min(100, cur + (up ? 1 : -1))));
  A.status(star ? hint("star") : `Polygon (${settings.polySides} sides, +/- to change): drag from the centre to a corner.`);
  return true;
}

// ============================================================================================ Text
const textTool = {
  async down(w, e, v) {
    const p = v.snapped(w), d = doc();
    const vals = await textDialog();
    if (!vals) return;
    try {
      const r = await run("text", { project: d.project, sheet: d.sheetId, layer: d.layerId, at: p, ...vals }, { label: "Text" });
      d.replace("Draw text", r.project, r.selection);
    } catch (err) { report(err, "Text"); }
  },
};
async function textOnCurve() {
  const d = doc();
  if (d.selection.length !== 1) { A.status("Select one vector to put the text on."); await alertBox("Select one vector to put the text on.", "Text on curve"); return; }
  const vals = await textOnCurveDialog();
  if (!vals) return;
  try {
    const r = await run("text_on_curve", { project: d.project, sheet: d.sheetId, selection: d.selection, layer: d.layerId, ...vals }, { label: "Text on curve" });
    d.replace("Text on curve", r.project, r.selection);
  } catch (err) { report(err, "Text on curve"); }
}

// ============================================================================================ Trim
const TR = { hover: null };
const hitVector = (w, v) => { const d = doc(), layers = layerMap(d); return G.hit(d.sheet.vectors, G.flatOf, w[0], w[1], 6 / v.view.s, vec => { const l = layers.get(vec.layer); return !l || l.visible; }); };
const trimTool = {
  hover(p, e, v) { v.marker = null; const { sx, sy } = v._pt(e), id = hitVector(v.s2w(sx, sy), v); if (id !== TR.hover) { TR.hover = id; v.redraw(); } },
  async down(w, e, v) {
    const id = hitVector(w, v), d = doc();
    if (!id) return;
    try {
      const r = await run("trim", { project: d.project, sheet: d.sheetId, vector: id, point: w }, { label: "Trim" });
      if (r.project) d.replace("Trim", r.project); else A.status(r.message);
      TR.hover = null;
    } catch (err) { report(err, "Trim"); }
  },
};

// ============================================================================================ Set Start Point
const ST = { hover: null, drag: null, live: null, last: null, alt: false, feats: new WeakMap(), info: new WeakMap(), token: 0 };
const KIND = { node: "Corner", quadrant: "Quadrant", mid: "Middle", edge: "On the vector", start: "Start point" };
const clings = () => !!settings.snapVectors && !ST.alt;
const reachWorld = () => Math.max(6, settings.snapReach) / view().view.s;

function startContours() {
  const d = doc(), layers = layerMap(d), out = [];
  for (const vec of d.sheet.vectors) {
    const l = layers.get(vec.layer);
    if (l && !l.visible) continue;
    vec.contours.forEach((c, ci) => { if (c.closed && c.segs.length) out.push([vec, ci, c]); });
  }
  return out;
}
/** the box, polyline and area of a contour, made once (a contour is never changed, a changed one is a new object) */
function cinfo(c) {
  let i = ST.info.get(c);
  if (!i) {
    const flat = G.flatten(c);
    let a = 0;
    for (let k = 0; k < flat.length; k++) { const p = flat[k], q = flat[(k + 1) % flat.length]; a += p[0] * q[1] - q[0] * p[1]; }
    i = { box: G.contourBBox(c), flat, area: Math.abs(a) / 2 };
    ST.info.set(c, i);
  }
  return i;
}
/** what a pointer is on: the start point under it (to grab), else the contour whose line is nearest, else the smallest closed one it is inside of */
function startTarget(sx, sy, w) {
  const v = view(), cs = startContours();
  for (const [vec, ci, c] of cs) { const [qx, qy] = v.w2s(c.start[0], c.start[1]); if (Math.hypot(qx - sx, qy - sy) <= 9) return { vec, ci, c, point: c.start, kind: "start", marker: true }; }
  const bd = Math.max(12 / v.view.s, reachWorld());
  let best = null;
  for (const [vec, ci, c] of cs) {
    const b = cinfo(c).box;
    if (w[0] < b.x0 - bd || w[0] > b.x1 + bd || w[1] < b.y0 - bd || w[1] > b.y1 + bd) continue;
    const q = G.nearest(c, w[0], w[1]);
    if (q && q.d < bd && (!best || q.d < best.d)) best = { vec, ci, c, d: q.d, point: q.pt };
  }
  if (!best) {
    let area = Infinity;
    for (const [vec, ci, c] of cs) {
      const i = cinfo(c);
      if (w[0] < i.box.x0 || w[0] > i.box.x1 || w[1] < i.box.y0 || w[1] > i.box.y1 || i.area >= area || !G.inside(i.flat, w[0], w[1])) continue;
      area = i.area; best = { vec, ci, c, point: G.nearest(c, w[0], w[1]).pt };
    }
  }
  return best ? { ...best, kind: "edge", marker: false } : null;
}
/** ask the engine where the start would go for the pointer on a contour (clinging to corners, middles, arc extremes) */
const probe = new Latest(async job => {
  const first = !ST.feats.has(job.c);
  const r = await app.engine.call("start_probe", { contour: job.c, point: job.w, reach: reachWorld(), cling: job.cling, features: first });
  if (first && r.features) ST.feats.set(job.c, r.features);
  if (job.token === ST.token) job.apply(r);
  view().redraw();
});
function askProbe(c, w, apply) { probe.push({ c, w, cling: clings(), token: ++ST.token, apply }); }

async function startsOp(label, payload) {
  const d = doc();
  try {
    const r = await run("start_points", { project: d.project, sheet: d.sheetId, selection: d.selection, ...payload }, { label });
    if (r.changed && r.project) d.replace(label, r.project);
    if (r.message) A.status(r.message);
    return r;
  } catch (e) { report(e, label); return null; }
}
const setStart = (vec, ci, point) => startsOp("Set start point", { action: "set", items: [[vec.id, ci, point]] });

const startTool = {
  hover(p, e, v) {
    v.marker = null;
    const { sx, sy } = v._pt(e), w = v.s2w(sx, sy);
    ST.alt = e.altKey;
    if (ST.drag) return;
    const t = startTarget(sx, sy, w);
    ST.hover = t;
    if (!t) { A.status(hint("start")); v.redraw(); return; }
    if (t.marker) A.status(t.vec.locked_starts && t.vec.locked_starts.includes(t.ci) ? "Start point set by hand: drag it along the vector, ] and [ step it to the next / previous corner, right click gives it back to smart start points"
      : "Start point: click to keep it where it is, or drag it along the vector, ] and [ step it to the next / previous corner");
    else {
      A.status(`Click to start the cut at ${units.fmt(t.point[0])}, ${units.fmt(t.point[1])} (on the vector); drag to slide it along the vector, Alt: no clinging`);
      askProbe(t.c, w, r => { if (ST.hover && ST.hover.c === t.c) { ST.hover.point = r.point; ST.hover.kind = r.kind; A.status(`Click to start the cut at ${units.fmt(r.point[0])}, ${units.fmt(r.point[1])} (${(KIND[r.kind] || "").toLowerCase()}); drag to slide it along the vector, Alt: no clinging`); } });
    }
    v.redraw();
  },
  down(w, e, v) {
    if (e.button !== 0) return;
    const { sx, sy } = v._pt(e);
    ST.alt = e.altKey;
    const t = startTarget(sx, sy, w);
    if (!t) return;
    ST.drag = { vec: t.vec, ci: t.ci, c: t.c };
    ST.live = { point: t.point, kind: t.kind };
    ST.hover = null;
    v.drag = { kind: "draw" };
    v.redraw();
  },
  move(w, e, v) {
    const dg = ST.drag;
    ST.alt = e.altKey;
    if (!dg) return;
    askProbe(dg.c, w, r => { ST.live = { point: r.point, kind: r.kind }; A.status(`Start point at ${units.fmt(r.point[0])}, ${units.fmt(r.point[1])} (${(KIND[r.kind] || "").toLowerCase()}): let go to start the cut here, Esc cancels`); });
  },
  async up(w, e, v) {
    const dg = ST.drag;
    if (!dg) return;
    ST.drag = null;
    askProbe(dg.c, w, r => { ST.live = { point: r.point, kind: r.kind }; });
    await probe.idle();
    const q = ST.live && ST.live.point;
    ST.live = null;
    if (q && (dist(q, dg.c.start) > 1e-6 || !(dg.vec.locked_starts || []).includes(dg.ci))) { ST.last = [dg.vec.id, dg.ci]; await setStart(dg.vec, dg.ci, q); }
    v.redraw();
  },
};
/** the contour a key is about: the one under the pointer, else the one changed last */
function stepKeys() {
  if (ST.hover) return [[ST.hover.vec.id, ST.hover.ci]];
  if (ST.last && doc().sheet.vectors.some(v => v.id === ST.last[0])) return [ST.last];
  return [];
}
async function step(direction) {
  const keys = app.tool === "start" ? stepKeys() : [];
  const r = keys.length ? await startsOp("Start point at the " + (direction > 0 ? "next" : "previous") + " node", { action: "step", direction, contours: keys }) : null;
  if (r) { if (keys.length) ST.last = keys[0]; view().redraw(); return; }
  await startsOp("Start point at the " + (direction > 0 ? "next" : "previous") + " node", { action: "step", direction });
}
function startMenu(e, v) {
  const { sx, sy } = v._pt(e), w = v.s2w(sx, sy), t = startTarget(sx, sy, w);
  if (t && (t.vec.locked_starts || []).includes(t.ci)) startsOp("Release start point", { action: "release", contours: [[t.vec.id, t.ci]] });
}
function drawStarts(ctx, v) {
  ctx.save();
  const shown = ST.drag ? ST.drag : ST.hover;
  for (const [vec, ci, c] of startContours()) {
    const [x, y] = v.w2s(c.start[0], c.start[1]), locked = (vec.locked_starts || []).includes(ci), dragging = ST.drag && ST.drag.c === c;
    ctx.lineWidth = 1.2; ctx.strokeStyle = locked ? "#4a1486" : "#08519c"; ctx.fillStyle = dragging ? "rgba(0,0,0,0)" : locked ? "#9e9ac8" : "#6baed6";
    ctx.beginPath();
    if (locked) ctx.rect(x - 4, y - 4, 8, 8); else ctx.arc(x, y, 3.5, 0, 7);
    ctx.fill(); ctx.stroke();
  }
  const feats = shown && clings() && ST.feats.get(shown.c);
  if (feats && feats.length <= 400) {
    ctx.strokeStyle = "#4a90d9"; ctx.lineWidth = 1;
    for (const [fx, fy, kind] of feats) {
      const [x, y] = v.w2s(fx, fy);
      ctx.beginPath();
      if (kind === "node") ctx.rect(x - 2.5, y - 2.5, 5, 5);
      else if (kind === "quadrant") { ctx.moveTo(x, y - 3.5); ctx.lineTo(x + 3.5, y); ctx.lineTo(x, y + 3.5); ctx.lineTo(x - 3.5, y); ctx.closePath(); }
      else ctx.arc(x, y, 2, 0, 7);
      ctx.stroke();
    }
  }
  const ring = (pt, kind, fill) => {
    const [x, y] = v.w2s(pt[0], pt[1]), text = KIND[kind] || "";
    ctx.strokeStyle = "#d6249f"; ctx.lineWidth = 1.5; ctx.fillStyle = "#f1b6da"; ctx.beginPath(); ctx.arc(x, y, fill ? 5.5 : 6.5, 0, 7); if (fill) ctx.fill(); ctx.stroke();
    ctx.font = "10px system-ui, sans-serif";
    const wd = ctx.measureText(text).width + 8;
    ctx.fillStyle = "rgba(255,255,255,0.88)"; ctx.fillRect(x + 10, y + 8, wd, 14); ctx.strokeStyle = "#d6249f"; ctx.lineWidth = 1; ctx.strokeRect(x + 10, y + 8, wd, 14);
    ctx.fillStyle = "#1c2333"; ctx.textAlign = "left"; ctx.textBaseline = "middle"; ctx.fillText(text, x + 14, y + 15.5);
  };
  if (ST.drag && ST.live) ring(ST.live.point, ST.live.kind, true);
  else if (ST.hover && !ST.hover.marker) ring(ST.hover.point, ST.hover.kind, false);
  ctx.restore();
}

// ============================================================================================ Trace Bitmap
/** a picture as grey values (as the desktop does: pixels on white, Qt's grey weights), at most `max` pixels on a side */
async function imageGray(bytes, max = 2400) {
  const bmp = await createImageBitmap(new Blob([bytes]));
  const k = Math.min(1, max / Math.max(bmp.width, bmp.height)), w = Math.max(2, Math.round(bmp.width * k)), hh = Math.max(2, Math.round(bmp.height * k));
  const cv = h("canvas", { width: w, height: hh }), ctx = cv.getContext("2d", { willReadFrequently: true });
  ctx.fillStyle = "#fff"; ctx.fillRect(0, 0, w, hh); ctx.drawImage(bmp, 0, 0, w, hh);
  const px = ctx.getImageData(0, 0, w, hh).data, g = new Uint8Array(w * hh);
  for (let i = 0; i < g.length; i++) g[i] = (px[4 * i] * 11 + px[4 * i + 1] * 16 + px[4 * i + 2] * 5) >> 5;
  return { gray: g, width: w, height: hh };
}
export async function traceBitmap(file = null) {
  const d = doc();
  const f = file || await openFile({ title: "Trace bitmap", accept: "image/*,.png,.jpg,.jpeg,.bmp,.gif,.tif,.tiff,.webp", exts: ["png", "jpg", "jpeg", "bmp", "gif", "tif", "tiff", "webp"] });
  if (!f) return;
  let img;
  try { img = await imageGray(f.bytes); } catch { A.status("This picture cannot be read"); await alertBox(`${f.name} cannot be read as a picture.`, "Trace bitmap"); return; }
  const options = await traceDialog(f.name, img.width, img.height, d.sheet.width);
  if (!options) return;
  try {
    const r = await run("trace_bitmap", { project: d.project, sheet: d.sheetId, layer: d.layerId, gray_b64: toB64(img.gray), width: img.width, height: img.height,
      name: f.name.replace(/\.[^.]+$/, ""), options }, { label: "Tracing " + f.name });
    d.replace("Trace bitmap", r.project, r.selection);
    A.status(`Traced ${r.contours} contour(s)`);
  } catch (e) { if (e && e.code === "nothing_found") { A.status(e.message); await alertBox(e.message, "Trace bitmap"); } else report(e, "Trace bitmap"); }
}

// ============================================================================================ sheet outline
async function sheetOutline(clear) {
  const d = doc();
  try {
    const r = await run("sheet_outline", { project: d.project, sheet: d.sheetId, selection: d.selection, clear }, { label: "Sheet outline" });
    if (r.project) { d.replace(clear ? "Clear sheet outline" : "Sheet outline", r.project, []); view().zoomExtents(); }
    A.status(r.message);
  } catch (e) { if (e && e.code === "bad_selection") { A.status(e.message); await alertBox(e.message, "Sheet outline"); } else report(e, "Sheet outline"); }
}
/** the plate drawn over the sheet: what is not material is shaded and the outline is traced */
function drawOutline(ctx, v, phase) {
  const d = doc();
  if (phase !== "world" || !d || !d.sheet.outline) return;
  const sh = d.sheet, s = v.view.s;
  ctx.save();
  ctx.beginPath(); ctx.rect(0, 0, sh.width, sh.height);
  for (const poly of sh.outline) poly.forEach(([x, y], i) => (i ? ctx.lineTo(x, y) : ctx.moveTo(x, y)));
  const dark = document.documentElement.dataset.resolved === "dark";
  ctx.fillStyle = dark ? "rgba(0,0,0,0.38)" : "rgba(70,80,100,0.2)";
  ctx.fill("evenodd");
  ctx.beginPath();
  for (const poly of sh.outline) { poly.forEach(([x, y], i) => (i ? ctx.lineTo(x, y) : ctx.moveTo(x, y))); ctx.closePath(); }
  ctx.strokeStyle = "#c2410c"; ctx.lineWidth = 1.8 / s; ctx.stroke();
  ctx.restore();
}

// ============================================================================================ install
function typing(t) { return t && (t.tagName === "INPUT" || t.tagName === "TEXTAREA" || t.tagName === "SELECT" || t.isContentEditable); }

export function install() {
  const reg = A.register;
  for (const [id, t] of [["draw.edit_nodes", "nodes"], ["draw.star", "star"], ["draw.text", "text"], ["draw.trim_vectors", "trim"], ["draw.set_start_point", "start"],
    ["vectors.start_points.set_start_point", "start"]]) reg(id, toolItem(t));
  app.extraTools.nodes = nodeTool; app.extraTools.star = starTool; app.extraTools.text = textTool; app.extraTools.trim = trimTool; app.extraTools.start = startTool;
  A.TOOLS.nodes = "Edit Nodes"; A.TOOLS.star = "Star"; A.TOOLS.text = "Text"; A.TOOLS.trim = "Trim Vectors"; A.TOOLS.start = "Set Start Point";

  // Vectors > Start Points
  const at = (id, place, label) => reg(id, { run: () => startsOp(label, { action: "place", place }), enabled: hasDoc });
  const SP = "vectors.start_points.";
  at(SP + "bottom_left_corner", "sw", "Start point at the bottom left corner"); at(SP + "bottom_right_corner", "se", "Start point at the bottom right corner");
  at(SP + "top_left_corner", "nw", "Start point at the top left corner"); at(SP + "top_right_corner", "ne", "Start point at the top right corner");
  at(SP + "middle_of_a_side.top_side", "n", "Start point at the top"); at(SP + "middle_of_a_side.bottom_side", "s", "Start point at the bottom");
  at(SP + "middle_of_a_side.left_side", "w", "Start point at the left"); at(SP + "middle_of_a_side.right_side", "e", "Start point at the right");
  reg(SP + "next_corner", { run: () => step(1), enabled: hasDoc }); reg(SP + "previous_corner", { run: () => step(-1), enabled: hasDoc });
  reg(SP + "opposite_point", { run: () => startsOp("Start point on the opposite side", { action: "opposite" }), enabled: hasDoc });
  reg(SP + "smart_best_place", { run: () => startsOp("Smart start point", { action: "smart" }), enabled: hasDoc });
  reg(SP + "release_to_smart_start_points", { run: () => startsOp("Release start points", { action: "release" }), enabled: hasDoc });
  reg(SP + "smart_start_points", { run: () => app.ui.docks.show("cutorder"), enabled: hasDoc });

  // Vectors
  reg("vectors.vector_library", () => app.ui.docks.show("veclib"));
  reg("vectors.keep_selection_in_the_library", { run: () => { app.ui.docks.show("veclib"); return libraryApi.keep(); }, enabled: hasSel });
  reg("vectors.trace_bitmap", { run: () => traceBitmap(), enabled: hasDoc });
  reg("vectors.text_on_curve", { run: textOnCurve, enabled: hasDoc });
  // Nesting
  reg("nesting.set_sheet_outline_from_selected_vectors", { run: () => sheetOutline(false), enabled: hasSel });
  reg("nesting.clear_sheet_outline", { run: () => sheetOutline(true), enabled: () => !!doc() && !!doc().sheet.outline });

  // what is drawn over the drawing
  app.overlays.push((ctx, v, phase) => {
    if (!doc()) return;
    drawOutline(ctx, v, phase);
    if (phase === "world" && app.tool === "trim" && TR.hover) {
      const vec = doc().sheet.vectors.find(x => x.id === TR.hover);
      if (vec) { ctx.save(); ctx.strokeStyle = "#ec4899"; ctx.lineWidth = 3 / v.view.s; ctx.stroke(G.pathOf(vec)); ctx.restore(); }
    }
    if (phase === "screen" && app.tool === "nodes") drawNodes(ctx, v);
    if (phase === "screen" && app.tool === "start") drawStarts(ctx, v);
  });

  // the pointer: a double click inserts a node, a right click offers what can be done there
  const cv = view().canvas;
  cv.addEventListener("dblclick", e => {
    if (app.tool !== "nodes" || !doc()) return;
    const { sx, sy } = view()._pt(e), hit = pickNode(sx, sy);
    if (hit && hit.kind === "span") editVectors("Insert node", { vectors: vectorsOf(new Set([hit.vid])), action: "insert", vector: hit.vid, contour: hit.ci, seg: hit.si, t: hit.t });
  });
  cv.addEventListener("contextmenu", e => {
    if (!doc()) return;
    const v = view();
    if (app.tool === "nodes") { const { sx, sy } = v._pt(e), hit = pickNode(sx, sy); if (hit) nodeMenu(e, hit); }
    else if (app.tool === "start") startMenu(e, v);
  });
  // keys of the tools: Del deletes the selected nodes (not the vectors), + / - the points of the star
  document.addEventListener("keydown", e => {
    if (typing(e.target) || document.querySelector(".dlg-root:not(.modeless)") || !doc()) return;
    if (app.tool === "nodes" && (e.key === "Delete" || e.key === "Backspace") && NT.sel.size) { e.preventDefault(); e.stopImmediatePropagation(); deleteNodes(); return; }
    if (!e.ctrlKey && !e.metaKey && !e.altKey && pointsKey(e)) { e.preventDefault(); e.stopImmediatePropagation(); }
  });
  // the tool is left: its marks go
  on("tool", () => { if (app.tool !== "nodes") { NT.sel.clear(); NT.span = null; NT.drag = null; } if (app.tool !== "start") { ST.hover = null; ST.drag = null; ST.live = null; } if (app.tool !== "trim") TR.hover = null; });
  on("selection", () => { const d = doc(), ids = new Set(d ? d.selection : []); for (const k of [...NT.sel]) if (!ids.has(nparse(k)[0])) NT.sel.delete(k); });
  on("docs", () => { NT.sel.clear(); NT.span = null; ST.hover = null; ST.last = null; });
}

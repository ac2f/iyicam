/* iyiCAM web: the Cut Order and Simulation panels (of the Toolpaths column).
 *
 * Cut Order edits what the desktop's panel edits: the order of the cuts (strategy, by hand), their cutting directions and where each
 * one starts.  The work is the engine's (`cut_order` shows a toolpath's cuts or previews a change, `cut_edit` changes the project, see
 * iyicam/service/ops_job.py and iyicam/cam/cutedit.py); the panel keeps what the person chose and has not applied yet, and draws the
 * preview (pink) and the cut that is looked at (yellow) over the drawing.
 *
 * Simulation plays the calculated toolpaths move by move (`sim_timeline`, iyicam/cam/sim2d.py): the tool runs over the sheet and paints
 * where it has cut. */
import G from "./geom.js";
import { h, fill, toast, fmtTime } from "./dom.js";
import { app, settings, setSetting, units, emit } from "./state.js";
import { run, report } from "./engine.js";
import { dialog } from "./dialog.js";
import * as A from "./actions.js";

const fmtNum = (v, d = 3) => String(+Number(v).toFixed(d));
const QUALITY = { hand: ["■ by hand", "#6a51a3"], good: ["● good", "#238b45"], warn: ["▲ check", "#d94801"], plain: ["○ as drawn", "#737373"] };
const ARROW = { cw: "↻ cw", ccw: "↺ ccw" };
const PLACES = [["nw", "↖", "Top left corner"], ["n", "↑", "Middle of the top side"], ["ne", "↗", "Top right corner"], ["w", "←", "Middle of the left side"], null,
  ["e", "→", "Middle of the right side"], ["sw", "↙", "Bottom left corner"], ["s", "↓", "Middle of the bottom side"], ["se", "↘", "Bottom right corner"]];

// ------------------------------------------------------------------------------------------ Cut Order
/** what the panel and the overlays share */
export const cutState = {
  tid: null,              // the toolpath shown
  pending: {},            // tid -> settings chosen and not applied yet
  preview: null,          // {tid, changes, smart, summary, cuts}: a calculated preview (pink on the drawing)
  listing: null,          // what `cut_order` answered
  options: null,
  focus: null,            // the key of the cut that is looked at
  numbering: null,        // {picked: [keys], mode, at}: cuts being numbered by clicking them
  live: false,            // choices are applied at once
};
const cut = cutState;
let seq = 0, sig = "", panel = null, rseq = 0;
const resultsId = d => (d.results ? (d.results._rid || (Object.defineProperty(d.results, "_rid", { value: ++rseq }), d.results._rid)) : 0);
const directionFor = s => (s.directions || {})[s.strategy] ?? s.direction;
const vecOf = (d, vid) => d.sheet.vectors.find(v => v.id === vid);
const cutsShown = () => (cut.preview && cut.preview.tid === cut.tid ? cut.preview.cuts : cut.listing ? cut.listing.cuts || [] : []);
const keyVector = key => key.slice(0, key.lastIndexOf(":"));

export function cutOrderPanel() {
  const el = h("div", { class: "panel cutorder" });
  panel = { el, update, render };

  function shown() {
    const l = cut.listing;
    return { ...(l ? l.settings : {}), ...(cut.pending[cut.tid] || {}), ...(cut.preview && cut.preview.tid === cut.tid ? cut.preview.changes : {}) };
  }
  async function load(force = false) {
    const d = app.cur;
    const vis = el.offsetParent !== null;
    if (!d || !vis) { if (!d) { cut.listing = null; sig = ""; } if (!vis) sig = ""; return false; }
    const now = [d.id, d.sheetId, resultsId(d), cut.tid].join("|");
    if (!force && now === sig) return false;
    sig = now;
    if (!d.results) { cut.listing = null; return true; }
    const my = ++seq;
    try {
      let r = await app.engine.call("cut_order", { project: d.project, sheet: d.sheetId, toolpath: cut.tid || "", options: !cut.options });
      if (my !== seq) return false;
      if (r.options) cut.options = r.options;
      let tid = cut.tid && r.toolpaths.some(t => t.id === cut.tid) ? cut.tid : (d.toolpathSelection.find(i => r.toolpaths.some(t => t.id === i)) || (r.toolpaths[0] && r.toolpaths[0].id) || null);
      if (tid && !r.cuts) {
        const again = await app.engine.call("cut_order", { project: d.project, sheet: d.sheetId, toolpath: tid });
        if (my !== seq) return false;
        r = { ...again, options: r.options };
      }
      cut.tid = tid; cut.listing = tid ? r : { toolpaths: r.toolpaths, cuts: [], settings: {} };
      sig = [d.id, d.sheetId, resultsId(d), cut.tid].join("|");
    } catch (e) { if (my === seq) { cut.listing = null; fill(el, h("p", { class: "dlg-warn" }, e.message || String(e))); } return false; }
    return true;
  }
  async function update() {
    const d = app.cur;
    const typing = el.contains(document.activeElement) && /^(INPUT|SELECT|TEXTAREA)$/.test(document.activeElement.tagName);
    if (el.offsetParent === null) { sig = ""; return; }
    if (await load()) render(true);
    else if (!typing) render();
    panel.refocus();
    void d;
  }

  // -- changes
  async function edit(action, extra, label, o = {}) {
    const d = app.cur, tid = cut.tid;
    if (!d || !tid) return false;
    try {
      const r = await run("cut_edit", { project: d.project, sheet: d.sheetId, toolpath: tid, action, ...extra }, { label, cancel: true });
      if (!r.ok) { A.status("Nothing changed"); return false; }
      d.replace(label, r.project);
      cut.preview = null;
      if (o.recalc !== false) await A.recalc();
      return true;
    } catch (e) { report(e, label); return false; }
  }
  function stage(changes) {
    const d = app.cur;
    if (!d || !cut.tid) return;
    const now = shown();
    changes = Object.fromEntries(Object.entries(changes).filter(([k, v]) => !(typeof v === "number" && typeof now[k] === "number" ? Math.abs(v - now[k]) < 1e-3 : JSON.stringify(now[k]) === JSON.stringify(v))));
    if (!Object.keys(changes).length) return;
    cut.preview = null; app.ui.canvas.redraw();
    if (cut.live) { edit("sequence", { changes }, "Cut order settings"); return; }
    cut.pending[cut.tid] = { ...(cut.pending[cut.tid] || {}), ...changes };
    render(true);
  }
  async function preview(extra = {}) {
    const d = app.cur, tid = cut.tid;
    if (!d || !tid) return;
    const changes = { ...(cut.pending[tid] || {}), ...(cut.preview ? cut.preview.changes : {}), ...(extra.changes || {}) };
    const smart = extra.smart !== undefined ? extra.smart : !!(cut.preview && cut.preview.smart);
    try {
      const r = await run("cut_order", { project: d.project, sheet: d.sheetId, toolpath: tid, changes, smart_starts: smart, polylines: true }, { label: "Calculating the preview", cancel: true });
      cut.preview = { tid, changes, smart, summary: r.preview, cuts: r.cuts };
      app.ui.canvas.redraw(); render(true);
    } catch (e) { report(e, "Preview"); }
  }
  async function apply() {
    const tid = cut.tid;
    if (!tid) return;
    const p = cut.preview && cut.preview.tid === tid ? cut.preview : null;
    const changes = { ...(cut.pending[tid] || {}), ...(p ? p.changes : {}) };
    delete cut.pending[tid];
    const smart = !!(p && p.smart);
    if (Object.keys(changes).length) { if (!(await edit("sequence", { changes }, "Cut order settings", { recalc: !smart }))) return; }
    if (smart) await edit("optimise", {}, "Optimise start points");
  }
  function cancel() { delete cut.pending[cut.tid]; cut.preview = null; app.ui.canvas.redraw(); render(true); }

  // -- the pieces
  const row = (label, ...c) => h("label", { class: "row" }, h("span", { class: "lab small" }, label), ...c);
  const check = (label, on, fn, title) => h("label", { class: "row check", title: title || "" }, h("input", { type: "checkbox", checked: !!on, onchange: e => fn(e.target.checked) }), h("span", null, label));
  const small = (label, fn, o = {}) => h("button", { type: "button", class: "btn small", title: o.title || "", disabled: o.disabled, onclick: fn }, label);
  const lenInput = (v, fn, special) => {
    const inp = h("input", { type: "text", class: "num", value: v ? fmtNum(units.toDisplay(v), units.decimals) : "", placeholder: special || "" });
    inp.addEventListener("change", () => { const x = parseFloat(String(inp.value).replace(",", ".")); fn(Number.isFinite(x) ? units.fromDisplay(Math.max(0, x)) : 0); });
    return h("span", { class: "unit" }, inp, h("span", { class: "suf" }, units.suffix.trim()));
  };

  function render(force) {
    const d = app.cur;
    void force;
    if (!d) { fill(el, h("p", { class: "muted" }, "No project open.")); return; }
    const L = cut.listing;
    if (!d.project.toolpaths.length) { fill(el, h("p", { class: "muted" }, "Make a toolpath first (Toolpaths ▸ New Toolpath).")); return; }
    if (!d.results) { fill(el, h("p", { class: "muted" }, "Calculate to see the order of the cuts (F5).")); return; }
    if (!L || !L.toolpaths.length) { fill(el, h("p", { class: "muted" }, "No toolpath here cuts vector by vector (profile, engrave ...): they have no cut order.")); return; }
    const S = shown(), O = cut.options || { strategies: [], directions: [], groupings: [], places: [] };
    const sel = h("select", { onchange: e => { cut.tid = e.target.value; cut.preview = null; cut.focus = null; sig = ""; update(); app.ui.canvas.redraw(); } },
      L.toolpaths.map(t => h("option", { value: t.id, selected: t.id === cut.tid }, t.name)));
    const strategy = h("select", { onchange: e => stage({ strategy: e.target.value }) },
      O.strategies.map(g => h("optgroup", { label: g.group }, g.strategies.map(s => h("option", { value: s.key, selected: s.key === S.strategy }, s.label)))));
    const grouping = h("select", { onchange: e => stage({ grouping: e.target.value }), title: "Göbek: the inner pieces of a part (holes, the hub of a ring, parts nested in holes). First option: all of them are cut first, then the outlines." },
      O.groupings.map(g => h("option", { value: g.key, selected: g.key === S.grouping }, g.label)));
    const direction = h("select", { title: "Cutting direction used with this strategy (every strategy keeps its own); holes mill like their outline.", onchange: e => stage({ directions: { ...(S.directions || {}), [S.strategy]: e.target.value } }) },
      O.directions.map(g => h("option", { value: g.key, selected: g.key === directionFor(S) }, g.label)));
    const chosen = S.strategy === "auto" ? h("div", { class: "muted" }, L.strategy && L.strategy !== "auto" ? "Auto chose: " + ((O.strategies.flatMap(g => g.strategies).find(s => s.key === L.strategy) || {}).label || L.strategy) : "Auto: chosen when the toolpath is calculated") : null;
    const waiting = cut.pending[cut.tid] && Object.keys(cut.pending[cut.tid]);
    const p = cut.preview && cut.preview.tid === cut.tid ? cut.preview : null;
    const bar = p || (waiting && waiting.length)
      ? h("fieldset", { class: "group cutbar" }, h("legend", null, p ? "Preview (pink on the drawing)" : "Settings waiting"),
        h("div", { class: "muted" }, p ? previewText(p.summary, p.smart) : `Chosen, not applied yet: ${waiting.join(", ")}.\nNothing is calculated until Apply.`),
        h("div", { class: "btnrow" }, small("Apply", apply, { title: "Calculate this toolpath with the settings above (the only thing that does)" }),
          small("Reset order", async () => { const ch = { ...(cut.pending[cut.tid] || {}), ...(p ? p.changes : {}) }; delete cut.pending[cut.tid]; await edit("sequence", { changes: ch, reset: true }, "Reset the cut order"); }, { title: "Apply the settings and order the cuts again from nothing: a hand-made or frozen order is forgotten" }),
          small("Cancel", cancel, { title: "Forget the changes (and the preview)" }))) : null;
    const cuts = cutsShown();
    const vmap = new Map(d.sheet.vectors.map(v => [v.id, v]));
    const body = cuts.map((c, i) => {
      const vid = keyVector(c.key), ci = +c.key.slice(c.key.lastIndexOf(":") + 1), v = vmap.get(vid);
      let nm = v ? v.name || "Vector" : "?";
      if (v && v.contours.length > 1) nm += ` (${ci + 1})`;
      const [mark, color] = c.closed ? QUALITY[c.quality] || QUALITY.plain : ["open: on the line", "#737373"];
      const dir = c.closed ? ARROW[c.direction] || "" : "→ line";
      return h("tr", { class: c.key === cut.focus ? "on" : "", draggable: !p, dataset: { key: c.key }, title: (c.lines || []).join("\n"),
        onclick: () => focusCut(c.key, true),
        ondragstart: e => { e.dataTransfer.setData("text/plain", c.key); e.dataTransfer.effectAllowed = "move"; },
        ondragover: e => { if (!p) e.preventDefault(); },
        ondrop: e => { e.preventDefault(); const k = e.dataTransfer.getData("text/plain"); if (k && k !== c.key) edit("manual_order", { keys: [k], index: i }, "Cut order"); } },
      h("td", { class: "c" }, String(i + 1)), h("td", null, nm), h("td", null, dir), h("td", { style: { color } }, c.closed ? `${mark}  ${c.place}` : mark));
    });
    const info = (() => {
      const marks = cuts.filter(c => c.closed).map(c => c.quality);
      const words = [["good", "good"], ["hand", "set by hand"], ["warn", "to check"], ["plain", "as drawn"]];
      let t = `${cuts.length} cut(s)`;
      if (marks.length) t += " · start points: " + words.filter(([k]) => marks.includes(k)).map(([k, w]) => `${marks.filter(x => x === k).length} ${w}`).join(", ");
      if (cuts.length > 1 && L.route != null) t += `, ${fmtNum(units.toDisplay(L.route), units.inch ? 1 : 0)}${units.suffix} between the cuts`;
      if (d.stale && !settings.autoRecalc) t += " (the toolpaths are not calculated: F5)";
      return [t, ...(L.warnings || [])].join("\n");
    })();
    const fc = cuts.find(c => c.key === cut.focus);
    const num = cut.numbering;
    const modeSel = h("select", { title: "How the cuts you click on the drawing are numbered", onchange: e => { cut.numMode = e.target.value; render(true); } },
      [["from", "Number from #"], ["end", "Number from the end"], ["swap", "Swap two"]].map(([k, t]) => h("option", { value: k, selected: (cut.numMode || "from") === k }, t)));
    const at = h("input", { type: "number", class: "num narrow", min: 1, max: Math.max(1, cuts.length), value: cut.numAt || 1, title: "Where the numbering starts: from # on (from the end: # places from the end, #1 is the last)",
      disabled: (cut.numMode || "from") === "swap", onchange: e => { cut.numAt = Math.max(1, +e.target.value || 1); } });
    const step = h("input", { type: "text", class: "num", value: fmtNum(units.toDisplay(cut.step || 5), units.decimals), title: "How far the arrows move the start point", onchange: e => { const x = parseFloat(e.target.value); if (x > 0) cut.step = units.fromDisplay(x); } });
    const closed = !!(fc && fc.closed) && !p;
    const startBox = h("fieldset", { class: "group" }, h("legend", null, "Start point of the selected cut"),
      h("div", { class: "muted startwhy" }, fc ? (fc.closed ? (fc.lines || []).join("\n") : "An open vector is cut on the line, from one end to the other: it has no start point to move.") : "Select a cut in the list."),
      h("div", { class: "row" }, small("◀", () => stepStart(-1), { disabled: !closed, title: "Move the start point backwards along the cut" }), h("span", { class: "unit" }, step, h("span", { class: "suf" }, units.suffix.trim())), small("▶", () => stepStart(1), { disabled: !closed, title: "Move the start point forwards along the cut" })),
      h("div", { class: "places" }, PLACES.map(pl => pl ? h("button", { type: "button", class: "btn small", disabled: !closed, title: "Start at the " + pl[2].toLowerCase(), onclick: () => edit("place", { key: cut.focus, place: pl[0] }, "Start point") }, pl[1]) : h("span", { class: "muted c" }, "start"))),
      h("div", { class: "btnrow" }, small("Pick…", () => pickStart(), { disabled: !closed, title: "Click on the vector where this cut should start" }),
        small("Smart", () => edit("smart_one", { key: cut.focus }, "Smart start point"), { disabled: !closed, title: "Let smart start points choose the start of this cut" }),
        small("Release", () => edit("release", { key: cut.focus }, "Release start point"), { disabled: !closed, title: "Give a start point set by hand back to smart start points" })),
      h("div", { class: "muted" }, "The place of the cut in the order does not change."));
    fill(el,
      row("Toolpath", sel),
      h("fieldset", { class: "group" }, h("legend", null, "Order"),
        check("Smart order (re-order automatically)", S.auto_order, v => stage({ auto_order: v }), "On: the cuts are put in the order of the strategy again whenever the toolpath is calculated. Off: the order is frozen; new vectors are added at the end."),
        row("Strategy", strategy), chosen,
        check("Big parts last (parts beside and between them first)", S.big_last, v => stage({ big_last: v }), "A big part is cut after the smaller parts next to it and between its arms."),
        row("Inner pieces", grouping), row("Row size", lenInput(S.band, v => stage({ band: v }), "Automatic")),
        row("Direction", direction, small("All…", () => allDirections(S), { title: "Set the direction of every strategy at once" })),
        h("div", { class: "btnrow" }, small("Preview", () => preview({ changes: { order: [] } }), { title: "Show the order the strategy would give now, and what it saves" }),
          small("Re-order now", async () => { const ch = cut.pending[cut.tid] || {}; delete cut.pending[cut.tid]; await edit("sequence", { changes: ch, reorder: true }, "Re-order the cuts"); }, { title: "Order the cuts with the strategy once (a frozen order stays frozen)" }))),
      h("fieldset", { class: "group" }, h("legend", null, "Start points"),
        check("Smart start points (automatic)", S.smart_starts, v => stage({ smart_starts: v }), "Every calculation puts each start point at the best place of its vector (a node is made there); start points set by hand are kept"),
        row("Clear of cuts", lenInput(S.start_margin, v => stage({ start_margin: v }))), row("Off corners", lenInput(S.corner_distance, v => stage({ corner_distance: v }), "Automatic")),
        h("div", { class: "btnrow" }, small("Preview", () => preview({ smart: true }), { title: "Show where smart start points would put the start points (nothing moves yet)" }),
          small("Optimise now", async () => { const ch = cut.pending[cut.tid] || {}; delete cut.pending[cut.tid]; if (Object.keys(ch).length) await edit("sequence", { changes: ch }, "Cut order settings", { recalc: false }); await edit("optimise", {}, "Optimise start points"); }, { title: "Move the start points once (undoable), even with the automatic mode off" }),
          small("Release", () => edit("release", { vectors: d.selection.length ? d.selection : undefined }, "Release start points"), { title: "Start points of the selected vectors (or of the whole toolpath) may be moved by smart start points again" }),
          small("Draw (B)", () => (app.extraTools.start ? A.setTool("start") : A.status("The Set Start Point tool is not on this page")), { title: "Click on a vector, or drag a start point along it, on the drawing" }))),
      bar,
      h("fieldset", { class: "group" }, h("legend", null, "Show on the drawing"),
        h("div", { class: "viewopts" }, [["showCutOrder", "Numbers"], ["showRoute", "Route"], ["showDirections", "Direction arrows"], ["showStarts", "Start points"]].map(([k, t]) => check(t, settings[k], v => setSetting(k, v)))),
        check("Calculate while I change settings", cut.live, v => { cut.live = v; }, "Off (the default): choosing a setting calculates nothing; Preview shows the result in pink and Apply makes it so. On: every choice is applied at once."),
        h("div", { class: "muted legend" }, "numbers: first (blue) → last (red) · pink: the preview · yellow: the cut selected in the list")),
      h("fieldset", { class: "group" }, h("legend", null, "Cuts in the order they are made"),
        h("div", { class: "listbox cuts" }, h("table", { class: "grid cuttable" }, h("thead", null, h("tr", null, [["#", "c"], ["Vector", ""], ["Direction", ""], ["Start point", ""]].map(([t, c]) => h("th", { class: c }, t)))), h("tbody", null, body))),
        h("div", { class: "btnrow" }, small("Up", () => moveCut(-1), { disabled: !!p || !cut.focus }), small("Down", () => moveCut(1), { disabled: !!p || !cut.focus }),
          modeSel, h("span", { class: "lab small" }, "#"), at,
          num ? small(`Done (${num.picked.length})`, finishNumbering, { title: "Apply the numbering" }) : small("Click to number…", startNumbering, { disabled: !!p, title: "Click the vectors on the drawing in the order they should be cut" }),
          num ? small("Cancel", stopNumbering) : null),
        info.split("\n").map(t => h("div", { class: "muted" }, t))),
      startBox);
  }
  const previewText = (s, smart) => {
    if (!s) return "";
    const parts = [];
    parts.push(`${s.reordered} cut(s) at another place of the order`);
    if (s.route_before > 1e-6) parts.push(`travel between the cuts ${fmtNum(units.toDisplay(s.route_before), 0)} → ${fmtNum(units.toDisplay(s.route_after), 0)}${units.suffix} (${((s.route_after - s.route_before) / s.route_before * 100).toFixed(0)} %)`);
    if (s.directions) parts.push(`${s.directions} cut(s) cut the other way round`);
    if (smart) parts.push(`${(s.starts || []).length} start point(s) would move`);
    return parts.join("; ");
  };
  function focusCut(key, selectIt) {
    cut.focus = key;
    if (selectIt && key) A.select([keyVector(key)]);
    app.ui.canvas.redraw(); render(true);
  }
  function stepStart(sign) {
    const c = cutsShown().find(x => x.key === cut.focus);
    if (!c) return;
    const d = app.cur, v = vecOf(d, keyVector(c.key));
    const delta = (cut.step || 5) * sign * (v && c.direction && (c.direction === "ccw") === isCcw(v.contours[+c.key.slice(c.key.lastIndexOf(":") + 1)]) ? 1 : -1);
    edit("move_start", { key: c.key, delta }, "Move the start point");
  }
  function isCcw(c) {                                      // (the sign of the area: the tool walks the contour as it is drawn when they agree)
    const pts = G.flatten(c); let a = 0;
    for (let i = 0; i < pts.length; i++) { const p = pts[i], q = pts[(i + 1) % pts.length]; a += p[0] * q[1] - q[0] * p[1]; }
    return a > 0;
  }
  function moveCut(delta) {
    const keys = cutsShown().map(c => c.key), i = keys.indexOf(cut.focus);
    if (i < 0 || i + delta < 0 || i + delta >= keys.length) return;
    edit("manual_order", { keys: [cut.focus], index: i + delta }, "Cut order");
  }
  function allDirections(S) {
    const O = cut.options;
    const sels = {};
    const rows = O.strategies.flatMap(g => [h("tr", null, h("th", { colSpan: 2 }, g.group)), ...g.strategies.map(s => {
      const sl = h("select", null, O.directions.map(dd => h("option", { value: dd.key, selected: dd.key === ((S.directions || {})[s.key] ?? S.direction) }, dd.label)));
      sels[s.key] = sl; return h("tr", null, h("td", null, s.label), h("td", null, sl));
    })]);
    dialog({ title: "Cutting direction per strategy", width: 480, body: h("div", null, h("p", { class: "muted" }, "Each strategy cuts in its own direction. Climb milling runs clockwise around the outside of a part; holes always mill like their outline."),
      h("div", { class: "scrollform" }, h("table", { class: "kv" }, rows))), buttons: [{ label: "Cancel", value: null }, { label: "OK", primary: true, value: "ok", onclick: () => stage({ directions: Object.fromEntries(Object.entries(sels).map(([k, s]) => [k, s.value])) }) }] });
  }

  // -- clicking cuts on the drawing
  function startNumbering() {
    cut.numbering = { picked: [] };
    A.setTool("cutnumber");
    A.status("Click the vectors on the drawing in the order they should be cut (Enter: apply, Esc: cancel). Numbers show as you click.");
    render(true);
  }
  function stopNumbering() { cut.numbering = null; if (app.tool === "cutnumber") A.setTool("select"); app.ui.canvas.redraw(); render(true); }
  async function finishNumbering() {
    const n = cut.numbering;
    if (!n || !n.picked.length) { stopNumbering(); return; }
    const mode = cut.numMode || "from";
    cut.numbering = null; if (app.tool === "cutnumber") A.setTool("select");
    await edit("manual_order", { keys: n.picked, index: (cut.numAt || 1) - 1, mode }, "Cut order");
  }
  function pickStart() {
    if (!cut.focus) return;
    cut.pickKey = cut.focus; A.setTool("cutstart");
    A.status("Click on the vector where this cut should start.");
  }
  panel.finishNumbering = finishNumbering; panel.stopNumbering = stopNumbering; panel.edit = edit;
  panel.refocus = () => {
    const d = app.cur;
    if (!d || cut.numbering) return;
    if (d.selection.length === 1) {
      const c = cutsShown().find(x => keyVector(x.key) === d.selection[0]);
      if (c && c.key !== cut.focus) { cut.focus = c.key; render(true); }
    } else if (cut.focus) { cut.focus = null; render(true); }
  };
  render();
  return { el, update, show: () => update() };
}

/** the drawing tools and the overlays of the Cut Order panel (called once by feat_job.js) */
export function installCutOrder() {
  // click the vectors in the order they are cut
  app.extraTools.cutnumber = {
    down(w, e, v) {
      const d = app.cur, n = cut.numbering;
      if (!d || !n) return;
      const hit = G.hit(d.sheet.vectors, G.flatOf, w[0], w[1], 8 / v.view.s);
      if (!hit) return;
      const mine = cutsShown().filter(c => keyVector(c.key) === hit);
      if (!mine.length) { A.status("This vector has no cut in the toolpath."); return; }
      const vec = vecOf(d, hit), flat = G.flatOf(vec);
      let best = mine[0], bd = Infinity;
      for (const c of mine) { const dd = G.distance([flat[+c.key.slice(c.key.lastIndexOf(":") + 1)]], w[0], w[1]); if (dd < bd) { bd = dd; best = c; } }
      if (!n.picked.includes(best.key)) n.picked.push(best.key); else n.picked = n.picked.filter(k => k !== best.key);
      v.redraw(); panel && panel.render(true);
    },
    cursor: "crosshair",
  };
  // click where a cut should start
  app.extraTools.cutstart = {
    async down(w, e, v) {
      const d = app.cur, key = cut.pickKey;
      if (!d || !key) { A.setTool("select"); return; }
      const vec = vecOf(d, keyVector(key));
      const p = v.snapped ? v.snapped(w) : w;
      A.setTool("select");
      if (vec) await panel.edit("start_at", { key, point: p }, "Set start point");
    },
    cursor: "crosshair",
  };
  document.addEventListener("keydown", e => {
    if (app.tool !== "cutnumber" || !cut.numbering || document.querySelector(".dlg-root:not(.modeless)")) return;
    if (e.key === "Enter") { e.preventDefault(); e.stopPropagation(); panel.finishNumbering(); }
    else if (e.key === "Escape") { cut.numbering = null; panel.render(true); }
  }, true);
  app.overlays.push((ctx, view, phase) => {
    const d = app.cur;
    if (!d || el0().offsetParent === null && !cut.preview && !cut.numbering) return;
    const s = view.view.s;
    const pv = cut.preview && cut.preview.tid === cut.tid ? cut.preview : null;
    if (phase === "world") {
      ctx.save(); ctx.lineJoin = "round"; ctx.lineCap = "round";
      if (pv) {                                                                    // the preview: its cuts in pink, the route between them dashed
        ctx.strokeStyle = "rgba(224,64,154,0.9)"; ctx.lineWidth = 2 / s;
        for (const c of pv.cuts) if (c.poly) { ctx.beginPath(); for (let i = 0; i + 1 < c.poly.length; i += 2) (i ? ctx.lineTo(c.poly[i], c.poly[i + 1]) : ctx.moveTo(c.poly[i], c.poly[i + 1])); if (c.closed) ctx.closePath(); ctx.stroke(); }
        ctx.setLineDash([5 / s, 4 / s]); ctx.lineWidth = 1.2 / s; ctx.beginPath();
        let pos = d.project.material.home || [0, 0];
        for (const c of pv.cuts) { ctx.moveTo(pos[0], pos[1]); ctx.lineTo(c.entry[0], c.entry[1]); pos = c.end; }
        ctx.stroke(); ctx.setLineDash([]);
      }
      const fc = cut.focus && cutsShown().find(c => c.key === cut.focus);
      const vec = fc && vecOf(d, keyVector(fc.key));
      if (fc && vec && (el0().offsetParent !== null)) {                              // the cut that is looked at: a yellow line
        const f = G.flatOf(vec)[+fc.key.slice(fc.key.lastIndexOf(":") + 1)];
        if (f) { ctx.strokeStyle = "#f5c518"; ctx.lineWidth = 3.5 / s; ctx.beginPath(); f.pts.forEach((p, i) => (i ? ctx.lineTo(p[0], p[1]) : ctx.moveTo(p[0], p[1]))); if (f.closed) ctx.closePath(); ctx.stroke(); }
      }
      ctx.restore();
    } else {
      ctx.save(); ctx.font = "bold 10px system-ui, sans-serif"; ctx.textAlign = "center"; ctx.textBaseline = "middle";
      const dot = (p, text, color) => { const [x, y] = view.w2s(p[0], p[1]); ctx.fillStyle = color; ctx.beginPath(); ctx.arc(x, y, 8, 0, 7); ctx.fill(); ctx.fillStyle = "#fff"; ctx.fillText(String(text), x, y + 0.5); };
      if (pv) pv.cuts.forEach((c, i) => dot(c.start, i + 1, "#d6338a"));
      const n = cut.numbering;
      if (n) {
        const at = (cut.numAt || 1), all = cutsShown();
        n.picked.forEach((k, i) => { const c = all.find(x => x.key === k); if (c) dot(c.start, (cut.numMode || "from") === "swap" ? i + 1 : (cut.numMode === "end" ? "-" + (at + i) : at + i), "#6a51a3"); });
      }
      ctx.restore();
    }
  });
  const el0 = () => (panel ? panel.el : document.body);
}

// ------------------------------------------------------------------------------------------ Simulation (played move by move)
const SPEEDS = [1, 2, 5, 10, 25, 50, 100, 250, 1000];
const CUT_RGB = [176, 120, 64], THROUGH = "rgb(52,56,64)";
/** the simulation of a document: `{tl, canvas, t, done, playing, which}` (kept on the document, so every tab has its own) */
export const simState = { raf: 0, last: 0 };

class SimView {
  constructor(tl, sheet) {
    this.tl = tl; this.w = sheet.width; this.h = sheet.height;
    this.ppm = Math.max(0.25, Math.min(4, 4000 / Math.max(this.w, this.h, 1)));
    this.canvas = document.createElement("canvas");
    this.canvas.width = Math.max(1, Math.floor(this.w * this.ppm)); this.canvas.height = Math.max(1, Math.floor(this.h * this.ppm));
    this.ctx = this.canvas.getContext("2d");
    this.t = 0; this.done = 0; this.playing = false; this.speed = 10;
    this.starts = tl.t;                                           // (a move i runs from t[i] to t[i + 1])
    this.reset();
  }
  reset() { this.ctx.clearRect(0, 0, this.canvas.width, this.canvas.height); this.done = 0; this.t = 0; }
  _upto(t) { let lo = 0, hi = this.tl.k.length; while (lo < hi) { const m = (lo + hi) >> 1; if (this.starts[m] <= t) lo = m + 1; else hi = m; } return lo; }   // (moves that have started by t)
  _style(i) {
    const c = this.tl.c[i];
    if (c < 0) return null;
    const color = c >= 2 ? THROUGH : `rgb(${CUT_RGB.map(v => Math.round(v * 100 / (100 + 60 * c))).join(",")})`;
    return [color, Math.max(this.tl.w[i], 1 / this.ppm)];
  }
  advance(t) {
    const tl = this.tl;
    t = Math.max(0, Math.min(t, tl.total));
    if (t < this.t) this.reset();
    const upto = this._upto(t), ctx = this.ctx;
    ctx.save(); ctx.setTransform(this.ppm, 0, 0, -this.ppm, 0, this.h * this.ppm); ctx.lineCap = "round"; ctx.lineJoin = "round";
    let last = null;
    for (let i = this.done; i < upto; i++) {
      const st = this._style(i);
      if (tl.t[i + 1] > t) {                                      // (the move the tool is on: up to where it is)
        if (st) {
          const u = tl.t[i + 1] > tl.t[i] ? (t - tl.t[i]) / (tl.t[i + 1] - tl.t[i]) : 1;
          ctx.strokeStyle = st[0]; ctx.lineWidth = st[1]; ctx.beginPath(); ctx.moveTo(tl.x[i], tl.y[i]);
          ctx.lineTo(tl.x[i] + (tl.x[i + 1] - tl.x[i]) * u, tl.y[i] + (tl.y[i + 1] - tl.y[i]) * u); ctx.stroke();
        }
        break;
      }
      if (st) {
        if (!last || last[0] !== st[0] || last[1] !== st[1]) { ctx.strokeStyle = st[0]; ctx.lineWidth = st[1]; last = st; }
        ctx.beginPath(); ctx.moveTo(tl.x[i], tl.y[i]); ctx.lineTo(tl.x[i + 1], tl.y[i + 1]); ctx.stroke();
      }
      this.done = i + 1;
    }
    ctx.restore();
    this.t = t;
  }
  /** `[x, y, z, tool]` of the tool at time t, or null before anything */
  position(t = this.t) {
    const tl = this.tl, n = this._upto(t) - 1;
    if (n < 0) return null;
    const u = tl.t[n + 1] > tl.t[n] ? Math.min(1, Math.max(0, (t - tl.t[n]) / (tl.t[n + 1] - tl.t[n]))) : 1;
    return [tl.x[n] + (tl.x[n + 1] - tl.x[n]) * u, tl.y[n] + (tl.y[n + 1] - tl.y[n]) * u, tl.z[n] + (tl.z[n + 1] - tl.z[n]) * u, tl.k[n]];
  }
}

export function simulationPanel() {
  const which = h("select", { title: "The selected ones: what is selected on the Toolpaths panel (all when none is)", onchange: () => { if (cur()) start(false); } },
    h("option", { value: "selected" }, "The selected ones"), h("option", { value: "all" }, "All on this sheet"));
  const speed = h("select", { title: "How much faster than the machine (1x: as long as the job takes)", onchange: () => { const v = cur(); if (v) v.speed = +speed.value; } }, SPEEDS.map(s => h("option", { value: s, selected: s === 10 }, s + "x")));
  const slider = h("input", { type: "range", min: 0, max: 1000, value: 0, oninput: () => { const v = cur(); if (v) { v.advance(v.tl.total * slider.value / 1000); show(); } } });
  const info = h("div", { class: "muted siminfo" }, "Press Play to see the calculated toolpaths cut, move by move.");
  const play = h("button", { type: "button", class: "btn", onclick: () => toggle() }, "▶ Play");
  const bRestart = h("button", { type: "button", class: "btn", title: "From the start", onclick: () => { const v = cur(); if (v) { v.advance(0); show(); } } }, "⏮");
  const bEnd = h("button", { type: "button", class: "btn", title: "Show everything cut at once", onclick: () => { const v = cur(); if (v) { v.playing = false; v.advance(v.tl.total); show(); } } }, "⏭");
  const bStop = h("button", { type: "button", class: "btn", title: "End the simulation (the drawing as before)", onclick: () => stop() }, "✕");
  const lines = h("input", { type: "checkbox", checked: settings.showToolpaths, onchange: () => setSetting("showToolpaths", lines.checked) });
  const shade = h("input", { type: "checkbox", checked: settings.showSim, onchange: () => setSetting("showSim", shade.checked) });
  const shadeInfo = h("div", { class: "muted" });
  const cur = () => (app.cur && app.cur.sim2d) || null;
  async function start(playNow = true) {
    const d = app.cur;
    if (!d) return false;
    if (!d.project.toolpaths.some(t => t.enabled)) { info.textContent = "Make a toolpath first: the simulation plays what it cuts."; return false; }
    try {
      const ids = which.value === "selected" ? d.toolpathSelection : [];
      const tl = await run("sim_timeline", { project: d.project, sheet: d.sheetId, toolpaths: ids, backplot: d.backplot && d.backplot.sheet === d.sheetId ? d.backplot.text : undefined }, { label: "Preparing the simulation", cancel: true });
      const v = new SimView(tl, d.sheet);
      v.speed = +speed.value; v.sheetId = d.sheetId;
      d.sim2d = v;
      v.playing = playNow; slider.value = 0; app.ui.canvas.redraw(); show(); if (playNow) loop();
      return true;
    } catch (e) {
      if (e && e.code === "no_toolpaths") { info.textContent = "There is no calculated toolpath to play on this sheet: calculate first (F5)."; return false; }
      report(e, "Simulation"); return false;
    }
  }
  function stop() { const d = app.cur; if (d) { d.sim2d = null; } app.ui.canvas.redraw(); show(); }
  async function toggle() {
    let v = cur();
    if (!v) { await start(true); return; }
    if (v.t >= v.tl.total - 1e-9) v.reset();
    v.playing = !v.playing; if (v.playing) loop(); show();
  }
  function loop() {
    cancelAnimationFrame(simState.raf);
    simState.last = performance.now();
    const tick = now => {
      const v = cur();
      if (!v || !v.playing) return;
      const dt = Math.min(0.1, (now - simState.last) / 1000); simState.last = now;
      let t = v.t + dt * v.speed;
      if (t >= v.tl.total) { t = v.tl.total; v.playing = false; }
      v.advance(t); show(); app.ui.canvas.redraw();
      if (v.playing) simState.raf = requestAnimationFrame(tick);
    };
    simState.raf = requestAnimationFrame(tick);
  }
  function show() {
    const v = cur();
    play.textContent = v && v.playing ? "⏸ Pause" : "▶ Play";
    for (const b of [bRestart, bEnd, bStop, slider]) b.disabled = !v;
    if (!v) { slider.value = 0; return; }
    const total = v.tl.total;
    slider.value = total > 0 ? Math.round(1000 * v.t / total) : 1000;
    const pos = v.position();
    info.textContent = `${fmtTime(v.t)} / ${fmtTime(total)}` + (pos ? `  ·  ${v.tl.tools[pos[3]] || ""}  ·  X ${units.fmt(pos[0])}  Y ${units.fmt(pos[1])}  Z ${units.fmt(pos[2])}` : "");
    app.ui.canvas.redraw();
  }
  const el = h("div", { class: "panel simulation" },
    row2("Toolpaths", which),
    h("div", { class: "btnrow" }, bRestart, play, bEnd, bStop),
    row2("Speed", speed), h("label", { class: "row check" }, lines, h("span", null, "Toolpath lines"), ),
    slider, info,
    h("fieldset", { class: "group" }, h("legend", null, "Shaded result (3D)"),
      h("div", { class: "btnrow" }, h("button", { type: "button", class: "btn", title: "The material as it is left by the whole job, shaded (takes a moment)", onclick: () => A.simulate() }, "Shaded result (3D)…"), h("button", { type: "button", class: "btn", onclick: () => A.clearSimulation() }, "✕ Clear")),
      h("label", { class: "row check" }, shade, h("span", null, "Show the result on the drawing")), shadeInfo));
  function update() {
    const d = app.cur, v = cur();
    if (v && d && v.sheetId !== d.sheetId) { /* (another sheet is open: the picture is of the first) */ }
    shade.checked = settings.showSim; lines.checked = settings.showToolpaths;
    fill(shadeInfo, d && d.sim ? `Simulated ${d.sim.size[0]} × ${d.sim.size[1]} px, ${d.sim.res.toFixed ? d.sim.res.toFixed(2) : d.sim.res} mm per pixel. Machining time ${fmtTime(d.sim.time_s)}.` : "Press the button to see what the calculated toolpaths cut, shaded.");
    show();
  }
  setTimeout(show, 0);
  return { el, update, start, stop };
}
function row2(label, ctl) { return h("label", { class: "row" }, h("span", { class: "lab small" }, label), ctl); }

/** the overlays of the simulation (called once by feat_job.js) */
export function installSimulation() {
  app.overlays.push((ctx, view, phase) => {
    const d = app.cur, v = d && d.sim2d;
    if (!v) return;
    if (phase === "world") {
      ctx.save(); ctx.translate(0, v.h); ctx.scale(1 / v.ppm, -1 / v.ppm); ctx.imageSmoothingEnabled = view.view.s < 3; ctx.drawImage(v.canvas, 0, 0); ctx.restore();
    } else {
      const pos = v.position();
      if (!pos) return;
      const [x, y] = view.w2s(pos[0], pos[1]), dia = (v.tl.diameters && v.tl.diameters[pos[3]]) || 6, r = Math.max(4, dia / 2 * view.view.s);
      ctx.save(); ctx.strokeStyle = "#e11d48"; ctx.lineWidth = 2; ctx.fillStyle = pos[2] < 0 ? "rgba(225,29,72,0.28)" : "rgba(225,29,72,0.1)";
      ctx.beginPath(); ctx.arc(x, y, r, 0, 7); ctx.fill(); ctx.stroke();
      ctx.beginPath(); ctx.moveTo(x - r - 4, y); ctx.lineTo(x + r + 4, y); ctx.moveTo(x, y - r - 4); ctx.lineTo(x, y + r + 4); ctx.stroke(); ctx.restore();
    }
  });
}
void toast;

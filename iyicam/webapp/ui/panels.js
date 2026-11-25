/* iyiCAM web: the panels of the docks, as the desktop's: Layers, Sheets, Selection (properties), Transform, Toolpaths,
 * Cut Order, Simulation, Vector Library.  Each is `{el, update()}`; `update` runs when the project, the selection or the
 * results change. */
import G from "./geom.js";
import { h, fill, fmtTime, toast } from "./dom.js";
import { app, settings, setSetting, units, emit } from "./state.js";
import { icon } from "./icons.js";
import { calc } from "./forms.js";
import * as A from "./actions.js";

const tbtn = (ic, title, fn, o = {}) => h("button", { type: "button", class: "tb", title, disabled: o.disabled, onclick: fn }, icon(ic, 16));
const fmtNum = (v, d = 3) => String(+Number(v).toFixed(d));

// ------------------------------------------------------------------------------------------ Layers
export function layersPanel() {
  const body = h("tbody"), colorInput = h("input", { type: "color", class: "hidden-color" });
  let colorFor = null;
  colorInput.addEventListener("input", () => { if (colorFor) A.layerOps.set(colorFor, { color: colorInput.value }, "Layer colour"); });
  const table = h("table", { class: "grid" }, h("thead", null, h("tr", null, h("th", null, "Layer"), h("th", { class: "c" }, "Show"), h("th", { class: "c" }, "Lock"))), body);
  const cur = () => app.cur && app.cur.layerId;
  const el = h("div", { class: "panel layers" }, h("div", { class: "listbox" }, table), colorInput,
    h("div", { class: "btnrow" },
      tbtn("plus", "Add layer", () => A.layerOps.add()), tbtn("minus", "Delete the layer", () => A.layerOps.remove(cur())),
      tbtn("up", "Move the layer up", () => A.layerOps.move(cur(), -1)), tbtn("down", "Move the layer down", () => A.layerOps.move(cur(), 1)),
      tbtn("stop", "Colour of the layer", () => { const d = app.cur; if (!d) return; colorFor = d.layerId; colorInput.value = d.layer.color; colorInput.click(); }),
      tbtn("indent", "Move the selected vectors to this layer", () => A.layerOps.moveSelection(cur())),
      tbtn("dashed", "Select the vectors of this layer", () => A.layerOps.selectLayer(cur()))));
  function update() {
    const d = app.cur;
    if (!d) { fill(body); return; }
    const counts = new Map();
    for (const v of d.sheet.vectors) counts.set(v.layer, (counts.get(v.layer) || 0) + 1);
    fill(body, d.project.layers.map(l => {
      const name = h("span", { class: "lname" }, `${l.name} (${counts.get(l.id) || 0})`);
      const tr = h("tr", { class: l.id === d.layerId ? "on" : "", onclick: () => { if (d.layerId !== l.id) { d.layerId = l.id; emit("project", d); } } },
        h("td", { class: "namecell" }, h("span", { class: "sw", style: { background: l.color } }), name),
        h("td", { class: "c" }, h("input", { type: "checkbox", checked: l.visible, onclick: e => e.stopPropagation(), onchange: e => A.layerOps.set(l.id, { visible: e.target.checked }, "Show / hide layer") })),
        h("td", { class: "c" }, h("input", { type: "checkbox", checked: l.locked, onclick: e => e.stopPropagation(), onchange: e => A.layerOps.set(l.id, { locked: e.target.checked }, "Lock / unlock layer") })));
      name.addEventListener("dblclick", () => {
        const inp = h("input", { type: "text", value: l.name, class: "inline" });
        fill(name, inp); inp.focus(); inp.select();
        let fin = false;
        const done = ok => { if (fin || !inp.isConnected) return; fin = true; if (ok && inp.value.trim() && inp.value.trim() !== l.name) A.layerOps.set(l.id, { name: inp.value.trim() }, "Rename layer"); else update(); };
        inp.addEventListener("blur", () => done(true));
        inp.addEventListener("keydown", e => { if (e.key === "Enter") done(true); else if (e.key === "Escape") { done(false); } e.stopPropagation(); });
      });
      return tr;
    }));
  }
  return { el, update };
}

// ------------------------------------------------------------------------------------------ Sheets
export function sheetsPanel() {
  const list = h("div", { class: "tree" });
  const folded = new Set();
  const el = h("div", { class: "panel sheets" }, h("div", { class: "listbox" }, list),
    h("div", { class: "btnrow" },
      tbtn("plus", "Add sheet", () => A.sheetOps.add()), tbtn("copy", "Duplicate sheet", () => A.sheetOps.duplicate()), tbtn("minus", "Delete sheet", () => A.sheetOps.remove()),
      tbtn("up", "Move the sheet up", () => A.sheetOps.move(-1)), tbtn("down", "Move the sheet down", () => A.sheetOps.move(1)),
      tbtn("gear", "Sheet properties", () => A.sheetOps.props()), tbtn("stop", "Group of the sheet (they are stacked under it)", () => A.sheetOps.group())));
  function update() {
    const d = app.cur;
    if (!d) { fill(list); return; }
    const th = s => (s.thickness != null ? s.thickness : d.project.material.thickness);
    const rowOf = s => h("div", { class: "trow" + (s.id === d.sheetId ? " on" : ""), onclick: () => A.sheetOps.open(s.id), ondblclick: () => A.sheetOps.props() },
      `${s.name}  —  ${units.fmt(s.width)} x ${units.fmt(s.height)} x ${units.fmt(th(s))}${s.outline ? "  (shape)" : ""}  [${s.vectors.length}]`);
    const seen = new Set(), out = [];
    for (const s of d.project.sheets) {
      if (!s.group) { out.push(rowOf(s)); continue; }
      if (seen.has(s.group)) continue;
      seen.add(s.group);
      const mine = d.project.sheets.filter(x => x.group === s.group), open = !folded.has(s.group) || mine.some(x => x.id === d.sheetId);
      out.push(h("div", { class: "tgroup" },
        h("div", { class: "tg-head", onclick: () => { open ? folded.add(s.group) : folded.delete(s.group); update(); } }, h("span", { class: "tri" }, open ? "▾" : "▸"),
          h("b", null, `${s.group}  (${mine.length} sheet${mine.length === 1 ? "" : "s"}, ${mine.reduce((a, x) => a + x.vectors.length, 0)} vectors)`)),
        open ? h("div", { class: "kids" }, mine.map(rowOf)) : null));
    }
    fill(list, out);
  }
  return { el, update };
}

/** the Project dock: Layers and Sheets under tabs */
export function projectPanel() {
  const L = layersPanel(), S = sheetsPanel();
  const tabsEl = h("div", { class: "subtabs" });
  const pages = h("div", { class: "subpages" }, L.el, S.el);
  let which = 0;
  const names = ["Layers", "Sheets"];
  const show = i => { which = i; [L.el, S.el].forEach((e, k) => { e.hidden = k !== i; }); fill(tabsEl, names.map((n, k) => h("button", { type: "button", class: "subtab" + (k === i ? " on" : ""), onclick: () => show(k) }, n))); };
  show(0);
  return { el: h("div", { class: "panel project" }, tabsEl, pages), update() { L.update(); S.update(); } , show };
}

// ------------------------------------------------------------------------------------------ Selection
export function selectionPanel() {
  const info = h("div", { class: "selinfo" }, "Nothing selected");
  const inputs = {};
  const mk = (k, label) => { inputs[k] = h("input", { type: "text", class: "num", inputMode: "decimal", disabled: true, onchange: apply }); return h("label", { class: "row" }, h("span", { class: "lab" }, label), h("span", { class: "unit" }, inputs[k], h("span", { class: "suf" }, "mm"))); };
  const keep = h("input", { type: "checkbox", checked: true });
  const name = h("input", { type: "text", disabled: true, onchange: () => { const v = app.cur.selected()[0]; if (v && v.name !== name.value) app.cur.edit("Rename vector", p => { const sh = p.sheets.find(s => s.id === app.cur.sheetId); sh.vectors = sh.vectors.map(x => (x.id === v.id ? { ...x, name: name.value } : x)); }); } });
  const layer = h("select", { disabled: true, onchange: () => A.layerOps.moveSelection(layer.value) });
  let box = null;
  function apply() {
    const d = app.cur;
    if (!d || !box) return;
    const read = k => { const v = calc(inputs[k].value); return Number.isFinite(v) ? units.fromDisplay(v) : null; };
    const x = read("x"), y = read("y"), w = read("w"), hh = read("h");
    if ([x, y, w, hh].some(v => v === null)) { update(true); return; }
    const bw = box.x1 - box.x0, bh = box.y1 - box.y0;
    if ((bw > 1e-9 && w < 1e-3) || (bh > 1e-9 && hh < 1e-3)) { update(true); return; }
    let sx = bw > 1e-9 ? w / bw : 1, sy = bh > 1e-9 ? hh / bh : 1;
    if (keep.checked) { if (Math.abs(sx - 1) > 1e-9) sy = sx; else sx = sy; }
    if ([x - box.x0, y - box.y0, sx - 1, sy - 1].every(v => Math.abs(v) < 1e-9)) return;
    const m = [sx, 0, 0, sy, x - sx * box.x0, y - sy * box.y0];
    A.transformSelection(m, "Position / size", { engine: { command: "xform.scale", values: { sx: sx * 100, sy: sy * 100, uniform: Math.abs(sx - sy) < 1e-9, about: "point", px: box.x0, py: box.y0, copy: false } } });
  }
  function update(force) {
    const d = app.cur, sel = d ? d.selected() : [];
    box = sel.length ? G.bbox(sel) : null;
    for (const k of Object.keys(inputs)) inputs[k].disabled = !box;
    name.disabled = sel.length !== 1; layer.disabled = !sel.length;
    fill(layer, (d ? d.project.layers : []).map(l => h("option", { value: l.id }, l.name)));
    if (!box) { fill(info, "Nothing selected"); name.value = ""; for (const k of Object.keys(inputs)) inputs[k].value = ""; return; }
    for (const [k, v] of [["x", box.x0], ["y", box.y0], ["w", box.x1 - box.x0], ["h", box.y1 - box.y0]]) if (force || document.activeElement !== inputs[k]) inputs[k].value = fmtNum(units.toDisplay(v), units.decimals);
    const nc = sel.reduce((a, v) => a + v.contours.length, 0), closed = sel.reduce((a, v) => a + v.contours.filter(c => c.closed).length, 0);
    fill(info, h("b", null, String(sel.length)), ` vector(s), ${nc} contour(s), ${closed} closed`);
    name.value = sel.length === 1 ? sel[0].name || "" : "";
    const ls = new Set(sel.map(v => v.layer));
    layer.value = ls.size === 1 ? [...ls][0] : "";
  }
  const el = h("div", { class: "panel selection" }, info, h("div", { class: "fields" }, mk("x", "X (left)"), mk("y", "Y (bottom)"), mk("w", "Width"), mk("h", "Height"),
    h("label", { class: "row check" }, keep, h("span", null, "Keep proportions")), h("label", { class: "row" }, h("span", { class: "lab" }, "Name"), name), h("label", { class: "row" }, h("span", { class: "lab" }, "Layer"), layer)));
  return { el, update };
}

// ------------------------------------------------------------------------------------------ Transform
const REFS = [["tl", 0, 1], ["tc", .5, 1], ["tr", 1, 1], ["ml", 0, .5], ["mc", .5, .5], ["mr", 1, .5], ["bl", 0, 0], ["bc", .5, 0], ["br", 1, 0]];
export function transformPanel() {
  const modes = [["position", "Position"], ["rotate", "Rotate"], ["scale", "Scale and mirror"], ["size", "Size"], ["slant", "Slant"]];
  const sel = h("select", { onchange: () => { mode = sel.value; build(); } }, modes.map(([v, t]) => h("option", { value: v }, t)));
  const info = h("div", { class: "selinfo" }, "Nothing selected");
  const fields = h("div", { class: "fields" });
  let mode = "position", ref = "bl", box = null;
  const inp = {}, mkNum = (k, label, suffix, val) => { inp[k] = h("input", { type: "text", class: "num", inputMode: "decimal", value: val ?? "" }); return h("label", { class: "row" }, h("span", { class: "lab" }, label), h("span", { class: "unit" }, inp[k], h("span", { class: "suf" }, suffix))); };
  const rel = h("input", { type: "checkbox" }), copies = h("input", { type: "checkbox" }), n = h("input", { type: "number", min: 1, max: 999, value: 1, class: "num" }), keepP = h("input", { type: "checkbox", checked: true });
  const refGrid = h("div", { class: "refgrid" }, REFS.map(([k]) => h("button", { type: "button", class: "ref" + (k === ref ? " on" : ""), "data-k": k, onclick: () => { ref = k; refGrid.querySelectorAll(".ref").forEach(b => b.classList.toggle("on", b.dataset.k === ref)); loadValues(); } })));
  const apply = h("button", { type: "button", class: "btn", onclick: () => doApply() }, "Apply");
  const mirrorRow = h("div", { class: "btnrow" }, h("button", { class: "btn", type: "button", onclick: () => mirror("h") }, "Mirror left / right"), h("button", { class: "btn", type: "button", onclick: () => mirror("v") }, "Mirror top / bottom"));
  const refPoint = () => { const [, fx, fy] = REFS.find(r => r[0] === ref); return box ? [box.x0 + (box.x1 - box.x0) * fx, box.y0 + (box.y1 - box.y0) * fy] : [0, 0]; };
  const u = () => units.suffix.trim();
  function build() {
    const f = [];
    if (mode === "position") f.push(mkNum("x", "X", u()), mkNum("y", "Y", u()), h("label", { class: "row check" }, rel, h("span", null, "Relative: move by these amounts")));
    else if (mode === "rotate") f.push(mkNum("angle", "Angle", "°", "90"));
    else if (mode === "scale") f.push(mkNum("sx", "Scale X", "%", "100"), mkNum("sy", "Scale Y", "%", "100"), h("label", { class: "row check" }, keepP, h("span", null, "Keep proportions")), mirrorRow);
    else if (mode === "size") f.push(mkNum("w", "Width", u()), mkNum("h", "Height", u()), h("label", { class: "row check" }, keepP, h("span", null, "Keep proportions")));
    else f.push(mkNum("ax", "Slant X", "°", "15"), mkNum("ay", "Slant Y", "°", "0"));
    fill(fields, f, h("div", { class: "reffoot" }, h("div", null, h("div", { class: "lab small" }, "Reference point"), refGrid), h("div", { class: "copies" }, h("label", { class: "row check" }, copies, h("span", null, "Make copies")), h("label", { class: "row" }, h("span", { class: "lab" }, "Copies:"), n))), apply);
    sel.value = mode;
    loadValues();
  }
  function loadValues() {
    if (!box) return;
    const p = refPoint();
    if (mode === "position" && !rel.checked) { inp.x.value = fmtNum(units.toDisplay(p[0]), units.decimals); inp.y.value = fmtNum(units.toDisplay(p[1]), units.decimals); }
    if (mode === "position" && rel.checked) { inp.x.value = "0"; inp.y.value = "0"; }
    if (mode === "size") { inp.w.value = fmtNum(units.toDisplay(box.x1 - box.x0), units.decimals); inp.h.value = fmtNum(units.toDisplay(box.y1 - box.y0), units.decimals); }
  }
  rel.addEventListener("change", loadValues);
  const val = (k, fallback = 0) => { const v = calc(inp[k].value); return Number.isFinite(v) ? v : fallback; };
  async function mirror(axis) {
    const p = refPoint();
    await A.transformSelection(axis === "h" ? G.flipHMap(p[0]) : G.flipVMap(p[1]), "Mirror", { keep: copies.checked });
  }
  async function doApply() {
    if (!box) return;
    const p = refPoint(), cnt = copies.checked ? Math.max(1, parseInt(n.value, 10) || 1) : 0;
    if (mode === "position") {
      let dx, dy;
      if (rel.checked) { dx = units.fromDisplay(val("x")); dy = units.fromDisplay(val("y")); } else { dx = units.fromDisplay(val("x")) - p[0]; dy = units.fromDisplay(val("y")) - p[1]; }
      if (cnt && rel.checked) return A.transformSelection(null, "Copies", { matrices: Array.from({ length: cnt }, (_, i) => [1, 0, 0, 1, dx * (i + 1), dy * (i + 1)]) });
      return A.transformSelection([1, 0, 0, 1, dx, dy], "Position", { keep: !!cnt });
    }
    if (mode === "rotate") {
      const a = val("angle", 0);
      if (cnt) return A.transformSelection(null, "Rotate copies", { matrices: Array.from({ length: cnt }, (_, i) => G.rotateMap(a * (i + 1), p[0], p[1])) });
      return A.transformSelection(G.rotateMap(a, p[0], p[1]), "Rotate");
    }
    if (mode === "scale" || mode === "size") {
      let sx, sy;
      if (mode === "scale") { sx = val("sx", 100) / 100; sy = keepP.checked ? sx : val("sy", 100) / 100; }
      else { const w = units.fromDisplay(val("w")), hh = units.fromDisplay(val("h")); sx = (box.x1 - box.x0) > 1e-9 ? w / (box.x1 - box.x0) : 1; sy = (box.y1 - box.y0) > 1e-9 ? hh / (box.y1 - box.y0) : 1; if (keepP.checked) { if (Math.abs(sx - 1) > 1e-9) sy = sx; else sx = sy; } }
      if (!(sx > 0.0001) || !(sy > 0.0001)) { toast("The scale must be more than 0"); return; }
      return A.transformSelection([sx, 0, 0, sy, p[0] - sx * p[0], p[1] - sy * p[1]], mode === "size" ? "Size" : "Scale", { keep: !!cnt,
        engine: { command: "xform.scale", values: { sx: sx * 100, sy: sy * 100, uniform: Math.abs(sx - sy) < 1e-9, about: "point", px: p[0], py: p[1], copy: !!cnt } } });
    }
    const tx = Math.tan(val("ax", 0) * Math.PI / 180), ty = Math.tan(val("ay", 0) * Math.PI / 180);
    return A.transformSelection([1, ty, tx, 1, -tx * p[1], -ty * p[0]], "Slant", { keep: !!cnt, engine: { command: "xform.slant", values: { angle_x: val("ax", 0), angle_y: val("ay", 0), about: "point", px: p[0], py: p[1], copy: !!cnt } } });
  }
  build();
  app.ui.transformMode = m => { mode = m; build(); };
  const el = h("div", { class: "panel transform" }, info, h("div", { class: "row" }, sel), fields);
  return { el, update() {
    const d = app.cur, s = d ? d.selected() : [];
    box = s.length ? G.bbox(s) : null;
    fill(info, box ? [h("b", null, String(s.length)), ` vector(s), ${units.fmt(box.x1 - box.x0)} × ${units.fmt(box.y1 - box.y0)}`] : "Nothing selected");
    apply.disabled = !box;
    if (!(fields.contains(document.activeElement) && document.activeElement.tagName === "INPUT")) loadValues();
  } };
}

// ------------------------------------------------------------------------------------------ Toolpaths
function materialText(d) {
  const m = d.project.material, sh = d.sheet;
  return `${units.fmt(sh && sh.thickness != null ? sh.thickness : m.thickness)} thick · Z zero at the ${m.z_zero === "top" ? "top" : "bottom"} · safe Z ${units.fmt(m.safe_z)} · rapid-down ${units.fmt(m.clearance)}`;
}
export function toolpathsPanel() {
  const body = h("div", { class: "tree tp" });
  const total = h("div", { class: "tptotal" }), mat = h("div", { class: "matinfo" });
  const newMenu = () => {
    const m = h("div", { class: "popmenu" }, app.schema.operations.map(o => h("div", { class: "item", onclick: () => { m.remove(); A.newToolpath(o.id); } }, o.label)));
    const r = newBtn.getBoundingClientRect(); Object.assign(m.style, { left: r.left + "px", top: (r.bottom + 2) + "px" });
    document.body.append(m);
    setTimeout(() => document.addEventListener("pointerdown", () => m.remove(), { once: true }), 0);
  };
  const newBtn = h("button", { type: "button", class: "btn", onclick: newMenu, title: "Create a toolpath" }, "New ▾");
  const sel = () => (app.cur ? app.cur.toolpathSelection : []);
  const one = fn => () => { const s = sel(); if (s.length) fn(s[0]); };
  let anchor = null;
  const el = h("div", { class: "panel toolpaths" },
    h("div", { class: "listbox tall" }, h("div", { class: "tphead" }, h("span", { class: "c1" }, "Toolpath"), h("span", { class: "c2" }, "Group"), h("span", { class: "c3" }, "Tool"), h("span", { class: "c4" }, "Time")), body),
    total, h("div", { class: "matrow" }, mat, h("button", { type: "button", class: "btn", onclick: () => A.runItem("toolpaths.material_setup") }, "Material Setup...")),
    h("div", { class: "btnrow" }, newBtn, tbtn("edit", "Edit toolpath", one(A.editToolpath)), tbtn("copy", "Duplicate", one(A.duplicateToolpath)),
      tbtn("minus", "Delete", () => A.deleteToolpaths(sel())), tbtn("up", "Move up", one(id => A.moveToolpath(id, -1))), tbtn("down", "Move down", one(id => A.moveToolpath(id, 1))), tbtn("recalc", "Recalculate all (F5)", () => A.recalc()),
      tbtn("select", "Select the toolpaths of this sheet", () => { const d = app.cur; if (!d) return; const v = tpView(d); d.toolpathSelection = d.project.toolpaths.filter(t => !v || v.shown.has(t.id)).map(t => t.id); update(); })),
    h("div", { class: "btnrow two" }, h("button", { type: "button", class: "btn", onclick: () => A.saveGcode() }, "Save G-code..."), h("button", { type: "button", class: "btn", onclick: () => A.simulate() }, "Simulate")));
  function update() {
    const d = app.cur;
    if (!d) { fill(body); fill(total); fill(mat); return; }
    const res = d.results ? d.results.results : [];
    const time = id => { const r = res.find(x => x.toolpath === id && x.sheet === d.sheetId); return r ? fmtTime(r.stats.time_s) : ""; };
    const rowOf = t => {
      const r = res.find(x => x.toolpath === t.id && x.sheet === d.sheetId);
      return h("div", { class: "trow" + (d.toolpathSelection.includes(t.id) ? " on" : "") + (r && r.error ? " err" : ""), title: r && r.error ? r.error : r && r.warnings.length ? r.warnings.join("\n") : "",
        onclick: e => {
          const ids = d.project.toolpaths.map(x => x.id);
          if (e.shiftKey && anchor) { const a = ids.indexOf(anchor), b = ids.indexOf(t.id); d.toolpathSelection = ids.slice(Math.min(a, b), Math.max(a, b) + 1); }
          else if (e.ctrlKey || e.metaKey) d.toolpathSelection = d.toolpathSelection.includes(t.id) ? d.toolpathSelection.filter(x => x !== t.id) : [...d.toolpathSelection, t.id];
          else d.toolpathSelection = [t.id];
          anchor = t.id; update();
        }, ondblclick: () => A.editToolpath(t.id) },
        h("span", { class: "c1", title: view && view.names[t.id] !== t.name ? "Name: " + t.name : undefined }, h("input", { type: "checkbox", checked: t.enabled, onclick: e => e.stopPropagation(), onchange: e => A.setToolpathEnabled(t.id, e.target.checked) }), " ", view && view.names[t.id] != null ? view.names[t.id] : t.name, r && r.error ? " ⚠" : ""),
        h("span", { class: "c2" }, t.group || ""), h("span", { class: "c3" }, t.tool ? t.tool.name : ""), h("span", { class: "c4" }, time(t.id)));
    };
    // the names with their {variables} put in and the toolpaths that belong on this sheet come from the engine
    // (post/naming.toolpath_name, cam/job.shown_on): asked again when the toolpaths or the sheets change
    const view = tpView(d);
    const listed = d.project.toolpaths.filter(t => !settings.toolpathsOfSheetOnly || !view || view.shown.has(t.id));
    const seen = new Set(), out = [];
    for (const t of listed) {
      if (!t.group) { out.push(rowOf(t)); continue; }
      if (seen.has(t.group)) continue;
      seen.add(t.group);
      out.push(h("div", { class: "tgroup" }, h("div", { class: "tg-head" }, h("span", { class: "tri" }, "▾"), h("b", null, t.group)), h("div", { class: "kids" }, listed.filter(x => x.group === t.group).map(rowOf))));
    }
    const hidden = d.project.toolpaths.length - listed.length;
    if (hidden) out.push(h("div", { class: "fb-empty", title: "View ▸ Toolpaths of This Sheet Only" }, `${hidden} toolpath(s) of other sheets not listed`));
    fill(body, out.length ? out : h("div", { class: "fb-empty" }, "No toolpaths yet — Toolpaths ▸ New Toolpath"));
    const tot = d.results && d.results.totals ? d.results.totals[d.sheetId] : null;
    fill(total, tot != null ? `Sheet total: ${fmtTime(tot)}${d.stale ? "  (out of date: F5)" : ""}` : (d.project.toolpaths.length ? "Not calculated yet (F5)" : ""));
    fill(mat, materialText(d));
  }
  return { el, update };
}

// the engine's view of the toolpaths on the sheet shown: {names: {id: name}, shown: Set(ids)}; null until it answered
const tpViews = new WeakMap();
function tpView(d) {
  const key = d.sheetId + "|" + JSON.stringify(d.project.toolpaths) + "|" + JSON.stringify(d.project.sheets.map(s => [s.id, s.name, s.group || "", s.vectors.map(v => v.id)]));
  const v = tpViews.get(d);
  if (v && v.key === key) return v.done ? v : v.last;
  const job = { key, done: false, last: v ? (v.done ? v : v.last) : null };
  tpViews.set(d, job);
  app.engine.call("toolpath_view", { project: d.project, sheet: d.sheetId }).then(r => {
    if (tpViews.get(d) !== job) return;
    Object.assign(job, { done: true, names: r.names, shown: new Set(r.shown) });
    emit("results");
  }).catch(() => { if (tpViews.get(d) === job) { Object.assign(job, { done: true, names: {}, shown: new Set(d.project.toolpaths.map(t => t.id)) }); } });
  return job.last;
}

export { cutOrderPanel, simulationPanel } from "./panels_job.js";
export { vectorLibraryPanel } from "./panels_draw.js";

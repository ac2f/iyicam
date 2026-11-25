/* iyiCAM web: what the menu items, tool bar buttons, shortcuts and panels do.  Every item of the desktop's menus is in
 * menudef.js; an item that has no entry in `registry` is shown greyed with "Not on the web yet". */
import G from "./geom.js";
import { h, uid, deepCopy, toast, store } from "./dom.js";
import { app, Doc, addDoc, closeDoc, reopenClosed, setCur, settings, setSetting, emit, rememberRecent, units } from "./state.js";
import { run, report, files as filesApi, cancelled } from "./engine.js";
import { MENUS } from "./menudef.js";
import { askBox, confirmBox, alertBox, promptBox, dialog } from "./dialog.js";
import { openFile, saveWhere, writeAgent, saveOnDevice, textBytes, fromB64, toB64 } from "./files.js";
import { commandForm } from "./dialogs/command.js";
import { reportDialog } from "./dialogs/common.js";
import { toolpathDialog } from "./dialogs/toolpath.js";
import { toolDatabase } from "./dialogs/tools.js";
import { newProjectDialog, materialSetup, sheetDialog, nestingDialog, nestReport, preferencesDialog, exportVectors, gcodeDialog, deliverFiles, shortcutsDialog, aboutDialog } from "./dialogs/misc.js";
import { scopeOf, layerApplies, layersFor, sheetsWith, openGroups } from "./scope.js";

export const registry = new Map();
const reg = (id, def) => registry.set(id, typeof def === "function" ? { run: def } : def);
/** for the feature modules (feat_*.js): an item of the menus made to work */
export const register = reg;
export const doc = () => app.cur;
export const status = msg => { app.message = msg; emit("message"); };
const hasDoc = () => !!app.cur;
const hasSel = n => () => !!app.cur && app.cur.selection.length >= n;
const view = () => app.ui.canvas;

// ------------------------------------------------------------------------------------------ documents
export async function startProject(spec) {
  const r = await run("new_project", spec, { label: "New project" });
  return r.project;
}
export async function newProject() {
  const spec = await newProjectDialog();
  if (!spec) return;
  try {
    const p = await startProject(spec);
    const d = addDoc(new Doc(p, spec.name || "Untitled"));
    d.dirty = false;
    app.ui.canvas.zoomExtents();
    status(`New project: ${p.sheets[0].width} × ${p.sheets[0].height}`);
  } catch (e) { report(e, "New project"); }
}

export async function openDocFromBytes({ name, bytes, path }) {
  const r = await run("import", { name, data_b64: toB64(bytes) }, { label: "Opening " + name });
  const d = new Doc(r.project, name.replace(/\.[^.]+$/, ""));
  if (path && /\.icam$/i.test(name)) { d.path = path; rememberRecent(name, path); }
  if (!/\.icam$/i.test(name)) d.dirty = true;
  addDoc(d);
  app.ui.canvas.zoomExtents();
  if (r.report && r.report.summary && !/\.icam$/i.test(name)) toast(r.report.summary);
  if (settings.calcOnOpen && d.project.toolpaths.length) await recalc();
  return d;
}
const importExts = () => [...new Set(app.schema.importers.flatMap(i => i.extensions))];
export async function openProject() {
  try {
    const f = await openFile({ title: "Open", accept: importExts().map(e => "." + e).join(","), exts: importExts() });
    if (f) await openDocFromBytes(f);
  } catch (e) { report(e, "Open"); }
}
export async function openRecent(r) {
  try {
    const F = await filesApi();
    await openDocFromBytes({ name: r.name, path: r.path, bytes: await F.read(r.path) });
  } catch (e) { report(e, "Open " + r.name); }
}

const projectBytes = d => textBytes(JSON.stringify(d.project));
export async function saveProject(d = doc(), as = false) {
  if (!d) return false;
  try {
    const bytes = projectBytes(d), name = d.name.replace(/\.icam$/i, "") + ".icam";
    if (!as && d.path) { const F = await filesApi(); await F.write(d.path, bytes, { overwrite: true }); }
    else if (!as && d.handle) await saveOnDevice(name, bytes, "application/json", d.handle);
    else {
      const w = await saveWhere({ title: "Save project", name, ext: "icam" });
      if (!w) return false;
      if (w.where === "agent") { await writeAgent(w.path, bytes); d.path = w.path; d.handle = null; d.name = w.name.replace(/\.icam$/i, ""); rememberRecent(w.name, w.path); }
      else { d.handle = (await saveOnDevice(name, bytes, "application/json", null)) || null; d.path = null; }
    }
    d.dirty = false; emit("docs");
    status(`Saved ${d.path || name}`);
    return true;
  } catch (e) { if (e && e.name === "AbortError" || cancelled(e)) return false; report(e, "Save"); return false; }
}
export async function saveAll() { for (const d of app.docs) if (d.dirty) await saveProject(d); }
export async function closeTab(d = doc()) {
  if (!d) return;
  if (d.dirty) {
    const a = await askBox(`Save the changes to "${d.name}"?`, { yes: "Save", no: "Discard" });
    if (a === undefined) return;
    if (a === "yes" && !(await saveProject(d))) return;
  }
  closeDoc(d);
}
export function reopenTab() { const d = reopenClosed(); if (d) addDoc(d); else status("No closed tab to reopen"); }

export async function importVectors() {
  const d = doc();
  if (!d) return;
  try {
    const exts = importExts().filter(e => e !== "icam");
    const f = await openFile({ title: "Import Vectors", accept: exts.map(e => "." + e).join(","), exts });
    if (!f) return;
    const r = await run("import", { name: f.name, data_b64: toB64(f.bytes), project: d.project, sheet: d.sheetId }, { label: "Importing " + f.name });
    d.replace("Import " + f.name, r.project, r.added || []);
    app.ui.canvas.zoomSelection();
    const rep = r.report || {};
    status(rep.summary || `Imported ${f.name}`);
    if (rep.errors && rep.errors.length) await alertBox(rep.errors.join("\n"), "Import");
    else if (rep.notes && rep.notes.length) toast(rep.notes[0]);
  } catch (e) { report(e, "Import"); }
}

export async function saveGcode() {
  const d = doc();
  if (!d) return;
  if (!d.project.toolpaths.some(t => t.enabled)) { alertBox("There are no toolpaths to write. Make a toolpath first (Toolpaths ▸ New Toolpath).", "Save G-code"); return; }
  const v = await gcodeDialog(d);
  if (!v) return;
  try {
    let ids;
    if (v.which === "selected") ids = d.toolpathSelection.slice();
    else if (v.which === "sheet") {          // (the toolpaths that belong on this sheet: the engine's cam.job.shown_on)
      const view = await app.engine.call("toolpath_view", { project: d.project, sheet: d.sheetId });
      ids = view.shown;
      if (!ids.length) { alertBox("No toolpath is calculated on this sheet.", "Save G-code"); return; }
    }
    const r = await run("gcode", { project: d.project, post: v.post, file_name: v.file_name, program_number: v.program_number, recalculate: v.recalculate || d.stale,
      sheets: v.all_sheets && v.which !== "sheet" ? sheetsWith(d.project, d.sheet).map(s => s.id) : [d.sheetId], per_toolpath: !!v.per_toolpath,
      toolpaths: ids }, { label: "Writing G-code", cancel: true });
    if (r.errors && r.errors.length) await alertBox(r.errors.join("\n"), "Save G-code");
    if (!r.files.length) { status("Nothing was written"); return; }
    if (await deliverFiles(r.files, "Save G-code")) status(`G-code: ${r.files.map(f => f.name).join(", ")}`);
    if (r.warnings && r.warnings.length) toast(r.warnings[0], "error");
  } catch (e) { report(e, "Save G-code"); }
}
function exportImage() {
  const d = doc();
  if (!d) return;
  view().canvas.toBlob(b => { const a = h("a", { href: URL.createObjectURL(b), download: d.name + ".png" }); document.body.append(a); a.click(); setTimeout(() => a.remove(), 1000); });
}

// ------------------------------------------------------------------------------------------ editing
export function select(ids) { const d = doc(); if (!d) return; d.selection = ids; emit("selection", d); }
export function undo() { const d = doc(); if (d && !d.undo()) status("Nothing to undo"); }
export function redo() { const d = doc(); if (d && !d.redo()) status("Nothing to redo"); }

function sheetOf(p, d) { return p.sheets.find(s => s.id === d.sheetId); }
export function addContours(contours, label, selectIt = true) {
  const d = doc();
  d.edit(label, p => {
    const v = G.vectorOf(contours, d.layerId);
    sheetOf(p, d).vectors.push(v);
    if (selectIt) d.selection = [v.id];
  });
}
export function moveSelection(dx, dy, copy = false) {
  const d = doc(), sel = new Set(d.selection);
  d.edit(copy ? "Copy" : "Move", p => {
    const sh = sheetOf(p, d);
    if (copy) {
      const made = sh.vectors.filter(v => sel.has(v.id)).map(v => ({ ...G.translate(v, dx, dy), id: uid() }));
      sh.vectors.push(...made);
      d.selection = made.map(v => v.id);
    } else sh.vectors = sh.vectors.map(v => (sel.has(v.id) ? G.translate(v, dx, dy) : v));
  });
}
/** Apply a map [a b c d e f] to the selection (or copies of it: `copies` maps). `engine`: the command that does it when an arc
 *  cannot follow the map (a stretch): {command, values}. */
export async function transformSelection(m, label, { engine = null, keep = false, matrices = null } = {}) {
  const d = doc();
  if (!d || !d.selection.length) return;
  const sel = new Set(d.selection);
  const maps = matrices || [m];
  const mine = d.sheet.vectors.filter(v => sel.has(v.id));
  const outs = maps.map(mm => mine.map(v => G.affine(v, mm)));
  if (outs.some(o => o.some(v => v === null))) {
    if (!engine) { status("This cannot be done on arcs here: use the Scale… or Slant… command"); return; }
    return runCommand(engine.command, engine.values);
  }
  d.edit(label, p => {
    const sh = sheetOf(p, d);
    if (keep || matrices) {
      const made = outs.flat().map(v => ({ ...v, id: uid() }));
      sh.vectors.push(...made);
      d.selection = made.map(v => v.id);
    } else {
      const byId = new Map(outs[0].map(v => [v.id, v]));
      sh.vectors = sh.vectors.map(v => byId.get(v.id) || v);
    }
  });
}
export function deleteSelection() {
  const d = doc();
  if (!d || !d.selection.length) return;
  const sel = new Set(d.selection);
  d.edit("Delete", p => { const sh = sheetOf(p, d); sh.vectors = sh.vectors.filter(v => !sel.has(v.id)); d.selection = []; });
}
const clone = (v, dx = 0, dy = 0) => ({ ...G.translate(v, dx, dy), id: uid() });
export function duplicate() {
  const d = doc();
  if (!d || !d.selection.length) return;
  const sel = new Set(d.selection);
  d.edit("Duplicate", p => { const sh = sheetOf(p, d); const made = sh.vectors.filter(v => sel.has(v.id)).map(v => clone(v, 10, 10)); sh.vectors.push(...made); d.selection = made.map(v => v.id); });
}
export function copySelection() {
  const d = doc();
  if (!d || !d.selection.length) return false;
  const layers = new Map(d.project.layers.map(l => [l.id, l]));
  app.clipboard = { n: 0, vectors: deepCopy(d.selected()).map(v => ({ ...v, layerName: layers.get(v.layer) ? layers.get(v.layer).name : "" })) };
  status(`${app.clipboard.vectors.length} vector(s) copied`);
  return true;
}
export function cutSelection() { if (copySelection()) deleteSelection(); }
export function paste() {
  const d = doc(), cb = app.clipboard;
  if (!d || !cb) return;
  cb.n++;
  d.edit("Paste", p => {
    const sh = sheetOf(p, d), layers = new Map(p.layers.map(l => [l.name, l.id]));
    const made = cb.vectors.map(v => { const c = clone(v, 10 * cb.n, 10 * cb.n); c.layer = layers.get(v.layerName) || d.layerId; delete c.layerName; return c; });
    sh.vectors.push(...made); d.selection = made.map(v => v.id);
  });
}
export function selectAll() {
  const d = doc(); if (!d) return;
  const layers = new Map(d.project.layers.map(l => [l.id, l]));
  select(d.sheet.vectors.filter(v => { const l = layers.get(v.layer); return !l || (l.visible && !l.locked); }).map(v => v.id));
}
export function invertSelection() {
  const d = doc(); if (!d) return;
  const cur = new Set(d.selection), layers = new Map(d.project.layers.map(l => [l.id, l]));
  select(d.sheet.vectors.filter(v => { const l = layers.get(v.layer); return !cur.has(v.id) && (!l || (l.visible && !l.locked)); }).map(v => v.id));
}
function selectOpen() { const d = doc(); if (d) select(d.sheet.vectors.filter(v => v.contours.some(c => !c.closed)).map(v => v.id)); }

function nudgeStep() {
  const s = view().view.s;
  if (settings.arrowStep === "auto") {
    const want = 4 / s;
    return [0.01, 0.02, 0.05, 0.1, 0.2, 0.5, 1, 2, 5, 10, 20, 50, 100].find(x => x >= want) || 100;
  }
  return settings.arrowStep === "custom" ? settings.arrowStepCustom : Number(settings.arrowStep) || 1;
}
export function nudge(dx, dy, e) {
  const d = doc();
  if (!d || !d.selection.length) return;
  const k = nudgeStep() * (e.shiftKey ? 10 : e.ctrlKey ? 0.1 : 1);
  const last = d.undoStack[d.undoStack.length - 1];
  const now = performance.now();
  if (last && last.label === "Nudge" && now - (nudge.t || 0) < 800) {          // (a run of arrow presses is one step of Undo)
    const sel = new Set(d.selection);
    d.sheet.vectors = d.sheet.vectors.map(v => (sel.has(v.id) ? G.translate(v, dx * k, dy * k) : v));
    d.stale = true; d.dirty = true; d.changed();
  } else d.edit("Nudge", p => { const sel = new Set(d.selection); const sh = sheetOf(p, d); sh.vectors = sh.vectors.map(v => (sel.has(v.id) ? G.translate(v, dx * k, dy * k) : v)); });
  nudge.t = now;
}

// ------------------------------------------------------------------------------------------ commands of the schema
let lastCommand = null;
export async function runCommand(id, values = null) {
  const d = doc();
  if (!d) return;
  const cmd = app.schema.commands.find(c => c.id === id);
  if (!cmd) return;
  if (d.selection.length < cmd.min_selection) { status(`${cmd.label.replace(/\.\.\.$/, "")}: select ${cmd.min_selection} vector(s) or more first`); toast(`Select at least ${cmd.min_selection} vector(s) first`); return; }
  if (!values) values = cmd.params.length ? await commandForm(cmd, d) : {};
  if (!values) return;
  try {
    const r = await run("command", { project: d.project, sheet: d.sheetId, selection: d.selection, command: id, values, active_layer: d.layerId }, { label: cmd.label.replace(/\.\.\.$/, "") });
    lastCommand = { id, values };
    if (r.changed) d.replace(cmd.label.replace(/\.\.\.$/, ""), r.project, r.selection);
    else select(r.selection);
    if (id.startsWith("create.") || id.startsWith("arrange.array")) view().redraw();
    const res = r.result;
    if (res && ((res.report && res.report.length) || (res.warnings && res.warnings.length))) await reportDialog(res, cmd.label.replace(/\.\.\.$/, ""));
    else if (r.messages && r.messages.length) { status(r.messages[0]); toast(r.messages.join(" · ")); }
  } catch (e) { report(e, cmd.label.replace(/\.\.\.$/, "")); }
}
const repeat = () => (lastCommand ? runCommand(lastCommand.id, lastCommand.values) : status("Nothing to repeat"));

// ------------------------------------------------------------------------------------------ toolpaths
function applyPatch(d, patch) {
  if (!patch) return;
  for (const [vid, vec] of Object.entries(patch.vectors || {})) for (const s of d.project.sheets) { const i = s.vectors.findIndex(v => v.id === vid); if (i >= 0) s.vectors[i] = vec; }
  for (const [tid, seq] of Object.entries(patch.sequences || {})) { const t = d.project.toolpaths.find(x => x.id === tid); if (t && seq && Object.keys(seq).length) t.sequence = seq; }
}
let calcSeq = 0;
export async function recalc() {
  const d = doc();
  if (!d) return;
  if (!d.project.toolpaths.length) { d.results = null; d.stale = false; emit("results", d); return; }
  const my = ++calcSeq;
  try {
    const r = await run("compute", { project: d.project, preview: true }, { label: "Calculating the toolpaths", cancel: true });
    if (my !== calcSeq || !app.docs.includes(d)) return;
    applyPatch(d, r.patch);
    d.results = r; d.stale = false;
    emit("results", d); emit("project", d);
    const bad = r.results.filter(x => x.error), warn = r.results.flatMap(x => x.warnings.map(w => `${x.name}: ${w}`));
    status(bad.length ? `${bad[0].name}: ${bad[0].error}` : `Calculated in ${r.seconds} s` + (warn.length ? ` — ${warn[0]}` : ""));
    if (bad.length) toast(`${bad[0].name}: ${bad[0].error}`, "error");
  } catch (e) { report(e, "Calculate"); }
}
export async function newToolpath(opId) {
  const d = doc();
  if (!d) return;
  const op = app.schema.operations.find(o => o.id === opId);
  let tool = null;
  if (settings.askTool) { tool = await toolDatabase({ pick: true, types: op.tool_types }); if (!tool) return; }
  const r = await toolpathDialog(d, opId, null, { tool });
  if (!r) return;
  d.edit(`Add toolpath ${r.tp.name}`, p => p.toolpaths.push(r.tp));
  d.toolpathSelection = [r.tp.id];
  await recalc();
}
export async function editToolpath(id) {
  const d = doc();
  const tp = d && d.project.toolpaths.find(t => t.id === id);
  if (!tp) return;
  const r = await toolpathDialog(d, tp.op, tp);
  if (!r) return;
  if (r.keepOld) { r.tp.id = uid(); d.edit(`Add toolpath ${r.tp.name}`, p => p.toolpaths.push(r.tp)); }
  else d.edit(`Edit toolpath ${r.tp.name}`, p => { p.toolpaths[p.toolpaths.findIndex(t => t.id === id)] = r.tp; });
  await recalc();
}
export function deleteToolpaths(ids) {
  const d = doc();
  if (!d || !ids.length) return;
  const s = new Set(ids);
  d.edit("Delete toolpath", p => { p.toolpaths = p.toolpaths.filter(t => !s.has(t.id)); });
  d.toolpathSelection = [];
  if (d.results) d.results = { ...d.results, results: d.results.results.filter(r => !s.has(r.toolpath)) };
  recalc();
}
export function duplicateToolpath(id) {
  const d = doc(), tp = d && d.project.toolpaths.find(t => t.id === id);
  if (!tp) return;
  const c = { ...deepCopy(tp), id: uid(), name: tp.name + " copy" };
  d.edit("Duplicate toolpath", p => { p.toolpaths.splice(p.toolpaths.findIndex(t => t.id === id) + 1, 0, c); });
  d.toolpathSelection = [c.id];
  recalc();
}
export function moveToolpath(id, dir) {
  const d = doc();
  if (!d) return;
  const i = d.project.toolpaths.findIndex(t => t.id === id), j = i + dir;
  if (i < 0 || j < 0 || j >= d.project.toolpaths.length) return;
  d.edit("Move toolpath", p => { const [t] = p.toolpaths.splice(i, 1); p.toolpaths.splice(j, 0, t); });
  recalc();
}
export function setToolpathEnabled(id, on) {
  const d = doc();
  d.edit(on ? "Switch toolpath on" : "Switch toolpath off", p => { p.toolpaths.find(t => t.id === id).enabled = on; });
  recalc();
}
export async function simulate() {
  const d = doc();
  if (!d) return;
  if (!d.project.toolpaths.some(t => t.enabled)) { alertBox("Make a toolpath first: the simulation shows what it cuts.", "Simulate"); return; }
  try {
    const r = await run("simulate", { project: d.project, sheet: d.sheetId, ...(settings.simResolution ? { resolution: settings.simResolution } : {}) }, { label: "Simulating", cancel: true });
    const img = new Image();
    img.src = "data:image/png;base64," + r.png_b64;
    await img.decode();
    d.sim = { img, extent: r.extent, sheet: r.sheet, time_s: r.time_s, res: r.resolution, size: [r.width_px, r.height_px] };
    setSetting("showSim", true);
    emit("results", d);
    status(`Simulation done (${r.resolution.toFixed ? r.resolution.toFixed(2) : r.resolution} mm per pixel)`);
  } catch (e) { report(e, "Simulate"); }
}
export function clearSimulation() { const d = doc(); if (d) { d.sim = null; emit("results", d); } }

export async function nest() {
  const d = doc();
  if (!d) return;
  if (!d.sheet.vectors.length) { alertBox("There are no vectors to nest.", "Nesting"); return; }
  const o = await nestingDialog(d);
  if (!o) return;
  try {
    const start = d.sheet;
    const r = await run("nest", { project: d.project, settings: o.settings, objects: o.objects, sheet: d.sheetId }, { label: "Nesting", cancel: true });
    d.replace("Nest parts", r.project, []);
    if (r.new_sheets && r.new_sheets.length && !scopeOf(start)) {     // (an isolated group keeps its new sheets: nothing to ask)
      const choice = await newSheetsGroup(r.new_sheets.length, start.group || "", d.project);
      if (choice !== null && choice !== (r.new_sheets_group || "")) await sheetScope("Group the new sheets", { action: "new_sheets", sheets: r.new_sheets, sheet: start.id, name: choice });
    }
    app.ui.canvas.zoomExtents();
    status(`${r.placed} part(s) placed` + (r.unplaced ? `, ${r.unplaced} did not fit` : ""));
    await nestReport(r);
    if (d.project.toolpaths.length && settings.autoRecalc) recalc();
  } catch (e) { report(e, "Nesting"); }
}

/** after a nesting that made new sheets: the group they go into (the group of the sheet it started on first), a new group,
 * or none; null: as they are (the engine put them in the group of that sheet) */
async function newSheetsGroup(n, current, project) {
  const sel = h("select", null,
    current ? h("option", { value: "g:" + current }, `The group of this sheet: ${current}`) : null,
    openGroups(project).filter(g => g !== current).map(g => h("option", { value: "g:" + g }, `The group ${g}`)),
    h("option", { value: "new" }, "A new group..."), h("option", { value: "none" }, "No group"));
  const name = h("input", { type: "text", placeholder: "Name of the new group", disabled: true });
  sel.addEventListener("change", () => { name.disabled = sel.value !== "new"; if (!name.disabled) name.focus(); });
  const r = await dialog({ title: "New sheets", width: 420, body: h("div", { class: "fields" },
    h("p", { class: "dlg-text" }, `The nesting made ${n} new sheet${n === 1 ? "" : "s"}. Put ${n === 1 ? "it" : "them"} in a group?`),
    h("label", { class: "row" }, h("span", { class: "lab" }, "Group"), sel), h("label", { class: "row" }, h("span", { class: "lab" }, "New group"), name)),
    buttons: [{ label: "Cancel", value: undefined }, { label: "OK", primary: true, value: "ok" }] }).done;
  if (r !== "ok") return null;
  if (sel.value === "none") return "";
  if (sel.value === "new") return name.value.trim() || "Nested";
  return sel.value.slice(2);
}

// ------------------------------------------------------------------------------------------ layers and sheets
export const layerOps = {
  add() { const d = doc(); d.edit("Add layer", p => { const used = p.layers.length, side = scopeOf(d.sheet); const l = { id: uid(), name: `Layer ${used + 1}`, color: app.schema.layer_colors[used % app.schema.layer_colors.length], visible: true, locked: false, ...(side ? { sheet_group: side } : {}) }; p.layers.push(l); d.layerId = l.id; }); },
  /** the layers in a new order and groups (a drag in the Layers panel): `order` the ids shown, `groups` id -> group */
  arrange(order, groups) {
    const d = doc();
    d.edit("Arrange layers", p => {
      const by = new Map(p.layers.map(l => [l.id, l])), want = order.filter(i => by.has(i)), set = new Set(want);
      const slots = p.layers.map((l, k) => (set.has(l.id) ? k : -1)).filter(k => k >= 0);
      const out = p.layers.slice();
      slots.forEach((k, n) => { out[k] = by.get(want[n]); });
      p.layers = out;
      for (const [id, g] of Object.entries(groups)) { const l = by.get(id); if (l) { if (g) l.group = g; else delete l.group; } }
    });
  },
  renameGroup(old, name) { const d = doc(); if (!name || name === old) return; d.edit("Rename group of layers", p => { for (const l of p.layers) if (l.group === old) l.group = name; }); },
  setGroupState(g, patch) { const d = doc(); d.edit("Layer state", p => { for (const l of p.layers) if (l.group === g && layerApplies(l, sheetOf(p, d))) Object.assign(l, patch); }); },
  async remove(id) {
    const d = doc();
    if (d.project.layers.length < 2) { status("A project keeps at least one layer"); return; }
    const n = d.project.sheets.reduce((a, s) => a + s.vectors.filter(v => v.layer === id).length, 0);
    if (n && !(await confirmBox(`Delete the layer and its ${n} vector(s)?`, { yes: "Delete" }))) return;
    d.edit("Delete layer", p => { p.layers = p.layers.filter(l => l.id !== id); for (const s of p.sheets) s.vectors = s.vectors.filter(v => v.layer !== id); });
  },
  move(id, dir) { const d = doc(); d.edit("Move layer", p => { const i = p.layers.findIndex(l => l.id === id), j = i + dir; if (i < 0 || j < 0 || j >= p.layers.length) return; const [l] = p.layers.splice(i, 1); p.layers.splice(j, 0, l); }); },
  set(id, patch, label = "Change layer") { const d = doc(); d.edit(label, p => Object.assign(p.layers.find(l => l.id === id), patch)); },
  moveSelection(id) { const d = doc(); const sel = new Set(d.selection); if (!sel.size) return; d.edit("Move to layer", p => { const sh = sheetOf(p, d); sh.vectors = sh.vectors.map(v => (sel.has(v.id) ? { ...v, layer: id } : v)); }); },
  selectLayer(id) { const d = doc(); select(d.sheet.vectors.filter(v => v.layer === id).map(v => v.id)); },
};
/** a change of groups or isolation made by the engine (core/scope.py: the layers and toolpaths go along), one undo step */
async function sheetScope(label, payload) {
  const d = doc();
  if (!d) return null;
  try {
    const r = await run("sheet_scope", { project: d.project, ...payload }, { label });
    d.replace(label, r.project);
    layerOfSide(d);
    return r;
  } catch (e) { report(e, label); return null; }
}
/** the current layer is one that is there on the sheet open (an isolated group has layers of its own) */
export function layerOfSide(d) {
  const l = d.project.layers.find(x => x.id === d.layerId), sh = d.sheet;
  if (!sh || (l && layerApplies(l, sh))) return;
  const here = layersFor(d.project, sh), same = l && here.find(x => x.name === l.name);
  if (same || here.length) d.layerId = (same || here[0]).id;
}
export const sheetOps = {
  add() { const d = doc(); const cur = d.sheet; d.edit("Add sheet", p => { const s = { id: uid(), name: `Sheet ${p.sheets.length + 1}`, width: cur.width, height: cur.height, thickness: cur.thickness != null ? cur.thickness : p.material.thickness, vectors: [], outline: null, group: cur.group || "", ...(cur.isolated && cur.group ? { isolated: true } : {}) }; p.sheets.push(s); d.sheetId = s.id; d.selection = []; }); },
  duplicate() { const d = doc(); const cur = d.sheet; d.edit("Duplicate sheet", p => { const s = { ...deepCopy(cur), id: uid(), name: cur.name + " copy" }; s.vectors = s.vectors.map(v => ({ ...v, id: uid() })); p.sheets.splice(p.sheets.findIndex(x => x.id === cur.id) + 1, 0, s); d.sheetId = s.id; d.selection = []; }); },
  async remove() {
    const d = doc();
    if (d.project.sheets.length < 2) { status("A project keeps at least one sheet"); return; }
    if (d.sheet.vectors.length && !(await confirmBox(`Delete "${d.sheet.name}" and its ${d.sheet.vectors.length} vector(s)?`, { yes: "Delete" }))) return;
    d.edit("Delete sheet", p => { p.sheets = p.sheets.filter(s => s.id !== d.sheetId); d.sheetId = p.sheets[0].id; d.selection = []; });
  },
  move(dir) { const d = doc(); d.edit("Move sheet", p => { const i = p.sheets.findIndex(s => s.id === d.sheetId), j = i + dir; if (i < 0 || j < 0 || j >= p.sheets.length) return; const [s] = p.sheets.splice(i, 1); p.sheets.splice(j, 0, s); }); },
  async group() { const d = doc(); const n = await promptBox("Name of the group of sheets (empty: none)", d.sheet.group || ""); if (n === null) return; await sheetScope("Sheet group", { action: "group", sheets: [d.sheetId], name: n.trim() }); },
  /** a drag in the Sheets panel: the sheets in a new order and groups (into an isolated group: isolated with it) */
  arrange(order, groups) { return sheetScope("Arrange sheets", { action: "arrange", order, groups }); },
  renameGroup(old, name) { if (!name || name === old) return; return sheetScope("Rename group", { action: "rename", old, name }); },
  /** isolate the group of the sheet open (a sheet of no group: a group of its own), or end its isolation */
  async isolate() {
    const d = doc(), side = scopeOf(d.sheet);
    const r = side ? await sheetScope("End isolation", { action: "unisolate", name: side }) : await sheetScope("Isolate", { action: "isolate", sheets: [d.sheetId] });
    if (r) toast(side ? `${side} is part of the project again` : `${r.group} is isolated: its layers, toolpaths, nesting and G-code are its own`);
  },
  open(id) { const d = doc(); if (d.sheetId === id) return; d.sheetId = id; d.selection = []; layerOfSide(d); d.changed(); view().zoomExtents(); },
  props() { const d = doc(); return sheetDialog(d, d.sheet); },
};

// ------------------------------------------------------------------------------------------ drawing tools
export const TOOLS = { select: "Select / Transform", polyline: "Polyline / Bezier", line: "Line", rect: "Rectangle", circle: "Circle", ellipse: "Ellipse", polygon: "Polygon", arc3: "Arc (3 points)", measure: "Measure" };
const TOOL_OF_ID = { "draw.select_transform": "select", "draw.polyline_bezier": "polyline", "draw.line": "line", "draw.rectangle": "rect", "draw.circle": "circle", "draw.ellipse": "ellipse",
  "draw.polygon": "polygon", "draw.arc_3_points": "arc3", "draw.measure": "measure" };
export function setTool(t) {
  if (app.ui.canvas.draft) app.ui.canvas.draft = null;
  app.tool = t; emit("tool"); view().redraw();
  const hint = { select: "Click to select (Shift: add, Ctrl: toggle). Drag to move (Ctrl+drag: copy), handles to scale/rotate. Arrows nudge.",
    polyline: "Click the points; double-click or Enter finishes, Backspace takes a point back, click the first point to close.", line: "Drag, or click the two ends (Esc cancels).",
    rect: "Drag a corner to the opposite one (Ctrl: square, Shift: from the centre).", circle: "Drag from the centre to the edge.", ellipse: "Drag the box of the ellipse (Ctrl: circle, Shift: from the centre).",
    polygon: "Drag from the centre to a corner (sides: " + (settings.polySides || 6) + ", Ctrl: 15° steps).", arc3: "Click the start, a point on the arc, and the end.", measure: "Drag from one point to another." }[t];
  status(hint || "");
}
const sidesOf = () => settings.polySides || 6;
function shapeFor(tool, a, b, e) {
  const k = { ctrl: e && (e.ctrlKey || e.metaKey), shift: e && e.shiftKey };
  if (tool === "line" || tool === "measure") { const { a: p, b: q } = G.dragShape("line", a, b, k); return [G.polyline([p, q], false)]; }
  if (tool === "rect") { const { a: p, b: q } = G.dragShape("rect", a, b, k); return [G.rectangle(p[0], p[1], q[0], q[1])]; }
  if (tool === "ellipse") { const { a: p, b: q } = G.dragShape("ellipse", a, b, k); const cx = (p[0] + q[0]) / 2, cy = (p[1] + q[1]) / 2, rx = Math.abs(q[0] - p[0]) / 2, ry = Math.abs(q[1] - p[1]) / 2; return rx < 1e-9 || ry < 1e-9 ? null : [G.ellipse(cx, cy, rx, ry)]; }
  if (tool === "circle") { const r = Math.hypot(b[0] - a[0], b[1] - a[1]); return r < 1e-9 ? null : [G.circle(a[0], a[1], r)]; }
  if (tool === "polygon") { const { b: q } = G.dragShape("polygon", a, b, k); const r = Math.hypot(q[0] - a[0], q[1] - a[1]); return r < 1e-9 ? null : [G.polygon(a[0], a[1], r, sidesOf(), Math.atan2(q[1] - a[1], q[0] - a[0]) * 180 / Math.PI - 90)]; }
  return null;
}
const describeShape = (tool, a, b) => {
  const dx = b[0] - a[0], dy = b[1] - a[1], L = Math.hypot(dx, dy);
  if (tool === "line" || tool === "measure") return `Length ${units.fmt(L)}  ΔX ${units.fmt(dx)}  ΔY ${units.fmt(dy)}  angle ${(Math.atan2(dy, dx) * 180 / Math.PI).toFixed(2)}°`;
  if (tool === "rect") return `${units.fmt(Math.abs(dx))} × ${units.fmt(Math.abs(dy))}`;
  if (tool === "circle" || tool === "polygon") return `radius ${units.fmt(L)} (Ø ${units.fmt(2 * L)})`;
  return `${units.fmt(Math.abs(dx))} × ${units.fmt(Math.abs(dy))}`;
};
const LABELS = { line: "Line", rect: "Rectangle", circle: "Circle", ellipse: "Ellipse", polygon: "Polygon", polyline: "Polyline", arc3: "Arc" };
function finishPolyline(closed = false) {
  const v = view(), dr = v.draft;
  if (!dr || dr.tool !== "polyline") return;
  const pts = dr.pts;
  v.draft = null;
  if (pts.length >= 2) addContours([G.polyline(pts, closed)], "Draw polyline");
  v.redraw();
}
export const canvasApi = {
  select, moveSelection, transformSelection: (m, label, dg) => {
    const info = dg && dg.h && dg.h.kind === "scale" && dg.sx !== undefined ? { engine: { command: "xform.scale", values: { sx: dg.sx * 100, sy: dg.sy * 100, uniform: Math.abs(dg.sx - dg.sy) < 1e-9, about: "point", px: dg.anchor[0], py: dg.anchor[1], copy: false } } } : {};
    transformSelection(m, label, info);
  },
  finishPolyline: () => finishPolyline(false),
  dblclick() { if (view().draft && view().draft.tool === "polyline") { const d = view().draft; d.pts.pop(); finishPolyline(false); } },
  toolDown(tool, w, e, v) {
    const x = app.extraTools[tool]; if (x) return x.down && x.down(w, e, v);
    const p = v.snapped(w);
    let dr = v.draft;
    if (tool === "polyline") {
      if (!dr) dr = v.draft = { tool, pts: [] };
      if (dr.pts.length > 2 && Math.hypot(p[0] - dr.pts[0][0], p[1] - dr.pts[0][1]) * v.view.s < 8) { finishPolyline(true); return; }
      dr.pts.push(p); dr.shape = [G.polyline([...dr.pts, p], false)];
      v.redraw(); return;
    }
    if (tool === "arc3") {
      if (!dr) dr = v.draft = { tool, pts: [] };
      dr.pts.push(p);
      if (dr.pts.length === 3) { const r = G.arc3(dr.pts[0], dr.pts[1], dr.pts[2]); v.draft = null; if (r) addContours([r.contour], "Draw arc"); else status("The three points are in line"); }
      else dr.shape = [G.polyline(dr.pts, false)];
      v.redraw(); return;
    }
    if (dr && dr.mode === "wait") {              // (the second click of a click-click shape)
      const shape = shapeFor(tool, dr.a, p, e);
      v.draft = null;
      if (shape && tool !== "measure") addContours(shape, "Draw " + LABELS[tool].toLowerCase());
      else if (shape) { v.draft = { tool, a: dr.a, shape, mode: "done" }; }
      v.redraw(); return;
    }
    v.draft = { tool, a: p, mode: "drag", shape: null, t0: performance.now(), s0: [e.clientX, e.clientY] };
    v.drag = { kind: "draw" };
  },
  toolMove(tool, w, e, v) {
    const x = app.extraTools[tool]; if (x) return x.move && x.move(w, e, v);
    const dr = v.draft;
    if (!dr || dr.mode !== "drag") return;
    const p = v.snapped(w);
    dr.shape = shapeFor(tool, dr.a, p, e);
    if (dr.shape) status(describeShape(tool, dr.a, p));
    v.redraw();
  },
  toolHover(tool, p, e, v) {
    const x = app.extraTools[tool]; if (x) return x.hover && x.hover(p, e, v);
    const dr = v.draft;
    if (!dr) return;
    if (tool === "polyline" && dr.pts.length) { dr.shape = [G.polyline([...dr.pts, p], false)]; status(`${dr.pts.length} point(s); ${describeShape("line", dr.pts[dr.pts.length - 1], p)}`); }
    else if (tool === "arc3" && dr.pts.length) {
      const arc = dr.pts.length === 2 ? G.arc3(dr.pts[0], dr.pts[1], p) : null;
      dr.shape = [arc ? arc.contour : G.polyline([dr.pts[0], p], false)];
    }
    else if (dr.mode === "wait") { dr.shape = shapeFor(tool, dr.a, p, e); if (dr.shape) status(describeShape(tool, dr.a, p)); }
  },
  toolUp(tool, w, e, v) {
    const x = app.extraTools[tool]; if (x) return x.up && x.up(w, e, v);
    const dr = v.draft;
    if (!dr || dr.mode !== "drag") return;
    const p = v.snapped(w);
    const moved = Math.hypot(e.clientX - dr.s0[0], e.clientY - dr.s0[1]) > 5;
    if (!moved) { dr.mode = "wait"; dr.shape = null; return; }          // (a click: the second click ends it)
    const shape = shapeFor(tool, dr.a, p, e);
    if (tool === "measure") { v.draft = { tool, a: dr.a, shape, mode: "done" }; v.redraw(); return; }
    v.draft = null;
    if (shape) addContours(shape, "Draw " + LABELS[tool].toLowerCase());
    v.redraw();
  },
};

// ------------------------------------------------------------------------------------------ the registry
const NYI = "Not on the web yet";
export const notYet = NYI;
const toggleSetting = k => ({ run: () => setSetting(k, !settings[k]), checked: () => !!settings[k], enabled: () => true });

export function registerAll() {
  reg("file.new", newProject); reg("file.open", openProject);
  reg("file.reopen_closed_tab", reopenTab);
  reg("file.save", { run: () => saveProject(), enabled: hasDoc }); reg("file.save_as", { run: () => saveProject(doc(), true), enabled: hasDoc });
  reg("file.save_all", { run: saveAll, enabled: hasDoc }); reg("file.close_tab", { run: () => closeTab(), enabled: hasDoc });
  reg("file.import_vectors", { run: importVectors, enabled: hasDoc }); reg("file.export_vectors", { run: () => exportVectors(doc()), enabled: hasDoc });
  reg("file.save_g_code", { run: saveGcode, enabled: hasDoc }); reg("file.export_image", { run: exportImage, enabled: hasDoc });
  reg("edit.undo", { run: undo, enabled: () => !!(doc() && doc().undoStack.length) }); reg("edit.redo", { run: redo, enabled: () => !!(doc() && doc().redoStack.length) });
  reg("edit.repeat", { run: repeat, enabled: () => !!lastCommand });
  reg("edit.delete", { run: deleteSelection, enabled: hasSel(1) }); reg("edit.duplicate", { run: duplicate, enabled: hasSel(1) });
  reg("edit.cut", { run: cutSelection, enabled: hasSel(1) }); reg("edit.copy", { run: copySelection, enabled: hasSel(1) }); reg("edit.paste", { run: paste, enabled: () => !!(doc() && app.clipboard) });
  reg("edit.select.select_all", { run: selectAll, enabled: hasDoc }); reg("edit.select.invert_selection", { run: invertSelection, enabled: hasDoc }); reg("edit.select.select_open_vectors", { run: selectOpen, enabled: hasDoc });
  reg("edit.preferences", preferencesDialog);
  reg("view.zoom_extents", { run: () => view().zoomExtents(), enabled: hasDoc }); reg("view.zoom_selection", { run: () => view().zoomSelection(), enabled: hasDoc });
  for (const [id, k] of [["view.show_toolpaths", "showToolpaths"], ["view.show_tool_width", "showToolWidth"], ["view.draw_toolpaths_solid", "solidToolpaths"], ["view.show_directions", "showDirections"], ["view.show_cut_order", "showCutOrder"],
    ["view.show_route_between_cuts", "showRoute"], ["view.show_start_points", "showStarts"], ["view.show_simulation", "showSim"], ["view.toolpaths_of_this_sheet_only", "toolpathsOfSheetOnly"],
    ["view.snap_to_grid", "snapGrid"], ["view.snap_to_vectors", "snapVectors"], ["view.snap_to_alignments", "snapLevel"], ["view.snap_to_edges", "snapEdges"], ["vectors.edit.resize_handles_mouse", "resizeHandles"],
    ["toolpaths.cut_inner_pieces_g_bek_first", "cutInnerFirst"]]) reg(id, toggleSetting(k));
  reg("view.clear_guides", { run: () => { doc().guides = []; view().redraw(); }, enabled: () => !!(doc() && doc().guides.length) });
  for (const [id, dock] of [["view.project", "project"], ["view.selection", "selection"], ["view.transform", "transform"], ["view.toolpaths", "toolpaths"], ["view.cut_order", "cutorder"], ["view.simulation", "simulation"], ["view.vector_library", "veclib"]])
    reg(id, { run: () => app.ui.docks.toggle(dock), checked: () => app.ui.docks.visible(dock) });
  for (const [id, v] of [["view.appearance.follow_the_system", "system"], ["view.appearance.light", "light"], ["view.appearance.dark", "dark"]]) reg(id, { run: () => setSetting("appearance", v), checked: () => settings.appearance === v });
  for (const [id, t] of Object.entries(TOOL_OF_ID)) reg(id, { run: () => setTool(t), checked: () => app.tool === t, enabled: hasDoc });
  for (const [id, v] of [["automatic_follows_the_zoom", "auto"], ["0_1_mm", "0.1"], ["1_mm", "1"], ["10_mm", "10"]]) reg("vectors.edit.arrow_key_step." + id, { run: () => setSetting("arrowStep", v), checked: () => settings.arrowStep === v });
  reg("vectors.edit.arrow_key_step.custom", { run: async () => { const t = await promptBox("Arrow key step (mm)", String(settings.arrowStepCustom)); const n = Number(t); if (t !== null && n > 0) { setSetting("arrowStepCustom", n); setSetting("arrowStep", "custom"); } }, checked: () => settings.arrowStep === "custom" });
  const dockPanel = (id, dock, mode) => reg(id, { run: () => { app.ui.docks.show(dock); app.ui.transformMode && app.ui.transformMode(mode); }, enabled: hasDoc });
  dockPanel("vectors.transform.position_panel", "transform", "position"); dockPanel("vectors.transform.rotate_panel", "transform", "rotate");
  dockPanel("vectors.transform.scale_and_mirror_panel", "transform", "scale"); dockPanel("vectors.transform.size_panel", "transform", "size"); dockPanel("vectors.transform.slant_panel", "transform", "slant");
  for (const [id, op] of [["profile", "profile"], ["pocket_area_clearance", "pocket"], ["drilling", "drill"], ["engrave_follow_vector", "engrave"], ["v_carve", "vcarve"], ["inlay", "inlay"], ["fluting", "fluting"]])
    reg("toolpaths.new_toolpath." + id, { run: () => newToolpath(op), enabled: hasDoc });
  reg("toolpaths.recalculate_all", { run: recalc, enabled: () => !!(doc() && doc().project.toolpaths.length) });
  reg("toolpaths.cut_order", { run: () => app.ui.docks.show("cutorder"), enabled: hasDoc });
  reg("toolpaths.material_setup", { run: () => materialSetup(doc()), enabled: hasDoc }); reg("toolpaths.tool_database", () => toolDatabase());
  reg("toolpaths.simulate", { run: simulate, enabled: hasDoc }); reg("toolpaths.clear_simulation", { run: clearSimulation, enabled: () => !!(doc() && doc().sim) });
  reg("toolpaths.save_g_code", { run: saveGcode, enabled: hasDoc });
  reg("nesting.nest_parts", { run: nest, enabled: hasDoc });
  reg("help.keyboard_shortcuts", shortcutsDialog); reg("help.about_iyicam", aboutDialog);
  // the commands of the schema: Vectors ▸ Create / Edit / Corners / Transform / Arrange and Edit ▸ Select
  const walk = items => { for (const it of items) { if (it === "-") continue; if (it.cmd) { const c = app.schema.commands.find(x => x.id === it.cmd); if (c && !registry.has(it.id)) reg(it.id, { run: () => runCommand(it.cmd), enabled: () => !!doc() && doc().selection.length >= c.min_selection }); } if (it.menu) walk(it.menu); } };
  for (const m of MENUS) walk(m.menu);
}

// ------------------------------------------------------------------------------------------ keys
const SHORT = new Map();
const normKey = k => ({ delete: "Del", " ": "Space", escape: "Esc", arrowup: "Up", arrowdown: "Down", arrowleft: "Left", arrowright: "Right" }[k.toLowerCase()] || (k.length === 1 ? k.toUpperCase() : k));
export function comboOf(e) {
  const parts = [];
  if (e.ctrlKey || e.metaKey) parts.push("Ctrl");
  if (e.altKey) parts.push("Alt");
  if (e.shiftKey) parts.push("Shift");
  let k = e.key;
  if (e.code && /^Digit|^Key/.test(e.code)) k = e.code.slice(-1);
  if (e.code === "Backquote") k = "`";
  parts.push(normKey(k));
  return parts.join("+");
}
// what the person changed in Edit ▸ Keyboard Shortcuts: item id -> list of keys ([] for none); only what differs from the program's own
// keys is kept (in this browser), so a new version can bring new keys without overwriting the person's
let OVERRIDES = store.get("iyicam.shortcuts", {});
if (!OVERRIDES || typeof OVERRIDES !== "object") OVERRIDES = {};
export const shortcutOverrides = () => OVERRIDES;
export const defaultKeys = it => (it.sc ? it.sc.split(",").map(x => x.trim()).filter(Boolean) : []);
/** the keys of a menu item now: the person's, else the program's own */
export const keysOf = it => (Array.isArray(OVERRIDES[it.id]) ? OVERRIDES[it.id] : defaultKeys(it));
/** what the menu shows beside the item */
export const shortcutText = it => keysOf(it).join(", ");
export function setShortcutOverrides(o) { OVERRIDES = o || {}; store.set("iyicam.shortcuts", OVERRIDES); buildShortcuts(); emit("settings"); }
export function buildShortcuts() {
  SHORT.clear();
  const walk = items => { for (const it of items) { if (it === "-") continue; if (it.id && !it.menu) for (const s of keysOf(it)) if (!SHORT.has(s)) SHORT.set(s, it.id); if (it.menu) walk(it.menu); } };
  for (const m of MENUS) walk(m.menu);
}
export function runItem(id) {
  const r = registry.get(id);
  if (!r) { status(NYI); return false; }
  if (r.enabled && !r.enabled()) return false;
  Promise.resolve(r.run()).catch(e => report(e));
  return true;
}
const typing = t => t && (t.tagName === "INPUT" || t.tagName === "TEXTAREA" || t.tagName === "SELECT" || t.isContentEditable);
export function onKeyDown(e) {
  if (e.key === " " && !typing(e.target)) { if (app.ui.canvas) app.ui.canvas.space = true; if (e.target === app.ui.canvas.canvas) e.preventDefault(); }
  if (typing(e.target) || document.querySelector(".dlg-root:not(.modeless)")) return;
  const d = doc(), v = app.ui.canvas;
  if (e.key === "Escape") {
    if (v.draft) { v.draft = null; v.drag = null; v.redraw(); } else if (app.tool !== "select") setTool("select"); else if (d && d.selection.length) select([]);
    return;
  }
  if (v.draft && v.draft.tool === "polyline") {
    if (e.key === "Enter") { e.preventDefault(); finishPolyline(false); return; }
    if (e.key === "Backspace") { e.preventDefault(); v.draft.pts.pop(); v.draft.shape = null; v.redraw(); return; }
  }
  if (d && ["ArrowUp", "ArrowDown", "ArrowLeft", "ArrowRight"].includes(e.key) && !e.altKey) {
    e.preventDefault();
    nudge(e.key === "ArrowLeft" ? -1 : e.key === "ArrowRight" ? 1 : 0, e.key === "ArrowUp" ? 1 : e.key === "ArrowDown" ? -1 : 0, e);
    return;
  }
  const id = SHORT.get(comboOf(e));
  if (id && registry.has(id)) { e.preventDefault(); runItem(id); }
}
export function onKeyUp(e) { if (e.key === " " && app.ui.canvas) app.ui.canvas.space = false; }

// ------------------------------------------------------------------------------------------ start
export async function startDefault() {
  const p = await startProject({ width: 1200, height: 800, thickness: 18, name: "Untitled" });
  const d = addDoc(new Doc(p, "Untitled"));
  d.dirty = false;
  return d;
}
export { store, fromB64, setCur };

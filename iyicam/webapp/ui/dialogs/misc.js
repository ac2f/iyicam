/* iyiCAM web: the smaller windows of the desktop program: New, Material Setup, Sheet, Nesting, Preferences, Export, Save G-code,
 * keyboard shortcuts, About. */
import { h, fill, store, toast, deepCopy, fmtTime } from "../dom.js";
import { dialog, tabs, alertBox, promptBox } from "../dialog.js";
import { ParamForm } from "../forms.js";
import { app, settings, setSetting, units } from "../state.js";
import { run, report } from "../engine.js";
import { formDialog } from "./common.js";
import { download, fromB64, saveWhere, writeAgent, saveOnDevice, textBytes, joinPath } from "../files.js";
import { MENUS } from "../menudef.js";
import { scopeOf, openGroups } from "../scope.js";

const P = (name, label, kind, def, o = {}) => ({ name, label, kind, default: def, min: null, max: null, step: null, choices: null, group: "", help: "", visible: null, decimals: 3, advanced: false, ...o });

const presetPicker = (onPick) => {
  const sel = h("select", { class: "presets" }, h("option", { value: "" }, "Preset sheet sizes..."),
    (app.schema.new_project.presets || []).map(([n, w, hh]) => h("option", { value: `${w}x${hh}` }, n)));
  sel.addEventListener("change", () => { if (sel.value) { const [w, hh] = sel.value.split("x").map(Number); onPick(w, hh); sel.value = ""; } });
  return sel;
};

// ------------------------------------------------------------------------------------------ New
export async function newProjectDialog() {
  const tpls = app.schema.new_project.templates || [];
  const base = [P("name", "Name", "str", "Untitled"), P("width", "Width (X)", "length", 1200, { min: 1 }), P("height", "Height (Y)", "length", 800, { min: 1 }), P("thickness", "Thickness", "length", store.get("iyicam.thickness", 18), { min: 0.01 })];
  if (tpls.length > 1) base.unshift(P("template", "Start from", "choice", "", { choices: tpls.map(t => [t.id, t.label]) }));
  let form;
  const picker = presetPicker((w, hh) => form.set({ width: w, height: hh }));
  const v = await formDialog({ title: "New project", params: base, width: 420, extra: picker, ok: "OK", remember: "new", afterBuild: f => { form = f; } });
  if (!v) return null;
  store.set("iyicam.thickness", v.thickness);
  return { width: v.width, height: v.height, thickness: v.thickness, name: v.name, template: v.template || "", values: {} };
}

// ------------------------------------------------------------------------------------------ Material Setup
export async function materialSetup(doc) {
  const m = doc.project.material, s = doc.sheet;
  const origins = app.schema.origins.map(o => [o, o.replace(/_/g, " ").replace(/\b\w/g, c => c.toUpperCase())]);
  const params = [
    P("width", "Sheet width (X)", "length", s.width, { min: 1, group: "Sheet" }),
    P("height", "Sheet height (Y)", "length", s.height, { min: 1, group: "Sheet", visible: { rotary: false } }),
    P("thickness", "Thickness of this sheet", "length", s.thickness != null ? s.thickness : m.thickness, { min: 0.01, group: "Sheet", visible: { rotary: false }, help: "Every sheet has its own thickness (Sheets panel: Properties for the others)" }),
    P("z_zero", "Z zero", "choice", m.z_zero, { choices: [["top", "Top of the material"], ["bottom", "Bottom (machine bed / rotary axis)"]], group: "Material" }),
    P("xy_origin", "XY origin", "choice", m.xy_origin, { choices: origins, group: "Material" }),
    P("safe_z", "Safe Z (above surface)", "length", m.safe_z, { min: 0.1, group: "Heights", help: "The height above the material surface the tool retracts to and travels at between cuts. A toolpath can have a safe Z of its own (on its Settings tab)." }),
    P("clearance", "Rapid-down clearance", "length", m.clearance, { min: 0, group: "Heights", help: "The tool rapids down to this height above the material and then plunges" }),
    P("home_x", "Home X", "length", m.home[0], { group: "Home position" }),
    P("home_y", "Home Y", "length", m.home[1], { group: "Home position" }),
    P("home_z", "Home Z (above surface)", "length", m.home[2], { group: "Home position" }),
    P("rapid_rate", "Machine rapid rate", "feed", m.rapid_rate, { min: 1, group: "Machine", help: "Only used for machining time estimates" }),
    P("corner_mode", "Corners", "choice", m.corner_mode || "", { choices: [["", "As the machine is set (the estimate slows down for corners)"], ["blend", "Straight through without slowing (G64, like ArtCAM)"], ["exact", "A stop at every corner (G61)"]], group: "Machine" }),
    P("z_rapid_rate", "Rapid rate of Z alone", "feed", m.z_rapid_rate, { min: 0, group: "Machine", help: "Time estimates: Z is often slower than X and Y (0: the same)" }),
    P("accel", "Acceleration (mm/s²)", "float", m.accel, { min: 1, max: 100000, decimals: 0, group: "Machine", help: "Time estimates: how fast the machine speeds up and slows down." }),
    P("corner_dev", "Corner deviation", "length", m.corner_dev, { min: 0.0001, group: "Machine" }),
    P("time_scale", "Time estimate correction", "float", m.time_scale, { min: 0.05, max: 20, decimals: 3, group: "Machine" }),
    P("rotary", "Wrap around a rotary axis", "bool", m.rotary, { group: "Rotary", help: "The sheet is the unrolled cylinder: X along the axis, Y around it (written as A)" }),
    P("rotary_diameter", "Cylinder diameter", "length", m.rotary_diameter, { min: 1, group: "Rotary", visible: { rotary: true } }),
  ];
  let form;
  const cal = h("button", { type: "button", class: "btn wide", title: "Type how long the calculated toolpaths of this sheet really took: the acceleration of the machine is found from it", onclick: async () => {
    const t = await promptBox("How long did the toolpaths of this sheet really take? (h:mm:ss or m:ss; a plain number is minutes)", "", "Calibrate");
    if (!t || !t.trim()) return;
    const parts = t.trim().split(":").map(Number);
    if (parts.some(Number.isNaN)) { alertBox(`'${t}' is not a time.`); return; }
    const seconds = parts.length === 1 ? parts[0] * 60 : parts.length === 2 ? parts[0] * 60 + parts[1] : parts[0] * 3600 + parts[1] * 60 + parts[2];
    try {
      const v = form.values();
      const proj = deepCopy(doc.project);
      Object.assign(proj.material, { rapid_rate: v.rapid_rate, z_rapid_rate: v.z_rapid_rate, accel: v.accel, corner_dev: v.corner_dev, time_scale: 1 });
      const r = await run("calibrate_time", { project: proj, seconds, sheet: doc.sheetId }, { label: "Calibrating" });
      form.set({ accel: r.accel, time_scale: r.time_scale });
      alertBox(`Acceleration ${r.accel} mm/s²` + (Math.abs(r.time_scale - 1) > 1e-3 ? `, correction x${r.time_scale}` : "") + `.\nThe estimate was ${fmtTime(r.estimate_before)}, now ${fmtTime(r.estimate_after)}. Press OK to keep it.`, "Calibrate");
    } catch (e) { report(e, "Calibrate"); }
  } }, "Calibrate the time estimate from a real time...");
  const v = await formDialog({ title: "Material Setup", params, width: 440, footer: cal, ok: "OK", afterBuild: f => { form = f; },
    buttons: [], validate: x => (x.clearance > x.safe_z + 1e-9 ? "The rapid-down clearance cannot be higher than the safe Z: the tool would rapid down from a height it never travels at." : "") });
  if (!v) return false;
  doc.edit("Material setup", p => {
    const mm = p.material;
    Object.assign(mm, { z_zero: v.z_zero, xy_origin: v.xy_origin, safe_z: v.safe_z, clearance: v.clearance, rapid_rate: v.rapid_rate, z_rapid_rate: v.z_rapid_rate,
      accel: v.accel, corner_dev: v.corner_dev, time_scale: v.time_scale, home: [v.home_x, v.home_y, v.home_z], rotary: v.rotary, rotary_diameter: v.rotary_diameter });
    if (v.corner_mode || "corner_mode" in mm) mm.corner_mode = v.corner_mode;
    const sh = p.sheets.find(x => x.id === doc.sheetId);
    sh.width = v.width; sh.height = v.height; sh.thickness = v.thickness;          // (this sheet's: the others keep theirs)
    if (v.rotary) { mm.thickness = v.rotary_diameter / 2; for (const x of p.sheets) { x.height = Math.PI * v.rotary_diameter; x.thickness = mm.thickness; } }
  });
  return true;
}
/** Material Setup with the calibration button (a form dialog cannot hold it above its buttons: it is added after the fields) */
export async function materialSetupWithCalibrate(doc) { return materialSetup(doc); }

// ------------------------------------------------------------------------------------------ Sheet properties
export async function sheetDialog(doc, sheet) {
  const params = [P("name", "Name", "str", sheet.name), P("group", "Group", "str", sheet.group || "", { help: "Sheets of one group are nested onto together and can have toolpaths of their own" }),
    P("width", "Width (X)", "length", sheet.width, { min: 1 }), P("height", "Height (Y)", "length", sheet.height, { min: 1 }),
    P("thickness", "Thickness", "length", sheet.thickness ?? doc.project.material.thickness, { min: 0.01, help: "Every sheet has its own thickness: its toolpaths cut through this" })];
  let form;
  const picker = presetPicker((w, hh) => form.set({ width: w, height: hh }));
  const v = await formDialog({ title: "Sheet", params, width: 400, extra: picker, afterBuild: f => { form = f; } });
  if (!v) return false;
  doc.edit("Sheet properties", p => {
    const s = p.sheets.find(x => x.id === sheet.id);
    s.name = v.name; s.width = v.width; s.height = v.height; s.group = (v.group || "").trim();
    s.thickness = v.thickness;
  });
  return true;
}

// ------------------------------------------------------------------------------------------ Nesting
const ROT = { "0": { rotation: 0 }, "180": { rotation: 180 }, "90": { rotation: 90 }, "45": { rotation: 45 }, "15": { rotation: 15 } };
const DIRECTIONS = { along_x: ["bottom", true], along_y: ["left", true], auto: ["bottom", false], compact: ["compact", true], diag: ["diag", true] };

export async function nestingDialog(doc) {
  const sheet = doc.sheet, saved = (doc.project.metadata && doc.project.metadata.nesting) || {};
  const toolD = Math.max(6, ...doc.project.toolpaths.filter(t => t.op === "profile").map(t => t.tool.diameter));
  const groups = scopeOf(doc.sheet) ? [scopeOf(doc.sheet)] : openGroups(doc.project);   // (an isolated group nests on its own)
  const params = [
    P("width", "Reserve / new sheet width", "length", sheet.width, { min: 1, group: "Sheets", help: "The size of the sheets started when the plates of the Plates tab are full (or when there are none)" }),
    P("height", "Reserve / new sheet height", "length", sheet.height, { min: 1, group: "Sheets" }),
    P("reserve_quantity", "Reserve sheets at most (0 = as many as needed)", "int", 0, { min: 0, max: 10000, group: "Sheets" }),
    P("plate_order", "Order of the plates", "choice", "leftovers", { choices: [["leftovers", "Leftovers first, then the plates small to large"], ["listed", "As listed in the Plates tab"], ["largest", "The plates large to small"], ["best", "Best order: the orders of the sizes are tried, the one using the least material is kept"]], group: "Sheets" }),
    P("margin", "Border gap", "length", 10, { min: 0, group: "Sheets" }),
    P("spacing", "Gap between parts", "length", toolD + 2, { min: 0, group: "Sheets", help: "Usually the cutter diameter plus a little clearance" }),
    P("min_leftover", "Leftover narrower than this is not used", "length", 150, { min: 0, group: "Sheets" }),
    P("target", "Place on", "choice", "current", { choices: [["current", "Current sheet first (around what stays on it), then the stock"], ["all", "All sheets first (around what stays on them), then the stock"], ...groups.map(g => [`group:${g}`, `The sheets of the group '${g}' first (around what stays on them), then the stock`]), ["new", "The stock and new sheets only"]], group: "Sheets" }),
    P("new_sheets", "Use reserve (new) sheets when the plates are full", "bool", true, { group: "Sheets" }),
    P("use_up", "Take the plates used out of the stock", "bool", true, { group: "Sheets" }),
    P("rotation", "Rotation", "choice", "90", { choices: [["0", "No rotation"], ["180", "0 / 180°"], ["90", "90° steps"], ["45", "45° steps"], ["15", "15° steps"], ["custom", "Steps of my own..."], ["list", "List of angles..."]], group: "Strategy" }),
    P("rotation_step", "Rotation step", "angle", 30, { min: 0.1, max: 360, group: "Strategy", visible: { rotation: "custom" } }),
    P("angles", "Angles (degrees)", "str", "0, 45, 90, 135", { group: "Strategy", visible: { rotation: "list" } }),
    P("tilt_step", "Step of limited rotation", "angle", 5, { min: 0.1, max: 90, group: "Strategy" }),
    P("arrange", "Arrangement", "choice", "free", { choices: [["free", "Free (the parts interlock, least material)"], ["rows", "Rows along X (lined up, a row at a time)"], ["columns", "Columns along Y (lined up, a column at a time)"]], group: "Strategy" }),
    P("corner", "Start in the corner", "choice", "bl", { choices: [["bl", "Bottom left"], ["br", "Bottom right"], ["tl", "Top left"], ["tr", "Top right"]], group: "Strategy", visible: { arrange: ["rows", "columns"] } }),
    P("direction", "Direction", "choice", "along_x", { choices: [["along_x", "Along X (fill the width, the leftover is a strip on top)"], ["along_y", "Along Y (fill the height, the leftover is on the right)"], ["auto", "Automatic (try every direction, keep the least area)"], ["compact", "Smallest area"], ["diag", "Lower left corner (diagonal)"]], group: "Strategy", visible: { arrange: "free" } }),
    P("iterations", "Optimisation attempts", "int", 30, { min: 1, max: 200, group: "Strategy" }),
    P("time_limit", "Time limit of the search (seconds)", "int", 120, { min: 0, max: 3600, group: "Strategy" }),
    P("max_points", "Outline points per part", "int", 300, { min: 0, max: 5000, group: "Strategy", advanced: true }),
    P("group_inner", "Inner pieces (göbek) stay with their part", "bool", false, { group: "Parts" }),
    P("fill_holes", "Place smaller parts in the holes of parts", "bool", false, { group: "Parts" }),
    P("squeeze", "Squeeze parts in", "choice", "off", { choices: [["off", "Off"], ["auto", "Squeeze in automatically"]], group: "Squeezing in" }),
    P("squeeze_angle", "Tilt by up to", "angle", 5, { min: 0, max: 90, group: "Squeezing in", visible: { squeeze: "auto" } }),
    P("squeeze_step", "Tilt step", "angle", 1, { min: 0.1, max: 45, group: "Squeezing in", visible: { squeeze: "auto" } }),
    P("squeeze_margin", "Closer to the edge by up to", "length", 3, { min: 0, group: "Squeezing in", visible: { squeeze: "auto" } }),
  ];
  const form = new ParamForm(params, { ...store.get("iyicam.nest", {}), ...saved, width: sheet.width, height: sheet.height }, { collapse: true });
  const picker = presetPicker((w, hh) => form.set({ width: w, height: hh }));
  // plates
  const plates = store.get("iyicam.nestplates", []);
  const plateBox = h("div", { class: "plates" });
  const drawPlates = () => fill(plateBox, h("table", { class: "kv" }, h("tr", null, h("th", null, "Width"), h("th", null, "Height"), h("th", null, "Quantity"), h("th")),
    plates.map((p, i) => h("tr", null, h("td", null, units.fmt(p.width)), h("td", null, units.fmt(p.height)), h("td", null, String(p.quantity)),
      h("td", null, h("button", { class: "btn small", onclick: () => { plates.splice(i, 1); drawPlates(); } }, "−"))))),
    plates.length ? null : h("p", { class: "muted" }, "No plates: the sheets of the reserve size are used."));
  drawPlates();
  const pf = new ParamForm([P("width", "Width", "length", 2440, { min: 1 }), P("height", "Height", "length", 1220, { min: 1 }), P("quantity", "Quantity", "int", 1, { min: 1 })], {}, {});
  const addPlate = h("button", { class: "btn", onclick: () => { const v = pf.values(); plates.push({ width: v.width, height: v.height, quantity: v.quantity }); drawPlates(); } }, "Add plate");
  const stock = doc.project.stock || [];
  const qty = new ParamForm([P("quantity", "Copies of each part", "int", 1, { min: 1, max: 10000, group: "Parts" })], {}, {});
  const parts = h("div", null, qty.el, h("p", { class: "muted" }, `${doc.selection.length || sheet.vectors.length} vector(s) are nested${doc.selection.length ? " (the selection)" : " (the whole sheet)"}. Rotation rules by layer or by part are not on the web yet.`));
  const tb = tabs([["Settings", h("div", { class: "scrollform tall" }, picker, form.el)],
    ["Plates", h("div", { class: "scrollform tall" }, plateBox, h("div", { class: "fields" }, pf.el), addPlate, stock.length ? h("p", { class: "muted" }, `The stock of the project: ${stock.length} item(s) (used as the desktop does).`) : null)],
    ["Parts and rotation", h("div", { class: "scrollform tall" }, parts)]]);
  const dlg = dialog({ title: "Nesting", width: 560, className: "nestdlg", body: tb.el, buttons: [{ label: "Cancel", value: null }, { label: "Nest", primary: true, value: "ok" }] });
  if ((await dlg.done) !== "ok") return null;
  const v = form.values();
  store.set("iyicam.nest", (({ width, height, ...rest }) => rest)(v)); store.set("iyicam.nestplates", plates);
  const rot = ROT[v.rotation] || (v.rotation === "custom" ? { rotation: v.rotation_step } : {});
  const [gravity, gonly] = DIRECTIONS[v.direction] || DIRECTIONS.along_x;
  const place = v.target.startsWith("group:") ? { place_on: "group", group: v.target.slice(6) } : { place_on: v.target };
  const settingsOut = {
    margin: v.margin, spacing: v.spacing, min_leftover: v.min_leftover, ...rot, ...(v.rotation === "list" ? { angles: v.angles } : {}),
    tilt_step: v.tilt_step, arrange: v.arrange, corner: v.corner, gravity, gravity_only: v.arrange === "free" ? gonly : false,
    iterations: v.iterations, time_limit: v.time_limit, max_points: v.max_points, group_inner: v.group_inner, fill_holes: v.fill_holes,
    ...place, plate_order: plates.length && v.plate_order === "leftovers" ? "listed" : v.plate_order, quantity: qty.values().quantity,
    no_new_sheets: !v.new_sheets, keep_stock: !v.use_up,
    reserve: v.new_sheets ? { width: v.width, height: v.height, quantity: v.reserve_quantity } : false,
    plates: plates.map(p => ({ width: p.width, height: p.height, quantity: p.quantity })),
    ...(v.squeeze === "auto" ? { squeeze_angle: v.squeeze_angle, squeeze_step: v.squeeze_step, squeeze_margin: v.squeeze_margin, apply_squeeze: true } : {}),
  };
  return { settings: settingsOut, objects: (doc.selection.length ? doc.selection : sheet.vectors.map(x => x.id)) };
}

export async function nestReport(r) {
  const body = h("div", { class: "report" },
    h("p", null, `${r.placed} part(s) placed` + (r.unplaced ? `, ${r.unplaced} did not fit.` : ".")),
    h("table", { class: "kv" }, h("tr", null, h("th", null, "Sheet"), h("th", null, "Size"), h("th", null, "Used")),
      (r.sheets || []).map(s => h("tr", null, h("td", null, s.name + (s.reserve ? " (new)" : "")), h("td", null, `${units.fmt(s.width)} × ${units.fmt(s.height)}`), h("td", null, `${(s.utilization * 100).toFixed(1)} %`)))),
    (r.squeezed && r.squeezed.length) ? h("pre", null, r.squeezed.join("\n")) : null,
    r.suggestion ? h("p", { class: "dlg-warn" }, `Squeezing parts in would place ${r.suggestion.unplaced ? "all but " + r.suggestion.unplaced : "all"} of them.`) : null);
  await dialog({ title: "Nesting", body, width: 460, buttons: [{ label: "OK", primary: true, value: true }] }).done;
}

// ------------------------------------------------------------------------------------------ Preferences
export async function preferencesDialog() {
  const s = settings;
  const params = [
    P("units", "Display units", "choice", s.units, { choices: [["mm", "Millimetres"], ["inch", "Inches"]], group: "Settings" }),
    P("feedUnit", "Feed rates", "choice", s.feedUnit, { choices: [["min", "Per minute (mm/min)"], ["sec", "Per second (mm/s)"]], group: "Settings", help: "How feed and plunge rates are shown and typed; the G-code is always per minute (F)" }),
    P("appearance", "Appearance", "choice", s.appearance, { choices: [["system", "Follow the system"], ["light", "Light"], ["dark", "Dark"]], group: "Settings" }),
    P("grid", "Grid spacing (mm)", "float", s.grid, { min: 0.01, group: "Settings" }),
    P("snapGrid", "Snap to grid", "bool", s.snapGrid, { group: "Settings" }),
    P("snapVectors", "Snap to vectors (nodes, middles, centres, crossings, sheet)", "bool", s.snapVectors, { group: "Settings" }),
    P("snapLevel", "Snap level with a node (same X or Y)", "bool", s.snapLevel, { group: "Settings" }),
    P("snapEdges", "Snap to any point on a vector", "bool", s.snapEdges, { group: "Settings" }),
    P("snapReach", "Snap reach (pixels)", "float", s.snapReach, { min: 2, max: 60, group: "Settings", help: "How near the pointer has to be for something to catch it; the same at every zoom" }),
    P("resizeHandles", "Resize handles round a selection (mouse)", "bool", s.resizeHandles, { group: "Settings" }),
    P("arrowStep", "Arrow key step", "choice", s.arrowStep, { choices: [["auto", "Automatic (follows the zoom: far moves more, close finer)"], ["0.1", "0.1 mm"], ["1", "1 mm"], ["10", "10 mm"], ["custom", "Custom"]], group: "Settings" }),
    P("autoRecalc", "Recalculate toolpaths automatically after every edit", "bool", s.autoRecalc, { group: "Settings" }),
    P("calcOnOpen", "Calculate the toolpaths of a project when it is opened", "bool", s.calcOnOpen, { group: "Settings" }),
    P("askTool", "A new toolpath asks for its tool from the tool database first", "bool", s.askTool, { group: "Settings" }),
    P("showDirections", "Show direction arrows", "bool", s.showDirections, { group: "Settings" }),
    P("simResolution", "Simulation resolution (mm, 0 = auto)", "float", s.simResolution, { min: 0, group: "Settings" }),
  ];
  // the defaults of new toolpaths are the engine's (the user folder: the desktop has the same ones)
  let mine = null;
  try { mine = await app.engine.call("tp_defaults", {}); } catch { mine = null; }
  if (mine) for (const [k, label] of mine.keys) params.push(P(k, label, "length", mine.values[k], { min: 0, max: 100, group: "New toolpaths", help: "For new toolpaths; 0: the cut stops at the bottom of the material" }));
  const v = await formDialog({ title: "Preferences", params, width: 560, ok: "OK" });
  if (!v) return;
  if (mine) {
    const keep = {};
    for (const [k] of mine.keys) { keep[k] = v[k]; delete v[k]; }
    if (mine.keys.some(([k]) => keep[k] !== mine.values[k])) {
      await app.engine.call("tp_defaults", { values: keep });
      app.schema = await app.engine.call("schema", {});        // (the forms of new toolpaths take the new defaults)
    }
  }
  for (const [k, val] of Object.entries(v)) if (settings[k] !== val) setSetting(k, val);
}

// ------------------------------------------------------------------------------------------ writing files out
/** files that an operation made (`{name, mime, data_b64}` or `{name, text}`) go to this device or to the agent */
export async function deliverFiles(files, title = "Save") {
  if (!files.length) return false;
  const bytesOf = f => (f.data_b64 ? fromB64(f.data_b64) : textBytes(f.text || ""));
  const where = await saveWhere({ title, name: files[0].name, ext: files[0].name.split(".").pop(), folderOnly: files.length > 1 });
  if (!where) return false;
  if (where.where === "device") {                     // (a download: the browser puts the files in its downloads folder)
    for (const f of files) { download(f.name, bytesOf(f), f.mime); if (files.length > 1) await new Promise(r => setTimeout(r, 250)); }
    return true;
  }
  if (files.length === 1 && where.path && !/[\\/]$/.test(where.path) && where.name) { await writeAgent(where.path, bytesOf(files[0])); toast(`Saved ${where.path}`); return true; }
  for (const f of files) await writeAgent(joinPath(where.dir || where.path, f.name), bytesOf(f));
  toast(`Saved ${files.length} file(s) in ${where.dir || where.path}`);
  return true;
}

// ------------------------------------------------------------------------------------------ Export vectors
export async function exportVectors(doc) {
  const exporters = app.schema.exporters.filter(e => e.extension !== "icam");
  const fmt = h("select", null, exporters.map(e => h("option", { value: e.extension }, `${e.label} (.${e.extension})`)));
  fmt.value = store.get("iyicam.exportfmt", exporters[0] && exporters[0].extension);
  const holder = h("div");
  let form;
  const build = () => { const e = exporters.find(x => x.extension === fmt.value); form = new ParamForm(e ? e.params : [], {}, {}); fill(holder, form.el); };
  fmt.addEventListener("change", build); build();
  const selOnly = h("input", { type: "checkbox", checked: doc.selection.length > 0, disabled: !doc.selection.length });
  const allSheets = h("input", { type: "checkbox", disabled: doc.project.sheets.length < 2 });
  const dlg = dialog({ title: "Export Vectors", width: 420, body: h("div", null, h("label", { class: "row" }, h("span", { class: "lab" }, "Format"), fmt), holder,
    h("label", { class: "row check" }, selOnly, h("span", null, "Selected vectors only")), doc.project.sheets.length > 1 ? h("label", { class: "row check" }, allSheets, h("span", null, "Every sheet (one file each)")) : null),
    buttons: [{ label: "Cancel", value: null }, { label: "Export", primary: true, value: "ok" }] });
  if ((await dlg.done) !== "ok") return;
  store.set("iyicam.exportfmt", fmt.value);
  try {
    const r = await run("export", { project: doc.project, format: fmt.value, sheet: allSheets.checked ? "all" : doc.sheetId, selection: selOnly.checked ? doc.selection : undefined, options: form.values(), file_name: doc.name.replace(/\.[^.]+$/, "") }, { label: "Exporting" });
    await deliverFiles(r.files, "Export Vectors");
  } catch (e) { report(e, "Export"); }
}

// ------------------------------------------------------------------------------------------ Save G-code
export async function gcodeDialog(doc) {
  const posts = app.schema.posts;
  if (!posts.length) { alertBox("There is no post-processor."); return null; }
  const last = store.get("iyicam.post", posts[0].id);
  const params = [
    P("post", "Post-processor", "choice", posts.some(p => p.id === last) ? last : posts[0].id, { choices: posts.map(p => [p.id, `${p.name} (.${p.extension})`]), help: "" }),
    P("file_name", "File name", "str", doc.name.replace(/\.[^.]+$/, "")),
    P("program_number", "Program number", "int", 1000, { min: 0, max: 999999 }),
    P("all_sheets", scopeOf(doc.sheet) ? `Every sheet of the isolated group ${scopeOf(doc.sheet)} (one file each)` : "Every sheet (one file each; isolated groups are saved from their own sheets)", "bool", true, { visible: null }),
    P("which", "Toolpaths", "choice", doc.toolpathSelection && doc.toolpathSelection.length ? "selected" : "on", { choices: [
      ["on", "Every toolpath switched on"], ["sheet", `The toolpaths of this sheet (${doc.sheet ? doc.sheet.name : ""})`],
      ...(doc.toolpathSelection && doc.toolpathSelection.length ? [["selected", `The toolpaths selected (${doc.toolpathSelection.length})`]] : [])] }),
    P("per_toolpath", "One file per toolpath (all in one folder)", "bool", store.get("iyicam.perToolpath", false), { help: "Every toolpath in a G-code file of its own, named after the toolpath" }),
    P("recalculate", "Calculate the toolpaths again first", "bool", false, { help: "Off: the toolpaths as they were calculated last are written" }),
  ];
  const info = h("p", { class: "muted post-desc" });
  let form;
  const show = () => { const p = posts.find(x => x.id === form.values().post); fill(info, p ? p.description : ""); };
  const v = await formDialog({ title: "Save G-code", params, width: 440, ok: "Save", extra: info, afterBuild: f => { form = f; f.opts.onChange = show; setTimeout(show, 0); } });
  if (!v) return null;
  store.set("iyicam.perToolpath", !!v.per_toolpath);
  store.set("iyicam.post", v.post);
  return v;
}

// ------------------------------------------------------------------------------------------ shortcuts, about
export function shortcutsDialog() {
  const rows = [];
  const walk = (items, path) => { for (const it of items) { if (it === "-") continue; if (it.menu) walk(it.menu, [...path, it.text]); else if (it.sc) rows.push([path.join(" ▸ "), it.text.replace(/\.\.\.$/, ""), it.sc]); } };
  for (const m of MENUS) walk(m.menu, [m.text]);
  rows.push(["Canvas", "Zoom at the cursor", "Wheel"], ["Canvas", "Pan", "Middle drag, or Space + drag, or two fingers"], ["Canvas", "Nudge the selection", "Arrow keys (Shift ×10, Ctrl ÷10)"],
    ["Canvas", "Delete", "Del"], ["Canvas", "Cancel the tool", "Esc"]);
  dialog({ title: "Keyboard Shortcuts", width: 560, body: h("div", { class: "scrollform tall" }, h("table", { class: "kv shortcuts" }, rows.map(([a, b, c]) => h("tr", null, h("td", { class: "muted" }, a), h("td", null, b), h("td", { class: "mono" }, c))))) });
}
export function aboutDialog() {
  dialog({ title: "About iyiCAM", width: 380, body: h("div", { class: "about" }, h("h2", null, "iyiCAM"), h("p", null, `Version ${app.schema ? app.schema.iyicam : ""} (web)`),
    h("p", { class: "muted" }, "2D / 2.5D CAM for CNC routers. The toolpaths are calculated in this browser, by the same program as the desktop's."),
    h("p", { class: "muted" }, `Engine ${app.engine && app.engine.version ? app.engine.version : ""}`)) });
}

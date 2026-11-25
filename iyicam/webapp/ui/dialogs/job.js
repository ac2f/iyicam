/* iyiCAM web: the windows of the job features: job report, G-code backplot, quit, keyboard shortcuts, G-code words, plugin manager,
 * toolpath packages and templates, stock of plates, post-processors.  The work is done by the engine (ops_job.py, ops.py): these
 * are forms that send JSON and show what comes back. */
import { h, fill, store, toast, deepCopy, uid } from "../dom.js";
import { dialog, tabs, alertBox, confirmBox, promptBox, askBox } from "../dialog.js";
import { ParamForm } from "../forms.js";
import { app, settings, units, emit } from "../state.js";
import { run, report } from "../engine.js";
import { formDialog } from "./common.js";
import { deliverFiles } from "./misc.js";
import { toolDatabase, describeTool } from "./tools.js";
import { openFile, pickDeviceFiles, textBytes, toB64, download } from "../files.js";
import { MENUS } from "../menudef.js";
import * as A from "../actions.js";

const P = (name, label, kind, def, o = {}) => ({ name, label, kind, default: def, min: null, max: null, step: null, choices: null, group: "", help: "", visible: null, decimals: 3, advanced: false, ...o });
const btn = (label, fn, o = {}) => h("button", { type: "button", class: "btn" + (o.small ? " small" : "") + (o.primary ? " primary" : ""), title: o.title || "", disabled: o.disabled, onclick: fn }, label);
const ops = () => (app.schema.operations || []);
const opLabel = id => { const o = ops().find(x => x.id === id); return o ? o.label : id; };

// ------------------------------------------------------------------------------------------ Job report
export async function jobReport(d) {
  if (!d.project.toolpaths.some(t => t.enabled)) { await alertBox("There are no toolpaths to report. Make a toolpath first (Toolpaths ▸ New Toolpath).", "Job Report"); return; }
  try {
    const r = await run("job_report", { project: d.project, inch: units.inch }, { label: "Job report", cancel: true });
    const open = () => { const u = URL.createObjectURL(new Blob([r.html], { type: "text/html" })); window.open(u, "_blank", "noopener"); setTimeout(() => URL.revokeObjectURL(u), 60000); };
    const text = h("div", { class: "reportpreview" });
    const doc = new DOMParser().parseFromString(r.html, "text/html");       // (shown as text and tables only: no scripts run)
    for (const el of doc.querySelectorAll("script, style, link")) el.remove();
    text.innerHTML = doc.body.innerHTML;
    await dialog({ title: "Job Report", width: 760, body: h("div", { class: "scrollform tall" }, text),
      buttons: [{ label: "Open in a tab", keep: true, onclick: open }, { label: "Save…", keep: true, onclick: () => deliverFiles([{ name: r.name, text: r.html, mime: "text/html" }], "Job report") },
        { label: "Close", primary: true, value: true }] }).done;
  } catch (e) { report(e, "Job report"); }
}

// ------------------------------------------------------------------------------------------ Backplot
const GCODE_EXTS = ["nc", "tap", "ngc", "gcode", "cnc", "txt", "u00", "eia"];
export async function openBackplot(d) {
  try {
    const f = await openFile({ title: "Open G-code", accept: GCODE_EXTS.map(e => "." + e).join(","), exts: GCODE_EXTS });
    if (!f) return;
    const text = new TextDecoder("latin1").decode(f.bytes);
    const r = await run("backplot", { project: d.project, sheet: d.sheetId, text }, { label: "Reading " + f.name });
    d.backplot = { name: f.name, sheet: r.sheet, preview: r.preview, tools: r.tools, stats: r.stats, text };
    emit("results", d); app.ui.canvas.redraw();
    A.status(`${f.name}: ${r.tools.length} tool(s), cut length ${units.fmt(r.stats.feed_length)}, estimated ${fmtClock(r.stats.time_s)}`);
  } catch (e) { report(e, "Open G-code"); }
}
export const fmtClock = s => { s = Math.round(s || 0); const hh = Math.floor(s / 3600), m = Math.floor((s % 3600) / 60), ss = s % 60, p = n => String(n).padStart(2, "0"); return hh ? `${hh}:${p(m)}:${p(ss)}` : `${m}:${p(ss)}`; };

// ------------------------------------------------------------------------------------------ Quit
/** ask to save every tab with changes, then sign out (the page cannot close itself when it was not opened by a script) */
export async function quit() {
  for (const d of [...app.docs]) {
    if (!d.dirty) continue;
    app.cur = d; emit("docs"); emit("project", d);
    const a = await askBox(`Save the changes to "${d.name}"?`, { yes: "Save", no: "Discard", title: "Quit" });
    if (a === undefined) return false;
    if (a === "yes" && !(await A.saveProject(d))) return false;
    d.dirty = false;
  }
  window.onbeforeunload = null;
  try { await fetch("/api/logout", { method: "POST", credentials: "same-origin" }); } catch { /* a static page: nothing to sign out of */ }
  document.body.classList.add("quit");
  fill(document.body, h("div", { class: "login" }, h("div", { class: "quitbox" }, h("h1", null, "iyiCAM"), h("p", null, "You are signed out. You can close this tab."),
    h("button", { class: "btn primary", type: "button", onclick: () => location.reload() }, "Start again"))));
  try { window.close(); } catch { /* the browser decides */ }
  return true;
}

// ------------------------------------------------------------------------------------------ Keyboard shortcuts
export function shortcutEditor() {
  const rows = [];
  const walk = (items, path) => { for (const it of items) { if (it === "-") continue; if (it.menu) { if (it.id !== "file.open_recent" && it.id !== "window") walk(it.menu, [...path, it.text.replace(/\.\.\.$/, "")]); } else if (it.id && !it.id.endsWith(".none")) rows.push({ it, path: path.join(" ▸ "), name: it.text.replace(/\.\.\.$/, "") }); } };
  for (const m of MENUS) if (m.id !== "window") walk(m.menu, [m.text]);
  const keys = {};
  for (const r of rows) keys[r.it.id] = [...A.keysOf(r.it)];
  const eq = (a, b) => a.length === b.length && a.every((k, i) => k === b[i]);
  const filter = h("input", { type: "text", placeholder: "Search a function or a key (Ctrl+D) ...", oninput: () => draw() });
  const list = h("div", { class: "sclist listbox" });
  const note = h("div", { class: "muted" }, "Select a function, then press the keys.");
  const capture = h("button", { type: "button", class: "btn sccapture", title: "Click here and press the keys (Esc and Enter keep their meaning)", disabled: true }, "Press keys…");
  let cur = null;
  const text = id => keys[id].join(", ") || "";
  function draw() {
    const f = filter.value.trim().toLowerCase();
    let last = null;
    fill(list, rows.filter(r => !f || (r.path + " " + r.name).toLowerCase().includes(f) || keys[r.it.id].some(k => k.toLowerCase().includes(f))).map(r => {
      const head = r.path !== last ? h("div", { class: "schead" }, r.path) : null;
      last = r.path;
      const changed = !eq(keys[r.it.id], A.defaultKeys(r.it));
      return [head, h("div", { class: "scrow" + (cur === r.it.id ? " on" : ""), dataset: { id: r.it.id }, onclick: () => pick(r.it.id) },
        h("span", { class: "n" }, r.name), h("span", { class: "k mono" + (changed ? " changed" : "") }, text(r.it.id)))];
    }));
  }
  function pick(id) {
    cur = id; draw();
    const r = rows.find(x => x.it.id === id);
    capture.disabled = false; capture.textContent = text(id) || "(none)  — click and press the keys";
    fill(note, `${r.path} ▸ ${r.name} — the program's own: ${A.defaultKeys(r.it).join(", ") || "none"}`);
  }
  async function setKeys(id, ks) {
    for (const k of ks) for (const other of Object.keys(keys)) {
      if (other === id || !keys[other].includes(k)) continue;
      const o = rows.find(x => x.it.id === other);
      if (!(await confirmBox(`${k} is the shortcut of\n${o.path} ▸ ${o.name}.\n\nTake it from there?`, { title: "Keyboard Shortcuts", yes: "Take it" }))) { pick(id); return; }
      keys[other] = keys[other].filter(x => x !== k);
    }
    keys[id] = ks; pick(id);
  }
  capture.addEventListener("keydown", e => {
    if (!cur || ["Escape", "Enter", "Tab"].includes(e.key) || ["Control", "Shift", "Alt", "Meta"].includes(e.key)) return;
    e.preventDefault(); e.stopPropagation();
    if (e.key === "Backspace" && !e.ctrlKey && !e.altKey && !e.shiftKey) { setKeys(cur, []); return; }
    setKeys(cur, [A.comboOf(e)]);
  });
  const body = h("div", { class: "scedit" }, filter, list,
    h("div", { class: "row" }, h("span", { class: "lab small" }, "Shortcut"), capture,
      btn("None", () => cur && setKeys(cur, []), { small: true, title: "No shortcut for this function" }),
      btn("Default", () => { if (!cur) return; setKeys(cur, [...A.defaultKeys(rows.find(x => x.it.id === cur).it)]); }, { small: true, title: "The program's own shortcut" })),
    note);
  draw();
  const dlg = dialog({ title: "Keyboard Shortcuts", width: 640, body, buttons: [
    { label: "Reset all", keep: true, onclick: () => { for (const r of rows) keys[r.it.id] = [...A.defaultKeys(r.it)]; if (cur) pick(cur); else draw(); } },
    { label: "Cancel", value: null },
    { label: "OK", primary: true, value: "ok", onclick: () => {
      const o = {};
      for (const r of rows) if (!eq(keys[r.it.id], A.defaultKeys(r.it))) o[r.it.id] = keys[r.it.id];
      A.setShortcutOverrides(o);
    } }] });
  return dlg.done;
}

// ------------------------------------------------------------------------------------------ G-code words
export async function gcodeWords() {
  let posts;
  try { posts = await app.engine.call("posts", {}); } catch (e) { report(e, "G-code words"); return; }
  let lang = store.get("iyicam.gcodeLang", "en");
  const list = h("div", { class: "listbox glossary" });
  const filter = h("input", { type: "text", placeholder: "Search a word (I, G2, M3 ...)", oninput: () => draw() });
  const sel = h("select", { onchange: () => { lang = sel.value; store.set("iyicam.gcodeLang", lang); draw(); } }, h("option", { value: "en" }, "English"), h("option", { value: "tr" }, "Türkçe"));
  sel.value = lang;
  const out = h("div", { class: "explained" });
  const line = h("input", { type: "text", class: "mono", placeholder: "G2 X10 Y0 I5 J0 F800", value: "G2 X10 Y0 I5 J0 F800" });
  const draw = () => {
    const f = filter.value.trim().toLowerCase();
    fill(list, (posts.glossary[lang] || []).filter(g => !f || g.word.toLowerCase().includes(f) || g.text.toLowerCase().includes(f)).map(g => h("div", { class: "gw" }, h("b", { class: "mono" }, g.word), h("span", null, g.text))));
  };
  async function explain() {
    try {
      const r = await app.engine.call("gcode_explain", { lines: [line.value], index: 0, lang });
      fill(out, h("p", null, r.summary || ""), h("table", { class: "kv" }, (r.words || []).map(w => h("tr", null, h("td", { class: "mono" }, w.word), h("td", null, w.text)))));
    } catch (e) { fill(out, h("p", { class: "dlg-warn" }, e.message || String(e))); }
  }
  line.addEventListener("keydown", e => { if (e.key === "Enter") { e.preventDefault(); e.stopPropagation(); explain(); } });
  draw(); explain();
  await dialog({ title: "G-code Words (I J K ...)", width: 680, body: h("div", null,
    h("div", { class: "row" }, filter, sel), list,
    h("h4", null, "Try a line"), h("div", { class: "row" }, line, btn("Explain", explain, { small: true })), out), buttons: [{ label: "Close", primary: true, value: true }] }).done;
}

// ------------------------------------------------------------------------------------------ Plugin manager
async function refreshSchema() {
  try { app.schema = await app.engine.call("schema", {}); emit("settings"); } catch (e) { console.error(e); }
}
export async function pluginManager() {
  let data;
  const call = async (op, payload, label) => { try { data = await run(op, payload, { label }); if (data.plugin !== undefined || op !== "plugins") await refreshSchema(); return true; } catch (e) { report(e, "Plugins"); return false; } };
  try { data = await app.engine.call("plugins", {}); } catch (e) { report(e, "Plugins"); return; }
  let cur = data.plugins[0] ? data.plugins[0].key : null;
  const listEl = h("div", { class: "listbox plist" }), detail = h("div", { class: "pdetail" });
  const provides = p => Object.entries(p.provides || {}).filter(([, v]) => v && v.length).map(([k, v]) => `${k}: ${v.join(", ")}`);
  function render() {
    fill(listEl, data.plugins.length ? data.plugins.map(p => h("div", { class: "prow" + (p.key === cur ? " on" : ""), onclick: () => { cur = p.key; render(); } },
      h("span", { class: "n" }, p.name || p.key), h("span", { class: "muted" }, p.version || ""), h("span", { class: "st " + (p.error ? "bad" : p.enabled ? "ok" : "off") }, p.error ? "error" : p.enabled ? "on" : "off")))
      : h("div", { class: "fb-empty" }, data.plugins_on ? "No plugins installed." : "Plugins are switched off on this engine."));
    const p = data.plugins.find(x => x.key === cur);
    if (!p) { fill(detail, h("p", { class: "muted" }, "Install a plugin (a .py file or a .zip) to add commands, toolpath types, importers or post-processors.")); return; }
    fill(detail, h("h3", null, p.name || p.key), h("p", { class: "muted" }, [p.version && "Version " + p.version, p.author && "by " + p.author, p.kind].filter(Boolean).join(" · ")),
      p.description ? h("p", null, p.description) : null, p.error ? h("p", { class: "dlg-warn" }, p.error) : null,
      provides(p).length ? h("ul", { class: "provides" }, provides(p).map(t => h("li", null, t))) : null,
      h("div", { class: "btnrow" },
        btn(p.enabled ? "Switch off" : "Switch on", async () => { if (await call("plugin_set", { key: p.key, action: p.enabled ? "disable" : "enable" }, "Plugins")) render(); }),
        btn("Reload", async () => { if (await call("plugin_set", { key: p.key, action: "reload" }, "Plugins")) render(); }),
        p.removable ? btn("Remove", async () => { if (await confirmBox(`Remove the plugin "${p.name || p.key}"?`, { yes: "Remove" }) && await call("plugin_set", { key: p.key, action: "remove" }, "Plugins")) { cur = data.plugins[0] ? data.plugins[0].key : null; render(); } }) : null));
  }
  async function install() {
    const f = (await pickDeviceFiles(".py,.zip"))[0];
    if (!f) return;
    if (await call("plugin_install", { name: f.name, data_b64: toB64(f.bytes) }, "Installing " + f.name)) { cur = data.installed || cur; toast(`Installed ${f.name}`); render(); }
  }
  render();
  await dialog({ title: "Plugin Manager", width: 700, body: h("div", { class: "pm" }, listEl, detail,
    data.folder ? h("p", { class: "muted pmfolder" }, "Plugins live in " + data.folder + " (kept in this browser)") : null),
  buttons: [data.install ? { label: "Install…", keep: true, onclick: install } : null, { label: "Close", primary: true, value: true }].filter(Boolean) }).done;
}

// ------------------------------------------------------------------------------------------ Python console
/** The console pane (under the drawing): one line at a time goes to the engine, which keeps the variables from line to line. */
export function consolePane() {
  const out = h("pre", { class: "conout mono" }), inp = h("input", { type: "text", class: "mono conin", placeholder: ">>> ", spellcheck: false, autocomplete: "off", autocapitalize: "off" });
  const el = h("div", { class: "consolepane", hidden: true }, h("div", { class: "contitle" }, h("span", null, "Python Console"), btn("Clear", () => { out.textContent = ""; started = false; start(); }, { small: true }),
    btn("×", () => api.hide(), { small: true, title: "Close" })), out, inp);
  const history = []; let hpos = 0, more = false, started = false, busy = false;
  const write = t => { if (!t) return; out.append(document.createTextNode(t)); out.scrollTop = out.scrollHeight; };
  async function start() {
    if (started) return;
    started = true;
    try { const r = await app.engine.call("console_reset", {}); write(r.banner + "\n"); } catch (e) { write(String(e.message || e) + "\n"); }
  }
  async function exec(line) {
    const d = app.cur;
    if (!d || busy) return;
    busy = true;
    write((more ? "... " : ">>> ") + line + "\n");
    try {
      const r = await app.engine.call("console_run", { project: d.project, sheet: d.sheetId, selection: d.selection, layer: d.layerId, line });
      write(r.output);
      more = r.more; inp.placeholder = more ? "... " : ">>> ";
      if (r.changed) { d.replace("Python console", r.project, r.selection); app.ui.canvas.redraw(); }
    } catch (e) { write((e.message || String(e)) + "\n"); more = false; }
    busy = false;
  }
  inp.addEventListener("keydown", e => {
    e.stopPropagation();
    if (e.key === "Enter") { const v = inp.value; inp.value = ""; if (v.trim()) history.push(v); hpos = history.length; exec(v); }
    else if (e.key === "ArrowUp" && history.length) { e.preventDefault(); hpos = Math.max(0, hpos - 1); inp.value = history[hpos]; }
    else if (e.key === "ArrowDown" && history.length) { e.preventDefault(); hpos = Math.min(history.length, hpos + 1); inp.value = history[hpos] || ""; }
    else if (e.key === "Escape") api.hide();
  });
  el.addEventListener("keyup", e => e.stopPropagation());
  const api = {
    el, get visible() { return !el.hidden; },
    show() { el.hidden = false; start(); setTimeout(() => { inp.focus(); app.ui.canvas.resize && app.ui.canvas.resize(); app.ui.canvas.redraw(); }, 30); },
    hide() { el.hidden = true; setTimeout(() => app.ui.canvas.redraw(), 30); },
    toggle() { el.hidden ? api.show() : api.hide(); },
  };
  return api;
}

// ------------------------------------------------------------------------------------------ the {…} menu of a name
/** a select that puts a variable into a text field: `vars` is `[[name, text]]` */
function varsMenu(input, vars, title = "Put a variable in the name") {
  const sel = h("select", { class: "varsel", title, onchange: () => {
    if (!sel.value) return;
    const a = input.selectionStart ?? input.value.length, b = input.selectionEnd ?? a;
    input.value = input.value.slice(0, a) + sel.value + input.value.slice(b);
    input.focus(); input.setSelectionRange(a + sel.value.length, a + sel.value.length);
    input.dispatchEvent(new Event("input", { bubbles: true })); input.dispatchEvent(new Event("change", { bubbles: true }));
    sel.value = "";
  } }, h("option", { value: "" }, "{…}"), vars.map(([n, t]) => h("option", { value: n.startsWith("{") ? n : `{${n}}` }, `${n.startsWith("{") ? n : `{${n}}`}  ${t}`)));
  return sel;
}
const varList = (a, b) => [...(a || []).map(v => [v.name, v.text.en]), ...(b || []).map(v => [v.name, v.text.en])];

// ------------------------------------------------------------------------------------------ Toolpath packages
let packageCache = [], packageInfo = null;
export const packages = () => packageCache;
/** the kept packages (and the variables their names may use); the Use a Package menu follows them */
export async function loadPackages() {
  try { const r = await app.engine.call("packages", {}); packageCache = r.packages; packageInfo = r; return r; } catch (e) { console.error(e); return null; }
}
const toolText = t => (t ? describeTool(t) : "(none)");

async function pickTool(current, title) {
  const t = await toolDatabase({ pick: true, selectedId: current && current.id });
  void title;
  return t;
}

/** one toolpath of a package: name, tool (the package's or its own), layers, settings, and which settings are asked when it is used */
async function packageItemDialog(item, packageTool, info) {
  const op = ops().find(o => o.id === item.op);
  if (!op) { await alertBox(`The toolpath type '${item.op}' is not available.`, "Package"); return null; }
  const it = deepCopy(item);
  const name = h("input", { type: "text", value: it.name });
  const own = h("input", { type: "checkbox", checked: it.tool !== null });
  const toolLabel = h("span", { class: "toollabel" });
  const showTool = () => fill(toolLabel, toolText(own.checked ? it.tool : packageTool), own.checked ? "" : "  (the package's)");
  own.addEventListener("change", showTool);
  const layers = h("input", { type: "text", value: (it.layers || []).join(", "), title: "When the package is put on layers: the layers this toolpath machines (names, with commas). Empty: the layers chosen when it is used" });
  const layerNames = (app.cur ? app.cur.project.layers : []).map(l => l.name);
  const layerPick = h("select", { class: "varsel", onchange: () => { if (layerPick.value) { layers.value = [...new Set([...layers.value.split(",").map(x => x.trim()).filter(Boolean), layerPick.value])].join(", "); layerPick.value = ""; } } },
    h("option", { value: "" }, "▾"), layerNames.map(n => h("option", { value: n }, n)));
  const form = new ParamForm(op.params, it.params, { collapse: true, tools: app.tools });
  const askable = op.params.filter(p => p.kind !== "tool" && p.kind !== "points" && p.name !== "tab_points");
  const asked = new Set(it.ask);
  const askBoxes = askable.map(p => h("label", { class: "row check" }, h("input", { type: "checkbox", checked: asked.has(p.name), dataset: { name: p.name } }), h("span", null, `${p.label}  (${p.name})`)));
  showTool();
  const body = h("div", null,
    h("div", { class: "row" }, h("span", { class: "lab small" }, "Name"), name, varsMenu(name, varList(info.variables, info.toolpath_variables))),
    h("label", { class: "row check" }, own, h("span", null, "A tool of its own (not the package's)")),
    h("div", { class: "row" }, toolLabel, btn("Choose tool…", async () => { const t = await pickTool(it.tool || packageTool); if (t) { it.tool = t; own.checked = true; showTool(); } }, { small: true })),
    h("div", { class: "row" }, h("span", { class: "lab small" }, "Layers (on layers)"), layers, layerPick),
    h("h4", null, "Settings"), h("div", { class: "scrollform" }, form.el),
    h("h4", null, "Asked when the package is used (changed for that time)"), h("div", { class: "checklist" }, askBoxes));
  const r = await dialog({ title: `${op.label} in the package`, width: 520, body, buttons: [{ label: "Cancel", value: null }, { label: "OK", primary: true, value: "ok" }] }).done;
  if (r !== "ok") return null;
  it.name = name.value.trim() || op.label;
  if (!own.checked) it.tool = null; else if (!it.tool) it.tool = deepCopy(packageTool);
  it.params = form.values();
  it.ask = askBoxes.map(l => l.querySelector("input")).filter(i => i.checked).map(i => i.dataset.name);
  it.layers = [...new Set(layers.value.split(",").map(x => x.trim()).filter(Boolean))];
  return it;
}

/** the packages window: make, change and remove packages; resolves the id of a package to put on the selection (or null) */
export async function packagesDialog(d, selectId = null) {
  let info;
  info = await loadPackages();
  if (!info) { toast("The packages cannot be read", "error"); return null; }
  let pkgs = deepCopy(info.packages), cur = pkgs.find(p => p.id === selectId) || pkgs[0] || null, toApply = null;
  const listEl = h("div", { class: "listbox pklist" }), right = h("div", { class: "pkright" });
  const name = h("input", { type: "text" }), groupName = h("input", { type: "text" });
  const makeGroup = h("input", { type: "checkbox" }), thisSheet = h("input", { type: "checkbox" });
  const toolLabel = h("span", { class: "toollabel" }), shown = h("div", { class: "muted" });
  const itemsEl = h("tbody");
  let row = -1, timer = 0;
  const commit = () => { if (!cur) return; cur.name = name.value.trim() || cur.name; cur.make_group = makeGroup.checked; cur.group_name = groupName.value || info.group_default; cur.this_sheet = thisSheet.checked; };
  async function showGroup() {
    clearTimeout(timer);
    timer = setTimeout(async () => {
      if (!cur || !makeGroup.checked) { fill(shown, cur ? "(no group)" : ""); return; }
      try { const r = await app.engine.call("package_info", { package: { ...cur, name: name.value.trim() || cur.name }, project: d.project, sheet: d.sheetId, group_template: groupName.value || info.group_default }); fill(shown, `→ ${r.group}  (on this sheet, now)`); } catch { fill(shown, ""); }
    }, 250);
  }
  const drawList = () => fill(listEl, pkgs.map(p => h("div", { class: "pkrow" + (p === cur ? " on" : ""), onclick: () => { commit(); cur = p; row = -1; show(); } }, p.name)));
  function drawItems() {
    fill(itemsEl, cur ? cur.items.map((it, i) => h("tr", { class: i === row ? "on" : "", title: it.layers && it.layers.length ? "On layers: " + it.layers.join(", ") : "", onclick: () => { row = i; drawItems(); }, ondblclick: () => editItem() },
      h("td", null, it.name), h("td", null, opLabel(it.op)), h("td", null, it.tool ? describeTool(it.tool) : "(package's)"), h("td", null, it.ask.join(", ")))) : []);
  }
  function show() {
    drawList();
    const on = !!cur;
    for (const w of [name, makeGroup, groupName, thisSheet, ...right.querySelectorAll("button")]) w.disabled = !on;
    if (!on) { name.value = ""; fill(toolLabel, ""); drawItems(); return; }
    name.value = cur.name; makeGroup.checked = cur.make_group; groupName.value = cur.group_name; thisSheet.checked = cur.this_sheet; fill(toolLabel, toolText(cur.tool));
    groupName.disabled = !cur.make_group; drawItems(); showGroup();
  }
  makeGroup.addEventListener("change", () => { groupName.disabled = !makeGroup.checked; showGroup(); });
  for (const w of [name, groupName]) w.addEventListener("input", showGroup);
  async function newPackage() {
    const n = await promptBox("Name of the package:", "", "New package");
    if (!n || !n.trim()) return;
    const tool = await pickTool(null);
    if (!tool) return;
    commit();
    cur = { id: uid(), name: n.trim(), tool, items: [], make_group: true, group_name: info.group_default, this_sheet: true };
    pkgs.push(cur); row = -1; show();
  }
  async function addItem(opId) {
    const op = ops().find(o => o.id === opId), names = new Set(op.params.map(p => p.name));
    let item = { name: `${op.label.split(" (")[0]} ${cur.items.length + 1}`, op: opId, params: {}, tool: cur.tool ? null : (app.tools[0] ? deepCopy(app.tools[0]) : null), ask: info.ask_default.filter(a => names.has(a)), safe_z: null, clearance: null, sequence: {}, layers: [] };
    item = await packageItemDialog(item, cur.tool, info);
    if (item) { cur.items.push(item); drawItems(); }
  }
  async function editItem() {
    if (!cur || row < 0) return;
    const it = await packageItemDialog(cur.items[row], cur.tool, info);
    if (it) { cur.items[row] = it; drawItems(); }
  }
  const addSel = h("select", { class: "addop", onchange: () => { if (addSel.value) { addItem(addSel.value); addSel.value = ""; } } }, h("option", { value: "" }, "Add toolpath ▾"), info.operations.map(o => h("option", { value: o.id }, o.label)));
  const fromSelected = async () => {
    if (!cur) return;
    try {
      const r = await run("package_items", { project: d.project, toolpaths: d.toolpathSelection, tool: cur.tool }, { label: "Package" });
      cur.items.push(...r.items); drawItems();
    } catch (e) { report(e, "Package"); }
  };
  right.append(h("div", { class: "row" }, h("span", { class: "lab small" }, "Name"), name),
    h("div", { class: "row" }, h("span", { class: "lab small" }, "Tool"), toolLabel, btn("Change tool…", async () => { if (!cur) return; const t = await pickTool(cur.tool); if (t) { cur.tool = t; fill(toolLabel, toolText(t)); drawItems(); } }, { small: true })),
    h("label", { class: "row check" }, makeGroup, h("span", null, "Put the toolpaths in a new toolpath group")),
    h("div", { class: "row" }, h("span", { class: "lab small" }, "Group name"), groupName, varsMenu(groupName, varList(info.variables), "{package}, {sheet_number}, {sheet_name}, {n}: the next number ({n:02}: 01, 02 ...)")),
    shown, h("label", { class: "row check" }, thisSheet, h("span", null, "Only on the sheet that is open (else on every sheet)")),
    h("h4", null, "Toolpaths of the package"),
    h("div", { class: "listbox" }, h("table", { class: "grid pkitems" }, h("thead", null, h("tr", null, ["Name", "Type", "Tool", "Asked"].map(t => h("th", null, t)))), itemsEl)),
    h("div", { class: "btnrow" }, addSel, btn("From selected toolpaths", fromSelected, { small: true }), btn("Edit…", editItem, { small: true }),
      btn("Remove", () => { if (cur && row >= 0) { cur.items.splice(row, 1); row = -1; drawItems(); } }, { small: true }),
      btn("Up", () => { if (cur && row > 0) { [cur.items[row - 1], cur.items[row]] = [cur.items[row], cur.items[row - 1]]; row--; drawItems(); } }, { small: true }),
      btn("Down", () => { if (cur && row >= 0 && row < cur.items.length - 1) { [cur.items[row + 1], cur.items[row]] = [cur.items[row], cur.items[row + 1]]; row++; drawItems(); } }, { small: true })));
  const left = h("div", { class: "pkleft" }, listEl, h("div", { class: "btnrow" },
    btn("New…", newPackage, { small: true }),
    btn("Duplicate", () => { if (!cur) return; commit(); const q = { ...deepCopy(cur), id: uid(), name: cur.name + " copy" }; pkgs.push(q); cur = q; show(); }, { small: true }),
    btn("Delete", async () => { if (cur && await confirmBox(`Delete the package '${cur.name}'?`, { yes: "Delete", title: "Delete package" })) { pkgs = pkgs.filter(p => p !== cur); cur = pkgs[0] || null; row = -1; show(); } }, { small: true }),
    btn("Import…", async () => {
      const f = (await pickDeviceFiles(".icampkg,.json"))[0];
      if (!f) return;
      try { const r = await run("package_import", { text: new TextDecoder().decode(f.bytes), keep: false }, { label: "Importing packages" }); commit(); pkgs.push(...r.imported); cur = r.imported[0] || cur; show(); } catch (e) { report(e, "Import packages"); }
    }, { small: true }),
    btn("Export…", async () => { if (!cur) return; commit(); try { const r = await run("package_export", { package: cur }, { label: "Package" }); await deliverFiles([{ name: r.name, text: r.text, mime: "application/json" }], "Export package"); } catch (e) { report(e, "Export package"); } }, { small: true })));
  show();
  const save = async () => {
    commit();
    try { const r = await run("package_save", { packages: pkgs }, { label: "Saving the packages" }); packageCache = r.packages; emit("settings"); return true; } catch (e) { report(e, "Toolpath Packages"); return false; }
  };
  const dlg = dialog({ title: "Toolpath Packages", width: 860, className: "pkdlg", body: h("div", { class: "pkwin" }, left, right), buttons: [
    { label: "Cancel", value: null }, { label: "Save", value: "ok", onclick: async () => (await save()) },
    { label: "Save and use on the selection…", primary: true, value: "ok", onclick: async () => { if (!(await save())) return false; toApply = cur && cur.id; } }] });
  const r = await dlg.done;
  return r === "ok" ? toApply : null;
}

/** a package on the selected vectors (or on layers): the tool for this time, the asked settings and the names of every toolpath */
export async function applyPackage(d, pid) {
  let info;
  try { info = await app.engine.call("package_info", { id: pid, project: d.project, sheet: d.sheetId }); } catch (e) { report(e, "Package"); return; }
  const pkg = { ...info.package, variables: ((packageInfo || await loadPackages() || {}).variables) || [] };
  let tool = deepCopy(pkg.tool);
  const itemTools = {};
  const hasSel = d.selection.length > 0;
  const onSel = h("input", { type: "radio", name: "pkon", checked: hasSel, disabled: !hasSel }), onLayers = h("input", { type: "radio", name: "pkon", checked: !hasSel });
  const layerBoxes = d.project.layers.map(l => ({ l, el: h("input", { type: "checkbox", checked: l.id === d.layerId }) }));
  const layersEl = h("div", { class: "checklist" }, layerBoxes.map(({ l, el }) => h("label", { class: "row check" }, el, h("span", null, l.name))));
  const toolLabel = h("span", { class: "toollabel" }, toolText(tool));
  const makeGroup = h("input", { type: "checkbox", checked: pkg.make_group }), groupName = h("input", { type: "text", value: pkg.group_name }), thisSheet = h("input", { type: "checkbox", checked: pkg.this_sheet });
  const groupShown = h("div", { class: "muted" }), warn = h("div", { class: "dlg-warn" });
  const boxes = pkg.items.map((it, i) => {
    const use = h("input", { type: "checkbox", checked: it.available }), nm = h("input", { type: "text", value: it.name }), sh = h("div", { class: "muted" });
    const layer = h("select", null, h("option", { value: "" }, "(the layers checked above)"), d.project.layers.map(l => h("option", { value: l.id }, l.name)));
    const own = d.project.layers.find(l => (it.layers || []).includes(l.name));
    if (own) layer.value = own.id;
    const form = it.asked.length ? new ParamForm(it.asked, {}, { tools: app.tools, onChange: () => refresh() }) : null;
    const tl = h("span", { class: "toollabel" }, it.tool ? "Tool: " + toolText(it.tool) : "");
    const layerRow = h("label", { class: "row" }, h("span", { class: "lab small" }, "Layer"), layer);
    use.addEventListener("change", () => refresh()); nm.addEventListener("input", () => refresh()); layer.addEventListener("change", () => refresh());
    const el = h("fieldset", { class: "group pkitem" }, h("legend", null, h("label", { class: "row check" }, use, h("span", null, it.label + (it.available ? "" : " (not available)")))),
      h("div", { class: "row" }, h("span", { class: "lab small" }, "Name"), nm, varsMenu(nm, varList(pkg.variables, (packageInfo || {}).toolpath_variables))), sh, layerRow,
      it.tool ? h("div", { class: "row" }, tl, btn("Change…", async () => { const t = await pickTool(itemTools[i] || it.tool); if (t) { itemTools[i] = t; fill(tl, "Tool: " + toolText(t)); refresh(); } }, { small: true })) : null,
      form ? form.el : null);
    return { it, use, nm, sh, layer, layerRow, form, el };
  });
  function request(extra = {}) {
    const values = {}, names = {}, skip = [], itemLayers = {};
    boxes.forEach((b, i) => {
      if (!b.use.checked) { skip.push(i); return; }
      names[i] = b.nm.value;
      if (b.form) values[i] = b.form.values();
      if (onLayers.checked && b.layer.value) itemLayers[i] = [b.layer.value];
    });
    return { id: pid, project: d.project, sheet: d.sheetId, selection: onLayers.checked ? { mode: "layers", layers: layerBoxes.filter(x => x.el.checked).map(x => x.l.id) } : { mode: "vectors", vectors: d.selection },
      tool, values, names, skip, item_layers: itemLayers, item_tools: itemTools, make_group: makeGroup.checked, group_template: groupName.value || undefined, this_sheet: thisSheet.checked, ...extra };
  }
  let seq = 0, timer = 0;
  function refresh() {
    layersEl.hidden = !onLayers.checked; for (const b of boxes) b.layerRow.hidden = !onLayers.checked; groupName.disabled = !makeGroup.checked;
    clearTimeout(timer);
    timer = setTimeout(async () => {
      const my = ++seq;
      try {
        const r = await app.engine.call("package_apply", request());
        if (my !== seq) return;
        fill(warn, ""); fill(groupShown, r.group ? "→ " + r.group : "(no group)");
        let k = 0;
        for (const b of boxes) fill(b.sh, b.use.checked ? "→ " + (r.names[k++] ?? "") : "(left out)");
      } catch (e) { if (my === seq) fill(warn, e.message || String(e)); }
    }, 200);
  }
  for (const w of [onSel, onLayers, makeGroup, thisSheet]) w.addEventListener("change", refresh);
  groupName.addEventListener("input", refresh);
  for (const { el } of layerBoxes) el.addEventListener("change", refresh);
  refresh();
  const body = h("div", { class: "scrollform tall" },
    h("label", { class: "row check" }, onSel, h("span", null, `The ${d.selection.length} selected vector(s)`)),
    h("label", { class: "row check" }, onLayers, h("span", null, "Vectors on layers (each toolpath its own layers, or these):")), layersEl,
    pkg.tool ? h("div", { class: "row" }, h("span", { class: "lab small" }, "Tool"), toolLabel, btn("Change tool…", async () => { const t = await pickTool(tool); if (t) { tool = t; fill(toolLabel, toolText(t)); refresh(); } }, { small: true, title: "Another tool for this time (the package keeps its own)" })) : null,
    h("label", { class: "row check" }, makeGroup, h("span", null, "Put them in a new toolpath group")),
    h("div", { class: "row" }, h("span", { class: "lab small" }, "Group name"), groupName, varsMenu(groupName, varList(pkg.variables || [], []))), groupShown,
    h("label", { class: "row check" }, thisSheet, h("span", null, "Only on the sheet that is open")), boxes.map(b => b.el), warn);
  let made = null;
  const r = await dialog({ title: `Package: ${pkg.name}`, width: 540, body, buttons: [{ label: "Cancel", value: null }, { label: "Create toolpaths", primary: true, value: "ok",
    onclick: async () => {
      if (boxes.every(b => !b.use.checked)) { warn.textContent = "Every toolpath of the package is left out."; return false; }
      try { made = await run("package_apply", request(), { label: "Package" }); } catch (e) { warn.textContent = e.message || String(e); return false; }
    } }] }).done;
  if (r !== "ok" || !made) return;
  d.edit(`Package ${pkg.name}`, p => { p.toolpaths.push(...made.toolpaths); });
  d.toolpathSelection = made.toolpaths.map(t => t.id);
  A.status(`${made.toolpaths.length} toolpath(s) of the package ${pkg.name}` + (made.group ? ` in the group ${made.group}` : ""));
  await A.recalc();
}

export async function savePackage(d) {
  if (!d.project.toolpaths.length) { await alertBox("Create toolpaths first: a package is made of them.", "Package"); return; }
  const tps = d.project.toolpaths.filter(t => d.toolpathSelection.includes(t.id));
  const n = await promptBox(`Name of the package (${(tps.length ? tps : d.project.toolpaths).length} toolpath(s)):`, "", "Save as a package");
  if (!n || !n.trim()) return;
  try {
    const r = await run("package_from_toolpaths", { project: d.project, toolpaths: d.toolpathSelection, name: n.trim() }, { label: "Package" });
    packageCache = r.packages; emit("settings");
    const use = await packagesDialog(d, r.saved[0]);
    if (use) await applyPackage(d, use);
  } catch (e) { report(e, "Package"); }
}
export async function managePackages(d, selectId = null) {
  const use = await packagesDialog(d, selectId);
  if (use) await applyPackage(d, use);
}

// ------------------------------------------------------------------------------------------ toolpath templates
export async function saveTemplate(d) {
  if (!d.project.toolpaths.length) { await alertBox("There are no toolpaths to save.", "Toolpath template"); return; }
  try {
    const r = await run("template_save", { project: d.project }, { label: "Toolpath template" });
    await deliverFiles([{ name: r.name, text: r.text, mime: "application/json" }], "Save toolpath template");
  } catch (e) { report(e, "Toolpath template"); }
}
export async function loadTemplate(d) {
  try {
    const f = await openFile({ title: "Load toolpath template", accept: ".icamtpl,.json", exts: ["icamtpl", "json"] });
    if (!f) return;
    const r = await run("template_load", { project: d.project, text: new TextDecoder().decode(f.bytes) }, { label: "Toolpath template" });
    d.replace("Load toolpath template", r.project);
    A.status(`${r.added.length} toolpath(s) loaded`);
    await A.recalc();
  } catch (e) { report(e, "Load toolpath template"); }
}

// ------------------------------------------------------------------------------------------ stock of plates
const SHAPES = [["rect", "Rectangle"], ["rounded", "Rounded rectangle"], ["circle", "Circle (with an optional hole)"], ["ellipse", "Ellipse"], ["polygon", "Regular polygon"], ["l", "L shape"]];
async function plateDialog(d) {
  const s = d.sheet, w = ["rect", "rounded", "ellipse", "l"];
  const params = [P("shape", "Shape", "choice", "rect", { choices: SHAPES }), P("name", "Name (empty: from the size)", "str", ""),
    P("width", "Width (X)", "length", s.width, { min: 1, visible: { shape: w } }), P("height", "Height (Y)", "length", s.height, { min: 1, visible: { shape: w } }),
    P("radius", "Corner radius", "length", 50, { min: 0, visible: { shape: "rounded" } }),
    P("diameter", "Diameter", "length", Math.min(s.width, s.height), { min: 1, visible: { shape: ["circle", "polygon"] }, help: "Across the corners for a polygon" }),
    P("hole", "Hole in the middle (diameter, 0 = none)", "length", 0, { min: 0, visible: { shape: "circle" } }), P("sides", "Sides", "int", 6, { min: 3, max: 64, visible: { shape: "polygon" } }),
    P("notch_width", "Notch width (top right)", "length", s.width / 3, { min: 1, visible: { shape: "l" } }), P("notch_height", "Notch height (top right)", "length", s.height / 3, { min: 1, visible: { shape: "l" } }),
    P("quantity", "Quantity (0 = unlimited)", "int", 1, { min: 0, max: 9999 }), P("leftover", "Leftover (use before new sheets)", "bool", false)];
  let form;
  const presets = h("select", { class: "presets" }, h("option", { value: "" }, "Preset sheet sizes..."), (app.schema.new_project.presets || []).map(([n, a, b]) => h("option", { value: `${a}x${b}` }, n)));
  presets.addEventListener("change", () => { if (presets.value) { const [a, b] = presets.value.split("x").map(Number); form.set({ width: a, height: b }); presets.value = ""; } });
  const v = await formDialog({ title: "Add Plate", params, width: 420, extra: presets, afterBuild: f => { form = f; } });
  if (!v) return null;
  const sizes = {};
  for (const k of ["width", "height", "radius", "diameter", "hole", "sides", "notch_width", "notch_height"]) if (v[k]) sizes[k] = v[k];
  try { return (await run("stock_plate", { shape: v.shape, name: v.name, quantity: v.quantity, leftover: v.leftover, ...sizes }, { label: "Plate" })).plate; } catch (e) { report(e, "Add plate"); return null; }
}
async function leftoverValues(d) {
  const toolD = Math.max(6, ...d.project.toolpaths.filter(t => t.op === "profile").map(t => (t.tool && t.tool.diameter) || 0)) || 6;
  const v = await formDialog({ title: "Leftover of the Sheet", width: 420, remember: "leftover", params: [
    P("clearance", "Cut width round the parts", "length", toolD / 2 + 1, { min: 0, help: "How far the cuts reach beyond the parts: the tool radius and a little more" }),
    P("min_width", "Narrowest strip worth keeping", "length", 30, { min: 0 }), P("min_area", "Smallest piece worth keeping (cm²)", "float", 25, { min: 0 })],
  description: `The material that is left on ${d.sheet.name} after its parts are cut out becomes plates of the stock.` });
  return v ? { clearance: v.clearance, min_width: v.min_width, min_area: v.min_area * 100 } : null;
}
export async function leftoverToStock(d, then) {
  const v = await leftoverValues(d);
  if (!v) return null;
  try {
    const r = await run("stock_leftover", { project: d.project, sheet: d.sheetId, ...v }, { label: "Leftover of the sheet" });
    if (!r.plates.length) { await alertBox("Nothing worth keeping is left on this sheet.", "Stock"); return []; }
    if (then) then(r.plates);
    else { d.edit("Add leftover to stock", p => { p.stock = [...(p.stock || []), ...r.plates]; }); await alertBox(`Added ${r.plates.length} plate(s) to the stock: ` + r.plates.map(p => `${Math.round(p.width)} x ${Math.round(p.height)}`).join(", "), "Stock"); }
    return r.plates;
  } catch (e) { report(e, "Stock"); return null; }
}
export async function stockFromSelection(d, then) {
  try {
    const r = await run("stock_from_vectors", { project: d.project, sheet: d.sheetId, selection: d.selection }, { label: "Stock" });
    if (!r.plates.length) { await alertBox("Select closed vectors that outline the plates first.", "Stock"); return null; }
    if (then) then(r.plates);
    else { d.edit("Add selected vectors to stock", p => { p.stock = [...(p.stock || []), ...r.plates]; }); await alertBox(`Added ${r.plates.length} plate(s) to the stock.`, "Stock"); }
    return r.plates;
  } catch (e) { report(e, "Stock"); return null; }
}
export async function stockDialog(d) {
  let items = deepCopy(d.project.stock || []), sel = new Set();
  const body = h("div", { class: "stockwin" }), table = h("div", { class: "listbox" });
  const commit = () => { /* the inputs write into `items` as they change */ };
  const draw = () => fill(table, items.length ? h("table", { class: "grid stocktable" }, h("thead", null, h("tr", null, ["Name", "Size", "Quantity", "Leftover", "Thickness"].map(t => h("th", null, t)))),
    h("tbody", null, items.map((it, i) => {
      const nm = h("input", { type: "text", class: "inline", value: it.name, oninput: () => { it.name = nm.value; } });
      const qty = h("input", { type: "number", class: "num", min: 0, max: 9999, value: it.quantity, title: "0 = unlimited", oninput: () => { it.quantity = Math.max(0, parseInt(qty.value || "0", 10)); } });
      const left = h("input", { type: "checkbox", checked: !!it.leftover, title: "Leftovers are used before new sheets", onchange: () => { it.leftover = left.checked; } });
      const th = h("input", { type: "text", class: "num", value: it.thickness ? units.toDisplay(it.thickness).toFixed(units.decimals) : "", placeholder: "material", onchange: () => { const x = parseFloat(th.value); it.thickness = x > 0 ? units.fromDisplay(x) : null; } });
      return h("tr", { class: sel.has(i) ? "on" : "", onclick: e => { if (e.target.closest("input")) return; if (e.ctrlKey || e.metaKey) sel.has(i) ? sel.delete(i) : sel.add(i); else sel = new Set([i]); draw(); } },
        h("td", null, nm), h("td", null, `${units.fmt(it.width)} x ${units.fmt(it.height)}` + (it.outline ? "  (shape)" : "")), h("td", null, qty), h("td", { class: "c" }, left), h("td", null, th));
    }))) : h("div", { class: "fb-empty" }, "No plates yet. Add plates, or the leftover of a sheet."));
  draw();
  const add = plates => { items = [...items, ...plates]; draw(); };
  body.append(h("p", { class: "muted" }, "Plates that jobs are nested onto: leftovers of any shape and the sheet sizes you buy. Leftovers are used first, the smallest first."), table,
    h("div", { class: "btnrow" },
      btn("Add plate…", async () => { const p = await plateDialog(d); if (p) add([p]); }, { small: true, title: "A plate of a size or a shape" }),
      btn("From selected vectors", () => stockFromSelection(d, add), { small: true, title: "Closed vectors drawn as the outline of a plate; closed vectors inside it are holes" }),
      btn("Leftover of sheet…", () => leftoverToStock(d, add), { small: true, title: "The material left on the current sheet" }),
      btn("Remove", () => { items = items.filter((_, i) => !sel.has(i)); sel = new Set(); draw(); }, { small: true })),
    h("div", { class: "btnrow" },
      btn("Load…", async () => {
        const f = (await pickDeviceFiles(".json"))[0];
        if (!f) return;
        try { add((await run("stock_import", { text: new TextDecoder().decode(f.bytes) }, { label: "Stock" })).plates); } catch (e) { report(e, "Stock"); }
      }, { small: true }),
      btn("Save…", async () => { try { const r = await run("stock_export", { plates: items }, { label: "Stock" }); await deliverFiles([{ name: r.name, text: r.text, mime: "application/json" }], "Save stock"); } catch (e) { report(e, "Stock"); } }, { small: true })));
  void commit;
  const r = await dialog({ title: "Stock of Plates", width: 680, body, buttons: [{ label: "Cancel", value: null }, { label: "OK", primary: true, value: "ok" }] }).done;
  if (r === "ok") d.edit("Edit stock", p => { p.stock = items; });
}

// ------------------------------------------------------------------------------------------ post-processors
const L = o => (o && typeof o === "object" ? o.en : o) || "";
const optionParams = specs => specs.options.map(o => P(o.key, L(o.label), o.kind === "float" ? "float" : o.kind, o.default, {
  group: L((specs.groups.find(g => g.id === o.group) || {}).label), help: L(o.help), advanced: !o.common, min: o.min ?? null, max: o.max ?? null,
  choices: o.choices ? o.choices.map(([v, l]) => [v, L(l)]) : null, decimals: o.kind === "float" ? 4 : 3 }));

export async function postEditor(d, selectId = null) {
  let data;
  try { data = await app.engine.call("posts", {}); } catch (e) { report(e, "Post-processors"); return; }
  const specs = data.specs;
  let cur = null;                         // the draft shown: {id, name, description, extension, extends, options, blocks, _new, _editable, _builtin}
  let dirty = false, form = null, blockName = specs.blocks[0].name, previewTimer = 0, previewSeq = 0, previewFiles = [], previewPost = null;
  const listEl = h("div", { class: "listbox postlist" }), status = h("div", { class: "muted poststatus" });
  const name = h("input", { type: "text" }), desc = h("input", { type: "text" }), ext = h("input", { type: "text", maxLength: 8, title: "The ending of the files, like nc or tap" });
  const optHost = h("div", { class: "scrollform" }), blockHost = h("div", { class: "blockedit" });
  const preview = h("pre", { class: "postpreview mono" }), explainBox = h("div", { class: "explained" });
  const source = h("select", null, h("option", { value: "sample" }, "a sample part"));
  const say = (t, kind = "") => { status.className = "muted poststatus " + kind; status.textContent = t; };
  const draftOf = p => ({ id: p.id, name: p.name, description: p.description, extension: p.extension, extends: p.extends || "", options: deepCopy(p.options), blocks: deepCopy(p.blocks), _editable: p.editable, _builtin: p.builtin });
  const posts = () => data.posts;
  function drawList() {
    fill(listEl, [...(cur && cur._new ? [cur] : []), ...posts()].map(p => h("div", { class: "pkrow" + (cur && (p === cur || (!p._new && p.id === cur.id && !cur._new)) ? " on" : ""), onclick: () => choose(p) }, (p.editable || p._new ? "✎ " : "") + p.name)),
      data.errors.map(e => h("div", { class: "pkrow bad", title: e.message }, `⚠ ${e.file} (cannot be loaded)`)));
  }
  function choose(p) {
    cur = p._new ? p : draftOf(p); dirty = !!p._new; show();
  }
  function show() {
    drawList();
    name.value = cur.name; desc.value = cur.description || ""; ext.value = cur.extension || "";
    const ro = !(cur._editable || cur._new);
    for (const w of [name, desc, ext]) w.disabled = ro;
    form = new ParamForm(optionParams(specs), cur.options, { collapse: true, onChange: f => { Object.assign(cur.options, f.values()); touched(); } });
    if (ro) form.setDisabled(Object.keys(form.inputs));
    fill(optHost, form.el);
    drawBlock();
    saveBtn.disabled = ro; delBtn.disabled = !cur._editable;
    say(ro ? "A built-in post cannot be changed: make your own from it with “New from selected…”." : cur._new ? "New post: not saved yet." : "");
    refreshPreview();
  }
  function touched() { dirty = true; clearTimeout(previewTimer); previewTimer = setTimeout(refreshPreview, 300); }
  for (const w of [name, desc, ext]) w.addEventListener("input", () => { cur.name = name.value; cur.description = desc.value; cur.extension = ext.value; touched(); });
  function drawBlock() {
    const b = specs.blocks.find(x => x.name === blockName) || specs.blocks[0];
    const ta = h("textarea", { class: "mono", rows: 9, spellcheck: false, value: cur.blocks[b.name] ?? "", disabled: !(cur._editable || cur._new), oninput: () => { cur.blocks[b.name] = ta.value; touched(); } });
    const vars = [...specs.variables.filter(v => v.group === "job" || b.vars === "tool"), ...b.extra.map(n => ({ name: n, text: { en: "" } }))];
    const words = b.words.map(w => [`{${w}}`, "a word of the move"]);
    const sel = h("select", { onchange: () => { if (sel.value) { const a = ta.selectionStart; ta.value = ta.value.slice(0, a) + sel.value + ta.value.slice(ta.selectionEnd); cur.blocks[b.name] = ta.value; sel.value = ""; touched(); } } },
      h("option", { value: "" }, "Insert a variable…"), [...vars.map(v => [`{${v.name}}`, L(v.text)]), ...words].map(([v, t]) => h("option", { value: v }, `${v}  ${t}`)));
    const blockSel = h("select", { onchange: () => { blockName = blockSel.value; drawBlock(); } },
      specs.block_groups.map(g => h("optgroup", { label: L(g.label) }, specs.blocks.filter(x => x.group === g.id).map(x => h("option", { value: x.name, selected: x.name === b.name }, L(x.label) + (x.common ? "" : "  ·"))))));
    fill(blockHost, h("div", { class: "row" }, blockSel), h("p", { class: "muted" }, L(b.when)), ta,
      h("div", { class: "btnrow" }, sel, btn("Back to the default", () => { cur.blocks[b.name] = b.default; drawBlock(); touched(); }, { small: true })));
  }
  const saveBtn = btn("Save", async () => {
    try {
      const draft = { id: cur._new ? "" : cur.id, name: cur.name, description: cur.description, extension: cur.extension, extends: cur.extends, options: cur.options, blocks: cur.blocks };
      const r = await run("post_save", { post: draft }, { label: "Saving the post" });
      data = r; await refreshSchema();
      const saved = data.posts.find(p => p.id === r.saved);
      cur = saved ? draftOf(saved) : cur; dirty = false; show(); say(`Saved ${r.file}` + (r.warnings && r.warnings.length ? " — " + r.warnings.join(" ") : ""), r.warnings && r.warnings.length ? "warn" : "");
    } catch (e) { say(e.message || String(e), "bad"); }
  }, { primary: true });
  const delBtn = btn("Delete", async () => {
    if (!cur || !cur._editable || !(await confirmBox(`Delete the post '${cur.name}'?`, { yes: "Delete", title: "Delete post" }))) return;
    try { data = await run("post_delete", { id: cur.id }, { label: "Post" }); await refreshSchema(); cur = draftOf(posts()[0]); show(); } catch (e) { say(e.message || String(e), "bad"); }
  });
  const newBtn = btn("New from selected…", async () => {
    if (!cur) return;
    const n = await promptBox("Name of your post-processor:", cur.name + " (mine)", "New post");
    if (!n || !n.trim()) return;
    const base = cur._new ? cur.extends : cur.id;
    cur = { ...deepCopy(cur), id: "", name: n.trim(), extends: base, _new: true, _editable: true }; dirty = true; show();
  });
  const reloadBtn = btn("Reload posts", async () => { try { data = await app.engine.call("posts", {}); await refreshSchema(); drawList(); say("Posts read again."); } catch (e) { report(e, "Posts"); } });
  async function refreshPreview() {
    const my = ++previewSeq;
    try {
      const r = await app.engine.call("post_preview", { post: { id: cur._new ? "" : cur.id, name: cur.name, description: cur.description, extension: cur.extension, extends: cur.extends, options: cur.options, blocks: cur.blocks } });
      if (my !== previewSeq) return;
      previewFiles = r.files; previewPost = cur.id;
      const lines = r.files.map(f => f.text).join("\n").split("\n");
      fill(preview, lines.map((l, i) => h("div", { class: "gl", dataset: { i }, onclick: () => explain(lines, i) }, l || " ")));
      if (r.warnings && r.warnings.length) say(r.warnings.join(" "), "warn");
    } catch (e) { if (my === previewSeq) { fill(preview, ""); say(e.message || String(e), "bad"); } }
  }
  async function explain(lines, i) {
    for (const el of preview.children) el.classList.toggle("on", +el.dataset.i === i);
    try {
      const r = await app.engine.call("gcode_explain", { lines: lines.slice(0, i + 1), index: i, lang: "en", post: previewPost && posts().some(p => p.id === previewPost) ? previewPost : undefined });
      fill(explainBox, h("p", null, r.summary || ""), h("table", { class: "kv" }, (r.words || []).map(w => h("tr", null, h("td", { class: "mono" }, w.word), h("td", null, w.text)))));
    } catch (e) { fill(explainBox, h("p", { class: "muted" }, e.message || String(e))); }
  }
  void previewFiles; void source;
  const settingsPage = h("div", null, h("div", { class: "row" }, h("span", { class: "lab small" }, "Name"), name), h("div", { class: "row" }, h("span", { class: "lab small" }, "Description"), desc),
    h("div", { class: "row" }, h("span", { class: "lab small" }, "File extension"), ext), optHost);
  const tb = tabs([["Settings", settingsPage], ["Blocks of text", blockHost]]);
  const body = h("div", { class: "postwin" }, h("div", { class: "postleft" }, listEl, newBtn, delBtn, reloadBtn),
    h("div", { class: "postright" }, tb.el, status, h("div", { class: "btnrow" }, saveBtn), h("div", { class: "muted" }, "Preview on a sample part (click a line to read what it says):"), preview, explainBox));
  const first = selectId ? posts().find(p => p.id === selectId) : posts().find(p => p.editable) || posts()[0];
  cur = draftOf(first); show();
  await dialog({ title: "Post-processors", width: 1000, className: "postdlg", body, buttons: [{ label: "Close", primary: true, value: true,
    onclick: async () => { if (dirty && !(await confirmBox("The post has changes that are not saved. Close anyway?", { yes: "Close", title: "Post-processors" }))) return false; } }] }).done;
}

// ------------------------------------------------------------------------------------------ Help ▸ Keyboard Shortcuts (the list, with the person's changes)
export function shortcutsHelp() {
  const rows = [];
  const walk = (items, path) => { for (const it of items) { if (it === "-") continue; if (it.menu) walk(it.menu, [...path, it.text]); else { const k = A.shortcutText(it); if (k) rows.push([path.join(" ▸ "), it.text.replace(/\.\.\.$/, ""), k]); } } };
  for (const m of MENUS) walk(m.menu, [m.text]);
  rows.push(["Canvas", "Zoom at the cursor", "Wheel"], ["Canvas", "Pan", "Middle drag, or Space + drag, or two fingers"], ["Canvas", "Nudge the selection", "Arrow keys (Shift ×10, Ctrl ÷10)"],
    ["Canvas", "Delete", "Del"], ["Canvas", "Cancel the tool", "Esc"]);
  dialog({ title: "Keyboard Shortcuts", width: 560, body: h("div", null, h("div", { class: "scrollform tall" }, h("table", { class: "kv shortcuts" }, rows.map(([a, b, c]) => h("tr", null, h("td", { class: "muted" }, a), h("td", null, b), h("td", { class: "mono" }, c))))),
    h("p", { class: "muted" }, "Edit ▸ Keyboard Shortcuts changes them.")) });
}

/* iyiCAM web: the window.  Starts the engine, reads the schema, builds the menu bar, tool bars, docks, the drawing, the document
 * tabs and the status bar.  Layout and look are the desktop program's (docs/webapp/desktop_reference). */
import { h, fill, clamp, store } from "./dom.js";
import { app, on, emit, settings, setSetting, Doc, addDoc, setCur } from "./state.js";
import { startEngine, run, report, files as filesApi } from "./engine.js";
import { startTheme } from "./theme.js";
import { DrawingView } from "./canvas.js";
import { Docks } from "./docks.js";
import { buildMenubar } from "./menus.js";
import { buildToolbars } from "./toolbars.js";
import * as A from "./actions.js";
import { icon } from "./icons.js";
import { deepCopy } from "./dom.js";

const $ = id => document.getElementById(id);

// ------------------------------------------------------------------------------------------ sign in and start
async function signIn() {
  let st = null;
  try { const r = await fetch("/api/session", { credentials: "same-origin" }); if (r.ok && (r.headers.get("content-type") || "").includes("json")) st = await r.json(); } catch { st = null; }
  if (!st || st.signed_in !== false) return;       // (no server that asks: a static page, or already in)
  await new Promise(resolve => {
    const key = h("input", { type: "password", placeholder: "Key", autocomplete: "current-password", autofocus: true });
    const err = h("div", { class: "login-err" });
    const go = async () => {
      try {
        const r = await fetch("/api/login", { method: "POST", credentials: "same-origin", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ key: key.value, remember: true }) });
        if (r.ok) { box.remove(); resolve(); } else fill(err, "That key is not right.");
      } catch (e) { fill(err, "Cannot reach the server."); }
    };
    key.addEventListener("keydown", e => { if (e.key === "Enter") go(); });
    const box = h("div", { class: "login" }, h("form", { onsubmit: e => { e.preventDefault(); go(); } }, h("h1", null, "iyiCAM"), h("p", null, "Sign in with the key"), key, h("button", { class: "btn primary", type: "submit" }, "Sign in"), err));
    document.body.append(box);
    setTimeout(() => key.focus(), 50);
  });
}

function splash(text) {
  let s = $("splash");
  if (!s) { s = h("div", { id: "splash" }, h("div", { class: "logo" }, "iyiCAM"), h("div", { class: "msg" })); document.body.append(s); }
  s.querySelector(".msg").textContent = text;
  return s;
}

async function boot() {
  startTheme();
  const sp = splash("Starting…");
  try {
    await signIn();
    splash("Loading the engine…");
    await startEngine(t => splash(t));
    splash("Reading the schema…");
    app.schema = await app.engine.call("schema", {});
    const local = store.get("iyicam.tools", null);
    app.tools = Array.isArray(local) && local.length ? local : deepCopy(app.schema.tools || []);
  } catch (e) {
    console.error(e);
    splash("The engine did not start: " + (e.message || e));
    sp.classList.add("failed");
    return;
  }
  build();
  A.registerAll();
  A.buildShortcuts();
  app.ui.refreshTools();
  try { await A.startDefault(); } catch (e) { report(e, "Start"); }
  sp.remove();
  app.ui.canvas.zoomExtents();
  app.ui.docks.update();
  emit("docs"); emit("tool");
  A.setTool("select");
  // the window is the drawing's: keys and the browser's own pinch / back gestures
  document.addEventListener("keydown", A.onKeyDown);
  document.addEventListener("keyup", A.onKeyUp);
  window.addEventListener("beforeunload", e => { if (app.docs.some(d => d.dirty)) { e.preventDefault(); e.returnValue = ""; } });
  window.__iyicam = { app, A };           // (for the tests)
  document.body.dataset.ready = "1";
}

// ------------------------------------------------------------------------------------------ the window
function build() {
  const root = $("app");
  fill(root,
    h("div", { id: "menubar", role: "menubar" }),
    h("div", { id: "quickbar", class: "toolbar" }),
    h("div", { id: "work" },
      h("div", { id: "drawbar", class: "toolbar vertical" }),
      h("div", { id: "left", class: "col" }), h("div", { id: "sp-left", class: "splitter v" }),
      h("div", { id: "center" }, h("div", { id: "doctabs" }), h("div", { id: "canvashost" })),
      h("div", { id: "sp-right", class: "splitter v" }), h("div", { id: "right", class: "col" })),
    h("div", { id: "statusbar" }));
  app.ui = {};
  buildMenubar($("menubar"));
  app.ui.refreshTools = buildToolbars($("quickbar"), $("drawbar"));
  app.ui.canvas = new DrawingView($("canvashost"));
  app.ui.canvas.api = A.canvasApi;
  app.ui.docks = new Docks({ left: $("left"), right: $("right"), spLeft: $("sp-left"), spRight: $("sp-right"), work: $("work") });
  buildDocTabs($("doctabs"));
  buildStatus($("statusbar"));
  const narrowMq = matchMedia("(max-width: 820px)");
  const nar = () => { root.classList.toggle("narrow", narrowMq.matches); if (!narrowMq.matches) app.ui.docks.closeDrawers(); app.ui.canvas.redraw(); };
  narrowMq.addEventListener("change", nar); nar();
  // auto recalculation after an edit, when the preferences ask for it
  let t = 0;
  on("project", () => { const d = app.cur; if (!settings.autoRecalc || !d || !d.stale || !d.project.toolpaths.length) return; clearTimeout(t); t = setTimeout(() => A.recalc(), 700); });
  document.title = "iyiCAM";
  on("docs", () => { document.title = (app.cur ? app.cur.name + (app.cur.dirty ? " • " : " — ") : "") + "iyiCAM"; });
  on("project", () => { document.title = (app.cur ? app.cur.name + (app.cur.dirty ? " • " : " — ") : "") + "iyiCAM"; });
}

function buildDocTabs(host) {
  const render = () => fill(host,
    app.docs.map(d => h("div", { class: "doctab" + (d === app.cur ? " on" : ""), title: d.path || d.name, onclick: () => setCur(d), onauxclick: e => { if (e.button === 1) A.closeTab(d); } },
      h("span", { class: "dn" }, d.name + (d.dirty ? " •" : "")), h("button", { type: "button", class: "dx", title: "Close", "aria-label": "Close " + d.name, onclick: e => { e.stopPropagation(); A.closeTab(d); } }, "×"))),
    h("button", { type: "button", class: "dplus", title: "New project (Ctrl+N)", onclick: () => A.newProject() }, "+"));
  for (const e of ["docs", "project"]) on(e, render);
  render();
}

function buildStatus(host) {
  const msg = h("div", { class: "smsg" }), coords = h("div", { class: "scoords" }), busy = h("div", { class: "sbusy", hidden: true });
  const toggles = [["Grid", "snapGrid", "Snap to grid (F7)"], ["Vectors", "snapVectors", "Snap to vectors (F8)"], ["Level", "snapLevel", "Snap level with a node (Shift+F8)"], ["Edges", "snapEdges", "Snap to any point on a vector (Shift+F7)"]]
    .map(([t, k, tip]) => ({ k, el: h("button", { type: "button", class: "stoggle", title: tip, onclick: () => setSetting(k, !settings[k]) }, t) }));
  const syncToggles = () => toggles.forEach(({ k, el }) => el.classList.toggle("on", !!settings[k]));
  fill(host, msg, busy, coords, h("div", { class: "stoggles" }, toggles.map(t => t.el)));
  syncToggles();
  on("settings", syncToggles);
  on("message", () => { msg.textContent = app.message; msg.title = app.message; });
  on("cursor", () => { const c = app.cursor; coords.textContent = c ? `X ${c[0].toFixed(2)}  Y ${c[1].toFixed(2)}` + (app.snapMsg ? `  ·  ${app.snapMsg}` : "") : ""; });
  on("busy", () => {
    const b = app.busy;
    busy.hidden = !b;
    if (!b) return;
    fill(busy, h("span", { class: "spin" }), h("span", null, `${b.label}${b.text && b.text !== b.label ? " — " + b.text : ""}${b.n ? ` (${b.k}/${b.n})` : "…"}`),
      b.n ? h("progress", { max: b.n, value: b.k }) : null, b.cancel ? h("button", { type: "button", class: "btn small", onclick: b.cancel }, "Cancel") : null);
  });
}

boot();
export { clamp, icon, run, filesApi, Doc, addDoc };

/* iyiCAM web: what the window knows.  The project of every open tab is plain data (the .icam JSON); a change to it is
 * made through `doc.edit` (a snapshot is kept for Undo) or `doc.replace` (what the engine gave back).  A vector is never
 * changed in place, a changed one is a new object: the drawing keeps its polylines with the object (see geom.js). */
import { deepCopy, store, uid } from "./dom.js";

export const bus = new EventTarget();
export const on = (name, fn) => { bus.addEventListener(name, fn); return () => bus.removeEventListener(name, fn); };
let pending = new Map();
/** `emit("project")`: listeners run once per frame however often it is said */
export function emit(name, detail) {
  if (pending.has(name)) { pending.get(name).detail = detail; return; }
  const slot = { detail };
  pending.set(name, slot);
  queueMicrotask(() => { pending.delete(name); bus.dispatchEvent(new CustomEvent(name, { detail: slot.detail })); });
}
export const emitNow = (name, detail) => bus.dispatchEvent(new CustomEvent(name, { detail }));

// ------------------------------------------------------------------------------------------------ settings
const DEFAULTS = {
  units: "mm", feedUnit: "min", appearance: "system", grid: 10, snapGrid: false, snapVectors: true, snapLevel: true,
  snapEdges: false, snapReach: 10, resizeHandles: true, arrowStep: "auto", arrowStepCustom: 5, autoRecalc: false,
  calcOnOpen: true, askTool: true, showDirections: false, simResolution: 0,
  showToolpaths: true, showToolWidth: false, showCutOrder: false, showRoute: true, showStarts: false, showSim: true,
  cutInnerFirst: true,
};
export const settings = { ...DEFAULTS, ...store.get("iyicam.settings", {}) };
export function setSetting(k, v) { settings[k] = v; store.set("iyicam.settings", settings); emit("settings", k); }

/** Units as the desktop's Units: lengths in the display unit, feeds per minute or per second. */
export const units = {
  get inch() { return settings.units === "inch"; },
  get factor() { return this.inch ? 25.4 : 1; },
  get suffix() { return this.inch ? " in" : " mm"; },
  get decimals() { return this.inch ? 4 : 3; },
  toDisplay(v) { return v / this.factor; },
  fromDisplay(v) { return v * this.factor; },
  feedToDisplay(v) { return settings.feedUnit === "sec" ? v / 60 : v; },
  feedFromDisplay(v) { return settings.feedUnit === "sec" ? v * 60 : v; },
  get feedSuffix() { return settings.feedUnit === "sec" ? " mm/s" : " mm/min"; },
  fmt(v) { const d = this.toDisplay(v); return `${+d.toFixed(this.inch ? 4 : 2)}${this.suffix}`; },
};

// ------------------------------------------------------------------------------------------------ documents
let counter = 0;
export class Doc {
  constructor(project, name) {
    this.id = ++counter;
    this.project = project;
    this.name = name || project.name || "Untitled";
    this.path = null;             // where the agent keeps the file (or null)
    this.handle = null;           // the file of this device that Save writes to (File System Access API)
    this.dirty = false;
    this.undoStack = []; this.redoStack = [];
    this.selection = [];          // ids of the selected vectors
    this.sheetId = project.sheets[0] && project.sheets[0].id;
    this.layerId = project.layers[0] && project.layers[0].id;
    this.results = null;          // what `compute` gave back
    this.stale = false;           // the project changed since
    this.view = null;             // {s, ox, oy} of the drawing
    this.sim = null;              // {img, extent, png}
    this.guides = [];             // [{axis: "x"|"y", pos}]
    this.hiddenToolpaths = new Set();
    this.toolpathSelection = [];
    this.groupsOpen = new Set();
  }
  get sheet() { return this.project.sheets.find(s => s.id === this.sheetId) || this.project.sheets[0]; }
  get layer() { return this.project.layers.find(l => l.id === this.layerId) || this.project.layers[0]; }
  selected() { const s = new Set(this.selection); return this.sheet.vectors.filter(v => s.has(v.id)); }
  snap() { return JSON.stringify(this.project); }

  /** Keep the project as it is now for Undo, under this label (what the Edit menu says: "Undo Move"). */
  checkpoint(label, snap = this.snap(), selection = this.selection.slice()) {
    this.undoStack.push({ label, snap, selection, sheetId: this.sheetId });
    if (this.undoStack.length > 100) this.undoStack.shift();
    this.redoStack.length = 0;
    this.dirty = true;
    this.stale = true;
  }
  /** `fn()` changes the project; Undo can take it back */
  edit(label, fn) {
    const snap = this.snap(), sel = this.selection.slice();
    fn(this.project);
    this.checkpoint(label, snap, sel);
    this.changed();
  }
  /** a whole new project (the engine's answer to a command) */
  replace(label, project, selection) {
    this.checkpoint(label);
    this.project = project;
    if (selection) this.selection = selection;
    this.fix();
    this.changed();
  }
  fix() {   // (the sheet and the layer that are open must still exist)
    if (!this.project.sheets.some(s => s.id === this.sheetId)) this.sheetId = this.project.sheets[0].id;
    if (!this.project.layers.some(l => l.id === this.layerId)) this.layerId = this.project.layers[0] && this.project.layers[0].id;
    const ids = new Set(this.sheet.vectors.map(v => v.id));
    this.selection = this.selection.filter(i => ids.has(i));
  }
  changed() { emit("project", this); emit("selection", this); }
  undoLabel() { const u = this.undoStack[this.undoStack.length - 1]; return u ? u.label : null; }
  redoLabel() { const u = this.redoStack[this.redoStack.length - 1]; return u ? u.label : null; }
  undo() { return this._step(this.undoStack, this.redoStack); }
  redo() { return this._step(this.redoStack, this.undoStack); }
  _step(from, to) {
    const e = from.pop();
    if (!e) return false;
    to.push({ label: e.label, snap: this.snap(), selection: this.selection.slice(), sheetId: this.sheetId });
    this.project = JSON.parse(e.snap);
    this.selection = e.selection; this.sheetId = e.sheetId;
    this.fix();
    this.dirty = true; this.stale = true;
    this.changed();
    return true;
  }
}

export const app = {
  schema: null, engine: null,
  docs: [], cur: null,
  tool: "select",
  clipboard: null,                 // {vectors: [...]}  (copied vectors, layers by name)
  closed: store.get("iyicam.closed", []),   // names of the tabs closed (Reopen Closed Tab keeps the projects in memory only)
  recent: store.get("iyicam.recent", []),   // [{name, path}] files of the agent opened lately
  busy: null,
  message: "",
  cursor: null,                    // [x, y] on the sheet
  snapMsg: "",
  overlays: [],                    // fn(ctx, view, "world" | "screen"): what features draw over the drawing
  extraTools: {},                  // name -> {down, move, hover, up, cursor}: drawing tools that features add (feat_*.js)
};
const closedDocs = [];

export function addDoc(doc) { app.docs.push(doc); setCur(doc); return doc; }
export function setCur(doc) { app.cur = doc; emit("docs"); emit("project", doc); emit("selection", doc); emit("results", doc); emit("view", doc); }
export function closeDoc(doc) {
  const i = app.docs.indexOf(doc);
  if (i < 0) return;
  closedDocs.push(doc);
  app.docs.splice(i, 1);
  if (app.cur === doc) app.cur = app.docs[Math.min(i, app.docs.length - 1)] || null;
  emit("docs"); emit("project", app.cur); emit("selection", app.cur); emit("results", app.cur); emit("view", app.cur);
}
export const reopenClosed = () => closedDocs.pop() || null;

export function rememberRecent(name, path) {
  if (!path) return;
  app.recent = [{ name, path }, ...app.recent.filter(r => r.path !== path)].slice(0, 10);
  store.set("iyicam.recent", app.recent);
}

export { deepCopy };

/* iyiCAM web: the docks.  Three groups as on the desktop: Project / Vector Library and Selection / Transform in the left column,
 * Toolpaths / Cut Order / Simulation in the right one, each group with its tabs under the panel.  A dock can be closed (its ×)
 * and shown again from the View menu; the sizes are kept in the browser.  On a narrow screen the two columns are drawers that
 * slide over the drawing. */
import { h, fill, store, clamp } from "./dom.js";
import { icon } from "./icons.js";
import { app, on, emit } from "./state.js";
import * as P from "./panels.js";

const DEFS = {
  project: { title: "Project", group: "lt", make: P.projectPanel },
  veclib: { title: "Vector Library", group: "lt", make: P.vectorLibraryPanel },
  selection: { title: "Selection", group: "lb", make: P.selectionPanel },
  transform: { title: "Transform", group: "lb", make: P.transformPanel },
  toolpaths: { title: "Toolpaths", group: "r", make: P.toolpathsPanel },
  cutorder: { title: "Cut Order", group: "r", make: P.cutOrderPanel },
  simulation: { title: "Simulation", group: "r", make: P.simulationPanel },
};
const GROUPS = { lt: ["project", "veclib"], lb: ["selection", "transform"], r: ["toolpaths", "cutorder", "simulation"] };

export class Docks {
  constructor({ left, right, spLeft, spRight, work }) {
    this.left = left; this.right = right; this.work = work;
    const saved = store.get("iyicam.docks", {});
    this.vis = { ...Object.fromEntries(Object.keys(DEFS).map(k => [k, true])), ...(saved.visible || {}) };
    this.active = { lt: "project", lb: "transform", r: "toolpaths", ...(saved.active || {}) };
    this.layout = { left: 270, right: 340, split: 0.58, ...store.get("iyicam.layout", {}) };
    this.panels = {};
    this.groupEls = {};
    for (const [g, ids] of Object.entries(GROUPS)) {
      for (const id of ids) this.panels[id] = DEFS[id].make();
      this.groupEls[g] = h("div", { class: "dockgroup", dataset: { g } });
    }
    this.vsplit = h("div", { class: "splitter h", title: "Drag to resize" });
    fill(left, this.groupEls.lt, this.vsplit, this.groupEls.lb);
    fill(right, this.groupEls.r);
    this.scrim = h("div", { class: "scrim", onclick: () => this.closeDrawers() });
    work.append(this.scrim);
    this._splitters(spLeft, spRight);
    this.render();
    this.applySizes();
    for (const e of ["project", "selection", "results", "docs", "settings"]) on(e, () => this.update());
  }
  visible(id) { return !!this.vis[id]; }
  save() { store.set("iyicam.docks", { visible: this.vis, active: this.active }); store.set("iyicam.layout", this.layout); }
  show(id) { this.vis[id] = true; this.active[DEFS[id].group] = id; this.save(); this.render(); this.update(); if (matchMedia("(max-width: 820px)").matches) this.drawer(DEFS[id].group === "r" ? "right" : "left", true); emit("settings"); }
  hide(id) { this.vis[id] = false; this.save(); this.render(); emit("settings"); }
  toggle(id) { this.vis[id] ? this.hide(id) : this.show(id); }
  update() { for (const p of Object.values(this.panels)) p.update && p.update(); }

  render() {
    for (const [g, ids] of Object.entries(GROUPS)) {
      const shown = ids.filter(i => this.vis[i]);
      const el = this.groupEls[g];
      el.hidden = !shown.length;
      if (!shown.length) continue;
      if (!shown.includes(this.active[g])) this.active[g] = shown[0];
      const act = this.active[g];
      fill(el,
        h("div", { class: "dock-title" }, h("span", { class: "t" }, DEFS[act].title),
          h("span", { class: "acts" }, h("button", { type: "button", class: "dbtn", title: "Close", "aria-label": "Close " + DEFS[act].title, onclick: () => this.hide(act) }, icon("close", 12)))),
        h("div", { class: "dock-body" }, ...shown.map(i => { const p = this.panels[i].el; p.hidden = i !== act; return p; })),
        shown.length > 1 ? h("div", { class: "dock-tabs" }, shown.map(i => h("button", { type: "button", class: "dtab" + (i === act ? " on" : ""), onclick: () => { this.active[g] = i; this.save(); this.render(); this.update(); } }, DEFS[i].title))) : null);
    }
    const lt = this.groupEls.lt, lb = this.groupEls.lb;
    this.vsplit.hidden = lt.hidden || lb.hidden;
    this.left.hidden = lt.hidden && lb.hidden;
    this.right.hidden = this.groupEls.r.hidden;
    if (this.spLeft) { this.spLeft.hidden = this.left.hidden; this.spRight.hidden = this.right.hidden; }
    this.applySizes();
  }
  applySizes() {
    const L = this.layout;
    this.left.style.width = clamp(L.left, 200, 520) + "px";
    this.right.style.width = clamp(L.right, 220, 640) + "px";
    const lt = this.groupEls.lt, lb = this.groupEls.lb;
    if (!lt.hidden && !lb.hidden) { lt.style.flex = `${L.split} 1 0`; lb.style.flex = `${1 - L.split} 1 0`; } else { lt.style.flex = "1 1 0"; lb.style.flex = "1 1 0"; }
  }
  _splitters(spLeft, spRight) {
    const drag = (el, move) => el.addEventListener("pointerdown", e => {
      e.preventDefault(); el.setPointerCapture(e.pointerId); el.classList.add("on");
      const mv = ev => { move(ev); this.applySizes(); app.ui.canvas && app.ui.canvas.redraw(); };
      const up = () => { el.classList.remove("on"); el.removeEventListener("pointermove", mv); el.removeEventListener("pointerup", up); this.save(); };
      el.addEventListener("pointermove", mv); el.addEventListener("pointerup", up);
    });
    drag(spLeft, ev => { this.layout.left = ev.clientX - this.left.getBoundingClientRect().left; });
    drag(spRight, ev => { this.layout.right = this.right.getBoundingClientRect().right - ev.clientX; });
    drag(this.vsplit, ev => { const r = this.left.getBoundingClientRect(); this.layout.split = clamp((ev.clientY - r.top) / r.height, 0.15, 0.85); });
    this.spLeft = spLeft; this.spRight = spRight;
  }
  /** narrow screens: slide a column over the drawing (or take it away) */
  drawer(side, on = null) {
    const el = side === "left" ? this.left : this.right, other = side === "left" ? this.right : this.left;
    const open = on === null ? !el.classList.contains("open") : on;
    other.classList.remove("open");
    el.classList.toggle("open", open);
    this.scrim.classList.toggle("on", open);
  }
  closeDrawers() { this.left.classList.remove("open"); this.right.classList.remove("open"); this.scrim.classList.remove("on"); }
}

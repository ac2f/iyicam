/* iyiCAM web: the quick tool bar on top and the drawing tool bar on the left (the desktop's, same buttons and tips). */
import { h, fill } from "./dom.js";
import { icon } from "./icons.js";
import { app, on } from "./state.js";
import * as A from "./actions.js";

const QUICK = [
  ["file.new", "new", "New project (Ctrl+N)"], ["file.open", "open", "Open project (Ctrl+O)"], ["file.save", "save", "Save project (Ctrl+S)"], "-",
  ["edit.undo", "undo", "Undo (Ctrl+Z)"], ["edit.redo", "redo", "Redo (Ctrl+Y)"], "-",
  ["view.zoom_extents", "zoomext", "Zoom to the whole sheet (F)"], "-",
  ["nesting.nest_parts", "nest", "Nest parts (Ctrl+Shift+N)"], ["toolpaths.new", "newtp", "New toolpath"],
  ["toolpaths.material_setup", "material", "Material and heights: thickness, Z zero, safe Z, home (Ctrl+Shift+M)"],
  ["toolpaths.cut_order", "cutorder", "Cut order, directions and start points (Ctrl+Alt+O)"],
  ["toolpaths.recalculate_all", "recalc", "Recalculate toolpaths (F5)"], ["toolpaths.simulate", "simulate", "Simulate (F6)"], ["toolpaths.save_g_code", "gcode", "Save G-code (Ctrl+Shift+E)"],
];
const DRAW = [
  ["draw.select_transform", "select", "Select / Transform (S)"], ["draw.edit_nodes", "nodes", "Edit Nodes (N)"], "-",
  ["draw.polyline_bezier", "polyline", "Polyline / Bezier (P)"], ["draw.line", "line", "Line (L)"], ["draw.rectangle", "rect", "Rectangle (R)"], ["draw.circle", "circle", "Circle (C)"],
  ["draw.ellipse", "ellipse", "Ellipse (E)"], ["draw.polygon", "polygon", "Polygon (G)"], ["draw.star", "star", "Star"], ["draw.arc_3_points", "arc", "Arc (3 points) (A)"], ["draw.text", "text", "Text (T)"], "-",
  ["draw.measure", "measure", "Measure (M)"], ["draw.trim_vectors", "trim", "Trim Vectors (X)"], ["draw.set_start_point", "startpoint", "Set Start Point (B)"],
];

function build(host, defs, cls) {
  const buttons = [];
  fill(host, defs.map(d => {
    if (d === "-") return h("span", { class: "tsep" });
    const [id, ic, tip] = d;
    const b = h("button", { type: "button", class: "tbtn " + cls, title: tip, "aria-label": tip, dataset: { id } }, icon(ic, cls === "draw" ? 22 : 22),
      id === "toolpaths.new" ? h("span", { class: "caret" }, "▾") : null);
    b.addEventListener("click", e => {
      if (id === "toolpaths.new") { newMenu(b); return; }
      if (!A.registry.has(id)) { A.status(A.notYet); return; }
      A.runItem(id);
      void e;
    });
    buttons.push([id, b]);
    return b;
  }));
  return buttons;
}
function newMenu(btn) {
  if (!app.cur) return;
  const m = h("div", { class: "popmenu" }, app.schema.operations.map(o => h("div", { class: "item", onclick: () => { m.remove(); A.newToolpath(o.id); } }, o.label)));
  const r = btn.getBoundingClientRect();
  document.body.append(m);
  Object.assign(m.style, { left: Math.min(r.left, innerWidth - m.offsetWidth - 4) + "px", top: r.bottom + "px" });
  setTimeout(() => document.addEventListener("pointerdown", () => m.remove(), { once: true }), 0);
}

export function buildToolbars(quick, draw) {
  const q = build(quick, QUICK, "quick"), d = build(draw, DRAW, "draw");
  // (narrow screens: the docks are drawers, opened from the ends of the quick bar)
  quick.prepend(h("button", { type: "button", class: "tbtn narrow-only", title: "Project, selection and transform panels", onclick: () => app.ui.docks.drawer("left") }, icon("panel", 22)));
  quick.append(h("button", { type: "button", class: "tbtn narrow-only", title: "Toolpaths, cut order and simulation", onclick: () => app.ui.docks.drawer("right") }, icon("panelr", 22)));
  function refresh() {
    for (const [id, b] of [...q, ...d]) {
      const r = A.registry.get(id);
      const ok = id === "toolpaths.new" ? !!app.cur : !!r && (!r.enabled || r.enabled());
      b.classList.toggle("disabled", !ok);
      b.setAttribute("aria-disabled", String(!ok));
      b.classList.toggle("on", !!(r && r.checked && r.checked()));
      if (!r && id !== "toolpaths.new") b.title = b.title.replace(/ — Not on the web yet$/, "") + " — Not on the web yet";
    }
  }
  for (const e of ["docs", "project", "selection", "tool", "settings", "results"]) on(e, refresh);
  refresh();
  return refresh;
}

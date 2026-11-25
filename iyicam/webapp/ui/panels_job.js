/* iyiCAM web: the Cut Order and Simulation panels (of the Toolpaths column). */
import G from "./geom.js";
import { h, fill, fmtTime, toast } from "./dom.js";
import { app, settings, setSetting, units, emit } from "./state.js";
import { icon } from "./icons.js";
import { calc } from "./forms.js";
import * as A from "./actions.js";

const tbtn = (ic, title, fn, o = {}) => h("button", { type: "button", class: "tb", title, disabled: o.disabled, onclick: fn }, icon(ic, 16));
const fmtNum = (v, d = 3) => String(+Number(v).toFixed(d));

// ------------------------------------------------------------------------------------------ Cut Order
export function cutOrderPanel() {
  const sel = h("select", { onchange: update });
  const info = h("div", { class: "muted" });
  const table = h("tbody");
  const el = h("div", { class: "panel cutorder" },
    h("label", { class: "row" }, h("span", { class: "lab" }, "Toolpath"), sel),
    h("div", { class: "listbox tall" }, h("table", { class: "grid" }, h("thead", null, h("tr", null, h("th", null, "#"), h("th", null, "Vector"), h("th", null, "Start point"))), table)), info,
    h("p", { class: "muted" }, "Changing the order, the directions and the start points is not on the web yet."));
  function update() {
    const d = app.cur;
    const tps = d ? d.project.toolpaths.filter(t => ["profile", "pocket", "engrave", "vcarve", "inlay", "fluting"].includes(t.op)) : [];
    const keep = sel.value;
    fill(sel, tps.map(t => h("option", { value: t.id }, t.name)));
    if (tps.some(t => t.id === keep)) sel.value = keep;
    const r = d && d.results ? d.results.results.find(x => x.toolpath === sel.value && x.sheet === d.sheetId) : null;
    if (!r || !r.order) { fill(table); fill(info, d && d.project.toolpaths.length ? "Calculate to see the order of the cuts (F5)." : ""); return; }
    const vmap = new Map(d.sheet.vectors.map(v => [v.id, v]));
    fill(table, r.order.map((key, i) => { const [vid, ci] = key.split(":"), v = vmap.get(vid), c = v && v.contours[+ci];
      return h("tr", { onclick: () => A.select([vid]) }, h("td", null, String(i + 1)), h("td", null, v ? (v.name || "Vector") : "?"), h("td", null, c ? `${fmtNum(c.start[0], 1)}, ${fmtNum(c.start[1], 1)}` : "")); }));
    fill(info, `${r.order.length} cut(s)`);
  }
  return { el, update };
}

// ------------------------------------------------------------------------------------------ Simulation
export function simulationPanel() {
  const info = h("div", { class: "muted" }, "Press Simulate (F6) to see what the calculated toolpaths cut.");
  const show = h("input", { type: "checkbox", checked: settings.showSim, onchange: () => setSetting("showSim", show.checked) });
  const el = h("div", { class: "panel simulation" },
    h("div", { class: "btnrow" }, h("button", { type: "button", class: "btn", onclick: () => A.simulate() }, "▶ Simulate"), h("button", { type: "button", class: "btn", onclick: () => A.clearSimulation() }, "✕ Clear")),
    h("label", { class: "row check" }, show, h("span", null, "Show the result on the drawing")), info,
    h("p", { class: "muted" }, "Playing the simulation move by move is not on the web yet."));
  return { el, update() { const d = app.cur; show.checked = settings.showSim; fill(info, d && d.sim ? `Simulated ${d.sim.size[0]} × ${d.sim.size[1]} px, ${d.sim.res.toFixed ? d.sim.res.toFixed(2) : d.sim.res} mm per pixel. Machining time ${fmtTime(d.sim.time_s)}.` : "Press Simulate (F6) to see what the calculated toolpaths cut."); } };
}


/* iyiCAM web: the Vector Library panel. */
import G from "./geom.js";
import { h, fill, fmtTime, toast } from "./dom.js";
import { app, settings, setSetting, units, emit } from "./state.js";
import { icon } from "./icons.js";
import { calc } from "./forms.js";
import * as A from "./actions.js";

const tbtn = (ic, title, fn, o = {}) => h("button", { type: "button", class: "tb", title, disabled: o.disabled, onclick: fn }, icon(ic, 16));
const fmtNum = (v, d = 3) => String(+Number(v).toFixed(d));

// ------------------------------------------------------------------------------------------ Vector Library
export function vectorLibraryPanel() {
  return { el: h("div", { class: "panel veclib" }, h("p", { class: "muted" }, "The vector library is not on the web yet.")), update() {} };
}

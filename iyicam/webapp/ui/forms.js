/* iyiCAM web: a form from a list of params, as the desktop's forms.py builds it.  Kinds length / feed / angle / int / float /
 * percent / bool / choice / str / text / tool / point, groups (a box each), `visible` conditions, advanced params folded
 * behind "More options (n)".  Lengths are shown in the display unit and kept in millimetres. */
import { h } from "./dom.js";
import { units } from "./state.js";

/** A number typed as a sum ("120/3+2") is worked out, as the desktop's fields do; null when it cannot be read. */
export function calc(text) {
  // (a small parser, not eval: the page's content security policy has no eval)
  const t = String(text).trim().replace(/,/g, ".").replace(/[×x·]/g, "*").replace(/[÷:]/g, "/").replace(/−/g, "-").replace(/\^/g, "**");
  if (t === "") return null;
  if (!/^[0-9+\-*/().eE\s]+$/.test(t)) return NaN;
  const tok = t.match(/\d+\.?\d*(?:[eE][+-]?\d+)?|\.\d+(?:[eE][+-]?\d+)?|\*\*|[-+*/()]/g);
  if (!tok || tok.join("").length !== t.replace(/\s+/g, "").length) return NaN;
  let i = 0;
  const peek = () => tok[i];
  const atom = () => {
    const k = tok[i++];
    if (k === "(") { const v = expr(); if (tok[i++] !== ")") throw new Error("paren"); return v; }
    if (k === undefined || !/^[\d.]/.test(k)) throw new Error("atom");
    return parseFloat(k);
  };
  const unary = () => { if (peek() === "-") { i++; return -unary(); } if (peek() === "+") { i++; return unary(); } return power(); };
  const power = () => { const b = atom(); if (peek() === "**") { i++; return b ** unary(); } return b; };
  const term = () => { let v = unary(); while (peek() === "*" || peek() === "/") { const o = tok[i++]; const r = unary(); v = o === "*" ? v * r : v / r; } return v; };
  const expr = () => { let v = term(); while (peek() === "+" || peek() === "-") { const o = tok[i++]; const r = term(); v = o === "+" ? v + r : v - r; } return v; };
  try { const v = expr(); return i === tok.length && Number.isFinite(v) ? v : NaN; } catch { return NaN; }
}

const isVisible = (p, vals) => {
  if (!p.visible) return true;
  for (const [k, want] of Object.entries(p.visible)) {
    const have = vals[k];
    if (Array.isArray(want) ? !want.includes(have) : have !== want) return false;
  }
  return true;
};

const SHOWN = (kind, v) => (kind === "length" ? units.toDisplay(v) : kind === "feed" ? units.feedToDisplay(v) : v);
const KEPT = (kind, v) => (kind === "length" ? units.fromDisplay(v) : kind === "feed" ? units.feedFromDisplay(v) : v);
const fmtNum = (v, dec) => (v === null || v === undefined ? "" : Number(v).toFixed(dec));

export class ParamForm {
  /** `opts`: `collapse` (advanced ones behind a button), `tools` (the tool database, for kind "tool"), `onChange` */
  constructor(params, values = {}, opts = {}) {
    this.params = params;
    this.opts = opts;
    this.extra = { ...values };            // values of names that are not in the form are kept
    this.inputs = {};
    this.rows = {};
    this.boxes = {};
    this.showAdvanced = !opts.collapse;
    this.el = h("div", { class: "paramform" });
    const groups = {};
    for (const p of params) {
      const g = p.group || "Settings";
      if (!groups[g]) {
        groups[g] = h("div", { class: "fields" });
        this.boxes[g] = h("fieldset", { class: "group" }, h("legend", null, g), groups[g]);
        this.el.append(this.boxes[g]);
      }
      const v = p.name in values ? values[p.name] : p.default;
      const input = this._make(p, v);
      const row = p.kind === "bool"
        ? h("label", { class: "row check", title: p.help || "" }, input.el, h("span", null, p.label))
        : h("label", { class: "row", title: p.help || "" }, h("span", { class: "lab" }, p.label), input.el);
      groups[g].append(row);
      this.inputs[p.name] = input;
      this.rows[p.name] = row;
    }
    this.advanced = opts.collapse ? params.filter(p => p.advanced) : [];
    if (this.advanced.length) {
      this.more = h("button", { type: "button", class: "more", onclick: () => { this.showAdvanced = !this.showAdvanced; this._refresh(); } });
      this.el.append(this.more);
    }
    this._refresh();
  }

  _make(p, v) {
    const change = () => this._changed();
    const k = p.kind;
    if (["length", "feed", "float", "angle", "percent"].includes(k)) {
      const dec = k === "length" ? units.decimals : k === "feed" ? 1 : Math.max(0, Math.min(4, p.decimals ?? 3));
      const suffix = { angle: " °", percent: " %", feed: units.feedSuffix }[k] || (k === "length" ? units.suffix : "");
      const inp = h("input", { type: "text", inputMode: "decimal", class: "num", placeholder: p.default === null ? "auto" : "", autocomplete: "off" });
      const step = p.step || (k === "length" && units.inch ? 0.1 : 1);
      const get = () => { const x = calc(inp.value); return x === null ? null : Number.isNaN(x) ? undefined : KEPT(k, x); };
      const put = val => { inp.value = val === null || val === undefined ? "" : fmtNum(SHOWN(k, val), dec); };
      put(v);
      inp.addEventListener("change", () => {
        const x = get();
        if (x !== undefined && x !== null) {
          let y = x;
          if (p.min !== null && p.min !== undefined) y = Math.max(y, p.min);
          if (p.max !== null && p.max !== undefined) y = Math.min(y, p.max);
          put(y);
        } else if (x === undefined) put(p.default);
        change();
      });
      inp.addEventListener("keydown", e => {
        if (e.key !== "ArrowUp" && e.key !== "ArrowDown") return;
        e.preventDefault();
        const x = get() ?? 0;
        const next = SHOWN(k, x) + (e.key === "ArrowUp" ? step : -step) * (k === "length" ? 1 : 1);
        put(KEPT(k, next)); change();
      });
      return { el: h("span", { class: "unit" }, inp, suffix ? h("span", { class: "suf" }, suffix.trim()) : null),
        get, set: put, focus: () => inp.focus() };
    }
    if (k === "int") {
      const inp = h("input", { type: "number", class: "num", step: 1, min: p.min ?? undefined, max: p.max ?? undefined, value: v ?? 0 });
      inp.addEventListener("change", change);
      return { el: inp, get: () => (inp.value === "" ? 0 : parseInt(inp.value, 10)), set: x => { inp.value = x ?? 0; } };
    }
    if (k === "bool") {
      const inp = h("input", { type: "checkbox", checked: !!v });
      inp.addEventListener("change", change);
      return { el: inp, get: () => inp.checked, set: x => { inp.checked = !!x; } };
    }
    if (k === "choice") {
      const sel = h("select", null, (p.choices || []).map(([val, label]) => h("option", { value: val }, label)));
      sel.value = String(v);
      sel.addEventListener("change", change);
      return { el: sel, get: () => sel.value, set: x => { sel.value = String(x); } };
    }
    if (k === "text") {
      const ta = h("textarea", { rows: 3, value: v ?? "" });
      ta.addEventListener("input", change);
      return { el: ta, get: () => ta.value, set: x => { ta.value = x ?? ""; } };
    }
    if (k === "tool") {
      const tools = this.opts.tools || [];
      const sel = h("select", null, h("option", { value: "" }, "(none)"), tools.map(t => h("option", { value: t.id }, `T${t.number}  ${t.name}`)));
      let own = v && typeof v === "object" ? v : null;       // (a tool of the project that is not in the database)
      if (own && !tools.some(t => t.id === own.id)) sel.append(h("option", { value: own.id }, `${own.name} (project)`));
      sel.value = own ? own.id : "";
      sel.addEventListener("change", change);
      return { el: sel, get: () => (sel.value ? (tools.find(t => t.id === sel.value) || own) : null),
        set: x => { own = x; sel.value = x ? x.id : ""; } };
    }
    if (k === "point") {
      const xs = h("input", { type: "text", class: "num" }), ys = h("input", { type: "text", class: "num" });
      const put = val => { const [a, b] = val || [0, 0]; xs.value = fmtNum(units.toDisplay(a), units.decimals); ys.value = fmtNum(units.toDisplay(b), units.decimals); };
      put(v);
      for (const i of [xs, ys]) i.addEventListener("change", change);
      return { el: h("span", { class: "point" }, xs, ys), get: () => [units.fromDisplay(calc(xs.value) || 0), units.fromDisplay(calc(ys.value) || 0)], set: put };
    }
    const inp = h("input", { type: "text", value: v ?? "" });
    inp.addEventListener("input", change);
    return { el: inp, get: () => inp.value, set: x => { inp.value = x ?? ""; } };
  }

  /** the values as the form has them now (the ones that are hidden too: they are the form's, not the person's) */
  values() {
    const out = { ...this.extra };
    for (const p of this.params) {
      const v = this.inputs[p.name].get();
      out[p.name] = v === undefined ? p.default : v;
    }
    return out;
  }
  set(values) {
    for (const [k, v] of Object.entries(values)) {
      if (this.inputs[k]) this.inputs[k].set(v); else this.extra[k] = v;
    }
    this._refresh();
    this.opts.onChange && this.opts.onChange(this);
  }
  /** switch fields off (a tool's size in the toolpath window: another tool is picked from the database instead) */
  setDisabled(names, on = true) {
    for (const n of names) for (const el of this.rows[n] ? this.rows[n].querySelectorAll("input,select,textarea") : []) el.disabled = on;
  }
  focusFirst() {
    const p = this.params.find(q => ["length", "float", "int", "angle", "percent", "str"].includes(q.kind));
    p && this.inputs[p.name].focus && this.inputs[p.name].focus();
  }
  _changed() { this._refresh(); this.opts.onChange && this.opts.onChange(this); }
  _refresh() {
    const vals = this.values();
    const adv = new Set(this.advanced.map(p => p.name));
    for (const p of this.params) this.rows[p.name].hidden = !(isVisible(p, vals) && (this.showAdvanced || !adv.has(p.name)));
    for (const [g, box] of Object.entries(this.boxes)) box.hidden = !this.params.some(p => (p.group || "Settings") === g && !this.rows[p.name].hidden);
    if (this.more) this.more.textContent = this.showAdvanced ? "▾ Fewer options" : `▸ More options (${this.advanced.length})`;
  }
}

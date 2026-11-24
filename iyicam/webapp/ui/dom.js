/* iyiCAM web: the few helpers every module uses to build the page (no framework: elements are made, not templated). */

/** An element: `h("button", { class: "x", onclick: fn, title: "..." }, "text", child)`.  `style` may be an object,
 *  `dataset` an object; the other keys are properties (`value`, `checked`, `disabled` ...) or `on...` handlers. */
export function h(tag, attrs, ...kids) {
  const el = tag === "svg" || tag === "path" || tag === "circle" || tag === "rect" || tag === "g" || tag === "line" || tag === "ellipse" || tag === "polygon"
    ? document.createElementNS("http://www.w3.org/2000/svg", tag) : document.createElement(tag);
  for (const [k, v] of Object.entries(attrs || {})) {
    if (v === undefined || v === null || v === false) continue;
    if (k === "class") el.setAttribute("class", v);
    else if (k === "style" && typeof v === "object") Object.assign(el.style, v);
    else if (k === "dataset") Object.assign(el.dataset, v);
    else if (k.startsWith("on") && typeof v === "function") el.addEventListener(k.slice(2), v);
    else if (k in el && !(el instanceof SVGElement)) el[k] = v;
    else el.setAttribute(k, v === true ? "" : v);
  }
  add(el, kids);
  return el;
}
function add(el, kids) {
  for (const k of kids) {
    if (k === null || k === undefined || k === false) continue;
    if (Array.isArray(k)) add(el, k);
    else el.append(k instanceof Node ? k : document.createTextNode(String(k)));
  }
}
export const clear = el => { while (el.firstChild) el.removeChild(el.firstChild); return el; };
export const fill = (el, ...kids) => { clear(el); add(el, kids); return el; };

/** Settings kept in the browser: a convenience only, the page works when it is not allowed to store. */
export const store = {
  get(k, d = null) { try { const v = localStorage.getItem(k); return v === null ? d : JSON.parse(v); } catch { return d; } },
  set(k, v) { try { localStorage.setItem(k, JSON.stringify(v)); } catch { /* private window, full: nothing is kept */ } },
};

export const clamp = (v, a, b) => Math.min(b, Math.max(a, v));
export const uid = () => Array.from(crypto.getRandomValues(new Uint8Array(6)), b => b.toString(16).padStart(2, "0")).join("");
export const deepCopy = o => JSON.parse(JSON.stringify(o));

/** "28:35" for a time in seconds */
export function fmtTime(s) {
  s = Math.round(s || 0);
  const hh = Math.floor(s / 3600), m = Math.floor((s % 3600) / 60), ss = s % 60;
  const p = n => String(n).padStart(2, "0");
  return hh ? `${hh}:${p(m)}:${p(ss)}` : `${m}:${p(ss)}`;
}

/** A short-lived message at the bottom of the window (the status bar says it too). */
export function toast(text, kind = "") {
  const box = document.getElementById("toasts");
  if (!box) return;
  const el = h("div", { class: "toast " + kind }, text);
  box.append(el);
  setTimeout(() => el.remove(), kind === "error" ? 9000 : 3500);
}

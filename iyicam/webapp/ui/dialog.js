/* iyiCAM web: dialogs.  A modal one dims the window; a modeless one (the toolpath window) sits beside the drawing so that
 * the drawing can still be used while it is open, as the desktop's does.  On a narrow screen both fill the screen. */
import { h, clear } from "./dom.js";

let zTop = 100;

/** `dialog({title, body, buttons: [{label, primary, value, keep}], width, modeless, onKey})` -> `{el, close(value), done}`.
 *  `done` resolves with the value of the button pressed (undefined when it was closed).  A button with `keep: true` runs
 *  its `onclick` and does not close; `onclick` may return false to refuse the close. */
export function dialog(o) {
  let finish;
  const done = new Promise(r => { finish = r; });
  const buttons = (o.buttons || [{ label: "Close", primary: true, value: true }]);
  const bar = h("div", { class: "dlg-buttons" });
  const body = h("div", { class: "dlg-body" }, o.body);
  const card = h("div", { class: "dlg " + (o.className || ""), role: "dialog", "aria-modal": o.modeless ? null : "true", style: { width: o.width ? o.width + "px" : null } },
    h("div", { class: "dlg-title" }, h("span", null, o.title || ""), o.modeless ? null : null), body, buttons.length ? bar : null);
  const root = h("div", { class: "dlg-root" + (o.modeless ? " modeless" : "") }, card);
  root.style.zIndex = ++zTop;
  const api = { el: root, card, body, bar, done, close };
  function close(value) {
    if (!root.isConnected) return;
    root.remove();
    document.removeEventListener("keydown", onKey, true);
    o.onClose && o.onClose(value);
    finish(value);
  }
  for (const b of buttons) {
    const btn = h("button", { type: "button", class: "btn" + (b.primary ? " primary" : ""), disabled: !!b.disabled, title: b.title || "",
      onclick: async () => {
        if (b.onclick) { const r = await b.onclick(api); if (r === false) return; }
        if (!b.keep) close(b.value);
      } }, b.label);
    b.el = btn;
    bar.append(btn);
  }
  api.buttons = buttons;
  function onKey(e) {
    if (root !== topRoot()) return;
    if (e.key === "Escape") { e.stopPropagation(); e.preventDefault(); close(undefined); }
    else if (e.key === "Enter" && !e.shiftKey && !(e.target instanceof HTMLTextAreaElement) && !(e.target instanceof HTMLButtonElement) && !e.isComposing) {
      const p = buttons.find(b => b.primary);
      if (p && !p.disabled) { e.preventDefault(); p.el.click(); }
    }
    o.onKey && o.onKey(e);
  }
  document.addEventListener("keydown", onKey, true);
  document.body.append(root);
  setTimeout(() => { const f = card.querySelector("[autofocus], input:not([type=checkbox]):not([hidden]), select"); f && f.focus && f.focus(); if (f && f.select && f.type === "text") f.select(); }, 30);
  return api;
}
const topRoot = () => { const all = [...document.querySelectorAll(".dlg-root")]; return all.sort((a, b) => a.style.zIndex - b.style.zIndex).pop(); };

export async function alertBox(message, title = "iyiCAM") {
  await dialog({ title, body: h("p", { class: "dlg-text" }, message), buttons: [{ label: "OK", primary: true, value: true }] }).done;
}
export async function confirmBox(message, { title = "iyiCAM", yes = "OK", no = "Cancel" } = {}) {
  return !!(await dialog({ title, body: h("p", { class: "dlg-text" }, message), buttons: [{ label: no, value: false }, { label: yes, primary: true, value: true }] }).done);
}
/** three-way question: "yes" / "no" / undefined (cancel) */
export async function askBox(message, { title = "iyiCAM", yes = "Yes", no = "No" } = {}) {
  return dialog({ title, body: h("p", { class: "dlg-text" }, message), buttons: [{ label: "Cancel", value: undefined }, { label: no, value: "no" }, { label: yes, primary: true, value: "yes" }] }).done;
}
export async function promptBox(label, value = "", title = "iyiCAM") {
  const inp = h("input", { type: "text", value, autofocus: true });
  const r = await dialog({ title, body: h("label", { class: "row" }, h("span", { class: "lab" }, label), inp), width: 360,
    buttons: [{ label: "Cancel", value: undefined }, { label: "OK", primary: true, value: "ok" }] }).done;
  return r === "ok" ? inp.value : null;
}

/** tabs inside a dialog: `tabs([["Settings", node], ...])` */
export function tabs(items, onSwitch) {
  const heads = h("div", { class: "tabs" });
  const pages = h("div", { class: "tabpages" });
  const els = items.map(([name, node], i) => {
    const b = h("button", { type: "button", class: "tab", onclick: () => show(i) }, name);
    const p = h("div", { class: "tabpage" }, node);
    heads.append(b); pages.append(p);
    return { b, p };
  });
  function show(i) {
    els.forEach((e, k) => { e.b.classList.toggle("on", k === i); e.p.hidden = k !== i; });
    onSwitch && onSwitch(i);
  }
  show(0);
  return { el: h("div", { class: "tabbed" }, heads, pages), show };
}
export { clear };

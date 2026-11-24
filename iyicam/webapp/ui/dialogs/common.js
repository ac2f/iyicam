/* iyiCAM web: what the dialogs have in common: a dialog that is a form of params, and the report of a command. */
import { h } from "../dom.js";
import { dialog } from "../dialog.js";
import { ParamForm } from "../forms.js";
import { store } from "../dom.js";

/** A dialog that is a form: resolves the values (a dict) when OK is pressed, null when cancelled.
 *  `extra`: a node above the form; `validate(values)` returns a text to refuse the close; `remember`: a key under which the
 *  values are kept in the browser for the next time (a convenience only). */
export async function formDialog({ title, params, values = {}, ok = "OK", width = 440, collapse = false, extra, validate, remember, tools, description, buttons = [], afterBuild, footer }) {
  const saved = remember ? store.get("iyicam.form." + remember, {}) : {};
  const form = new ParamForm(params, { ...saved, ...values }, { collapse, tools });
  const warn = h("div", { class: "dlg-warn", hidden: true });
  afterBuild && afterBuild(form);
  const dlg = dialog({ title, width, className: "formdlg",
    body: h("div", null, description ? h("p", { class: "dlg-text" }, description) : null, extra, h("div", { class: "scrollform" }, form.el), footer, warn),
    buttons: [...buttons, { label: "Cancel", value: null }, { label: ok, primary: true, value: "ok",
      onclick: () => {
        const msg = validate && validate(form.values());
        if (msg) { warn.textContent = msg; warn.hidden = false; return false; }
      } }] });
  setTimeout(() => form.focusFirst(), 40);
  const r = await dlg.done;
  if (r !== "ok") return null;
  const v = form.values();
  if (remember) store.set("iyicam.form." + remember, v);
  return v;
}

/** the report a command hands back: sections of rows, warnings and messages */
export function reportDialog(result, title) {
  const body = h("div", { class: "report" });
  if (result.title) body.append(h("h3", null, result.title));
  for (const m of result.messages || []) body.append(h("p", null, m));
  for (const sec of result.report || []) {
    body.append(h("h4", null, sec.title || ""));
    if (sec.rows && sec.rows.length) body.append(h("table", { class: "kv" }, sec.rows.map(r => h("tr", null, r.map(c => h("td", null, String(c)))))));
    if (sec.text) body.append(h("pre", null, sec.text));
  }
  for (const w of result.warnings || []) body.append(h("p", { class: "dlg-warn" }, w));
  return dialog({ title: title || result.title || "Result", body, width: 480, buttons: [{ label: "Close", primary: true, value: true }] }).done;
}

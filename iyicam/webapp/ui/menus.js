/* iyiCAM web: the menu bar, built from menudef.js (the desktop's menus).  An item without an action is greyed with
 * "Not on the web yet"; the Edit menu says what Undo / Redo would undo; Open Recent and Window are filled when opened. */
import { h, fill } from "./dom.js";
import { app, setCur, settings } from "./state.js";
import { MENUS } from "./menudef.js";
import * as A from "./actions.js";

let openTop = null, bar = null, drop = null;

function itemRow(it, close) {
  if (it === "-") return h("div", { class: "msep" });
  const r = A.registry.get(it.id);
  let text = it.text.replace(/\.\.\.$/, "…");
  if (it.id === "edit.undo") { const l = app.cur && app.cur.undoLabel(); text = l ? `Undo ${l}` : "Undo"; }
  if (it.id === "edit.redo") { const l = app.cur && app.cur.redoLabel(); text = l ? `Redo ${l}` : "Redo"; }
  const dynamic = it.id === "file.open_recent" || it.id === "window" || it.menu;
  const implemented = !!r || (it.menu && it.menu.some(x => x !== "-")) || it.id === "file.open_recent";
  const enabled = r ? (!r.enabled || r.enabled()) : it.id === "file.open_recent" ? app.recent.length > 0 : !!it.menu;
  const checked = r && r.checked ? r.checked() : false;
  const row = h("div", { class: "mitem" + (enabled ? "" : " disabled") + (it.menu ? " has-sub" : "") + (checked ? " checked" : ""), title: !r && !it.menu ? A.notYet : "", role: "menuitem" },
    h("span", { class: "mcheck" }, it.check ? (checked ? "✓" : "") : ""), h("span", { class: "mtext" }, text), it.sc && !it.menu ? h("span", { class: "msc" }, it.sc) : null, it.menu ? h("span", { class: "marrow" }, "▸") : null);
  void dynamic; void implemented;
  if (it.menu) {
    const sub = h("div", { class: "menu submenu" }, subItems(it).map(x => itemRow(x, close)));
    row.append(sub);
    row.addEventListener("click", e => { if (e.target.closest(".submenu")) return; row.classList.toggle("open"); e.stopPropagation(); });
    row.addEventListener("mouseenter", () => { if (matchMedia("(hover: hover)").matches) { for (const s of row.parentElement.children) s.classList.remove("open"); row.classList.add("open"); } });
    if (!enabled) row.classList.remove("has-sub");
  } else if (enabled && r) row.addEventListener("click", e => { e.stopPropagation(); close(); A.runItem(it.id); });
  else if (!r) row.addEventListener("click", e => { e.stopPropagation(); A.status(A.notYet); });
  return row;
}
function subItems(it) {
  if (it.id === "file.open_recent") {
    if (!app.recent.length) return [{ id: "none", text: "(none)" }];
    return app.recent.map(r => { const id = "recent." + r.path; A.registry.set(id, { run: () => A.openRecent(r) }); return { id, text: r.name }; });
  }
  return it.menu;
}
function topItems(m) {
  if (m.id === "window") {
    return app.docs.map(d => { const id = "window." + d.id; A.registry.set(id, { run: () => setCur(d), checked: () => app.cur === d }); return { id, text: d.name + (d.dirty ? " •" : ""), check: true }; })
      .concat(app.docs.length ? [] : [{ id: "window.none", text: "(no project open)" }]);
  }
  return m.menu;
}
function close() { if (drop) { drop.remove(); drop = null; } openTop = null; bar.querySelectorAll(".mtop").forEach(b => b.classList.remove("on")); }
function open(m, btn) {
  if (drop) drop.remove();
  openTop = m.id;
  bar.querySelectorAll(".mtop").forEach(b => b.classList.toggle("on", b === btn));
  drop = h("div", { class: "menu dropdown", role: "menu" }, topItems(m).map(it => itemRow(it, close)));
  const r = btn.getBoundingClientRect();
  document.body.append(drop);
  const w = drop.offsetWidth;
  Object.assign(drop.style, { left: Math.max(4, Math.min(r.left, innerWidth - w - 4)) + "px", top: r.bottom + "px" });
}

export function buildMenubar(host) {
  bar = host;
  fill(host, MENUS.map(m => h("button", { type: "button", class: "mtop", dataset: { id: m.id },
    onclick: e => { e.stopPropagation(); openTop === m.id ? close() : open(m, e.currentTarget); },
    onmouseenter: e => { if (openTop && openTop !== m.id) open(m, e.currentTarget); } }, m.text)));
  document.addEventListener("pointerdown", e => { if (drop && !drop.contains(e.target) && !bar.contains(e.target)) close(); }, true);
  document.addEventListener("keydown", e => { if (e.key === "Escape" && drop) { close(); e.stopPropagation(); } }, true);
  window.addEventListener("blur", close);
  void settings;
}

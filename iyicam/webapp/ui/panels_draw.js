/* iyiCAM web: the Vector Library panel (the desktop's VectorLibraryPanel): groups in groups of drawings kept to be put in again.
 * The library itself is the engine's (`veclib` op, `vector_library.json` in the user folder, shared by every project); the panel
 * shows its tree, a picture of the drawing that is selected, and asks the engine for every change. */
import { h, fill, store, toast } from "./dom.js";
import { app } from "./state.js";
import { icon } from "./icons.js";
import { run, report } from "./engine.js";
import { promptBox, confirmBox } from "./dialog.js";
import { openFile, saveWhere, saveOnDevice, writeAgent, textBytes } from "./files.js";
import * as A from "./actions.js";

const tbtn = (ic, title, fn) => h("button", { type: "button", class: "tb", title, "aria-label": title, onclick: fn }, icon(ic, 16));
const fmtNum = v => String(+Number(v).toFixed(2));

/** what feat_draw.js and the menu call: `libraryApi.keep()` keeps the selection in the library */
export const libraryApi = { keep: async () => {} };

export function vectorLibraryPanel() {
  let tree = { id: "root", name: "", groups: [], items: [] };
  let selected = null;                                     // id of the group or drawing that is selected
  let loaded = false;
  const open = new Set(store.get("iyicam.veclib.open", []));
  const search = h("input", { type: "search", placeholder: "Search the library...", "aria-label": "Search the library", oninput: () => draw() });
  const list = h("div", { class: "tree vtree", role: "tree" });
  const pic = h("canvas", { class: "vlpic", width: 208, height: 208 });
  const info = h("div", { class: "muted vlinfo" });
  const own = h("input", { type: "checkbox", id: "vlown" });
  const more = h("div", { class: "vlmore" });

  const find = (id, g = tree, parent = null) => {
    if (g.id === id) return [g, parent];
    for (const s of g.groups) { const r = find(id, s, g); if (r) return r; }
    const it = g.items.find(i => i.id === id);
    return it ? [it, g] : null;
  };
  const isGroup = n => !!n && "groups" in n;
  const count = g => g.items.length + g.groups.reduce((n, s) => n + count(s), 0);
  /** the group that a new group or drawing goes into: the group selected, or the group of the drawing selected */
  const groupForNew = () => {
    const f = selected && find(selected);
    if (!f) return "root";
    return isGroup(f[0]) ? f[0].id : (f[1] ? f[1].id : "root");
  };

  async function call(payload, label = "Vector library") {
    try { const r = await run("veclib", payload, { label }); if (r.tree) { tree = r.tree; loaded = true; } return r; }
    catch (e) { report(e, "Vector library"); return null; }
  }
  async function refresh(select = null) {
    const r = await call({ action: "tree" });
    if (!r) return;
    if (select) selected = select;
    if (selected && !find(selected)) selected = null;
    draw();
  }

  // -- the tree
  function visible(g, q) { return !q || g.name.toLowerCase().includes(q) || g.items.some(i => i.name.toLowerCase().includes(q)) || g.groups.some(s => visible(s, q)); }
  function draw() {
    const q = search.value.trim().toLowerCase();
    const rows = [];
    const walk = (g, depth) => {
      for (const s of g.groups) {
        if (!visible(s, q)) continue;
        const expanded = q ? true : open.has(s.id);
        rows.push(h("div", { class: "trow vgroup" + (s.id === selected ? " on" : ""), dataset: { id: s.id, kind: "group" }, style: { paddingLeft: 6 + depth * 14 + "px" }, draggable: true,
          onclick: () => { selected = s.id; draw(); showPicture(); },
          ondblclick: () => { if (open.has(s.id)) open.delete(s.id); else open.add(s.id); store.set("iyicam.veclib.open", [...open]); draw(); } },
          h("span", { class: "vtoggle", onclick: e => { e.stopPropagation(); if (open.has(s.id)) open.delete(s.id); else open.add(s.id); store.set("iyicam.veclib.open", [...open]); draw(); } }, expanded ? "▾" : "▸"),
          icon("folder", 14), h("span", { class: "vname" }, `${s.name}  (${count(s)})`)));
        if (expanded) walk(s, depth + 1);
      }
      for (const it of g.items) {
        if (q && !it.name.toLowerCase().includes(q) && !g.name.toLowerCase().includes(q)) continue;
        rows.push(h("div", { class: "trow vitem" + (it.id === selected ? " on" : ""), dataset: { id: it.id, kind: "item" }, style: { paddingLeft: 20 + depth * 14 + "px" }, draggable: true,
          title: `${it.name}: ${it.vectors} vector(s), ${fmtNum(it.size[0])} × ${fmtNum(it.size[1])} mm. Double click: put it in the sheet`,
          onclick: () => { selected = it.id; draw(); showPicture(); }, ondblclick: () => insert() },
          h("span", { class: "vname" }, it.name), h("span", { class: "vsize muted" }, `${fmtNum(it.size[0])} × ${fmtNum(it.size[1])}`)));
      }
    };
    walk(tree, 0);
    fill(list, rows.length ? rows : h("div", { class: "muted vlempty" }, loaded ? "The library is empty. Select vectors and press +▣ to keep them here." : "Loading the library…"));
    for (const r of list.querySelectorAll(".trow")) wireDrag(r);
    showInfo();
  }
  // dragging: a drawing or a group onto a group (into it), onto a drawing (next to it)
  function wireDrag(row) {
    row.addEventListener("dragstart", e => { e.dataTransfer.setData("text/plain", row.dataset.id); e.dataTransfer.effectAllowed = "move"; });
    row.addEventListener("dragover", e => { e.preventDefault(); row.classList.add("drop"); });
    row.addEventListener("dragleave", () => row.classList.remove("drop"));
    row.addEventListener("drop", async e => {
      e.preventDefault(); row.classList.remove("drop");
      const id = e.dataTransfer.getData("text/plain"), target = find(row.dataset.id);
      if (!id || !target || id === row.dataset.id) return;
      const into = isGroup(target[0]) ? target[0].id : (target[1] ? target[1].id : "root");
      const index = isGroup(target[0]) ? null : target[1].items.findIndex(i => i.id === row.dataset.id);
      if (isGroup(target[0])) { open.add(target[0].id); store.set("iyicam.veclib.open", [...open]); }
      await call({ action: "move", id, to: into, index }); selected = id; draw();
    });
  }

  // -- the picture of the drawing
  let pictureFor = null;
  async function showPicture() {
    const f = selected && find(selected), ctx = pic.getContext("2d");
    ctx.clearRect(0, 0, pic.width, pic.height);
    pictureFor = selected;
    if (!f || isGroup(f[0])) { pic.hidden = true; showInfo(); return; }
    pic.hidden = false;
    try {
      const r = await app.engine.call("veclib", { action: "preview", id: selected });
      if (pictureFor !== selected) return;
      const [w, hh] = r.size, S = pic.width, k = (S - 24) / Math.max(w, hh, 1e-6), ox = (S - w * k) / 2, oy = (S + hh * k) / 2;
      ctx.strokeStyle = getComputedStyle(document.body).getPropertyValue("--accent") || "#2a6fdb"; ctx.lineWidth = 2.4; ctx.lineJoin = "round";
      r.paths.forEach((pts, i) => { ctx.beginPath(); pts.forEach(([x, y], j) => (j ? ctx.lineTo(ox + x * k, oy - y * k) : ctx.moveTo(ox + x * k, oy - y * k))); if (r.closed[i]) ctx.closePath(); ctx.stroke(); });
    } catch { /* the picture is a glance: no picture is no harm */ }
    showInfo();
  }
  function showInfo() {
    const f = selected && find(selected);
    if (!f) { info.textContent = `${count(tree)} drawing(s) in the library`; return; }
    if (isGroup(f[0])) { info.textContent = `${count(f[0])} drawing(s) in this group`; return; }
    const path = []; let p = f[1];
    while (p && p.id !== "root") { path.unshift(p.name); const up = find(p.id); p = up && up[1]; }
    info.textContent = `${path.join(" ▸ ") || "(top level)"}  ·  ${f[0].vectors} vector(s)`;
  }

  // -- what is done
  async function newGroup() {
    const parent = groupForNew(), pf = find(parent);
    const name = await promptBox(`Name of the group${pf && parent !== "root" ? " in " + pf[0].name : ""}:`, "", "New group");
    if (!name || !name.trim()) return;
    const r = await call({ action: "add_group", name, parent });
    if (r) { if (parent !== "root") open.add(parent); store.set("iyicam.veclib.open", [...open]); selected = r.id; draw(); showPicture(); }
  }
  async function keep() {
    const d = app.cur;
    if (!d || !d.selection.length) { A.status("Select the vectors to keep in the library first."); toast("Select the vectors to keep in the library first."); return; }
    if (!loaded) await refresh();
    const first = d.selected()[0];
    const name = await promptBox(`Name of the drawing (${d.selection.length} vector(s)):`, first && first.name || "", "Keep in the library");
    if (name === null) return;
    const group = groupForNew();
    const r = await call({ action: "add_item", project: d.project, sheet: d.sheetId, selection: d.selection, name, group });
    if (r) { if (group !== "root") open.add(group); store.set("iyicam.veclib.open", [...open]); selected = r.id; draw(); showPicture(); A.status(`Kept "${name || "Drawing"}" in the library`); }
  }
  /** the drawing selected, in the middle of what the drawing shows (its vectors selected: one step of Undo) */
  async function insert() {
    const d = app.cur, f = selected && find(selected);
    if (!d) return;
    if (!f || isGroup(f[0])) { A.status("Select a drawing of the library (not a group)."); toast("Select a drawing of the library (not a group)."); return; }
    const v = app.ui.canvas, { w, h: hh } = v.size, [cx, cy] = v.s2w(22 + (w - 22) / 2, 22 + (hh - 22) / 2), [iw, ih] = f[0].size;
    const r = await call({ action: "insert", project: d.project, sheet: d.sheetId, layer: d.layerId, id: selected, at: [cx - iw / 2, cy - ih / 2], own_layers: own.checked }, "Library: " + f[0].name);
    if (r && r.project) d.replace("Library: " + f[0].name, r.project, r.selection);
  }
  async function rename() {
    const f = selected && find(selected);
    if (!f || selected === "root") return;
    const name = await promptBox("New name:", f[0].name, "Rename");
    if (name && name.trim()) { await call({ action: "rename", id: selected, name }); draw(); }
  }
  async function remove() {
    const f = selected && find(selected);
    if (!f) return;
    const what = isGroup(f[0]) ? `the group '${f[0].name}' and the ${count(f[0])} drawing(s) in it` : `the drawing '${f[0].name}'`;
    if (!(await confirmBox(`Delete ${what}?`, { title: "Vector Library", yes: "Delete" }))) return;
    await call({ action: "remove", id: selected }); selected = null; draw(); showPicture();
  }
  async function shift(step) { if (selected) { await call({ action: "shift", id: selected, step }); draw(); } }
  async function toTop() { if (selected) { await call({ action: "to_top", id: selected }); draw(); } }
  async function importLib() {
    const f = await openFile({ title: "Import a vector library", accept: ".icamlib,.json", exts: ["icamlib", "json"] });
    if (!f) return;
    let data;
    try { data = JSON.parse(new TextDecoder().decode(f.bytes)); } catch (e) { toast("This is not a vector library: " + e.message, "error"); return; }
    const r = await call({ action: "import", data, group: groupForNew(), name: f.name.replace(/\.[^.]+$/, "") });
    if (r) { selected = r.id; draw(); showPicture(); }
  }
  async function exportLib() {
    const f = selected && find(selected), id = selected || "root";
    try {
      const r = await app.engine.call("veclib", { action: "export", id });
      const name = ((f && f[0].name) || "library") + ".icamlib", bytes = textBytes(JSON.stringify(r.data));
      const w = await saveWhere({ title: "Export from the vector library", name, ext: "icamlib" });
      if (!w) return;
      if (w.where === "agent") await writeAgent(w.path, bytes); else await saveOnDevice(name, bytes, "application/json", null);
      A.status(`Exported ${name}`);
    } catch (e) { report(e, "Vector library"); }
  }

  const menu = h("button", { type: "button", class: "tb", title: "Rename, move up / down, import, export ...", "aria-label": "More", onclick: e => {
    const items = [["Rename...", rename], ["Move up", () => shift(-1)], ["Move down", () => shift(1)], "-", ["Import a library or a group...", importLib], ["Export the group that is selected...", exportLib], ["Move to the top level", toTop]];
    document.querySelectorAll(".popmenu.draw").forEach(m => m.remove());
    const m = h("div", { class: "popmenu draw" }, items.map(it => (it === "-" ? h("div", { class: "psep" }) : h("div", { class: "item", onclick: () => { m.remove(); it[1](); } }, it[0]))));
    document.body.append(m);
    const r = e.currentTarget.getBoundingClientRect();
    Object.assign(m.style, { left: Math.max(4, Math.min(r.left, innerWidth - m.offsetWidth - 4)) + "px", top: r.bottom + "px" });
    const away = ev => { if (m.contains(ev.target)) return; m.remove(); document.removeEventListener("pointerdown", away, true); };
    setTimeout(() => document.addEventListener("pointerdown", away, true), 0);
  } }, "⋯");

  const el = h("div", { class: "panel veclib" },
    h("div", { class: "btnrow" }, tbtn("plus", "New group (in the group that is selected: a subgroup)", newGroup), tbtn("copy", "Keep the selected vectors in the library (in the group that is selected)", keep),
      tbtn("down", "Put the drawing in the open sheet (double click does it too)", insert), tbtn("minus", "Delete", remove), menu),
    search, h("div", { class: "listbox vlist" }, list), pic, info,
    h("label", { class: "row check", title: "Put a drawing on the layers it came from (made when missing); off: on the current layer" }, own, h("span", null, "On the layers it came from")), more);
  pic.hidden = true;
  libraryApi.keep = keep;
  libraryApi.refresh = refresh;
  libraryApi.tree = () => tree;
  setTimeout(() => refresh().catch(() => {}), 0);
  return { el, update() { /* the library does not depend on the project */ } };
}

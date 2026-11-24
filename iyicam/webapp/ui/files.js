/* iyiCAM web: files of this device (a picker, a download) and files of the workshop PC (the agent, through the files API
 * behind a file browser).  Without an agent only the device is offered. */
import { h, fill } from "./dom.js";
import { dialog, confirmBox } from "./dialog.js";
import { files as filesApi } from "./engine.js";
import { icon } from "./icons.js";

/** bytes to a base64 string and back (the engine's way of carrying bytes) */
export function toB64(bytes) {
  let s = "";
  const CH = 0x8000;
  for (let i = 0; i < bytes.length; i += CH) s += String.fromCharCode.apply(null, bytes.subarray(i, i + CH));
  return btoa(s);
}
export function fromB64(b64) {
  const s = atob(b64), out = new Uint8Array(s.length);
  for (let i = 0; i < s.length; i++) out[i] = s.charCodeAt(i);
  return out;
}
export const textBytes = t => new TextEncoder().encode(t);

let agentStatus = null;
/** `{agent: {name} | null, roots}`: whether there is a workshop PC to browse */
export async function agent(refresh = false) {
  if (agentStatus && !refresh) return agentStatus;
  const F = await filesApi();
  if (!F) return (agentStatus = { agent: null, roots: [] });
  try {
    const st = await F.status();
    agentStatus = { agent: st.agent && st.agent.connected !== false ? st.agent : (st.roots && st.roots.length ? { name: "this computer" } : null), roots: st.roots || [] };
  } catch { agentStatus = { agent: null, roots: [] }; }
  return agentStatus;
}

// ------------------------------------------------------------------------------------------ this device
/** pick files of this device: `[{name, bytes}]` (empty when cancelled) */
export function pickDeviceFiles(accept = "", multiple = false) {
  return new Promise(resolve => {
    const inp = h("input", { type: "file", accept, multiple, style: { display: "none" } });
    inp.addEventListener("change", async () => {
      const out = [];
      for (const f of inp.files) out.push({ name: f.name, bytes: new Uint8Array(await f.arrayBuffer()) });
      inp.remove(); resolve(out);
    });
    inp.addEventListener("cancel", () => { inp.remove(); resolve([]); });
    document.body.append(inp);
    inp.click();
  });
}
export function download(name, data, mime = "application/octet-stream") {
  const blob = new Blob([data], { type: mime });
  const a = h("a", { href: URL.createObjectURL(blob), download: name, style: { display: "none" } });
  document.body.append(a); a.click();
  setTimeout(() => { URL.revokeObjectURL(a.href); a.remove(); }, 2000);
}
/** a real Save (the same file again) where the browser allows it; returns the handle, or null (a download was made) */
export async function saveOnDevice(name, data, mime, handle) {
  if (window.showSaveFilePicker) {
    try {
      handle = handle || await window.showSaveFilePicker({ suggestedName: name, types: [{ description: name.split(".").pop().toUpperCase(), accept: { [mime]: ["." + name.split(".").pop()] } }] });
      const w = await handle.createWritable(); await w.write(data); await w.close();
      return handle;
    } catch (e) { if (e && e.name === "AbortError") throw e; }
  }
  download(name, data, mime);
  return null;
}

// ------------------------------------------------------------------------------------------ the workshop PC
const base = p => String(p).split(/[\\/]/).pop();
const joinPath = (dir, name) => (dir ? dir.replace(/[\\/]+$/, "") + (dir.includes("\\") && !dir.includes("/") ? "\\" : "/") + name : name);
const extOf = n => (n.includes(".") ? n.split(".").pop().toLowerCase() : "");

/** a file browser on the roots of the agent.  `mode`: "open" resolves `{path, name}`, "save" resolves `{path}`
 *  (the folder and the name typed), "folder" resolves `{path}` of the folder.  `exts`: the extensions shown (all when empty). */
export async function browse({ mode = "open", title = "Workshop PC", exts = [], name = "", ext = "" }) {
  const F = await filesApi();
  let cwd = "", stack = [];
  const list = h("div", { class: "fb-list" });
  const crumbs = h("div", { class: "fb-path" });
  const nameInp = h("input", { type: "text", value: name, placeholder: "File name" });
  let chosen = null;
  const msg = h("div", { class: "fb-msg" });
  async function load(path) {
    fill(msg, "Loading…");
    try {
      const items = await F.list(path);
      cwd = path;
      fill(msg, "");
      const dirs = items.filter(i => i.dir).sort((a, b) => a.name.localeCompare(b.name));
      const fl = items.filter(i => !i.dir && (!exts.length || exts.includes(extOf(i.name)))).sort((a, b) => a.name.localeCompare(b.name));
      fill(crumbs, h("span", { class: "fb-here" }, path || "Folders"));
      fill(list, stack.length ? row("..", "folder", () => { cwd = stack.pop(); load(cwd); }) : null,
        dirs.map(d => row(d.name, "folder", () => { stack.push(cwd); load(d.path); })),
        mode === "folder" ? null : fl.map(f => row(f.name, "file", () => { chosen = f; nameInp.value = f.name; mark(); }, () => { chosen = f; dlg.buttons.find(b => b.primary).el.click(); }, f)),
        !dirs.length && !fl.length ? h("div", { class: "fb-empty" }, "(empty)") : null);
      chosen = null; mark();
    } catch (e) { fill(msg, "Cannot list this folder: " + (e.message || e)); }
  }
  function row(text, ic, onclick, ondbl, f) {
    return h("div", { class: "fb-row", dataset: { name: text }, onclick, ondblclick: ondbl || onclick }, icon(ic, 16), h("span", { class: "n" }, text),
      f && f.size != null ? h("span", { class: "s" }, f.size > 1e6 ? (f.size / 1e6).toFixed(1) + " MB" : Math.max(1, Math.round(f.size / 1024)) + " kB") : null);
  }
  function mark() { for (const r of list.children) r.classList.toggle("on", !!chosen && r.dataset.name === chosen.name); }
  const body = h("div", { class: "filebrowser" }, crumbs, list, mode === "save" ? h("label", { class: "row" }, h("span", { class: "lab" }, "Name"), nameInp) : null, msg);
  const dlg = dialog({ title, body, width: 520, buttons: [{ label: "Cancel", value: undefined },
    { label: mode === "save" ? "Save here" : mode === "folder" ? "Choose this folder" : "Open", primary: true, value: "ok",
      onclick: async () => {
        if (mode === "open" && !chosen) return false;
        if (mode === "save") {
          let n = nameInp.value.trim();
          if (!n) return false;
          if (ext && extOf(n) !== ext) n += "." + ext;
          if (!cwd) { fill(msg, "Open a folder first."); return false; }
          nameInp.value = n;
        }
      } }] });
  load("");
  const r = await dlg.done;
  if (r !== "ok") return null;
  if (mode === "open") return { path: chosen.path, name: chosen.name };
  if (mode === "folder") return { path: cwd };
  return { path: joinPath(cwd, nameInp.value.trim()), name: nameInp.value.trim(), dir: cwd };
}

/** Where to open a file from: "This device" or the workshop PC.  Resolves `{name, bytes, path?}` or null. */
export async function openFile({ title = "Open", accept = "", exts = [] } = {}) {
  const st = await agent();
  if (!st.agent) { const f = await pickDeviceFiles(accept); return f[0] || null; }
  let choice;
  const dlg = dialog({ title, width: 380, body: h("div", { class: "choose" },
    h("button", { class: "btn big", onclick: async () => { const f = await pickDeviceFiles(accept); if (f[0]) { choice = { ...f[0] }; dlg.close("device"); } } }, icon("open", 18), " This device"),
    h("button", { class: "btn big", onclick: () => dlg.close("agent") }, icon("folder", 18), ` ${st.agent.name || "Workshop PC"}`)), buttons: [{ label: "Cancel", value: undefined }] });
  const r = await dlg.done;
  if (r === "device") return choice;
  if (r !== "agent") return null;
  const pick = await browse({ mode: "open", title: st.agent.name || "Workshop PC", exts });
  if (!pick) return null;
  const F = await filesApi();
  return { name: pick.name, path: pick.path, bytes: await F.read(pick.path) };
}

/** Where to save: this device (download) or the agent.  Resolves `{where: "device", name}` or `{where: "agent", path, name}` or null. */
export async function saveWhere({ title = "Save", name, ext, folderOnly = false }) {
  const st = await agent();
  if (!st.agent) return { where: "device", name };
  const dlg = dialog({ title, width: 380, body: h("div", { class: "choose" },
    h("button", { class: "btn big", onclick: () => dlg.close("device") }, icon("save", 18), " Download to this device"),
    h("button", { class: "btn big", onclick: () => dlg.close("agent") }, icon("folder", 18), ` Save to ${st.agent.name || "the workshop PC"}`)),
    buttons: [{ label: "Cancel", value: undefined }] });
  const r = await dlg.done;
  if (r === "device") return { where: "device", name };
  if (r !== "agent") return null;
  const p = await browse({ mode: folderOnly ? "folder" : "save", title: st.agent.name || "Workshop PC", name, ext });
  if (!p) return null;
  return { where: "agent", path: p.path, name: p.name || name, dir: p.dir || p.path };
}

/** write bytes to the agent, asking before an existing file is replaced */
export async function writeAgent(path, bytes) {
  const F = await filesApi();
  try { return await F.write(path, bytes, { overwrite: false }); } catch (e) {
    if (!/exist/i.test(String(e && (e.code || e.message)))) throw e;
    if (!(await confirmBox(`${base(path)} exists. Replace it?`, { yes: "Replace" }))) throw Object.assign(new Error("cancelled"), { code: "cancelled" });
    return F.write(path, bytes, { overwrite: true });
  }
}
export { joinPath, base as baseName };

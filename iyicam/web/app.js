"use strict";
/* iyiCAM in a browser: opens a drawing, defines toolpaths, calculates, writes G-code.  Everything that needs the
   engine goes through /api/<operation> (the service of iyicam); the project lives here, as data. */
(() => {

// ------------------------------------------------------------------------------------------ text
const TXT = {
  tr: {
    "tab.project": "Proje", "tab.draw": "Çizim", "tab.paths": "Yollar", "tab.out": "Çıktı",
    "login.hint": "Devam etmek için anahtarını gir.", "login.key": "Anahtar", "login.go": "Giriş",
    "login.bad": "Anahtar yanlış.", "login.wait": "Çok fazla yanlış deneme. {0} sn bekle.",
    "logout": "Çıkış", "busy": "Çalışıyor…", "cancel": "Vazgeç", "cancelled": "İptal edildi.",
    "open.title": "Dosya aç", "open.hint": "iyiCAM projesi (.icam), DXF, SVG, HPGL, CorelDRAW…",
    "open.pick": "Bu cihazdan seç…", "open.thickness": "Malzeme kalınlığı (mm)", "open.add": "Açık projeye ekle",
    "open.done": "{0} açıldı.", "report.title": "Dosyadan okunan", "report.notes": "Notlar", "report.errors": "Hatalar",
    "proj.none": "Henüz proje yok", "proj.none2": "Yukarıdan bir çizim dosyası aç.",
    "proj.name": "Proje adı", "proj.sheets": "Tezgahlar", "proj.layers": "Katmanlar", "proj.material": "Malzeme",
    "proj.vectors": "{0} vektör", "proj.width": "Genişlik (mm)", "proj.height": "Yükseklik (mm)",
    "proj.thickness": "Kalınlık (mm)", "proj.safez": "Güvenli Z (mm)", "proj.clear": "Dalış payı (mm)",
    "proj.zzero": "Z sıfırı", "proj.top": "Malzeme üstü", "proj.bottom": "Tezgah yüzeyi",
    "proj.origin": "XY başlangıcı", "proj.save": ".icam olarak kaydet", "proj.close": "Projeyi kapat",
    "proj.closeask": "Proje kapatılsın mı? (Kaydetmediysen kaybolur.)",
    "draw.fit": "Sığdır", "draw.paths": "Takım yolları", "draw.none": "Çizilecek bir şey yok.",
    "paths.title": "Takım yolları", "paths.add": "Yeni yol", "paths.none": "Henüz takım yolu yok.",
    "paths.none2": "Bir işlem seç (profil, cep, delik…), takımı ve katmanları belirle.",
    "paths.new": "Yeni takım yolu", "paths.edit": "Takım yolunu düzenle", "paths.name": "Ad",
    "paths.op": "İşlem", "paths.tool": "Takım", "paths.layers": "İşlenecek katmanlar", "paths.toolvals": "Takım ayarları",
    "paths.params": "İşlem ayarları", "paths.save": "Kaydet", "paths.delete": "Sil", "paths.cancel": "Vazgeç",
    "paths.nolayer": "En az bir katman seç.", "paths.on": "Açık", "paths.none_tool": "(yok)",
    "tool.diameter": "Çap (mm)", "tool.feed_rate": "İlerleme (mm/dk)", "tool.plunge_rate": "Dalış (mm/dk)",
    "tool.spindle_speed": "Devir (dev/dk)", "tool.stepdown": "Paso derinliği (mm)", "tool.stepover": "Yanal adım (mm)",
    "tool.number": "Takım no",
    "out.post": "G-code biçimi (post)", "out.compute": "Hesapla", "out.gcode": "G-code üret", "out.sim": "Simülasyon",
    "out.export": "Dışa aktar", "out.needpaths": "Önce Yollar sekmesinden bir takım yolu ekle.",
    "out.results": "Hesap sonucu", "out.show": "Çizimde göster", "out.time": "süre", "out.cut": "kesim",
    "out.files": "G-code dosyaları", "out.download": "İndir", "out.view": "Göster", "out.hide": "Gizle",
    "out.lines": "{0} satır", "out.errors": "Hatalar", "out.warnings": "Uyarılar", "out.nogcode": "G-code üretilemedi.",
    "out.simdone": "Malzeme kaldırma, {0}", "out.stale": "Proje değişti; yeniden hesapla.",
    "err.network": "Sunucuya ulaşılamıyor.", "err.session": "Oturum bitti; anahtarı yeniden gir.",
    "e.unsupported_file": "Bu dosya türü okunamıyor.", "e.empty_file": "Dosya boş.", "e.too_large": "Dosya çok büyük.",
    "e.invalid_project": "Geçerli bir iyiCAM projesi değil.", "e.too_busy": "Sunucu meşgul, biraz sonra dene.",
    "files.open": "Bilgisayardan aç…", "files.openTitle": "Bilgisayardan dosya aç", "files.saveTitle": "Projeyi kaydet",
    "files.folderTitle": "Klasör seç", "files.up": "Üst klasör", "files.roots": "Klasörler", "files.empty": "Burada dosya yok.",
    "files.nofolders": "Alt klasör yok.", "files.name": "Dosya adı", "files.chooseHere": "Bu klasörü seç", "files.save": "Kaydet",
    "files.saveTo": "Bilgisayara kaydet…", "files.saveHere": "Kaydet", "files.saveAs": "Farklı kaydet…",
    "files.saved": "{0} dosya yazıldı: {1}", "files.savedProject": "Kaydedildi: {0}",
    "files.overwrite": "Bu dosya var: {0}. Üzerine yazılsın mı?",
    "files.changed": "{0} bu arada değişti. Üzerine yazılsın mı?",
    "files.toComputer": "İndirmek yerine bilgisayara kaydet", "files.where": "Bilgisayarda", "files.opened": "{0} açıldı (bilgisayardan).",
    "e.path_not_allowed": "Bu klasör kullanılamaz.", "e.folder_not_found": "Klasör bulunamadı.",
    "e.file_not_found": "Dosya bulunamadı.", "e.file_exists": "Bu adda bir dosya var.", "e.file_changed": "Dosya bu arada değişti.",
    "e.file_too_large": "Dosya çok büyük.", "e.invalid_name": "Dosya adı geçerli değil.", "e.invalid_path": "Yol geçerli değil.",
    "e.files_disabled": "Bu sunucu dosyalara erişim vermiyor.", "e.payload_too_large": "Çok fazla veri.",
    "e.agent_offline": "Bilgisayardaki iyiCAM ajanı bağlı değil. O bilgisayarda ajanı başlat (iyicam agent), sonra yeniden dene.",
    "e.agent_timeout": "Bilgisayardaki ajan zamanında cevap vermedi.",
    "e.reply_too_large": "Cevap, sunucunun taşıyabileceğinden büyük.",
    "e.bad_agent_answer": "Ajanın cevabı okunamadı.",
    "e.os_error": "Dosya işlemi yapılamadı."
  },
  en: {
    "tab.project": "Project", "tab.draw": "Drawing", "tab.paths": "Toolpaths", "tab.out": "Output",
    "login.hint": "Enter your key to continue.", "login.key": "Key", "login.go": "Sign in",
    "login.bad": "The key is not right.", "login.wait": "Too many wrong tries. Wait {0} s.",
    "logout": "Sign out", "busy": "Working…", "cancel": "Cancel", "cancelled": "Cancelled.",
    "open.title": "Open a file", "open.hint": "iyiCAM project (.icam), DXF, SVG, HPGL, CorelDRAW…",
    "open.pick": "From this device…", "open.thickness": "Material thickness (mm)", "open.add": "Add to the open project",
    "open.done": "{0} opened.", "report.title": "Read from the file", "report.notes": "Notes", "report.errors": "Errors",
    "proj.none": "No project yet", "proj.none2": "Open a drawing file above.",
    "proj.name": "Project name", "proj.sheets": "Sheets", "proj.layers": "Layers", "proj.material": "Material",
    "proj.vectors": "{0} vectors", "proj.width": "Width (mm)", "proj.height": "Height (mm)",
    "proj.thickness": "Thickness (mm)", "proj.safez": "Safe Z (mm)", "proj.clear": "Plunge clearance (mm)",
    "proj.zzero": "Z zero", "proj.top": "Material top", "proj.bottom": "Machine bed",
    "proj.origin": "XY origin", "proj.save": "Save as .icam", "proj.close": "Close the project",
    "proj.closeask": "Close the project? (Unsaved work is lost.)",
    "draw.fit": "Fit", "draw.paths": "Toolpaths", "draw.none": "Nothing to draw.",
    "paths.title": "Toolpaths", "paths.add": "New toolpath", "paths.none": "No toolpaths yet.",
    "paths.none2": "Pick an operation (profile, pocket, drill…), the tool and the layers.",
    "paths.new": "New toolpath", "paths.edit": "Edit toolpath", "paths.name": "Name",
    "paths.op": "Operation", "paths.tool": "Tool", "paths.layers": "Layers to machine", "paths.toolvals": "Tool settings",
    "paths.params": "Operation settings", "paths.save": "Save", "paths.delete": "Delete", "paths.cancel": "Cancel",
    "paths.nolayer": "Pick at least one layer.", "paths.on": "On", "paths.none_tool": "(none)",
    "tool.diameter": "Diameter (mm)", "tool.feed_rate": "Feed (mm/min)", "tool.plunge_rate": "Plunge (mm/min)",
    "tool.spindle_speed": "Spindle (rpm)", "tool.stepdown": "Step down (mm)", "tool.stepover": "Step over (mm)",
    "tool.number": "Tool number",
    "out.post": "G-code format (post)", "out.compute": "Calculate", "out.gcode": "Write G-code", "out.sim": "Simulation",
    "out.export": "Export", "out.needpaths": "Add a toolpath on the Toolpaths tab first.",
    "out.results": "Result", "out.show": "Show on the drawing", "out.time": "time", "out.cut": "cutting",
    "out.files": "G-code files", "out.download": "Download", "out.view": "View", "out.hide": "Hide",
    "out.lines": "{0} lines", "out.errors": "Errors", "out.warnings": "Warnings", "out.nogcode": "No G-code could be made.",
    "out.simdone": "Material removal, {0}", "out.stale": "The project changed; calculate again.",
    "err.network": "Cannot reach the server.", "err.session": "Signed out; enter the key again.",
    "e.unsupported_file": "This kind of file cannot be read.", "e.empty_file": "The file is empty.",
    "e.too_large": "The file is too large.", "e.invalid_project": "Not a valid iyiCAM project.",
    "e.too_busy": "The server is busy, try again in a moment.",
    "files.open": "Open from the computer…", "files.openTitle": "Open a file from the computer", "files.saveTitle": "Save the project",
    "files.folderTitle": "Choose a folder", "files.up": "Parent folder", "files.roots": "Folders", "files.empty": "No files here.",
    "files.nofolders": "No sub-folders.", "files.name": "File name", "files.chooseHere": "Choose this folder", "files.save": "Save",
    "files.saveTo": "Save to the computer…", "files.saveHere": "Save", "files.saveAs": "Save as…",
    "files.saved": "{0} file(s) written to {1}", "files.savedProject": "Saved: {0}",
    "files.overwrite": "{0} exists. Replace it?",
    "files.changed": "{0} was changed in the meantime. Replace it?",
    "files.toComputer": "Save to the computer instead of downloading", "files.where": "On the computer", "files.opened": "{0} opened (from the computer).",
    "e.path_not_allowed": "This folder cannot be used.", "e.folder_not_found": "Folder not found.",
    "e.file_not_found": "File not found.", "e.file_exists": "A file with this name exists.", "e.file_changed": "The file was changed in the meantime.",
    "e.file_too_large": "The file is too large.", "e.invalid_name": "The file name is not valid.", "e.invalid_path": "The path is not valid.",
    "e.files_disabled": "This server gives no access to files.", "e.payload_too_large": "Too much data.",
    "e.agent_offline": "The iyiCAM agent on the computer is not connected. Start it there (iyicam agent), then try again.",
    "e.agent_timeout": "The agent on the computer did not answer in time.",
    "e.reply_too_large": "The answer is larger than the server can carry.",
    "e.bad_agent_answer": "The agent's answer could not be read.",
    "e.os_error": "The file operation failed."
  }
};
let lang = (() => { try { return localStorage.getItem("iyicam.lang"); } catch { return null; } })()
  || ((navigator.language || "").toLowerCase().startsWith("tr") ? "tr" : "en");
const t = (k, ...a) => (TXT[lang][k] ?? TXT.en[k] ?? k).replace(/\{(\d+)\}/g, (_, i) => a[i] ?? "");

// ------------------------------------------------------------------------------------------ small things
function h(tag, attrs, ...kids) {
  const el = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs || {})) {
    if (v === false || v == null) continue;
    if (k === "class") el.className = v;
    else if (k.startsWith("on")) el.addEventListener(k.slice(2), v);
    else if (["value", "checked", "disabled", "hidden", "selected"].includes(k)) el[k] = v;
    else el.setAttribute(k, v === true ? "" : v);
  }
  for (const c of kids.flat(Infinity)) {
    if (c == null || c === false) continue;
    el.append(c.nodeType ? c : document.createTextNode(String(c)));
  }
  return el;
}
const $ = (s, r = document) => r.querySelector(s);
const sleep = ms => new Promise(r => setTimeout(r, ms));
const clamp = (v, a, b) => Math.min(b, Math.max(a, v));
const fmt = (n, d = 1) => (Number.isFinite(n) ? n : 0).toLocaleString(lang === "tr" ? "tr-TR" : "en-US", { maximumFractionDigits: d });
const fmtTime = s => { s = Math.round(s || 0); const m = Math.floor(s / 60); return m >= 60 ? `${Math.floor(m / 60)}:${String(m % 60).padStart(2, "0")}:${String(s % 60).padStart(2, "0")}` : `${m}:${String(s % 60).padStart(2, "0")}`; };
const newId = () => Array.from(crypto.getRandomValues(new Uint8Array(6)), b => b.toString(16).padStart(2, "0")).join("");
const store = {
  get(k) { try { return localStorage.getItem(k); } catch { return null; } },
  set(k, v) { try { localStorage.setItem(k, v); } catch { /* private window, full: the page works without */ } },
  del(k) { try { localStorage.removeItem(k); } catch { /* same */ } },
};

function download(name, blob) {
  const a = h("a", { href: URL.createObjectURL(blob), download: name });
  document.body.append(a);
  a.click();
  setTimeout(() => { URL.revokeObjectURL(a.href); a.remove(); }, 5000);
}
function b64blob(b64, mime) {
  const bin = atob(b64), u = new Uint8Array(bin.length);
  for (let i = 0; i < bin.length; i++) u[i] = bin.charCodeAt(i);
  return new Blob([u], { type: mime });
}
function fileToB64(file) {
  return new Promise((ok, no) => {
    const r = new FileReader();
    r.onload = () => ok(String(r.result).split(",", 2)[1] || "");
    r.onerror = () => no(r.error);
    r.readAsDataURL(file);
  });
}

// ------------------------------------------------------------------------------------------ the server
class ApiError extends Error {
  constructor(body, status) { super(body.message || body.error || "error"); this.code = body.error; this.body = body; this.status = status; }
  get text() { const k = "e." + this.code; return TXT[lang][k] ? t(k) : (this.code === "network" ? t("err.network") : this.message); }
}
/** a body of some size goes gzipped (a drawing is mostly numbers: about a fifth of the size over the air) */
async function packBody(text) {
  const headers = { "Content-Type": "application/json" };
  if (text.length > 16000 && typeof CompressionStream === "function") {
    try {
      const gz = await new Response(new Blob([text]).stream().pipeThrough(new CompressionStream("gzip"))).arrayBuffer();
      return { body: gz, headers: { ...headers, "Content-Encoding": "gzip" } };
    } catch { /* the plain body goes */ }
  }
  return { body: text, headers };
}
async function api(op, payload) {
  let r;
  try {
    const { body: sent, headers } = await packBody(JSON.stringify(payload || {}));
    r = await fetch("/api/" + op, { method: "POST", credentials: "same-origin", headers, body: sent });
  } catch { throw new ApiError({ error: "network" }, 0); }
  let body;
  try { body = await r.json(); } catch { body = { error: "bad_answer", message: "HTTP " + r.status }; }
  if (r.status === 401 && body.error === "not_logged_in") { signedOut(); }
  if (body.error) throw new ApiError(body, r.status);
  return body;
}

/** a file as the body of a request (``application/octet-stream``), answered like any call */
async function apiRaw(path, file) {
  let r;
  try { r = await fetch(path, { method: "POST", credentials: "same-origin", headers: { "Content-Type": "application/octet-stream" }, body: file }); }
  catch { throw new ApiError({ error: "network" }, 0); }
  let body;
  try { body = await r.json(); } catch { body = { error: "bad_answer", message: "HTTP " + r.status }; }
  if (r.status === 401 && body.error === "not_logged_in") signedOut();
  if (body.error) throw new ApiError(body, r.status);
  return body;
}

async function runJob(op, payload) {
  const first = await api("job.start", { op, payload, wait: 1500 });       // (a job that is over within 1.5 s answers right here)
  if (first.state === "done") return first.result;
  if (first.state === "error") throw new ApiError(first.error || { error: "internal_error" }, 400);
  const { id } = first;
  const busy = { id, label: t("busy"), k: 0, n: 0, stop: false };
  setBusy(busy);
  let wait = 100;
  try {
    for (;;) {
      await sleep(wait); wait = Math.min(400, wait * 1.5);
      if (busy.stop) { busy.stop = false; await api("job.cancel", { id }).catch(() => {}); }
      const st = await api("job.status", { id });
      busy.k = st.k || 0; busy.n = st.n || 0; busy.label = st.label || t("busy");
      setBusy(busy);
      if (st.state === "done" || st.state === "error") return await api("job.result", { id });
      if (st.state === "cancelled") throw new ApiError({ error: "job_cancelled", message: t("cancelled") }, 0);
    }
  } finally { setBusy(null); }
}

// ------------------------------------------------------------------------------------------ state
const S = { schema: null, project: null, tab: "project", sheetId: null, results: null, gcode: null, sim: null,
            report: null, post: store.get("iyicam.post") || "", showPaths: true, views: {}, busy: null,
            files: null,          // what the server says about files of its computer ({files, roots, limits}); null: none
            file: null,           // the file the open project came from or was saved to: {path, hash}
            toComputer: store.get("iyicam.tocomputer") === "1",
            rev: 0 };             // counts the changes of the project: what is drawn from it is made again when it moves on

const layerMap = p => new Map(p.layers.map(l => [l.id, l]));
const sheetThickness = (p, s) => (s && s.thickness != null ? s.thickness : p.material.thickness);
function activeSheet() {
  const p = S.project;
  return p ? (p.sheets.find(s => s.id === S.sheetId) || p.sheets[0]) : null;
}
/** a project too big to keep in the browser's storage (counted, not written out: writing it out is what is slow) */
function tooBigToKeep(p) {
  let n = 0;
  for (const s of p.sheets) for (const v of s.vectors) for (const c of v.contours) { n += c.segs.length; if (n > 30000) return true; }
  return false;
}
function persistNow() {
  if (!S.project || tooBigToKeep(S.project)) return store.del("iyicam.project");
  const s = JSON.stringify(S.project);
  if (s.length < 4e6) store.set("iyicam.project", s); else store.del("iyicam.project");
}
let persistTimer = 0;
function persist() { clearTimeout(persistTimer); persistTimer = setTimeout(persistNow, 700); }     // (a burst of changes is kept once)
addEventListener("pagehide", persistNow);
function touch() { S.rev++; S.results = S.gcode = S.sim = null; persist(); }       // the project changed; what was calculated is stale
function changed() { touch(); render(); }
function applyPatch(patch) {
  S.rev++;
  for (const sh of S.project.sheets)
    sh.vectors = sh.vectors.map(v => patch.vectors[v.id] || v);
  for (const tp of S.project.toolpaths)
    if (patch.sequences[tp.id]) tp.sequence = patch.sequences[tp.id];
  persist();
}

// ------------------------------------------------------------------------------------------ toasts, busy, sheets
function toast(text, kind = "") {
  const box = $("#toasts") || document.body.appendChild(h("div", { id: "toasts", style: "position:fixed;left:12px;right:12px;bottom:calc(var(--tab-h) + 12px + env(safe-area-inset-bottom));z-index:30;display:flex;flex-direction:column;gap:6px;pointer-events:none" }));
  const el = h("div", { class: "msg " + kind, style: "background:var(--card);box-shadow:0 4px 18px rgba(0,0,0,.25);pointer-events:auto" }, text);
  box.append(el);
  setTimeout(() => el.remove(), kind === "bad" ? 9000 : 4500);
}
const fail = e => { if (!(e instanceof ApiError) || e.code !== "not_logged_in") toast(e instanceof ApiError ? e.text : String(e), "bad"); };

function setBusy(b) {
  S.busy = b;
  const box = $("#busy");
  if (!box) return;
  box.hidden = !b;
  if (!b) return;
  $(".lab", box).textContent = b.label;
  $("i", box).style.width = (b.n ? Math.round(100 * b.k / b.n) : 6) + "%";
}

function openSheet(title, body, onClose) {
  let closed = false;
  const close = () => { if (closed) return; closed = true; bg.remove(); document.removeEventListener("keydown", esc); if (onClose) onClose(); };
  const esc = e => { if (e.key === "Escape") close(); };
  const bg = h("div", { class: "sheet-bg", onclick: e => { if (e.target === bg) close(); } },
    h("div", { class: "sheet-pane", role: "dialog", "aria-modal": "true" }, h("h3", {}, title), body));
  document.addEventListener("keydown", esc);
  document.body.append(bg);
  return close;
}

// ------------------------------------------------------------------------------------------ drawing
const flatCache = new WeakMap();
const flatten = IyiGeom.flatten;                           // (geom.js: the points of a contour as a polyline)
const flatOf = v => { let f = flatCache.get(v); if (!f) { f = v.contours.map(c => ({ closed: c.closed, pts: flatten(c) })); flatCache.set(v, f); } return f; };

/** How a drawing is drawn by what can be seen of it.  A stroked path of a quarter of a million curves takes tens of
 *  seconds to draw (and a phone gives up), and at the size of a phone most of the vectors of a big drawing are smaller
 *  than a pixel.  So: what is outside the screen is not drawn; a vector under 1.2 px is a dot; one under 60 px is a line
 *  through (at most) 32 of its points; only a bigger one is drawn with its curves (a budget of curves per frame, the
 *  biggest first).  The paths of a vector are made when they are first wanted and kept with the vector. */
const LOD_DOT_PX = 1.2, LOD_FULL_PX = 60, LOD_FULL_BUDGET = 2500;
const lodInfo = new WeakMap();
function lodOf(v) {
  let i = lodInfo.get(v);
  if (!i) lodInfo.set(v, i = { simple: null, full: null, segs: v.contours.reduce((n, c) => n + c.segs.length, 0) });
  return i;
}
const segEnd = s => (s[0] === "C" ? 5 : 1);
function simplePathOf(v) {
  const i = lodOf(v);
  if (!i.simple) {
    const p = new Path2D();
    for (const c of v.contours) {
      const stride = Math.max(1, Math.ceil((c.segs.length + 1) / 32));
      p.moveTo(c.start[0], c.start[1]);
      for (let k = stride - 1; k < c.segs.length - 1; k += stride) { const s = c.segs[k], e = segEnd(s); p.lineTo(s[e], s[e + 1]); }
      const last = c.segs[c.segs.length - 1];
      if (last) { const e = segEnd(last); p.lineTo(last[e], last[e + 1]); }
      if (c.closed) p.closePath();
    }
    i.simple = p;
  }
  return i.simple;
}
function fullPathOf(v) {
  const i = lodOf(v);
  if (!i.full) {
    const path = new Path2D();
    for (const c of v.contours) {
      let [x, y] = c.start;
      path.moveTo(x, y);
      for (const s of c.segs) {
        if (s[0] === "L") { path.lineTo(s[1], s[2]); x = s[1]; y = s[2]; }
        else if (s[0] === "A") {
          const g = IyiGeom.arcGeom(x, y, s[1], s[2], s[3]);
          if (g) path.arc(g.cx, g.cy, g.r, g.a0, g.a0 + g.th, g.th < 0); else path.lineTo(s[1], s[2]);
          x = s[1]; y = s[2];
        } else { path.bezierCurveTo(s[1], s[2], s[3], s[4], s[5], s[6]); x = s[5]; y = s[6]; }
      }
      if (c.closed) path.closePath();
    }
    i.full = path;
  }
  return i.full;
}
/** The vectors that can be seen in a screen of w x h (css pixels) with this view, as {lines, dots} Path2D per colour
 *  (``colorOf`` gives null for a vector that is not drawn). */
function lodGroups(vectors, view, w, h, colorOf) {
  const s = view.s, vx0 = -view.ox / s, vx1 = (w - view.ox) / s, vy1 = view.oy / s, vy0 = (view.oy - h) / s;
  const groups = new Map(), big = [];
  for (const v of vectors) {
    const col = colorOf(v);
    if (col === null) continue;
    const b = IyiGeom.vbox(v);
    if (!b || b.x1 < vx0 || b.x0 > vx1 || b.y1 < vy0 || b.y0 > vy1) continue;
    let g = groups.get(col);
    if (!g) groups.set(col, g = { lines: new Path2D(), dots: new Path2D() });
    const size = Math.max(b.x1 - b.x0, b.y1 - b.y0) * s;
    if (size < LOD_DOT_PX) g.dots.rect((b.x0 + b.x1) / 2 - 0.5 / s, (b.y0 + b.y1) / 2 - 0.5 / s, 1 / s, 1 / s);
    else if (size >= LOD_FULL_PX) big.push([size, v, g]);
    else g.lines.addPath(simplePathOf(v));
  }
  big.sort((a, b) => b[0] - a[0]);
  let budget = LOD_FULL_BUDGET;
  for (const [, v, g] of big) {
    const n = lodOf(v).segs;
    if (budget >= n) { budget -= n; g.lines.addPath(fullPathOf(v)); } else g.lines.addPath(simplePathOf(v));
  }
  return groups;
}
/** how many segments a sheet has (for deciding how to draw it), counted once per change of the project */
const segCounts = new Map();
function sceneSegs(sheet) {
  const key = S.rev + "|" + sheet.vectors.length;
  let c = segCounts.get(sheet.id);
  if (!c || c.key !== key) segCounts.set(sheet.id, c = { key, n: sheet.vectors.reduce((n, v) => n + lodOf(v).segs, 0) });
  return c.n;
}
/** the toolpaths that were calculated, as paths per depth: made once for a calculation */
const previews = new WeakMap();
function previewOf(sheet, th) {
  let per = previews.get(S.results);
  if (!per) previews.set(S.results, per = new Map());
  const key = sheet.id + "|" + th;
  let pv = per.get(key);
  if (pv) return pv;
  const buckets = Array.from({ length: 8 }, () => new Path2D()), rapid = new Path2D(), drills = [];
  for (const it of S.results.results) {
    if (it.sheet !== sheet.id || !it.preview) continue;
    for (const c of it.preview.cuts) {
      const b = buckets[clamp(Math.floor(clamp(-c.z / th, 0, 1) * 7.999), 0, 7)];
      for (let i = 0; i + 1 < c.p.length; i += 2) i ? b.lineTo(c.p[i], c.p[i + 1]) : b.moveTo(c.p[i], c.p[i + 1]);
    }
    const r = it.preview.rapids;
    for (let i = 0; i + 3 < r.length; i += 4) { rapid.moveTo(r[i], r[i + 1]); rapid.lineTo(r[i + 2], r[i + 3]); }
    drills.push(...it.preview.drills);
  }
  per.set(key, pv = { buckets, rapid, drills });
  return pv;
}

function fitView(cv, sheet) {
  const w = cv.clientWidth || 300, hgt = cv.clientHeight || 300, m = 24;
  const s = Math.max(1e-6, Math.min((w - 2 * m) / Math.max(sheet.width, 1), (hgt - 2 * m) / Math.max(sheet.height, 1)));
  return { s, ox: (w - sheet.width * s) / 2, oy: (hgt + sheet.height * s) / 2 };
}

function drawScene(cv, sheet, view, extra) {
  const p = S.project, dpr = window.devicePixelRatio || 1, w = cv.clientWidth, hgt = cv.clientHeight;
  if (cv.width !== Math.round(w * dpr) || cv.height !== Math.round(hgt * dpr)) { cv.width = Math.round(w * dpr); cv.height = Math.round(hgt * dpr); }
  const ctx = cv.getContext("2d"), dark = matchMedia("(prefers-color-scheme: dark)").matches;
  ctx.setTransform(1, 0, 0, 1, 0, 0);
  ctx.clearRect(0, 0, cv.width, cv.height);
  ctx.setTransform(dpr * view.s, 0, 0, -dpr * view.s, dpr * view.ox, dpr * view.oy);      // millimetres, Y up
  const px = 1 / view.s;
  // the material
  ctx.beginPath();
  if (sheet.outline) for (const poly of sheet.outline) { poly.forEach(([x, y], i) => i ? ctx.lineTo(x, y) : ctx.moveTo(x, y)); ctx.closePath(); }
  else ctx.rect(0, 0, sheet.width, sheet.height);
  ctx.fillStyle = dark ? "#1b251f" : "#ffffff"; ctx.fill("evenodd");
  ctx.lineWidth = 1.5 * px; ctx.strokeStyle = dark ? "#4b6455" : "#9db2a5"; ctx.stroke();
  // the vectors: their paths are made once (with the curves as curves) and only stroked at every frame
  ctx.lineWidth = 1.6 * px; ctx.lineJoin = "round";
  const layers = layerMap(p);
  for (const [col, g] of lodGroups(sheet.vectors, view, w, hgt, v => { const l = layers.get(v.layer); return l && !l.visible ? null : (l && l.color) || "#1f77b4"; })) {
    ctx.strokeStyle = col; ctx.stroke(g.lines); ctx.fillStyle = col; ctx.fill(g.dots);
  }
  // the toolpaths
  if (S.showPaths && S.results) {
    const th = Math.max(sheetThickness(p, sheet), 0.01), { buckets, rapid, drills } = previewOf(sheet, th);
    ctx.lineWidth = 1 * px; ctx.setLineDash([4 * px, 4 * px]); ctx.strokeStyle = dark ? "#7d8b83" : "#9aa7a0"; ctx.stroke(rapid); ctx.setLineDash([]);
    ctx.lineWidth = 2 * px;
    buckets.forEach((path, i) => { ctx.strokeStyle = `hsl(${140 - 125 * i / 7} 80% ${dark ? 58 : 42}%)`; ctx.stroke(path); });
    ctx.fillStyle = dark ? "#fbbf24" : "#b45309";
    for (const [x, y] of drills) { ctx.beginPath(); ctx.arc(x, y, 3 * px, 0, 7); ctx.fill(); }
  }
  if (extra && extra.overlay) extra.overlay(ctx, view, px, dark, w, hgt);      // the selection, the nodes, what is being drawn
}

function drawStage(sheet) {
  const cv = h("canvas", { "aria-label": "drawing" });
  const stage = h("div", { class: "stage" }, cv);
  let view = S.views[sheet.id];
  let raf = 0;
  const redraw = () => { cancelAnimationFrame(raf); raf = requestAnimationFrame(() => { if (!view) view = S.views[sheet.id] = fitView(cv, sheet); drawScene(cv, sheet, view); }); };
  const zoomAt = (cx, cy, f) => { f = clamp(f, 0.2, 40); view.ox = cx - (cx - view.ox) * f; view.oy = cy - (cy - view.oy) * f; view.s *= f; };
  const ptrs = new Map();
  cv.addEventListener("pointerdown", e => { cv.setPointerCapture(e.pointerId); ptrs.set(e.pointerId, { x: e.offsetX, y: e.offsetY }); });
  const up = e => ptrs.delete(e.pointerId);
  cv.addEventListener("pointerup", up); cv.addEventListener("pointercancel", up);
  cv.addEventListener("pointermove", e => {
    const q = ptrs.get(e.pointerId);
    if (!q || !view) return;
    const nx = e.offsetX, ny = e.offsetY;
    if (ptrs.size === 1) { view.ox += nx - q.x; view.oy += ny - q.y; }
    else if (ptrs.size === 2) {
      const o = [...ptrs].find(([id]) => id !== e.pointerId)[1];
      const d0 = Math.hypot(q.x - o.x, q.y - o.y) || 1, d1 = Math.hypot(nx - o.x, ny - o.y) || 1;
      zoomAt((q.x + o.x) / 2, (q.y + o.y) / 2, d1 / d0);
      view.ox += (nx + o.x) / 2 - (q.x + o.x) / 2; view.oy += (ny + o.y) / 2 - (q.y + o.y) / 2;
    }
    q.x = nx; q.y = ny;
    redraw();
  });
  cv.addEventListener("wheel", e => { e.preventDefault(); if (view) { zoomAt(e.offsetX, e.offsetY, Math.exp(-e.deltaY * 0.0015)); redraw(); } }, { passive: false });
  cv.addEventListener("dblclick", () => { view = S.views[sheet.id] = fitView(cv, sheet); redraw(); });
  new ResizeObserver(() => redraw()).observe(cv);
  const btn = (label, title, fn) => h("button", { title, "aria-label": title, onclick: fn }, label);
  const tools = h("div", { class: "tools" },
    btn("⤢", t("draw.fit"), () => { view = S.views[sheet.id] = fitView(cv, sheet); redraw(); }),
    btn("＋", "+", () => { zoomAt(cv.clientWidth / 2, cv.clientHeight / 2, 1.5); redraw(); }),
    btn("－", "−", () => { zoomAt(cv.clientWidth / 2, cv.clientHeight / 2, 1 / 1.5); redraw(); }));
  if (S.results) tools.append(btn(S.showPaths ? "◉" : "○", t("draw.paths"), e => { S.showPaths = !S.showPaths; e.currentTarget.textContent = S.showPaths ? "◉" : "○"; redraw(); }));
  stage.append(tools);
  stage.append(h("div", { class: "legend" }, `${sheet.name} · ${fmt(sheet.width, 0)}×${fmt(sheet.height, 0)} mm · ${t("proj.vectors", sheet.vectors.length)}`));
  return stage;
}

// ------------------------------------------------------------------------------------------ forms from the schema
const NUMERIC = new Set(["float", "length", "int", "angle", "feed", "percent"]);
const visibleParam = (p, vals) => !p.visible || Object.entries(p.visible).every(([k, want]) => Array.isArray(want) ? want.includes(vals[k]) : vals[k] === want);

function numberInput(value, onSet, p = {}) {
  const el = h("input", { type: "number", inputmode: "decimal", step: p.kind === "int" ? "1" : "any", value: value ?? "" });
  if (p.min != null) el.min = p.min;
  if (p.max != null) el.max = p.max;
  el.addEventListener("input", () => { if (Number.isFinite(el.valueAsNumber)) onSet(el.valueAsNumber); });
  return el;
}

function paramsForm(params, vals, tools) {
  const root = h("div"), rows = [];
  const refresh = () => rows.forEach(([p, el]) => { el.hidden = !visibleParam(p, vals); });
  let group = null;
  for (const p of params) {
    if (!(p.name in vals) && p.default != null) vals[p.name] = p.default;
    let el;
    const help = p.help ? h("div", { class: "muted small" }, p.help) : null;
    if (p.kind === "bool") {
      const cb = h("input", { type: "checkbox", checked: !!vals[p.name] });
      cb.onchange = () => { vals[p.name] = cb.checked; refresh(); };
      el = h("div", {}, h("label", { class: "chk" }, cb, p.label), help);
    } else if (p.kind === "choice") {
      const sel = h("select", {}, (p.choices || []).map(([v, l]) => h("option", { value: v }, l)));
      sel.value = String(vals[p.name] ?? p.default ?? "");
      if (sel.value === "" && sel.options.length) sel.value = sel.options[0].value;
      vals[p.name] = sel.value;
      sel.onchange = () => { vals[p.name] = sel.value; refresh(); };
      el = h("label", { class: "f" }, h("span", {}, p.label), sel, help);
    } else if (NUMERIC.has(p.kind)) {
      el = h("label", { class: "f" }, h("span", {}, p.label), numberInput(vals[p.name], v => { vals[p.name] = v; refresh(); }, p), help);
    } else if (p.kind === "tool") {
      const cur = vals[p.name] && (vals[p.name].__tool__ || vals[p.name]);
      const sel = h("select", {}, h("option", { value: "" }, t("paths.none_tool")), tools.map((tl, i) => h("option", { value: i }, tl.name)));
      const at = cur ? tools.findIndex(tl => tl.id === cur.id) : -1;
      sel.value = at >= 0 ? String(at) : "";
      sel.onchange = () => { if (sel.value === "") delete vals[p.name]; else vals[p.name] = { __tool__: { ...tools[+sel.value] } }; };
      el = h("label", { class: "f" }, h("span", {}, p.label), sel, help);
    } else if (p.kind === "point") {
      const pt = Array.isArray(vals[p.name]) ? vals[p.name] : [0, 0];
      vals[p.name] = pt;
      el = h("div", { class: "grid2" },
        h("label", { class: "f" }, h("span", {}, p.label + " X"), numberInput(pt[0], v => { pt[0] = v; })),
        h("label", { class: "f" }, h("span", {}, p.label + " Y"), numberInput(pt[1], v => { pt[1] = v; })));
    } else {
      const inp = p.kind === "text" ? h("textarea", {}, vals[p.name] ?? "") : h("input", { type: "text", value: vals[p.name] ?? "" });
      inp.addEventListener("input", () => { vals[p.name] = inp.value; refresh(); });
      el = h("label", { class: "f" }, h("span", {}, p.label), inp, help);
    }
    if (p.group && p.group !== group) { group = p.group; root.append(h("div", { class: "group-h" }, p.group)); }
    rows.push([p, el]);
    root.append(el);
  }
  refresh();
  return root;
}

// ------------------------------------------------------------------------------------------ toolpath editor
/** the settings of the cut order (the engine's ``schema.sequence``) as a form */
function sequenceParams() {
  const q = S.schema.sequence, d = q.defaults || {};
  return [
    { name: "auto_order", label: t("seq.auto"), kind: "bool", default: d.auto_order !== false },
    { name: "strategy", label: t("seq.strategy"), kind: "choice", choices: q.strategies, default: d.strategy },
    { name: "grouping", label: t("seq.grouping"), kind: "choice", choices: q.groupings, default: d.grouping },
    { name: "direction", label: t("seq.direction"), kind: "choice", choices: q.directions, default: d.direction },
    { name: "band", label: t("seq.band"), kind: "length", default: d.band ?? 0, min: 0 },
    { name: "smart_starts", label: t("seq.smart"), kind: "bool", default: !!d.smart_starts },
    { name: "start_margin", label: t("seq.margin"), kind: "length", default: d.start_margin ?? 1, min: 0 },
    { name: "corner_distance", label: t("seq.corner"), kind: "length", default: d.corner_distance ?? 0, min: 0 },
  ];
}
const TOOL_FIELDS = ["diameter", "feed_rate", "plunge_rate", "spindle_speed", "stepdown", "stepover", "number"];

function editToolpath(existing) {
  const p = S.project, sc = S.schema;
  const ops = sc.operations;
  const tp = existing ? JSON.parse(JSON.stringify(existing)) : {
    id: newId(), name: "", op: ops[0].id, tool: null, params: {}, enabled: true, sheets: null,
    selection: { mode: "layers", vectors: [], layers: p.layers.map(l => l.id) }, sequence: {}, safe_z: null, clearance: null };
  const chosen = new Set(tp.selection.mode === "layers" ? tp.selection.layers : p.layers.map(l => l.id));
  // what it machines: whole layers, or the vectors that are selected on the drawing (edit.js keeps the selection)
  const ed = window.IyiPage.editor, picked = ed ? [...ed.state.sel] : [];
  let mode = tp.selection.mode === "vectors" ? "vectors" : "layers";
  const vecIds = tp.selection.mode === "vectors" ? tp.selection.vectors.slice() : picked;
  const modeSel = h("select", {}, h("option", { value: "layers" }, t("paths.bylayers")), h("option", { value: "vectors" }, t("paths.byvectors", vecIds.length)));
  modeSel.value = mode;
  const opBox = h("div"), toolBox = h("div"), name = h("input", { type: "text", value: tp.name });
  const opSel = h("select", {}, ops.map(o => h("option", { value: o.id }, o.label)));
  opSel.value = tp.op;
  let nameTouched = !!existing;
  name.addEventListener("input", () => { nameTouched = true; });
  const opOf = () => ops.find(o => o.id === opSel.value);
  const toolsFor = o => sc.tools.filter(tl => !o.tool_types || o.tool_types.includes(tl.type));

  function buildOp() {
    const o = opOf(), list = toolsFor(o);
    if (!tp.tool || (o.tool_types && !o.tool_types.includes(tp.tool.type))) tp.tool = list.length ? { ...list[0] } : (sc.tools[0] ? { ...sc.tools[0] } : tp.tool);
    if (!nameTouched) name.value = o.label;
    const opts = list.slice();        // the tool of an old toolpath may not be in the library any more (or have another id)
    if (tp.tool && !opts.some(tl => tl.id === tp.tool.id)) {
      const same = opts.find(tl => tl.name === tp.tool.name);
      if (same) tp.tool.id = same.id; else opts.unshift(tp.tool);
    }
    const toolSel = h("select", {}, opts.map(tl => h("option", { value: tl.id }, `${tl.name} (Ø${fmt(tl.diameter, 2)})`)));
    toolSel.value = tp.tool ? tp.tool.id : "";
    toolSel.onchange = () => { tp.tool = { ...opts.find(tl => tl.id === toolSel.value) }; buildTool(); };
    const toolVals = h("div", { class: "grid2" });
    function buildTool() {
      toolVals.replaceChildren(...TOOL_FIELDS.map(f => h("label", { class: "f" }, h("span", {}, t("tool." + f)),
        numberInput(tp.tool[f], v => { tp.tool[f] = f === "number" || f === "spindle_speed" ? Math.round(v) : v; }, { kind: f === "number" ? "int" : "float" }))));
    }
    if (tp.tool) buildTool();
    toolBox.replaceChildren(
      h("label", { class: "f" }, h("span", {}, t("paths.tool")), toolSel),
      h("div", { class: "group-h" }, t("paths.toolvals")), toolVals);
    // every parameter has a value from the first moment (the engine fills gaps, but the form shows them)
    const prms = o.params.filter(q => !o.extra_tool_params.includes(q.name) || q.kind === "tool");
    opBox.replaceChildren(
      o.description ? h("div", { class: "muted small" }, o.description) : null,
      h("div", { class: "group-h" }, t("paths.params")), paramsForm(prms, tp.params, sc.tools));
  }
  opSel.onchange = () => { tp.params = {}; buildOp(); };

  const layerBox = h("div", {}, p.layers.map(l => {
    const cb = h("input", { type: "checkbox", checked: chosen.has(l.id) });
    cb.onchange = () => cb.checked ? chosen.add(l.id) : chosen.delete(l.id);
    return h("label", { class: "chk" }, cb, h("span", { class: "dot", style: `background:${l.color}` }), l.name);
  }));
  modeSel.onchange = () => { mode = modeSel.value; layerBox.hidden = mode !== "layers"; };
  layerBox.hidden = mode !== "layers";
  // the order of the cuts and where each one starts (the engine's own settings; shown when asked for, so that a
  // toolpath that is only looked at is not changed)
  const seqBox = h("div"), seqDet = h("details", {}, h("summary", { class: "group-h" }, t("paths.order")), seqBox);
  seqDet.addEventListener("toggle", () => {
    if (seqDet.open && !seqBox.firstChild) { tp.sequence = tp.sequence || {}; seqBox.append(paramsForm(sequenceParams(), tp.sequence, sc.tools)); }
  });
  const body = h("div", {},
    h("label", { class: "f" }, h("span", {}, t("paths.op")), opSel),
    h("label", { class: "f" }, h("span", {}, t("paths.name")), name),
    h("div", { class: "group-h" }, t("paths.layers")), modeSel, layerBox,
    toolBox, opBox, seqDet);
  buildOp();
  let close;
  const save = () => {
    if (mode === "vectors" ? !vecIds.length : !chosen.size) return toast(mode === "vectors" ? t("paths.novec") : t("paths.nolayer"), "warn");
    tp.name = name.value.trim() || opOf().label;
    tp.op = opSel.value;
    tp.selection = mode === "vectors" ? { mode: "vectors", vectors: vecIds, layers: [] }
      : { mode: "layers", vectors: [], layers: p.layers.filter(l => chosen.has(l.id)).map(l => l.id) };
    const i = p.toolpaths.findIndex(x => x.id === tp.id);
    if (i >= 0) p.toolpaths[i] = tp; else p.toolpaths.push(tp);
    close(); changed();
  };
  const del = () => { p.toolpaths = p.toolpaths.filter(x => x.id !== tp.id); close(); changed(); };
  body.append(h("div", { class: "row", style: "margin-top:18px" },
    h("button", { class: "primary grow", onclick: save }, t("paths.save")),
    existing ? h("button", { class: "danger", onclick: del }, t("paths.delete")) : null,
    h("button", { onclick: () => close() }, t("paths.cancel"))));
  close = openSheet(existing ? t("paths.edit") : t("paths.new"), body);
}

// ------------------------------------------------------------------------------------------ views
function view() {
  const p = S.project;
  if (S.tab === "project") return viewProject();
  if (!p) return h("div", { class: "empty" }, h("div", { class: "big" }, "📂"), h("div", {}, t("proj.none")), h("div", { class: "muted" }, t("proj.none2")));
  return { draw: viewDraw, paths: viewPaths, out: viewOutput }[S.tab]();
}

async function openFile(input, replace) {
  const file = input.files && input.files[0];
  input.value = "";
  if (!file || S.busy) return;
  try {
    setBusy({ label: t("busy"), k: 0, n: 0 });
    const add = !replace && S.project;
    const thickness = +($("#thk") && $("#thk").value) || 18;
    let out;
    if (add) {                                       // (added to a project: the project goes with it, as JSON)
      const data_b64 = await fileToB64(file);
      setBusy(null);
      out = await runJob("import", { name: file.name, data_b64, thickness, project: S.project });
    } else {                                         // a new project: the file is sent as it is (no copy of it in this phone's memory)
      out = await apiRaw(`/api/import_file?name=${encodeURIComponent(file.name)}&thickness=${thickness}`, file);
      setBusy(null);
    }
    S.project = out.project; S.report = out.report;
    S.sheetId = S.project.sheets[0].id; S.views = {};
    if (!add) { setFile(null); S.tab = out.project.toolpaths.length || out.report.errors.length ? "project" : "paths"; if (window.IyiPage.editor) window.IyiPage.editor.clear(); }
    changed();
    toast(t("open.done", file.name), "ok");
  } catch (e) { setBusy(null); fail(e); }
}

// ------------------------------------------------------------------------------------------ files of the computer
// Only when the server gives them (S.files.files): folders inside the roots the server was started with, see docs/web.md.
const hasFiles = () => !!(S.files && S.files.files);
const dirOf = path => (path || "").replace(/[\\/][^\\/]*$/, "");
const baseOf = path => (path || "").split(/[\\/]/).pop();
const joinPath = (dir, name) => dir.replace(/[\\/]$/, "") + (dir.includes("\\") && !dir.includes("/") ? "\\" : "/") + name;
const fmtSize = n => n < 1024 ? n + " B" : n < 1048576 ? fmt(n / 1024, 0) + " KB" : fmt(n / 1048576, 1) + " MB";
const EXT_ICON = { icam: "◧", dxf: "▱", svg: "◇", plt: "✎", hpgl: "✎", hpg: "✎", cdr: "◈" };

function setFile(f) {
  S.file = f;
  if (f) store.set("iyicam.file", JSON.stringify(f)); else store.del("iyicam.file");
}

/** Browse the computer.  mode "open" -> {path, name}, "save" -> {path, dir, name} (a file to write), "folder" -> {path};
    ``null`` when it was closed without a choice. */
function fileBrowser({ mode = "open", ext = null, start = "", name = "", title = "" } = {}) {
  return new Promise(res => {
    let dir = "", parent = null, done = false, close = () => {}, first = true;
    const finish = v => { if (done) return; done = true; if (v && v.dir) store.set("iyicam.dir", v.dir); res(v); close(); };
    const readable = new Set(S.schema.importers.flatMap(i => i.extensions));
    const list = h("div", { class: "files" }), crumb = h("div", { class: "crumb muted small" });
    const nameIn = h("input", { type: "text", value: name, autocapitalize: "off", spellcheck: "false", autocomplete: "off" });
    const up = h("button", { class: "small", "aria-label": t("files.up"), onclick: () => go(parent) }, "↑");
    const choose = h("button", { class: "grow primary", onclick: () => {
      if (!dir) return;
      if (mode === "folder") return finish({ path: dir, dir });
      const n = nameIn.value.trim();
      if (!n) return;
      const named = ext && !n.toLowerCase().endsWith("." + ext) ? `${n}.${ext}` : n;
      finish({ path: joinPath(dir, named), dir, name: named });
    } }, mode === "save" ? t("files.save") : t("files.chooseHere"));
    // the folder as the server gave it; what is shown is made from it here (sorted and searched at once, no call)
    let entries = [], truncated = false, found = null;
    let sort = ["name", "date", "size"].includes(store.get("iyicam.sort")) ? store.get("iyicam.sort") : "name";
    let desc = store.get("iyicam.sortdesc") === "1";
    const fold = s => s.toLocaleLowerCase("tr").replace(/ı/g, "i").normalize("NFD").replace(/[\u0300-\u036f]/g, "");
    const query = h("input", { type: "search", placeholder: t("files.search"), "aria-label": t("files.search"), autocomplete: "off", autocapitalize: "off", spellcheck: "false" });
    const sortBtns = h("div", { class: "row fsort" });
    const deep = h("div");
    let serverTimer = 0;
    const MAX_ROWS = 300;
    function sortBar() {
      sortBtns.replaceChildren(...["name", "date", "size"].map(k => h("button", { class: "small" + (sort === k ? " primary" : ""), onclick: () => {
        if (sort === k) desc = !desc; else { sort = k; desc = k !== "name"; }           // (newest and biggest first)
        store.set("iyicam.sort", sort); store.set("iyicam.sortdesc", desc ? "1" : "0");
        sortBar(); draw(); refetchIfCut();
      } }, t("files.sort." + k) + (sort === k ? (desc ? " ↓" : " ↑") : ""))));
    }
    function refetchIfCut() {                                   // (a folder too big for one answer is asked again, in this order)
      if (!truncated || found || !dir) return;
      clearTimeout(serverTimer);
      serverTimer = setTimeout(() => load(dir, true), 250);
    }
    function draw() {
      const needle = fold(query.value.trim());
      let rows = (found || entries).filter(e => e.type === "dir" || (mode !== "folder" && (mode !== "open" || readable.has(e.ext))));
      if (needle && !found) rows = rows.filter(e => fold(e.name).includes(needle));
      const byName = (a, b) => (fold(a.name) < fold(b.name) ? -1 : fold(a.name) > fold(b.name) ? 1 : 0);
      const key = { name: byName, date: (a, b) => (a.mtime || 0) - (b.mtime || 0), size: (a, b) => (a.size || 0) - (b.size || 0) }[sort];
      const dirs = rows.filter(e => e.type === "dir").sort(byName);
      const files = rows.filter(e => e.type !== "dir").sort((a, b) => (desc ? -1 : 1) * key(a, b) || byName(a, b));
      const all = [...dirs, ...files];
      list.replaceChildren();
      if (!all.length) list.append(h("div", { class: "empty" }, found ? t("files.deepNone") : t(mode === "folder" ? "files.nofolders" : "files.empty")));
      for (const e of all.slice(0, MAX_ROWS)) {
        if (e.type === "dir") list.append(h("button", { class: "frow dir", onclick: () => { query.value = ""; go(e.path); } }, h("span", { class: "ic" }, "📁"), h("span", { class: "nm" }, e.name), h("span", { class: "sz" }, "›")));
        else list.append(h("button", { class: "frow file" + (ext && e.ext !== ext ? " dim" : ""), onclick: () => {
          if (mode === "open") finish({ path: e.path, name: e.name, dir: dirOf(e.path) }); else nameIn.value = e.name;
        } }, h("span", { class: "ic" }, EXT_ICON[e.ext] || "▤"),
          h("span", { class: "nm" }, e.name, found ? h("div", { class: "muted small" }, dirOf(e.path).replace(dir, "…")) : null),
          h("span", { class: "sz" }, `${fmtSize(e.size)} · ${new Date(e.mtime * 1000).toLocaleDateString(lang === "tr" ? "tr-TR" : "en-GB")}`)));
      }
      if (all.length > MAX_ROWS) list.append(h("div", { class: "empty" }, t("files.more", all.length - MAX_ROWS)));
      else if (truncated && !found) list.append(h("div", { class: "empty" }, t("files.cut")));
      // the search below this folder (a call: it looks into every sub-folder, which can take a moment)
      deep.replaceChildren();
      if (needle && mode !== "folder" && dir) deep.append(found
        ? h("button", { class: "small", onclick: () => { found = null; draw(); } }, "✕ " + t("files.deepOff"))
        : h("button", { class: "small primary", onclick: deepSearch }, "🔎 " + t("files.deep")));
    }
    async function deepSearch() {
      deep.replaceChildren(h("span", { class: "muted small" }, "…"));
      try {
        const r = await api("search", { path: dir, q: query.value.trim() });
        found = r.entries; draw();
        if (r.truncated) toast(t("files.deepCut"), "warn");
      } catch (e) { fail(e); draw(); }
    }
    query.addEventListener("input", () => { found = null; draw(); clearTimeout(serverTimer); if (truncated) serverTimer = setTimeout(() => load(dir, true), 350); });
    sortBar();
    const body = h("div", {}, h("div", { class: "row fbar" }, up, crumb), query, sortBtns, deep, list,
      mode === "save" ? h("label", { class: "f" }, h("span", {}, t("files.name")), nameIn) : null,
      h("div", { class: "row", style: "margin-top:12px" }, h("button", { class: "grow", onclick: () => finish(null) }, t("cancel")), mode === "open" ? null : choose));
    close = openSheet(title || (mode === "open" ? t("files.openTitle") : mode === "save" ? t("files.saveTitle") : t("files.folderTitle")), body, () => finish(null));

    function go(path) { found = null; return load(path, false); }
    async function load(path, again) {
      if (!again) list.replaceChildren(h("div", { class: "empty" }, "…"));
      try {
        const r = await api("list", { path: path || "", sort, desc, ...(again && query.value.trim() ? { q: query.value.trim() } : {}) });
        first = false;
        if (r.roots && r.entries.length === 1) return go(r.entries[0].path);          // one place to browse: go in
        dir = r.roots ? "" : r.path; parent = r.roots ? null : r.parent;
        up.disabled = !parent; choose.disabled = !dir;
        crumb.textContent = r.roots ? t("files.roots") : r.path;
        entries = r.entries; truncated = !!r.truncated;
        draw();
      } catch (e) {
        if (path && first) { first = false; return go(""); }                         // the remembered folder is gone: start again
        list.replaceChildren(h("div", { class: "empty bad" }, e instanceof ApiError ? e.text : String(e)));
      }
    }
    go(start || store.get("iyicam.dir") || "");
  });
}

async function openPath(path, replace) {
  if (S.busy) return;
  try {
    setBusy({ label: t("busy"), k: 0, n: 0 });
    const add = !replace && S.project;
    const out = await api("open", { path, thickness: +($("#thk") && $("#thk").value) || 18, ...(add ? { project: S.project } : {}) });
    S.project = out.project; S.report = out.report;
    S.sheetId = S.project.sheets[0].id; S.views = {};
    if (out.kind === "project") setFile({ path: out.path, hash: out.hash });
    else if (!add) setFile(null);                 // (a drawing added to a project leaves the file of that project as it was)
    store.set("iyicam.dir", dirOf(out.path));
    if (!add) { S.tab = out.project.toolpaths.length || out.report.errors.length ? "project" : "paths"; if (window.IyiPage.editor) window.IyiPage.editor.clear(); }
    changed();
    toast(t("files.opened", out.name), "ok");
  } catch (e) { fail(e); } finally { setBusy(null); }
}

async function openFromComputer(replace) {
  const pick = await fileBrowser({ mode: "open", start: dirOf(S.file && S.file.path) });
  if (pick) await openPath(pick.path, replace);
}

/** Write the project to a .icam file of the computer; ``as`` (or no file yet) asks where.  A file that exists is
    replaced only when it is the one this project came from and nobody changed it meanwhile, or when the person says so. */
async function saveProject(as) {
  const p = S.project;
  let path = !as && S.file ? S.file.path : null;
  if (!path) {
    const pick = await fileBrowser({ mode: "save", ext: "icam", start: dirOf(S.file && S.file.path),
      name: (p.name || "project").replace(/[^\w .-]+/g, "_") + ".icam", title: t("files.saveTitle") });
    if (!pick) return;
    path = pick.path;
  }
  const write = overwrite => api("save", { path, project: p, overwrite: !!overwrite,
    ...(!overwrite && S.file && S.file.path === path ? { base_hash: S.file.hash } : {}) });
  let r;
  try { r = await write(false); }
  catch (e) {
    if (e.code !== "file_exists" && e.code !== "file_changed") throw e;
    if (!confirm(t(e.code === "file_exists" ? "files.overwrite" : "files.changed", baseOf(path)))) return;
    r = await write(true);
  }
  setFile({ path: r.path, hash: r.hash });
  store.set("iyicam.dir", dirOf(r.path));
  toast(t("files.savedProject", r.path), "ok");
  render();
}

/** Write files (G-code, exports) into a folder of the computer.  [{name, text} or {name, data_b64}] */
async function saveToComputer(files) {
  const pick = await fileBrowser({ mode: "folder", start: dirOf(S.file && S.file.path), title: t("files.folderTitle") });
  if (!pick) return;
  const write = overwrite => api("save_files", { folder: pick.path, overwrite: !!overwrite,
    files: files.map(f => f.text !== undefined ? { name: f.name, text: f.text } : { name: f.name, data_b64: f.data_b64 }) });
  let r;
  try { r = await write(false); }
  catch (e) {
    if (e.code !== "file_exists") throw e;
    const names = ((e.body.params || {}).names || []).join(", ");
    if (!confirm(t("files.overwrite", names))) return;
    r = await write(true);
  }
  toast(t("files.saved", r.written.length, r.folder), "ok");
}

function viewProject() {
  const p = S.project, ext = S.schema.importers.flatMap(i => i.extensions.map(x => "." + x)).join(",");
  const pick = h("input", { type: "file", accept: ext, hidden: true, onchange: e => openFile(e.target) });
  const out = [];
  const addToggle = h("input", { type: "checkbox", id: "addto", checked: true });
  out.push(h("div", { class: "card" }, h("h2", {}, t("open.title")),
    h("div", { class: "muted small" }, t("open.hint")),
    h("label", { class: "f" }, h("span", {}, t("open.thickness")), h("input", { type: "number", id: "thk", inputmode: "decimal", step: "any", value: p ? p.material.thickness : 18 })),
    p ? h("label", { class: "chk" }, addToggle, t("open.add")) : null,
    h("div", { class: "row", style: "margin-top:12px" },
      h("button", { class: "primary grow", onclick: () => { pick.onchange = e => openFile(e.target, !addToggle.checked); pick.click(); } }, "📂 " + t("open.pick")),
      hasFiles() ? h("button", { class: "grow", onclick: () => guarded(() => openFromComputer(!addToggle.checked)) }, "🖥 " + t("files.open")) : null,
      !p && window.IyiPage.editor ? h("button", { class: "grow", onclick: () => guarded(() => window.IyiPage.editor.newProject()) }, t("proj.new")) : null), pick));
  if (S.report) {
    const r = S.report;
    out.push(h("div", { class: "card" }, h("h2", {}, t("report.title")), h("div", {}, r.summary || ""),
      r.via ? h("div", { class: "muted small" }, r.via) : null,
      r.errors.length ? h("div", { class: "msg bad" }, h("b", {}, t("report.errors") + ": "), r.errors.slice(0, 8).join(" · ")) : null,
      r.notes.length ? h("div", { class: "msg" }, h("b", {}, t("report.notes") + ": "), r.notes.slice(0, 8).join(" · ")) : null));
  }
  if (!p) { out.push(h("div", { class: "empty" }, h("div", { class: "big" }, "📐"), h("div", {}, t("proj.none")), h("div", { class: "muted" }, t("proj.none2")))); return h("div", {}, out); }

  const nameIn = h("input", { type: "text", value: p.name });
  nameIn.addEventListener("input", () => { p.name = nameIn.value; persist(); });
  out.push(h("div", { class: "card" }, h("h2", {}, t("proj.name")), nameIn));

  // sheets
  out.push(h("div", { class: "card" }, h("h2", {}, t("proj.sheets")), h("div", { class: "list" }, p.sheets.map(s => {
    const on = activeSheet().id === s.id;
    const w = numberInput(s.width, v => { s.width = v; touch(); }), hh = numberInput(s.height, v => { s.height = v; touch(); });
    return h("div", { class: "item", style: "flex-wrap:wrap" },
      h("button", { class: "small" + (on ? " primary" : ""), onclick: () => { S.sheetId = s.id; render(); } }, on ? "✓" : "○"),
      h("div", { class: "grow" }, h("b", {}, s.name), h("div", { class: "muted small" }, t("proj.vectors", s.vectors.length))),
      h("div", { class: "grid2", style: "width:100%" },
        h("label", { class: "f" }, h("span", {}, t("proj.width")), w), h("label", { class: "f" }, h("span", {}, t("proj.height")), hh),
        h("label", { class: "f" }, h("span", {}, t("proj.sheetthk")), thicknessOverride(s))));
  })), window.IyiPage.editor ? window.IyiPage.editor.sheetTools() : null));

  // material
  const m = p.material, zz = h("select", {}, h("option", { value: "top" }, t("proj.top")), h("option", { value: "bottom" }, t("proj.bottom")));
  zz.value = m.z_zero; zz.onchange = () => { m.z_zero = zz.value; touch(); };
  const org = h("select", {}, S.schema.origins.map(o => h("option", { value: o }, o.replace(/_/g, " "))));
  org.value = m.xy_origin; org.onchange = () => { m.xy_origin = org.value; touch(); };
  out.push(h("div", { class: "card" }, h("h2", {}, t("proj.material")), h("div", { class: "grid2" },
    h("label", { class: "f" }, h("span", {}, t("proj.thickness")), numberInput(m.thickness, v => { m.thickness = v; touch(); })),
    h("label", { class: "f" }, h("span", {}, t("proj.safez")), numberInput(m.safe_z, v => { m.safe_z = v; touch(); })),
    h("label", { class: "f" }, h("span", {}, t("proj.clear")), numberInput(m.clearance, v => { m.clearance = v; touch(); })),
    h("label", { class: "f" }, h("span", {}, t("proj.zzero")), zz)),
    h("label", { class: "f" }, h("span", {}, t("proj.origin")), org),
    h("div", { class: "group-h" }, t("proj.home")),
    h("div", { class: "grid2" }, ["X", "Y", "Z"].map((a, i) => h("label", { class: "f" }, h("span", {}, "home " + a),
      numberInput((m.home || [0, 0, 20])[i], v => { m.home = (m.home || [0, 0, 20]).slice(); m.home[i] = v; touch(); }))),
      h("label", { class: "f" }, h("span", {}, t("proj.rapid")), numberInput(m.rapid_rate ?? 8000, v => { m.rapid_rate = v; touch(); }))),
    h("div", { class: "grid2" },
      (() => { const cb = h("input", { type: "checkbox", checked: !!m.rotary }); cb.onchange = () => { m.rotary = cb.checked; touch(); }; return h("label", { class: "chk" }, cb, t("proj.rotary")); })(),
      h("label", { class: "f" }, h("span", {}, t("proj.rotdia")), numberInput(m.rotary_diameter ?? 100, v => { m.rotary_diameter = v; touch(); })))));

  // layers
  const count = new Map();
  for (const s of p.sheets) for (const v of s.vectors) count.set(v.layer, (count.get(v.layer) || 0) + 1);
  if (window.IyiPage.editor) out.push(window.IyiPage.editor.layersCard());      // (edit.js: add, rename, colour, lock, delete)
  else out.push(h("div", { class: "card" }, h("h2", {}, t("proj.layers")), h("div", { class: "list" }, p.layers.map(l => {
    const cb = h("input", { type: "checkbox", checked: l.visible });
    cb.onchange = () => { l.visible = cb.checked; persist(); };
    return h("label", { class: "item" }, h("span", { class: "dot", style: `background:${l.color}` }), h("span", { class: "grow" }, l.name), h("span", { class: "badge" }, count.get(l.id) || 0), cb);
  }))));

  if (hasFiles()) out.push(h("div", { class: "row", style: "margin-bottom:8px" },
    h("button", { class: "primary grow", onclick: () => guarded(() => saveProject(false)) }, "🖥 " + (S.file ? `${t("files.saveHere")}: ${baseOf(S.file.path)}` : t("files.saveTo"))),
    S.file ? h("button", { onclick: () => guarded(() => saveProject(true)) }, t("files.saveAs")) : null));
  out.push(h("div", { class: "row" },
    h("button", { class: "grow", onclick: () => download((p.name || "project").replace(/[^\w .-]+/g, "_") + ".icam", new Blob([JSON.stringify(p)], { type: "application/json" })) }, "💾 " + t("proj.save")),
    h("button", { class: "danger", onclick: () => { if (confirm(t("proj.closeask"))) { S.project = null; S.report = null; S.views = {}; setFile(null); if (window.IyiPage.editor) window.IyiPage.editor.clear(); changed(); } } }, t("proj.close"))));
  return h("div", {}, out);
}

/** the thickness of one sheet when it is not the material's (empty: the material's) */
function thicknessOverride(s) {
  const el = h("input", { type: "number", inputmode: "decimal", step: "any", value: s.thickness ?? "", placeholder: fmt(S.project.material.thickness, 2) });
  el.addEventListener("input", () => { s.thickness = Number.isFinite(el.valueAsNumber) ? el.valueAsNumber : null; touch(); });
  return el;
}

function viewDraw() {
  const s = activeSheet(), p = S.project;
  const wrap = h("div");
  if (p.sheets.length > 1) {
    const sel = h("select", { style: "margin:8px 16px;width:calc(100% - 32px)" }, p.sheets.map(x => h("option", { value: x.id }, x.name)));
    sel.value = s.id; sel.onchange = () => { S.sheetId = sel.value; render(); };
    wrap.append(sel);
  }
  const ed = window.IyiPage.editor;
  if (ed) wrap.append(ed.stage(s));                         // (edit.js: the drawing with its tools)
  else wrap.append(s.vectors.length || S.results ? drawStage(s) : h("div", { class: "empty" }, t("draw.none")));
  return wrap;
}

function moveToolpath(tp, d) {                       // (the order of the toolpaths is the order in the G-code)
  const list = S.project.toolpaths, i = list.indexOf(tp), j = i + d;
  if (j < 0 || j >= list.length) return;
  list.splice(i, 1); list.splice(j, 0, tp);
  changed();
}

function viewPaths() {
  const p = S.project;
  const items = p.toolpaths.map(tp => {
    const op = S.schema.operations.find(o => o.id === tp.op);
    const cb = h("input", { type: "checkbox", checked: tp.enabled, title: t("paths.on"), "aria-label": t("paths.on") });
    cb.onchange = () => { tp.enabled = cb.checked; touch(); };
    return h("div", { class: "item" }, cb,
      h("div", { class: "grow" }, h("b", {}, tp.name), h("div", { class: "muted small" }, `${op ? op.label : tp.op} · ${tp.tool.name}`)),
      h("button", { class: "small", title: t("paths.up"), "aria-label": t("paths.up"), onclick: () => moveToolpath(tp, -1) }, "↑"),
      h("button", { class: "small", title: t("paths.down"), "aria-label": t("paths.down"), onclick: () => moveToolpath(tp, 1) }, "↓"),
      h("button", { class: "small", title: t("paths.dup"), "aria-label": t("paths.dup"), onclick: () => { const c = JSON.parse(JSON.stringify(tp)); c.id = newId(); c.name = tp.name + " (2)"; if (c.sequence) delete c.sequence.order; p.toolpaths.splice(p.toolpaths.indexOf(tp) + 1, 0, c); changed(); } }, "⧉"),
      h("button", { class: "small", onclick: () => editToolpath(tp) }, "✎"));
  });
  return h("div", {}, h("div", { class: "card" },
    h("div", { class: "row sp" }, h("h2", { style: "margin:0" }, t("paths.title")), h("button", { class: "primary small", onclick: () => editToolpath(null) }, "＋ " + t("paths.add"))),
    items.length ? h("div", { class: "list", style: "margin-top:8px" }, items)
      : h("div", { class: "empty" }, h("div", {}, t("paths.none")), h("div", { class: "muted small" }, t("paths.none2")))));
}

async function guarded(fn) { if (S.busy) return; try { await fn(); } catch (e) { fail(e); } }

function viewOutput() {
  const p = S.project, sc = S.schema;
  if (!p.toolpaths.length) return h("div", { class: "empty" }, h("div", { class: "big" }, "🛠"), h("div", {}, t("out.needpaths")));
  if (!S.post || !sc.posts.some(x => x.id === S.post)) S.post = (sc.posts.find(x => x.id === "mach3") || sc.posts[0] || {}).id || "";
  const post = h("select", {}, sc.posts.map(x => h("option", { value: x.id }, `${x.name} (.${x.extension})`)));
  post.value = S.post; post.onchange = () => { S.post = post.value; store.set("iyicam.post", S.post); S.gcode = null; render(); };
  const stem = (p.name || "job").replace(/[^\w .-]+/g, "_");
  const out = [];

  out.push(h("div", { class: "card" }, h("h2", {}, t("out.post")), post,
    h("div", { class: "row", style: "margin-top:12px" },
      h("button", { class: "grow", onclick: () => guarded(async () => {
        const r = await runJob("compute", { project: p, preview: true });
        applyPatch(r.patch); S.results = r; S.gcode = S.sim = null; render();
      }) }, "▶ " + t("out.compute")),
      h("button", { class: "primary grow", onclick: () => guarded(async () => {
        const r = await runJob("gcode", { project: p, post: S.post, file_name: stem });
        S.gcode = r; render();
        if (!r.ok) toast(t("out.nogcode"), "bad");
      }) }, "⚙ " + t("out.gcode"))),
    h("div", { class: "row", style: "margin-top:8px" },
      h("button", { class: "grow", onclick: () => guarded(async () => {
        const sh = activeSheet(), r = await runJob("simulate", { project: p, sheet: sh.id });
        S.sim = { ...r, sheetName: sh.name }; render();
      }) }, "🧊 " + t("out.sim")))));

  if (S.results) {
    const r = S.results, sheets = new Map(p.sheets.map(s => [s.id, s.name]));
    out.push(h("div", { class: "card" }, h("h2", {}, t("out.results")), h("div", { class: "list" }, r.results.map(it => {
      const msgs = [];
      if (it.error) msgs.push(h("div", { class: "bad small" }, it.error));
      for (const w of it.warnings.slice(0, 3)) msgs.push(h("div", { class: "warn small" }, w));
      if (it.warnings.length > 3) msgs.push(h("div", { class: "muted small" }, `+${it.warnings.length - 3}`));
      return h("div", { class: "item", style: "align-items:flex-start" },
        h("div", { class: "grow" }, h("b", {}, it.name), h("div", { class: "muted small" }, `${sheets.get(it.sheet) || ""}${it.enabled ? "" : " · off"}`), msgs),
        h("div", { class: "muted small", style: "text-align:right" }, it.enabled ? [h("div", {}, `${t("out.time")} ${fmtTime(it.stats.time_s)}`), h("div", {}, `${t("out.cut")} ${fmt(it.stats.feed_length / 1000, 2)} m`)] : null));
    })), h("div", { class: "row", style: "margin-top:10px" }, h("button", { class: "small", onclick: () => { S.tab = "draw"; render(); } }, "🗺 " + t("out.show")))));
  }

  if (S.gcode) {
    const g = S.gcode;
    out.push(h("div", { class: "card" }, h("h2", {}, t("out.files")),
      g.errors.map(e => h("div", { class: "msg bad" }, e)), g.warnings.slice(0, 5).map(w => h("div", { class: "msg warn" }, w)),
      h("div", { class: "list" }, g.files.map(f => {
        const pre = h("pre", { class: "code", hidden: true }), lines = f.text.split("\n").length;
        const show = h("button", { class: "small" }, t("out.view"));
        show.onclick = () => { pre.hidden = !pre.hidden; if (!pre.hidden && !pre.textContent) pre.textContent = f.text.split("\n").slice(0, 400).join("\n") + (lines > 400 ? "\n…" : ""); show.textContent = pre.hidden ? t("out.view") : t("out.hide"); };
        return h("div", { class: "item", style: "flex-wrap:wrap" },
          h("div", { class: "grow" }, h("b", {}, f.name), h("div", { class: "muted small" }, `${t("out.lines", fmt(lines, 0))} · ${fmt(f.text.length / 1024, 0)} KB`)),
          show, h("button", { class: "small primary", onclick: () => download(f.name, new Blob([f.text], { type: "text/plain" })) }, "⬇ " + t("out.download")), pre);
      })),
      hasFiles() && g.files.length ? h("div", { class: "row", style: "margin-top:10px" },
        h("button", { class: "grow", onclick: () => guarded(() => saveToComputer(g.files.map(f => ({ name: f.name, text: f.text })))) }, "🖥 " + t("files.saveTo"))) : null));
  }

  if (S.sim) {
    out.push(h("div", { class: "card" }, h("h2", {}, t("out.sim")),
      h("div", { class: "muted small" }, t("out.simdone", S.sim.sheetName) + ` · ${t("out.time")} ${fmtTime(S.sim.time_s)}`),
      h("img", { class: "sim", alt: "simulation", src: "data:image/png;base64," + S.sim.png_b64 })));
  }

  const exp = fmtId => h("button", { class: "grow", onclick: () => guarded(async () => {
    const r = await api("export", { project: p, format: fmtId, file_name: stem });
    if (hasFiles() && S.toComputer) await saveToComputer(r.files.map(f => ({ name: f.name, data_b64: f.data_b64 })));
    else for (const f of r.files) download(f.name, b64blob(f.data_b64, f.mime));
  }) }, (hasFiles() && S.toComputer ? "🖥 " : "⬇ ") + fmtId.toUpperCase());
  const toComp = h("input", { type: "checkbox", checked: S.toComputer });
  toComp.onchange = () => { S.toComputer = toComp.checked; store.set("iyicam.tocomputer", S.toComputer ? "1" : "0"); render(); };
  out.push(h("div", { class: "card" }, h("h2", {}, t("out.export")),
    hasFiles() ? h("label", { class: "chk" }, toComp, t("files.toComputer")) : null,
    h("div", { class: "row" }, ["dxf", "svg", "icam"].filter(x => sc.exporters.some(e => e.extension === x)).map(exp))));
  return h("div", {}, out);
}

// ------------------------------------------------------------------------------------------ shell
const TABS = [["project", "📁", "tab.project"], ["draw", "🗺", "tab.draw"], ["paths", "🛠", "tab.paths"], ["out", "📤", "tab.out"]];

function render() {
  const v = $("#view");
  if (!v) return;
  v.className = S.tab === "draw" && S.project ? "wide" : "";
  v.replaceChildren(view());
  document.querySelectorAll("nav.tabs button").forEach(b => b.classList.toggle("on", b.dataset.tab === S.tab));
  window.scrollTo(0, 0);
}

function mountApp() {
  document.documentElement.lang = lang;          // (upper-case headings follow it: "file" must not become "FİLE")
  const app = $("#app");
  app.replaceChildren(
    h("header", { class: "top" }, h("h1", {}, "iyiCAM", h("span", { class: "sub" }, S.schema ? "v" + S.schema.iyicam : "")),
      h("button", { class: "small", onclick: () => { lang = lang === "tr" ? "en" : "tr"; store.set("iyicam.lang", lang); mountApp(); } }, lang === "tr" ? "EN" : "TR"),
      h("button", { class: "small", onclick: async () => { await fetch("/api/logout", { method: "POST", credentials: "same-origin", headers: { "Content-Type": "application/json" }, body: "{}" }).catch(() => {}); showLogin(); } }, t("logout"))),
    h("div", { id: "busy", class: "card", hidden: true, style: "margin:8px 16px 0" },
      h("div", { class: "row sp" }, h("span", { class: "lab" }, t("busy")), h("button", { class: "small", onclick: () => { if (S.busy) S.busy.stop = true; } }, t("cancel"))),
      h("div", { class: "progress" }, h("i"))),
    h("main", { id: "view" }),
    h("nav", { class: "tabs" }, TABS.map(([id, ic, k]) => h("button", { "data-tab": id, onclick: () => { S.tab = id; render(); } }, h("span", { class: "ic" }, ic), t(k)))));
  setBusy(S.busy);
  render();
}

function showLogin(note) {
  document.documentElement.lang = lang;
  const key = h("input", { type: "password", autocomplete: "off", autocapitalize: "characters", spellcheck: "false", placeholder: "XXXXXX-XXXXXX-XXXXXX-XXXXXX", "aria-label": t("login.key") });
  const msg = h("div", { class: "msg bad", hidden: true });
  const go = async e => {
    e.preventDefault();
    msg.hidden = true;
    try {
      await api("login", { key: key.value });
      await start();
    } catch (err) {
      msg.hidden = false;
      msg.textContent = err.code === "invalid_key" ? t("login.bad") : err.code === "too_many_attempts" ? t("login.wait", err.body.retry_after) : err.text;
      key.select();
    }
  };
  $("#app").replaceChildren(h("form", { class: "login", onsubmit: go },
    h("h1", {}, "iyiCAM"), h("p", { class: "muted" }, note || t("login.hint")),
    h("label", { class: "f" }, h("span", {}, t("login.key")), key), msg,
    h("div", { style: "margin-top:16px" }, h("button", { class: "primary", style: "width:100%", type: "submit" }, t("login.go")))));
  key.focus();
}
function signedOut() { if (!$(".login")) showLogin(t("err.session")); }

async function start() {
  // the two calls go together (through a hub each one has a way to the computer and back)
  [S.schema, S.files] = await Promise.all([api("schema"), api("status").catch(() => null)]);   // (no files, or an older server: null)
  if (!S.project) {
    const saved = store.get("iyicam.project");
    if (saved) {
      try { S.project = (await api("validate", { project: JSON.parse(saved) })).project; } catch { store.del("iyicam.project"); }
    }
    if (S.project) {                                  // the file it came from is remembered with it
      try { const f = JSON.parse(store.get("iyicam.file") || "null"); S.file = f && f.path && f.hash ? f : null; } catch { S.file = null; }
    }
  }
  if (S.project) S.sheetId = S.project.sheets[0].id;
  mountApp();
}

// what edit.js (the drawing tools) needs of this file; it adds ``editor`` to it
window.IyiPage = { S, h, $, t, TXT, api, runJob, toast, fail, openSheet, paramsForm, numberInput, fmt, store, guarded, render, touch,
                   persist, changed, activeSheet, layerMap, flatOf, drawScene, fitView, setFile, editor: null,
                   sceneSegs, lodGroups };

(async () => {
  let note;
  try {
    const r = await fetch("/api/session", { credentials: "same-origin" });
    if (r.ok) return await start();
  } catch (e) {          // (the login page says what is wrong when it cannot be reached; a hub whose agent is away says so)
    if (e instanceof ApiError && e.code === "agent_offline") note = e.text;
  }
  showLogin(note);
})();

})();

/* iyiCAM page: plugins (look at them, install one, switch it on or off, reload it, remove it).
 *
 * It sits on app.js (through ``window.IyiPage``).  A plugin is Python code that runs on the engine (the server, or the PC
 * of the agent), so what is installed here is a decision of the person who owns the engine: it is asked for twice (a warning
 * with a tick) and the engine can turn it off.  What a plugin registers (vector commands, toolpath types, importers and
 * exporters, posts, G-code filters) is in the engine at once; the page builds its lists from ``schema``, so after an
 * install, a switch or a removal it asks for the schema again and every list follows (the commands of a plugin are in
 * the ⋯ button of the Drawing tab, its toolpath types on the Toolpaths tab, its file formats on Output and Project). */
(function () {
  "use strict";
  const B = window.IyiPage;
  if (!B) return;
  const { S, h, t, api, toast, fail, openSheet, TXT, guarded, render } = B;

  Object.assign(TXT.tr, {
    "plug.title": "Eklentiler", "plug.hint": "Eklenti, motorun (sunucu ya da ajanın bilgisayarı) üzerinde çalışan bir Python dosyasıdır; vektör komutları, kesim türleri, dosya biçimleri ve postlar ekleyebilir. Eklediği komutlar Çizim sekmesindeki ⋯ düğmesinde, kesim türleri Yollar sekmesinde görünür.",
    "plug.none": "Kurulu eklenti yok.", "plug.install": "Eklenti kur (.py / .zip)", "plug.folder": "Klasör: {0}",
    "plug.warnTitle": "Eklenti kur", "plug.warn": "Bir eklenti bir programdır: motorun çalıştığı kullanıcının tüm yetkileriyle çalışır (dosyaları okuyabilir, yazabilir, ağa çıkabilir). Yalnız güvendiğin kaynaktan gelen dosyayı kur.",
    "plug.trust": "Bu dosyaya güveniyorum", "plug.go": "Kur", "plug.file": "Dosya: {0} ({1} KB)",
    "plug.installed": "«{0}» kuruldu.", "plug.failed": "«{0}» yüklenemedi: aşağıdaki hataya bak.", "plug.off": "Eklenti kurmak bu motorda kapalı (sahibi kapatmış).",
    "plug.pluginsOff": "Eklentiler bu motorda kapalı.", "plug.on": "Açık", "plug.disabled": "Kapalı", "plug.reload": "Yeniden yükle", "plug.remove": "Kaldır",
    "plug.removeAsk": "«{0}» kaldırılsın mı? Dosyası silinir.", "plug.removed": "«{0}» kaldırıldı.", "plug.error": "Hata ayrıntısı", "plug.by": "{0}",
    "plug.cmds": "{0} komut", "plug.ops": "{0} kesim türü", "plug.imp": "{0} içe aktarma", "plug.exp": "{0} dışa aktarma", "plug.posts": "{0} post",
    "plug.filters": "{0} G-code süzgeci", "plug.hooks": "{0} olay", "plug.gui": "masaüstü penceresi (burada yok)", "plug.nothing": "hiçbir şey eklemedi",
    "plug.installedPkg": "Python paketi olarak kurulu (buradan kaldırılamaz)", "plug.reloaded": "«{0}» yeniden yüklendi.",
    "e.invalid_plugin": "Eklenti dosyası kabul edilmedi.", "e.plugin_install_off": "Eklenti kurmak bu motorda kapalı.",
    "e.plugins_off": "Eklentiler bu motorda kapalı.", "e.plugin_not_found": "Böyle bir eklenti yok."
  });
  Object.assign(TXT.en, {
    "plug.title": "Plugins", "plug.hint": "A plugin is a Python file that runs on the engine (the server, or the PC of the agent); it can add vector commands, toolpath types, file formats and posts. Its commands are in the ⋯ button of the Drawing tab, its toolpath types on the Toolpaths tab.",
    "plug.none": "No plugin is installed.", "plug.install": "Install a plugin (.py / .zip)", "plug.folder": "Folder: {0}",
    "plug.warnTitle": "Install a plugin", "plug.warn": "A plugin is a program: it runs with all the rights of the user the engine runs as (it can read and write files and reach the network). Install only a file from a source you trust.",
    "plug.trust": "I trust this file", "plug.go": "Install", "plug.file": "File: {0} ({1} KB)",
    "plug.installed": "“{0}” is installed.", "plug.failed": "“{0}” could not be loaded: see the error below.", "plug.off": "Installing plugins is turned off on this engine (its owner did it).",
    "plug.pluginsOff": "Plugins are turned off on this engine.", "plug.on": "On", "plug.disabled": "Off", "plug.reload": "Reload", "plug.remove": "Remove",
    "plug.removeAsk": "Remove “{0}”? Its file is deleted.", "plug.removed": "“{0}” was removed.", "plug.error": "Error details", "plug.by": "{0}",
    "plug.cmds": "{0} command(s)", "plug.ops": "{0} toolpath type(s)", "plug.imp": "{0} importer(s)", "plug.exp": "{0} exporter(s)", "plug.posts": "{0} post(s)",
    "plug.filters": "{0} G-code filter(s)", "plug.hooks": "{0} hook(s)", "plug.gui": "desktop windows (not here)", "plug.nothing": "added nothing",
    "plug.installedPkg": "Installed as a Python package (cannot be removed here)", "plug.reloaded": "“{0}” was reloaded.",
    "e.invalid_plugin": "The plugin file was not accepted.", "e.plugin_install_off": "Installing plugins is turned off on this engine.",
    "e.plugins_off": "Plugins are turned off on this engine.", "e.plugin_not_found": "There is no such plugin."
  });

  let cache = null;                 // what the engine said last: {plugins, install, plugins_on, folder, limits}
  let loading = false;

  /** the lists of the page follow what the engine has now (commands, toolpath types, formats) */
  async function refreshSchema() {
    S.schema = await api("schema");
    render();
  }

  /** the file as base64 (a big one in pieces: one call with the whole of it would be too much for a phone) */
  async function b64(file) {
    const buf = new Uint8Array(await file.arrayBuffer());
    let out = "";
    for (let i = 0; i < buf.length; i += 0x8000) out += String.fromCharCode.apply(null, buf.subarray(i, i + 0x8000));
    return btoa(out);
  }

  function summary(p) {
    const v = p.provides || {}, parts = [];
    if ((v.commands || []).length) parts.push(t("plug.cmds", v.commands.length));
    if ((v.operations || []).length) parts.push(t("plug.ops", v.operations.length));
    if ((v.importers || []).length) parts.push(t("plug.imp", v.importers.length));
    if ((v.exporters || []).length) parts.push(t("plug.exp", v.exporters.length));
    if ((v.posts || []).length) parts.push(t("plug.posts", v.posts.length));
    if (v.gcode_filters) parts.push(t("plug.filters", v.gcode_filters));
    if ((v.hooks || []).length) parts.push(t("plug.hooks", v.hooks.length));
    if (v.gui) parts.push(t("plug.gui"));
    return parts.length ? parts.join(" · ") : (p.loaded ? t("plug.nothing") : "");
  }

  async function act(p, action) {
    const r = await api("plugin_set", { key: p.key, action });
    cache = r;
    await refreshSchema();
    if (action === "remove") toast(t("plug.removed", p.name), "ok");
    else if (action === "reload") toast(t("plug.reloaded", p.name), "ok");
  }

  function installDialog(file) {
    const trust = h("input", { type: "checkbox" });
    const go = h("button", { class: "primary grow", disabled: true }, t("plug.go"));
    trust.onchange = () => { go.disabled = !trust.checked; };
    let close;
    go.onclick = () => { close(); guarded(async () => {
      const r = await api("plugin_install", { name: file.name, data_b64: await b64(file) });
      cache = r;
      const p = r.plugin;
      if (p.error) toast(t("plug.failed", p.name || p.key), "bad");
      else toast(t("plug.installed", p.name || p.key), "ok");
      await refreshSchema();
    }); };
    close = openSheet(t("plug.warnTitle"), h("div", {},
      h("div", { class: "msg bad" }, t("plug.warn")),
      h("div", { class: "muted small", style: "margin:8px 0" }, t("plug.file", file.name, Math.max(1, Math.round(file.size / 1024)))),
      h("label", { class: "chk" }, trust, t("plug.trust")),
      h("div", { class: "row", style: "margin-top:14px" }, go, h("button", { onclick: () => close() }, t("ed.cancel")))));
  }

  /** the card of the Project tab */
  function card() {
    const box = h("div", { class: "card plugins" });
    const build = () => {
      const c = cache;
      const pick = h("input", { type: "file", accept: ".py,.zip", hidden: true, onchange: e => { const f = e.target.files[0]; e.target.value = ""; if (f) installDialog(f); } });
      const kids = [h("h2", {}, t("plug.title")), h("div", { class: "muted small" }, t("plug.hint"))];
      if (!c) kids.push(h("div", { class: "muted" }, "…"));
      else {
        if (!c.plugins_on) kids.push(h("div", { class: "msg" }, t("plug.pluginsOff")));
        kids.push(h("div", { class: "list" }, c.plugins.length ? c.plugins.map(p => {
          const sw = h("input", { type: "checkbox", checked: p.enabled });
          sw.onchange = () => guarded(() => act(p, sw.checked ? "enable" : "disable"));
          return h("div", { class: "item", style: "flex-wrap:wrap" },
            h("div", { class: "grow" },
              h("b", {}, p.name), p.version ? h("span", { class: "muted small" }, " v" + p.version) : null,
              p.author ? h("span", { class: "muted small" }, " · " + p.author) : null,
              p.description ? h("div", { class: "muted small" }, p.description) : null,
              summary(p) ? h("div", { class: "small" }, summary(p)) : null,
              p.kind === "installed" ? h("div", { class: "muted small" }, t("plug.installedPkg")) : null,
              p.error ? h("details", {}, h("summary", { class: "bad" }, "⚠ " + t("plug.error")), h("pre", { class: "small", style: "white-space:pre-wrap;overflow:auto" }, p.error)) : null),
            h("label", { class: "chk", title: p.enabled ? t("plug.on") : t("plug.disabled") }, sw, p.enabled ? t("plug.on") : t("plug.disabled")),
            h("div", { class: "row", style: "width:100%;margin-top:6px" },
              p.enabled ? h("button", { class: "small", onclick: () => guarded(() => act(p, "reload")) }, "↻ " + t("plug.reload")) : null,
              p.removable && c.install ? h("button", { class: "small danger", onclick: () => { if (confirm(t("plug.removeAsk", p.name))) guarded(() => act(p, "remove")); } }, t("plug.remove")) : null));
        }) : h("div", { class: "muted" }, t("plug.none"))));
        kids.push(h("div", { class: "muted small" }, t("plug.folder", c.folder)));
        if (c.install) kids.push(h("div", { class: "row", style: "margin-top:10px" }, h("button", { class: "grow", onclick: () => pick.click() }, "🧩 " + t("plug.install")), pick));
        else kids.push(h("div", { class: "msg" }, c.note || t("plug.off")));
      }
      box.replaceChildren(...kids);
    };
    build();
    if (!loading && !cache) {
      loading = true;
      api("plugins").then(r => { cache = r; }).catch(e => {
        const away = e && (e.code === "needs_agent" || e.code === "agent_offline");       // (a hub whose agent is away: said on the card, not as an error)
        cache = { plugins: [], install: false, plugins_on: true, folder: "", note: away ? e.text : "" };
        if (!away) fail(e);
      })
        .finally(() => { loading = false; if (box.isConnected) build(); });
    }
    return box;
  }

  /** forget what was asked (a new project, another engine): the next card asks again */
  function reset() { cache = null; }
  window.addEventListener("iyicam:engine", reset);       // (the engine that answers changed: ask again)

  B.plugins = { card, reset, refresh: refreshSchema };
})();

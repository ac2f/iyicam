/* iyiCAM page: post-processors (make one, change it, try it) and what the words of G-code mean (I J K R F ...).
 *
 * It sits on app.js (through ``window.IyiPage``), like edit.js.  The engine describes every option, block and variable
 * of a post (``posts`` operation: ``specs``), so the forms here are built from that and a new option of the engine shows
 * up without a change in this file.  A post is edited as a *draft* (the whole post as an object) and saved by
 * ``post_save``, which writes only what differs from the post it is based on.  ``post_preview`` writes a small sample
 * job with the draft, and ``gcode_explain`` says what a line of G-code means (the engine knows the position the tool is
 * at when the line starts, so an arc's centre can be given as a place on the sheet). */
(function () {
  "use strict";
  const B = window.IyiPage;
  if (!B) return;
  const { S, h, $, t, api, toast, fail, openSheet, numberInput, TXT, guarded, render, store } = B;
  const L = () => B.lang();

  // ------------------------------------------------------------------------------------------ texts
  Object.assign(TXT.tr, {
    "post.edit": "Düzenle", "post.copy": "Kopyasını yap", "post.make": "Bunu temel alıp kendi postumu yap", "post.mine": "benim",
    "post.titleEdit": "Postu düzenle", "post.titleNew": "Yeni post", "post.name": "Ad", "post.desc": "Açıklama",
    "post.ext": "Dosya uzantısı", "post.based": "Temel aldığı post: {0}", "post.mine2": "Bu post programla gelir ve değişmez; kaydedersen kendi postun olur.",
    "post.options": "Ayarlar", "post.blocks": "G-code blokları", "post.blocksHint": "Her blok G-code satırlarıdır. {ad} yazılan yerlere program değeri koyar (örneğin {safe_z}); düğmelerle ekleyebilirsin.",
    "post.words": "Kelimeler", "post.vars": "Değişkenler", "post.changed": "Değişti", "post.reset": "İlk haline getir",
    "post.save": "Kaydet", "post.preview": "Önizle", "post.cancel": "Vazgeç", "post.delete": "Sil", "post.delask": "«{0}» silinsin mi?",
    "post.saved": "«{0}» kaydedildi.", "post.deleted": "«{0}» silindi.", "post.noname": "Posta bir ad ver.",
    "post.previewTitle": "Önizleme", "post.previewNote": "Küçük bir örnek parça: yuvarlak köşeli bir levha, bir delik ve bir cep. Yaylar, delme ve iki takım görünür.",
    "post.badfiles": "Yüklenemeyen post dosyaları: {0}",
    "gx.help": "I J K ne demek?", "gx.tap": "Bir satıra dokun: ne dediğini göster.", "gx.more": "{0} satır daha göster",
    "gx.glossary": "G-code sözlüğü", "gx.letters": "Harfler", "gx.g": "G kodları (hareket ve mod)", "gx.m": "M kodları (makine)",
    "gx.arcTitle": "Yay satırı nasıl okunur?", "gx.arc1": "G2 saat yönünde, G3 saat yönünün tersine bir yay keser. X ve Y yayın BİTİŞ noktasıdır; yay, kesicinin o anda bulunduğu noktadan başlar.",
    "gx.arc2": "Yayın merkezi I ve J ile verilir. I, merkezin X yönünde, J ise Y yönünde yayın BAŞLANGIÇ noktasından ne kadar uzakta olduğudur. Yani I ve J sacda bir konum değil, bir uzaklıktır (eksi: sola ya da geriye).",
    "gx.arc3": "Bazı kontrolcüler I J yerine R (yarıçap) ister, bazıları yayı hiç bilmez ve kısa doğrularla kesilir; bunlar post ayarlarından seçilir (Yaylar bölümü).",
    "gx.start": "Başlangıç", "gx.end": "Bitiş", "gx.centre": "Merkez", "gx.none": "Açıklama yok."
  });
  Object.assign(TXT.en, {
    "post.edit": "Edit", "post.copy": "Make a copy", "post.make": "Make my own post from this one", "post.mine": "mine",
    "post.titleEdit": "Edit post", "post.titleNew": "New post", "post.name": "Name", "post.desc": "Description",
    "post.ext": "File extension", "post.based": "Based on: {0}", "post.mine2": "This post comes with the program and does not change; saving makes it your own post.",
    "post.options": "Settings", "post.blocks": "G-code blocks", "post.blocksHint": "Each block is lines of G-code. Where {name} is written the program puts its value (for example {safe_z}); the buttons add them.",
    "post.words": "Words", "post.vars": "Variables", "post.changed": "Changed", "post.reset": "Back to the first",
    "post.save": "Save", "post.preview": "Preview", "post.cancel": "Cancel", "post.delete": "Delete", "post.delask": "Delete “{0}”?",
    "post.saved": "“{0}” saved.", "post.deleted": "“{0}” deleted.", "post.noname": "Give the post a name.",
    "post.previewTitle": "Preview", "post.previewNote": "A small sample part: a plate with rounded corners, a hole and a pocket. Arcs, drilling and two tools show.",
    "post.badfiles": "Post files that could not be loaded: {0}",
    "gx.help": "What are I J K?", "gx.tap": "Tap a line to see what it says.", "gx.more": "Show {0} more lines",
    "gx.glossary": "G-code words", "gx.letters": "Letters", "gx.g": "G codes (moves and modes)", "gx.m": "M codes (machine)",
    "gx.arcTitle": "How to read an arc line", "gx.arc1": "G2 cuts an arc clockwise, G3 counter-clockwise. X and Y are where the arc ENDS; it starts where the tool is at that moment.",
    "gx.arc2": "The centre of the arc is given by I and J. I is how far the centre is from the START of the arc along X, J along Y. So I and J are not a place on the sheet, they are a distance (minus: to the left or toward you).",
    "gx.arc3": "Some controllers want R (the radius) instead of I J, and some do not know arcs at all and cut short straight lines; that is chosen in the settings of the post (the Arcs part).",
    "gx.start": "Start", "gx.end": "End", "gx.centre": "Centre", "gx.none": "No explanation."
  });

  // ------------------------------------------------------------------------------------------ what the engine says
  async function ensurePosts() {
    if (!S.postsInfo) S.postsInfo = await api("posts");
    return S.postsInfo;
  }
  /** after a save or a delete the engine answers with the posts as they are now */
  function adopt(answer) {
    S.postsInfo = { ...S.postsInfo, ...answer };
    S.schema.posts = answer.posts.map(p => ({ id: p.id, name: p.name, extension: p.extension, description: p.description,
                                              builtin: p.builtin, editable: p.editable }));
  }
  const clone = o => JSON.parse(JSON.stringify(o));
  const lab = o => o[L()] || o.en || "";

  // ------------------------------------------------------------------------------------------ G-code you can ask
  const WORD = /([A-Za-z])(\s*[-+]?(?:\d+\.?\d*|\.\d+))/g;
  /** the line as pieces: the words coloured by what they are (a comment stays plain) */
  function pieces(line) {
    const out = [], m = /\([^)]*\)|;.*$/.exec(line);
    const body = m ? line.slice(0, m.index) : line, tail = m ? line.slice(m.index) : "";
    let at = 0;
    for (const w of body.matchAll(WORD)) {
      if (w.index > at) out.push(body.slice(at, w.index));
      const c = w[1].toUpperCase();
      out.push(h("span", { class: "gw gw-" + (c === "G" || c === "M" ? "c" : "IJKR".includes(c) ? "a" : "p") }, w[0]));
      at = w.index + w[0].length;
    }
    if (at < body.length) out.push(body.slice(at));
    if (tail) out.push(h("span", { class: "gc" }, tail));
    return out.length ? out : [line || " "];
  }

  /** G-code as lines to tap: what the line says is read from the engine, in the language of the page */
  function viewer(text, postId) {
    const all = text.replace(/\r\n/g, "\n").split("\n");
    if (all.length && all[all.length - 1] === "") all.pop();
    const STEP = 400, CAP = 5000;
    const list = h("div", { class: "gl-list" }), more = h("button", { class: "small", style: "margin-top:8px" });
    const cache = new Map();
    let shown = 0, open = null;

    async function explain(i) {
      const key = L() + ":" + i;
      if (cache.has(key)) return cache.get(key);
      const from = Math.max(0, i - (CAP - 1));
      const r = await api("gcode_explain", { lines: all.slice(from, i + 1), index: i - from, lang: L(), post: postId || "" });
      cache.set(key, r);
      return r;
    }
    function panel(r) {
      const words = (r.words || []).map(w => h("div", { class: "gxw" }, h("b", {}, w.word), h("span", {}, w.text)));
      return h("div", { class: "gx" }, h("div", { class: "gxs" }, r.summary || t("gx.none")), words);
    }
    async function toggle(i, row) {
      if (open && open.row === row) { open.box.remove(); row.classList.remove("on"); open = null; return; }
      if (open) { open.box.remove(); open.row.classList.remove("on"); open = null; }
      const box = h("div", { class: "gx" }, "…");
      row.classList.add("on"); row.after(box); open = { row, box };
      try {
        const r = await explain(i);
        if (open && open.box === box) box.replaceWith((open.box = panel(r)));
      } catch (e) { box.remove(); row.classList.remove("on"); open = null; fail(e); }
    }
    function addRows() {
      const to = Math.min(all.length, shown + STEP, CAP);
      for (let i = shown; i < to; i++) {
        const row = h("div", { class: "gl", role: "button", tabindex: "0" }, h("span", { class: "gn" }, String(i + 1)), h("span", { class: "gt" }, pieces(all[i])));
        row.onclick = () => toggle(i, row);
        row.onkeydown = e => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); toggle(i, row); } };
        list.append(row);
      }
      shown = to;
      more.hidden = shown >= Math.min(all.length, CAP);
      more.textContent = t("gx.more", Math.min(STEP, Math.min(all.length, CAP) - shown));
    }
    more.onclick = addRows;
    addRows();
    return h("div", { class: "gcode" }, h("div", { class: "muted small" }, t("gx.tap")), list, more);
  }

  // ------------------------------------------------------------------------------------------ the words of G-code
  /** an arc and what I and J are, drawn: the start S, the end E, the centre C, I along X and J along Y from the start */
  function arcDiagram() {
    const box = h("div", { class: "arcfig" });
    box.innerHTML = `<svg viewBox="0 0 230 150" role="img" aria-label="I J" xmlns="http://www.w3.org/2000/svg">
      <path d="M50 120 A72.1 72.1 0 0 1 70 20" class="fa"/>
      <path d="M50 120 H110 V80" class="fd"/><circle cx="110" cy="80" r="4" class="fc"/>
      <circle cx="50" cy="120" r="4" class="fs"/><circle cx="70" cy="20" r="4" class="fs"/>
      <text x="26" y="124">S</text><text x="76" y="16">E</text><text x="118" y="78">C</text>
      <text x="76" y="138" class="fi">I</text><text x="116" y="104" class="fi">J</text></svg>`;
    return box;
  }
  async function glossary() {
    const info = await ensurePosts();
    const g = info.glossary[L()] || info.glossary.en;
    const group = (title, key) => [h("div", { class: "group-h" }, title),
      ...g.filter(x => x.group === key).map(x => h("div", { class: "gli" }, h("b", {}, x.word), h("span", {}, x.text)))];
    openSheet(t("gx.glossary"), h("div", {},
      h("div", { class: "group-h" }, t("gx.arcTitle")), arcDiagram(),
      h("p", {}, t("gx.arc1")), h("p", {}, t("gx.arc2")), h("p", {}, t("gx.arc3")),
      group(t("gx.letters"), "letter"), group(t("gx.g"), "g"), group(t("gx.m"), "m")));
  }

  // ------------------------------------------------------------------------------------------ the editor
  const same = (a, b) => JSON.stringify(a) === JSON.stringify(b);

  /** which options matter given the others (the rest are hidden, not removed) */
  const SHOWN = {
    line_number_start: o => o.line_numbers, line_number_step: o => o.line_numbers, line_number_max: o => o.line_numbers,
    line_number_space: o => o.line_numbers, ijk: o => o.arcs === "ijk", arc_max_sweep: o => o.arcs !== "none",
    arc_min_radius: o => o.arcs !== "none", arc_max_radius: o => o.arcs !== "none", helical: o => o.arcs !== "none",
    arc_xy_always: o => o.arcs !== "none",
  };

  function insertAt(ta, text) {
    const a = ta.selectionStart ?? ta.value.length, b = ta.selectionEnd ?? a;
    ta.setRangeText(text, a, b, "end");
    ta.dispatchEvent(new Event("input"));
    ta.focus();
  }

  function editor(base, mode) {
    const sp = S.postsInfo.specs, orig = clone(base), d = clone(base);
    if (mode === "copy") {
      d.id = ""; d.extends = base.id; d.name = `${base.name} (${t("post.mine")})`;
    }
    const refreshers = [], refresh = () => refreshers.forEach(f => f());
    const watch = fn => { refreshers.push(fn); fn(); return fn; };
    const chg = (isChanged) => { const m = h("span", { class: "chg", title: t("post.changed") }, "●"); watch(() => { m.hidden = !isChanged(); }); return m; };

    // the name, what it is
    const name = h("input", { type: "text", value: d.name, maxlength: "80" });
    name.oninput = () => { d.name = name.value; };
    const desc = h("input", { type: "text", value: d.description || "", maxlength: "300" });
    desc.oninput = () => { d.description = desc.value; };
    const ext = h("input", { type: "text", value: d.extension || "nc", maxlength: "8", autocapitalize: "off", autocomplete: "off", spellcheck: "false" });
    ext.oninput = () => { d.extension = ext.value.trim().replace(/^\./, "").toLowerCase(); };
    const parent = d.extends && S.postsInfo.posts.find(p => p.id === d.extends);
    const head = h("div", {},
      mode === "copy" && base.builtin ? h("div", { class: "msg" }, t("post.mine2")) : null,
      h("label", { class: "f" }, h("span", {}, t("post.name")), name),
      h("label", { class: "f" }, h("span", {}, t("post.desc")), desc),
      h("label", { class: "f" }, h("span", {}, t("post.ext")), ext),
      parent ? h("div", { class: "muted small", style: "margin-top:6px" }, t("post.based", parent.name)) : null);

    // the settings, a group at a time
    const field = s => {
      const k = s.key, cur = () => d.options[k];
      let input;
      if (s.kind === "bool") {
        const cb = h("input", { type: "checkbox", checked: !!cur() });
        cb.onchange = () => { d.options[k] = cb.checked; refresh(); };
        input = h("label", { class: "chk" }, cb, lab(s.label), chg(() => !same(cur(), orig.options[k])));
      } else {
        let el;
        if (s.kind === "choice") {
          el = h("select", {}, s.choices.map(([v, l]) => h("option", { value: v }, lab(l))));
          el.value = String(cur());
          el.onchange = () => { d.options[k] = el.value; refresh(); };
        } else if (s.kind === "str") {
          el = h("input", { type: "text", value: cur() ?? "", maxlength: "8", autocapitalize: "off", spellcheck: "false" });
          el.oninput = () => { d.options[k] = el.value; refresh(); };
        } else {
          el = numberInput(cur(), v => { d.options[k] = s.kind === "int" ? Math.round(v) : v; refresh(); },
                           { kind: s.kind, min: s.min, max: s.max });
        }
        input = h("label", { class: "f" }, h("span", {}, lab(s.label), " ", chg(() => !same(cur(), orig.options[k]))), el);
      }
      const help = lab(s.help);
      const box = h("div", { class: "opt" }, input, help ? h("div", { class: "muted small" }, help) : null);
      const need = SHOWN[k];
      if (need) watch(() => { box.hidden = !need(d.options); });
      return box;
    };
    const optBox = h("div", {}, sp.groups.map(g => h("details", { class: "grp", open: g.id === "units" },
      h("summary", {}, lab(g.label)), sp.options.filter(o => o.group === g.id).map(field))));

    // the blocks of text
    const block = s => {
      const nm = s.name, cur = () => d.blocks[nm] ?? "";
      const lines = (cur().match(/\n/g) || []).length + 1;
      const ta = h("textarea", { class: "mono", rows: String(Math.min(12, Math.max(3, lines + 1))), spellcheck: "false", autocapitalize: "off", autocomplete: "off", wrap: "off" });
      ta.value = cur();
      ta.oninput = () => { d.blocks[nm] = ta.value; refresh(); };
      const chip = (txt, title) => h("button", { type: "button", class: "chip", title: title || "", onclick: () => insertAt(ta, "{" + txt + "}") }, txt);
      const vars = S.postsInfo.specs.variables.filter(v => v.group === "job" || (s.vars === "tool" && v.group === "tool"));
      const words = [...s.words, ...s.extra];
      const reset = h("button", { type: "button", class: "small", onclick: () => { ta.value = orig.blocks[nm] ?? ""; d.blocks[nm] = ta.value; refresh(); } }, "↺ " + t("post.reset"));
      watch(() => { reset.hidden = same(cur(), orig.blocks[nm] ?? ""); });
      return h("details", { class: "blk" },
        h("summary", {}, lab(s.label), " ", chg(() => !same(cur(), orig.blocks[nm] ?? "")), h("span", { class: "muted small" }, " · " + lab(s.when))),
        ta,
        words.length ? h("div", { class: "chips" }, h("span", { class: "muted small" }, t("post.words")), words.map(w => chip(w))) : null,
        h("details", { class: "vars" }, h("summary", { class: "muted small" }, t("post.vars")),
          h("div", { class: "chips" }, vars.map(v => chip(v.name, lab(v.text))))),
        h("div", { class: "row" }, reset));
    };
    const blkBox = h("div", {}, sp.block_groups.map(g => h("div", {},
      h("div", { class: "group-h" }, lab(g.label)), sp.blocks.filter(b => b.group === g.id).map(block))));

    let close;
    const save = () => guarded(async () => {
      if (!(d.name || "").trim()) return toast(t("post.noname"), "warn");
      const r = await api("post_save", { post: d });
      adopt(r);
      S.post = r.saved; store.set("iyicam.post", S.post); S.gcode = null;
      close(); toast(t("post.saved", d.name), "ok");
      for (const w of r.warnings || []) toast(w, "warn");
      render();
    });
    const del = () => guarded(async () => {
      if (!confirm(t("post.delask", base.name))) return;
      const r = await api("post_delete", { id: base.id });
      adopt(r);
      if (S.post === base.id) { S.post = (r.posts.find(p => p.id === "mach3") || r.posts[0] || {}).id || ""; store.set("iyicam.post", S.post); }
      S.gcode = null; close(); toast(t("post.deleted", base.name), "ok"); render();
    });
    const body = h("div", {}, head,
      h("div", { class: "group-h" }, t("post.options")), optBox,
      h("div", { class: "group-h" }, t("post.blocks")), h("div", { class: "muted small" }, t("post.blocksHint")), blkBox,
      h("div", { class: "stickybar" },
        h("button", { class: "primary grow", onclick: save }, t("post.save")),
        h("button", { onclick: () => preview(d) }, "👁 " + t("post.preview")),
        mode === "edit" ? h("button", { class: "danger", onclick: del }, t("post.delete")) : null,
        h("button", { onclick: () => close() }, t("post.cancel"))));
    close = openSheet(mode === "edit" ? t("post.titleEdit") : t("post.titleNew"), body);
  }

  /** the draft's G-code on the sample job, with every line to tap */
  async function preview(d) {
    const box = h("div", {}, h("div", { class: "muted small" }, t("post.previewNote")), h("div", { class: "muted", style: "margin:10px 0" }, "…"));
    const close = openSheet(t("post.previewTitle"), box);
    try {
      const r = await api("post_preview", { post: d });
      box.replaceChildren(h("div", { class: "muted small" }, t("post.previewNote")),
        (r.warnings || []).map(w => h("div", { class: "msg warn" }, w)),
        h("div", { class: "row", style: "margin:8px 0" }, h("button", { class: "small", onclick: () => glossary().catch(fail) }, "❓ " + t("gx.help")),
          h("button", { class: "small", onclick: () => close() }, "✕")),
        viewer(r.files.map(f => f.text).join("\n"), d.id || ""));
    } catch (e) {                        // (a mistake in the draft is told here, where it is being made)
      box.replaceChildren(h("div", { class: "msg bad" }, e && e.text ? e.text : String(e)),
        h("div", { class: "row" }, h("button", { onclick: () => close() }, t("post.cancel"))));
    }
  }

  // ------------------------------------------------------------------------------------------ in the output tab
  /** the buttons under the choice of the post: change it, copy it, learn what its words mean */
  function tools(postId) {
    const cur = S.schema.posts.find(p => p.id === postId);
    const open = (mode) => guarded(async () => {
      const info = await ensurePosts();
      const full = info.posts.find(p => p.id === postId);
      if (!full) return;
      if (info.errors && info.errors.length) toast(t("post.badfiles", info.errors.map(e => e.file).join(", ")), "warn");
      editor(full, mode);
    });
    return h("div", { class: "row", style: "margin-top:8px" },
      cur && cur.editable ? [h("button", { class: "small", onclick: () => open("edit") }, "✎ " + t("post.edit")),
                             h("button", { class: "small", onclick: () => open("copy") }, "⧉ " + t("post.copy"))]
                          : h("button", { class: "small", onclick: () => open("copy") }, "✎ " + t("post.make")),
      h("button", { class: "small", onclick: () => guarded(glossary) }, "❓ " + t("gx.help")));
  }

  B.post = { tools, viewer, glossary, editor, preview };
})();

/* iyiCAM page: the toolpaths looked at, their order seen and changed.
 *
 * A tab ("Önizleme" / "Preview", between the toolpaths and the output) with the sheet and, under it, what the desktop's Cut
 * Order panel does:
 *
 *  - **preview**: the calculated toolpaths coloured by depth, each one shown or hidden, the moves in the air, arrows along the
 *    cuts (the way the tool goes), the place of every cut in the order (numbers), the start points; and a **playback**: a
 *    slider (and play) that shows the cuts one after the other, the tool at the end of the last;
 *  - **order**: the cuts of a toolpath in the order they are made, as a list: tap a row (or the cut on the drawing) to light it up;
 *  - **editing**: move a cut up or down, to the first or the last place, turn the order round, **tap the cuts on the drawing in the
 *    order they should be made**, or let a strategy (and a cutting direction) order them; the change is **calculated and
 *    drawn in pink first**, with what it does to the way the tool travels (``2169 mm → 3681 mm``), and applied (or dropped) by a button.
 *
 * It sits on app.js (through ``window.IyiPage``), like edit.js; the page works without it.  What it asks of the engine is the
 * ``compute`` operation with the toolpath's cut order (``sequence``: ``auto_order``, ``order``, ``strategy``, ``direction``)
 * changed in a copy of the project, so it is the engine's own order, with its rules (everything inside a cut is cut first). */
(function () {
  "use strict";
  const B = window.IyiPage, G = window.IyiGeom;
  if (!B || !G) return;
  const { S, h, t, toast, fail, TXT } = B;

  Object.assign(TXT.tr, {
    "tab.prev": "Önizleme", "pv.need": "Önce Yollar sekmesinden bir takım yolu ekle.", "pv.calc": "Hesapla",
    "pv.notyet": "Takım yollarını görmek için önce hesapla.", "pv.paths": "Takım yolları", "pv.show": "Çizimde göster",
    "pv.rapids": "Boşta hareket", "pv.arrows": "Yön okları", "pv.numbers": "Sıra numaraları", "pv.starts": "Başlangıç noktaları",
    "pv.depth": "Derinlik: yüzey → {0} mm", "pv.play": "Oynat", "pv.step": "Hareket {0} / {1}", "pv.all": "Tamamı",
    "pv.order": "Kesim sırası", "pv.orderNone": "Bu takım yolunun kesim sırası yok (delme gibi sıra gerektirmeyen bir işlem).",
    "pv.pick": "Sırayı çizimde dokunarak ver", "pv.picking": "Kesilecek sırayla çizimdeki parçalara dokun. Seçilen: {0}",
    "pv.done": "Bitti", "pv.up": "Bir yukarı", "pv.down": "Bir aşağı", "pv.first": "En başa", "pv.last": "En sona", "pv.reverse": "Sırayı ters çevir",
    "pv.strategy": "Otomatik sıra (strateji)", "pv.reorder": "Bu stratejiyle sırala", "pv.direction": "Kesim yönü", "pv.startp": "Başlangıç noktaları ⚑",
    "pv.draft": "Önizleme (pembe): değişiklik henüz uygulanmadı", "pv.apply": "Uygula", "pv.drop": "Vazgeç", "pv.computing": "Hesaplanıyor…",
    "pv.compare": "Boşta hareket {0} → {1} mm ({2}) · süre {3} → {4}", "pv.moved": "{0} kesim başka sıraya geçti.",
    "pv.rule": "Güvenlik kuralları gereği sıra düzeltildi: içtekiler (delikler) önce kesilir.", "pv.applied": "Sıra uygulandı.",
    "pv.cutof": "{0} · {1}×{2} mm", "pv.same": "Aynı", "pv.choose": "Sırasını görmek için bir takım yolu seç.",
    "pv.stats": "süre {0} · kesim {1} m · boşta {2} m", "pv.total": "Toplam"
  });
  Object.assign(TXT.en, {
    "tab.prev": "Preview", "pv.need": "Add a toolpath on the Toolpaths tab first.", "pv.calc": "Calculate",
    "pv.notyet": "Calculate first to see the toolpaths.", "pv.paths": "Toolpaths", "pv.show": "Show on the drawing",
    "pv.rapids": "Moves in the air", "pv.arrows": "Direction arrows", "pv.numbers": "Order numbers", "pv.starts": "Start points",
    "pv.depth": "Depth: surface → {0} mm", "pv.play": "Play", "pv.step": "Move {0} / {1}", "pv.all": "All",
    "pv.order": "Cut order", "pv.orderNone": "This toolpath has no cut order (drilling and the like need none).",
    "pv.pick": "Give the order by tapping the drawing", "pv.picking": "Tap the parts on the drawing in the order they are to be cut. Picked: {0}",
    "pv.done": "Done", "pv.up": "One up", "pv.down": "One down", "pv.first": "To the first", "pv.last": "To the last", "pv.reverse": "Turn the order round",
    "pv.strategy": "Automatic order (strategy)", "pv.reorder": "Order with this strategy", "pv.direction": "Cutting direction", "pv.startp": "Start points ⚑",
    "pv.draft": "Preview (pink): the change is not applied yet", "pv.apply": "Apply", "pv.drop": "Cancel", "pv.computing": "Calculating…",
    "pv.compare": "Moves in the air {0} → {1} mm ({2}) · time {3} → {4}", "pv.moved": "{0} cut(s) come at another place of the order.",
    "pv.rule": "The order was corrected by the safety rules: what is inside a part (holes) is cut first.", "pv.applied": "The order was applied.",
    "pv.cutof": "{0} · {1}×{2} mm", "pv.same": "The same", "pv.choose": "Pick a toolpath to see its order.",
    "pv.stats": "time {0} · cutting {1} m · in the air {2} m", "pv.total": "Total"
  });

  // ------------------------------------------------------------------------------------------ state
  const PV = { tp: null, sel: -1, hl: null, step: null, playing: false, speed: 1, timer: 0, draft: null, pick: null, ref: null,
               previewTimer: 0, seq: 0 };
  const V = { stage: null, dock: null, sheet: null, redrawing: 0 };
  S.pv.rapids = true; S.pv.arrows = true; S.pv.numbers = true; S.pv.starts = false;

  const fmtT = s => { s = Math.round(s || 0); const m = Math.floor(s / 60); return `${m}:${String(s % 60).padStart(2, "0")}`; };
  const loc = () => (B.lang() === "tr" ? "tr-TR" : "en-US");
  const mm = v => Math.round(v || 0).toLocaleString(loc());
  const metres = v => ((v || 0) / 1000).toLocaleString(loc(), { maximumFractionDigits: 1 });
  const results = sheet => (S.results ? S.results.results.filter(r => r.sheet === sheet.id && r.enabled !== false) : []);
  const resultOf = (sheet, tp) => results(sheet).find(r => r.toolpath === tp);
  const tpOf = id => S.project.toolpaths.find(x => x.id === id);
  const thickness = sheet => Math.max(sheet.thickness != null ? sheet.thickness : S.project.material.thickness, 0.01);
  const redraw = () => { if (V.stage && V.stage.redraw) V.stage.redraw(); };
  const orderOf = sheet => {
    const r = PV.tp ? resultOf(sheet, PV.tp) : null;
    return (PV.draft && PV.draft.order) || (r && r.order) || [];
  };

  /** the cuts, in the order the machine makes them: for each toolpath that is shown (the one being changed as the preview has it) */
  function stepsOf(sheet) {
    const out = [];
    for (const tp of S.project.toolpaths) {
      if (tp.enabled === false || S.pv.hidden.has(tp.id)) continue;
      const r = PV.draft && PV.draft.result && tp.id === PV.tp ? PV.draft.result : resultOf(sheet, tp.id);
      if (!r || !r.preview) continue;
      for (const c of r.preview.cuts) out.push({ z: c.z, p: c.p, tp: tp.id });
      for (const d of r.preview.drills) out.push({ drill: d, tp: tp.id });
    }
    return out;
  }

  // ------------------------------------------------------------------------------------------ on the drawing
  const vectorMap = (() => { let rev = -1, sheetId = null, map = null; return sheet => { if (rev !== S.rev || sheetId !== sheet.id || !map) { map = new Map(sheet.vectors.map(v => [v.id, v])); rev = S.rev; sheetId = sheet.id; } return map; }; })();
  const keyPoint = (sheet, key) => { const [id, ci] = key.split(":"), v = vectorMap(sheet).get(id), c = v && v.contours[+ci]; return c ? { v, c } : null; };

  function strokeContour(ctx, c) {
    ctx.beginPath();
    G.flatten(c).forEach(([x, y], i) => (i ? ctx.lineTo(x, y) : ctx.moveTo(x, y)));
    if (c.closed) ctx.closePath();
    ctx.stroke();
  }
  function arrows(ctx, pts, px, every) {
    let acc = every / 2;
    for (let i = 0; i + 3 < pts.length; i += 2) {
      const x0 = pts[i], y0 = pts[i + 1], x1 = pts[i + 2], y1 = pts[i + 3], d = Math.hypot(x1 - x0, y1 - y0);
      if (d < 1e-9) continue;
      let at = acc;
      while (at < d) {
        const f = at / d, x = x0 + (x1 - x0) * f, y = y0 + (y1 - y0) * f, th = Math.atan2(y1 - y0, x1 - x0), a = 4.5 * px;
        ctx.moveTo(x + a * Math.cos(th), y + a * Math.sin(th));
        ctx.lineTo(x + a * Math.cos(th + 2.5), y + a * Math.sin(th + 2.5));
        ctx.lineTo(x + a * Math.cos(th - 2.5), y + a * Math.sin(th - 2.5));
        ctx.closePath();
        at += every;
      }
      acc = at - d;
    }
  }

  /** the preview tab's own drawing of the toolpaths (app.js leaves them to it): playback, arrows, the order, a change in pink */
  function scene(ctx, view, px, dark, sheet) {
    if (S.tab !== "prev" || !S.pv.custom || !S.results) return;
    const steps = stepsOf(sheet), n = steps.length, upto = PV.step == null ? n : Math.min(PV.step, n), th = thickness(sheet);
    const draft = PV.draft && PV.draft.result;
    ctx.lineJoin = "round"; ctx.lineCap = "round";
    // the moves in the air
    if (S.pv.rapids) {
      ctx.lineWidth = 1 * px; ctx.setLineDash([4 * px, 4 * px]); ctx.strokeStyle = dark ? "#7d8b83" : "#9aa7a0"; ctx.beginPath();
      for (const r of results(sheet)) {
        if (S.pv.hidden.has(r.toolpath) || !r.preview) continue;
        const src = draft && r.toolpath === PV.tp ? null : r.preview.rapids;     // (the one being changed: its new moves are the pink ones)
        if (src) for (let i = 0; i + 3 < src.length; i += 4) { ctx.moveTo(src[i], src[i + 1]); ctx.lineTo(src[i + 2], src[i + 3]); }
      }
      ctx.stroke(); ctx.setLineDash([]);
    }
    // the cuts that were made so far, by depth
    const buckets = Array.from({ length: 8 }, () => new Path2D()), drills = [];
    for (let i = 0; i < upto; i++) {
      const s = steps[i];
      if (s.drill) { drills.push(s.drill); continue; }
      const b = buckets[Math.max(0, Math.min(7, Math.floor(Math.max(0, Math.min(1, -s.z / th)) * 7.999)))];
      for (let k = 0; k + 1 < s.p.length; k += 2) (k ? b.lineTo(s.p[k], s.p[k + 1]) : b.moveTo(s.p[k], s.p[k + 1]));
    }
    ctx.lineWidth = 2 * px;
    buckets.forEach((path, i) => { ctx.strokeStyle = `hsl(${140 - 125 * i / 7} 80% ${dark ? 58 : 42}%)`; ctx.stroke(path); });
    ctx.fillStyle = dark ? "#fbbf24" : "#b45309";
    for (const [x, y] of drills) { ctx.beginPath(); ctx.arc(x, y, 3 * px, 0, 7); ctx.fill(); }
    if (S.pv.arrows) {                       // the way the tool goes along every cut
      ctx.fillStyle = dark ? "#e8efe9" : "#14201a"; ctx.globalAlpha = 0.75; ctx.beginPath();
      const every = 70 * px;
      for (let i = 0; i < upto; i++) if (!steps[i].drill) arrows(ctx, steps[i].p, px, every);
      ctx.fill(); ctx.globalAlpha = 1;
    }
    // a change that is only looked at: the new toolpath in pink, with its moves in the air
    if (draft && draft.preview) {
      ctx.strokeStyle = "#ec4899"; ctx.lineWidth = 2.4 * px; ctx.beginPath();
      for (const c of draft.preview.cuts) for (let k = 0; k + 1 < c.p.length; k += 2) (k ? ctx.lineTo(c.p[k], c.p[k + 1]) : ctx.moveTo(c.p[k], c.p[k + 1]));
      ctx.stroke();
      ctx.setLineDash([3 * px, 4 * px]); ctx.lineWidth = 1.2 * px; ctx.beginPath(); const rp = draft.preview.rapids;
      for (let i = 0; i + 3 < rp.length; i += 4) { ctx.moveTo(rp[i], rp[i + 1]); ctx.lineTo(rp[i + 2], rp[i + 3]); }
      ctx.stroke(); ctx.setLineDash([]);
    }
    // where the tool is: the end of the last cut that was made
    if (PV.step != null && upto > 0) {
      const s = steps[upto - 1], x = s.drill ? s.drill[0] : s.p[s.p.length - 2], y = s.drill ? s.drill[1] : s.p[s.p.length - 1];
      if (!s.drill) { ctx.strokeStyle = "#f97316"; ctx.lineWidth = 3.2 * px; ctx.beginPath(); for (let k = 0; k + 1 < s.p.length; k += 2) (k ? ctx.lineTo(s.p[k], s.p[k + 1]) : ctx.moveTo(s.p[k], s.p[k + 1])); ctx.stroke(); }
      ctx.fillStyle = "#f97316"; ctx.beginPath(); ctx.arc(x, y, 6 * px, 0, 7); ctx.fill();
    }
    // the cut that is picked in the list
    if (PV.hl) { const kp = keyPoint(sheet, PV.hl); if (kp) { ctx.strokeStyle = "#f97316"; ctx.lineWidth = 4 * px; ctx.globalAlpha = 0.55; strokeContour(ctx, kp.c); ctx.globalAlpha = 1; } }
    // the order: a number where each cut starts (the ones being picked, while picking)
    const order = orderOf(sheet), picks = PV.pick;
    if (PV.tp && order.length && (S.pv.numbers || S.pv.starts || picks)) {
      const dpr = window.devicePixelRatio || 1;
      ctx.save(); ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
      ctx.font = "bold 11px system-ui, sans-serif"; ctx.textAlign = "center"; ctx.textBaseline = "middle";
      const list = picks ? picks : order;
      list.forEach((key, i) => {
        const kp = keyPoint(sheet, key);
        if (!kp) return;
        const x = view.ox + view.s * kp.c.start[0], y = view.oy - view.s * kp.c.start[1];
        if (S.pv.numbers || picks) {
          ctx.fillStyle = picks ? "#ec4899" : `hsl(${240 - 240 * (order.length > 1 ? i / (order.length - 1) : 0)} 80% 45%)`;
          ctx.beginPath(); ctx.arc(x, y, key === PV.hl ? 11 : 9, 0, 7); ctx.fill();
          ctx.fillStyle = "#fff"; ctx.fillText(String(i + 1), x, y + 0.5);
        } else if (S.pv.starts) { ctx.fillStyle = "#16a34a"; ctx.beginPath(); ctx.arc(x, y, 4, 0, 7); ctx.fill(); }
      });
      ctx.restore();
    }
  }
  B.SCENE_HOOKS.push(scene);

  // ------------------------------------------------------------------------------------------ a tap on the drawing
  /** the key ("vector:contour") of the cut of this toolpath under a point, or null */
  function keyAt(sheet, x, y, view) {
    const keys = orderOf(sheet), byVec = new Map();
    for (const k of keys) { const [id, ci] = k.split(":"); if (!byVec.has(id)) byVec.set(id, []); byVec.get(id).push(+ci); }
    if (!byVec.size) return null;
    const id = G.hit(sheet.vectors, B.flatOf, x, y, 14 / view.s, v => byVec.has(v.id));
    if (!id) return null;
    const v = vectorMap(sheet).get(id);
    let best = null;
    for (const ci of byVec.get(id)) { const q = G.nearest(v.contours[ci], x, y); if (q && (!best || q.d < best.d)) best = { ci, d: q.d }; }
    return best ? `${id}:${best.ci}` : null;
  }
  function onTap(x, y, view) {
    const key = keyAt(V.sheet, x, y, view);
    if (PV.pick) {
      if (!key) return;
      const i = PV.pick.indexOf(key);
      if (i >= 0) PV.pick.splice(i, 1); else PV.pick.push(key);
      fill(); redraw();
      return;
    }
    if (!key) { PV.hl = null; PV.sel = -1; fill(); redraw(); return; }
    PV.hl = key; PV.sel = orderOf(V.sheet).indexOf(key);
    fill(); redraw();
    const row = V.dock && V.dock.querySelector(".orow.on");
    if (row) row.scrollIntoView({ block: "nearest" });
  }

  // ------------------------------------------------------------------------------------------ editing the order
  const strategies = () => (S.schema.sequence && S.schema.sequence.strategies) || [];
  const directions = () => (S.schema.sequence && S.schema.sequence.directions) || [];
  function startDraft(seq, order, requested) {
    const keep = PV.draft ? PV.draft.seq : {};
    PV.draft = { seq: { ...keep, ...seq }, order: order ? order.slice() : null, requested: requested ? requested.slice() : null, result: null, computing: false, corrected: false };
    if (seq.order) PV.draft.seq.order = seq.order.slice();
    stopPlay(); PV.step = null;
    fill(); redraw();
    clearTimeout(PV.previewTimer);
    PV.previewTimer = setTimeout(runPreview, 450);
  }
  const setOrder = order => startDraft({ auto_order: false, order }, order, order);
  function move(to) {
    const o = orderOf(V.sheet).slice(), i = PV.sel;
    if (i < 0 || i >= o.length) return;
    const j = Math.max(0, Math.min(o.length - 1, typeof to === "number" ? to : i));
    const [k] = o.splice(i, 1); o.splice(j, 0, k);
    PV.sel = j; PV.hl = k;
    setOrder(o);
  }

  /** the toolpath with the change, calculated by the engine on a copy of the project (nothing of the project is changed) */
  async function runPreview() {
    const d = PV.draft;
    if (!d || !PV.tp) return;
    const mine = ++PV.seq, sheet = V.sheet, tp = PV.tp;
    d.computing = true; fillDraftBar();
    const proj = { ...S.project, toolpaths: S.project.toolpaths.map(x => (x.id === tp ? { ...x, sequence: { ...(x.sequence || {}), ...d.seq } } : x)) };
    try {
      const r = await B.runJob("compute", { project: proj, preview: true, toolpaths: [tp], sheets: [sheet.id] });
      if (mine !== PV.seq || PV.draft !== d) return;
      const res = r.results.find(x => x.toolpath === tp && x.sheet === sheet.id);
      d.result = res || null;
      const eng = (r.patch.sequences && r.patch.sequences[tp] && r.patch.sequences[tp].order) || (res && res.order) || [];
      d.corrected = !!(d.requested && eng.length === d.requested.length && eng.some((k, i) => k !== d.requested[i]));
      d.order = eng;
      d.computing = false;
      fill(); redraw();
    } catch (e) { if (mine === PV.seq) { d.computing = false; fillDraftBar(); fail(e); } }
  }
  async function applyDraft() {
    const d = PV.draft, tp = tpOf(PV.tp);
    if (!d || !tp || d.computing) return;
    tp.sequence = { ...(tp.sequence || {}), ...d.seq, order: d.order || (d.seq.order || []) };
    PV.draft = null; PV.pick = null;
    B.touch();
    await B.guarded(async () => { await B.recompute(); toast(t("pv.applied"), "ok"); });
  }
  const dropDraft = () => { clearTimeout(PV.previewTimer); PV.seq++; PV.draft = null; PV.pick = null; fill(); redraw(); };

  // ------------------------------------------------------------------------------------------ playback
  function stopPlay() { PV.playing = false; clearInterval(PV.timer); }
  function play(n) {
    if (PV.playing) { stopPlay(); fillPlay(); return; }
    PV.playing = true;
    if (PV.step == null || PV.step >= n) PV.step = 0;
    PV.timer = setInterval(() => {
      if (S.tab !== "prev") { stopPlay(); return; }
      PV.step = Math.min(n, (PV.step || 0) + Math.max(1, Math.round(n / 220 * PV.speed)));
      if (PV.step >= n) stopPlay();
      fillPlay(); redraw();
    }, 60);
    fillPlay();
  }

  // ------------------------------------------------------------------------------------------ the panel
  let playBox = null;
  function fillPlay() {
    if (!playBox || !playBox.isConnected) return;
    const steps = stepsOf(V.sheet), n = steps.length, at = PV.step == null ? n : Math.min(PV.step, n);
    playBox.replaceChildren(...playControls(n, at));
  }
  function playControls(n, at) {
    const slider = h("input", { type: "range", min: "0", max: String(n), value: String(at), "aria-label": t("pv.play"), style: "flex:1 1 140px;min-height:36px" });
    slider.oninput = () => { stopPlay(); PV.step = +slider.value >= n ? null : +slider.value; label.textContent = lab(+slider.value); redraw(); };
    const lab = k => (PV.step == null && k >= n ? t("pv.all") : t("pv.step", k, n));
    const label = h("span", { class: "muted small", style: "flex:1 0 100%" }, lab(at));
    const b = (txt, title, fn) => h("button", { class: "small", title, "aria-label": title, onclick: fn }, txt);
    return [b("⏮", "0", () => { stopPlay(); PV.step = 0; fillPlay(); redraw(); }),
            b("◀", "−1", () => { stopPlay(); PV.step = Math.max(0, (PV.step == null ? n : PV.step) - 1); fillPlay(); redraw(); }),
            b(PV.playing ? "⏸" : "▶", t("pv.play"), () => play(n)),
            b("▶|", "+1", () => { stopPlay(); const k = (PV.step == null ? n : PV.step) + 1; PV.step = k >= n ? null : k; fillPlay(); redraw(); }),
            b("⏭", t("pv.all"), () => { stopPlay(); PV.step = null; fillPlay(); redraw(); }), slider, label];
  }

  function chip(label, key) {
    const on = !!S.pv[key];
    return h("button", { class: "small" + (on ? " primary" : ""), "aria-pressed": String(on), onclick: e => {
      S.pv[key] = !S.pv[key]; e.currentTarget.classList.toggle("primary", S.pv[key]); e.currentTarget.setAttribute("aria-pressed", String(S.pv[key])); redraw();
    } }, label);
  }

  function pathsSection(sheet) {
    const rs = results(sheet);
    let time = 0, cut = 0, rapid = 0;
    const rows = rs.map(r => {
      const tp = tpOf(r.toolpath);
      time += r.stats.time_s || 0; cut += r.stats.feed_length || 0; rapid += r.stats.rapid_length || 0;
      const cb = h("input", { type: "checkbox", checked: !S.pv.hidden.has(r.toolpath), "aria-label": r.name });
      cb.onchange = () => { if (cb.checked) S.pv.hidden.delete(r.toolpath); else S.pv.hidden.add(r.toolpath); fillPlay(); redraw(); };
      return h("div", { class: "item" }, cb,
        h("div", { class: "grow" }, h("b", {}, r.name), h("div", { class: "muted small" }, `${tp ? tp.tool.name : ""} · ` + t("pv.stats", fmtT(r.stats.time_s), metres(r.stats.feed_length), metres(r.stats.rapid_length))),
          r.error ? h("div", { class: "bad small" }, r.error) : null, (r.warnings || []).slice(0, 2).map(w => h("div", { class: "warn small" }, w))),
        h("button", { class: "small" + (PV.tp === r.toolpath ? " primary" : ""), onclick: () => { PV.tp = r.toolpath; PV.sel = -1; PV.hl = null; PV.draft = null; PV.pick = null; fill(); redraw(); } }, "① " + t("pv.order")));
    });
    return h("details", { class: "grp", open: true }, h("summary", {}, t("pv.paths")),
      h("div", { class: "list" }, rows), rs.length > 1 ? h("div", { class: "muted small" }, `${t("pv.total")}: ` + t("pv.stats", fmtT(time), metres(cut), metres(rapid))) : null);
  }

  function orderSection(sheet) {
    const res = PV.tp ? resultOf(sheet, PV.tp) : null;
    const order = orderOf(sheet), tp = PV.tp ? tpOf(PV.tp) : null;
    const box = h("details", { class: "grp", open: true }, h("summary", {}, `① ${t("pv.order")}` + (res ? ` · ${res.name}` : "")));
    if (!res) { box.append(h("div", { class: "muted small" }, t("pv.choose"))); return box; }
    if (!order.length) { box.append(h("div", { class: "muted small" }, t("pv.orderNone"))); return box; }
    const sel = PV.sel;
    const rows = (PV.pick || order).map((key, i) => {
      const kp = keyPoint(sheet, key);
      const bb = kp ? G.contourBBox(kp.c) : null;
      const name = kp && kp.v.name ? kp.v.name : (kp ? `${key.split(":")[0].slice(0, 4)}` : key);
      return h("div", { class: "orow" + (i === sel && !PV.pick ? " on" : ""), role: "button", tabindex: "0",
        onclick: () => { if (PV.pick) return; PV.sel = i; PV.hl = key; fill(); redraw(); } },
        h("span", { class: "onum" }, String(i + 1)),
        h("span", { class: "grow" }, bb ? t("pv.cutof", name, mm(bb.x1 - bb.x0), mm(bb.y1 - bb.y0)) : name));
    });
    box.append(h("div", { class: "pvlist" }, rows));
    const b = (txt, title, fn, dis) => h("button", { class: "small", title, "aria-label": title, disabled: !!dis, onclick: fn }, txt);
    if (PV.pick) {
      box.append(h("div", { class: "msg", style: "margin:6px 0" }, t("pv.picking", PV.pick.length)),
        h("div", { class: "row" }, h("button", { class: "small primary", onclick: () => {
          const picked = PV.pick, rest = order.filter(k => !picked.includes(k));
          PV.pick = null; if (picked.length) setOrder([...picked, ...rest]); else { fill(); redraw(); }
        } }, "✓ " + t("pv.done")), b("✕", t("pv.drop"), () => { PV.pick = null; fill(); redraw(); })));
      return box;
    }
    const one = sel >= 0;
    box.append(h("div", { class: "row", style: "margin-top:6px" },
      b("↑", t("pv.up"), () => move(sel - 1), !one || sel === 0), b("↓", t("pv.down"), () => move(sel + 1), !one || sel === order.length - 1),
      b("⤒", t("pv.first"), () => move(0), !one || sel === 0), b("⤓", t("pv.last"), () => move(order.length - 1), !one || sel === order.length - 1),
      b("⇅", t("pv.reverse"), () => { setOrder(orderOf(sheet).slice().reverse()); }),
      b("👆 " + t("pv.pick"), t("pv.pick"), () => { PV.pick = []; PV.hl = null; fill(); redraw(); })));
    // the engine's own ordering and the cutting direction
    const seq = tp ? (tp.sequence || {}) : {}, def = (S.schema.sequence && S.schema.sequence.defaults) || {};
    const stratSel = h("select", { "aria-label": t("pv.strategy") }, strategies().map(([v, l]) => h("option", { value: v }, l)));
    stratSel.value = (PV.draft && PV.draft.seq.strategy) || seq.strategy || def.strategy || "nearest";
    const dirSel = h("select", { "aria-label": t("pv.direction") }, directions().map(([v, l]) => h("option", { value: v }, l)));
    dirSel.value = (PV.draft && PV.draft.seq.direction) || seq.direction || def.direction || "toolpath";
    dirSel.onchange = () => startDraft({ direction: dirSel.value }, orderOf(sheet), null);
    box.append(h("label", { class: "f" }, h("span", {}, t("pv.strategy")), stratSel),
      h("div", { class: "row", style: "margin-top:6px" }, h("button", { class: "small", onclick: () => startDraft({ auto_order: true, strategy: stratSel.value, order: undefined }, null, null) }, "⚙ " + t("pv.reorder")),
        h("button", { class: "small", onclick: () => { if (B.editor) B.editor.state.tool = "start"; S.tab = "draw"; B.render(); } }, t("pv.startp"))),
      h("label", { class: "f" }, h("span", {}, t("pv.direction")), dirSel));
    return box;
  }

  function draftBar() {
    const d = PV.draft;
    const box = h("div", { class: "pvdraft", hidden: !d });
    if (!d) return box;
    const cur = V.sheet && PV.tp ? resultOf(V.sheet, PV.tp) : null, now = d.result;
    const lines = [h("b", {}, t("pv.draft"))];
    if (d.computing) lines.push(h("div", { class: "muted small" }, t("pv.computing")));
    else if (cur && now) {
      const a = cur.stats.rapid_length || 0, c = now.stats.rapid_length || 0, pct = a > 0 ? Math.round(100 * (c - a) / a) : 0;
      lines.push(h("div", { class: "small" }, t("pv.compare", mm(a), mm(c), (pct > 0 ? "+" : "") + pct + " %", fmtT(cur.stats.time_s), fmtT(now.stats.time_s))));
      const was = cur.order || [], moved = (d.order || []).filter((k, i) => was[i] !== k).length;
      lines.push(h("div", { class: "muted small" }, moved ? t("pv.moved", moved) : t("pv.same")));
      if (d.corrected) lines.push(h("div", { class: "warn small" }, t("pv.rule")));
      for (const w of (now.warnings || []).slice(0, 2)) lines.push(h("div", { class: "warn small" }, w));
    }
    box.append(...lines, h("div", { class: "row", style: "margin-top:6px" },
      h("button", { class: "small primary grow", disabled: d.computing || !d.result, onclick: applyDraft }, "✓ " + t("pv.apply")),
      h("button", { class: "small", onclick: dropDraft }, t("pv.drop"))));
    return box;
  }
  let draftHolder = null;
  function fillDraftBar() { if (draftHolder && draftHolder.isConnected) draftHolder.replaceChildren(draftBar()); }

  function fill() {
    if (!V.dock) return;
    const sheet = V.sheet, steps = stepsOf(sheet), n = steps.length, at = PV.step == null ? n : Math.min(PV.step, n);
    playBox = h("div", { class: "row pvplay" }, ...playControls(n, at));
    draftHolder = h("div");
    const th = thickness(sheet);
    V.dock.replaceChildren(
      draftHolder,
      pathsSection(sheet),
      h("details", { class: "grp" }, h("summary", {}, t("pv.show")),
        h("div", { class: "row", style: "padding-bottom:6px" }, chip(t("pv.rapids"), "rapids"), chip(t("pv.arrows"), "arrows"), chip(t("pv.numbers"), "numbers"), chip(t("pv.starts"), "starts")),
        h("div", { class: "pvlegend" }, h("i"), h("span", { class: "muted small" }, t("pv.depth", mm(th))))),
      h("details", { class: "grp", open: true }, h("summary", {}, "▶ " + t("pv.play")), playBox),
      orderSection(sheet));
    fillDraftBar();
  }

  // ------------------------------------------------------------------------------------------ the tab
  function empty(text, action) {
    return h("div", { class: "empty" }, h("div", { class: "big" }, "🛤"), h("div", {}, text),
      action ? h("div", { style: "margin-top:12px" }, action) : null);
  }
  function view() {
    const p = S.project;
    S.pv.custom = true;
    stopPlay();
    if (!p.toolpaths.length) return empty(t("pv.need"));
    if (!S.results) {
      return empty(t("pv.notyet"), h("button", { class: "primary", onclick: () => B.guarded(() => B.recompute()) }, "▶ " + t("pv.calc")));
    }
    if (PV.ref !== S.results) { PV.ref = S.results; PV.draft = null; PV.pick = null; PV.sel = -1; PV.hl = null; PV.step = null; }
    const sheet = B.activeSheet();
    V.sheet = sheet;
    const rs = results(sheet);
    if (!PV.tp || !rs.some(r => r.toolpath === PV.tp)) {
      const first = rs.find(r => (r.order || []).length > 1) || rs[0];
      PV.tp = first ? first.toolpath : null; PV.sel = -1; PV.hl = null;
    }
    const wrap = h("div", { class: "drawview" });
    if (p.sheets.length > 1) {
      const sel = h("select", { class: "sheetpick" }, p.sheets.map(x => h("option", { value: x.id }, x.name)));
      sel.value = sheet.id; sel.onchange = () => { S.sheetId = sel.value; PV.tp = null; PV.draft = null; B.render(); };
      wrap.append(sel);
    }
    V.stage = B.drawStage(sheet, { onTap });
    V.dock = h("div", { class: "eddock pvdock" });
    wrap.append(h("div", { class: "edstage" }, V.stage, V.dock));
    fill();
    return wrap;
  }

  B.EXTRA_TABS.push({ id: "prev", icon: "🛤", key: "tab.prev", view, wide: true });
  B.preview = { state: PV, view, stepsOf };
})();

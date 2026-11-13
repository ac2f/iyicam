/* iyiCAM page: jobs that outlive the page.
 *
 * A nesting (or any job started with ``keep``) runs at the engine, in its own thread, whatever happens to the phone: the
 * connection breaks, the browser is closed or goes to sleep.  The engine keeps the **last three** such jobs, with their
 * results (on disk too, so a restart of the engine does not lose a finished one), and knows which device started each and
 * which devices were given its result (the ``jobs`` operation).  This script is the page's half:
 *
 *  - when the page is opened again (or the network is back), it asks the engine about the jobs of this device, shows how far
 *    a running one is, and **asks before it takes the result of a finished one** ("Nesting finished. Send the result to this
 *    device?");
 *  - the header button opens the list of the last three jobs, newest first, from any device: get the result again, cancel a
 *    running one, delete one;
 *  - a result is applied by what the kind of job registered in ``IyiPage.jobApply`` (edit.js does it for nesting).
 *
 * It sits on app.js (through ``window.IyiPage``), like edit.js and post.js; the page works without it. */
(function () {
  "use strict";
  const B = window.IyiPage;
  if (!B) return;
  const { S, h, $, t, api, toast, fail, openSheet, store, TXT } = B;

  Object.assign(TXT.tr, {
    "job.title": "Son işler", "job.btn": "Son işler (en fazla 3)",
    "job.none": "Henüz iş yok. Bir yerleştirme (nesting) başlatınca burada görünür; bağlantı kopsa, tarayıcıyı kapatsan ya da telefon uyusa da sunucuda devam eder.",
    "job.hint": "Son 3 iş tutulur (yenileri eskileri siler). Biten bir işin sonucunu istediğin zaman yeniden isteyebilirsin.",
    "job.queued": "Sırada", "job.running": "Çalışıyor %{0}", "job.done": "Bitti", "job.error": "Hata", "job.cancelled": "İptal edildi",
    "job.mine": "bu cihaz", "job.get": "Sonucu getir", "job.cancel": "İptal et", "job.drop": "Sil",
    "job.finishedTitle": "İş bitti", "job.finished": "{0} bitti ({1}).", "job.askNote": "Sonucu bu cihaza göndereyim mi? Şu anki proje bu sonuçla değiştirilir; geri alınabilir.",
    "job.send": "Gönder", "job.later": "Şimdi değil", "job.failed": "{0}: {1}", "job.applied": "Sonuç uygulandı (geri alınabilir).",
    "job.interrupted": "Sunucu yeniden başlatıldığı için iş yarıda kaldı.", "job.noapply": "Bu iş türünün sonucu burada uygulanamıyor.",
    "e.job_not_found": "Bu işin sonucu artık yok (yenileri eskileri sildi ya da süresi doldu).",
    "e.interrupted": "Sunucu yeniden başlatıldığı için iş yarıda kaldı."
  });
  Object.assign(TXT.en, {
    "job.title": "Last jobs", "job.btn": "Last jobs (up to 3)",
    "job.none": "No jobs yet. A nesting you start shows up here; it goes on at the server when the connection breaks, the browser is closed or the phone sleeps.",
    "job.hint": "The last 3 jobs are kept (newer ones push out the older). The result of a finished job can be asked for again at any time.",
    "job.queued": "Waiting", "job.running": "Running {0}%", "job.done": "Done", "job.error": "Failed", "job.cancelled": "Cancelled",
    "job.mine": "this device", "job.get": "Get the result", "job.cancel": "Cancel", "job.drop": "Delete",
    "job.finishedTitle": "Job finished", "job.finished": "{0} finished ({1}).", "job.askNote": "Send the result to this device? The project that is open now is replaced by it; it can be undone.",
    "job.send": "Send", "job.later": "Not now", "job.failed": "{0}: {1}", "job.applied": "The result was applied (it can be undone).",
    "job.interrupted": "The job was cut short: the server was restarted.", "job.noapply": "The result of this kind of job cannot be applied here.",
    "e.job_not_found": "The result of this job is gone (newer jobs pushed it out, or its time was up).",
    "e.interrupted": "The job was cut short: the server was restarted."
  });

  const SEEN = "iyicam.jobs.seen";
  const J = { list: [], supported: true, started: false, timer: 0, asked: new Set(), here: new Set(), promptOpen: false, sheet: null };

  const seen = () => { try { return new Set(JSON.parse(store.get(SEEN) || "[]")); } catch { return new Set(); } };
  const markSeen = id => { const s = seen(); s.add(id); store.set(SEEN, JSON.stringify([...s].slice(-30))); };
  const open = j => j.state === "queued" || j.state === "running";
  const loc = () => (B.lang() === "tr" ? "tr-TR" : "en-GB");
  function when(ts) {
    if (!ts) return "";
    const d = new Date(ts * 1000), now = new Date();
    const time = d.toLocaleTimeString(loc(), { hour: "2-digit", minute: "2-digit" });
    return d.toDateString() === now.toDateString() ? time : `${d.toLocaleDateString(loc(), { day: "numeric", month: "short" })} ${time}`;
  }
  const kindName = j => (j.kind === "nest" ? t("nest.title") : j.kind);
  const nameOf = j => j.title || kindName(j);
  const pct = j => (j.n ? Math.round(100 * j.k / j.n) : 0);
  const statusText = j => (j.state === "running" ? t("job.running", pct(j)) : t("job." + j.state));
  const errText = j => (j.error && j.error.error === "interrupted" ? t("job.interrupted") : (j.error && j.error.message) || "");
  /** what needs the person: a job of this device that is not over, or that is over and was not given to it */
  const attention = () => J.list.filter(j => j.mine && (open(j) || (j.state === "done" && j.has_result && !j.delivered))).length;

  // ------------------------------------------------------------------------------------------ asking the engine
  function schedule(ms) {
    clearTimeout(J.timer);
    if (J.list.some(open) || ms) J.timer = setTimeout(refresh, ms || 2500);      // (only while a job is going: nothing is asked for nothing)
  }

  async function refresh() {
    if (!J.supported || $(".login")) return;
    let r;
    try { r = await api("jobs", { device: B.deviceInfo().id }); }
    catch (e) {
      if (e instanceof B.ApiError && e.code === "unknown_operation") J.supported = false;      // (an engine that does not know it)
      else if (B.lostConnection(e)) schedule(8000);                                             // (try again; the job is not lost)
      return;
    }
    J.list = r.jobs || [];
    badge();
    follow();
    schedule();
    if (J.sheet) J.sheet.render();
    promptNext();
  }

  /** a running job of this device that this page did not start (it was started before the page was closed): show how far it is */
  function follow() {
    const run = J.list.find(j => j.mine && open(j) && !J.here.has(j.id));
    if (S.busy && S.busy.follow && S.busy.stop) {                                                // (the Cancel of the bar)
      S.busy.stop = false;
      api("job.cancel", { id: S.busy.follow }).then(refresh, fail);
    }
    if (run && (!S.busy || S.busy.follow)) {
      B.setBusy({ id: run.id, follow: run.id, label: `${nameOf(run)} · ${statusText(run)}`, k: run.k, n: run.n, stop: S.busy ? S.busy.stop : false });
    } else if (!run && S.busy && S.busy.follow) B.setBusy(null);
  }

  /** a finished job of this device that it was not given: ask; a failed one: say so once */
  function promptNext() {
    if (J.promptOpen) return;
    const old = seen();
    for (const j of J.list) {
      if (!j.mine || J.here.has(j.id) || J.asked.has(j.id)) continue;
      if (j.state === "done" && j.has_result && !j.delivered) { J.asked.add(j.id); prompt(j); return; }
      if ((j.state === "error" || j.state === "cancelled") && !old.has(j.id)) {
        markSeen(j.id); J.asked.add(j.id);
        toast(j.state === "error" ? t("job.failed", nameOf(j), errText(j)) : `${nameOf(j)}: ${t("job.cancelled")}`, j.state === "error" ? "bad" : "warn");
      }
    }
  }

  function prompt(j) {
    J.promptOpen = true;
    const close = openSheet(t("job.finishedTitle"), h("div", {},
      h("p", {}, t("job.finished", nameOf(j), when(j.finished))), h("p", { class: "muted small" }, t("job.askNote")),
      h("div", { class: "row", style: "margin-top:12px" },
        h("button", { class: "primary grow", onclick: () => { close(); getResult(j); } }, t("job.send")),
        h("button", { onclick: () => close() }, t("job.later")),
        h("button", { class: "danger", onclick: () => { close(); dismiss(j); } }, t("job.drop")))),
      () => { J.promptOpen = false; promptNext(); });
  }

  // ------------------------------------------------------------------------------------------ what can be done with a job
  async function getResult(j) {
    const apply = B.jobApply[j.kind];
    if (!apply) return toast(t("job.noapply"), "warn");
    await B.guarded(async () => {
      const r = await api("job.result", { id: j.id, device: B.deviceInfo().id });     // (asked from the engine again: it may be a result of hours ago)
      apply(r);
      toast(t("job.applied"), "ok");
    });
    refresh();
  }
  async function cancel(j) { await B.guarded(() => api("job.cancel", { id: j.id })); refresh(); }
  async function dismiss(j) { await B.guarded(() => api("job.dismiss", { id: j.id })); refresh(); }

  // ------------------------------------------------------------------------------------------ the list of the last three
  function openList() {
    const box = h("div");
    const row = j => h("div", { class: "item", style: "flex-wrap:wrap;align-items:flex-start" },
      h("div", { class: "grow" }, h("b", {}, nameOf(j)),
        h("div", { class: "muted small" }, [statusText(j), when(j.created), j.mine ? t("job.mine") : j.device_label].filter(Boolean).join(" · ")),
        j.error ? h("div", { class: "bad small" }, errText(j)) : null,
        open(j) ? h("div", { class: "progress" }, h("i", { style: `width:${j.n ? pct(j) : 6}%` })) : null),
      h("div", { class: "row" },
        j.state === "done" && j.has_result ? h("button", { class: "small primary", onclick: () => getResult(j) }, t("job.get")) : null,
        open(j) ? h("button", { class: "small", onclick: () => cancel(j) }, t("job.cancel")) : null,
        h("button", { class: "small danger", onclick: () => dismiss(j) }, t("job.drop"))));
    const render = () => box.replaceChildren(J.list.length
      ? h("div", {}, h("div", { class: "muted small", style: "margin-bottom:6px" }, t("job.hint")), h("div", { class: "list" }, J.list.map(row)))
      : h("div", { class: "empty" }, t("job.none")));
    J.sheet = { render };
    render();
    const close = openSheet(t("job.title"), h("div", {}, box, h("div", { class: "row", style: "margin-top:12px" }, h("button", { class: "grow", onclick: () => close() }, t("ed.close")))), () => { J.sheet = null; });
    refresh();
  }

  function badge() {
    const n = attention();
    document.querySelectorAll("#jobsbtn .n").forEach(el => { el.textContent = n ? String(n) : ""; el.hidden = !n; });
  }

  /** the button of the header */
  function button() {
    const b = h("button", { id: "jobsbtn", class: "small", title: t("job.btn"), "aria-label": t("job.btn"), onclick: openList },     // (not guarded: the list is for looking at, also while a job runs)
      "🕘", h("span", { class: "n badge", hidden: true, style: "margin-left:4px" }));
    queueMicrotask(badge);
    return b;
  }

  // ------------------------------------------------------------------------------------------ the page's side
  /** the page was opened (or signed in): what went on while it was away is asked about */
  function start() {
    if (!J.started) {
      J.started = true;
      const wake = () => { if (!document.hidden) refresh(); };
      document.addEventListener("visibilitychange", wake);
      addEventListener("online", refresh);
    }
    refresh();
  }

  /** a job this page started itself (it takes its result itself, unless the connection is lost on the way) */
  function started(id) { J.here.add(id); setTimeout(refresh, 800); }

  /** the connection was lost while a job of this page was going and it came back finished: ask before taking the result */
  async function offer(id) {
    await refresh();
    const j = J.list.find(x => x.id === id);
    if (j && j.has_result && !J.promptOpen) { J.asked.add(id); prompt(j); }
    else if (j && !j.has_result) toast(`${nameOf(j)}: ${j.error ? errText(j) : t("job." + j.state)}`, "warn");
  }

  B.jobs = { start, started, offer, refresh, button, openList, state: J };
})();

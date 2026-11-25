/* Groups of sheets and layers made by dragging, and isolated groups on the web: a sheet dropped on a sheet makes a group,
 * an isolated group shows only its own layers and toolpaths (and the rest does not show its), a layer dropped on a layer
 * makes a group of layers.
 *   KEY=... NODE_PATH=$(npm root -g) node check_isolation.js */
const { open, loadFixture } = require("./lib");
const results = [];
const ok = (name, cond, extra = "") => { results.push(cond); console.log(cond ? "ok  " : "FAIL", name, extra); };

(async () => {
  const { browser, page, errors } = await open({ viewport: { width: 1500, height: 950 } });
  const ev = (fn, arg) => page.evaluate(fn, arg);
  await loadFixture(page);
  await page.waitForFunction(() => window.__iyicam.app.cur.results, null, { timeout: 120000 });

  // two more sheets; the Sheets tab of the Project dock
  await ev(() => {
    const { app } = window.__iyicam, d = app.cur;
    d.edit("test", p => {
      const s0 = p.sheets[0];
      for (const n of ["Two", "Three"]) p.sheets.push({ id: "s_" + n, name: n, width: s0.width, height: s0.height, thickness: 18, vectors: [], outline: null });
    });
  });
  await page.click(".panel.project .subtab:has-text('Sheets')");
  await page.waitForSelector(".panel.sheets .trow[data-id='s_Three']");

  // drag "Three" onto "Two": a group of the two
  await page.dragAndDrop(".panel.sheets .trow[data-id='s_Three']", ".panel.sheets .trow[data-id='s_Two']");
  await page.waitForFunction(() => window.__iyicam.app.cur.project.sheets.find(s => s.id === "s_Two").group, null, { timeout: 30000 }).catch(() => {});
  const g = await ev(() => { const p = window.__iyicam.app.cur.project; return [p.sheets.find(s => s.id === "s_Two").group, p.sheets.find(s => s.id === "s_Three").group, p.sheets[0].group || ""]; });
  ok("a sheet dropped on a sheet makes a group of the two", g[0] && g[0] === g[1] && !g[2], JSON.stringify(g));

  // isolate it from one of its sheets
  await ev(() => { const { app, A } = window.__iyicam; app.cur.sheetId = "s_Two"; A.setCur(app.cur); });
  await ev(() => window.__iyicam.A.sheetOps.isolate());
  await page.waitForFunction(() => window.__iyicam.app.cur.project.sheets.find(s => s.id === "s_Two").isolated, null, { timeout: 30000 }).catch(() => {});
  const iso = await ev(() => window.__iyicam.app.cur.project.sheets.filter(s => s.isolated).map(s => s.id).sort());
  ok("the whole group is isolated", JSON.stringify(iso) === JSON.stringify(["s_Three", "s_Two"]), JSON.stringify(iso));
  ok("the group is marked isolated in the panel", await ev(() => !!document.querySelector(".panel.sheets .tg-head.isolated")));

  // its layers: none of the rest's (the current layer is one of its own: made for it)
  await page.click(".panel.project .subtab:has-text('Layers')");
  const lay = await ev(() => { const d = window.__iyicam.app.cur; return { rows: [...document.querySelectorAll(".panel.layers tbody tr:not(.lgroup)")].length, rest: d.project.layers.filter(l => !l.sheet_group).length }; });
  ok("an isolated sheet shows none of the layers of the rest", lay.rows === 0 && lay.rest > 0, JSON.stringify(lay));
  await ev(() => window.__iyicam.A.layerOps.add());
  const own = await ev(() => { const d = window.__iyicam.app.cur; return d.project.layers.find(l => l.id === d.layerId).sheet_group; });
  ok("a layer added on it is the group's", own === g[0], own);

  // its toolpaths: none of the rest's; a new one is the group's and not listed on the other sheets
  const listedHere = await ev(() => [...document.querySelectorAll(".panel.toolpaths .trow")].length);
  ok("none of the toolpaths of the rest is listed on it", listedHere === 0, String(listedHere));
  await ev(() => { const d = window.__iyicam.app.cur; d.edit("tp", p => { const t = JSON.parse(JSON.stringify(p.toolpaths[0])); t.id = "own_tp"; t.name = "Own"; t.sheet_group = d.sheet.group; delete t.sheets; p.toolpaths.push(t); }); });
  await page.waitForFunction(() => [...document.querySelectorAll(".panel.toolpaths .trow .c1")].some(e => /Own/.test(e.textContent)), null, { timeout: 30000 }).catch(() => {});
  ok("its own toolpath is listed on it", await ev(() => [...document.querySelectorAll(".panel.toolpaths .trow .c1")].some(e => /Own/.test(e.textContent))));
  await ev(() => { const { app, A } = window.__iyicam; app.cur.sheetId = app.cur.project.sheets[0].id; A.setCur(app.cur); });
  await page.waitForTimeout(400);
  const there = await ev(() => [...document.querySelectorAll(".panel.toolpaths .trow .c1")].map(e => e.textContent));
  ok("the rest does not list the isolated group's toolpath", there.length && !there.some(x => /Own/.test(x)), JSON.stringify(there));

  // a layer dropped on a layer: a group of layers
  await ev(() => window.__iyicam.A.layerOps.add());
  await page.waitForTimeout(200);
  const ids = await ev(() => [...document.querySelectorAll(".panel.layers tbody tr[data-id]")].map(r => r.dataset.id));
  if (ids.length >= 2) await page.dragAndDrop(`.panel.layers tr[data-id='${ids[1]}']`, `.panel.layers tr[data-id='${ids[0]}']`);
  await page.waitForTimeout(300);
  const lg = await ev(ids => { const p = window.__iyicam.app.cur.project; return ids.slice(0, 2).map(i => p.layers.find(l => l.id === i).group || ""); }, ids);
  ok("a layer dropped on a layer makes a group of layers", ids.length >= 2 && lg[0] && lg[0] === lg[1], JSON.stringify(lg));
  ok("the group of layers is shown", await ev(() => !!document.querySelector(".panel.layers tr.lgroup")));

  console.log(errors.join("\n") || "no page errors");
  ok("no page errors", errors.length === 0);
  await browser.close();
  const bad = results.filter(x => !x).length;
  console.log(bad ? `${bad} failed` : `all ok (${results.length} checks)`);
  process.exit(bad ? 1 : 0);
})().catch(e => { console.error(e); process.exit(2); });

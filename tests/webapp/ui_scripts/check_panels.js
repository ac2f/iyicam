/* Panels, View toggles, docks, handles, drawing tools, snapping, units.   KEY=... NODE_PATH=$(npm root -g) node check_panels.js */
const { open, shot, loadFixture } = require("./lib");
const results = [];
const ok = (name, cond, extra = "") => { results.push(cond); console.log(cond ? "ok  " : "FAIL", name, extra); };
(async () => {
  const { browser, page, errors } = await open();
  await loadFixture(page);
  const ev = (fn, arg) => page.evaluate(fn, arg);
  const pt = (x, y) => ev(([x, y]) => { const c = window.__iyicam.app.ui.canvas, r = c.canvas.getBoundingClientRect(), [sx, sy] = c.w2s(x, y); return [r.left + sx, r.top + sy]; }, [x, y]);
  const bbox = () => ev(async () => { const { default: G } = await import("/app/ui/geom.js"); const b = G.bbox(window.__iyicam.app.cur.selected()); return { w: b.x1 - b.x0, h: b.y1 - b.y0, x0: b.x0, y0: b.y0 }; });
  // layers
  await ev(() => window.__iyicam.A.layerOps.add());
  await page.waitForSelector(".layers tbody tr >> nth=1");
  await page.dblclick(".layers tbody tr >> nth=1 >> .lname"); await page.fill(".layers input.inline", "Holes"); await page.keyboard.press("Enter");
  ok("a layer is renamed in the list", (await ev(() => window.__iyicam.app.cur.project.layers[1].name)) === "Holes");
  await page.click(".layers tbody tr >> nth=1 >> input[type=checkbox] >> nth=0");
  ok("Show unticked hides the layer", (await ev(() => window.__iyicam.app.cur.project.layers[1].visible)) === false);
  await page.keyboard.press("Control+z"); await page.keyboard.press("Control+z"); await page.keyboard.press("Control+z");
  // sheets panel
  await page.click(".subtab:has-text('Sheets')"); await ev(() => window.__iyicam.A.sheetOps.add()); await page.waitForSelector(".sheets .trow >> nth=1");
  await shot(page, "panel_sheets");
  ok("the Sheets list shows both sheets", (await page.locator(".sheets .trow").count()) === 2);
  await ev(() => { window.__iyicam.A.sheetOps.group(); }); await page.fill(".dlg input", "Plates"); await page.click(".dlg .btn.primary");
  await page.waitForSelector(".sheets .tgroup"); ok("a group of sheets stacks under a heading", (await page.locator(".sheets .tgroup").count()) === 1);
  await page.keyboard.press("Control+z"); await page.keyboard.press("Control+z"); await page.click(".subtab:has-text('Layers')");
  // selection panel: type a new X
  await ev(() => { const { app, A } = window.__iyicam; A.select([app.cur.sheet.vectors[0].id]); });
  await page.click(".dtab:has-text('Selection')");
  const before = await bbox();
  await page.fill(".selection input.num >> nth=0", "100"); await page.press(".selection input.num >> nth=0", "Enter");
  const after = await bbox();
  ok("X typed in the Selection panel moves the vector", Math.abs(after.x0 - 100) < 1e-6 && Math.abs(after.w - before.w) < 1e-6, `${before.x0} -> ${after.x0}`);
  await page.fill(".selection input.num >> nth=2", "280"); await page.press(".selection input.num >> nth=2", "Enter");
  const sized = await bbox();
  ok("Width typed in the Selection panel scales (proportions kept)", Math.abs(sized.w - 280) < 1e-6 && Math.abs(sized.h / sized.w - before.h / before.w) < 1e-6, `${sized.w} x ${sized.h}`);
  await page.keyboard.press("Control+z"); await page.keyboard.press("Control+z");
  // transform panel: relative move with 2 copies
  await page.click(".dtab:has-text('Transform')");
  const n0 = await ev(() => window.__iyicam.app.cur.sheet.vectors.length);
  await page.check(".transform .row.check:has-text('Relative') input"); await page.fill(".transform input.num >> nth=0", "300"); await page.fill(".transform input.num >> nth=1", "0");
  await page.check(".transform .row.check:has-text('copies') input"); await page.fill(".transform input[type=number]", "2");
  await page.click(".transform .btn:has-text('Apply')");
  ok("Transform: relative move with copies makes 2 copies", (await ev(() => window.__iyicam.app.cur.sheet.vectors.length)) === n0 + 2);
  await page.keyboard.press("Control+z");
  await page.uncheck(".transform .row.check:has-text('copies') input");
  // rotate 90 about the centre reference
  await page.selectOption(".transform select", "rotate");
  const bb0 = await bbox(); await page.fill(".transform input.num >> nth=0", "90"); await page.click(".transform .ref >> nth=4"); await page.click(".transform .btn:has-text('Apply')");
  const bb1 = await bbox(); ok("Transform: rotate 90 swaps width and height", Math.abs(bb1.w - bb0.h) < 1e-6 && Math.abs(bb1.h - bb0.w) < 1e-6, `${bb0.w}x${bb0.h} -> ${bb1.w}x${bb1.h}`);
  await page.keyboard.press("Control+z");
  // handles: drag the top-right corner
  const hb = await ev(() => { const c = window.__iyicam.app.ui.canvas, h = c.handles(); const r = c.canvas.getBoundingClientRect(); const [sx, sy] = c.w2s(h.pts[2][0], h.pts[2][1]); return [r.left + sx, r.top + sy]; });
  const w0 = (await bbox()).w;
  await page.mouse.move(hb[0], hb[1]); await page.mouse.down(); await page.mouse.move(hb[0] + 40, hb[1] - 40, { steps: 5 }); await page.mouse.up();
  const w1 = (await bbox()).w; ok("dragging a corner handle scales the selection", w1 > w0 * 1.1, `${w0} -> ${w1}`);
  await page.keyboard.press("Control+z");
  // View menu toggles and docks
  await ev(() => { const { A } = window.__iyicam; A.runItem("view.show_cut_order"); A.runItem("view.show_start_points"); A.runItem("view.show_directions"); });
  await page.waitForTimeout(200); await shot(page, "view_toggles");
  await ev(() => { const { A } = window.__iyicam; A.runItem("view.show_cut_order"); A.runItem("view.show_start_points"); A.runItem("view.show_directions"); });
  await page.click(".dock-title:has-text('Toolpaths') .dbtn");
  ok("a dock closes with its x (its tab goes)", (await page.locator(".dtab:has-text('Toolpaths')").count()) === 0);
  await page.click(".mtop[data-id=view]"); await page.click(".dropdown .mitem .mtext:text-is('Toolpaths')");
  ok("and shows again from the View menu", (await page.locator(".dtab:has-text('Toolpaths')").count()) === 1);
  await page.click(".dtab:has-text('Cut Order')"); await shot(page, "panel_cutorder");
  await page.click(".dtab:has-text('Toolpaths')");
  // drawing: polyline by clicks and Enter, with snap to grid
  await ev(() => { const { A } = window.__iyicam; A.runItem("view.snap_to_grid"); });
  await page.keyboard.press("p");
  const nPoly = await ev(() => window.__iyicam.app.cur.sheet.vectors.length);
  for (const [x, y] of [[300, 600], [400, 700], [500, 600]]) { const [sx, sy] = await pt(x + 1.3, y - 1.2); await page.mouse.click(sx, sy); }
  await page.keyboard.press("Enter");
  const poly = await ev(() => { const d = window.__iyicam.app.cur; const v = d.sheet.vectors[d.sheet.vectors.length - 1]; return { n: d.sheet.vectors.length, start: v.contours[0].start, segs: v.contours[0].segs }; });
  ok("the Polyline tool draws from clicks and snaps to the grid", poly.n === nPoly + 1 && poly.start[0] === 300 && poly.start[1] === 600 && poly.segs.length === 2, JSON.stringify(poly.start));
  await page.keyboard.press("Escape");
  // measure
  await page.keyboard.press("m"); const m0 = await pt(300, 300), m1 = await pt(400, 300);
  await page.mouse.move(m0[0], m0[1]); await page.mouse.down(); await page.mouse.move(m1[0], m1[1], { steps: 4 }); await page.mouse.up();
  ok("Measure says the length in the status bar", /Length 100/.test(await page.locator(".smsg").innerText()), await page.locator(".smsg").innerText());
  await page.keyboard.press("Escape"); await page.keyboard.press("Escape");
  // units
  await ev(() => window.__iyicam.A.runItem("edit.preferences")); await page.waitForSelector(".dlg");
  await page.selectOption(".dlg select >> nth=0", "inch"); await page.click(".dlg .btn.primary");
  await ev(() => { const { app, A } = window.__iyicam; A.select([app.cur.sheet.vectors[0].id]); });
  await page.click(".dtab:has-text('Selection')");
  const x = await page.inputValue(".selection input.num >> nth=0");
  ok("inches as the display unit", Math.abs(parseFloat(x) - 60 / 25.4) < 0.01, x);
  await shot(page, "units_inch");
  console.log(errors.join("\n") || "no page errors");
  ok("no page errors", errors.length === 0);
  await browser.close();
  process.exit(results.every(Boolean) ? 0 : 1);
})().catch(e => { console.error(e); process.exit(2); });

/* Toolpaths and sheets on the web: {sheet_group} in names, a toolpath kept to a group listed on its sheets only, the button
 * that selects the toolpaths of the sheet, every sheet's own thickness (Material Setup, a new profile's depth), the defaults
 * of new toolpaths (Preferences) and one G-code file per toolpath.
 *   KEY=... NODE_PATH=$(npm root -g) node check_sheets.js */
const { open, shot, loadFixture } = require("./lib");
const results = [];
const ok = (name, cond, extra = "") => { results.push(cond); console.log(cond ? "ok  " : "FAIL", name, extra); };

(async () => {
  const { browser, page, errors } = await open({ viewport: { width: 1500, height: 950 } });
  const ev = (fn, arg) => page.evaluate(fn, arg);
  const waitIdle = () => page.waitForFunction(() => !window.__iyicam.app.busy, null, { timeout: 120000 });
  await loadFixture(page);
  await page.waitForFunction(() => window.__iyicam.app.cur.results, null, { timeout: 120000 });
  await waitIdle();

  // a second sheet in the group "Pleksi", 6 mm thick; Profile 1 named with variables and kept to the group
  await ev(() => {
    const { app } = window.__iyicam, d = app.cur;
    d.edit("test", p => {
      const s0 = p.sheets[0];
      p.sheets.push({ id: "pleksi1", name: "Pleksi 1", width: s0.width, height: s0.height, thickness: 6, vectors: [], outline: null, group: "Pleksi" });
      const t = p.toolpaths[0]; t.name = "Kesim {sheet_group} S{sheet_number}"; t.sheet_group = "Pleksi";
    });
  });
  await page.waitForTimeout(300);
  const rows = () => ev(() => [...document.querySelectorAll(".panel.toolpaths .trow .c1")].map(e => e.textContent.trim()));
  await page.waitForFunction(() => document.querySelector(".panel.toolpaths .fb-empty") && /other sheets/.test(document.querySelector(".panel.toolpaths").textContent), null, { timeout: 30000 });
  let r = await rows();
  ok("a toolpath kept to a group is not listed on another sheet", r.length === 1 && /Pocket/.test(r[0]), JSON.stringify(r));
  await ev(() => { const { app } = window.__iyicam; app.cur.sheetId = "pleksi1"; });
  await ev(() => window.__iyicam.A.setCur(window.__iyicam.app.cur));
  await page.waitForFunction(() => [...document.querySelectorAll(".panel.toolpaths .trow .c1")].some(e => /Kesim Pleksi S2/.test(e.textContent)), null, { timeout: 30000 }).catch(() => {});
  r = await rows();
  ok("on its sheet it is listed with {sheet_group} and {sheet_number} put in", r.some(x => /Kesim Pleksi S2/.test(x)), JSON.stringify(r));
  await page.click(".panel.toolpaths button[title='Select the toolpaths of this sheet']");
  const sel = await ev(() => window.__iyicam.app.cur.toolpathSelection.length);
  ok("the button selects the toolpaths of this sheet", sel === r.length, `${sel} of ${r.length}`);
  await ev(() => window.__iyicam.A.runItem("view.toolpaths_of_this_sheet_only"));
  await page.waitForTimeout(300);
  ok("View ▸ Toolpaths of This Sheet Only off lists every toolpath", (await rows()).length === 2);
  await ev(() => window.__iyicam.A.runItem("view.toolpaths_of_this_sheet_only"));
  await shot(page, "sheets_toolpaths");

  // Material Setup edits this sheet's thickness; a new profile cuts as deep as this sheet
  await ev(() => { window.__iyicam.A.runItem("toolpaths.material_setup"); });
  await page.waitForSelector(".dlg");
  const lab = await page.textContent(".dlg");
  ok("Material Setup has the thickness of this sheet", /Thickness of this sheet/.test(lab) && !/Material thickness/.test(lab));
  await page.keyboard.press("Escape"); await page.waitForTimeout(200);
  await ev(() => { const { app } = window.__iyicam; app.cur.toolpathSelection = []; });
  await ev(async () => { const st = await import("./ui/state.js"); st.setSetting("askTool", false); });
  await ev(() => { window.__iyicam.A.runItem("toolpaths.new_toolpath.profile"); });
  await page.waitForSelector(".dlg");
  await page.waitForTimeout(300);
  const depth = await ev(() => { const i = [...document.querySelectorAll(".dlg .row")].find(r => /^Cut depth/.test(r.textContent.trim())); return i ? i.querySelector("input").value : null; });
  ok("a new profile on a 6 mm sheet is 6 mm deep", depth && Math.abs(parseFloat(depth) - 6) < 1e-6, String(depth));
  const overcutShown = await ev(() => [...document.querySelectorAll(".dlg .row")].some(r => /Cut below bottom/.test(r.textContent) && !r.hidden));
  ok("'Cut below bottom' is beside 'Cut through' (not behind More options)", overcutShown);
  await page.keyboard.press("Escape"); await page.waitForTimeout(200);

  // Preferences: the defaults of new toolpaths (0: stop at the bottom)
  await ev(() => { window.__iyicam.A.runItem("edit.preferences"); });
  await page.waitForSelector(".dlg");
  await page.waitForTimeout(300);
  const inp = page.locator(".dlg .row:has-text('Through cuts go below the bottom by') input");
  await inp.fill("0"); await inp.press("Tab");
  await page.click(".dlg button:has-text('OK')");
  await page.waitForFunction(() => { const o = window.__iyicam.app.schema.operations.find(x => x.id === "profile"); return o.params.find(p => p.name === "overcut").default === 0; }, null, { timeout: 30000 }).catch(() => {});
  const def = await ev(() => window.__iyicam.app.schema.operations.find(x => x.id === "profile").params.find(p => p.name === "overcut").default);
  ok("Preferences set a through cut to stop at the bottom for new toolpaths", def === 0, String(def));

  // one G-code file per toolpath
  const files = await ev(async () => {
    const { app } = window.__iyicam, d = app.cur;
    const proj = JSON.parse(JSON.stringify(d.project));
    proj.toolpaths[0].sheet_group = "";                        // (both on the first sheet, where the vectors are)
    const r = await app.engine.call("gcode", { project: proj, post: app.schema.posts[0].id, file_name: "is", per_toolpath: true, sheets: [proj.sheets[0].id] });
    return r.files.map(f => f.name);
  });
  ok("one G-code file per toolpath", files.length === 2 && new Set(files).size === 2, files.join(", "));

  // Save G-code: "the toolpaths of this sheet" writes those that belong on the sheet shown
  await ev(() => { const { app, A } = window.__iyicam; app.cur.sheetId = app.cur.project.sheets[0].id; A.setCur(app.cur); });
  await ev(() => { window.__iyicam.A.runItem("file.save_g_code"); });
  await page.waitForSelector(".dlg");
  const opts = await ev(() => [...document.querySelectorAll(".dlg select option")].map(o => o.value));
  ok("Save G-code offers the toolpaths of this sheet", opts.includes("sheet"), opts.join(","));
  await page.selectOption(".dlg .row:has-text('Toolpaths') select", "sheet");
  const dlp = page.waitForEvent("download", { timeout: 120000 }).catch(() => null);
  await page.click(".dlg .btn.primary");
  await page.waitForSelector(".dlg .choose", { timeout: 120000 });          // (where to: this device or the agent)
  await page.click(".dlg button:has-text('Download to this device')");
  const dl = await dlp;
  const text = dl ? require("fs").readFileSync(await dl.path(), "utf8") : "";
  ok("only the toolpaths of this sheet are written", /Pocket 1/.test(text) && !/Profile 1|Kesim/.test(text), dl ? dl.suggestedFilename() : "no file");
  await page.keyboard.press("Escape"); await page.waitForTimeout(200);

  // Model ▸ Rotate Model 90° Left: the sheet turns with everything on it
  const before = await ev(() => { const d = window.__iyicam.app.cur; return { w: d.sheet.width, h: d.sheet.height, id: d.sheet.id }; });
  await ev(() => window.__iyicam.A.runItem("model.rotate_model_90_left"));
  await page.waitForFunction(b => { const d = window.__iyicam.app.cur; return d.sheet.id === b.id && d.sheet.width === b.h; }, before, { timeout: 30000 }).catch(() => {});
  const after = await ev(() => { const d = window.__iyicam.app.cur; return { w: d.sheet.width, h: d.sheet.height }; });
  ok("Model ▸ Rotate 90° Left turns the sheet (width and height swap)", after.w === before.h && after.h === before.w, JSON.stringify([before, after]));
  await ev(() => window.__iyicam.A.runItem("model.rotate_model_90_right"));
  await page.waitForFunction(b => window.__iyicam.app.cur.sheet.width === b.w, before, { timeout: 30000 }).catch(() => {});

  // View ▸ Draw Toolpaths Solid: the cuts drawn as wide as the tool
  await ev(async () => { const { A } = window.__iyicam; await A.runItem("toolpaths.recalculate_all"); });
  await page.waitForFunction(() => { const d = window.__iyicam.app.cur; return d.results && !d.stale; }, null, { timeout: 120000 }).catch(() => {});
  const wide = async () => ev(() => {                       // (the widest line drawn, in world units, at the next redraw)
    const { app } = window.__iyicam, v = app.ui.canvas, ctx = v.canvas.getContext("2d"), orig = ctx.stroke;
    let most = 0, n = 0, world = 0;
    ctx.stroke = function (...a) { const s = Math.abs(ctx.getTransform().a) || 1; most = Math.max(most, ctx.lineWidth * s); world = Math.max(world, ctx.lineWidth); n++; return orig.apply(this, a); };
    try { v.draw(); } finally { ctx.stroke = orig; }
    const tools = app.cur.project.toolpaths.map(t => t.tool.diameter);
    return { most, n, world, tools, results: (app.cur.results && app.cur.results.results || []).filter(r => r.sheet === app.cur.sheet.id && r.preview).length };
  });
  const thin = await wide();
  await ev(() => window.__iyicam.A.runItem("view.draw_toolpaths_solid"));
  await page.waitForTimeout(300);
  const solidW = await wide();
  const solid = solidW.most, thinW = thin;
  ok("Draw Toolpaths Solid fills what the tool cuts", solidW.results > 0 && solidW.tools.includes(solidW.world) && thinW.world < Math.min(...thinW.tools), JSON.stringify([thinW, solidW]));
  await ev(() => window.__iyicam.A.runItem("view.draw_toolpaths_solid"));

  console.log(errors.join("\n") || "no page errors");
  ok("no page errors", errors.length === 0);
  await browser.close();
  const bad = results.filter(x => !x).length;
  console.log(bad ? `${bad} failed` : `all ok (${results.length} checks)`);
  process.exit(bad ? 1 : 0);
})().catch(e => { console.error(e); process.exit(2); });

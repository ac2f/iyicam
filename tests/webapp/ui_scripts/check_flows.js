/* The flows of the first round, end to end against the real engine: a toolpath from its window, G-code, simulation,
 * nesting, a command form, import, new project, layers and sheets, files of the agent.
 *   KEY=... NODE_PATH=$(npm root -g) node check_flows.js */
const { open, shot, loadFixture } = require("./lib");
const results = [];
const ok = (name, cond, extra = "") => { results.push(cond); console.log(cond ? "ok  " : "FAIL", name, extra); };
const SVG = '<svg xmlns="http://www.w3.org/2000/svg" width="100" height="50"><rect x="5" y="5" width="40" height="30" fill="none" stroke="black"/><circle cx="70" cy="25" r="15" fill="none" stroke="black"/></svg>';

(async () => {
  const { browser, page, errors } = await open();
  // (a call that opens a window must not be awaited: it resolves when the window closes)
  const A = (fn, ...a) => page.evaluate(([fn, a]) => { window.__iyicam.A[fn](...a); }, [fn, a]);
  const doc = () => page.evaluate(() => { const d = window.__iyicam.app.cur; return { name: d.name, n: d.sheet.vectors.length, tps: d.project.toolpaths.map(t => t.name), sheets: d.project.sheets.length, layers: d.project.layers.length,
    results: d.results ? d.results.results.length : 0, stale: d.stale, sel: d.selection.length, w: d.sheet.width, sim: !!d.sim, docs: window.__iyicam.app.docs.length, dirty: d.dirty }; });
  const waitIdle = () => page.waitForFunction(() => !window.__iyicam.app.busy, null, { timeout: 120000 });

  // -- import an SVG into the open project through the File menu
  await page.evaluate(() => window.__iyicam.A.runItem("file.import_vectors"));
  await page.waitForSelector(".dlg"); // (no agent chooser when the agent offers only this computer? it asks)
  const chooser = await page.locator(".dlg button:has-text('This device')").count();
  if (chooser) { const [fc] = await Promise.all([page.waitForEvent("filechooser"), page.click(".dlg button:has-text('This device')")]); await fc.setFiles({ name: "shapes.svg", mimeType: "image/svg+xml", buffer: Buffer.from(SVG) }); }
  await page.waitForFunction(() => window.__iyicam.app.cur.sheet.vectors.length > 0, null, { timeout: 60000 });
  let d = await doc();
  ok("Import Vectors reads an SVG", d.n >= 2 && d.sel >= 2, `${d.n} vectors, ${d.sel} selected`);
  // -- a command form: Offset
  await A("runItem", "vectors.edit.offset"); await page.waitForSelector(".dlg"); await shot(page, "flow_offset");
  await page.click(".dlg .btn.primary"); await waitIdle(); await page.waitForTimeout(300);
  d = await doc(); ok("a command runs from its form (Offset)", d.n >= 2);
  await A("undo");
  // -- a toolpath from its window
  await page.evaluate(() => { const { app, A } = window.__iyicam; A.select(app.cur.sheet.vectors.map(v => v.id)); });
  await A("newToolpath", "profile");
  await page.waitForSelector(".dlg"); await page.click(".dlg .btn.primary:has-text('Select')"); await page.waitForSelector(".tpwin");
  await shot(page, "flow_tp_window");
  await page.click(".tpwin .btn.primary"); await waitIdle(); await page.waitForTimeout(500);
  d = await doc(); ok("Calculate in the toolpath window adds a toolpath and its result", d.tps.length === 1 && d.results === 1 && !d.stale, JSON.stringify(d.tps));
  await shot(page, "flow_after_toolpath");
  // -- edit it, change the name, keep the original
  await page.evaluate(() => { const d = window.__iyicam.app.cur; window.__iyicam.A.editToolpath(d.project.toolpaths[0].id); });
  await page.waitForSelector(".tpwin"); await page.fill(".tpwin .tp-top input[type=text]", "Outline"); await page.click(".tpwin .btn.primary"); await waitIdle(); await page.waitForTimeout(400);
  d = await doc(); ok("editing a toolpath renames it", d.tps[0] === "Outline", JSON.stringify(d.tps));
  // -- simulate
  await A("simulate"); await waitIdle(); await page.waitForTimeout(500);
  d = await doc(); ok("Simulate gives a picture of the cut", d.sim); await shot(page, "flow_sim");
  // -- G-code downloaded
  await page.evaluate(() => window.__iyicam.A.runItem("file.save_g_code")); await page.waitForSelector(".dlg"); await shot(page, "flow_gcode");
  const dl = page.waitForEvent("download", { timeout: 60000 });
  await page.click(".dlg .btn.primary");
  await page.waitForSelector(".dlg .choose", { timeout: 60000 });          // (where to: this device or the agent)
  await page.click(".dlg button:has-text('Download to this device')");
  const download = await dl; const path = await download.path();
  const text = require("fs").readFileSync(path, "utf8");
  ok("Save G-code downloads a file with G-code", /G0|G1|M3/.test(text), download.suggestedFilename());
  // -- nesting
  await page.evaluate(() => { const { app, A } = window.__iyicam; A.select([]); });
  await A("nest"); await page.waitForSelector(".nestdlg"); await page.click(".nestdlg .btn.primary:has-text('Nest')");
  await page.waitForSelector(".dlg .report", { timeout: 120000 }); await shot(page, "flow_nest_report"); await page.click(".dlg .btn.primary"); await waitIdle();
  d = await doc(); ok("Nest Parts nests", d.n >= 2);
  // -- layers and sheets
  await page.evaluate(() => window.__iyicam.A.layerOps.add()); d = await doc(); ok("a layer is added", d.layers === 2);
  await page.evaluate(() => window.__iyicam.A.sheetOps.add()); d = await doc(); ok("a sheet is added and opened", d.sheets === 2 && d.n === 0);
  await page.keyboard.press("Control+z"); await page.keyboard.press("Control+z"); d = await doc(); ok("undo takes them back", d.sheets === 1 && d.layers === 1);
  // -- new project
  await A("runItem", "file.new"); await page.waitForSelector(".dlg");
  await page.click(".dlg .btn.primary"); await waitIdle(); await page.waitForTimeout(300);
  d = await doc(); ok("New makes a new tab", d.docs === 2, d.name);
  // -- files of the agent: open the demo project through the file browser
  await page.evaluate(() => window.__iyicam.A.runItem("file.open")); await page.waitForSelector(".dlg");
  const hasAgent = await page.locator(".dlg button:has-text('this computer'), .dlg .choose .btn.big").count();
  ok("Open offers the device and the workshop PC", hasAgent >= 2, String(hasAgent));
  await page.click(".dlg .choose .btn.big >> nth=1"); await page.waitForSelector(".filebrowser .fb-row"); await shot(page, "flow_browser");
  await page.click(".fb-row:has-text('jobs')"); await page.waitForSelector(".fb-row:has-text('demo.icam')");
  await page.dblclick(".fb-row:has-text('demo.icam')"); await waitIdle();
  await page.waitForFunction(() => window.__iyicam.app.docs.length === 3, null, { timeout: 60000 });
  d = await doc(); ok("a project of the agent opens", d.n === 19 && d.name === "demo", `${d.name} ${d.n}`);
  // -- save to the agent
  await page.evaluate(() => window.__iyicam.A.runItem("file.save_as")); await page.waitForSelector(".dlg");
  await page.click(".dlg .choose .btn.big >> nth=1"); await page.waitForSelector(".filebrowser .fb-row"); await page.click(".fb-row:has-text('jobs')"); await page.waitForSelector(".fb-row:has-text('demo.icam')");
  await page.fill(".filebrowser input[type=text]", "copy.icam"); await page.click(".dlg .btn.primary"); await waitIdle(); await page.waitForTimeout(500);
  d = await doc(); ok("Save As writes to the agent and clears the dirty mark", !d.dirty);
  console.log(errors.join("\n") || "no page errors");
  ok("no page errors", errors.length === 0);
  await browser.close();
  process.exit(results.every(Boolean) ? 0 : 1);
})().catch(e => { console.error(e); process.exit(2); });

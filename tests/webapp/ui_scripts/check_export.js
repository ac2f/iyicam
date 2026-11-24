/* Export Vectors (DXF, SVG) as downloads, and the DXF read back by Import Vectors.   KEY=... NODE_PATH=$(npm root -g) node check_export.js */
const { open, loadFixture } = require("./lib");
const fs = require("fs");
const results = [];
const ok = (name, cond, extra = "") => { results.push(cond); console.log(cond ? "ok  " : "FAIL", name, extra); };
const step = t => console.log("..", t);
(async () => {
  const { browser, page, errors } = await open();
  await loadFixture(page);
  const exportAs = async ext => {
    step("export " + ext);
    await page.evaluate(() => { window.__iyicam.A.runItem("file.export_vectors"); });
    await page.waitForSelector(".dlg select"); await page.selectOption(".dlg select >> nth=0", ext);
    const dl = page.waitForEvent("download", { timeout: 60000 });
    step("click Export");
    await page.click(".dlg .btn.primary:has-text('Export')");
    await page.waitForSelector(".dlg .choose", { timeout: 60000 }); await page.click(".dlg button:has-text('Download to this device')");
    const d = await dl; return { name: d.suggestedFilename(), text: fs.readFileSync(await d.path(), "utf8") };
  };
  const dxf = await exportAs("dxf");
  ok("Export Vectors writes a DXF", /SECTION/.test(dxf.text) && dxf.name.endsWith(".dxf"), dxf.name);
  const svg = await exportAs("svg");
  ok("Export Vectors writes an SVG", /<svg/.test(svg.text) && svg.name.endsWith(".svg"), svg.name);
  // read the DXF back into a new project
  await page.evaluate(() => { window.__iyicam.A.runItem("file.new"); });
  await page.waitForSelector(".dlg"); await page.click(".dlg .btn.primary"); await page.waitForFunction(() => window.__iyicam.app.docs.length === 3 && !window.__iyicam.app.busy);
  await page.evaluate(() => { window.__iyicam.A.runItem("file.import_vectors"); });
  await page.waitForSelector(".dlg .choose"); const [fc] = await Promise.all([page.waitForEvent("filechooser"), page.click(".dlg button:has-text('This device')")]);
  await fc.setFiles({ name: dxf.name, mimeType: "application/dxf", buffer: Buffer.from(dxf.text) });
  await page.waitForFunction(() => window.__iyicam.app.cur.sheet.vectors.length > 0, null, { timeout: 60000 });
  ok("the DXF reads back", (await page.evaluate(() => window.__iyicam.app.cur.sheet.vectors.length)) >= 19);
  console.log(errors.join("\n") || "no page errors"); ok("no page errors", errors.length === 0);
  await browser.close(); process.exit(results.every(Boolean) ? 0 : 1);
})().catch(e => { console.error(e); process.exit(2); });

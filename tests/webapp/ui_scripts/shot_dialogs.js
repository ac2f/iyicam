/* Screenshots of the dialogs next to the desktop's (docs/webapp/desktop_reference/dialog_*.png). */
const { open, shot, loadFixture } = require("./lib");
(async () => {
  const { browser, page, errors } = await open({ viewport: { width: 1440, height: 900 } });
  await loadFixture(page);
  const run = id => page.evaluate(id => { window.__iyicam.A.runItem(id); }, id);
  const close = async () => { await page.keyboard.press("Escape"); await page.waitForTimeout(150); };
  // select a vector first so the toolpath window has one
  await page.evaluate(() => { const { app, A } = window.__iyicam; A.select([app.cur.sheet.vectors[0].id]); });
  await run("toolpaths.new_toolpath.profile"); await page.waitForTimeout(400);
  // asks for the tool from the database first
  await shot(page, "dlg_pick_tool");
  await page.click(".dlg button:has-text('Select')"); await page.waitForTimeout(500);
  await shot(page, "dlg_toolpath");
  await page.click(".dlg .tab:has-text('Tool')"); await shot(page, "dlg_toolpath_tool");
  await page.click(".dlg .tab:has-text('Vectors')"); await shot(page, "dlg_toolpath_vectors");
  await close();
  await run("toolpaths.material_setup"); await page.waitForTimeout(300); await shot(page, "dlg_material"); await close();
  await run("toolpaths.tool_database"); await page.waitForTimeout(300); await shot(page, "dlg_tooldb"); await close();
  await run("nesting.nest_parts"); await page.waitForTimeout(300); await shot(page, "dlg_nesting"); await close();
  await run("edit.preferences"); await page.waitForTimeout(300); await shot(page, "dlg_prefs"); await close();
  await run("file.new"); await page.waitForTimeout(300); await shot(page, "dlg_new"); await close();
  await run("vectors.edit.offset"); await page.waitForTimeout(300); await shot(page, "dlg_offset"); await close();
  await run("file.save_g_code"); await page.waitForTimeout(300); await shot(page, "dlg_gcode"); await close();
  await run("help.keyboard_shortcuts"); await page.waitForTimeout(300); await shot(page, "dlg_shortcuts"); await close();
  console.log(errors.join("\n") || "no page errors");
  await browser.close();
})().catch(e => { console.error(e); process.exit(2); });

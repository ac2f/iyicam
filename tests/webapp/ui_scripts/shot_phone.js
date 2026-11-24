/* The same window on a phone (390 x 844, touch) and in the dark theme at desktop size. */
const { open, shot, loadFixture } = require("./lib");
(async () => {
  let { browser, page, errors } = await open({ viewport: { width: 390, height: 844 }, touch: true, dpr: 2 });
  await loadFixture(page);
  await shot(page, "phone_main");
  await page.tap("#quickbar .narrow-only >> nth=0"); await page.waitForTimeout(400); await shot(page, "phone_left");
  await page.tap(".scrim"); await page.waitForTimeout(300);
  await page.tap("#quickbar .narrow-only >> nth=1"); await page.waitForTimeout(400); await shot(page, "phone_right");
  await page.tap(".scrim"); await page.waitForTimeout(300);
  await page.tap(".mtop[data-id=toolpaths]"); await page.waitForTimeout(300); await shot(page, "phone_menu");
  await page.evaluate(() => document.body.click());
  await page.keyboard.press("Escape");
  await page.evaluate(() => { const { app, A } = window.__iyicam; A.select([app.cur.sheet.vectors[0].id]); A.runItem("toolpaths.new_toolpath.profile"); });
  await page.waitForTimeout(500); await page.tap(".dlg button:has-text('Select')"); await page.waitForTimeout(500); await shot(page, "phone_dialog");
  console.log(errors.join("\n") || "no page errors");
  await browser.close();
  ({ browser, page, errors } = await open({ dark: true }));
  await loadFixture(page);
  await page.evaluate(() => { const { app, A } = window.__iyicam; A.select(app.cur.sheet.vectors.slice(0, 2).map(v => v.id)); });
  await page.waitForTimeout(300);
  await shot(page, "dark_main");
  console.log(errors.join("\n") || "no page errors");
  await browser.close();
})().catch(e => { console.error(e); process.exit(2); });

/* End to end with a real agent (started by test_e2e_agent.py): sign in, open a .icam of the agent's folder, recalculate (F5),
 * save the G-code to the agent's folder.  Phase "work" does that; phase "gone" runs after the agent was stopped.
 *   URL=... KEY=... PHASE=work|gone POST=<post id> NODE_PATH=$(npm root -g) node e2e_agent.js          (prints one JSON line "RESULT {...}") */
const { open } = require("./ui_scripts/lib");
const out = {};
let page;
const waitIdle = () => page.waitForFunction(() => !window.__iyicam.app.busy, null, { timeout: 120000 });

(async () => {
  const opened = await open(); page = opened.page; const { browser, errors } = opened;
  const doc = () => page.evaluate(() => { const d = window.__iyicam.app.cur; return { name: d.name, n: d.sheet.vectors.length, tps: d.project.toolpaths.length, results: d.results ? d.results.results.length : 0, stale: d.stale, dirty: d.dirty }; });
  out.status = await page.evaluate(() => fetch("/api/files/status").then(r => r.json()));
  if (process.env.PHASE === "work") {
    await page.evaluate(() => window.__iyicam.A.runItem("file.open")); await page.waitForSelector(".dlg");
    await page.click(".dlg .choose .btn.big >> nth=1");                                       // the agent
    await page.waitForSelector(".filebrowser .fb-row");
    out.roots = await page.locator(".fb-row .n").allInnerTexts();
    await page.click(".fb-row >> nth=0");                                                      // the root
    await page.waitForSelector(".fb-row:has-text('project.icam')");
    out.listing = await page.locator(".fb-row .n").allInnerTexts();
    await page.dblclick(".fb-row:has-text('project.icam')"); await waitIdle();
    await page.waitForFunction(() => window.__iyicam.app.cur.name === "project", null, { timeout: 60000 });
    out.opened = await doc();
    await page.keyboard.press("F5"); await waitIdle(); await page.waitForTimeout(300);
    out.recalculated = await doc();
    // Save G-code, to the agent
    await page.evaluate(() => { window.__iyicam.A.runItem("file.save_g_code"); }); await page.waitForSelector(".dlg");
    out.post = await page.evaluate(() => { const s = document.querySelector(".dlg select"); return s && s.value; });
    await page.click(".dlg .btn.primary");
    await page.waitForSelector(".dlg .choose", { timeout: 60000 });
    await page.click(".dlg .choose .btn.big >> nth=1");
    await page.waitForSelector(".filebrowser .fb-row");
    await page.click(".fb-row >> nth=0");
    await page.waitForSelector(".filebrowser input[type=text]");
    out.name = await page.inputValue(".filebrowser input[type=text]");
    await page.click(".dlg .btn.primary:has-text('Save here')"); await waitIdle(); await page.waitForTimeout(800);
  } else {
    // the page knows the agent (Open once, cancel), then the agent is stopped from outside
    await page.evaluate(() => { window.__iyicam.A.runItem("file.open"); }); await page.waitForSelector(".dlg .choose");
    out.beforeChooser = await page.locator(".dlg .choose .btn").count();
    await page.keyboard.press("Escape"); await page.waitForTimeout(300);
    if (await page.locator(".dlg").count()) await page.click(".dlg .btn:has-text('Cancel')");
    console.log("READY");
    await new Promise(r => process.stdin.once("data", r));
    for (let i = 0; i < 80; i++) {
      const st = await page.evaluate(() => fetch("/api/files/status").then(r => r.json()));
      if (!st.agent || st.agent.connected === false) { out.statusGone = st; break; }
      await page.waitForTimeout(1000);
    }
    // (1) the page that was open: what does Open say now?
    await page.evaluate(() => { window.__iyicam.A.runItem("file.open"); });
    await page.waitForTimeout(1500);
    out.staleChooser = await page.locator(".dlg .choose .btn").count();
    if (out.staleChooser > 1) {
      await page.click(".dlg .choose .btn.big >> nth=1"); await page.waitForTimeout(1500);
      out.staleBrowserText = (await page.locator(".dlg").allInnerTexts()).join(" | ");
    }
    await page.keyboard.press("Escape"); await page.waitForTimeout(300);
    // (2) a page loaded now: Open goes straight to the device's file picker, which opens a file
    await page.reload(); await page.waitForSelector("body[data-ready='1']", { timeout: 120000 });
    const fcp = page.waitForEvent("filechooser", { timeout: 8000 }).catch(() => null);
    await page.evaluate(() => { window.__iyicam.A.runItem("file.open"); });
    out.freshChooser = await page.locator(".dlg .choose").count();
    const fc = await fcp;
    out.freshFileChooser = !!fc;
    if (fc) {
      await fc.setFiles({ name: "local.icam", mimeType: "application/json", buffer: require("fs").readFileSync(process.env.FIXTURE) });
      await page.waitForFunction(() => window.__iyicam.app.cur.sheet.vectors.length > 0, null, { timeout: 60000 }).catch(() => {});
      out.deviceOpened = await doc();
    }
  }
  out.errors = errors;
  console.log("RESULT " + JSON.stringify(out));
  await browser.close();
})().catch(e => { console.error(e); process.exit(2); });

/* Helpers of the UI scripts (Playwright): start the page of a running `iyicam serve`, sign in, wait for the window.
 *   iyicam serve --local --root DIR --port 8300      (the key: iyicam serve --show-key)
 *   URL=http://localhost:8300/app/ KEY=... NODE_PATH=$(npm root -g) node shot_main.js
 */
const { chromium } = require("playwright");
const fs = require("fs");
const URL_ = process.env.URL || "http://localhost:8300/app/";
const OUT = process.env.OUT || "/tmp/iyicam-ui-shots";
fs.mkdirSync(OUT, { recursive: true });

async function open(opts = {}) {
  // behind a proxy the page's CDN and PyPI requests need it; the app itself is on localhost
  const proxy = process.env.HTTPS_PROXY ? { server: process.env.HTTPS_PROXY, bypass: "localhost,127.0.0.1" } : undefined;
  const browser = await chromium.launch({ proxy });
  const ctx = await browser.newContext({ ignoreHTTPSErrors: !!proxy, viewport: opts.viewport || { width: 1440, height: 900 }, hasTouch: !!opts.touch, isMobile: !!opts.touch,
    colorScheme: opts.dark ? "dark" : "light", deviceScaleFactor: opts.dpr || 1, acceptDownloads: true });
  // (the File System Access picker cannot be driven: Save falls back to a download, which can)
  await ctx.addInitScript(() => { try { delete window.showSaveFilePicker; } catch { /* kept */ } });
  const page = await ctx.newPage();
  const errors = [];
  page.on("pageerror", e => errors.push("pageerror: " + e.message));
  page.on("console", m => { if (m.type() === "error") errors.push("console: " + m.text()); });
  await page.goto(URL_);
  if (process.env.KEY) {
    try { await page.waitForSelector(".login input", { timeout: 5000 }); await page.fill(".login input", process.env.KEY); await page.press(".login input", "Enter"); } catch { /* signed in already */ }
  }
  await page.waitForSelector("body[data-ready='1']", { timeout: 120000 });
  return { browser, ctx, page, errors };
}
const shot = (page, name) => page.screenshot({ path: `${OUT}/${name}.png` });
/** open a project of the demo fixture (the .icam of docs/webapp/fixtures) through the app's own import */
async function loadFixture(page, file = "project.json") {
  const p = require("path").resolve(__dirname, "../../../docs/webapp/fixtures/" + file);
  const json = fs.readFileSync(p, "utf8");
  await page.evaluate(async json => {
    const { app, A } = window.__iyicam;
    const bytes = new TextEncoder().encode(json);
    await A.openDocFromBytes({ name: "demo.icam", bytes });
  }, json);
  await page.waitForTimeout(500);
}
module.exports = { open, shot, loadFixture, OUT };

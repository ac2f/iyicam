/* What a CNC user does, as a script (run by test_explore.py): MODE=desktop draws, imports an SVG and a DXF, makes a profile and a
 * pocket, changes the tool in the Tool Database (and reloads), undoes / redoes, nests and simulates; MODE=phone looks at a phone
 * (Playwright iPhone devices): touch drawing, dialogs inside the screen, the layer buttons of the drawer.
 *   URL=... KEY=... FILES=<folder with t.dxf> MODE=desktop|phone NODE_PATH=$(npm root -g) node explore.js     (prints "RESULT {...}") */
const { chromium, devices } = require("playwright");
const fs = require("fs");
const out = {};
const step = t => console.error(".. " + t);
const SVG = '<svg xmlns="http://www.w3.org/2000/svg" width="100mm" height="50mm" viewBox="0 0 100 50"><rect x="5" y="5" width="40" height="30" fill="none" stroke="black"/><circle cx="70" cy="25" r="15" fill="none" stroke="black"/><path d="M 10 45 C 30 20, 60 60, 90 45" fill="none" stroke="black"/></svg>';

async function start(device) {
  const proxy = process.env.HTTPS_PROXY ? { server: process.env.HTTPS_PROXY, bypass: "localhost,127.0.0.1" } : undefined;
  const browser = await chromium.launch({ proxy });
  const ctx = await browser.newContext({ ...(device ? devices[device] : { viewport: { width: 1440, height: 900 } }), ignoreHTTPSErrors: !!proxy, acceptDownloads: true });
  await ctx.addInitScript(() => { try { delete window.showSaveFilePicker; } catch { /* kept */ } });
  const page = await ctx.newPage();
  const errors = [];
  page.on("pageerror", e => errors.push("pageerror: " + e.message));
  page.on("console", m => { if (m.type() === "error" && !/status of 409/.test(m.text())) errors.push("console: " + m.text()); });
  await page.goto(process.env.URL);
  try { await page.waitForSelector(".login input", { timeout: 5000 }); await page.fill(".login input", process.env.KEY); await page.press(".login input", "Enter"); } catch { /* signed in */ }
  await page.waitForSelector("body[data-ready='1']", { timeout: 120000 });
  return { browser, ctx, page, errors };
}
const idle = page => page.waitForFunction(() => !window.__iyicam.app.busy, null, { timeout: 120000 });
const pt = (page, x, y) => page.evaluate(([x, y]) => { const c = window.__iyicam.app.ui.canvas, r = c.canvas.getBoundingClientRect(), [sx, sy] = c.w2s(x, y); return [r.left + sx, r.top + sy]; }, [x, y]);
const doc = page => page.evaluate(() => { const d = window.__iyicam.app.cur; return { n: d.sheet.vectors.length, sel: d.selection.length, undo: d.undoStack.length, tps: d.project.toolpaths.map(t => t.name), results: d.results ? d.results.results.length : 0,
  errs: d.results ? d.results.results.filter(r => r.error).map(r => r.error) : [], stale: d.stale, sim: !!d.sim, tool: window.__iyicam.app.tool }; });
async function importFile(page, file) {
  await page.evaluate(() => window.__iyicam.A.runItem("file.import_vectors")); await page.waitForSelector(".dlg");
  const [fc] = await Promise.all([page.waitForEvent("filechooser"), page.click(".dlg button:has-text('This device')")]);
  await fc.setFiles(file); await page.waitForFunction(() => !window.__iyicam.app.busy, null, { timeout: 60000 }); await page.waitForTimeout(1500); await idle(page);
}

async function desktop() {
  const { browser, page, errors } = await start();
  // draw a rectangle and a circle with the mouse
  await page.keyboard.press("r");
  let a = await pt(page, 50, 50), b = await pt(page, 150, 120);
  await page.mouse.move(...a); await page.mouse.down(); await page.mouse.move(...b, { steps: 5 }); await page.mouse.up();
  await page.keyboard.press("c");
  a = await pt(page, 250, 100); b = await pt(page, 280, 100);
  await page.mouse.move(...a); await page.mouse.down(); await page.mouse.move(...b, { steps: 5 }); await page.mouse.up();
  await page.keyboard.press("Escape"); await page.keyboard.press("Escape");
  out.drawn = await doc(page); step("drawn");
  await importFile(page, { name: "shapes.svg", mimeType: "image/svg+xml", buffer: Buffer.from(SVG) });
  out.svg = await doc(page); step("svg");
  out.svgBox = await page.evaluate(async () => { const { default: G } = await import("/app/ui/geom.js"); const v = window.__iyicam.app.cur.selected(); const b = G.bbox(v); return [b.x0, b.y0, b.x1, b.y1].map(x => +x.toFixed(2)); });
  await importFile(page, process.env.FILES + "/t.dxf");
  out.dxf = await doc(page); step("dxf");
  // a profile and a pocket of everything
  await page.evaluate(() => { const { app, A } = window.__iyicam; A.select(app.cur.sheet.vectors.map(v => v.id)); });
  for (const op of ["profile", "pocket"]) {
    await page.evaluate(op => { window.__iyicam.A.newToolpath(op); }, op);
    await page.waitForSelector(".dlg"); await page.click(".dlg .btn.primary:has-text('Select')"); await page.waitForSelector(".tpwin");
    await page.click(".tpwin .btn.primary"); await idle(page); await page.waitForTimeout(500);
  }
  out.toolpaths = await doc(page); step("toolpaths");
  out.stats = await page.evaluate(() => window.__iyicam.app.cur.results.results.map(r => r.stats));
  await page.keyboard.press("F6"); await idle(page); await page.waitForTimeout(500);
  out.sim = await doc(page); step("sim");
  // undo everything back to the drawn rectangle, redo it all
  const steps = out.toolpaths.undo;
  for (let i = 0; i < steps; i++) await page.keyboard.press("Control+z");
  out.undone = await doc(page);
  for (let i = 0; i < steps; i++) await page.keyboard.press("Control+y");
  out.redone = await doc(page);
  await page.keyboard.press("F5"); await idle(page);
  out.recalc = await doc(page); step("recalc");
  // nest
  await page.evaluate(() => window.__iyicam.A.select([]));
  await page.evaluate(() => { window.__iyicam.A.nest(); }); await page.waitForSelector(".nestdlg"); await page.click(".nestdlg .btn.primary:has-text('Nest')");
  await page.waitForSelector(".dlg .report", { timeout: 120000 }); out.nestReport = await page.locator(".dlg .report").innerText();
  await page.click(".dlg .btn.primary"); await idle(page);
  out.nested = await doc(page);
  // the tool database: change a tool, Save, reload the page
  const toolsOf = () => page.evaluate(() => window.__iyicam.app.tools.map(t => [t.name, t.diameter]));
  out.toolsBefore = await toolsOf();
  await page.evaluate(() => { window.__iyicam.A.runItem("toolpaths.tool_database"); }); await page.waitForSelector(".tooldlg");
  await page.fill(".tooldlg .toolform input[type=text] >> nth=0", "My Cutter");
  await page.click(".tooldlg .btn.primary:has-text('Save')"); await idle(page); await page.waitForTimeout(800);
  out.toolsSaved = await toolsOf();
  await page.reload(); await page.waitForSelector("body[data-ready='1']", { timeout: 120000 });
  out.toolsReloaded = await toolsOf();
  out.errors = errors;
  await browser.close();
}

async function phone(device) {
  const { browser, ctx, page, errors } = await start(device);
  const r = {};
  const fx = fs.readFileSync(process.env.FIXTURE, "utf8");
  await page.evaluate(async j => { await window.__iyicam.A.openDocFromBytes({ name: "demo.icam", bytes: new TextEncoder().encode(j) }); }, fx);
  await page.waitForTimeout(600);
  const overflow = () => page.evaluate(() => { const W = innerWidth, bad = []; for (const e of document.querySelectorAll(".dlg *, .tpwin *")) { const b = e.getBoundingClientRect(); if (b.width && b.right > W + 1) bad.push(String(e.className || e.tagName)); } return { page: document.documentElement.scrollWidth - W, bad: bad.slice(0, 5) }; });
  r.pageOverflow = await overflow();
  // touch drawing: the Rectangle tool by a tap, a one-finger drag
  const cdp = await ctx.newCDPSession(page);
  const touch = (type, pts) => cdp.send("Input.dispatchTouchEvent", { type, touchPoints: pts.map(([x, y], id) => ({ x, y, id })) });
  await page.tap("[data-id="draw.rectangle"]");
  const a = await pt(page, 100, 100), b = await pt(page, 300, 250);
  await touch("touchStart", [a]); for (let i = 1; i <= 6; i++) await touch("touchMove", [[a[0] + (b[0] - a[0]) * i / 6, a[1] + (b[1] - a[1]) * i / 6]]); await touch("touchEnd", []);
  await page.waitForTimeout(300);
  r.touchDraw = await doc(page);
  // dialogs stay inside the screen and their buttons can be reached
  r.dialogs = {};
  for (const id of ["toolpaths.tool_database", "toolpaths.material_setup", "file.save_g_code", "edit.preferences", "file.new"]) {
    await page.evaluate(id => { window.__iyicam.A.runItem(id); }, id);
    await page.waitForSelector(".dlg", { timeout: 5000 }).catch(() => {}); await page.waitForTimeout(400);
    r.dialogs[id] = await page.evaluate(() => { const e = document.querySelector(".dlg"); if (!e) return null; const bb = e.getBoundingClientRect(); const prim = document.querySelector(".dlg .btn.primary"); const pr = prim && prim.getBoundingClientRect();
      return { left: bb.left, right: bb.right, vw: innerWidth, primaryVisible: !!pr && pr.bottom <= innerHeight && pr.top >= 0, overflow: [...document.querySelectorAll(".dlg *")].filter(x => x.getBoundingClientRect().right > innerWidth + 1).length }; });
    await page.keyboard.press("Escape"); await page.waitForTimeout(300);
    if (await page.locator(".dlg").count()) { await page.locator(".dlg .btn:has-text('Cancel')").first().tap(); await page.waitForTimeout(300); }
  }
  // the left drawer: the buttons under the layer list (add, delete ...) must be reachable
  await page.locator(".toolbar button").first().tap(); await page.waitForTimeout(500);
  r.layerButtons = await page.evaluate(() => { const btn = [...document.querySelectorAll(".panel.layers .btnrow button")].map(b => { const q = b.getBoundingClientRect(); const x = q.left + q.width / 2, y = q.top + q.height / 2; const top = document.elementFromPoint(x, y); return { title: b.title, hit: !!top && (top === b || b.contains(top)), y: Math.round(y), vh: innerHeight }; }); return btn; });
  // the toolpath window: Calculate is reachable on a phone
  await page.keyboard.press("Escape");
  r.errors = errors;
  await browser.close();
  return r;
}

(async () => {
  if (process.env.MODE === "desktop") await desktop();
  else out[process.env.DEVICE] = await phone(process.env.DEVICE);
  console.log("RESULT " + JSON.stringify(out));
})().catch(e => { console.error(e); process.exit(2); });

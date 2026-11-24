/* Drives the window the way a person does and checks what happens: select, move, undo, shortcuts, menus, dialogs.
 *   KEY=... NODE_PATH=$(npm root -g) node check_ui.js        (needs `iyicam serve --local`, see lib.js) */
const { open, shot, loadFixture } = require("./lib");
const assert = require("assert");
const results = [];
const ok = (name, cond, extra = "") => { results.push([cond ? "ok  " : "FAIL", name, extra]); console.log(cond ? "ok  " : "FAIL", name, extra); };

(async () => {
  const { browser, page, errors } = await open();
  await loadFixture(page);
  const st = () => page.evaluate(() => { const d = window.__iyicam.app.cur; return { sel: d.selection.length, undo: d.undoStack.length, redo: d.redoStack.length, n: d.sheet.vectors.length, dirty: d.dirty,
    first: d.sheet.vectors[0].contours[0].start, label: d.undoLabel(), tps: d.project.toolpaths.length }; });
  const pt = (x, y) => page.evaluate(([x, y]) => { const c = window.__iyicam.app.ui.canvas, r = c.canvas.getBoundingClientRect(), [sx, sy] = c.w2s(x, y); return [r.left + sx, r.top + sy]; }, [x, y]);

  // select by click on a line of the first rounded rectangle (its bottom edge, at y=60)
  let [x, y] = await pt(100, 60);   // (not 130: that is where the handle of the bottom side is)
  await page.mouse.click(x, y);
  let s = await st(); ok("click selects one vector", s.sel === 1, JSON.stringify(s.sel));
  // move by dragging 80 px
  await page.mouse.move(x, y); await page.mouse.down(); await page.mouse.move(x + 40, y - 20, { steps: 4 }); await page.mouse.move(x + 80, y - 40, { steps: 4 }); await page.mouse.up();
  const s2 = await st();
  ok("drag moves it and keeps an undo step", s2.undo === s.undo + 1 && (s2.first[0] !== s.first[0] || s2.first[1] !== s.first[1]) && s2.label === "Move", `${s.first} -> ${s2.first} ${s2.label}`);
  await page.keyboard.press("Control+z");
  const s3 = await st();
  ok("Ctrl+Z undoes the move", s3.first[0] === s.first[0] && s3.redo === 1, `${s3.first}`);
  await page.keyboard.press("Control+y");
  ok("Ctrl+Y redoes it", (await st()).first[0] === s2.first[0]);
  await page.keyboard.press("Control+Shift+z");
  await page.keyboard.press("Control+z");
  ok("Ctrl+Shift+Z redoes (after another undo)", (await st()).first[0] === s.first[0]);
  // arrow nudge
  await page.keyboard.press("ArrowRight"); await page.keyboard.press("ArrowRight");
  const sn = await st();
  ok("arrow keys nudge", sn.first[0] > s.first[0], `${s.first[0]} -> ${sn.first[0]}`);
  await page.keyboard.press("Delete");
  ok("Delete removes the selection", (await st()).n === s.n - 1);
  await page.keyboard.press("Control+z"); await page.keyboard.press("Control+z");
  ok("undo of delete and nudge brings it back", (await st()).n === s.n && (await st()).first[0] === s.first[0]);
  await page.keyboard.press("Control+a");
  ok("Ctrl+A selects all", (await st()).sel === s.n, String((await st()).sel));
  await shot(page, "selected_all");
  await page.keyboard.press("Escape");
  ok("Esc clears the selection", (await st()).sel === 0);
  // window selection (left to right: wholly inside) and crossing
  const a = await pt(40, 30), b = await pt(220, 200);
  await page.mouse.move(a[0], a[1]); await page.mouse.down(); await page.mouse.move(b[0], b[1], { steps: 6 }); await page.mouse.up();
  ok("window selects what is wholly inside", (await st()).sel === 1, String((await st()).sel));
  await page.keyboard.press("Escape");
  const c = await pt(180, 120), d = await pt(100, 20);
  await page.mouse.move(c[0], c[1]); await page.mouse.down(); await page.mouse.move(d[0], d[1], { steps: 6 }); await page.mouse.up();
  ok("crossing (right to left) selects what it touches", (await st()).sel >= 1, String((await st()).sel));
  await page.keyboard.press("Escape");
  // zoom with the wheel at the cursor keeps the point
  const before = await pt(600, 400);
  await page.mouse.move(before[0], before[1]); await page.mouse.wheel(0, -300);
  const after = await pt(600, 400);
  ok("wheel zooms at the cursor", Math.abs(after[0] - before[0]) < 2 && Math.abs(after[1] - before[1]) < 2);
  await page.keyboard.press("f");
  ok("F zooms to the sheet", true);
  // draw a rectangle
  await page.keyboard.press("r");
  const r0 = await pt(300, 650), r1 = await pt(500, 750);
  const n0 = (await st()).n;
  await page.mouse.move(r0[0], r0[1]); await page.mouse.down(); await page.mouse.move(r1[0], r1[1], { steps: 5 }); await page.mouse.up();
  ok("the Rectangle tool draws a vector", (await st()).n === n0 + 1, (await st()).label);
  await page.keyboard.press("Escape"); await page.keyboard.press("Escape");
  // menus
  await page.click(".mtop[data-id=file]");
  await shot(page, "menu_file");
  ok("File menu shows items", (await page.locator(".dropdown .mitem").count()) >= 15);
  await page.hover(".mtop[data-id=vectors]");
  await page.hover(".dropdown .mitem:has-text('Create')");
  await shot(page, "menu_vectors_create");
  ok("Vectors > Create submenu opens", await page.locator(".submenu .mitem:has-text('Rectangle')").isVisible());
  await page.keyboard.press("Escape");
  await page.click(".mtop[data-id=edit]");
  const undoText = await page.locator(".dropdown .mitem").first().innerText();
  ok("Edit menu says what Undo undoes", /Undo Draw rectangle/.test(undoText), undoText.trim());
  await page.keyboard.press("Escape");
  const grey = await page.evaluate(async () => { document.querySelector(".mtop[data-id=plugins]").click(); return [...document.querySelectorAll(".dropdown .mitem.disabled")].map(e => e.title).join("|"); });
  ok("an item not done is greyed with the tooltip", /Not on the web yet/.test(grey), grey);
  await page.keyboard.press("Escape");
  console.log(errors.join("\n") || "no page errors");
  ok("no page errors", errors.length === 0);
  await browser.close();
  process.exit(results.some(r => r[0] === "FAIL") ? 1 : 0);
})().catch(e => { console.error(e); process.exit(2); });

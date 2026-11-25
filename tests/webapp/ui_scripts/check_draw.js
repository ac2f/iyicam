/* The draw features: Edit Nodes, Star, Text, Trim, Set Start Point, start points menu, Text on Curve, Trace Bitmap, Vector Library,
 * sheet outline.   URL=http://localhost:PORT/app/ KEY=... NODE_PATH=$(npm root -g) node check_draw.js */
const { open, shot, loadFixture } = require("./lib");
const fs = require("fs"), os = require("os"), path = require("path");
const results = [];
const ok = (name, cond, extra = "") => { results.push(!!cond); console.log(cond ? "ok  " : "FAIL", name, extra); };
(async () => {
  const { browser, page, errors } = await open();
  const ev = (fn, arg) => page.evaluate(fn, arg);
  const pt = (x, y) => ev(([x, y]) => { const c = window.__iyicam.app.ui.canvas, r = c.canvas.getBoundingClientRect(), [sx, sy] = c.w2s(x, y); return [r.left + sx, r.top + sy]; }, [x, y]);
  const sheetVecs = () => ev(() => window.__iyicam.app.cur.sheet.vectors.length);
  const last = () => ev(() => { const v = window.__iyicam.app.cur.sheet.vectors; return JSON.parse(JSON.stringify(v[v.length - 1])); });
  const byId = id => ev(id => JSON.parse(JSON.stringify(window.__iyicam.app.cur.sheet.vectors.find(v => v.id === id))), id);
  const run = id => ev(id => window.__iyicam.A.runItem(id), id);
  const settle = async (ms = 400) => { await page.waitForTimeout(ms); await page.waitForFunction(() => !window.__iyicam.app.busy, null, { timeout: 60000 }); };
  const addRect = (x0, y0, x1, y1) => ev(([x0, y0, x1, y1]) => { const { A } = window.__iyicam; return import("/app/ui/geom.js").then(({ default: G }) => { A.addContours([G.rectangle(x0, y0, x1, y1)], "Draw rectangle"); }); }, [x0, y0, x1, y1]);
  const drag = async (a, b, steps = 6) => { const [ax, ay] = await pt(...a), [bx, by] = await pt(...b); await page.mouse.move(ax, ay); await page.mouse.down(); await page.mouse.move(bx, by, { steps }); await page.mouse.up(); };
  const click = async p => { const [x, y] = await pt(...p); await page.mouse.click(x, y); };

  // a fresh project: the demo fixture is not needed, everything is drawn
  const items = ["draw.edit_nodes", "draw.star", "draw.text", "draw.trim_vectors", "draw.set_start_point", "vectors.vector_library", "vectors.keep_selection_in_the_library",
    "vectors.trace_bitmap", "vectors.text_on_curve", "vectors.start_points.set_start_point", "vectors.start_points.bottom_left_corner",
    "vectors.start_points.bottom_right_corner", "vectors.start_points.top_left_corner", "vectors.start_points.top_right_corner", "vectors.start_points.middle_of_a_side.top_side",
    "vectors.start_points.middle_of_a_side.bottom_side", "vectors.start_points.middle_of_a_side.left_side", "vectors.start_points.middle_of_a_side.right_side",
    "vectors.start_points.next_corner", "vectors.start_points.previous_corner", "vectors.start_points.opposite_point", "vectors.start_points.smart_best_place",
    "vectors.start_points.release_to_smart_start_points", "vectors.start_points.smart_start_points", "nesting.set_sheet_outline_from_selected_vectors", "nesting.clear_sheet_outline"];
  const missing = await ev(items => items.filter(i => !window.__iyicam.A.registry.has(i)), items);
  ok("every item is registered", missing.length === 0, missing.join(", "));
  const grey = await ev(() => [...document.querySelectorAll("#drawbar .tbtn")].filter(b => b.classList.contains("disabled")).map(b => b.dataset.id));
  ok("no button of the drawing bar is greyed", grey.length === 0, grey.join(","));

  // ---------------------------------------------------------------------------------- Star
  await run("draw.star");
  ok("Star is the tool", (await ev(() => window.__iyicam.app.tool)) === "star");
  const n0 = await sheetVecs();
  await drag([300, 300], [380, 300]);
  let v = await last();
  ok("Star: a drag draws a 5 pointed star (10 corners)", (await sheetVecs()) === n0 + 1 && v.contours[0].closed && v.contours[0].segs.length === 10, JSON.stringify(v.contours[0].segs.length));
  const r = Math.hypot(v.contours[0].start[0] - 300, v.contours[0].start[1] - 300);
  ok("Star: the first tip is on the radius dragged", Math.abs(r - 80) < 1.5, String(r));
  await page.keyboard.press("+");
  await drag([500, 300], [560, 300]);
  v = await last();
  ok("Star: + gives it a point more", v.contours[0].segs.length === 12, String(v.contours[0].segs.length));
  await page.keyboard.press("-"); await page.keyboard.press("-");
  await shot(page, "draw_star");

  // ---------------------------------------------------------------------------------- Text
  await run("draw.text");
  await click([100, 600]);
  await page.waitForSelector(".dlg textarea");
  await page.fill(".dlg textarea", "iyiCAM Çğş");
  await shot(page, "draw_text_dialog");
  await page.click(".dlg .btn.primary");
  await settle();
  v = await last();
  const tb = await ev(async id => { const { default: G } = await import("/app/ui/geom.js"); const b = G.bbox([window.__iyicam.app.cur.sheet.vectors.slice(-1)[0]]); return b; }, null);
  ok("Text: a vector of the letters is added at the click", v.name.startsWith("iyiCAM") && v.contours.length > 8 && tb.x0 >= 99 && tb.x0 < 110, JSON.stringify(tb));
  ok("Text: the capital letters are 30 mm (the default) tall", tb.y1 - tb.y0 > 29 && tb.y1 - tb.y0 < 45, String(tb.y1 - tb.y0));
  const textId = v.id;

  // ---------------------------------------------------------------------------------- Edit Nodes
  await run("draw.select_transform");
  await addRect(100, 100, 220, 200);
  const rect = await last();
  ev(() => window.__iyicam.A.select([window.__iyicam.app.cur.sheet.vectors.slice(-1)[0].id]));
  await run("draw.edit_nodes");
  const before = JSON.stringify(rect.contours[0]);
  await drag([220, 200], [260, 240]);
  await settle();
  let moved = await byId(rect.id);
  ok("Edit Nodes: dragging a corner moves it (both sides follow)", JSON.stringify(moved.contours[0]) !== before && moved.contours[0].segs.some(s => s[1] > 240 && s[2] > 225) && moved.contours[0].segs.length === 4, JSON.stringify(moved.contours[0].segs));
  await page.keyboard.press("Control+z");
  moved = await byId(rect.id);
  ok("Edit Nodes: Undo takes the corner back", JSON.stringify(moved.contours[0]) === before);
  await page.mouse.dblclick(...(await pt(160, 100)));
  await settle();
  moved = await byId(rect.id);
  ok("Edit Nodes: a double click on a span inserts a node", moved.contours[0].segs.length === 5, String(moved.contours[0].segs.length));
  await click([160, 100]);              // (now a node: select it and delete it)
  await page.keyboard.press("Delete");
  await settle();
  moved = await byId(rect.id);
  ok("Edit Nodes: Del deletes the selected node, not the vector", moved && moved.contours[0].segs.length === 4, moved && String(moved.contours[0].segs.length));
  await page.mouse.click(...(await pt(160, 100)), { button: "right" });
  await page.waitForSelector(".popmenu.draw .item");
  const menu = await page.locator(".popmenu.draw .item").allTextContents();
  ok("Edit Nodes: right click offers the span options", menu.includes("Insert node here") && menu.includes("Convert span to arc") && menu.includes("Reverse direction"), menu.join("|"));
  await page.click(".popmenu.draw .item:has-text('Convert span to arc')");
  await settle();
  moved = await byId(rect.id);
  ok("Edit Nodes: convert span to arc", moved.contours[0].segs.some(s => s[0] === "A"));
  await shot(page, "draw_nodes");
  await run("draw.select_transform");
  await ev(() => window.__iyicam.A.select([]));

  // ---------------------------------------------------------------------------------- Trim
  await ev(async () => { const { A } = window.__iyicam; const { default: G } = await import("/app/ui/geom.js"); A.addContours([G.polyline([[400, 450], [600, 450]], false)], "line"); A.addContours([G.polyline([[450, 400], [450, 500]], false)], "line"); A.addContours([G.polyline([[550, 400], [550, 500]], false)], "line"); });
  const horiz = (await ev(() => window.__iyicam.app.cur.sheet.vectors.slice(-3)[0].id));
  await run("draw.trim_vectors");
  await click([500, 450]);
  await settle();
  const hv = await byId(horiz);
  ok("Trim: the piece between two crossings is removed", hv && hv.contours.length === 2, hv && String(hv.contours.length));
  await shot(page, "draw_trim");
  await run("draw.select_transform");

  // ---------------------------------------------------------------------------------- Start points
  await addRect(700, 100, 800, 180);
  const sq = await last();
  await ev(() => window.__iyicam.A.select([window.__iyicam.app.cur.sheet.vectors.slice(-1)[0].id]));
  const startOf = async () => (await byId(sq.id));
  for (const [id, want] of [["vectors.start_points.bottom_left_corner", [700, 100]], ["vectors.start_points.top_right_corner", [800, 180]], ["vectors.start_points.top_left_corner", [700, 180]],
    ["vectors.start_points.bottom_right_corner", [800, 100]], ["vectors.start_points.middle_of_a_side.top_side", [750, 180]], ["vectors.start_points.middle_of_a_side.left_side", [700, 140]],
    ["vectors.start_points.middle_of_a_side.right_side", [800, 140]], ["vectors.start_points.middle_of_a_side.bottom_side", [750, 100]]]) {
    await run(id); await settle(150);
    const s = (await startOf()).contours[0].start;
    ok("Start: " + id.split(".").slice(2).join(" "), Math.hypot(s[0] - want[0], s[1] - want[1]) < 0.01, JSON.stringify(s));
  }
  await run("vectors.start_points.bottom_left_corner"); await settle(150);
  await run("vectors.start_points.next_corner"); await settle(150);
  let s = (await startOf()).contours[0].start;
  ok("Start: next corner steps along (to the next node: the sides have the middles set above)", Math.hypot(s[0] - 700, s[1] - 100) > 1, JSON.stringify(s));
  await run("vectors.start_points.previous_corner"); await settle(150);
  s = (await startOf()).contours[0].start;
  ok("Start: previous corner steps back", Math.hypot(s[0] - 700, s[1] - 100) < 0.01, JSON.stringify(s));
  await run("vectors.start_points.opposite_point"); await settle(150);
  s = (await startOf()).contours[0].start;
  ok("Start: opposite point", Math.hypot(s[0] - 800, s[1] - 180) < 0.01, JSON.stringify(s));
  ok("Start: the start is kept by hand", (await startOf()).locked_starts && (await startOf()).locked_starts.length === 1);
  await run("vectors.start_points.release_to_smart_start_points"); await settle(150);
  ok("Start: Release gives it back", !((await startOf()).locked_starts || []).length);
  // the tool: click on the left side to start there, with clinging to the middle
  await run("draw.set_start_point");
  await ev(() => window.__iyicam.A.select([]));
  const [mx, my] = await pt(701, 141);
  await page.mouse.move(mx, my); await page.waitForTimeout(500);
  await shot(page, "draw_start_tool");
  await page.mouse.down(); await page.mouse.up();
  await settle(600);
  s = (await startOf()).contours[0].start;
  ok("Set Start Point: a click near the middle of a side starts there", Math.hypot(s[0] - 700, s[1] - 140) < 0.5, JSON.stringify(s));
  await drag([700, 140], [750, 180], 8);
  await settle(600);
  s = (await startOf()).contours[0].start;
  ok("Set Start Point: dragging slides the start along the vector", Math.hypot(s[0] - 750, s[1] - 180) < 0.5, JSON.stringify(s));
  await page.mouse.click(...(await pt(750, 180)), { button: "right" });
  await settle(500);
  ok("Set Start Point: right click gives it back to smart start points", !((await startOf()).locked_starts || []).length);
  await run("draw.select_transform");

  // ---------------------------------------------------------------------------------- Text on Curve
  await ev(async () => { const { A } = window.__iyicam; const { default: G } = await import("/app/ui/geom.js"); A.addContours([G.polyline([[100, 700], [400, 700]], false)], "curve"); });
  const nTc = await sheetVecs();
  await run("vectors.text_on_curve");
  await page.waitForSelector(".dlg input[type=text]");
  await page.fill(".dlg input[type=text] >> nth=0", "Along");
  await page.click(".dlg .btn.primary");
  await settle();
  v = await last();
  ok("Text on Curve: a vector of letters along the selected one", (await sheetVecs()) === nTc + 1 && v.name === "Along" && v.contours.length >= 5);

  // ---------------------------------------------------------------------------------- Trace Bitmap
  const png = await ev(async () => {
    const cv = document.createElement("canvas"); cv.width = 80; cv.height = 60; const c = cv.getContext("2d");
    c.fillStyle = "#fff"; c.fillRect(0, 0, 80, 60); c.fillStyle = "#000"; c.fillRect(20, 15, 40, 30);
    const b = await new Promise(r => cv.toBlob(r)); const u = new Uint8Array(await b.arrayBuffer()); let s = ""; for (const x of u) s += String.fromCharCode(x); return btoa(s);
  });
  const pngPath = path.join(os.tmpdir(), "iyicam-trace-test.png");
  fs.writeFileSync(pngPath, Buffer.from(png, "base64"));
  const nTr = await sheetVecs();
  const chooser = page.waitForEvent("filechooser");
  run("vectors.trace_bitmap");
  // (without an agent the picker of the device is used at once; with one, "This device" is asked for first)
  try { await page.click(".dlg .btn.big:has-text('This device')", { timeout: 1500 }); } catch { /* no agent */ }
  (await chooser).setFiles(pngPath);
  await page.waitForSelector(".dlg .paramform");
  await page.click(".dlg .btn.primary");
  await settle(800);
  v = await last();
  const trb = await ev(async () => { const { default: G } = await import("/app/ui/geom.js"); return G.bbox([window.__iyicam.app.cur.sheet.vectors.slice(-1)[0]]); });
  ok("Trace Bitmap: the dark rectangle of the picture becomes a vector", (await sheetVecs()) === nTr + 1 && Math.abs((trb.x1 - trb.x0) - 100) < 2, JSON.stringify(trb));

  // ---------------------------------------------------------------------------------- Vector Library
  await run("vectors.vector_library");
  await page.waitForSelector(".veclib");
  const keepIds = await ev(() => { const { app, A } = window.__iyicam; const ids = app.cur.sheet.vectors.slice(-1).map(v => v.id); A.select(ids); return ids; });
  await ev(() => window.__iyicam.A.runItem("vectors.keep_selection_in_the_library"));
  await page.waitForSelector(".dlg input[type=text]");
  await page.fill(".dlg input[type=text]", "Traced box");
  await page.click(".dlg .btn.primary");
  await page.waitForSelector(".veclib .vitem");
  ok("Library: the selection is kept and listed", (await page.locator(".veclib .vitem .vname").first().textContent()) === "Traced box");
  await page.click(".veclib .btnrow button[title^='New group']");
  await page.fill(".dlg input[type=text]", "Logos"); await page.click(".dlg .btn.primary");
  await page.waitForSelector(".veclib .vgroup");
  await page.click(".veclib .btnrow button[title^='New group']");
  await page.fill(".dlg input[type=text]", "Customers"); await page.click(".dlg .btn.primary");
  await page.waitForFunction(() => document.querySelectorAll(".veclib .vgroup").length === 2);
  const rows = await page.locator(".veclib .vgroup").evaluateAll(els => els.map(e => parseInt(e.style.paddingLeft)));
  ok("Library: a group made in a group is stacked under it (indented)", rows.length === 2 && rows[1] > rows[0], JSON.stringify(rows));
  // keep into the subgroup
  await ev(ids => window.__iyicam.A.select(ids), keepIds);
  await ev(() => window.__iyicam.A.runItem("vectors.keep_selection_in_the_library"));
  await page.waitForSelector(".dlg input[type=text]"); await page.fill(".dlg input[type=text]", "In the subgroup"); await page.click(".dlg .btn.primary");
  await page.waitForFunction(() => document.querySelectorAll(".veclib .vitem").length === 2);
  const nIns = await sheetVecs();
  await page.dblclick(".veclib .vitem >> nth=0");
  await settle(600);
  ok("Library: a double click puts the drawing in the sheet, selected", (await sheetVecs()) === nIns + 1 && (await ev(() => window.__iyicam.app.cur.selection.length)) === 1);
  await page.waitForSelector(".veclib canvas.vlpic:not([hidden])");
  await shot(page, "draw_library");
  const kept = await ev(async () => (await window.__iyicam.app.engine.call("veclib", { action: "tree" })).tree.groups.length);
  ok("Library: it is kept in the engine's user folder", kept >= 1);
  await page.click(".veclib .vitem >> nth=0"); await page.click(".veclib .btnrow button[title='Delete']");
  await page.click(".dlg .btn.primary");
  await page.waitForFunction(() => document.querySelectorAll(".veclib .vitem").length === 1);
  ok("Library: Delete removes a drawing", true);

  // ---------------------------------------------------------------------------------- sheet outline
  await ev(async () => { const { A } = window.__iyicam; const { default: G } = await import("/app/ui/geom.js"); A.addContours([G.polyline([[900, 300], [1100, 300], [1100, 400], [1000, 400], [1000, 500], [900, 500]], true)], "plate"); });
  const w0 = await ev(() => window.__iyicam.app.cur.sheet.width);
  await run("nesting.set_sheet_outline_from_selected_vectors");
  await settle();
  const sh = await ev(() => { const s = window.__iyicam.app.cur.sheet; return { w: s.width, h: s.height, outline: !!s.outline, n: s.vectors.length }; });
  ok("Sheet outline: the sheet takes the shape and size of the selected vector", sh.outline && Math.abs(sh.w - 200) < 0.01 && Math.abs(sh.h - 200) < 0.01 && w0 !== sh.w, JSON.stringify(sh));
  await shot(page, "draw_outline");
  await run("nesting.clear_sheet_outline");
  await settle();
  ok("Sheet outline: Clear makes a rectangle again", !(await ev(() => !!window.__iyicam.app.cur.sheet.outline)));
  await page.keyboard.press("Control+z");
  ok("Sheet outline: Undo brings the shape back", await ev(() => !!window.__iyicam.app.cur.sheet.outline));
  await page.keyboard.press("Control+z"); await page.keyboard.press("Control+z");

  // ---------------------------------------------------------------------------------- the menu
  await page.click(".mtop[data-id='vectors']");
  await page.hover(".mitem:has-text('Start Points')");
  await page.waitForTimeout(200);
  const greyed = await page.locator(".dropdown .mitem.disabled").allTextContents();
  ok("the Vectors menu has none of these items greyed", !greyed.some(t => /Library|Trace|Text on Curve|Start Points|Corner|Opposite|Smart|Release/.test(t)), greyed.join("|"));
  await page.keyboard.press("Escape");
  await shot(page, "draw_menu");

  ok("no page errors", errors.length === 0, errors.join(" | "));
  await browser.close();
  const bad = results.filter(x => !x).length;
  console.log(bad ? `${bad} FAILED` : "all ok", `(${results.length} checks)`);
  process.exit(bad ? 1 : 0);
})().catch(e => { console.error(e); process.exit(2); });

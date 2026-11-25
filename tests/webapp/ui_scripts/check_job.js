/* The job features end to end against the real engine: backplot, job report, keyboard shortcuts, Python console, packages, templates,
 * post-processors, stock of plates, plugins, G-code words, the Cut Order panel, the Simulation panel, Quit.
 *   KEY=... NODE_PATH=$(npm root -g) node check_job.js */
const { open, shot, loadFixture } = require("./lib");
const results = [];
const ok = (name, cond, extra = "") => { results.push(cond); console.log(cond ? "ok  " : "FAIL", name, extra); };
const GCODE = "G21 G90\nT1 M6\nG0 X20 Y20 Z5\nG1 Z-2 F500\nG1 X120 Y20\nG1 X120 Y80\nG1 X20 Y80\nG1 X20 Y20\nG0 Z5\nM30\n";
const PLUGIN = 'PLUGIN = {"name": "Hello", "version": "1.0", "description": "Adds a square"}\n\ndef register(api):\n    def run(ctx, values):\n        ctx.add(api.shapes.rectangle(0, 0, 30, 30))\n    api.add_command("hello.square", "Hello Square", run, menu="Plugins")\n';

(async () => {
  const { browser, page, errors } = await open({ viewport: { width: 1500, height: 950 } });
  const ev = (fn, arg) => page.evaluate(fn, arg);
  const run = id => ev(id => { window.__iyicam.A.runItem(id); }, id);                // (a call that opens a window is not awaited)
  const waitIdle = () => page.waitForFunction(() => !window.__iyicam.app.busy, null, { timeout: 120000 });
  const doc = () => ev(() => { const d = window.__iyicam.app.cur; return { n: d.sheet.vectors.length, tps: d.project.toolpaths.length, stock: (d.project.stock || []).length, dirty: d.dirty, results: !!d.results, stale: d.stale, sel: d.selection.length }; });
  const chooseDevice = async (name, mime, buffer) => {
    await page.waitForSelector(".dlg");
    if (await page.locator(".dlg button:has-text('This device')").count()) {
      const [fc] = await Promise.all([page.waitForEvent("filechooser"), page.click(".dlg button:has-text('This device')")]);
      await fc.setFiles({ name, mimeType: mime, buffer });
    }
  };
  const closeDialogs = async () => { for (let i = 0; i < 5 && await page.locator(".dlg-root").count(); i++) { await page.keyboard.press("Escape"); await page.waitForTimeout(150); } };

  await loadFixture(page);
  await page.waitForFunction(() => window.__iyicam.app.cur.results, null, { timeout: 120000 });
  await waitIdle();
  await ev(() => window.__iyicam.app.ui.canvas.zoomExtents());

  // -- every item of this round works (is in the registry)
  const items = ["file.open_g_code_backplot", "toolpaths.clear_g_code_backplot", "file.job_report", "file.quit", "edit.keyboard_shortcuts", "view.python_console", "plugins.python_console",
    "toolpaths.toolpath_packages", "toolpaths.save_selected_toolpaths_as_a_package", "toolpaths.save_toolpath_template", "toolpaths.load_toolpath_template", "toolpaths.post_processors",
    "nesting.stock_of_plates", "nesting.add_selected_vectors_to_stock", "nesting.add_leftover_of_current_sheet_to_stock", "plugins.plugin_manager", "help.g_code_words_i_j_k"];
  const missing = await ev(items => items.filter(i => !window.__iyicam.A.registry.has(i)), items);
  ok("every item of the job round is registered", missing.length === 0, missing.join(","));

  // -- job report
  await run("file.job_report"); await page.waitForSelector(".reportpreview table", { timeout: 60000 });
  ok("Job Report shows the toolpaths of the sheet", (await page.locator(".reportpreview tr").count()) >= 4 && /Profile 1/.test(await page.textContent(".reportpreview")));
  await shot(page, "job_report"); await closeDialogs();

  // -- backplot
  await run("file.open_g_code_backplot"); await chooseDevice("square.nc", "text/plain", Buffer.from(GCODE));
  await page.waitForFunction(() => window.__iyicam.app.cur.backplot, null, { timeout: 60000 }); await page.waitForTimeout(300);
  const bp = await ev(() => { const b = window.__iyicam.app.cur.backplot; return { cuts: b.preview.cuts.length, tools: b.tools.length, len: b.stats.feed_length }; });
  ok("Open G-code (Backplot) reads the file", bp.cuts >= 1 && bp.tools === 1 && Math.abs(bp.len - 327) < 1, JSON.stringify(bp));
  await shot(page, "job_backplot");
  await run("toolpaths.clear_g_code_backplot"); await page.waitForTimeout(100);
  ok("Clear G-code Backplot removes it", !(await ev(() => window.__iyicam.app.cur.backplot)));

  // -- keyboard shortcuts: change Duplicate to Ctrl+Shift+K and use it
  await run("edit.keyboard_shortcuts"); await page.waitForSelector(".scedit");
  await page.fill(".scedit input[type=text]", "Duplicate");
  await page.click(".scrow:has-text('Duplicate') >> nth=0");
  await page.focus(".sccapture"); await page.keyboard.press("Control+Shift+K"); await page.waitForTimeout(150);
  ok("the shortcut editor takes the keys pressed", /Ctrl\+Shift\+K/.test(await page.textContent(".sccapture")));
  await page.click(".dlg .btn.primary:has-text('OK')"); await page.waitForTimeout(200);
  ok("the change is kept", (await ev(() => window.__iyicam.A.shortcutOverrides()["edit.duplicate"] || [])).join() === "Ctrl+Shift+K");
  await ev(() => { const { app, A } = window.__iyicam; A.select([app.cur.sheet.vectors[0].id]); });
  const n0 = (await doc()).n;
  await page.focus("#canvashost canvas"); await page.keyboard.press("Control+Shift+K"); await page.waitForTimeout(200);
  ok("the new shortcut runs the item", (await doc()).n === n0 + 1);
  await page.keyboard.press("Control+z");
  await page.click(".mtop:has-text('Edit')");
  ok("the menu shows the new shortcut", /Ctrl\+Shift\+K/.test(await page.textContent(".menu.dropdown")));
  await page.keyboard.press("Escape");
  await ev(() => window.__iyicam.A.setShortcutOverrides({}));
  ok("Reset brings the program's keys back", (await ev(() => window.__iyicam.A.keysOf({ id: "edit.duplicate", sc: "Ctrl+D" }).join())) === "Ctrl+D");

  // -- Python console
  await run("view.python_console"); await page.waitForSelector(".consolepane:not([hidden]) .conin");
  await page.waitForFunction(() => /Python console/.test(document.querySelector(".conout").textContent), null, { timeout: 30000 });
  const n1 = (await doc()).n;
  await page.fill(".conin", "add(shapes.circle((100, 100), 25))"); await page.press(".conin", "Enter");
  await page.waitForFunction(n => window.__iyicam.app.cur.sheet.vectors.length === n + 1, n1, { timeout: 30000 });
  ok("the console adds a vector to the project", true);
  await page.fill(".conin", "print(len(sheet().vectors), len(project.toolpaths))"); await page.press(".conin", "Enter");
  await page.waitForFunction(() => /\d+ 2\n/.test(document.querySelector(".conout").textContent), null, { timeout: 30000 });
  ok("the console prints what it knows of the project", true);
  await ev(() => window.__iyicam.A.undo()); await page.waitForTimeout(150);
  ok("a change from the console is one step of Undo", (await doc()).n === n1);
  await shot(page, "job_console");
  await run("plugins.python_console"); await page.waitForTimeout(200);
  ok("the console closes again", await page.locator(".consolepane[hidden]").count() === 1);

  // -- packages: a package from the toolpaths, then used on vectors
  await ev(() => { const { app } = window.__iyicam; app.cur.toolpathSelection = [app.cur.project.toolpaths[0].id]; });
  await run("toolpaths.save_selected_toolpaths_as_a_package"); await page.waitForSelector(".dlg input[type=text]");
  await page.fill(".dlg input[type=text]", "Contour"); await page.click(".dlg .btn.primary");
  await page.waitForSelector(".pkwin"); await page.waitForTimeout(400);
  ok("the packages window opens on the new package", /Contour/.test(await page.textContent(".pklist")) && (await page.locator(".pkitems tbody tr").count()) === 1);
  await shot(page, "job_packages");
  await page.click(".dlg-buttons .btn:has-text('Save'):not(.primary)"); await page.waitForTimeout(500);
  ok("the package is kept", (await ev(async () => (await window.__iyicam.app.engine.call("packages", {})).packages.map(p => p.name))).join() === "Contour");
  await ev(() => { const { app, A } = window.__iyicam; A.select(app.cur.sheet.vectors.slice(0, 3).map(v => v.id)); });
  const t0 = (await doc()).tps;
  await ev(async () => { const m = await import("/app/ui/dialogs/job.js"); const { app } = window.__iyicam; m.applyPackage(app.cur, m.packages()[0].id); });
  await page.waitForSelector(".pkitem"); await page.waitForTimeout(600);
  ok("the package window shows the names the toolpaths will have", /→/.test(await page.textContent(".pkitem")));
  await shot(page, "job_package_apply");
  await page.click(".dlg .btn.primary:has-text('Create toolpaths')"); await page.waitForFunction(t => window.__iyicam.app.cur.project.toolpaths.length === t + 1, t0, { timeout: 60000 }); await waitIdle();
  const made = await ev(() => { const d = window.__iyicam.app.cur; const t = d.project.toolpaths[d.project.toolpaths.length - 1]; return { group: t.group, vectors: t.selection.vectors.length }; });
  ok("Create toolpaths adds the package's toolpath on the selection, in a group", made.vectors === 3 && /Contour S1/.test(made.group), JSON.stringify(made));
  await ev(() => window.__iyicam.A.undo()); await waitIdle();
  // the Use a Package submenu
  await page.click(".mtop:has-text('Toolpaths')"); await page.hover(".mitem:has-text('Use a Package')"); await page.waitForTimeout(200);
  ok("Use a Package lists the package", /Contour…/.test(await page.textContent(".menu.dropdown")));
  await page.keyboard.press("Escape"); await page.mouse.click(700, 500);

  // -- toolpath templates
  await ev(() => { const d = window.__iyicam.app.cur; for (const t of d.project.toolpaths) { t.selection = { mode: "layers", vectors: [], layers: [d.project.layers[0].id] }; } });
  const dlTpl = page.waitForEvent("download", { timeout: 60000 });
  await run("toolpaths.save_toolpath_template"); await page.waitForSelector(".dlg .choose", { timeout: 60000 });
  await page.click(".dlg button:has-text('Download to this device')");
  const tplFile = require("fs").readFileSync(await (await dlTpl).path(), "utf8");
  ok("Save Toolpath Template writes the toolpaths", /iyicam-toolpaths/.test(tplFile) && /layer_names/.test(tplFile));
  const tt = (await doc()).tps;
  await run("toolpaths.load_toolpath_template"); await chooseDevice("toolpaths.icamtpl", "application/json", Buffer.from(tplFile));
  await page.waitForFunction(t => window.__iyicam.app.cur.project.toolpaths.length === t + 2, tt, { timeout: 60000 }); await waitIdle();
  ok("Load Toolpath Template adds them", true);
  await ev(() => window.__iyicam.A.undo()); await waitIdle();

  // -- post-processors: make one from a built-in, change an option, save, then delete it
  await run("toolpaths.post_processors"); await page.waitForSelector(".postwin .postpreview .gl", { timeout: 60000 });
  ok("the post editor lists the posts and previews a sample part", (await page.locator(".postlist .pkrow").count()) >= 5 && (await page.locator(".postpreview .gl").count()) > 10);
  await page.click(".postlist .pkrow:has-text('Fanuc') >> nth=0");
  await page.click(".postleft .btn:has-text('New from selected')"); await page.waitForSelector(".dlg input[type=text]");
  await page.fill(".dlg input[type=text] >> nth=-1", "My Fanuc"); await page.click(".dlg .btn.primary >> nth=-1"); await page.waitForTimeout(300);
  await page.click(".postwin .tab:has-text('Blocks')"); await page.waitForSelector(".blockedit textarea");
  await page.fill(".blockedit textarea", "(MY HEADER {file_name})\n{unit_code} G90 G17"); await page.waitForTimeout(900);
  ok("the preview follows a change of a block", /MY HEADER/.test(await page.textContent(".postpreview")));
  await page.click(".postwin .btn.primary:has-text('Save')"); await page.waitForTimeout(1200);
  const posts = await ev(async () => (await window.__iyicam.app.engine.call("posts", {})).posts.filter(p => p.editable).map(p => p.name));
  ok("Save keeps the post of the user", posts.includes("My Fanuc"), JSON.stringify(posts));
  ok("the Save G-code window knows it", await ev(() => window.__iyicam.app.schema.posts.some(p => p.name === "My Fanuc")));
  await page.click(".postlist .pkrow:has-text('My Fanuc')"); await page.click(".postleft .btn:has-text('Delete')"); await page.click(".dlg .btn.primary:has-text('Delete') >> nth=-1"); await page.waitForTimeout(800);
  ok("Delete removes it", !(await ev(async () => (await window.__iyicam.app.engine.call("posts", {})).posts.some(p => p.name === "My Fanuc"))));
  await shot(page, "job_posts"); await closeDialogs();

  // -- stock of plates
  await run("nesting.stock_of_plates"); await page.waitForSelector(".stockwin");
  await page.click(".stockwin .btn:has-text('Add plate')"); await page.waitForSelector(".formdlg"); await page.click(".formdlg .btn.primary"); await page.waitForTimeout(500);
  ok("Add plate puts a plate in the table", (await page.locator(".stocktable tbody tr").count()) === 1);
  await page.click(".stockwin .btn:has-text('Leftover of sheet')"); await page.waitForSelector(".formdlg"); await page.click(".formdlg .btn.primary"); await page.waitForTimeout(1500);
  const rows = await page.locator(".stocktable tbody tr").count();
  ok("Leftover of sheet adds the plates that are left", rows >= 2, String(rows));
  await shot(page, "job_stock");
  await page.click(".dlg .btn.primary:has-text('OK')"); await page.waitForTimeout(300);
  ok("OK keeps the stock in the project", (await doc()).stock === rows);
  await ev(() => window.__iyicam.A.undo());
  // plates drawn as closed vectors
  await ev(() => { const { app, A } = window.__iyicam; A.select([app.cur.sheet.vectors[0].id]); });
  await run("nesting.add_selected_vectors_to_stock"); await page.waitForSelector(".dlg .dlg-text"); await page.click(".dlg .btn.primary"); await page.waitForTimeout(200);
  ok("Add Selected Vectors to Stock adds a plate", (await doc()).stock >= 1);
  await ev(() => window.__iyicam.A.undo());
  await run("nesting.add_leftover_of_current_sheet_to_stock"); await page.waitForSelector(".formdlg"); await page.click(".formdlg .btn.primary"); await page.waitForSelector(".dlg .dlg-text", { timeout: 30000 }); await page.click(".dlg .btn.primary");
  ok("Add Leftover of Current Sheet to Stock adds plates", (await doc()).stock >= 1);
  await ev(() => window.__iyicam.A.undo());

  // -- G-code words
  await run("help.g_code_words_i_j_k"); await page.waitForSelector(".glossary .gw"); await page.waitForTimeout(500);
  ok("G-code Words lists the words and explains a line", (await page.locator(".glossary .gw").count()) > 20 && /centre/i.test(await page.textContent(".explained")));
  await closeDialogs();

  // -- plugins
  await run("plugins.plugin_manager"); await page.waitForSelector(".pm");
  const [fc] = await Promise.all([page.waitForEvent("filechooser"), page.click(".dlg .btn:has-text('Install')")]);
  await fc.setFiles({ name: "hello.py", mimeType: "text/x-python", buffer: Buffer.from(PLUGIN) });
  await page.waitForSelector(".prow:has-text('Hello')", { timeout: 60000 });
  ok("a plugin is installed and listed", true);
  ok("its command is in the schema now", await ev(() => window.__iyicam.app.schema.commands.some(c => c.id === "hello.square")));
  await closeDialogs();
  await page.click(".mtop:has-text('Plugins')");
  ok("the Plugins menu lists its command", /Hello Square/.test(await page.textContent(".menu.dropdown")));
  await page.keyboard.press("Escape"); await page.mouse.click(700, 500);
  await run("plugins.plugin_manager"); await page.waitForSelector(".pm .prow:has-text('Hello')"); await page.click(".pm .prow:has-text('Hello')");
  await page.click(".pdetail .btn:has-text('Remove')"); await page.click(".dlg .btn.primary:has-text('Remove') >> nth=-1");
  await page.waitForFunction(() => !document.querySelector(".pm .prow"), null, { timeout: 30000 });
  ok("the plugin is removed", true); await closeDialogs();

  // -- the Cut Order panel
  await ev(() => window.__iyicam.app.ui.docks.show("cutorder"));
  await page.waitForSelector(".cuttable tbody tr", { timeout: 60000 });
  const cuts0 = await page.locator(".cuttable tbody tr").count();
  ok("the Cut Order panel lists the cuts of the toolpath", cuts0 === 19 - 1 + 0 || cuts0 >= 10, String(cuts0));
  await shot(page, "job_cutorder");
  const order0 = await ev(() => window.__iyicam.app.cur.project.toolpaths[0].sequence.order.slice(0, 3));
  await page.click(".cuttable tbody tr >> nth=0"); await page.waitForTimeout(150);
  ok("selecting a row selects its vector", (await doc()).sel === 1);
  await page.click(".cutorder .btn:has-text('Down')"); await waitIdle(); await page.waitForTimeout(800);
  const order1 = await ev(() => window.__iyicam.app.cur.project.toolpaths[0].sequence.order.slice(0, 3));
  ok("Down moves the cut one place later (the order is frozen)", order1[1] === order0[0] && order1[0] === order0[1], JSON.stringify(order1));
  ok("and the toolpath was calculated again", !(await doc()).stale);
  // a setting waits for Apply; Preview shows the pink result
  await page.selectOption(".cutorder .group:has(legend:has-text('Order')) select >> nth=0", "rows_tl"); await page.waitForTimeout(200);
  ok("a chosen setting waits (nothing is applied)", /not applied yet/.test(await page.textContent(".cutbar")) && (await ev(() => window.__iyicam.app.cur.project.toolpaths[0].sequence.strategy)) !== "rows_tl");
  await page.click(".cutorder .group:has(legend:has-text('Order')) .btn:has-text('Preview')"); await page.waitForSelector(".cutbar:has-text('another place')", { timeout: 60000 }); await shot(page, "job_cutorder_preview");
  await page.click(".cutbar .btn:has-text('Apply')"); await waitIdle(); await page.waitForTimeout(800);
  ok("Apply puts the strategy into the toolpath", (await ev(() => window.__iyicam.app.cur.project.toolpaths[0].sequence.strategy)) === "rows_tl");
  // a start point at a place
  await page.click(".cuttable tbody tr >> nth=2"); await page.waitForTimeout(150);
  const key = await ev(() => document.querySelector(".cuttable tr.on").dataset.key);
  await page.click(".places .btn >> nth=2"); await waitIdle(); await page.waitForTimeout(800);       // (top right)
  const start = await ev(key => { const d = window.__iyicam.app.cur, [vid, ci] = key.split(":"); const v = d.sheet.vectors.find(x => x.id === vid); return { start: v.contours[+ci].start, locked: v.locked_starts }; }, key);
  ok("a place button starts the cut there and keeps it", start.locked && start.locked.includes(+key.split(":")[1]), JSON.stringify(start));
  await page.click(".startwhy ~ .btnrow .btn:has-text('Release')"); await waitIdle(); await page.waitForTimeout(500);
  ok("Release gives the start point back", !(await ev(key => { const d = window.__iyicam.app.cur, v = d.sheet.vectors.find(x => x.id === key.split(":")[0]); return (v.locked_starts || []).length; }, key)));
  // numbering by clicking: the third vector first
  await page.click(".cutorder .btn:has-text('Click to number')"); await page.waitForTimeout(150);
  const where = await ev(() => { const { app } = window.__iyicam; const c = app.ui.canvas, r = c.canvas.getBoundingClientRect(); const v = app.cur.sheet.vectors[5]; const b = window.__iyicam.A && null; void b; const pts = v.contours[0]; const [sx, sy] = c.w2s(pts.start[0], pts.start[1]); return [r.left + sx, r.top + sy, v.id]; });
  await page.mouse.click(where[0], where[1]); await page.waitForTimeout(150);
  await page.keyboard.press("Enter"); await waitIdle(); await page.waitForTimeout(800);
  ok("clicking a vector numbers its cut first", (await ev(() => window.__iyicam.app.cur.project.toolpaths[0].sequence.order[0])).startsWith(where[2]));
  await ev(() => { window.__iyicam.A.undo(); window.__iyicam.A.undo(); window.__iyicam.A.undo(); window.__iyicam.A.undo(); }); await waitIdle();

  // -- the Simulation panel
  await ev(() => window.__iyicam.app.ui.docks.show("simulation"));
  await page.waitForSelector(".simulation");
  await page.selectOption(".simulation select >> nth=0", "all");
  await page.click(".simulation .btn:has-text('Play')"); await page.waitForFunction(() => window.__iyicam.app.cur.sim2d, null, { timeout: 120000 });
  await page.waitForTimeout(1500);
  const t1 = await ev(() => window.__iyicam.app.cur.sim2d.t);
  ok("Play runs the tool over the sheet", t1 > 0, "t=" + t1.toFixed(2));
  await page.click(".simulation .btn:has-text('Pause')"); await page.waitForTimeout(300);
  const t2 = await ev(() => window.__iyicam.app.cur.sim2d.t); await page.waitForTimeout(400);
  ok("Pause stops it", Math.abs((await ev(() => window.__iyicam.app.cur.sim2d.t)) - t2) < 1e-9);
  await shot(page, "job_sim_playing");
  await page.click(".simulation .btn:has-text('⏭')"); await page.waitForTimeout(500);
  const total = await ev(() => window.__iyicam.app.cur.sim2d.tl.total);
  ok("the end shows everything cut", Math.abs((await ev(() => window.__iyicam.app.cur.sim2d.t)) - total) < 1e-6 && /\d+:\d\d \/ \d+:\d\d/.test(await page.textContent(".siminfo")));
  const painted = await ev(() => { const v = window.__iyicam.app.cur.sim2d, d = v.ctx.getImageData(0, 0, v.canvas.width, v.canvas.height).data; let n = 0; for (let i = 3; i < d.length; i += 4) if (d[i] > 0) n++; return n; });
  ok("something is painted", painted > 1000, String(painted));
  await shot(page, "job_sim_end");
  await page.click(".simulation .btn:has-text('⏮')"); await page.waitForTimeout(200);
  ok("the restart button goes back to the start", (await ev(() => window.__iyicam.app.cur.sim2d.t)) === 0);
  await page.click(".simulation .btn:has-text('✕') >> nth=0"); await page.waitForTimeout(200);
  ok("✕ ends the simulation", !(await ev(() => window.__iyicam.app.cur.sim2d)));

  // -- Quit: asks about the tab with changes
  await ev(() => { const d = window.__iyicam.app.cur; d.dirty = true; });
  await run("file.quit"); await page.waitForSelector(".dlg:has-text('Save the changes')");
  await page.click(".dlg .btn:has-text('Cancel')"); await page.waitForTimeout(200);
  ok("Quit asks to save and Cancel stays", (await page.locator(".quitbox").count()) === 0 && (await doc()).dirty);
  await run("file.quit"); await page.waitForSelector(".dlg:has-text('Save the changes')");
  await page.click(".dlg .btn:has-text('Discard')"); await page.waitForSelector(".quitbox", { timeout: 15000 });
  ok("Discard signs out", true);

  ok("no page errors", errors.length === 0, errors.slice(0, 3).join(" | "));
  await browser.close();
  const bad = results.filter(r => !r).length;
  console.log(bad ? `${bad} FAILED of ${results.length}` : `all ${results.length} passed`);
  process.exit(bad ? 1 : 0);
})().catch(e => { console.error(e); process.exit(1); });

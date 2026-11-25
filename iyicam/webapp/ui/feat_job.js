/* iyiCAM web: the job: backplot, job report, packages and templates, post-processors, stock of plates, plugins, Python console, keyboard shortcuts.
 * `install()` runs once the window is built: it registers the menu items it makes work (`A.register(id, {run, enabled, checked})`),
 * drawing tools (`app.extraTools[name] = {down, move, hover, up}`) and what it draws over the drawing (`app.overlays`).
 * The windows are in dialogs/job.js, the Cut Order and Simulation panels in panels_job.js; the work itself is the engine's (ops_job.py). */
import * as A from "./actions.js";
import { app } from "./state.js";
import { MENUS } from "./menudef.js";
import * as D from "./dialogs/job.js";
import { installCutOrder, installSimulation } from "./panels_job.js";

export function install() {
  const reg = A.register, doc = () => app.cur, has = () => !!app.cur;
  const hasTp = () => !!app.cur && app.cur.project.toolpaths.length > 0;
  app.dynamicMenus = app.dynamicMenus || {};

  // -- file
  reg("file.open_g_code_backplot", { run: () => D.openBackplot(doc()), enabled: has });
  reg("toolpaths.clear_g_code_backplot", { run: () => { const d = doc(); d.backplot = null; app.ui.canvas.redraw(); A.status("G-code backplot cleared"); }, enabled: () => !!(app.cur && app.cur.backplot) });
  reg("file.job_report", { run: () => D.jobReport(doc()), enabled: hasTp });
  reg("file.quit", { run: () => D.quit() });

  // -- keys
  reg("edit.keyboard_shortcuts", () => D.shortcutEditor());
  reg("help.keyboard_shortcuts", () => D.shortcutsHelp());

  // -- the Python console (a pane under the drawing)
  const con = D.consolePane();
  document.getElementById("center").append(con.el);
  for (const id of ["view.python_console", "plugins.python_console"]) reg(id, { run: () => con.toggle(), checked: () => con.visible, enabled: () => true });

  // -- packages, templates, posts
  reg("toolpaths.toolpath_packages", { run: () => D.managePackages(doc()), enabled: has });
  reg("toolpaths.save_selected_toolpaths_as_a_package", { run: () => D.savePackage(doc()), enabled: hasTp });
  reg("toolpaths.save_toolpath_template", { run: () => D.saveTemplate(doc()), enabled: hasTp });
  reg("toolpaths.load_toolpath_template", { run: () => D.loadTemplate(doc()), enabled: has });
  reg("toolpaths.post_processors", { run: () => D.postEditor(doc()), enabled: has });
  app.dynamicMenus["toolpaths.use_a_package"] = () => {
    const list = D.packages();
    return [...(list.length ? list.map(p => { const id = "usepkg." + p.id; A.registry.set(id, { run: () => D.applyPackage(doc(), p.id), enabled: has }); return { id, text: p.name + "..." }; }) : [{ id: "usepkg.none", text: "(no packages yet)" }]),
      "-", { id: "toolpaths.toolpath_packages", text: "Toolpath Packages..." }];
  };
  D.loadPackages();

  // -- stock of plates
  reg("nesting.stock_of_plates", { run: () => D.stockDialog(doc()), enabled: has });
  reg("nesting.add_selected_vectors_to_stock", { run: () => D.stockFromSelection(doc()), enabled: () => !!app.cur && app.cur.selection.length > 0 });
  reg("nesting.add_leftover_of_current_sheet_to_stock", { run: () => D.leftoverToStock(doc()), enabled: has });

  // -- plugins, help
  reg("plugins.plugin_manager", () => D.pluginManager());
  reg("help.g_code_words_i_j_k", () => D.gcodeWords());
  // the commands plugins bring go into the Plugins menu (as submenus by their menu path)
  app.dynamicMenus["top.plugins"] = () => {
    const known = new Set();
    const walk = items => { for (const it of items) { if (it === "-") continue; if (it.cmd) known.add(it.cmd); if (it.menu) walk(it.menu); } };
    for (const m of MENUS) walk(m.menu);
    const root = { menu: [] };
    for (const c of app.schema.commands || []) {
      if (known.has(c.id) || !c.menu || /^(Edit|Vectors)(\/|$)/.test(c.menu)) continue;
      const path = c.menu.split("/").filter(Boolean);
      if (path[0] === "Plugins") path.shift();
      let node = root;
      for (const seg of path) { let sub = node.menu.find(x => x.menu && x.text === seg); if (!sub) { sub = { id: "plugmenu." + c.menu + seg, text: seg, menu: [] }; node.menu.push(sub); } node = sub; }
      const id = "cmd." + c.id;
      A.registry.set(id, { run: () => A.runCommand(c.id), enabled: () => !!app.cur && app.cur.selection.length >= c.min_selection });
      node.menu.push({ id, text: c.label, sc: c.shortcut || undefined });
    }
    return root.menu.length ? ["-", ...root.menu] : [];
  };

  // -- the drawing: a backplot under the toolpaths' end, the cut order preview, the simulation
  app.overlays.push((ctx, view, phase) => {
    const d = app.cur, b = d && d.backplot;
    if (phase !== "world" || !b || b.sheet !== d.sheetId) return;
    let c = b._c;
    if (!c) {
      const feed = new Path2D(), rapid = new Path2D(), rp = b.preview.rapids || [];
      for (const cutp of b.preview.cuts) for (let k = 0; k + 1 < cutp.p.length; k += 2) (k ? feed.lineTo(cutp.p[k], cutp.p[k + 1]) : feed.moveTo(cutp.p[k], cutp.p[k + 1]));
      for (let k = 0; k + 3 < rp.length; k += 4) { rapid.moveTo(rp[k], rp[k + 1]); rapid.lineTo(rp[k + 2], rp[k + 3]); }
      c = { feed, rapid };
      Object.defineProperty(b, "_c", { value: c, enumerable: false });
    }
    const s = view.view.s;
    ctx.save(); ctx.lineJoin = "round"; ctx.lineCap = "round";
    ctx.strokeStyle = "rgba(126,63,242,0.95)"; ctx.lineWidth = 1.4 / s; ctx.stroke(c.feed);
    ctx.strokeStyle = "rgba(126,63,242,0.45)"; ctx.lineWidth = 0.8 / s; ctx.setLineDash([5 / s, 4 / s]); ctx.stroke(c.rapid); ctx.setLineDash([]);
    ctx.fillStyle = "rgba(126,63,242,0.95)";
    for (const dr of b.preview.drills || []) { ctx.beginPath(); ctx.arc(dr[0], dr[1], 3 / s, 0, 7); ctx.fill(); }
    ctx.restore();
  });
  installCutOrder();
  installSimulation();
}

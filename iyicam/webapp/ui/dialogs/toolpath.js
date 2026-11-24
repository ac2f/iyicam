/* iyiCAM web: the toolpath window of the desktop (ToolpathDialog): a name, a preset, and the tabs Settings / Tool / Vectors.
 * On a wide screen it sits beside the drawing (modeless) so that vectors can be selected while it is open and the toolpath
 * follows the selection; on a phone it fills the screen. */
import { h, fill, uid, deepCopy, store, toast } from "../dom.js";
import { dialog, tabs, alertBox, promptBox } from "../dialog.js";
import { ParamForm } from "../forms.js";
import { app, on, units } from "../state.js";
import { run, report } from "../engine.js";
import { ToolEditor, toolDatabase, describeTool } from "./tools.js";
import { materialSetup } from "./misc.js";
import { icon } from "../icons.js";

const VARIABLES = [["tool_name", "Name of the tool"], ["tool_number", "Number of the tool"], ["tool_diameter", "Diameter of the tool"], ["feed_rate", "Feed rate (mm/min)"],
  ["plunge_rate", "Plunge rate (mm/min)"], ["spindle_speed", "Spindle speed (rpm)"], ["stepdown", "Stepdown"], ["cut_depth", "Cut depth"], ["allowance", "Allowance"],
  ["material_thickness", "Thickness of the material"], ["safe_z", "Safe Z"], ["operation", "Kind of toolpath"], ["group", "Group"], ["sheet_group", "Sheet group"]];

const libraryTool = (op, id) => {
  const t = (app.tools || []).find(x => x.id === id);
  return t && (!op.tool_types || op.tool_types.includes(t.type)) ? deepCopy(t) : null;
};
const defaultTool = op => deepCopy((app.tools || []).find(t => !op.tool_types || op.tool_types.includes(t.type)) || (app.tools || [])[0] || { name: "Tool", type: "end_mill", diameter: 6, number: 1, flutes: 2, stepdown: 3, stepover: 2.4, feed_rate: 3000, plunge_rate: 1000, spindle_speed: 18000, spindle_dir: "cw", coolant: "off", group: "", notes: "", id: uid(), angle: 90, tip_diameter: 0, corner_radius: 0, flute_length: 22 });

/** Resolves `{tp, keepOld}` when Calculate is pressed, null when cancelled.  `existing`: the toolpath that is edited. */
export async function toolpathDialog(doc, opId, existing, opts = {}) {
  const op = app.schema.operations.find(o => o.id === opId);
  const proj = doc.project, mat = proj.material;
  const memKey = "iyicam.tp." + opId;
  const mem = existing ? {} : store.get(memKey, {});
  const n = proj.toolpaths.filter(t => t.op === opId).length + 1;
  const name = h("input", { type: "text", value: existing ? existing.name : `${op.label.split(" (")[0]} ${n}` });
  // -- heights
  const own0 = existing && (existing.safe_z !== null && existing.safe_z !== undefined || existing.clearance !== null && existing.clearance !== undefined);
  const ownBox = h("input", { type: "checkbox", checked: !!own0 });
  const heightsParams = [
    { name: "safe_z", label: "Safe Z", kind: "length", default: mat.safe_z, min: 0.1, group: "", help: "The tool retracts to this height and travels at it between cuts" },
    { name: "clearance", label: "Rapid-down clearance", kind: "length", default: mat.clearance, min: 0, group: "", help: "The tool rapids down to this height above the material and then plunges" },
  ].map(p => ({ max: null, step: null, choices: null, visible: null, decimals: 3, advanced: false, ...p }));
  const hv = { safe_z: existing && existing.safe_z != null ? existing.safe_z : mat.safe_z, clearance: existing && existing.clearance != null ? existing.clearance : mat.clearance };
  const heights = new ParamForm(heightsParams, hv, {});
  const note = h("div", { class: "heights-note" });
  const syncHeights = () => {
    heights.setDisabled(["safe_z", "clearance"], !ownBox.checked);
    if (!ownBox.checked) heights.set({ safe_z: doc.project.material.safe_z, clearance: doc.project.material.clearance });
    fill(note, "Project: safe Z ", h("b", null, units.fmt(doc.project.material.safe_z)), ", rapid-down clearance ", h("b", null, units.fmt(doc.project.material.clearance)));
  };
  ownBox.addEventListener("change", syncHeights);
  // -- settings
  const params = op.params.map(p => (p.name === "cut_depth" && !existing && opId === "profile" ? { ...p, default: mat.thickness } : p));
  const form = new ParamForm(params, existing ? existing.params : {}, { collapse: true, tools: app.tools });
  const heightsBox = h("fieldset", { class: "group" }, h("legend", null, "Heights above the material surface"),
    h("div", { class: "fields" },
      h("div", { class: "row hnote" }, note, h("button", { type: "button", class: "btn", onclick: async () => { await materialSetup(doc); syncHeights(); } }, "Material Setup...")),
      h("label", { class: "row check" }, ownBox, h("span", null, "This toolpath has heights of its own")),
      heights.el));
  heights.el.querySelectorAll("fieldset").forEach(f => { f.classList.add("bare"); const lg = f.querySelector("legend"); lg && lg.remove(); });
  // -- tool
  let tool = existing ? deepCopy(existing.tool) : opts.tool ? deepCopy(opts.tool) : libraryTool(op, mem.tool_id) || defaultTool(op);
  const toolLabel = h("div", { class: "toollabel" });
  const editor = new ToolEditor(tool, { lockSize: true, groups: null, onChange: t => { tool = t; showToolLabel(); } });
  const showToolLabel = () => {
    const warn = op.tool_types && !op.tool_types.includes(tool.type) ? "  ⚠ not recommended for this toolpath" : "";
    fill(toolLabel, h("b", null, describeTool(tool)), warn);
  };
  showToolLabel();
  const toolTab = h("div", { class: "tooltab" }, h("div", { class: "row" }, toolLabel, h("button", { type: "button", class: "btn", onclick: async () => {
    const t = await toolDatabase({ pick: true, types: op.tool_types, selectedId: tool.id });
    if (t) { tool = t; editor.set(t); showToolLabel(); }
  } }, "Select from database...")), editor.el);
  // -- vectors
  const sel = existing ? existing.selection : null;
  let vectorIds = doc.selection.length ? doc.selection.slice() : (existing && sel && sel.mode === "vectors" ? sel.vectors.slice() : []);
  const rbSel = h("input", { type: "radio", name: "vsel" }), rbLayers = h("input", { type: "radio", name: "vsel" });
  const selText = h("span");
  const layerChecks = proj.layers.map(l => ({ l, el: h("input", { type: "checkbox", checked: !!(sel && sel.layers && sel.layers.includes(l.id)) }) }));
  const allSheets = h("input", { type: "checkbox", checked: existing ? existing.sheets == null : mem.all_sheets !== false });
  const groups = [...new Set(proj.sheets.map(s => s.group).filter(Boolean))];
  const want = existing ? existing.sheet_group || "" : "";
  if (want && !groups.includes(want)) groups.push(want);
  const groupSel = h("select", null, h("option", { value: "" }, "(not by group)"), groups.map(g => h("option", { value: g }, `Only on the sheets of the group: ${g}`)));
  groupSel.value = want;
  groupSel.addEventListener("change", () => { allSheets.disabled = !!groupSel.value; });
  allSheets.disabled = !!groupSel.value;
  const follow = h("input", { type: "checkbox", checked: !existing });
  const setSelText = () => fill(selText, `Selected vectors (${vectorIds.length})`);
  setSelText();
  (sel && sel.mode === "layers" ? rbLayers : rbSel).checked = true;
  if (!vectorIds.length && !(sel && sel.mode === "layers")) { rbLayers.checked = true; const c = layerChecks.find(x => x.l.id === doc.layerId); if (c) c.el.checked = true; }
  const unFollow = on("selection", () => {
    if (!follow.checked || doc !== app.cur || !doc.selection.length) return;
    if (doc.selection.join() === vectorIds.join()) return;
    vectorIds = doc.selection.slice(); setSelText(); rbSel.checked = true;
  });
  const vecTab = h("div", { class: "vectab" },
    h("label", { class: "row check" }, rbSel, selText),
    h("label", { class: "row check" }, rbLayers, h("span", null, "Vectors on these layers (on every sheet):")),
    h("div", { class: "checklist" }, layerChecks.map(c => h("label", { class: "row check" }, c.el, h("span", { class: "sw", style: { background: c.l.color } }), h("span", null, c.l.name)))),
    h("label", { class: "row check" }, allSheets, h("span", null, "Calculate on every sheet")),
    h("div", { class: "row" }, groupSel),
    h("label", { class: "row check" }, follow, h("span", null, "Follow the selection on the drawing while this window is open")));
  // -- presets
  const presetSel = h("select", { class: "grow", style: { minWidth: "110px" } });
  let presets = [];
  const fillPresets = async () => {
    try { presets = (await app.engine.call("tp_presets", {})).presets || []; } catch { presets = []; }
    fill(presetSel, h("option", { value: "" }, "(no preset)"), presets.map(p => h("option", { value: p.id }, p.name + (!p.op || p.op === opId ? "" : `   [${p.op}]`))));
  };
  fillPresets();
  const current = () => {
    const own = ownBox.checked, hv2 = heights.values();
    const sheetsAll = groupSel.value || allSheets.checked;
    let sheets = null;
    if (!sheetsAll) {
      sheets = existing && existing.sheets && allSheets.checked === (existing.sheets == null) ? existing.sheets.slice() : [doc.sheetId];
      if (rbSel.checked && vectorIds.length) { const ids = new Set(vectorIds); const holding = proj.sheets.filter(s => s.vectors.some(v => ids.has(v.id))).map(s => s.id); if (holding.length && !holding.includes(doc.sheetId)) sheets = holding; }
    }
    return {
      id: existing ? existing.id : uid(), name: name.value, op: opId, tool: deepCopy(tool), params: form.values(), enabled: existing ? existing.enabled : true, sheets,
      selection: rbLayers.checked ? { mode: "layers", vectors: [], layers: layerChecks.filter(c => c.el.checked).map(c => c.l.id) } : { mode: "vectors", vectors: vectorIds.slice(), layers: [] },
      sequence: existing ? deepCopy(existing.sequence || {}) : { smart_starts: true },
      safe_z: own ? hv2.safe_z : null, clearance: own ? hv2.clearance : null,
      group: existing ? existing.group || "" : "", sheet_group: groupSel.value || "",
    };
  };
  const applyPreset = async () => {
    if (!presetSel.value) return;
    try {
      const r = await run("tp_preset_apply", { project: proj, id: presetSel.value, toolpath: current(), sheet: doc.sheetId }, { label: "Preset" });
      const t = r.toolpath;
      name.value = t.name; form.set(t.params); tool = t.tool; editor.set(tool); showToolLabel();
      ownBox.checked = t.safe_z != null || t.clearance != null; heights.set({ safe_z: t.safe_z ?? mat.safe_z, clearance: t.clearance ?? mat.clearance }); syncHeights();
      if (t.sheet_group !== undefined) groupSel.value = t.sheet_group || "";
      if (r.notes && r.notes.length) alertBox(r.notes.join("\n"), "Preset");
    } catch (e) { report(e, "Preset"); }
  };
  const savePreset = async () => {
    try {
      const info = await app.engine.call("tp_presets", { project: proj, toolpath: current() });
      const rows = info.fields.map(f => ({ f, on: h("input", { type: "checkbox", checked: true }), expr: h("input", { type: "text", placeholder: f.number ? "sum, e.g. material_thickness + 0.2" : "", disabled: !f.number }) }));
      const pname = h("input", { type: "text", value: presetSel.value ? presetSel.selectedOptions[0].textContent : name.value });
      const d = dialog({ title: "Save as preset", width: 560, body: h("div", null, h("label", { class: "row" }, h("span", { class: "lab" }, "Name"), pname),
        h("div", { class: "scrollform tall" }, h("table", { class: "kv" }, h("tr", null, h("th", null, "Keep"), h("th", null, "Setting"), h("th", null, "Value"), h("th", null, "Sum")),
          rows.map(r => h("tr", null, h("td", null, r.on), h("td", null, r.f.label), h("td", { class: "mono" }, typeof r.f.value === "object" && r.f.value ? "…" : String(r.f.value)), h("td", null, r.expr)))))),
        buttons: [{ label: "Cancel", value: null }, { label: "OK", primary: true, value: "ok" }] });
      if ((await d.done) !== "ok") return;
      const fields = {};
      for (const r of rows) if (r.on.checked) fields[r.f.key] = r.expr.value.trim() && r.f.number ? { expr: r.expr.value.trim() } : { value: r.f.value };
      await run("tp_preset_save", { preset: { name: pname.value, op: opId, fields } }, { label: "Preset" });
      await fillPresets();
    } catch (e) { report(e, "Preset"); }
  };
  const delPreset = async () => {
    if (!presetSel.value) return;
    try { await run("tp_preset_delete", { id: presetSel.value }, { label: "Preset" }); await fillPresets(); } catch (e) { report(e, "Preset"); }
  };
  // -- the window
  const varBtn = h("button", { type: "button", class: "btn small", title: "Put a variable of the toolpath in the name (the speed, the tool, the depth ...)", onclick: e => {
    const m = h("div", { class: "popmenu" }, VARIABLES.map(([v, t]) => h("div", { class: "item", onclick: () => { name.setRangeText("{" + v + "}", name.selectionStart ?? name.value.length, name.selectionEnd ?? name.value.length, "end"); m.remove(); name.focus(); } }, `{${v}}  ${t}`)));
    const r = varBtn.getBoundingClientRect(); Object.assign(m.style, { left: r.left + "px", top: r.bottom + "px" });
    document.body.append(m);
    setTimeout(() => document.addEventListener("pointerdown", () => m.remove(), { once: true }), 0);
    void e;
  } }, "{…}");
  const keepOld = h("input", { type: "checkbox" });
  const top = h("div", { class: "tp-top" },
    op.description ? h("p", { class: "dlg-text" }, op.description) : null,
    h("div", { class: "row" }, h("span", { class: "lab" }, "Name"), h("span", { class: "grow flex" }, name, varBtn)),
    h("div", { class: "row" }, h("span", { class: "lab" }, "Preset"), h("span", { class: "grow flex" }, presetSel,
      h("button", { type: "button", class: "btn", onclick: applyPreset }, "Apply"), h("button", { type: "button", class: "btn", onclick: savePreset }, "Save as preset..."), h("button", { type: "button", class: "btn", onclick: delPreset }, "Delete"))),
    existing ? h("label", { class: "row check" }, keepOld, h("span", null, "Keep the original: save this as a new toolpath")) : null);
  const tb = tabs([["Settings", h("div", { class: "scrollform" }, heightsBox, form.el)], ["Tool", h("div", { class: "scrollform" }, toolTab)], ["Vectors", h("div", { class: "scrollform" }, vecTab)]]);
  const narrow = matchMedia("(max-width: 820px)").matches;
  const warn = h("div", { class: "dlg-warn", hidden: true });
  const dlg = dialog({ title: `${op.label} Toolpath`, body: h("div", { class: "tpdlg" }, top, tb.el, warn), width: 480, modeless: !narrow, className: "tpwin",
    buttons: [{ label: "Calculate", primary: true, value: "ok", onclick: () => {
      if (rbSel.checked && !vectorIds.length) { warn.textContent = "Select vectors first, or choose layers on the Vectors tab."; warn.hidden = false; tb.show(2); return false; }
      if (rbLayers.checked && !layerChecks.some(c => c.el.checked)) { warn.textContent = "Tick at least one layer."; warn.hidden = false; tb.show(2); return false; }
    } }, { label: "Cancel", value: null }],
    onClose: () => unFollow() });
  syncHeights();
  const r = await dlg.done;
  if (r !== "ok") return null;
  const tp = current();
  if (!existing) store.set(memKey, { tool_id: tool.id, all_sheets: allSheets.checked });
  void toast; void promptBox; void icon;
  return { tp, keepOld: keepOld.checked };
}

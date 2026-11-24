/* iyiCAM web: the tool database window and the tool editor (as the desktop's ToolDatabaseDialog and ToolEditor).
 * The database lives in the user folder of the engine; it is changed here and written back (`tools_save`, or this browser
 * when the engine has no such operation yet). */
import { h, fill, uid, deepCopy, store, toast } from "../dom.js";
import { dialog, confirmBox, promptBox, alertBox } from "../dialog.js";
import { ParamForm } from "../forms.js";
import { app } from "../state.js";
import { run } from "../engine.js";
import { download, pickDeviceFiles, textBytes } from "../files.js";

const TAPERED = ["v_bit", "engraving"];
const P = (name, label, kind, def, o = {}) => ({ name, label, kind, default: def, min: null, max: null, step: null, choices: null, group: "", help: "", visible: null, decimals: 3, advanced: false, ...o });

/** the params of a tool, as dialogs.tool_params of the desktop */
export function toolParams(t, groups) {
  const types = app.schema.tool_types;
  const names = groups ? [...new Set([...groups, t.group || ""])] : null;
  return [
    P("name", "Name", "str", t.name, { group: "Tool", help: "It may have {variables} of the tool: {type} Ø{diameter}" }),
    P("type", "Type", "choice", t.type, { choices: types, group: "Tool" }),
    P("number", "Tool number", "int", t.number, { min: 0, max: 999, group: "Tool" }),
    names ? P("group", "Group", "choice", t.group || "", { choices: names.map(g => [g, g || "(no group)"]), group: "Tool" }) : P("group", "Group", "str", t.group || "", { group: "Tool" }),
    P("diameter", "Diameter", "length", t.diameter, { min: 0.01, group: "Geometry" }),
    P("angle", "Included angle", "angle", t.angle, { min: 1, max: 179, group: "Geometry", visible: { type: [...TAPERED, "drill"] } }),
    P("tip_diameter", "Tip (flat) diameter", "length", t.tip_diameter, { min: 0, group: "Geometry", visible: { type: TAPERED } }),
    P("corner_radius", "Corner radius", "length", t.corner_radius, { min: 0, group: "Geometry", visible: { type: "bull_nose" } }),
    P("flute_length", "Flute length", "length", t.flute_length, { min: 0, group: "Geometry" }),
    P("flutes", "Flutes", "int", t.flutes, { min: 1, max: 12, group: "Geometry" }),
    P("stepdown", "Stepdown", "length", t.stepdown, { min: 0.01, group: "Cutting" }),
    P("stepover", "Stepover", "length", t.stepover, { min: 0.001, group: "Cutting" }),
    P("feed_rate", "Feed rate", "feed", t.feed_rate, { min: 1, group: "Cutting" }),
    P("plunge_rate", "Plunge rate", "feed", t.plunge_rate, { min: 1, group: "Cutting" }),
    P("spindle_speed", "Spindle speed (rpm)", "int", t.spindle_speed, { min: 0, max: 100000, group: "Cutting" }),
    P("spindle_dir", "Spindle direction", "choice", t.spindle_dir, { choices: [["cw", "Clockwise (M3)"], ["ccw", "Counter-clockwise (M4)"]], group: "Cutting" }),
    P("coolant", "Coolant", "choice", t.coolant, { choices: [["off", "Off"], ["mist", "Mist (M7)"], ["flood", "Flood (M8)"]], group: "Cutting" }),
    P("notes", "Notes", "text", t.notes || "", { group: "Notes" }),
  ];
}

export const blankTool = () => ({ name: "New tool", type: "end_mill", diameter: 6, number: 1, angle: 90, tip_diameter: 0, corner_radius: 0, flute_length: 22, flutes: 2,
  stepdown: 3, stepover: 2.4, feed_rate: 3000, plunge_rate: 1000, spindle_speed: 18000, spindle_dir: "cw", coolant: "off", group: "", notes: "", id: uid() });

export const describeTool = t => `T${t.number}  ${t.name}`;

/** the tool editor: a form of a tool; `lockSize` switches the geometry off (the toolpath window picks another tool instead) */
export class ToolEditor {
  constructor(tool, { lockSize = false, groups = null, onChange } = {}) {
    this.base = deepCopy(tool);
    this.lockSize = lockSize; this.groups = groups; this.onChange = onChange;
    this.el = h("div", { class: "tooleditor" });
    this.set(tool);
  }
  set(tool) {
    this.base = deepCopy(tool);
    this.form = new ParamForm(toolParams(this.base, this.groups), this.base, { onChange: () => this.onChange && this.onChange(this.tool()) });
    if (this.lockSize) this.form.setDisabled(["type", "diameter", "angle", "tip_diameter", "corner_radius", "flute_length", "flutes"]);
    fill(this.el, this.form.el);
  }
  tool() { return { ...this.base, ...this.form.values() }; }
}

/** Save the database: into the user folder of the engine; a browser copy when the engine cannot (yet). */
export async function saveTools(tools) {
  app.tools = tools;
  try { await run("tools_save", { tools }, { label: "Saving the tools" }); store.set("iyicam.tools", null); } catch (e) {
    store.set("iyicam.tools", tools);
    if (e && e.code === "unknown_operation") toast("The engine cannot keep the tools yet: they are kept in this browser.");
    else toast("The tools are kept in this browser only: " + (e.message || e), "error");
  }
}

/** The tool database window.  `pick`: a tool is chosen (the button says Select) and resolves a copy of it; otherwise it
 *  resolves true when the database was saved. */
export async function toolDatabase({ pick = false, types = null, selectedId = null } = {}) {
  let tools = deepCopy(app.tools || []);
  const extraGroups = new Set(store.get("iyicam.toolgroups", []));
  let cur = tools.find(t => t.id === selectedId) || tools[0] || null;
  const tree = h("div", { class: "tooltree" });
  const editorHost = h("div", { class: "toolform" });
  let editor = null;
  const groups = () => [...new Set([...tools.map(t => t.group || ""), ...extraGroups])].sort((a, b) => (a === "" ? 1 : b === "" ? -1 : 0));
  const folded = new Set();
  function commit() { if (editor && cur) { const t = editor.tool(); Object.assign(cur, t); } }
  function select(t) { commit(); cur = t; showEditor(); renderTree(); }
  function showEditor() {
    if (!cur) { fill(editorHost, h("p", { class: "muted" }, "No tool.")); editor = null; return; }
    editor = new ToolEditor(cur, { groups: groups(), onChange: () => {} });
    fill(editorHost, editor.el);
  }
  function renderTree() {
    fill(tree, groups().map(g => {
      const mine = tools.filter(t => (t.group || "") === g);
      const open = !folded.has(g);
      return h("div", { class: "tgroup" },
        h("div", { class: "tg-head", onclick: () => { open ? folded.add(g) : folded.delete(g); renderTree(); } }, h("span", { class: "tri" }, open ? "▾" : "▸"), h("b", null, g || "(no group)")),
        open ? mine.map(t => h("div", { class: "tg-row" + (t === cur ? " on" : "") + (types && !types.includes(t.type) ? " dim" : ""), onclick: () => select(t), ondblclick: () => pick && ok.click() }, describeTool(t))) : null);
    }));
  }
  const btn = (label, fn, title) => h("button", { type: "button", class: "btn", title: title || "", onclick: fn }, label);
  const left = h("div", { class: "tl" }, tree,
    h("div", { class: "btnrow" },
      btn("Add", () => { commit(); const t = { ...blankTool(), group: cur ? cur.group || "" : "", number: Math.max(0, ...tools.map(x => x.number)) + 1 }; tools.push(t); cur = t; showEditor(); renderTree(); }),
      btn("Copy", () => { if (!cur) return; commit(); const t = { ...deepCopy(cur), id: uid(), name: cur.name + " copy", number: Math.max(0, ...tools.map(x => x.number)) + 1 }; tools.push(t); cur = t; showEditor(); renderTree(); }),
      btn("Delete", async () => { if (!cur || !(await confirmBox(`Delete the tool "${cur.name}"?`, { yes: "Delete" }))) return; tools = tools.filter(t => t !== cur); cur = tools[0] || null; showEditor(); renderTree(); }),
      h("button", { type: "button", class: "btn", disabled: true, title: "Not on the web yet" }, "Feeds..."),
      btn("Import...", async () => {
        const f = (await pickDeviceFiles(".json,application/json"))[0];
        if (!f) return;
        try { const d = JSON.parse(new TextDecoder().decode(f.bytes)); const list = Array.isArray(d) ? d : d.tools; for (const t of list) tools.push({ ...blankTool(), ...t, id: uid() }); renderTree(); toast(`${list.length} tool(s) imported`); }
        catch (e) { alertBox("Not a tool file: " + e.message); }
      }),
      btn("Export...", () => { commit(); download("tools.json", textBytes(JSON.stringify({ tools }, null, 1)), "application/json"); })),
    h("div", { class: "btnrow" },
      btn("New group...", async () => { const n = await promptBox("Group name"); if (n && n.trim()) { extraGroups.add(n.trim()); store.set("iyicam.toolgroups", [...extraGroups]); renderTree(); } }),
      btn("Rename group...", async () => {
        const g = cur ? cur.group || "" : ""; const n = await promptBox("New name of the group", g);
        if (n === null || !n.trim()) return;
        commit(); for (const t of tools) if ((t.group || "") === g) t.group = n.trim(); extraGroups.delete(g); extraGroups.add(n.trim()); showEditor(); renderTree();
      }),
      btn("Delete group", async () => {
        const g = cur ? cur.group || "" : ""; if (!g) return;
        if (!(await confirmBox(`Delete the group "${g}"? Its tools stay, without a group.`, { yes: "Delete" }))) return;
        commit(); for (const t of tools) if ((t.group || "") === g) t.group = ""; extraGroups.delete(g); showEditor(); renderTree();
      })));
  const body = h("div", { class: "tooldb" }, left, editorHost);
  let result = null;
  const ok = h("span");   // (placeholder for dblclick before the dialog exists)
  const dlg = dialog({ title: pick ? "Select tool" : "Tool Database", body, width: 780, className: "tooldlg", buttons: [
    { label: "Cancel", value: null },
    { label: pick ? "Select" : "Save", primary: true, value: "ok", onclick: async () => {
      commit();
      if (pick) { result = cur ? deepCopy(cur) : null; if (!result) return false; return; }
      await saveTools(tools);
    } }] });
  ok.click = () => dlg.buttons.find(b => b.primary).el.click();
  showEditor(); renderTree();
  const r = await dlg.done;
  if (r !== "ok") return null;
  return pick ? result : true;
}

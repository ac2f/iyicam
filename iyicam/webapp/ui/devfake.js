/* TEMPORARY: stands in for engine/engine.js while the real engine is not served (a plain static server with
 * docs/webapp/fixtures at /fixtures).  Delete this file and the `fake` branch of engine.js when the real engine works. */
import G from "./geom.js";
import { blankProject, } from "./state.js";

const sleep = ms => new Promise(r => setTimeout(r, ms));
const getJson = async name => (await fetch("/fixtures/" + name)).json();

export class FakeEngine {
  static async start({ onStatus } = {}) {
    onStatus && onStatus("Development stand-in for the engine (docs/webapp/fixtures)");
    const e = new FakeEngine();
    e.version = "devfake";
    e.ready = Promise.resolve();
    e.fixtures = {};
    return e;
  }
  async fixture(n) { return this.fixtures[n] || (this.fixtures[n] = await getJson(n)); }

  async call(op, payload = {}, opts = {}) {
    await sleep(120);
    const fn = this["op_" + op];
    if (!fn) throw Object.assign(new Error(`devfake: the operation '${op}' is not faked`), { code: "unsupported", details: "" });
    return fn.call(this, payload, opts);
  }
  async op_schema() { return this.fixture("schema.json"); }
  async op_new_project(p) { return { project: blankProject(p.width || 1200, p.height || 800, p.thickness || 18, p.name || "Untitled") }; }
  async op_import(p) {
    const name = String(p.name || "");
    if (name.toLowerCase().endsWith(".icam")) return { project: JSON.parse(atob(p.data_b64)), report: { summary: "", clean: true }, added: [] };
    throw Object.assign(new Error("devfake cannot read " + name), { code: "unsupported_file" });
  }
  async op_export(p) { return { files: [{ name: (p.project.name || "job") + ".icam", mime: "application/json", data_b64: btoa(JSON.stringify(p.project)) }] }; }
  async op_tp_presets() { return { presets: [], variables: [] }; }
  async op_tools_save() { return { ok: true }; }
  async op_posts() { return { posts: [] }; }

  async op_compute(p, opts) {
    const fx = await this.fixture("compute_result.json");
    const ids = new Set(p.project.toolpaths.map(t => t.id));
    const known = fx.results.filter(r => ids.has(r.toolpath));
    const results = known.slice();
    for (const t of p.project.toolpaths) {                          // (a toolpath of the person's own: a line along each vector)
      if (known.some(r => r.toolpath === t.id)) continue;
      const sheet = p.project.sheets[0];
      const vs = sheet.vectors.filter(v => t.selection.mode === "layers" ? t.selection.layers.includes(v.layer) : t.selection.vectors.includes(v.id));
      const cuts = vs.flatMap(v => G.flatOf(v).map(c => ({ z: -6, p: c.pts.flat() })));
      results.push({ toolpath: t.id, sheet: sheet.id, name: t.name, op: t.op, enabled: t.enabled, error: null, warnings: [],
        stats: { time_s: 60, feed_length: 1000, rapid_length: 100, zmin: -6, bbox: [0, 0, 1, 1] }, order: vs.map(v => v.id + ":0"),
        preview: { cuts, rapids: [], drills: [], points: 0, step: 1, truncated: false } });
    }
    opts && opts.onProgress && opts.onProgress(1, 1, "devfake");
    const totals = {};
    for (const s of p.project.sheets) totals[s.id] = results.filter(r => r.sheet === s.id).reduce((a, r) => a + r.stats.time_s, 0);
    return { results, patch: { vectors: {}, sequences: {} }, totals, seconds: 0.1 };
  }
  async op_gcode(p) {
    return { files: [{ name: (p.project.name || "job") + "_1." + (p.post === "linuxcnc" ? "ngc" : "nc"), mime: "text/plain", text: "(devfake: no G-code without the engine)\nM30\n", sheet: p.project.sheets[0].id }], errors: [], warnings: [], ok: true, reused: false };
  }
  async op_command(p) {
    const sheet = p.project.sheets.find(s => s.id === p.sheet) || p.project.sheets[0];
    const sel = new Set(p.selection || []);
    const v = p.values || {};
    if (p.command === "xform.move") {
      const next = sheet.vectors.map(x => (sel.has(x.id) ? G.translate(x, v.dx || 0, v.dy || 0) : x));
      sheet.vectors = next;
      return { ok: true, project: p.project, selection: [...sel], messages: [], changed: true, result: null };
    }
    if (p.command === "create.rectangle") {
      const w = v.width || 100, hh = v.height || 60, cx = v.x ?? sheet.width / 2, cy = v.y ?? sheet.height / 2;
      const nv = G.vectorOf([G.rectangle(cx - w / 2, cy - hh / 2, cx + w / 2, cy + hh / 2)], p.active_layer || p.project.layers[0].id);
      sheet.vectors.push(nv);
      return { ok: true, project: p.project, selection: [nv.id], messages: [], changed: true, result: null };
    }
    throw Object.assign(new Error(`devfake: the command ${p.command} needs the real engine`), { code: "command_failed", details: "" });
  }
}

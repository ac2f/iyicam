// Drives the real engine in Chromium (Playwright) against a running `iyicam serve` and prints what it got as JSON, for
// test_engine_parity.py to compare with the native engine.
//   NODE_PATH=$(npm root -g) node engine_parity.js BASE_URL KEY PROJECT.json OUT.json
const { chromium } = require("playwright");
const fs = require("fs");

const [base, key, projectFile, outFile] = process.argv.slice(2);

(async () => {
  // behind a proxy (CI of this project) the page's CDN and PyPI requests need it; the app itself is on localhost
  const proxy = process.env.HTTPS_PROXY ? { server: process.env.HTTPS_PROXY, bypass: "localhost,127.0.0.1" } : undefined;
  const browser = await chromium.launch({ proxy });
  const ctx = await browser.newContext({ ignoreHTTPSErrors: !!proxy });
  const page = await ctx.newPage();
  const problems = [];
  page.on("pageerror", e => problems.push("pageerror: " + e.message));
  page.on("console", m => { if (m.type() === "error") problems.push("console.error: " + m.text()); });
  await page.goto(base + "/app/config.json");           // (any page of the app's origin: it carries the isolation headers)

  const project = JSON.parse(fs.readFileSync(projectFile, "utf8"));
  const svg = Buffer.from('<svg xmlns="http://www.w3.org/2000/svg" width="100" height="50"><rect x="5" y="5" width="40" height="30" fill="none" stroke="black"/><circle cx="70" cy="25" r="15" fill="none" stroke="black"/></svg>').toString("base64");

  const out = await page.evaluate(async ({ project, svg, key }) => {
    const { Engine, EngineError } = await import("/app/engine/engine.js");
    const { Files } = await import("/app/engine/files.js");
    const r = { isolated: crossOriginIsolated, status: [] };
    const t0 = performance.now();
    const engine = await Engine.start({ onStatus: (t, f) => r.status.push([t, f]) });
    r.load_ms = Math.round(performance.now() - t0);
    r.version = engine.version; r.cancel_mode = engine.cancelMode;
    r.schema = await engine.call("schema", {});
    r.imported = await engine.call("import", { name: "a.svg", data_b64: svg });
    const progress = [];
    r.compute = await engine.call("compute", { project }, { onProgress: (k, n, l) => progress.push([k, n, l]) });
    r.progress = progress.length;
    r.gcode = await engine.call("gcode", { project, post: r.schema.posts[0].id });
    // errors come as EngineError with the code of the ServiceError
    r.errors = {};
    for (const [name, op, payload] of [["bad", "validate", {}], ["unknown", "nope", {}]]) {
      try { await engine.call(op, payload); r.errors[name] = "no error"; }
      catch (e) { r.errors[name] = e instanceof EngineError ? e.code : String(e); }
    }
    // cancelling: aborts at the first progress report
    const ac = new AbortController();
    const t1 = performance.now();
    try { await engine.call("compute", { project }, { signal: ac.signal, onProgress: () => ac.abort() }); r.cancel = "finished"; }
    catch (e) { r.cancel = e.code; }
    r.cancel_ms = Math.round(performance.now() - t1);
    r.after_cancel = (await engine.call("ping", {})).ok;
    // the user folder is kept: a preset written now is there after a restart of the engine
    await engine.call("tp_preset_save", { preset: { name: "parity", fields: { safe_z: { value: 7 } } } });
    engine.close();
    const e2 = await Engine.start({});
    r.presets_after_restart = (await e2.call("tp_presets", {})).presets.map(p => p.name);
    r.persistent = e2.persistent;
    await e2.call("tp_preset_delete", { id: (await e2.call("tp_presets", {})).presets[0].id });
    e2.close();
    // the files API
    r.files = {};
    r.files.before = (await Files.status()).signed_in;
    try { await Files.list(""); r.files.unsigned = "no error"; } catch (e) { r.files.unsigned = e.code; }
    await Files.login(key);
    const st = await Files.status();
    r.files.status = st;
    const roots = await Files.list("");
    r.files.roots = roots.map(x => x.name);
    const items = await Files.list(roots[0].path);
    r.files.items = items.map(x => x.name);
    const bytes = await Files.read(roots[0].path + "/a.svg");
    r.files.read_len = bytes.length;
    r.files.write = await Files.write(roots[0].path + "/written.nc", "G0 X0\n");
    try { await Files.write(roots[0].path + "/written.nc", "again"); r.files.exists = "no error"; } catch (e) { r.files.exists = e.code; }
    r.files.overwrite = (await Files.write(roots[0].path + "/written.nc", "again", { overwrite: true })).size;
    try { await Files.read(roots[0].path + "/../x.svg"); r.files.escape = "no error"; } catch (e) { r.files.escape = e.code; }
    return r;
  }, { project, svg, key });
  out.problems = problems;
  fs.writeFileSync(outFile, JSON.stringify(out));
  await browser.close();
})().catch(e => { console.error(e); process.exit(1); });

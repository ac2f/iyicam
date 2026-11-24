// The Web Worker that runs iyiCAM's Python engine (Pyodide).  The page talks to it through engine.js, never directly.
//
// Messages in:   {type: "init", config, flag}            load Pyodide and the iyicam package
//                {type: "call", id, op, payload}         payload is a JSON string; one call at a time
// Messages out:  {type: "status", text, fraction}        while loading
//                {type: "ready", version, persistent}    the engine answers
//                {type: "fail", message, stack}          loading failed
//                {type: "progress", id, k, n, label}     a long op reports
//                {type: "result", id, json}              what bridge.call returned (a JSON string)
//
// Loading is made of independent downloads (the Pyodide runtime, the iyicam package, the bridge, the wheels) that all start
// at once; only what needs Pyodide itself waits for it.

const HOME = "/home/pyodide/.iyicam";      // IYICAM_HOME, kept in IndexedDB
let pyodide = null, bridge = null, flagArray = null, syncing = Promise.resolve();

const status = (text, fraction) => postMessage({ type: "status", text, fraction });

async function fetchOk(url, what, as) {
  const r = await fetch(url, { cache: "no-cache" });          // (revalidates: an unchanged file answers 304 and comes from the cache)
  if (!r.ok) throw new Error(`${what}: ${url} answered ${r.status}`);
  return as === "text" ? r.text() : r.arrayBuffer();
}

// IndexedDB writes are one at a time: a second sync waits for the first.
function syncHome(populate) {
  syncing = syncing.then(() => new Promise(resolve => {
    pyodide.FS.syncfs(populate, err => { if (err) console.warn("iyicam: IndexedDB sync failed", err); resolve(!err); });
  }));
  return syncing;
}

async function init(cfg, flag) {
  flagArray = flag ? new Int32Array(flag) : null;
  status("Downloading the iyiCAM engine", 0.02);
  const base = cfg.pyodide.endsWith("/") ? cfg.pyodide : cfg.pyodide + "/";
  // started now, awaited when needed
  const zipP = fetchOk(cfg.zip, "the iyicam package");
  const bridgeP = fetchOk(cfg.bridge, "the bridge", "text");
  const wheelsP = Promise.all((cfg.wheels || []).map(u => fetchOk(u, "a wheel").then(b => [u, b])));
  const mod = await import(base + "pyodide.mjs");
  status("Starting Python", 0.15);
  pyodide = await mod.loadPyodide({ indexURL: base });

  status("Loading numpy and pyclipper", 0.35);
  const home = (async () => {                                   // the user folder, while the packages come
    pyodide.FS.mkdirTree(HOME);
    try {
      pyodide.FS.mount(pyodide.FS.filesystems.IDBFS, {}, HOME);
      return await syncHome(true);
    } catch (e) {                                               // (no IndexedDB: private window, blocked site data) the engine still works, forgetting at the end
      console.warn("iyicam: the user folder is not kept", e);
      return false;
    }
  })();
  await pyodide.loadPackage(["numpy", "pyclipper", "micropip"]);
  status("Installing ezdxf and svgelements", 0.55);
  const micropip = pyodide.pyimport("micropip");
  const wheels = await wheelsP;
  if (wheels.length) {                                          // a self-hosted folder brings its own wheels: no PyPI needed
    for (const [u] of wheels) await micropip.install(u, { deps: false });
  } else {
    await micropip.install(["ezdxf", "svgelements"]);
  }

  status("Unpacking iyiCAM", 0.8);
  pyodide.unpackArchive(await zipP, "zip", { extractDir: "/lib" });
  pyodide.FS.writeFile("/lib/bridge.py", await bridgeP);
  const persistent = await home;
  pyodide.runPython(`import sys\nif "/lib" not in sys.path: sys.path.insert(0, "/lib")`);
  status("Starting iyiCAM", 0.9);
  bridge = pyodide.pyimport("bridge");
  const version = bridge.init(HOME);
  postMessage({ type: "ready", version, persistent, pyodide: pyodide.version });
}

const isCancelled = () => (flagArray ? Atomics.load(flagArray, 0) !== 0 : false);

async function call(msg) {
  let json;
  try {
    json = bridge.call(msg.op, msg.payload, (k, n, label) => postMessage({ type: "progress", id: msg.id, k, n, label }),
      flagArray ? isCancelled : null);
    if (json.endsWith('"dirty":true}')) await syncHome(false);                           // the person's tools, posts, presets: kept before the answer is given
  } catch (e) {                                                 // (bridge.call does not raise; this is the proxy itself failing)
    json = JSON.stringify({ ok: false, error: { code: "internal", message: String(e && e.message || e), details: String(e && e.stack || "") } });
  }
  postMessage({ type: "result", id: msg.id, json });
}

onmessage = ev => {
  const m = ev.data;
  if (m.type === "init") {
    init(m.config, m.flag).catch(e => postMessage({ type: "fail", message: String(e && e.message || e), stack: String(e && e.stack || "") }));
  } else if (m.type === "call") {
    call(m);
  }
};

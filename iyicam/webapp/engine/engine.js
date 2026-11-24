// The engine of the web app: iyiCAM's Python code (numpy, pyclipper, ezdxf ... and iyicam itself) in a Web Worker with
// Pyodide.  The page calls the JSON operations of iyicam/service/ops.py through it:
//
//   import { Engine } from "./engine/engine.js";
//   const engine = await Engine.start({ onStatus: (text, fraction) => ... });
//   const schema = await engine.call("schema", {});
//   const r = await engine.call("compute", { project }, { onProgress: (k, n, label) => ..., signal });
//
// Cancelling.  A long call is synchronous Python in the worker, which cannot hear a message while it runs.  When the page
// is cross-origin isolated (the server sends COOP/COEP; see iyicam/share/server.py) a SharedArrayBuffer is shared with the
// worker: aborting sets a flag that the Python side reads between steps (JobControl.cancelled), the call ends with
// EngineError "cancelled" within a fraction of a second and the worker stays loaded.  Without isolation (a static host
// that sends no such headers, an old browser) the only way is to end the worker and start another, which costs the
// loading again (seconds, from the browser's cache): the call is rejected with "cancelled" at once and `engine.ready`
// is a new promise.  Either way the caller sees the same thing.

export class EngineError extends Error {
  constructor(code, message, details = null) {
    super(message);
    this.name = "EngineError";
    this.code = code;
    this.details = details;
  }
}

const HERE = new URL(".", import.meta.url);                       // .../engine/
const DEFAULTS = { pyodide: "https://cdn.jsdelivr.net/pyodide/v0.27.7/full/", zip: "/app/py/iyicam.zip", bridge: "/app/bridge.py", wheels: [] };

// What the server says about itself (a folder of Pyodide of its own, wheels); a host without the file gets the defaults.
async function loadConfig() {
  const cfg = { ...DEFAULTS };
  cfg.zip = new URL("../py/iyicam.zip", HERE).href;
  cfg.bridge = new URL("../bridge.py", HERE).href;
  try {
    const r = await fetch(new URL("../config.json", HERE), { cache: "no-cache" });
    if (r.ok) {
      const j = await r.json();
      for (const k of Object.keys(DEFAULTS)) if (j[k] != null) cfg[k] = j[k];
    }
  } catch { /* the defaults */ }
  const abs = u => new URL(u, location.href).href;
  cfg.zip = abs(cfg.zip); cfg.bridge = abs(cfg.bridge); cfg.pyodide = abs(cfg.pyodide);
  cfg.wheels = (cfg.wheels || []).map(abs);
  return cfg;
}

export class Engine {
  /** Load the engine; resolves when it answers.  Options: onStatus(text, fraction), config (overrides of pyodide, zip,
   *  bridge, wheels), isolated (false: never use a SharedArrayBuffer, for tests). */
  static async start(opts = {}) {
    const engine = new Engine(opts);
    await engine.ready;
    return engine;
  }

  constructor(opts = {}) {
    this.opts = opts;
    this.version = null;            // iyiCAM's version, once ready
    this.pyodideVersion = null;
    this.persistent = false;        // the user folder is kept in IndexedDB
    this.cancelMode = null;         // "flag" or "restart"
    this._worker = null;
    this._flag = null;              // Int32Array over a SharedArrayBuffer, or null
    this._queue = [];               // calls waiting for the worker
    this._running = null;           // the call the worker is busy with
    this._nextId = 1;
    this._cfg = null;
    this._starts = 0;
    this.ready = this._start();
  }

  _status(text, fraction) { if (this.opts.onStatus) this.opts.onStatus(text, fraction); }

  _start() {
    const gen = ++this._starts;
    this._ready = false;
    return (async () => {
      this._cfg = this._cfg || { ...(await loadConfig()), ...(this.opts.config || {}) };
      const useFlag = this.opts.isolated !== false && typeof SharedArrayBuffer !== "undefined" && globalThis.crossOriginIsolated;
      const sab = useFlag ? new SharedArrayBuffer(4) : null;
      this._flag = sab ? new Int32Array(sab) : null;
      this.cancelMode = sab ? "flag" : "restart";
      const worker = new Worker(new URL("./worker.js", import.meta.url), { type: "module", name: "iyicam-engine" });
      this._worker = worker;
      return new Promise((resolve, reject) => {
        worker.onmessage = ev => {
          const m = ev.data;
          if (gen !== this._starts) return;
          switch (m.type) {
            case "status": this._status(m.text, m.fraction); break;
            case "ready":
              this.version = m.version; this.pyodideVersion = m.pyodide; this.persistent = m.persistent;
              this._ready = true;
              this._status("Ready", 1);
              resolve(this);
              this._pump();
              break;
            case "fail": reject(new EngineError("engine_failed", "The engine could not be loaded: " + m.message, m.stack)); break;
            case "progress": if (this._running && this._running.id === m.id && this._running.onProgress) this._running.onProgress(m.k, m.n, m.label); break;
            case "result": this._finish(m); break;
          }
        };
        worker.onerror = ev => {                                    // (the worker died: out of memory, a script error)
          if (gen !== this._starts) return;
          const err = new EngineError("engine_crashed", "The engine stopped: " + (ev.message || "worker error"));
          reject(err);
          this._crashed(err);
        };
        worker.postMessage({ type: "init", config: this._cfg, flag: sab });
      });
    })();
  }

  _crashed(err) {
    if (this._running) { this._running.reject(err); this._detach(this._running); this._running = null; }
    try { this._worker.terminate(); } catch { /* gone already */ }
    this.ready = this._start();
    this.ready.catch(() => {});
  }

  _finish(m) {
    const job = this._running;
    if (!job || job.id !== m.id) return;
    this._running = null;
    this._detach(job);
    let r;
    try { r = JSON.parse(m.json); } catch (e) { r = { ok: false, error: { code: "internal", message: "the engine's answer is not JSON", details: String(m.json).slice(0, 500) } }; }
    if (r.ok) job.resolve(r.result);
    else job.reject(new EngineError(r.error.code, r.error.message, r.error.details));
    this._pump();
  }

  _detach(job) { if (job.signal && job.onAbort) job.signal.removeEventListener("abort", job.onAbort); }

  _pump() {
    if (!this._ready || this._running || !this._queue.length) return;
    const job = this._queue.shift();
    this._running = job;
    if (this._flag) Atomics.store(this._flag, 0, 0);               // (calls are one at a time, so nobody else can be waiting for this flag)
    this._worker.postMessage({ type: "call", id: job.id, op: job.op, payload: job.payload });
  }

  /** Run ``op_<op>`` of the service.  Resolves with its result; rejects with EngineError {code, message, details}.
   *  opts: onProgress(k, n, label), signal (AbortSignal: rejects with code "cancelled"). */
  call(op, payload = {}, opts = {}) {
    const { onProgress, signal } = opts;
    if (signal && signal.aborted) return Promise.reject(new EngineError("cancelled", "cancelled"));
    return new Promise((resolve, reject) => {
      const job = { id: this._nextId++, op, payload: JSON.stringify(payload ?? {}), resolve, reject, onProgress, signal, onAbort: null };
      if (signal) {
        job.onAbort = () => this._abort(job);
        signal.addEventListener("abort", job.onAbort, { once: true });
      }
      this._queue.push(job);
      this._pump();
    });
  }

  _abort(job) {
    const queued = this._queue.indexOf(job);
    if (queued >= 0) {                                            // not started: it is simply not run
      this._queue.splice(queued, 1);
      job.reject(new EngineError("cancelled", "cancelled"));
      return;
    }
    if (this._running !== job) return;                            // already finished
    if (this._flag) {
      Atomics.store(this._flag, 0, 1);                            // Python raises inside the call and the result says "cancelled"
    } else {                                                      // no way to reach the running call: end the worker
      job.reject(new EngineError("cancelled", "cancelled"));
      this._running = null;
      this._starts++;                                             // (messages of the old worker are ignored from here)
      try { this._worker.terminate(); } catch { /* gone already */ }
      this.ready = this._start();
      this.ready.catch(() => {});
    }
  }

  /** Stop the worker for good. */
  close() {
    this._starts++;
    try { this._worker.terminate(); } catch { /* gone already */ }
    const err = new EngineError("engine_closed", "The engine was closed");
    if (this._running) this._running.reject(err);
    for (const j of this._queue) j.reject(err);
    this._queue = []; this._running = null;
  }
}

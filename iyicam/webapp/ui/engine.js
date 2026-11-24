/* iyiCAM web: the engine (Pyodide in a Web Worker, ../engine/engine.js) and the files API behind one `run` that shows what
 * is going on: a busy bar with progress and a Cancel button for the long operations. */
import { app, emit } from "./state.js";
import { toast } from "./dom.js";

export async function startEngine(onStatus) {
  let real = false;
  try { real = (await fetch("./engine/engine.js", { method: "HEAD" })).ok; } catch { real = false; }
  if (real) {
    const { Engine } = await import("../engine/engine.js");
    app.engine = await Engine.start({ onStatus });
    app.fake = false;
  } else {
    const { FakeEngine } = await import("./devfake.js");     // (removed when the real engine is served everywhere)
    app.engine = await FakeEngine.start({ onStatus });
    app.fake = true;
  }
  return app.engine;
}

/** The files API (null when the server has none, as with a plain static server). */
let filesApi;
export async function files() {
  if (filesApi !== undefined) return filesApi;
  try { filesApi = (await import("../engine/files.js")).Files; } catch { filesApi = null; }
  return filesApi;
}

/** Run an engine operation with the busy bar.  `opts.cancel`: the operation may be cancelled by the person (a Cancel button). */
export async function run(op, payload, opts = {}) {
  const ctl = new AbortController();
  const busy = { label: opts.label || op, k: 0, n: 0, text: "", cancel: opts.cancel ? () => ctl.abort() : null };
  app.busy = busy;
  emit("busy");
  try {
    return await app.engine.call(op, payload, {
      signal: ctl.signal,
      onProgress: (k, n, label) => { busy.k = k; busy.n = n; busy.text = label || ""; emit("busy"); },
    });
  } finally {
    if (app.busy === busy) app.busy = null;
    emit("busy");
  }
}

export const cancelled = e => e && (e.code === "cancelled" || e.name === "AbortError");

/** What an engine error says to the person (the traceback of an internal error stays in the console). */
export function errorText(e) {
  if (!e) return "Unknown error";
  if (e.code === "internal") console.error(e.details || e);
  return e.message || String(e);
}
export function report(e, what = "") {
  if (cancelled(e)) { toast("Cancelled"); return; }
  console.error(e);
  toast((what ? what + ": " : "") + errorText(e), "error");
  app.message = (what ? what + ": " : "") + errorText(e);
  emit("message");
}

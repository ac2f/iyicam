// Runs the web app's geometry (iyicam/webapp/ui/geom.js) for the tests: one JSON request a line on stdin
// ({"fn": name, "args": [...]}), one JSON answer a line on stdout ({"ok": result} or {"error": text}).
import { createInterface } from "node:readline";
import { webcrypto } from "node:crypto";
if (!globalThis.crypto) globalThis.crypto = webcrypto;
const G = (await import(new URL("../../iyicam/webapp/ui/geom.js", import.meta.url))).default;
const rl = createInterface({ input: process.stdin });
for await (const line of rl) {
  let out;
  try {
    const { fn, args } = JSON.parse(line);
    out = { ok: G[fn](...args) };
  } catch (e) {
    out = { error: String(e && e.stack || e) };
  }
  process.stdout.write(JSON.stringify(out === undefined ? { ok: null } : out) + "\n");
}

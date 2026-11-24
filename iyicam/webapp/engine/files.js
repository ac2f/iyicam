// The files of the workshop computer, as the server (iyicam serve) gives them: the folders that an agent shares, or, when the
// server runs with --local, those of the computer it runs on.  Paths are virtual: "" is the list of roots, "jobs/2024/a.icam" is
// a file below the root called "jobs".  Everything needs the person to be signed in with the key (Files.login).
//
//   import { Files } from "./engine/files.js";
//   const st = await Files.status();             // {signed_in, agent: {connected, name, local} | null, roots: [{name, path}]}
//   const items = await Files.list("");          // [{name, path, dir, size, mtime}]
//   const bytes = await Files.read("jobs/a.icam");          // Uint8Array
//   await Files.write("jobs/b.icam", bytes, { overwrite: false });   // {path, size, mtime}; rejects with code "file_exists"
//
// A failure rejects with FilesError {code, message, status}: not_logged_in, agent_offline, no_files, path_not_allowed, not_found,
// file_exists, file_too_large, unsupported_file, bad_path, ... (the codes of iyicam/share/rootfs.py).

export class FilesError extends Error {
  constructor(code, message, status = 0, details = null) {
    super(message);
    this.name = "FilesError";
    this.code = code;
    this.status = status;
    this.details = details;
  }
}

async function request(url, init = {}) {
  let r;
  try {
    r = await fetch(url, { credentials: "same-origin", ...init });
  } catch (e) {
    throw new FilesError("network", "The server cannot be reached", 0, String(e));
  }
  if (r.ok) return r;
  let d = {};
  try { d = await r.json(); } catch { /* not JSON */ }
  throw new FilesError(d.error || "http_" + r.status, d.message || `The server answered ${r.status}`, r.status, d);
}

const q = path => "path=" + encodeURIComponent(path ?? "");

export const Files = {
  base: "/api",

  /** Sign in with the key.  ``remember``: stay signed in on this device (90 days instead of 12 hours). */
  async login(key, { remember = false } = {}) {
    await request(this.base + "/login", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ key, remember }) });
    return true;
  },
  async logout() { await request(this.base + "/logout", { method: "POST" }); },
  /** {signed_in, mode: "agent" | "local"} */
  async session() { return (await request(this.base + "/session")).json(); },

  /** {signed_in, agent: {connected, name, local} | null, roots: [{name, path}]} */
  async status() { return (await request(this.base + "/files/status")).json(); },
  /** The roots (path ""), or the folders and files of a folder: [{name, path, dir, size, mtime}], folders first. */
  async list(path = "") { return (await request(`${this.base}/files/list?${q(path)}`)).json().then(d => d.entries); },
  async stat(path) { return (await request(`${this.base}/files/stat?${q(path)}`)).json(); },
  async read(path) { return new Uint8Array(await (await request(`${this.base}/files/read?${q(path)}`)).arrayBuffer()); },
  /** ``data``: Uint8Array, ArrayBuffer, Blob or string (UTF-8).  Without ``overwrite`` an existing file is not touched. */
  async write(path, data, { overwrite = false } = {}) {
    const body = typeof data === "string" ? new TextEncoder().encode(data) : data;
    return (await request(`${this.base}/files/write?${q(path)}&overwrite=${overwrite ? 1 : 0}`,
      { method: "PUT", headers: { "Content-Type": "application/octet-stream" }, body })).json();
  },
};

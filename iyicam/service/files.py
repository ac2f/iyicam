"""Files of the computer for the web page: look into folders, open a drawing or project, save a project, write exported
files (G-code and the like) into a folder.

The operations and their answers are the same as the ones CNC-Assistant's mobile page calls on its agent
(``list``, ``open``, ``save``, ``save_files``, ``status``), so a page written for one works with the other, but the
code is this program's own: the two projects share nothing but that interface.

Everything is **inside the roots**, the folders the person who starts the server names (``--root``, or ``IYICAM_ROOTS``
separated like ``PATH``; without either: the home folder):

* a path is turned into its real path first (a symbolic link cannot lead out) and must lie inside a root;
* nothing below a root that is hidden (a name starting with a dot) is ever reachable, and neither is the folder in
  which iyiCAM keeps its own state (the key of the web service, the user's posts and tools) unless a root was
  named *inside* it on purpose;
* a folder lists its sub-folders and the files of the kinds the page knows, nothing else;
* only known extensions are read or written, never a program or a script;
* an existing file is replaced only when the caller saw its latest state (``base_hash``) or says ``overwrite``.

The project itself is data that the page holds: ``open`` gives a project in (through the ``import`` operation of the
service), ``save`` takes a project out.  Every failure is ``{"error": code, "message": ..., "params": {...}}`` like
every answer of the service; :meth:`Files.handle` never raises.
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
import secrets
import sys
import traceback

from ..core import paths
from .errors import ServiceError

MAX_OPEN_BYTES = 64 * 1024 * 1024        # biggest file that is opened
MAX_WRITE_BYTES = 256 * 1024 * 1024      # most that is written in one call
LIST_LIMIT = 3000

READ_EXT = {".icam", ".dxf", ".svg", ".plt", ".hpgl", ".hpg", ".cdr"}
GCODE_EXT = {".nc", ".tap", ".ngc", ".cnc", ".gcode", ".mpf", ".txt"}
WRITE_EXT = {".icam", ".dxf", ".svg", ".png"} | GCODE_EXT
DENIED_EXT = {".exe", ".bat", ".cmd", ".com", ".ps1", ".vbs", ".js", ".py", ".sh", ".lnk", ".dll", ".msi",
              ".scr", ".jar", ".reg", ".hta", ".pif", ".cpl"}

FILE_OPS = frozenset({"status", "list", "open", "save", "save_files"})


def default_roots() -> list[str]:
    """The roots when none were named: ``IYICAM_ROOTS`` (separated like ``PATH``), else the home folder."""
    env = os.environ.get("IYICAM_ROOTS")
    return [p for p in env.split(os.pathsep) if p.strip()] if env else [os.path.expanduser("~")]


def _norm(p: str) -> str:
    return os.path.normcase(p)


def _under(full: str, root: str) -> bool:
    try:
        return os.path.commonpath([_norm(full), _norm(root)]) == _norm(root)
    except ValueError:             # another drive
        return False


def _ext(path: str) -> str:
    return os.path.splitext(path)[1].lower()


def _sha256(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _write_atomic(full: str, raw: bytes) -> None:
    """The file appears whole or not at all.  (``replace`` swaps a symbolic link itself, it never writes through it.)"""
    tmp = f"{full}.{secrets.token_hex(4)}.tmp"
    try:
        with open(tmp, "wb") as f:
            f.write(raw)
        os.replace(tmp, full)
    finally:
        if os.path.exists(tmp):
            try:
                os.remove(tmp)
            except OSError:
                pass


class Files:
    """The file operations of one server.  ``engine`` is whatever has ``handle(op, payload)`` (the
    :class:`~iyicam.service.ServiceHost`); ``roots`` the folders that may be touched (default :func:`default_roots`)."""

    def __init__(self, engine, roots: list[str] | None = None):
        self.engine = engine
        self._roots = [r for r in (roots or []) if r and str(r).strip()] or None

    # -- the roots ------------------------------------------------------------------------------
    def roots(self) -> list[str]:
        """The real paths of the roots that exist now (looked up each time: a drive may appear later)."""
        out: list[str] = []
        for p in self._roots or default_roots():
            real = os.path.realpath(os.path.expanduser(str(p)))
            if os.path.isdir(real) and real not in out:
                out.append(real)
        return out

    def resolve(self, path) -> str:
        """The real path of ``path`` when it lies inside a root and nothing below the root is hidden."""
        if not isinstance(path, str) or not path.strip() or "\0" in path:
            raise ServiceError("invalid_path", "a path is required")
        full = os.path.realpath(os.path.expanduser(path))        # symbolic links are followed: no way out through one
        state = os.path.realpath(str(paths.user_dir()))
        for root in self.roots():
            if not _under(full, root):
                continue
            rel = os.path.relpath(full, root)
            parts = [] if rel == "." else rel.split(os.sep)
            if any(p.startswith(".") for p in parts):
                break      # ~/.ssh, ~/.iyicam (the key and the settings) and the like are never reachable
            # the state folder of iyiCAM is closed too, except for a root that was put inside it on purpose
            if _under(full, state) and not (_under(root, state) and _norm(root) != _norm(state)):
                break
            return full
        raise ServiceError("path_not_allowed", "this path is outside the folders that may be used", path=path)

    # -- the operations -------------------------------------------------------------------------
    def status(self, data=None) -> dict:
        return {"files": True, "roots": self.roots(), "limits": {"open": MAX_OPEN_BYTES, "write": MAX_WRITE_BYTES}}

    def list(self, data) -> dict:
        """Folders and the files of the kinds the page knows, inside the roots.  Without a path: the roots."""
        path = (data or {}).get("path")
        if not path:
            return {"path": "", "parent": None, "roots": True,
                    "entries": [{"name": r, "path": r, "type": "dir"} for r in self.roots()]}
        full = self.resolve(path)
        if not os.path.isdir(full):
            raise ServiceError("folder_not_found", f"folder not found: {path}", path=path)
        entries, truncated = [], False
        try:
            with os.scandir(full) as it:
                for e in it:
                    if e.name.startswith("."):
                        continue
                    try:
                        if e.is_dir():
                            entries.append({"name": e.name, "path": os.path.join(full, e.name), "type": "dir"})
                        elif _ext(e.name) in READ_EXT | GCODE_EXT:
                            st = e.stat()
                            entries.append({"name": e.name, "path": os.path.join(full, e.name), "type": "file",
                                            "ext": _ext(e.name).lstrip("."), "size": st.st_size, "mtime": st.st_mtime})
                    except OSError:
                        continue
                    if len(entries) >= LIST_LIMIT:
                        truncated = True
                        break
        except OSError as exc:
            raise ServiceError("os_error", str(exc)) from exc
        entries.sort(key=lambda x: (x["type"] != "dir", x["name"].lower()))
        parent = os.path.dirname(full)
        try:
            parent = self.resolve(parent) if parent != full else None
        except ServiceError:
            parent = None
        return {"path": full, "parent": parent, "entries": entries, "truncated": truncated}

    def open(self, data) -> dict:
        """Read a project (``.icam``) or a drawing (DXF, SVG, HPGL, CorelDRAW) into a project.  A drawing is added to
        the project in ``project`` (to ``sheet``) when one is given, else it starts a new one (``thickness``)."""
        data = data or {}
        full = self.resolve(data.get("path"))
        ext = _ext(full)
        if ext not in READ_EXT:
            raise ServiceError("unsupported_file", f"cannot open {ext or 'this'} files", extension=ext)
        if not os.path.isfile(full):
            raise ServiceError("file_not_found", f"file not found: {data.get('path')}", path=data.get("path"))
        size = os.path.getsize(full)
        if size > MAX_OPEN_BYTES:
            raise ServiceError("file_too_large", f"the file is {size // 1048576} MB; the limit is "
                                                 f"{MAX_OPEN_BYTES // 1048576} MB", size=size)
        with open(full, "rb") as f:
            raw = f.read()
        req = {"name": os.path.basename(full), "data_b64": base64.b64encode(raw).decode("ascii")}
        for key in ("thickness", "project", "sheet"):
            if data.get(key) is not None:
                req[key] = data[key]
        res = self.engine.handle("import", req)
        if "error" in res:
            return res
        res.update(path=full, name=os.path.basename(full), hash=_sha256(raw), size=size,
                   mtime=os.path.getmtime(full), kind="project" if ext == ".icam" else "drawing")
        return res

    def save(self, data) -> dict:
        """Write a project as ``.icam``.  An existing file is replaced only when the caller saw its latest state
        (``base_hash``) or says ``overwrite``: a project changed on the computer in the meantime is not lost."""
        data = data or {}
        full = self.resolve(data.get("path"))
        if _ext(full) != ".icam":
            raise ServiceError("invalid_path", "a project is saved as a .icam file")
        checked = self.engine.handle("validate", {"project": data.get("project")})
        if "error" in checked:
            return checked
        raw = json.dumps(checked["project"], separators=(",", ":")).encode("utf-8")
        if os.path.isdir(full):
            raise ServiceError("invalid_path", "this is a folder")
        if not os.path.isdir(os.path.dirname(full)):
            raise ServiceError("folder_not_found", "the folder does not exist", path=os.path.dirname(full))
        if os.path.exists(full) and not data.get("overwrite"):
            base = data.get("base_hash")
            with open(full, "rb") as f:
                now = _sha256(f.read())
            if not base:
                raise ServiceError("file_exists", "a file with this name exists", path=full)
            if base != now:
                raise ServiceError("file_changed", "the file was changed on the computer since it was opened",
                                   path=full)
        _write_atomic(full, raw)
        return {"path": full, "hash": _sha256(raw), "size": len(raw), "mtime": os.path.getmtime(full)}

    def _write_extensions(self) -> set[str]:
        """Known extensions plus those of the posts the engine knows (a custom post may use its own)."""
        exts = set(WRITE_EXT)
        try:
            for p in self.engine.handle("schema", {}).get("posts", []):
                exts.add("." + str(p.get("extension", "")).lower().lstrip("."))
        except Exception:  # noqa: BLE001  (the known extensions are enough)
            pass
        return {e for e in exts if len(e) > 1 and e not in DENIED_EXT}

    def save_files(self, data) -> dict:
        """Write exported files (G-code, DXF, SVG, a simulation image) into a folder.  Nothing is replaced unless
        ``overwrite``; when any name is taken nothing is written."""
        data = data or {}
        folder = self.resolve(data.get("folder"))
        if not os.path.isdir(folder):
            raise ServiceError("folder_not_found", "the folder does not exist", path=data.get("folder"))
        files = data.get("files")
        if not isinstance(files, list) or not files:
            raise ServiceError("bad_request", "no files to write")
        allowed = self._write_extensions()
        plan, total = [], 0
        for f in files:
            name = f.get("name") if isinstance(f, dict) else None
            if (not isinstance(name, str) or not name or os.path.basename(name) != name or name.startswith(".")
                    or "\0" in name or "/" in name or "\\" in name):
                raise ServiceError("invalid_name", "a file name is not valid", name=str(name))
            if _ext(name) not in allowed:
                raise ServiceError("unsupported_file", f"files of type {_ext(name) or '(none)'} are not written",
                                   name=name)
            if "text" in f:
                raw = str(f["text"]).encode("ascii", "replace")        # as write_gcode does: the post's line ends kept
            else:
                try:
                    raw = base64.b64decode(f.get("data_b64") or "", validate=False)
                except (ValueError, TypeError) as exc:
                    raise ServiceError("bad_request", "the file data is not valid base64", name=name) from exc
            total += len(raw)
            if total > MAX_WRITE_BYTES:
                raise ServiceError("payload_too_large", "too much data in one call")
            plan.append((os.path.join(folder, name), name, raw))
        taken = [n for p, n, _ in plan if os.path.exists(p)]
        if taken and not data.get("overwrite"):
            raise ServiceError("file_exists", "files with these names exist", names=taken)
        for p, _, raw in plan:
            _write_atomic(p, raw)
        return {"written": [p for p, _, _ in plan], "folder": folder}

    # -- the front door -------------------------------------------------------------------------
    def handle(self, op: str, data: dict | None = None) -> dict:
        """One of :data:`FILE_OPS`; never raises."""
        fn = {"status": self.status, "list": self.list, "open": self.open, "save": self.save,
              "save_files": self.save_files}.get(op)
        if fn is None:
            return ServiceError("unknown_operation", f"there is no operation '{op}'", operation=op).to_dict()
        try:
            return fn(data if isinstance(data, dict) else {})
        except ServiceError as exc:
            return exc.to_dict()
        except OSError as exc:
            return ServiceError("os_error", str(exc)).to_dict()
        except Exception as exc:  # noqa: BLE001  (a bug: said as it is, the server stays up)
            traceback.print_exc(file=sys.stderr)
            return {"error": "internal_error", "message": f"{type(exc).__name__}: {exc}"}

"""The folders that are shared (the **roots**) and the four things that may be done in them: list, stat, read, write.

Used by the agent (the roots of the workshop PC) and by ``iyicam serve --local`` (the roots of the computer the server
runs on).  The paths that travel are **virtual**: ``<root name>/sub/dir/file.icam``.  The root name is the last part of
the folder's real path (made unique when two are alike), so a browser never learns where on the disk a root is, and an
empty path means "the roots".  What is checked, in this order, for every path:

* only ``/`` separates, no ``..`` or ``.``, no empty parts, no NUL, no ``\\``;
* no part starts with a dot (``.ssh``, ``.iyicam`` ...) and iyiCAM's own state folder (the key, the posts, the tools) is
  closed, unless a root was named inside it on purpose;
* the **real** path (symbolic links followed) must lie inside the root: a link cannot lead out;
* what is read must be a kind of file the app opens, what is written must not be a program or a script, and both are
  limited in size.

Errors are :class:`FsError` with a stable ``code`` and the HTTP status that goes with it.
"""
from __future__ import annotations

import os
import secrets
import unicodedata

from ..core import paths

MAX_FILE_BYTES = 64 * 1024 * 1024        # biggest file that is read or written (a big CorelDRAW drawing is a few MB)
LIST_LIMIT = 5000

READ_EXT = {".icam", ".dxf", ".svg", ".plt", ".hpgl", ".hpg", ".cdr", ".png", ".json"}
GCODE_EXT = {".nc", ".tap", ".ngc", ".cnc", ".gcode", ".mpf", ".txt", ".cam", ".eia", ".anc", ".iso", ".tp", ".fan", ".h", ".m", ".pgm"}
SHOWN_EXT = READ_EXT | GCODE_EXT
# what is never written: a program or a script that something might run (a post may use any other extension)
DENIED_EXT = {".exe", ".bat", ".cmd", ".com", ".ps1", ".vbs", ".js", ".mjs", ".py", ".pyw", ".sh", ".lnk", ".dll", ".msi",
              ".scr", ".jar", ".reg", ".hta", ".pif", ".cpl", ".html", ".htm", ".php", ".so", ".dylib", ".app", ".desktop"}

STATUS = {"bad_path": 400, "path_not_allowed": 403, "not_found": 404, "not_a_file": 400, "not_a_folder": 400,
          "file_exists": 409, "file_too_large": 413, "unsupported_file": 415, "os_error": 500, "bad_request": 400,
          "agent_offline": 503, "agent_timeout": 504, "too_busy": 503, "no_files": 503}


class FsError(Exception):
    def __init__(self, code: str, message: str, **params):
        super().__init__(message)
        self.code, self.message, self.params = code, message, params
        self.status = STATUS.get(code, 400)

    def to_dict(self) -> dict:
        out = {"error": self.code, "message": self.message}
        out.update(self.params)
        return out


def _norm(p: str) -> str:
    return os.path.normcase(p)


def _under(full: str, root: str) -> bool:
    try:
        return os.path.commonpath([_norm(full), _norm(root)]) == _norm(root)
    except ValueError:                   # another drive
        return False


def _ext(name: str) -> str:
    return os.path.splitext(name)[1].lower()


def _fold(text: str) -> str:
    text = unicodedata.normalize("NFD", text.replace("İ", "i").replace("ı", "i").casefold())
    return "".join(ch for ch in text if not unicodedata.combining(ch))


class RootFS:
    """``roots``: the real folders that are shared.  ``max_bytes``: the biggest file read or written."""

    def __init__(self, roots: list[str], max_bytes: int = MAX_FILE_BYTES):
        self.max_bytes = max_bytes
        self._given = [str(r) for r in roots if str(r).strip()]

    # -- the roots ------------------------------------------------------------------------------
    def table(self) -> list[tuple[str, str]]:
        """``[(name, real path)]`` of the roots that exist now (looked up each time: a drive may appear later)."""
        out: list[tuple[str, str]] = []
        names: set[str] = set()
        for p in self._given:
            real = os.path.realpath(os.path.expanduser(p))
            if not os.path.isdir(real) or any(real == r for _n, r in out):
                continue
            base = os.path.basename(real.rstrip("/\\")) or real.rstrip(":/\\") or "root"
            base = base.replace("/", "_").replace("\\", "_")
            if base.startswith("."):
                base = "_" + base.lstrip(".")
            name, i = base, 2
            while _fold(name) in names:
                name, i = f"{base}-{i}", i + 1
            names.add(_fold(name))
            out.append((name, real))
        return out

    def roots(self) -> list[dict]:
        return [{"name": n, "path": n} for n, _r in self.table()]

    # -- paths ----------------------------------------------------------------------------------
    def resolve(self, path) -> tuple[str, str, str]:
        """``(real path, root real path, clean virtual path)`` for a virtual path inside a root."""
        if not isinstance(path, str) or "\0" in path or "\\" in path or len(path) > 4096:
            raise FsError("bad_path", "the path is not valid")
        parts = [p for p in path.split("/") if p != ""] if path.strip("/") else []
        if not parts:
            raise FsError("bad_path", "a path is required")
        if any(p in (".", "..") or p.startswith(".") or p != p.strip() for p in parts):
            raise FsError("path_not_allowed", "this path is not allowed", path=path)
        table = dict((_fold(n), (n, r)) for n, r in self.table())
        hit = table.get(_fold(parts[0]))
        if hit is None:
            raise FsError("path_not_allowed", "this path is outside the shared folders", path=path)
        name, root = hit
        full = os.path.realpath(os.path.join(root, *parts[1:]))     # (symbolic links are followed: none leads out)
        if not _under(full, root):
            raise FsError("path_not_allowed", "this path is outside the shared folders", path=path)
        state = os.path.realpath(str(paths.user_dir()))
        if _under(full, state) and not (_under(root, state) and _norm(root) != _norm(state)):
            raise FsError("path_not_allowed", "this path is not allowed", path=path)
        return full, root, "/".join([name] + parts[1:])

    @staticmethod
    def _entry(vpath: str, name: str, st: os.stat_result, is_dir: bool) -> dict:
        return {"name": name, "path": vpath, "dir": is_dir, "size": 0 if is_dir else st.st_size, "mtime": st.st_mtime}

    # -- the operations -------------------------------------------------------------------------
    def list(self, path: str = "") -> dict:
        """The roots (empty path), or the folders and the files the app knows inside a folder; folders first."""
        if not path or not path.strip("/"):
            ents = []
            for n, r in self.table():
                try:
                    ents.append(self._entry(n, n, os.stat(r), True))
                except OSError:
                    continue
            return {"path": "", "entries": ents}
        full, root, v = self.resolve(path)
        if not os.path.isdir(full):
            raise FsError("not_a_folder", "this is not a folder", path=path) if os.path.exists(full) else \
                FsError("not_found", "the folder does not exist", path=path)
        ents = []
        try:
            with os.scandir(full) as it:
                for e in it:
                    if e.name.startswith("."):
                        continue
                    try:
                        is_dir = e.is_dir()
                        if not is_dir and _ext(e.name) not in SHOWN_EXT:
                            continue
                        real = os.path.realpath(e.path)
                        if not _under(real, root):          # (a link to somewhere else is not shown at all)
                            continue
                        ents.append(self._entry(f"{v}/{e.name}", e.name, e.stat(), is_dir))
                    except OSError:
                        continue
                    if len(ents) >= LIST_LIMIT * 4:
                        break
        except OSError as exc:
            raise FsError("os_error", str(exc)) from exc
        ents.sort(key=lambda x: (not x["dir"], _fold(x["name"])))
        return {"path": v, "entries": ents[:LIST_LIMIT], "truncated": len(ents) > LIST_LIMIT}

    def stat(self, path: str) -> dict:
        if not path or not path.strip("/"):
            raise FsError("bad_path", "a path is required")
        full, _root, v = self.resolve(path)
        try:
            st = os.stat(full)
        except FileNotFoundError:
            raise FsError("not_found", "no such file or folder", path=path) from None
        except OSError as exc:
            raise FsError("os_error", str(exc)) from exc
        return self._entry(v, os.path.basename(full) if "/" in v else v, st, os.path.isdir(full))

    def read(self, path: str) -> bytes:
        full, _root, _v = self.resolve(path)
        if _ext(full) not in SHOWN_EXT:
            raise FsError("unsupported_file", "this kind of file is not opened here", path=path)
        try:
            if not os.path.isfile(full):
                raise FsError("not_found" if not os.path.exists(full) else "not_a_file", "this is not a file", path=path)
            size = os.path.getsize(full)
            if size > self.max_bytes:
                raise FsError("file_too_large", f"the file is bigger than {self.max_bytes // (1024 * 1024)} MB", path=path, limit=self.max_bytes)
            with open(full, "rb") as f:
                return f.read(self.max_bytes + 1)
        except OSError as exc:
            raise FsError("os_error", str(exc)) from exc

    def write(self, path: str, data: bytes, overwrite: bool = False) -> dict:
        """The file appears whole or not at all.  An existing file is replaced only with ``overwrite``."""
        full, _root, v = self.resolve(path)
        ext = _ext(full)
        if "/" not in v:
            raise FsError("bad_path", "a file is written inside a folder, not in the list of roots", path=path)
        if ext in DENIED_EXT or not ext:
            raise FsError("unsupported_file", "files of this kind are not written", path=path)
        if len(data) > self.max_bytes:
            raise FsError("file_too_large", f"the file is bigger than {self.max_bytes // (1024 * 1024)} MB", path=path, limit=self.max_bytes)
        folder = os.path.dirname(full)
        if not os.path.isdir(folder):
            raise FsError("not_found", "the folder does not exist", path=path)
        if os.path.isdir(full):
            raise FsError("not_a_file", "this is a folder", path=path)
        tmp = f"{full}.{secrets.token_hex(4)}.tmp"
        try:
            with open(tmp, "wb") as f:
                f.write(data)
            if overwrite:
                os.replace(tmp, full)            # (replaces a symbolic link itself, never writes through it)
            else:
                try:
                    os.link(tmp, full)           # atomic "only if it is not there"
                except FileExistsError:
                    raise FsError("file_exists", "a file with this name exists", path=path) from None
                except OSError:                  # (a file system without hard links)
                    if os.path.lexists(full):
                        raise FsError("file_exists", "a file with this name exists", path=path) from None
                    os.replace(tmp, full)
            st = os.stat(full)
        except OSError as exc:
            raise FsError("os_error", str(exc)) from exc
        finally:
            try:
                os.remove(tmp)
            except OSError:
                pass
        return {"path": v, "size": st.st_size, "mtime": st.st_mtime}


def default_roots() -> list[str]:
    """``IYICAM_ROOTS`` (separated like ``PATH``), else the home folder."""
    env = os.environ.get("IYICAM_ROOTS")
    return [p for p in env.split(os.pathsep) if p.strip()] if env else [os.path.expanduser("~")]

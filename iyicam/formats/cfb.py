"""Reading Microsoft compound files (OLE2 / "structured storage": the container of ArtCAM ``.art`` and ``.3dp`` files,
old Office documents and many more).

Only what reading needs, in plain Python (it runs in the browser too): the sectors, the allocation tables, the
directory, and the bytes of a stream by its path (``"ArtCAM/Models/Model1/Parameters"``).
"""
from __future__ import annotations

import struct

MAGIC = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"
FREE, END, FAT_SECT, DIFAT_SECT = 0xFFFFFFFF, 0xFFFFFFFE, 0xFFFFFFFD, 0xFFFFFFFC
NOSTREAM = 0xFFFFFFFF


class CfbError(ValueError):
    pass


def is_cfb(head: bytes) -> bool:
    return head[:8] == MAGIC


class CompoundFile:
    """``CompoundFile(data)``; ``paths()`` lists the streams, ``read(path)`` gives one (a ``KeyError`` when it is not there)."""

    def __init__(self, data: bytes):
        if len(data) < 512 or not is_cfb(data):
            raise CfbError("not a compound (OLE2) file")
        self.data = data
        major, _order, shift, mini_shift = struct.unpack_from("<HHHH", data, 0x1A)
        self.sector = 1 << shift
        self.mini_sector = 1 << mini_shift
        if self.sector not in (512, 4096) or self.mini_sector != 64:
            raise CfbError(f"a compound file of a kind not known (sector {self.sector})")
        (n_fat, first_dir, _tx, self.cutoff, first_minifat, n_minifat, first_difat, n_difat) = \
            struct.unpack_from("<IIIIIIII", data, 0x2C)
        difat = list(struct.unpack_from("<109I", data, 0x4C))
        s, seen = first_difat, 0
        per = self.sector // 4 - 1
        while s not in (FREE, END) and seen < n_difat:        # (big files: the rest of the list is in sectors of its own)
            vals = struct.unpack_from(f"<{per + 1}I", self._sector(s))
            difat += vals[:per]
            s, seen = vals[per], seen + 1
        fat: list[int] = []
        for fs in difat[:n_fat]:
            if fs in (FREE, END):
                continue
            fat += struct.unpack_from(f"<{self.sector // 4}I", self._sector(fs))
        self.fat = fat
        self.minifat: list[int] = []
        if n_minifat and first_minifat not in (FREE, END):
            raw = self._chain(first_minifat)
            self.minifat = list(struct.unpack_from(f"<{len(raw) // 4}I", raw))
        d = self._chain(first_dir)
        self.entries = []
        for i in range(len(d) // 128):
            e = d[i * 128:(i + 1) * 128]
            nlen = struct.unpack_from("<H", e, 64)[0]
            name = e[:max(0, nlen - 2)].decode("utf-16-le", "replace")
            kind = e[66]
            left, right, child = struct.unpack_from("<III", e, 68)
            start, size = struct.unpack_from("<IQ", e, 116)
            if major == 3:
                size &= 0xFFFFFFFF
            self.entries.append((name, kind, left, right, child, start, size))
        if not self.entries or self.entries[0][1] != 5:
            raise CfbError("the compound file has no root")
        root = self.entries[0]
        self.ministream = self._chain(root[5], root[6]) if root[5] not in (FREE, END) else b""
        self._paths: dict[str, int] = {}
        self._walk(root[4], "", set())

    # -- the inside -----------------------------------------------------------------------------------------------
    def _sector(self, n: int) -> bytes:
        off = (n + 1) * self.sector
        if off >= len(self.data):
            raise CfbError(f"sector {n} is past the end of the file")
        return self.data[off:off + self.sector].ljust(self.sector, b"\0")   # (the last sector may be cut short)

    def _chain(self, start: int, size: int | None = None) -> bytes:
        out, s, n = [], start, 0
        while s not in (FREE, END):
            if s >= len(self.fat) or n > len(self.fat):
                raise CfbError("a broken chain of sectors")
            out.append(self._sector(s))
            s, n = self.fat[s], n + 1
        b = b"".join(out)
        return b if size is None else b[:size]

    def _mini_chain(self, start: int, size: int) -> bytes:
        out, s, n = [], start, 0
        m = self.mini_sector
        while s not in (FREE, END) and n * m < size:
            if s >= len(self.minifat):
                raise CfbError("a broken chain of small sectors")
            out.append(self.ministream[s * m:(s + 1) * m])
            s, n = self.minifat[s], n + 1
        return b"".join(out)[:size]

    def _walk(self, i: int, prefix: str, seen: set) -> None:
        stack = [(i, prefix)]
        while stack:
            i, prefix = stack.pop()
            if i == NOSTREAM or i >= len(self.entries) or i in seen:
                continue
            seen.add(i)
            name, kind, left, right, child, _s, _z = self.entries[i]
            stack += [(left, prefix), (right, prefix)]
            path = prefix + name
            if kind == 2:
                self._paths[path] = i
            elif kind == 1:
                self._paths.setdefault(path + "/", i)
                stack.append((child, path + "/"))

    # -- what it is for ------------------------------------------------------------------------------------------
    def paths(self) -> list[str]:
        """The streams (``a/b/c``) and storages (``a/b/``)."""
        return sorted(self._paths)

    def exists(self, path: str) -> bool:
        return path in self._paths

    def read(self, path: str) -> bytes:
        name, kind, _l, _r, _c, start, size = self.entries[self._paths[path]]
        if kind != 2:
            raise KeyError(path)
        if size < self.cutoff:
            return self._mini_chain(start, size)
        return self._chain(start, size)

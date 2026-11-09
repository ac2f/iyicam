"""A minimal PNG writer (the service must not need Pillow)."""
from __future__ import annotations

import struct
import zlib

import numpy as np


def _chunk(kind: bytes, data: bytes) -> bytes:
    return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)


def encode_png(rgb: np.ndarray, level: int = 6) -> bytes:
    """``rgb``: (height, width, 3) uint8."""
    rgb = np.ascontiguousarray(rgb, dtype=np.uint8)
    if rgb.ndim != 3 or rgb.shape[2] != 3:
        raise ValueError("an (h, w, 3) uint8 image is needed")
    h, w, _ = rgb.shape
    rows = np.zeros((h, 1 + 3 * w), dtype=np.uint8)        # filter type 0 in front of every row
    rows[:, 1:] = rgb.reshape(h, 3 * w)
    return (b"\x89PNG\r\n\x1a\n"
            + _chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0))
            + _chunk(b"IDAT", zlib.compress(rows.tobytes(), level))
            + _chunk(b"IEND", b""))

"""Write the program icon as iyicam.png (256 px) and iyicam.ico (16 - 256 px) next to this file.

``python packaging/make_icons.py``. The picture is drawn by ``iyicam.gui.theme.app_icon``; the .ico is written
by hand (a header and the PNG pictures), so no image plugin is needed.
"""
from __future__ import annotations

import os
import struct
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))

SIZES = (16, 24, 32, 48, 64, 128, 256)


def png_bytes(icon, size: int) -> bytes:
    from PySide6.QtCore import QBuffer, QIODevice
    buf = QBuffer()
    buf.open(QIODevice.WriteOnly)
    icon.pixmap(size, size).save(buf, "PNG")
    return bytes(buf.data())


def write_ico(path: Path, pictures: list[tuple[int, bytes]]) -> None:
    head = struct.pack("<HHH", 0, 1, len(pictures))
    offset = 6 + 16 * len(pictures)
    entries, data = b"", b""
    for size, png in pictures:
        entries += struct.pack("<BBBBHHII", size % 256, size % 256, 0, 0, 1, 32, len(png), offset + len(data))
        data += png
    path.write_bytes(head + entries + data)


def main() -> None:
    from PySide6.QtWidgets import QApplication

    from iyicam.gui.theme import app_icon
    app = QApplication.instance() or QApplication([])
    icon = app_icon(256)
    (HERE / "iyicam.png").write_bytes(png_bytes(icon, 256))
    write_ico(HERE / "iyicam.ico", [(s, png_bytes(icon, s)) for s in SIZES])
    print("wrote", HERE / "iyicam.png", "and", HERE / "iyicam.ico")
    del app


if __name__ == "__main__":
    main()

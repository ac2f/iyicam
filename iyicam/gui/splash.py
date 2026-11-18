"""The window shown while the program starts: the name, the version and what is being done now, so the start
is seen at once (the main window takes a few seconds: plugins, posts, the projects of the last session)."""
from __future__ import annotations

import time

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QColor, QFont, QPainter, QPixmap
from PySide6.QtWidgets import QApplication, QSplashScreen

from .. import __version__

W, H = 460, 260


def _pixmap(ratio: float = 1.0) -> QPixmap:
    from .theme import app_icon
    pm = QPixmap(int(W * ratio), int(H * ratio))
    pm.setDevicePixelRatio(ratio)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing)
    p.setPen(Qt.NoPen)
    p.setBrush(QColor("#0f172a"))
    p.drawRoundedRect(QRectF(0, 0, W, H), 18, 18)
    p.drawPixmap(32, 40, app_icon(96).pixmap(96, 96))
    p.setPen(QColor("#f8fafc"))
    f = QFont()
    f.setPointSize(26)
    f.setBold(True)
    p.setFont(f)
    p.drawText(QRectF(148, 48, W - 160, 48), Qt.AlignLeft | Qt.AlignVCenter, "iyiCAM")
    f.setPointSize(10)
    f.setBold(False)
    p.setFont(f)
    p.setPen(QColor("#94a3b8"))
    p.drawText(QRectF(150, 96, W - 160, 24), Qt.AlignLeft | Qt.AlignVCenter, f"Version {__version__}")
    p.drawText(QRectF(150, 118, W - 160, 24), Qt.AlignLeft | Qt.AlignVCenter, "CAM for CNC routers")
    p.end()
    return pm


class Splash(QSplashScreen):
    """``step(text)`` shows what is being done and moves the bar; ``finish(window)`` closes it when the window
    is up."""

    STEPS = 7

    def __init__(self):
        screen = QApplication.primaryScreen()
        ratio = screen.devicePixelRatio() if screen else 1.0
        super().__init__(_pixmap(ratio), Qt.WindowStaysOnTopHint | Qt.FramelessWindowHint | Qt.SplashScreen)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.text = "Starting..."
        self.done = 0
        self.started = time.monotonic()

    def step(self, text: str) -> None:
        self.text = text
        self.done = min(self.done + 1, self.STEPS)
        self.repaint()
        QApplication.processEvents()

    def drawContents(self, p: QPainter) -> None:     # (Qt calls this on every repaint)
        p.setRenderHint(QPainter.Antialiasing)
        p.setPen(QColor("#cbd5e1"))
        f = QFont()
        f.setPointSize(9)
        p.setFont(f)
        p.drawText(QRectF(32, H - 66, W - 64, 22), Qt.AlignLeft | Qt.AlignVCenter, self.text)
        p.setPen(Qt.NoPen)
        p.setBrush(QColor("#1e293b"))
        bar = QRectF(32, H - 38, W - 64, 6)
        p.drawRoundedRect(bar, 3, 3)
        p.setBrush(QColor("#2f6df6"))
        p.drawRoundedRect(QRectF(bar.x(), bar.y(), bar.width() * self.done / self.STEPS, bar.height()), 3, 3)

    def mousePressEvent(self, event) -> None:          # (a click must not close it before the window is up)
        event.ignore()


def show() -> Splash:
    s = Splash()
    s.show()
    s.raise_()
    QApplication.processEvents()
    return s

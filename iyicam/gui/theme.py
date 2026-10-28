"""The look of the program: a light and a dark theme (or the one of the system), as colours, a palette and a
style sheet. The drawing, the icons and the widgets all take their colours from the theme that is current."""
from __future__ import annotations

import tempfile
from dataclasses import dataclass
from pathlib import Path

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QGuiApplication, QIcon, QPainter, QPainterPath, QPalette, QPen, QPixmap

THEMES = ("system", "light", "dark")


@dataclass(frozen=True)
class Theme:
    name: str
    dark: bool
    bg: str             # window
    panel: str          # docks, tool bars, menus
    panel_alt: str      # headers, buttons
    field: str          # inputs and lists
    border: str
    border_strong: str
    text: str
    muted: str
    accent: str
    accent_soft: str    # a quiet highlight (checked tool, hover on selection)
    accent_text: str
    hover: str
    canvas: str         # round the sheet
    sheet: str          # the material
    sheet_shadow: str   # outside the outline of a leftover
    sheet_edge: str
    ruler: str
    ruler_tick: str
    ruler_text: str
    grid_rgb: int       # 0: dark lines on a light sheet, 255: light lines on a dark one
    icon: str           # the colour of the drawn icons
    danger: str


LIGHT = Theme(
    "light", False, bg="#f2f4f8", panel="#ffffff", panel_alt="#eceff5", field="#ffffff", border="#d9dde6",
    border_strong="#b8bfcd", text="#1c2333", muted="#667085", accent="#2f6df6", accent_soft="#dce7ff",
    accent_text="#ffffff", hover="#e4e8f0", canvas="#e4e7ee", sheet="#fffdf8", sheet_shadow="#cdd1da",
    sheet_edge="#8a8f9c", ruler="#f2f4f8", ruler_tick="#8d95a6", ruler_text="#5b6477", grid_rgb=0,
    icon="#2b3a55", danger="#d92d20")

DARK = Theme(
    "dark", True, bg="#15171c", panel="#1d2027", panel_alt="#262a33", field="#14161b", border="#323743",
    border_strong="#4a5163", text="#e6e9f0", muted="#98a2b3", accent="#4a7cf7", accent_soft="#26365f",
    accent_text="#ffffff", hover="#2c313c", canvas="#101216", sheet="#2b303b", sheet_shadow="#1a1d23",
    sheet_edge="#6b7384", ruler="#1d2027", ruler_tick="#6b7384", ruler_text="#98a2b3", grid_rgb=255,
    icon="#d5dbe8", danger="#f97066")

_current: Theme = LIGHT
_asset_dir: Path | None = None
_applied: tuple[int, str] | None = None     # (the application, theme) the style sheet was made for


def get() -> Theme:
    """The theme that is applied now."""
    return _current


def resolve(name: str) -> Theme:
    """The theme for a setting (``system`` asks the operating system, light when it cannot tell)."""
    if name == "dark":
        return DARK
    if name == "light":
        return LIGHT
    try:
        return DARK if QGuiApplication.styleHints().colorScheme() == Qt.ColorScheme.Dark else LIGHT
    except (AttributeError, RuntimeError):      # (an older Qt)
        return LIGHT


def adapt(color: QColor) -> QColor:
    """A drawing colour that can be seen on the sheet: dark ones are lightened on a dark sheet, pale ones
    darkened on a light one."""
    lum = 0.299 * color.redF() + 0.587 * color.greenF() + 0.114 * color.blueF()
    if _current.dark and lum < 0.5:
        k = min(1.0, (0.5 - lum) / max(1e-6, 1.0 - lum) + 0.1)
        return QColor.fromRgbF(color.redF() + (1 - color.redF()) * k, color.greenF() + (1 - color.greenF()) * k,
                               color.blueF() + (1 - color.blueF()) * k, color.alphaF())
    if not _current.dark and lum > 0.8:
        return QColor.fromRgbF(color.redF() * 0.55, color.greenF() * 0.55, color.blueF() * 0.55, color.alphaF())
    return color


# -- generated pictures: the arrows and the cross the style sheet points to ------------------------------------
def _assets(t: Theme) -> Path | None:
    """Folder with the arrows and the cross in the colours of the theme (written once per theme)."""
    global _asset_dir
    from ..core import paths
    for base in (paths.user_dir() / "theme", Path(tempfile.gettempdir()) / "iyicam-theme"):
        d = base / t.name
        try:
            d.mkdir(parents=True, exist_ok=True)
            _write_assets(d, t)
            _asset_dir = d
            return d
        except OSError:
            continue
    _asset_dir = None
    return None


def _glyph(draw, color: str, size: int = 16) -> QPixmap:
    pm = QPixmap(size * 2, size * 2)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing)
    p.scale(2, 2)
    p.setPen(QPen(QColor(color), 1.6, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
    draw(p)
    p.end()
    return pm


def _write_assets(d: Path, t: Theme) -> None:
    def cross(p):
        p.drawLine(QPointF(5, 5), QPointF(11, 11))
        p.drawLine(QPointF(11, 5), QPointF(5, 11))

    def down(p):
        p.drawPolyline([QPointF(4.5, 6.5), QPointF(8, 10), QPointF(11.5, 6.5)])

    def up(p):
        p.drawPolyline([QPointF(4.5, 9.5), QPointF(8, 6), QPointF(11.5, 9.5)])

    for name, draw, color in (("close", cross, t.muted), ("close_hover", cross, t.danger), ("down", down, t.muted),
                              ("up", up, t.muted)):
        if not _glyph(draw, color).save(str(d / f"{name}.png")):
            raise OSError(f"cannot write {name}.png")


# -- the palette and the style sheet ---------------------------------------------------------------------------
def palette(t: Theme) -> QPalette:
    p = QPalette()
    roles = {QPalette.Window: t.bg, QPalette.WindowText: t.text, QPalette.Base: t.field,
             QPalette.AlternateBase: t.panel_alt, QPalette.Text: t.text, QPalette.Button: t.panel_alt,
             QPalette.ButtonText: t.text, QPalette.ToolTipBase: t.panel_alt, QPalette.ToolTipText: t.text,
             QPalette.Highlight: t.accent, QPalette.HighlightedText: t.accent_text, QPalette.Link: t.accent,
             QPalette.PlaceholderText: t.muted, QPalette.BrightText: t.danger, QPalette.Light: t.panel,
             QPalette.Midlight: t.panel_alt, QPalette.Mid: t.border, QPalette.Dark: t.border_strong,
             QPalette.Shadow: "#000000"}
    for role, color in roles.items():
        p.setColor(role, QColor(color))
    for role in (QPalette.WindowText, QPalette.Text, QPalette.ButtonText):
        p.setColor(QPalette.Disabled, role, QColor(t.muted))
    p.setColor(QPalette.Disabled, QPalette.Highlight, QColor(t.border_strong))
    return p


def stylesheet(t: Theme, assets: Path | None) -> str:
    def img(name: str) -> str:
        return f"image: url({(assets / (name + '.png')).as_posix()});" if assets else ""

    return f"""
* {{ outline: 0; }}
QMainWindow, QDialog {{ background: {t.bg}; }}
QToolTip {{ background: {t.panel_alt}; color: {t.text}; border: 1px solid {t.border_strong}; padding: 4px 6px; }}
QMainWindow::separator {{ background: {t.bg}; width: 5px; height: 5px; }}
QMainWindow::separator:hover {{ background: {t.accent}; }}

QMenuBar {{ background: {t.bg}; padding: 2px 4px; }}
QMenuBar::item {{ padding: 5px 10px; background: transparent; border-radius: 5px; }}
QMenuBar::item:selected {{ background: {t.hover}; }}
QMenu {{ background: {t.panel}; border: 1px solid {t.border_strong}; padding: 5px; }}
QMenu::item {{ padding: 6px 26px 6px 22px; border-radius: 4px; }}
QMenu::item:selected {{ background: {t.accent}; color: {t.accent_text}; }}
QMenu::item:disabled {{ color: {t.muted}; background: transparent; }}
QMenu::separator {{ height: 1px; background: {t.border}; margin: 5px 8px; }}

QToolBar {{ background: {t.panel}; border: 0; spacing: 2px; padding: 4px; }}
QToolBar::separator {{ background: {t.border}; width: 1px; height: 1px; margin: 5px 6px; }}
QToolBar QToolButton {{ padding: 6px; border-radius: 7px; border: 0; background: transparent; }}
QToolBar QToolButton:hover {{ background: {t.hover}; }}
QToolBar QToolButton:checked {{ background: {t.accent_soft}; }}
QToolBar QToolButton:pressed {{ background: {t.border}; }}
QToolButton {{ border-radius: 5px; padding: 3px 5px; background: transparent; }}
QToolButton:hover {{ background: {t.hover}; }}
QToolButton:pressed, QToolButton:checked {{ background: {t.accent_soft}; }}

QDockWidget::title {{ background: {t.panel_alt}; color: {t.muted}; padding: 7px 10px; text-align: left;
    font-weight: 600; border-bottom: 1px solid {t.border}; }}
QDockWidget::close-button, QDockWidget::float-button {{ background: transparent; border-radius: 4px; padding: 1px; }}
QDockWidget::close-button:hover, QDockWidget::float-button:hover {{ background: {t.hover}; }}

QTabWidget::pane {{ border: 0; background: {t.panel}; top: 0; }}
QTabBar {{ background: {t.bg}; qproperty-drawBase: 0; }}
QTabBar::tab {{ background: transparent; color: {t.muted}; padding: 8px 14px 7px 14px; margin: 0 1px 0 0;
    border: 0; border-bottom: 2px solid transparent; min-width: 70px; }}
QTabBar::tab:hover:!selected {{ background: {t.hover}; color: {t.text}; }}
QTabBar::tab:selected {{ background: {t.panel}; color: {t.text}; border-bottom: 2px solid {t.accent}; }}
QTabBar::close-button {{ {img('close')} subcontrol-position: right; margin: 0 0 0 6px; border-radius: 4px; }}
QTabBar::close-button:hover {{ {img('close_hover')} background: {t.hover}; }}
QTabBar QToolButton {{ background: {t.bg}; border: 0; }}
QToolButton#NewTabButton {{ font-size: 17px; font-weight: 600; color: {t.muted}; padding: 0 12px; margin: 3px 6px;
    border-radius: 6px; }}
QToolButton#NewTabButton:hover {{ background: {t.hover}; color: {t.text}; }}
#DocumentTabs > QTabBar::tab {{ min-width: 90px; max-width: 260px; }}

QPushButton {{ background: {t.panel_alt}; color: {t.text}; border: 1px solid {t.border}; border-radius: 6px;
    padding: 5px 14px; min-height: 18px; }}
QPushButton:hover {{ background: {t.hover}; border-color: {t.border_strong}; }}
QPushButton:pressed {{ background: {t.border}; }}
QPushButton:default {{ background: {t.accent}; color: {t.accent_text}; border-color: {t.accent}; }}
QPushButton:default:hover {{ background: {t.accent}; border-color: {t.text}; }}
QPushButton:disabled {{ color: {t.muted}; background: {t.bg}; }}
QPushButton:checked {{ background: {t.accent_soft}; border-color: {t.accent}; }}

QLineEdit, QSpinBox, QDoubleSpinBox, QComboBox, QPlainTextEdit, QTextEdit {{ background: {t.field};
    color: {t.text}; border: 1px solid {t.border}; border-radius: 6px; padding: 3px 6px;
    selection-background-color: {t.accent}; selection-color: {t.accent_text}; }}
QLineEdit:hover, QSpinBox:hover, QDoubleSpinBox:hover, QComboBox:hover {{ border-color: {t.border_strong}; }}
QLineEdit:focus, QSpinBox:focus, QDoubleSpinBox:focus, QComboBox:focus, QPlainTextEdit:focus, QTextEdit:focus {{
    border-color: {t.accent}; }}
QLineEdit:disabled, QSpinBox:disabled, QDoubleSpinBox:disabled, QComboBox:disabled {{ color: {t.muted};
    background: {t.bg}; }}
QComboBox {{ padding-right: 22px; }}
QComboBox::drop-down {{ border: 0; width: 22px; subcontrol-origin: padding; subcontrol-position: center right; }}
QComboBox::down-arrow {{ {img('down')} width: 14px; height: 14px; }}
QComboBox QAbstractItemView {{ background: {t.panel}; border: 1px solid {t.border_strong};
    selection-background-color: {t.accent}; selection-color: {t.accent_text}; padding: 3px; outline: 0; }}
QSpinBox, QDoubleSpinBox {{ padding-right: 18px; }}
QSpinBox::up-button, QDoubleSpinBox::up-button {{ subcontrol-origin: border; subcontrol-position: top right;
    width: 18px; border: 0; border-top-right-radius: 5px; }}
QSpinBox::down-button, QDoubleSpinBox::down-button {{ subcontrol-origin: border; subcontrol-position: bottom right;
    width: 18px; border: 0; border-bottom-right-radius: 5px; }}
QSpinBox::up-button:hover, QDoubleSpinBox::up-button:hover, QSpinBox::down-button:hover,
QDoubleSpinBox::down-button:hover {{ background: {t.hover}; }}
QSpinBox::up-arrow, QDoubleSpinBox::up-arrow {{ {img('up')} width: 12px; height: 12px; }}
QSpinBox::down-arrow, QDoubleSpinBox::down-arrow {{ {img('down')} width: 12px; height: 12px; }}

QTreeView, QListView, QTableView, QTreeWidget, QListWidget, QTableWidget {{ background: {t.field};
    color: {t.text}; border: 1px solid {t.border}; border-radius: 6px; alternate-background-color: {t.panel};
    gridline-color: {t.border}; selection-background-color: {t.accent}; selection-color: {t.accent_text}; }}
QTreeView::item, QListView::item {{ padding: 3px 2px; border-radius: 3px; }}
QTreeView::item:hover, QListView::item:hover {{ background: {t.hover}; }}
QTreeView::item:selected, QListView::item:selected {{ background: {t.accent}; color: {t.accent_text}; }}
QHeaderView::section {{ background: {t.panel_alt}; color: {t.muted}; border: 0; border-bottom: 1px solid {t.border};
    border-right: 1px solid {t.border}; padding: 5px 8px; font-weight: 600; }}
QTableCornerButton::section {{ background: {t.panel_alt}; border: 0; }}

QScrollBar:vertical {{ background: transparent; width: 11px; margin: 0; }}
QScrollBar::handle:vertical {{ background: {t.border_strong}; min-height: 28px; border-radius: 4px; margin: 2px; }}
QScrollBar::handle:vertical:hover {{ background: {t.muted}; }}
QScrollBar:horizontal {{ background: transparent; height: 11px; margin: 0; }}
QScrollBar::handle:horizontal {{ background: {t.border_strong}; min-width: 28px; border-radius: 4px; margin: 2px; }}
QScrollBar::handle:horizontal:hover {{ background: {t.muted}; }}
QScrollBar::add-line, QScrollBar::sub-line {{ width: 0; height: 0; background: none; border: 0; }}
QScrollBar::add-page, QScrollBar::sub-page {{ background: none; }}

QGroupBox {{ border: 1px solid {t.border}; border-radius: 8px; margin-top: 12px; padding: 10px 8px 6px 8px; }}
QGroupBox::title {{ subcontrol-origin: margin; left: 10px; padding: 0 4px; color: {t.muted}; font-weight: 600; }}
QProgressBar {{ background: {t.panel_alt}; border: 0; border-radius: 4px; text-align: center; min-height: 8px; }}
QProgressBar::chunk {{ background: {t.accent}; border-radius: 4px; }}
QStatusBar {{ background: {t.panel}; color: {t.muted}; border-top: 1px solid {t.border}; }}
QStatusBar::item {{ border: 0; }}
QSplitter::handle {{ background: {t.bg}; }}
QLabel {{ background: transparent; }}
"""


def apply(app, name: str = "system") -> Theme:
    """Make ``name`` (system, light or dark) the look of the program."""
    global _current, _applied
    t = resolve(name if name in THEMES else "system")
    _current = t
    if _applied == (id(app), t.name) and app.styleSheet():
        return t            # (setting a style sheet again polishes every widget of the program again)
    app.setStyle("Fusion")
    app.setPalette(palette(t))
    app.setStyleSheet(stylesheet(t, _assets(t)))
    _applied = (id(app), t.name)
    return t


# -- the icon of the program ----------------------------------------------------------------------------------
def app_icon(size: int = 256) -> QIcon:
    """A router bit cutting a rounded outline: the same in every theme."""
    pm = QPixmap(size, size)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing)
    s = size / 256.0
    p.scale(s, s)
    p.setPen(Qt.NoPen)
    p.setBrush(QColor("#2f6df6"))
    p.drawRoundedRect(QRectF(8, 8, 240, 240), 54, 54)
    p.setBrush(QColor("#1d4ed8"))
    path = QPainterPath()
    path.addRoundedRect(QRectF(8, 128, 240, 120), 54, 54)
    p.setClipPath(path)
    p.setOpacity(0.35)
    p.drawRect(QRectF(8, 128, 240, 120))
    p.setClipping(False)
    p.setOpacity(1.0)
    pen = QPen(QColor("#ffffff"), 14, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin)
    p.setPen(pen)
    p.setBrush(Qt.NoBrush)
    p.drawRoundedRect(QRectF(56, 112, 144, 100), 26, 26)
    p.setPen(Qt.NoPen)
    p.setBrush(QColor("#ffffff"))
    p.drawRoundedRect(QRectF(118, 36, 20, 74), 8, 8)
    p.setBrush(QColor("#fbbf24"))
    p.drawEllipse(QPointF(128, 112), 20, 20)
    p.end()
    return QIcon(pm)

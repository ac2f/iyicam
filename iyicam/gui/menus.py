"""Context menus: shown through one function, so that a test can answer a menu instead of waiting for a click."""
from __future__ import annotations

from PySide6.QtGui import QAction
from PySide6.QtWidgets import QMenu


def show_menu(menu: QMenu, global_pos) -> QAction | None:
    """Show ``menu`` at ``global_pos`` and return the action chosen (``None``: none)."""
    return menu.exec(global_pos)

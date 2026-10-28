"""The row of tabs above the drawing: one open project (document) per tab."""
from __future__ import annotations

from PySide6.QtCore import QEvent, QPoint, Qt, Signal
from PySide6.QtWidgets import QTabWidget, QToolButton


class DocumentTabs(QTabWidget):
    """Tabs that can be closed (also with the middle button), moved and added with the + button."""
    new_requested = Signal()
    menu_requested = Signal(int, QPoint)       # tab index, place on the screen

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("DocumentTabs")
        self.setDocumentMode(True)
        self.setTabsClosable(True)
        self.setMovable(True)
        bar = self.tabBar()
        bar.setExpanding(False)
        bar.setElideMode(Qt.ElideRight)
        bar.setUsesScrollButtons(True)
        bar.setDrawBase(False)
        bar.setContextMenuPolicy(Qt.CustomContextMenu)
        bar.customContextMenuRequested.connect(lambda pos: self.menu_requested.emit(bar.tabAt(pos), bar.mapToGlobal(pos)))
        bar.installEventFilter(self)
        self.add_button = QToolButton()
        self.add_button.setObjectName("NewTabButton")
        self.add_button.setText("+")
        self.add_button.setToolTip("New project (Ctrl+N)")
        self.add_button.setAutoRaise(True)
        self.add_button.setCursor(Qt.PointingHandCursor)
        self.add_button.clicked.connect(self.new_requested)
        self.setCornerWidget(self.add_button, Qt.TopRightCorner)

    def eventFilter(self, obj, ev) -> bool:
        if obj is self.tabBar() and ev.type() == QEvent.MouseButtonRelease and ev.button() == Qt.MiddleButton:
            i = self.tabBar().tabAt(ev.position().toPoint())
            if i >= 0:
                self.tabCloseRequested.emit(i)
                return True
        return super().eventFilter(obj, ev)

    def documents(self) -> list:
        """The drawing widgets in the order of the tabs."""
        return [self.widget(i) for i in range(self.count())]

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QButtonGroup,
    QFrame,
    QLabel,
    QPushButton,
    QVBoxLayout,
)


class NavButton(QPushButton):
    def __init__(self, text: str, icon_text: str = ""):
        super().__init__()
        self.setText(f"  {icon_text}   {text}" if icon_text else text)
        self.setCheckable(True)
        self.setCursor(Qt.PointingHandCursor)
        self.setObjectName("NavButton")
        self.setMinimumHeight(40)


class Sidebar(QFrame):
    """左侧导航。currentChanged(int) 与 QStackedWidget 索引一一对应。"""

    currentChanged = Signal(int)

    def __init__(self):
        super().__init__()
        self.setObjectName("Sidebar")
        self.setFixedWidth(200)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 16, 12, 16)
        layout.setSpacing(6)

        title = QLabel("Verthandi")
        title.setObjectName("SidebarTitle")
        layout.addWidget(title)

        self._group = QButtonGroup(self)
        self._group.setExclusive(True)
        self._buttons: list[NavButton] = []

        self._add_nav(layout, "主页面", "🏠")
        self._add_nav(layout, "设置", "⚙")

        layout.addStretch(1)

        self._add_nav(layout, "关于", "ℹ")

        self._group.idClicked.connect(self.currentChanged.emit)

    def _add_nav(self, layout: QVBoxLayout, text: str, icon: str) -> None:
        btn = NavButton(text, icon)
        idx = len(self._buttons)
        self._group.addButton(btn, idx)
        layout.addWidget(btn)
        self._buttons.append(btn)

    def set_current(self, index: int) -> None:
        if 0 <= index < len(self._buttons):
            self._buttons[index].setChecked(True)
            self.currentChanged.emit(index)

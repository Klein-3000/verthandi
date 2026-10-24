from __future__ import annotations

from pathlib import Path

from PySide6.QtWidgets import QLabel, QVBoxLayout, QWidget


class SettingsPage(QWidget):
    """设置页骨架。

    后续要加：
    - 必备属性：游戏目录、hoshimi 版本、home、runtime、端口
    - 服务运行时 setEnabled(False)
    """

    def __init__(self, base_dir: Path):
        super().__init__()
        self.base_dir = base_dir

        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(12)

        title = QLabel('设置')
        title.setObjectName('PageTitle')
        layout.addWidget(title)

        hint = QLabel('必备属性设置将在此处实现。')
        hint.setObjectName('MutedText')
        layout.addWidget(hint)

        layout.addStretch(1)

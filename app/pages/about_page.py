from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QLabel, QVBoxLayout, QWidget

from app import __version__


class AboutPage(QWidget):
    """关于页骨架。

    后续要加：
    - 项目简介、版本、许可证、致谢（hoshimi / PySide6）
    - 打开仓库链接
    """

    def __init__(self, base_dir: Path):
        super().__init__()
        self.base_dir = base_dir

        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(12)

        title = QLabel('关于')
        title.setObjectName('PageTitle')
        layout.addWidget(title)

        name = QLabel('Verthandi')
        name.setObjectName('AboutName')
        layout.addWidget(name)

        version = QLabel(f'版本 {__version__}')
        version.setObjectName('MutedText')
        layout.addWidget(version)

        desc = QLabel('Hoshimi 服务端的桌面控制台。')
        desc.setObjectName('MutedText')
        desc.setWordWrap(True)
        layout.addWidget(desc)

        layout.addStretch(1)
        footer = QLabel('本项目与游戏官方无关联。')
        footer.setObjectName('MutedText')
        footer.setAlignment(Qt.AlignLeft)
        layout.addWidget(footer)

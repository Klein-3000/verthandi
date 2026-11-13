"""选择战斗布局备份文件的小对话框。"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QVBoxLayout,
)
from qfluentwidgets import PrimaryPushButton, PushButton


class BattleBackupDialog(QDialog):
    """列出 data/client-settings 下的备份（按时间倒序），单选还原。"""

    def __init__(self, backups: list[Path], parent=None) -> None:
        super().__init__(parent)
        self._backups = backups
        self._selected: Path | None = None

        self.setWindowTitle("选择要还原的备份")
        self.resize(520, 360)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 16, 20, 16)
        layout.setSpacing(8)

        layout.addWidget(QLabel(f"共 {len(backups)} 份备份，选择一份还原："))

        self.list = QListWidget(self)
        self.list.setUniformItemSizes(True)
        self.list.itemDoubleClicked.connect(lambda _item: self._accept())
        for path in backups:
            mtime = datetime.fromtimestamp(path.stat().st_mtime).strftime(
                "%Y-%m-%d %H:%M:%S"
            )
            item = QListWidgetItem(f"{path.name}　·　{mtime}")
            item.setData(Qt.UserRole, path)
            self.list.addItem(item)
        if self.list.count():
            self.list.setCurrentRow(0)
        layout.addWidget(self.list, 1)

        buttons = QHBoxLayout()
        buttons.addStretch(1)
        cancel_btn = PushButton("取消")
        cancel_btn.clicked.connect(self.reject)
        buttons.addWidget(cancel_btn)
        self.restore_btn = PrimaryPushButton("还原")
        self.restore_btn.setEnabled(bool(backups))
        self.restore_btn.clicked.connect(self._accept)
        buttons.addWidget(self.restore_btn)
        layout.addLayout(buttons)

    def _accept(self) -> None:
        item = self.list.currentItem()
        if item is not None:
            self._selected = item.data(Qt.UserRole)
            self.accept()

    def selected(self) -> Path | None:
        return self._selected

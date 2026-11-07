"""手输 hoshimi 版本号的弹窗。

替代原 QInputDialog.getText 的交互：
- 输入时实时校验，格式合法前确定按钮禁用；
- 错误原因直接显示在输入框下方，不弹第二个框；
- 可预填从文件名提取的候选版本，用户确认而非从零手输。
"""

from __future__ import annotations

import re

from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QLabel,
    QLineEdit,
    QVBoxLayout,
)

# 放宽格式：多段数字（0.0.5 / 0.0.10），可选字母数字后缀（0.0.6-hotfix）。
# 仅允许数字、点、短横线、下划线和字母——它同时会用作 bin/ 下的目录名。
VERSION_RE = re.compile(r"^\d+(?:\.\d+)*(?:[-_][0-9A-Za-z]+)?$")


class VersionInputDialog(QDialog):
    def __init__(
        self,
        parent=None,
        *,
        title: str = "输入 hoshimi 版本号",
        label: str = "未能自动检测版本号，请手动输入：",
        initial: str = "",
    ):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setModal(True)
        self.setMinimumWidth(380)

        lay = QVBoxLayout(self)
        lay.setSpacing(8)

        tip = QLabel(label)
        tip.setWordWrap(True)
        lay.addWidget(tip)

        self.edit = QLineEdit(initial)
        self.edit.setPlaceholderText("0.0.5")
        lay.addWidget(self.edit)

        self.error = QLabel("")
        self.error.setWordWrap(True)
        self.error.setStyleSheet("color: #d9534f;")
        lay.addWidget(self.error)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        self._ok = buttons.button(QDialogButtonBox.Ok)
        lay.addWidget(buttons)

        self.edit.textChanged.connect(self._validate)
        self._validate()

    def _validate(self) -> None:
        text = self.edit.text().strip()
        if not text:
            self.error.setText("请输入版本号")
            ok = False
        elif not VERSION_RE.match(text):
            self.error.setText("版本号格式不正确。允许例如：0.0.5、0.0.6-hotfix")
            ok = False
        else:
            self.error.setText("")
            ok = True
        self._ok.setEnabled(ok)

    def value(self) -> str:
        return self.edit.text().strip()

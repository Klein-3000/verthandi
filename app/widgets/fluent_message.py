"""Fluent 风格消息框助手。

统一替代项目里散落的 QMessageBox：
- 底座为 qfluentwidgets.MessageBoxBase（带遮罩的对话框）；
- 提供 information / warning / critical / question 四个静态方法，
  签名与 QMessageBox 静态方法保持一致，方便逐处替换；
- 自定义按钮文案、危险操作确认等场景用 ask_confirm。
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import QApplication, QWidget
from qfluentwidgets import (
    BodyLabel,
    MessageBoxBase,
    PrimaryPushButton,
    PushButton,
    SubtitleLabel,
)


def _ensure_parent(parent: QWidget | None) -> QWidget | None:
    """MaskDialogBase 需要父窗口作为遮罩宿主；None 时回退到当前活动窗口。"""
    if parent is None:
        return QApplication.activeWindow()
    return parent


class MessageDialog(MessageBoxBase):
    """标题 + 正文 + 确定按钮（可选取消）的消息对话框。"""

    def __init__(
        self,
        parent: QWidget | None,
        title: str,
        text: str,
        *,
        yes_text: str = "确定",
        cancel_text: str | None = "取消",
        danger: bool = False,
        details: str | None = None,
    ):
        parent = _ensure_parent(parent)
        super().__init__(parent=parent)

        self.titleLabel = SubtitleLabel(title, self)
        self.contentLabel = BodyLabel(text, self)
        self.contentLabel.setWordWrap(True)
        self.contentLabel.setTextInteractionFlags(Qt.TextSelectableByMouse)

        self.viewLayout.addWidget(self.titleLabel)
        self.viewLayout.addWidget(self.contentLabel)

        if details:
            self.detailsLabel = BodyLabel(details, self)
            self.detailsLabel.setWordWrap(True)
            self.detailsLabel.setTextColor(QColor("#6b7280"))
            self.detailsLabel.setTextInteractionFlags(Qt.TextSelectableByMouse)
            self.viewLayout.addWidget(self.detailsLabel)

        self.yesButton.setText(yes_text)
        if cancel_text:
            self.cancelButton.setText(cancel_text)
            if danger:
                # 危险操作：默认焦点放在“取消”上，回车不会误删
                self.yesButton.clearFocus()
                self.cancelButton.setFocus()
                self.cancelButton.setDefault(True)
        else:
            self.hideCancelButton()

        self.widget.setMinimumWidth(420)
        self.widget.setMaximumWidth(560)


def _exec(
    parent: QWidget | None,
    title: str,
    text: str,
    *,
    yes_text: str = "确定",
    cancel_text: str | None = None,
    danger: bool = False,
    details: str | None = None,
) -> bool:
    box = MessageDialog(
        parent,
        title,
        text,
        yes_text=yes_text,
        cancel_text=cancel_text,
        danger=danger,
        details=details,
    )
    return box.exec() == 1  # QDialog.Accepted


def information(
    parent: QWidget | None, title: str, text: str, *, details: str | None = None
) -> bool:
    """信息提示，仅一个确定按钮。"""
    return _exec(parent, title, text, details=details)


def warning(parent: QWidget | None, title: str, text: str) -> bool:
    """警告提示，仅一个确定按钮。"""
    return _exec(parent, title, text)


def critical(parent: QWidget | None, title: str, text: str) -> bool:
    """错误提示，仅一个确定按钮。"""
    return _exec(parent, title, text)


def question(
    parent: QWidget | None,
    title: str,
    text: str,
    *,
    yes_text: str = "确定",
    no_text: str = "取消",
    danger: bool = False,
) -> bool:
    """二选一确认。返回 True 表示用户点了 yes 按钮。"""
    return _exec(
        parent, title, text, yes_text=yes_text, cancel_text=no_text, danger=danger
    )


# 语义别名：危险操作确认（默认焦点在“取消”，防误触回车）
ask_confirm = question


def ask_choice(
    parent: QWidget | None,
    title: str,
    text: str,
    choices: list[tuple[str, str]],
    *,
    cancel_text: str = "取消",
    default: int = 0,
) -> str | None:
    """多选一弹窗。choices 为 [(key, label), ...]，返回所选项 key；取消返回 None。

    choices 中第 default 项高亮为主题色按钮；cancel 按钮排在最后。
    """
    box = MessageBoxBase(parent=_ensure_parent(parent))

    box.titleLabel = SubtitleLabel(title, box)
    box.contentLabel = BodyLabel(text, box)
    box.contentLabel.setWordWrap(True)
    box.contentLabel.setTextInteractionFlags(Qt.TextSelectableByMouse)
    box.viewLayout.addWidget(box.titleLabel)
    box.viewLayout.addWidget(box.contentLabel)

    box.yesButton.hide()
    box.cancelButton.hide()

    result: list[str] = []

    def _make(key: str):
        def _on_click() -> None:
            result.append(key)
            box.accept()

        return _on_click

    for i, (key, label) in enumerate(choices):
        btn: PushButton = (
            PrimaryPushButton(label) if i == default else PushButton(label)
        )
        btn.setMinimumWidth(100)
        btn.clicked.connect(_make(key))
        box.buttonLayout.addWidget(btn, 1, Qt.AlignVCenter)

    cancel_btn = PushButton(cancel_text)
    cancel_btn.clicked.connect(box.reject)
    box.buttonLayout.addWidget(cancel_btn, 1, Qt.AlignVCenter)

    box.widget.setMinimumWidth(440)
    box.exec()
    return result[0] if result else None

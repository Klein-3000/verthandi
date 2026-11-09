"""Hoshimi 使用统计询问的转达弹窗。

0.0.8 起 hoshimi 在首次启动时会询问是否启用它自己的使用统计（telemetry）。
Verthandi 隐藏了 hoshimi 的控制台，用户无法在那里作答，因此由本弹窗代为转达。

注意：这是 hoshimi（第三方服务端程序）的功能，与 Verthandi 无关；
弹窗文案必须明确这一点。答复由 hoshimi 持久化到 home/config.json
（telemetry_enabled / telemetry_consent），一生只问一次。
"""

from __future__ import annotations

from PySide6.QtGui import QColor
from PySide6.QtWidgets import QWidget
from qfluentwidgets import BodyLabel, MessageBoxBase, SubtitleLabel

# 收集范围摘自 hoshimi 0.0.8 启动时的原生说明
_COLLECTED = (
    "Hoshimi 收集的内容（来自 Hoshimi 自己的说明）：\n"
    "• 项目专属 HWID 哈希（由 SMBIOS UUID 派生，可关联同一设备；不含原始硬件编号）\n"
    "• 启动会话时间、服务端与 Go 版本、Windows 版本与构建号\n"
    "• CPU 架构与逻辑核心数、启动结果与耗时、失败阶段、运行时长\n"
    "• 服务进程内存占用与 goroutine 数量（运行中每 6 小时汇总一次）\n"
    "\n"
    "明确不上传：账号、UID、存档、聊天、图片、完整日志、本机路径、"
    "计算机名或用户名。\n"
    "数据发送至第三方 Umami Cloud；统计失败不影响游戏。"
)

_AFTER_NOTE = (
    "你的选择由 Hoshimi 记录，之后不会再询问。\n"
    "如需更改，可编辑 home/config.json：将 telemetry_enabled 设为 false 关闭。"
)


class TelemetryDialog(MessageBoxBase):
    def __init__(self, parent: QWidget | None = None):
        if parent is None:
            from PySide6.QtWidgets import QApplication

            parent = QApplication.activeWindow()
        super().__init__(parent=parent)

        self.titleLabel = SubtitleLabel("Hoshimi 使用统计询问", self)
        self.viewLayout.addWidget(self.titleLabel)

        intro = BodyLabel(
            "Hoshimi 询问是否启用「使用统计」。\n\n"
            "这是 Hoshimi（第三方服务端程序）自身的遥测功能，"
            "与 Verthandi 无关。\n"
            "是否启用由你决定，Verthandi 仅代为转达你的选择。",
            self,
        )
        intro.setWordWrap(True)
        self.viewLayout.addWidget(intro)

        detail = BodyLabel(_COLLECTED, self)
        detail.setWordWrap(True)
        detail.setTextColor(QColor("#6b7280"))
        self.viewLayout.addWidget(detail)

        after = BodyLabel(_AFTER_NOTE, self)
        after.setWordWrap(True)
        self.viewLayout.addWidget(after)

        self.yesButton.setText("同意发送")
        self.cancelButton.setText("拒绝")
        # 默认拒绝：焦点与回车都落在“拒绝”上
        self.yesButton.clearFocus()
        self.cancelButton.setFocus()
        self.cancelButton.setDefault(True)

        self.widget.setMinimumWidth(480)
        self.widget.setMaximumWidth(620)


def ask_telemetry_consent(parent: QWidget | None) -> bool:
    """转达 hoshimi 的使用统计询问，返回用户是否同意。默认拒绝。"""
    dialog = TelemetryDialog(parent)
    return dialog.exec() == 1  # QDialog.Accepted

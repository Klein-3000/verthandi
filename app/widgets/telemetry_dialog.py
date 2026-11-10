"""Hoshimi 使用统计（telemetry）的转达弹窗。

0.0.8 起 hoshimi 会在首次启动时询问是否启用它自己的使用统计，提示以
`[y/N]：` 结尾且没有换行，并且阻塞在 stdin 上等待作答。Verthandi 隐藏了
控制台，用户无法在那里回答，因此由本弹窗代为转达，答案由
`app.core.telemetry_config` 写进 home/config.json（hoshimi 见键即不问）。

注意：这是 hoshimi（第三方服务端程序）的功能，与 Verthandi 无关；
文案必须让用户一眼看出主体是谁。
"""

from __future__ import annotations

from PySide6.QtGui import QColor
from PySide6.QtWidgets import QApplication, QWidget
from qfluentwidgets import BodyLabel, CheckBox, MessageBoxBase, SubtitleLabel

# 收集范围摘自 hoshimi 0.0.8 启动时的原生说明
_COLLECTED = (
    "Hoshimi 收集的内容（来自 Hoshimi 自己的说明）：\n"
    "• 项目专属 HWID 哈希（由 SMBIOS UUID 派生，可关联同一设备；不含原始硬件编号）\n"
    "• 启动会话时间、服务端与 Go 版本、Windows 版本与构建号\n"
    "• CPU 架构与逻辑核心数、启动结果与耗时、失败阶段、运行时长\n"
    "• 服务进程内存占用与 goroutine 数量（运行中每 6 小时汇总一次）\n"
    "明确不上传：账号、UID、存档、聊天、图片、完整日志、本机路径、计算机名或用户名。\n"
    "数据发送至第三方 Umami Cloud；统计失败不影响游戏。"
)


class TelemetryDialog(MessageBoxBase):
    """两种用法：ask（首次询问，带"记住"）与 confirm（设置页开启前确认）。"""

    def __init__(
        self,
        parent: QWidget | None = None,
        version: str = "",
        current: bool | None = None,
        confirm_only: bool = False,
    ):
        if parent is None:
            parent = QApplication.activeWindow()
        super().__init__(parent=parent)

        ver = f" {version}" if version else ""
        self.titleLabel = SubtitleLabel(f"Hoshimi{ver} 使用统计", self)
        self.viewLayout.addWidget(self.titleLabel)

        intro = BodyLabel(
            f"hoshimi{ver} 请求启用「使用统计」。\n\n"
            "这是 hoshimi（第三方服务端程序）自身的遥测功能，与 Verthandi 无关；"
            "Verthandi 只是代它把选择写进配置文件。",
            self,
        )
        intro.setWordWrap(True)
        self.viewLayout.addWidget(intro)

        detail = BodyLabel(_COLLECTED, self)
        detail.setWordWrap(True)
        detail.setTextColor(QColor("#6b7280"))
        self.viewLayout.addWidget(detail)

        if confirm_only:
            note = BodyLabel(
                "开启后会立即写入配置文件，从下次启动服务开始生效；"
                "随时可以在设置页关闭。",
                self,
            )
        elif current is not None:
            note = BodyLabel(
                f"当前状态：{'已开启' if current else '已关闭'}。"
                "上次没有选择「记住」，所以这次再确认一次。",
                self,
            )
        else:
            note = BodyLabel(
                "关闭不影响游戏，也不影响 Verthandi 的任何功能；"
                "选择会写入 hoshimi 的配置文件，之后不再询问。",
                self,
            )
        note.setWordWrap(True)
        self.viewLayout.addWidget(note)

        self.remember_checkbox: CheckBox | None = None
        if not confirm_only:
            self.remember_checkbox = CheckBox("记住我的选择，不再询问", self)
            self.remember_checkbox.setChecked(True)
            self.viewLayout.addWidget(self.remember_checkbox)

        self.yesButton.setText("允许发送")
        self.cancelButton.setText("保持关闭（推荐）")
        # 默认拒绝：焦点与回车都落在「保持关闭」
        self.yesButton.clearFocus()
        self.cancelButton.setFocus()
        self.cancelButton.setDefault(True)

        self.widget.setMinimumWidth(480)
        self.widget.setMaximumWidth(620)

    def remember(self) -> bool:
        return self.remember_checkbox is None or self.remember_checkbox.isChecked()


def ask_telemetry_consent(
    parent: QWidget | None, version: str = "", current: bool | None = None
) -> tuple[bool, bool]:
    """转达询问，返回 (是否允许, 是否记住)。

    关闭窗口等同于「保持关闭」——绝不能留下"没答复"，否则 hoshimi 会阻塞。
    """
    dialog = TelemetryDialog(parent, version, current)
    accepted = dialog.exec() == 1  # QDialog.Accepted
    return accepted, dialog.remember()


def confirm_enable_telemetry(parent: QWidget | None, version: str = "") -> bool:
    """设置页打开开关前的二次确认（涉及 HWID 哈希，值得停一下）。"""
    dialog = TelemetryDialog(parent, version, confirm_only=True)
    return dialog.exec() == 1

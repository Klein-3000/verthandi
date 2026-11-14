"""初始化（prepare）前的选项弹窗。

比普通确认框多两个选项，对应 hoshimi 0.0.10 起的「无陪伴服账号」路径：

- ``--download-missing``：无需登录官服，从官方 CDN 补齐缺失资源后再提取运行文件；
- ``--all``：连同全部语言的语音资源一起下载（从属于上一项）。

两个开关默认都关闭：常规用户走原有流程即可，只有「客户端没登录过官服、
本地资源不全」的用户才需要补齐，而这条路会联网下载、并向游戏目录写入文件，
代价明显更大。
"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import QApplication, QFrame, QHBoxLayout, QWidget
from qfluentwidgets import (
    BodyLabel,
    CheckBox,
    MessageBoxBase,
    SubtitleLabel,
    isDarkTheme,
)

_MUTED = "#6b7280"


class PrepareOptionsDialog(MessageBoxBase):
    """初始化确认 + 补资源选项。exec() 为真表示开始初始化。"""

    def __init__(
        self,
        parent: QWidget | None = None,
        *,
        client_dir: Path,
        version: str,
        runtime_dir: Path,
        allow_download_missing: bool,
    ):
        if parent is None:
            parent = QApplication.activeWindow()
        super().__init__(parent=parent)

        self._allow = allow_download_missing

        title = SubtitleLabel("初始化运行文件", self)
        self.viewLayout.addWidget(title)

        intro = BodyLabel(
            "初始化将从客户端提取运行文件，期间请勿关闭 Verthandi。\n\n"
            f"游戏目录：{client_dir}\n"
            f"hoshimi 版本：{version}\n"
            f"运行文件：{runtime_dir}",
            self,
        )
        intro.setWordWrap(True)
        intro.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.viewLayout.addWidget(intro)

        self.time_hint = BodyLabel("预计 1–2 分钟，取决于客户端大小。", self)
        self.time_hint.setWordWrap(True)
        self.time_hint.setTextColor(QColor(_MUTED))
        self.viewLayout.addWidget(self.time_hint)

        note = BodyLabel(
            "初始化先写入临时目录，完成后再整体替换；失败或取消不会影响现有运行文件。",
            self,
        )
        note.setWordWrap(True)
        note.setTextColor(QColor(_MUTED))
        self.viewLayout.addWidget(note)

        # 正文与选项之间一条细线，两部分在视觉上分开
        line = QFrame(self)
        line.setFixedHeight(1)
        color = "rgba(255,255,255,55)" if isDarkTheme() else "rgba(0,0,0,36)"
        line.setStyleSheet(f"border: none; background-color: {color};")
        self.viewLayout.addWidget(line)

        self.dl_check: CheckBox | None = None
        self.all_check: CheckBox | None = None
        if allow_download_missing:
            self.dl_check = CheckBox("我没有登录过官服，从官方 CDN 补齐缺失资源", self)
            self.dl_check.toggled.connect(self._on_download_toggled)
            self.viewLayout.addWidget(self.dl_check)

            detail = BodyLabel(
                "会下载缺失的脚本、剧情等资源（数百 MB 到数 GB），并在游戏目录里"
                "写入资源清单与下载的文件；需要联网，耗时取决于网速。"
                "基础客户端本身仍需已安装完整，本选项只补资源。",
                self,
            )
            detail.setWordWrap(True)
            detail.setTextColor(QColor(_MUTED))
            self.viewLayout.addWidget(detail)

            row = QWidget(self)
            row_layout = QHBoxLayout(row)
            row_layout.setContentsMargins(24, 0, 0, 0)
            self.all_check = CheckBox("同时下载全部语言的语音资源（体积最大）", row)
            self.all_check.setEnabled(False)
            row_layout.addWidget(self.all_check)
            self.viewLayout.addWidget(row)

        self.yesButton.setText("开始初始化")
        self.cancelButton.setText("取消")
        self.widget.setMinimumWidth(540)
        self.widget.setMaximumWidth(720)

    def _on_download_toggled(self, checked: bool) -> None:
        if self.all_check is not None:
            self.all_check.setEnabled(checked)
            if not checked:
                self.all_check.setChecked(False)
        if checked:
            self.time_hint.setText(
                "补齐资源需要联网下载，可能从几分钟到几十分钟，取决于网速与所选范围。"
            )
        else:
            self.time_hint.setText("预计 1–2 分钟，取决于客户端大小。")

    def download_missing(self) -> bool:
        return self.dl_check is not None and self.dl_check.isChecked()

    def download_all(self) -> bool:
        return self.download_missing() and (
            self.all_check is not None and self.all_check.isChecked()
        )

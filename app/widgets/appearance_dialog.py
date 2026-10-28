from __future__ import annotations

import sys
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
)

from app.core import game_config
from app.widgets.capsule_switch import CapsuleSwitch


class AppearanceDialog(QDialog):
    def __init__(self, base_dir: Path, parent=None):
        super().__init__(parent)
        self.base_dir = base_dir

        self._original_state = False  # False=普通, True=清凉
        self._current_selection = False
        self._config_loaded = False

        self.setWindowTitle("修改角色外观")
        self.setMinimumSize(560, 560)
        self.setModal(True)

        self._build_ui()

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(16)

        # 顶部：当前生效状态（胶囊 Tag 风格）
        status_layout = QHBoxLayout()
        status_layout.setSpacing(8)

        status_prefix = QLabel("当前生效状态:")
        status_prefix.setStyleSheet("color: #6b7280; font-size: 14px;")
        status_layout.addWidget(status_prefix)

        self.status_tag = QLabel("加载中...")
        # 胶囊 Tag 样式
        self.status_tag.setStyleSheet("""
            QLabel {
                background-color: #e0f2fe;
                color: #0369a1;
                border-radius: 12px;
                padding: 4px 12px;
                font-weight: bold;
                font-size: 13px;
            }
        """)
        status_layout.addWidget(self.status_tag)

        status_layout.addStretch(1)  # 把帮助按钮推到最右边

        # 新增：帮助按钮（圆圈带 ?）
        self.help_btn = QPushButton("?")
        self.help_btn.setCursor(Qt.PointingHandCursor)
        self.help_btn.setFixedSize(24, 24)  # 固定大小，保证是正圆
        self.help_btn.setStyleSheet("""
            QPushButton {
                background-color: #f3f4f6;
                color: #4b5563;
                border: 1px solid #d1d5db;
                border-radius: 12px; /* 半径为宽高的一半，即为正圆 */
                font-weight: bold;
                font-size: 14px;
            }
            QPushButton:hover {
                background-color: #e5e7eb;
                color: #111827;
            }
        """)
        self.help_btn.clicked.connect(self._show_help)
        status_layout.addWidget(self.help_btn)

        layout.addLayout(status_layout)
        # 胶囊切换组件
        switch_layout = QHBoxLayout()
        self.capsule = CapsuleSwitch(["普通外观", "清凉外观"])
        self.capsule.setFixedWidth(260)  # 控制宽度，让字不会太挤
        self.capsule.currentIndexChanged.connect(self._on_switch_changed)
        switch_layout.addStretch(1)
        switch_layout.addWidget(self.capsule)
        switch_layout.addStretch(1)
        layout.addLayout(switch_layout)

        # 图片预览区
        img_layout = QHBoxLayout()
        img_layout.setSpacing(30)

        # 陵光
        v1 = QVBoxLayout()
        self.img1_label = QLabel()
        self.img1_label.setFixedSize(200, 350)
        self.img1_label.setAlignment(Qt.AlignCenter)
        self.img1_label.setStyleSheet(
            "background-color: #f3f4f6; border-radius: 8px; border: 1px solid #e5e7eb;"
        )
        v1.addWidget(self.img1_label)
        lbl1 = QLabel("陵光")
        lbl1.setAlignment(Qt.AlignCenter)
        lbl1.setStyleSheet("font-weight: bold; color: #374151;")
        v1.addWidget(lbl1)
        img_layout.addLayout(v1)

        # 帕尔瓦蒂
        v2 = QVBoxLayout()
        self.img2_label = QLabel()
        self.img2_label.setFixedSize(200, 350)
        self.img2_label.setAlignment(Qt.AlignCenter)
        self.img2_label.setStyleSheet(
            "background-color: #f3f4f6; border-radius: 8px; border: 1px solid #e5e7eb;"
        )
        v2.addWidget(self.img2_label)
        lbl2 = QLabel("帕尔瓦蒂")
        lbl2.setAlignment(Qt.AlignCenter)
        lbl2.setStyleSheet("font-weight: bold; color: #374151;")
        v2.addWidget(lbl2)
        img_layout.addLayout(v2)

        img_layout.addStretch(1)
        layout.addLayout(img_layout)
        layout.addStretch(1)

        # 底部操作
        bottom = QHBoxLayout()
        bottom.addStretch(1)

        self.btn_close = QPushButton("关闭")
        self.btn_close.setObjectName("ToolbarButton")
        self.btn_close.clicked.connect(self.reject)
        bottom.addWidget(self.btn_close)

        self.btn_save = QPushButton("保存并应用")
        self.btn_save.setObjectName("ToolbarButton")
        self.btn_save.setProperty("variant", "switch")  # 蓝色焦点按钮
        self.btn_save.setEnabled(False)
        self.btn_save.clicked.connect(self._on_save)
        bottom.addWidget(self.btn_save)

        layout.addLayout(bottom)

    def showEvent(self, event):
        """窗口真正显示后再加载配置和滑块状态，防止宽高未确定导致滑块异常"""
        super().showEvent(event)
        if not self._config_loaded:
            self._load_current_config()
            self._config_loaded = True

    def _get_image_path(self, filename: str) -> Path:
        if getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"):
            return Path(sys._MEIPASS) / "app" / "resources" / filename
        return self.base_dir / "app" / "resources" / filename

    def _update_preview(self, is_cool: bool):
        prefix = "t" if is_cool else "f"

        pix1 = QPixmap(str(self._get_image_path(f"{prefix}1.png")))
        if pix1.isNull():
            self.img1_label.setText("图片缺失")
        else:
            self.img1_label.setPixmap(
                pix1.scaled(200, 350, Qt.IgnoreAspectRatio, Qt.SmoothTransformation)
            )

        pix2 = QPixmap(str(self._get_image_path(f"{prefix}2.png")))
        if pix2.isNull():
            self.img2_label.setText("图片缺失")
        else:
            self.img2_label.setPixmap(
                pix2.scaled(200, 350, Qt.IgnoreAspectRatio, Qt.SmoothTransformation)
            )

    def _load_current_config(self):
        config = game_config.read_config()

        if config is None:
            self.status_tag.setText("未找到配置文件")
            self.status_tag.setStyleSheet("""
                QLabel {
                    background-color: #fee2e2;
                    color: #b91c1c;
                    border-radius: 12px;
                    padding: 4px 12px;
                    font-weight: bold;
                    font-size: 13px;
                }
            """)
            self.capsule.setEnabled(False)
            return
        else:
            self.capsule.setEnabled(True)

        self._original_state = config.get("isLocalization") is True
        self._current_selection = self._original_state

        # 更新状态胶囊
        self._update_status_tag(self._original_state)

        # 更新组件状态（此时窗口已有尺寸，滑块能正确计算位置）
        target_index = 1 if self._original_state else 0
        self.capsule.setCurrentIndex(target_index, animate=False)

        self._update_preview(self._original_state)
        self._update_save_button()

    def _update_status_tag(self, is_cool: bool):
        state_text = "清凉外观" if is_cool else "普通外观"
        self.status_tag.setText(state_text)
        self.status_tag.setStyleSheet("""
            QLabel {
                background-color: #e0f2fe;
                color: #0369a1;
                border-radius: 12px;
                padding: 4px 12px;
                font-weight: bold;
                font-size: 13px;
            }
        """)

    def _on_switch_changed(self, index: int):
        self._current_selection = index == 1
        self._update_preview(self._current_selection)
        self._update_save_button()

    def _update_save_button(self):
        has_changes = self._current_selection != self._original_state
        self.btn_save.setEnabled(has_changes)

    def _on_save(self):
        is_cool = self._current_selection
        success, err_msg = game_config.update_localization(is_cool)

        if success:
            self._original_state = is_cool
            self._update_status_tag(self._original_state)
            self._update_save_button()
            QMessageBox.information(
                self, "成功", "外观配置已成功保存，重新进入游戏即可生效。"
            )
        else:
            QMessageBox.warning(self, "保存失败", err_msg)

    def closeEvent(self, event):
        if self._current_selection != self._original_state:
            reply = QMessageBox.question(
                self,
                "未保存的更改",
                "你有未保存的外观修改，确定要放弃修改并关闭吗？",
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.No,
            )
            if reply != QMessageBox.Yes:
                event.ignore()
                return
        super().closeEvent(event)

    def _show_help(self):
        QMessageBox.information(
            self,
            "外观修改提示",
            "修改角色外观并非对所有角色生效。\n\n"
            "例如：陵光的外观保持不变，而帕尔瓦蒂的外观会发生变化。",
        )

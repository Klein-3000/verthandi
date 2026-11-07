from __future__ import annotations

from pathlib import Path

from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from app.core import cleanup_manager as cm
from app.core.paths import Paths
from app.core.settings import Settings


class CleanupDialog(QDialog):
    def __init__(
        self,
        paths: Paths,
        settings: Settings,
        active_log_path: Path | None,
        parent=None,
    ):
        super().__init__(parent)
        self.paths = paths
        self.settings = settings
        self.active_log_path = active_log_path

        self.setWindowTitle("空间清理中心")
        self.setMinimumSize(640, 600)
        self.setModal(True)

        self._version_checks: list[tuple[QCheckBox, cm.VersionInfo]] = []
        self._runtime_checks: list[tuple[QCheckBox, cm.RuntimeEntry]] = []

        self._build_ui()
        self._load_all_data()

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(12)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        layout.addWidget(scroll, 1)

        content = QWidget()
        scroll.setWidget(content)
        self.scroll_layout = QVBoxLayout(content)
        self.scroll_layout.setContentsMargins(0, 0, 0, 0)
        self.scroll_layout.setSpacing(16)

        # 三个卡片的容器（数据加载时动态填充）
        self.version_card = QFrame()
        self.version_card.setObjectName("Card")
        self.scroll_layout.addWidget(self.version_card)

        self.runtime_card = QFrame()
        self.runtime_card.setObjectName("Card")
        self.scroll_layout.addWidget(self.runtime_card)

        self.log_card = QFrame()
        self.log_card.setObjectName("Card")
        self.scroll_layout.addWidget(self.log_card)

        self.scroll_layout.addStretch(1)

        # 底部操作区（仅保留关闭）
        bottom = QHBoxLayout()
        bottom.addStretch(1)
        btn_close = QPushButton("关闭")
        btn_close.setObjectName("ToolbarButton")
        btn_close.clicked.connect(self.reject)
        bottom.addWidget(btn_close)
        layout.addLayout(bottom)

    # ---------- 数据加载与绘制 ----------

    def _load_all_data(self):
        self._setup_version_card()
        self._setup_runtime_card()
        self._setup_log_card()

    def _setup_version_card(self):
        # 清空旧 UI
        if self.version_card.layout():
            QWidget().setLayout(self.version_card.layout())
        self._version_checks.clear()

        layout = QVBoxLayout(self.version_card)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(8)

        # 标题行
        header = QHBoxLayout()
        title = QLabel("hoshimi 版本")
        title.setObjectName("CardTitle")
        header.addWidget(title)

        self.version_stat_label = QLabel()
        self.version_stat_label.setObjectName("MutedText")
        header.addWidget(self.version_stat_label)

        header.addStretch(1)

        self.btn_clean_versions = QPushButton("清理选中")
        self.btn_clean_versions.setObjectName("ToolbarButton")
        self.btn_clean_versions.setProperty("variant", "danger")
        self.btn_clean_versions.clicked.connect(self._on_clean_versions)
        header.addWidget(self.btn_clean_versions)
        layout.addLayout(header)

        # 列表区
        versions = cm.scan_versions(self.paths, self.settings)
        list_box = QVBoxLayout()
        list_box.setSpacing(6)

        if not versions:
            lbl = QLabel("没有找到 hoshimi 版本")
            lbl.setObjectName("MutedText")
            list_box.addWidget(lbl)
        else:
            for v in versions:
                if v.is_current:
                    cb = QCheckBox(f"{v.name}  (当前使用中，不可清理)")
                    cb.setEnabled(False)
                    cb.setChecked(False)
                else:
                    cb = QCheckBox(f"{v.name}  ({cm.format_size(v.size_bytes)})")
                    cb.setChecked(True)
                    cb.stateChanged.connect(self._update_version_stat)
                    self._version_checks.append((cb, v))
                list_box.addWidget(cb)
        layout.addLayout(list_box)

        self._update_version_stat()

    def _setup_runtime_card(self):
        if self.runtime_card.layout():
            QWidget().setLayout(self.runtime_card.layout())
        self._runtime_checks.clear()

        layout = QVBoxLayout(self.runtime_card)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(8)

        header = QHBoxLayout()
        title = QLabel("runtime")
        title.setObjectName("CardTitle")
        header.addWidget(title)

        self.runtime_stat_label = QLabel()
        self.runtime_stat_label.setObjectName("MutedText")
        header.addWidget(self.runtime_stat_label)

        header.addStretch(1)

        self.btn_clean_runtimes = QPushButton("清理选中")
        self.btn_clean_runtimes.setObjectName("ToolbarButton")
        self.btn_clean_runtimes.setProperty("variant", "danger")
        self.btn_clean_runtimes.clicked.connect(self._on_clean_runtimes)
        header.addWidget(self.btn_clean_runtimes)
        layout.addLayout(header)

        entries = cm.scan_runtimes(self.paths, self.settings)
        list_box = QVBoxLayout()
        list_box.setSpacing(6)

        if not entries:
            lbl = QLabel("runtimes/ 下还没有任何运行文件")
            lbl.setObjectName("MutedText")
            list_box.addWidget(lbl)
        else:
            for e in entries:
                text = f"{e.label}  ({cm.format_size(e.size_bytes)})"
                if e.detail:
                    text += f"  ·  {e.detail}"
                if e.is_current:
                    cb = QCheckBox(f"{text}  （当前使用中）")
                    cb.setEnabled(False)
                elif not e.deletable:
                    cb = QCheckBox(text)
                    cb.setEnabled(False)
                else:
                    cb = QCheckBox(text)
                    cb.setChecked(True)
                    cb.stateChanged.connect(self._update_runtime_stat)
                    self._runtime_checks.append((cb, e))
                list_box.addWidget(cb)
        layout.addLayout(list_box)

        self._update_runtime_stat()

    def _setup_log_card(self):
        if self.log_card.layout():
            QWidget().setLayout(self.log_card.layout())

        layout = QVBoxLayout(self.log_card)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(8)

        header = QHBoxLayout()
        title = QLabel("日志文件")
        title.setObjectName("CardTitle")
        header.addWidget(title)

        self.log_stat_label = QLabel()
        self.log_stat_label.setObjectName("MutedText")
        header.addWidget(self.log_stat_label)

        header.addStretch(1)

        self.btn_clean_logs = QPushButton("清理日志")
        self.btn_clean_logs.setObjectName("ToolbarButton")
        self.btn_clean_logs.setProperty("variant", "danger")
        self.btn_clean_logs.clicked.connect(self._on_clean_logs)
        header.addWidget(self.btn_clean_logs)
        layout.addLayout(header)

        log_info = cm.scan_logs(self.paths, self.settings)
        self.log_stat_label.setText(
            f"(共 {log_info.count} 个, 约 {cm.format_size(log_info.total_size_bytes)})"
        )

        row = QHBoxLayout()
        row.addWidget(QLabel("清理策略："))
        self.log_combo = QComboBox()
        self.log_combo.addItem("不清理", 0)
        self.log_combo.addItem("清理所有历史日志", 1)
        self.log_combo.addItem("保留最近 7 天", 7)
        self.log_combo.addItem("保留最近 30 天", 30)
        self.log_combo.currentIndexChanged.connect(self._update_log_btn_state)
        row.addWidget(self.log_combo)
        row.addStretch(1)
        layout.addLayout(row)

        hint = QLabel("注：当前正在写入的日志不会被删除。")
        hint.setObjectName("MutedText")
        layout.addWidget(hint)

        self._update_log_btn_state()

    # ---------- 状态更新 ----------

    def _update_version_stat(self):
        total = sum(v.size_bytes for cb, v in self._version_checks if cb.isChecked())
        self.version_stat_label.setText(f"(选中约 {cm.format_size(total)})")
        self.btn_clean_versions.setEnabled(total > 0)

    def _update_runtime_stat(self):
        total = sum(e.size_bytes for cb, e in self._runtime_checks if cb.isChecked())
        self.runtime_stat_label.setText(f"(选中约 {cm.format_size(total)})")
        self.btn_clean_runtimes.setEnabled(total > 0)

    def _update_log_btn_state(self):
        days = self.log_combo.currentData()
        self.btn_clean_logs.setEnabled(days > 0)

    # ---------- 独立清理操作 ----------

    def _on_clean_versions(self):
        selected = [v for cb, v in self._version_checks if cb.isChecked()]
        if not selected:
            return

        box = QMessageBox(self)
        box.setIcon(QMessageBox.Warning)
        box.setWindowTitle("确认清理")
        box.setText(
            f"确定要永久删除选中的 {len(selected)} 个 hoshimi 版本吗？\n\n此操作无法恢复。"
        )
        del_btn = box.addButton("确认清理", QMessageBox.DestructiveRole)
        box.addButton("取消", QMessageBox.RejectRole)
        box.setDefaultButton(del_btn)
        box.exec()

        if box.clickedButton() is not del_btn:
            return

        cm.delete_versions(selected, self.paths, self.settings)
        self._setup_version_card()  # 重新扫描刷新
        self._setup_runtime_card()  # 同名 runtime 已被连带删除

    def _on_clean_runtimes(self):
        selected = [e for cb, e in self._runtime_checks if cb.isChecked()]
        if not selected:
            return

        box = QMessageBox(self)
        box.setIcon(QMessageBox.Warning)
        box.setWindowTitle("确认清理")
        box.setText(
            f"确定要永久删除选中的 {len(selected)} 项 runtime 吗？\n\n"
            "对应版本需要重新执行「初始化」才能启动服务。此操作无法恢复。"
        )
        del_btn = box.addButton("确认清理", QMessageBox.DestructiveRole)
        box.addButton("取消", QMessageBox.RejectRole)
        box.setDefaultButton(del_btn)
        box.exec()

        if box.clickedButton() is not del_btn:
            return

        cm.delete_runtimes(selected)
        self._setup_runtime_card()
        self._setup_version_card()

    def _on_clean_logs(self):
        days = self.log_combo.currentData()
        if days <= 0:
            return

        label = "所有历史" if days == 1 else f"超过 {days} 天的"
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Warning)
        box.setWindowTitle("确认清理")
        box.setText(f"确定要永久删除{label}日志吗？\n\n此操作无法恢复。")
        del_btn = box.addButton("确认清理", QMessageBox.DestructiveRole)
        box.addButton("取消", QMessageBox.RejectRole)
        box.setDefaultButton(del_btn)
        box.exec()

        if box.clickedButton() is not del_btn:
            return

        cm.delete_logs(self.paths, self.settings, days, self.active_log_path)
        self._setup_log_card()

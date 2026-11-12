from __future__ import annotations

import shutil
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtWidgets import (
    QApplication,
    QDialog,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)
from qfluentwidgets import (
    CheckBox,
    ComboBox,
    PushButton,
)
from qfluentwidgets import (
    FluentIcon as FIF,
)

from app.core import cleanup_manager as cm
from app.core import hoshimi_importer
from app.core.client_state import client_fingerprint
from app.core.game_process import GameWatcher
from app.core.hoshimi_finder import scan_versions
from app.core.log_bus import LogBus, LogSource, SessionLogWriter
from app.core.paths import Paths
from app.core.port_check import PortResult, check_port
from app.core.runtime_layout import (
    read_manifest,
    resolve_runtime,
    runtime_root,
)
from app.core.server_process import ServerProcess, ServerState
from app.core.settings import ClientEntry, ServerConfig, Settings, SettingsStore
from app.core.telemetry_config import (
    read_telemetry,
    supports_telemetry,
    write_telemetry,
)
from app.widgets.cleanup_dialog import CleanupDialog
from app.widgets.expand_card import ExpandCard
from app.widgets.fluent_message import (
    ask_choice,
    critical,
    information,
    question,
    warning,
)
from app.widgets.telemetry_dialog import confirm_enable_telemetry
from app.widgets.toast import show_toast
from app.widgets.version_input_dialog import VersionInputDialog

# 出厂端口，端口卡的标签文案从这里取，避免和 Settings 的默认值两处不一致
_DEFAULT_PORTS = (ServerConfig().http_port, ServerConfig().game_port)


class SettingsPage(QWidget):
    settings_changed = Signal(
        str
    )  # 'game_dir' / 'version' / 'paths' / 'ports' / 'log' / 'behavior' / 'cleanup'
    request_init = Signal()

    def __init__(
        self,
        base_dir: Path,
        paths: Paths,
        settings: Settings,
        settings_store: SettingsStore,
        log_bus: LogBus,
        log_writer: SessionLogWriter,
        server_process: ServerProcess,
        game_watcher: GameWatcher,
    ):
        super().__init__()
        self.base_dir = base_dir
        self.paths = paths
        self.settings = settings
        self.settings_store = settings_store
        self.log_bus = log_bus
        self.log_writer = log_writer
        self.server_process = server_process
        self.game_watcher = game_watcher
        # 监听到游戏运行状态变化时，更新外观按钮的状态
        self.game_watcher.running_changed.connect(self._on_game_running_changed)

        self._port_dirty = False
        self._http_check: PortResult | None = None
        self._game_check: PortResult | None = None

        # 外层布局（无外边距，用于放置滚动区域）
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        # 滚动区域
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        outer.addWidget(scroll)

        # 内容容器
        content = QWidget()
        scroll.setWidget(content)

        # 实际的内容布局（原有代码后续都往这个 layout 里塞）
        layout = QVBoxLayout(content)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(16)

        title = QLabel("设置")
        title.setObjectName("PageTitle")
        layout.addWidget(title)

        # 顶部警告条
        self.log_warning_banner = self._build_log_warning_banner()
        layout.addWidget(self.log_warning_banner)

        layout.addWidget(self._build_essential_card())
        layout.addWidget(self._build_port_card())
        layout.addWidget(self._build_appearance_card())
        layout.addWidget(self._build_behavior_card())
        layout.addWidget(self._build_logging_card())
        layout.addWidget(self._build_cleanup_card())
        layout.addStretch(1)

        self.refresh_all()
        self._apply_server_state(self.server_process.state)
        self.server_process.state_changed.connect(self._on_server_state)

    # ---------- 必备配置卡片 ----------

    def _build_essential_card(self) -> QFrame:
        card = QFrame()
        card.setObjectName("Card")
        box = QVBoxLayout(card)
        box.setContentsMargins(16, 16, 16, 16)
        box.setSpacing(10)

        title = QLabel("必备配置")
        title.setObjectName("CardTitle")
        box.addWidget(title)

        self.essential_notice = QLabel("服务运行中，停止服务后可修改")
        self.essential_notice.setObjectName("MutedText")
        self.essential_notice.setVisible(False)
        box.addWidget(self.essential_notice)

        # 游戏目录
        self.game_dir_label = QLabel("—")
        self.game_dir_label.setObjectName("StatusValue")
        self.game_dir_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.change_game_dir_btn = QPushButton("更换…")
        self.change_game_dir_btn.setObjectName("ToolbarButton")
        self.change_game_dir_btn.clicked.connect(self._on_change_game_dir)
        row = QHBoxLayout()
        k = QLabel("游戏目录")
        k.setObjectName("StatusKey")
        k.setFixedWidth(110)
        row.addWidget(k)
        row.addWidget(self.game_dir_label, 1)
        row.addWidget(self.change_game_dir_btn)
        box.addLayout(row)

        # hoshimi 版本
        self.version_combo = ComboBox()
        self.version_combo.setMinimumWidth(220)
        self.version_combo.currentIndexChanged.connect(self._on_version_changed)
        self.import_btn = QPushButton("导入…")
        self.import_btn.setObjectName("ToolbarButton")
        self.import_btn.setToolTip("从 ZIP 或 hoshimi.exe 文件导入")
        self.import_btn.clicked.connect(self._on_import_hoshimi)
        row = QHBoxLayout()
        k = QLabel("hoshimi 版本")
        k.setObjectName("StatusKey")
        k.setFixedWidth(110)
        row.addWidget(k)
        row.addWidget(self.version_combo, 1)
        row.addWidget(self.import_btn)
        box.addLayout(row)

        # home
        self.home_dir_label = QLabel("—")
        self.home_dir_label.setObjectName("StatusValue")
        self.home_dir_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.change_home_btn = QPushButton("更换…")
        self.change_home_btn.setObjectName("ToolbarButton")
        self.change_home_btn.clicked.connect(self._on_change_home_dir)
        row = QHBoxLayout()
        k = QLabel("home 目录")
        k.setObjectName("StatusKey")
        k.setFixedWidth(110)
        row.addWidget(k)
        row.addWidget(self.home_dir_label, 1)
        row.addWidget(self.change_home_btn)
        box.addLayout(row)

        # runtime
        self.runtime_dir_label = QLabel("—")
        self.runtime_dir_label.setObjectName("StatusValue")
        self.runtime_dir_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.change_runtime_btn = QPushButton("更换…")
        self.change_runtime_btn.setObjectName("ToolbarButton")
        self.change_runtime_btn.clicked.connect(self._on_change_runtime_dir)
        row = QHBoxLayout()
        k = QLabel("runtime 根目录")
        k.setObjectName("StatusKey")
        k.setFixedWidth(110)
        row.addWidget(k)
        row.addWidget(self.runtime_dir_label, 1)
        row.addWidget(self.change_runtime_btn)
        box.addLayout(row)

        return card

    def _build_port_card(self) -> ExpandCard:
        """端口（高级设置）：独立的可展开卡片（Fluent 风格）。"""
        card = ExpandCard(FIF.GLOBE, "端口（高级设置）", "修改服务端口，通常无需改动")

        body = QWidget()
        body.setStyleSheet("background: transparent;")
        layout = QVBoxLayout(body)
        layout.setContentsMargins(48, 4, 24, 16)
        layout.setSpacing(10)

        self.http_spin = QSpinBox()
        self.http_spin.setRange(1, 65535)
        self.http_spin.setValue(self.settings.server.http_port)
        self.http_spin.setMinimumWidth(120)
        self.http_spin.editingFinished.connect(self._on_http_editing_finished)
        self.http_spin.valueChanged.connect(self._on_port_value_changed)
        self.http_status = QLabel("")
        self.http_status.setStyleSheet("color: #9ca3af; background: transparent;")
        layout.addLayout(
            self._port_row(
                f"HTTP 端口（默认 {_DEFAULT_PORTS[0]}）",
                self.http_spin,
                self.http_status,
            )
        )

        self.game_spin = QSpinBox()
        self.game_spin.setRange(1, 65535)
        self.game_spin.setValue(self.settings.server.game_port)
        self.game_spin.setMinimumWidth(120)
        self.game_spin.editingFinished.connect(self._on_game_editing_finished)
        self.game_spin.valueChanged.connect(self._on_port_value_changed)
        self.game_status = QLabel("")
        self.game_status.setStyleSheet("color: #9ca3af; background: transparent;")
        layout.addLayout(
            self._port_row(
                f"游戏端口（默认 {_DEFAULT_PORTS[1]}）",
                self.game_spin,
                self.game_status,
            )
        )

        hint = QLabel(
            "游戏端口同时使用 TCP 和 UDP。修改后需要重新放行防火墙并更新玩家转发规则。"
        )
        hint.setStyleSheet("color: #9ca3af; background: transparent;")
        hint.setWordWrap(True)
        layout.addWidget(hint)

        action_row = QHBoxLayout()
        action_row.addStretch(1)
        self.apply_btn = PushButton("应用")
        self.apply_btn.setEnabled(False)
        self.apply_btn.clicked.connect(self._on_apply_ports)
        action_row.addWidget(self.apply_btn)
        layout.addLayout(action_row)

        card.addGroupWidget(body)
        return card

    def _port_row(self, label: str, spin: QSpinBox, status: QLabel) -> QHBoxLayout:
        row = QHBoxLayout()
        key = QLabel(label)
        # 展开卡自带样式会把 QLabel 刷成黑色，这里内联指定灰色
        key.setStyleSheet("color: #6b7280; background: transparent;")
        key.setFixedWidth(195)
        row.addWidget(key)
        row.addWidget(spin)
        row.addWidget(status, 1)
        return row

    # ---------- 修改角色外观 ----------

    def _build_appearance_card(self) -> QFrame:
        card = QFrame()
        card.setObjectName("Card")
        box = QVBoxLayout(card)
        box.setContentsMargins(16, 16, 16, 16)
        box.setSpacing(8)

        title = QLabel("角色外观")
        title.setObjectName("CardTitle")
        box.addWidget(title)

        row = QHBoxLayout()
        hint = QLabel("调整游戏内的角色外观（陵光 / 帕尔瓦蒂）。")
        hint.setObjectName("MutedText")
        row.addWidget(hint)
        row.addStretch(1)

        self.appearance_btn = QPushButton("修改外观")
        self.appearance_btn.setObjectName("ToolbarButton")
        self.appearance_btn.setProperty("variant", "start")  # 默认绿色
        self.appearance_btn.clicked.connect(self._on_open_appearance)
        row.addWidget(self.appearance_btn)

        box.addLayout(row)

        # 初始化一下按钮状态（以防打开设置页时游戏已经在运行）
        self._apply_appearance_btn_state(self.game_watcher.running)

        return card

    def _on_open_appearance(self) -> None:
        from app.widgets.appearance_dialog import AppearanceDialog

        dlg = AppearanceDialog(self.base_dir, self)
        dlg.exec()

    def _on_game_running_changed(self, running: bool) -> None:
        self._apply_appearance_btn_state(running)

    def _apply_appearance_btn_state(self, running: bool) -> None:
        if running:
            self.appearance_btn.setText("游戏运行中")
            self.appearance_btn.setEnabled(False)
            self.appearance_btn.setToolTip("请先退出游戏")
            self.appearance_btn.setProperty("variant", "busy")  # 变灰
        else:
            self.appearance_btn.setText("修改外观")
            self.appearance_btn.setEnabled(True)
            self.appearance_btn.setToolTip("")
            self.appearance_btn.setProperty("variant", "start")  # 变绿

        self.appearance_btn.style().unpolish(self.appearance_btn)
        self.appearance_btn.style().polish(self.appearance_btn)

    # ---------- 行为卡片 ----------

    def _build_behavior_card(self) -> QFrame:
        card = QFrame()
        card.setObjectName("Card")
        box = QVBoxLayout(card)
        box.setContentsMargins(16, 16, 16, 16)
        box.setSpacing(8)

        title = QLabel("行为")
        title.setObjectName("CardTitle")
        box.addWidget(title)

        self.auto_confirm_checkbox = CheckBox("首次启动时自动确认 Hoshimi 提示")
        self.auto_confirm_checkbox.setChecked(
            self.settings.behavior.auto_confirm_first_launch
        )
        self.auto_confirm_checkbox.toggled.connect(self._on_auto_confirm_toggled)
        box.addWidget(self.auto_confirm_checkbox)

        hint = QLabel(
            "关闭后，首次启动服务时会显示警告条，需要手动点击「继续启动」。"
            "这个提示只在 data 目录不存在时出现一次。"
        )
        hint.setObjectName("MutedText")
        hint.setWordWrap(True)
        box.addWidget(hint)

        self.hoshimi_log_checkbox = CheckBox("记录 Hoshimi 服务日志")
        self.hoshimi_log_checkbox.setChecked(self.settings.behavior.hoshimi_log)
        self.hoshimi_log_checkbox.toggled.connect(self._on_hoshimi_log_toggled)
        box.addWidget(self.hoshimi_log_checkbox)

        hint2 = QLabel(
            "开启后日志写入 logs/hoshimi/<版本>/（每次启动一个文件，"
            "可在清理页删除）；关闭时向 hoshimi 传 --no-log，不写日志文件。"
        )
        hint2.setObjectName("MutedText")
        hint2.setWordWrap(True)
        box.addWidget(hint2)

        self.telemetry_checkbox = CheckBox("允许 Hoshimi 发送使用统计")
        self.telemetry_checkbox.toggled.connect(self._on_telemetry_toggled)
        box.addWidget(self.telemetry_checkbox)

        self.telemetry_hint = QLabel("")
        self.telemetry_hint.setObjectName("MutedText")
        self.telemetry_hint.setWordWrap(True)
        box.addWidget(self.telemetry_hint)
        self._refresh_telemetry_row()

        return card

    # ---------- Hoshimi 使用统计 ----------

    def _telemetry_version(self) -> str:
        return resolve_runtime(self.paths, self.settings).version

    def _refresh_telemetry_row(self) -> None:
        version = self._telemetry_version()
        supported = supports_telemetry(version)
        enabled = read_telemetry(self.paths, self.settings) is True

        self.telemetry_checkbox.blockSignals(True)
        self.telemetry_checkbox.setChecked(enabled)
        self.telemetry_checkbox.blockSignals(False)
        self.telemetry_checkbox.setEnabled(supported)

        if supported:
            self.telemetry_hint.setText(
                "由 hoshimi 自身上报启动与运行信息（含设备 HWID 哈希），"
                "与 Verthandi 无关。开关直接写入 hoshimi 的配置文件，"
                "从下次启动服务开始生效。"
            )
        else:
            self.telemetry_hint.setText(
                "当前 hoshimi 版本不带使用统计（0.0.8 起才引入），此项无效。"
            )

    def _on_telemetry_toggled(self, checked: bool) -> None:
        if not supports_telemetry(self._telemetry_version()):
            return

        def revert() -> None:
            self.telemetry_checkbox.blockSignals(True)
            self.telemetry_checkbox.setChecked(not checked)
            self.telemetry_checkbox.blockSignals(False)

        # 开启涉及设备 HWID 哈希，先让用户看清范围
        if checked and not confirm_enable_telemetry(self, self._telemetry_version()):
            revert()
            return

        ok, err = write_telemetry(self.paths, self.settings, checked)
        if not ok:
            warning(
                self,
                "无法保存遥测设置",
                f"{err}\n\n请确认 home/config.json 存在且可写。",
            )
            revert()
            return

        # 用户在设置页明确表过态，启动时就不必再问
        self.settings.behavior.telemetry_prompted = True
        self.settings_store.save(self.settings)
        self.log_bus.emit_line(
            LogSource.APP,
            f"Hoshimi 使用统计：{'已开启' if checked else '已关闭'}",
            "info",
        )
        show_toast(self, "已开启，下次启动服务生效" if checked else "已关闭使用统计")
        self.settings_changed.emit("behavior")

    def _on_auto_confirm_toggled(self, checked: bool) -> None:
        self.settings.behavior.auto_confirm_first_launch = checked
        self.settings_store.save(self.settings)
        self.log_bus.emit_line(
            LogSource.APP,
            f"首次启动自动确认：{'已开启' if checked else '已关闭'}",
            "info",
        )
        self.settings_changed.emit("behavior")

    def _on_hoshimi_log_toggled(self, checked: bool) -> None:
        self.settings.behavior.hoshimi_log = checked
        self.settings_store.save(self.settings)
        self.log_bus.emit_line(
            LogSource.APP,
            f"Hoshimi 服务日志：{'已开启' if checked else '已关闭'}",
            "info",
        )
        self.settings_changed.emit("behavior")

    # ---------- 日志卡片 ----------

    def _build_logging_card(self) -> QFrame:
        card = QFrame()
        card.setObjectName("Card")
        box = QVBoxLayout(card)
        box.setContentsMargins(16, 16, 16, 16)
        box.setSpacing(8)

        title = QLabel("日志")
        title.setObjectName("CardTitle")
        box.addWidget(title)

        self.log_checkbox = CheckBox("记录日志到文件（logs/session-*.log）")
        self.log_checkbox.setChecked(self.settings.behavior.log_to_file)
        self.log_checkbox.toggled.connect(self._on_log_toggled)
        box.addWidget(self.log_checkbox)

        hint = QLabel("关闭后，出现问题时将无法提供完整日志。仅在确认服务稳定时关闭。")
        hint.setObjectName("MutedText")
        hint.setWordWrap(True)
        box.addWidget(hint)

        return card

    def _on_log_toggled(self, checked: bool) -> None:
        self.settings.behavior.log_to_file = checked
        self.settings_store.save(self.settings)

        if checked:
            self.log_writer.enable()
            self.log_bus.emit_line(
                LogSource.APP,
                f"日志文件已开启：{self.log_writer.path}",
                "info",
            )
        else:
            self.log_writer.disable()
            self.log_bus.emit_line(LogSource.APP, "日志文件已关闭", "info")

        self.settings_changed.emit("log")

    # ---------- 日志警告 ----------

    def _build_log_warning_banner(self) -> QFrame:
        banner = QFrame()
        banner.setObjectName("WarningBanner")
        banner.setVisible(False)
        row = QHBoxLayout(banner)
        row.setContentsMargins(12, 8, 12, 8)
        row.setSpacing(8)

        icon = QLabel("⚠")
        icon.setObjectName("WarningIcon")
        row.addWidget(icon)

        self.log_warning_text = QLabel("")
        self.log_warning_text.setObjectName("WarningText")
        row.addWidget(self.log_warning_text, 1)

        go_btn = QPushButton("去清理")
        go_btn.setObjectName("ToolbarButton")
        go_btn.clicked.connect(self._on_open_cleanup)
        row.addWidget(go_btn)

        close_btn = QPushButton("关闭")
        close_btn.setObjectName("IconButton")
        close_btn.clicked.connect(lambda: banner.setVisible(False))
        row.addWidget(close_btn)

        return banner

    def _show_log_warning(self, log_info: cm.LogInfo) -> None:
        self.log_warning_text.setText(
            f"日志文件较多（{log_info.count} 个，约 {cm.format_size(log_info.total_size_bytes)}），建议清理。"
        )
        self.log_warning_banner.setVisible(True)

    # ---------- 存储空间管理 ----------

    def _build_cleanup_card(self) -> QFrame:
        card = QFrame()
        card.setObjectName("Card")
        box = QVBoxLayout(card)
        box.setContentsMargins(16, 16, 16, 16)
        box.setSpacing(8)

        title = QLabel("存储空间管理")
        title.setObjectName("CardTitle")
        box.addWidget(title)

        row = QHBoxLayout()
        self.disk_usage_label = QLabel("计算中…")
        self.disk_usage_label.setObjectName("MutedText")
        row.addWidget(self.disk_usage_label)
        row.addStretch(1)

        self.open_cleanup_btn = QPushButton("打开清理中心")
        self.open_cleanup_btn.setObjectName("ToolbarButton")
        self.open_cleanup_btn.setProperty("variant", "danger")
        self.open_cleanup_btn.clicked.connect(self._on_open_cleanup)
        row.addWidget(self.open_cleanup_btn)

        box.addLayout(row)
        return card

    # ---------- 清理中心 ----------

    def _on_open_cleanup(self) -> None:
        QApplication.setOverrideCursor(Qt.WaitCursor)
        try:
            dlg = CleanupDialog(self.paths, self.settings, self.log_writer.path, self)
            QApplication.restoreOverrideCursor()
            if dlg.exec() == QDialog.Accepted:
                self._update_disk_usage()
                self.settings_changed.emit("cleanup")
        except Exception as e:
            QApplication.restoreOverrideCursor()
            critical(self, "错误", f"打开清理中心失败：\n{e}")

    # ---------- 磁盘占用统计 ----------

    def _update_disk_usage(self) -> None:
        self.disk_usage_label.setText("计算中…")
        # 延迟执行，避免阻塞 UI 初始化
        QTimer.singleShot(50, self._do_update_disk_usage)

    def _do_update_disk_usage(self) -> None:
        QApplication.setOverrideCursor(Qt.WaitCursor)
        try:
            versions = cm.scan_versions(self.paths, self.settings)
            runtimes = cm.scan_runtimes(self.paths, self.settings)
            log_info = cm.scan_logs(self.paths, self.settings)

            total = sum(v.size_bytes for v in versions)
            total += sum(r.size_bytes for r in runtimes)
            total += log_info.total_size_bytes

            self.disk_usage_label.setText(
                f"当前占用：约 {cm.format_size(total)}"
                f"（版本 {len(versions)} 个，runtime {len(runtimes)} 项，日志 {log_info.count} 个）"
            )

            # 判断是否要显示日志警告
            if log_info.count >= 50:
                self._show_log_warning(log_info)
            else:
                self.log_warning_banner.setVisible(False)

        except Exception:
            self.disk_usage_label.setText("计算失败")
        finally:
            QApplication.restoreOverrideCursor()

    # ---------- 刷新 ----------

    def refresh_all(self) -> None:
        s = self.settings

        client = s.active_client()
        self.game_dir_label.setText(
            client.exe_path if client and client.exe_path else "（未设置）"
        )

        self._refresh_version_combo()

        home = self.paths.home_dir(s)
        home_tag = "（默认）" if s.paths.home_dir == "home" else "（自定义）"
        self.home_dir_label.setText(f"{home}  {home_tag}")

        self._refresh_runtime_label()

        self.http_spin.blockSignals(True)
        self.http_spin.setValue(s.server.http_port)
        self.http_spin.blockSignals(False)

        self.game_spin.blockSignals(True)
        self.game_spin.setValue(s.server.game_port)
        self.game_spin.blockSignals(False)

        self.auto_confirm_checkbox.blockSignals(True)
        self.auto_confirm_checkbox.setChecked(s.behavior.auto_confirm_first_launch)
        self.auto_confirm_checkbox.blockSignals(False)

        self.hoshimi_log_checkbox.blockSignals(True)
        self.hoshimi_log_checkbox.setChecked(s.behavior.hoshimi_log)
        self.hoshimi_log_checkbox.blockSignals(False)

        self._refresh_telemetry_row()

        self.log_checkbox.blockSignals(True)
        self.log_checkbox.setChecked(s.behavior.log_to_file)
        self.log_checkbox.blockSignals(False)

        self._update_disk_usage()

    def _refresh_version_combo(self) -> None:
        self.version_combo.blockSignals(True)
        self.version_combo.clear()

        bin_dir = self.paths.bin_dir(self.settings)
        versions = scan_versions(bin_dir)
        current = self.settings.server.version

        if versions:
            self.version_combo.addItem(f"自动选择最新（{versions[0]}）", userData="")

        for v in versions:
            self.version_combo.addItem(v, userData=v)

        if current and current not in versions:
            self.version_combo.addItem(f"{current}（目录缺失）", userData=current)

        idx = self.version_combo.findData(current) if current else 0
        idx = max(idx, 0)
        if idx < self.version_combo.count():
            self.version_combo.setCurrentIndex(idx)

        self.version_combo.blockSignals(False)

    def refresh_version(self) -> None:
        """只刷新与 hoshimi 版本相关的行（下拉框 + runtime 路径）。

        切版本后调用；不要用 refresh_all()，那会覆盖用户尚未应用的端口输入。
        """
        self._refresh_version_combo()
        self._refresh_runtime_label()

    def _refresh_runtime_label(self) -> None:
        ref = resolve_runtime(self.paths, self.settings)
        self.runtime_dir_label.setText(
            f"{runtime_root(self.paths, self.settings)}  （按版本，当前 {ref.version}）"
        )

    # ---------- 游戏目录 ----------

    def _on_change_game_dir(self) -> None:
        client = self.settings.active_client()
        old_path = client.exe_path if client else ""

        path, _ = QFileDialog.getOpenFileName(
            self,
            "选择 AetherGazer.exe",
            str(Path(old_path).parent) if old_path else str(Path.home()),
            "AetherGazer (AetherGazer.exe);;可执行文件 (*.exe)",
        )
        if not path:
            return
        exe = Path(path)
        if exe.name.lower() != "aethergazer.exe":
            critical(
                self, "文件不匹配", f"选中的文件不是 AetherGazer.exe：\n{exe.name}"
            )
            return
        if old_path and Path(old_path).resolve() == exe.resolve():
            return

        ok = question(
            self,
            "更换游戏目录",
            "确定要更换游戏目录吗？\n\n"
            f"从：\n{old_path or '（未设置）'}\n\n"
            f"改为：\n{exe}\n\n"
            "runtime 与 config.json 可能不匹配，需要重新初始化。",
            yes_text="确认更换",
        )
        if not ok:
            return

        entry = client or ClientEntry(id="", name=exe.parent.name, exe_path=str(exe))
        entry.exe_path = str(exe)
        if not entry.name:
            entry.name = exe.parent.name
        self.settings.set_active_client(entry)
        self.settings_store.save(self.settings)
        self.log_bus.emit_line(LogSource.APP, f"游戏目录已更换：{exe}", "info")
        self.refresh_all()
        self.settings_changed.emit("game_dir")

        self._check_fingerprint_after_change()

    def _check_fingerprint_after_change(self) -> None:
        ref = resolve_runtime(self.paths, self.settings)
        if not ref.exists:
            return
        manifest = read_manifest(self.paths, self.settings, ref)
        stored = manifest.client_fingerprint if manifest else ""
        if not stored:
            return
        current = client_fingerprint(self.paths.active_client_dir(self.settings))
        if not current or current == stored:
            return
        self._prompt_init(
            "游戏目录已更换", "游戏目录已更换，runtime 与旧客户端对应，建议重新初始化。"
        )

    # ---------- hoshimi 版本 ----------

    def _on_version_changed(self, index: int) -> None:
        if index < 0:
            return
        data = self.version_combo.currentData()

        if data == "":
            versions = scan_versions(self.paths.bin_dir(self.settings))
            if not versions:
                return
            new_version = versions[0]
        else:
            new_version = data

        old_version = self.settings.server.version
        if new_version == old_version:
            return

        self.settings.server.version = new_version
        self.settings_store.save(self.settings)
        self.log_bus.emit_line(
            LogSource.APP, f"hoshimi 版本已切换：{new_version}", "info"
        )

        self.refresh_version()
        self.settings_changed.emit("version")
        # runtime 目录由版本派生，切版本即切目录，不再需要"版本不一致"提示

    # ---------- 导入 hoshimi ----------

    def _on_import_hoshimi(self) -> None:
        file_path, _ = QFileDialog.getOpenFileName(
            self,
            "选择 hoshimi 文件",
            str(self.base_dir),
            "Hoshimi (*.zip *.exe);;ZIP 压缩包 (*.zip);;hoshimi.exe (*.exe);;所有文件 (*)",
        )
        if not file_path:
            return
        source = Path(file_path)

        bin_dir = self.paths.bin_dir(self.settings)
        try:
            bin_dir.mkdir(parents=True, exist_ok=True)
        except OSError as e:
            critical(self, "导入失败", f"创建 bin 目录失败：{e}")
            return

        ts = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
        temp_dir = bin_dir / f"import-{ts}"

        try:
            temp_dir.mkdir()
        except OSError as e:
            critical(self, "导入失败", f"创建临时目录失败：{e}")
            return

        try:
            self._do_import(source, bin_dir, temp_dir)
        finally:
            # 成功时 temp_dir 已被重命名，此时 temp_dir.exists() 为 False
            if temp_dir.exists():
                shutil.rmtree(temp_dir, ignore_errors=True)

    def _do_import(self, source: Path, bin_dir: Path, temp_dir: Path) -> None:
        # ---------- 阶段 1：准备（wait cursor） ----------
        scripts_ok = False
        QApplication.setOverrideCursor(Qt.WaitCursor)
        try:
            suffix = source.suffix.lower()
            if suffix == ".zip":
                scripts_ok = hoshimi_importer.prepare_from_zip(source, temp_dir)
            elif suffix == ".exe":
                scripts_ok = hoshimi_importer.prepare_from_exe(source, temp_dir)
            else:
                raise hoshimi_importer.ImportError(
                    "bad_format",
                    f"不支持的文件格式：{source.suffix}\n\n"
                    "只支持 .zip 或 .exe。\n"
                    "其他格式请手动解压后选择 hoshimi.exe 导入。",
                )
            hoshimi_importer.validate(temp_dir)
            exe_path = temp_dir / "hoshimi.exe"
            detected = hoshimi_importer.detect_version(exe_path)
        except hoshimi_importer.ImportError as e:
            QApplication.restoreOverrideCursor()
            critical(self, "导入失败", e.message)
            return
        except Exception as e:
            QApplication.restoreOverrideCursor()
            critical(self, "导入失败", f"{type(e).__name__}: {e}")
            return
        finally:
            QApplication.restoreOverrideCursor()

        # ---------- 阶段 2：确定版本号（硬性前置，先于 scripts 警告） ----------
        version = detected
        if not version and source.suffix.lower() == ".zip":
            version = hoshimi_importer.detect_version_from_filename(source.name)

        if not version:
            dlg = VersionInputDialog(
                self,
                initial=hoshimi_importer.detect_version_from_filename(source.name)
                or "",
            )
            if dlg.exec() != QDialog.Accepted:
                return
            version = dlg.value()

        # ---------- 阶段 3：scripts 缺失确认 ----------
        if not scripts_ok:
            ok = question(
                self,
                "scripts 目录缺失",
                "未能找到有效的 scripts/ 目录（缺失或为空）。\n\n"
                "Verthandi 内置的切服功能不受影响，"
                "但缺少 scripts 会导致部分辅助脚本功能（如战斗布局修复）不可用。\n\n"
                "仍要继续导入吗？",
                yes_text="仍要导入",
                danger=True,
            )
            if not ok:
                return  # 用户选择"否"时退出导入

        # ---------- 阶段 4：冲突处理 ----------
        target = bin_dir / version
        action = "new"
        if target.exists():
            action = self._ask_conflict_action(version)
            if action == "cancel":
                return

        # ---------- 阶段 5：落位 ----------
        try:
            final_path = hoshimi_importer.finalize(temp_dir, bin_dir, version, action)
        except hoshimi_importer.ImportError as e:
            critical(self, "导入失败", e.message)
            return

        # ---------- 阶段 6：切换版本 ----------
        self.settings.server.version = version
        self.settings_store.save(self.settings)
        self.log_bus.emit_line(
            LogSource.APP,
            f"hoshimi 已导入：{version}（{final_path}）",
            "info",
        )

        self.refresh_all()
        self.settings_changed.emit("version")
        show_toast(self, f"已导入 hoshimi {version}")

    def _ask_conflict_action(self, version: str) -> str:
        return ask_choice(
            self,
            "版本目录已存在",
            f"bin/{version}/ 已存在。请选择处理方式：\n\n"
            f"• 备份：现有目录重命名为 bin/{version}-backup-<时间>/，\n"
            f"  然后放入新版本（推荐）\n"
            f"• 覆盖：直接删除旧目录并放入新版本\n"
            f"• 取消：不做任何改动",
            [("backup", "备份"), ("overwrite", "覆盖")],
            default=0,
        )

    def _prompt_init(self, title: str, message: str) -> None:
        if question(self, title, message, yes_text="去初始化"):
            self.request_init.emit()

    # ---------- home / runtime ----------

    def _on_change_home_dir(self) -> None:
        current = self.paths.home_dir(self.settings)
        path = QFileDialog.getExistingDirectory(
            self,
            "选择 home 目录",
            str(current) if current.exists() else str(self.base_dir),
        )
        if not path:
            return
        new_path = Path(path).resolve()
        if new_path == current:
            return

        cfg = new_path / "config.json"
        if not cfg.exists():
            information(
                self,
                "home 目录无 config.json",
                f"新 home 目录缺少 config.json：\n{cfg}\n\n"
                "需要执行「初始化」生成配置，或确认这是空目录后继续。",
            )

        self.settings.paths.home_dir = self.paths.relativize(new_path)
        self.settings_store.save(self.settings)
        self.log_bus.emit_line(LogSource.APP, f"home 目录已更换：{new_path}", "info")
        self.refresh_all()
        self.settings_changed.emit("paths")

    def _on_change_runtime_dir(self) -> None:
        """选择 runtime 根目录：恒按版本存放（runtimes/<版本>/runtime）。"""
        start = runtime_root(self.paths, self.settings)
        ref = resolve_runtime(self.paths, self.settings)
        if not start.exists():
            start = (
                ref.directory.parent if ref.directory.parent.exists() else self.base_dir
            )

        path = QFileDialog.getExistingDirectory(self, "选择 runtime 根目录", str(start))
        if not path:
            return
        new_path = Path(path).resolve()

        self.settings.paths.runtime_root = self.paths.relativize(new_path)
        self.settings_store.save(self.settings)
        self.log_bus.emit_line(
            LogSource.APP,
            f"runtime 根目录已更换（按版本存放）：{new_path}",
            "info",
        )
        self.refresh_all()
        self.settings_changed.emit("paths")

    # ---------- 端口 ----------

    def _on_port_value_changed(self, _value: int) -> None:
        self.http_status.setText("")
        self.http_status.setProperty("state", "")
        self._repolish(self.http_status)
        self.game_status.setText("")
        self.game_status.setProperty("state", "")
        self._repolish(self.game_status)
        self._port_dirty = True
        self._update_apply_state()

    def _on_http_editing_finished(self) -> None:
        self._check_one("http")

    def _on_game_editing_finished(self) -> None:
        self._check_one("game")

    def _check_one(self, which: str) -> PortResult | None:
        if which == "http":
            port = self.http_spin.value()
            current = self.settings.server.http_port
            if port == current:
                self._http_check = None
                self._set_status_label(self.http_status, None)
                return None
            result = check_port(port)
            self._http_check = result
            self._set_status_label(self.http_status, result)
            return result

        port = self.game_spin.value()
        current = self.settings.server.game_port
        if port == current:
            self._game_check = None
            self._set_status_label(self.game_status, None)
            return None
        result = check_port(port)
        self._game_check = result
        self._set_status_label(self.game_status, result)
        return result

    def _set_status_label(self, label: QLabel, result: PortResult | None) -> None:
        # 内联样式：展开卡自带的 QLabel 规则会覆盖全局 QSS 的状态色
        if result is None:
            label.setText("")
            label.setProperty("state", "")
            self._repolish(label)
            return
        if result.is_free:
            label.setText("✓ 空闲")
            label.setProperty("state", "ok")
            label.setStyleSheet("color: #16a34a; background: transparent;")
        else:
            label.setText(f"✗ {result.summary()}")
            label.setProperty("state", "error")
            label.setStyleSheet("color: #dc2626; background: transparent;")
        self._repolish(label)

    # ---------- 应用 ----------

    def _update_apply_state(self) -> None:
        editable = self.server_process.state == ServerState.STOPPED
        self.apply_btn.setEnabled(editable and self._port_dirty)

    def _on_apply_ports(self) -> None:
        new_http = self.http_spin.value()
        new_game = self.game_spin.value()
        cur_http = self.settings.server.http_port
        cur_game = self.settings.server.game_port

        if new_http == cur_http and new_game == cur_game:
            self._port_dirty = False
            self._update_apply_state()
            return

        problems: list[PortResult] = []
        if new_http != cur_http:
            r = check_port(new_http)
            if not r.is_free:
                problems.append(r)
        if new_game != cur_game:
            r = check_port(new_game)
            if not r.is_free:
                problems.append(r)

        if problems:
            lines = [f"• 端口 {r.port} {r.summary()}" for r in problems]
            ok = question(
                self,
                "保存端口",
                "以下端口当前不可用：\n\n"
                + "\n".join(lines)
                + "\n\n仍要保存吗？启动服务时会再次检查。",
                yes_text="强制保存",
                danger=True,
            )
            if not ok:
                return

        self.settings.server.http_port = new_http
        self.settings.server.game_port = new_game
        self.settings_store.save(self.settings)

        self._port_dirty = False
        self._http_check = None
        self._game_check = None
        self._set_status_label(self.http_status, None)
        self._set_status_label(self.game_status, None)
        self._update_apply_state()

        self.log_bus.emit_line(
            LogSource.APP,
            f"端口已更新：HTTP {new_http} / 游戏 {new_game}",
            "info",
        )

        # 通知主窗口：主页面状态面板需要刷新
        self.settings_changed.emit("ports")

        from app.core.client_state import ClientMode, read_client_mode

        actual = read_client_mode(self.paths.active_client_dir(self.settings))
        if actual == ClientMode.LOCAL and cur_http != new_http:
            self._show_switch_hint(cur_http, new_http)

    def _show_switch_hint(self, old_http: int, new_http: int) -> None:
        information(
            self,
            "端口已修改",
            f"HTTP 端口已从 {old_http} 改为 {new_http}。\n\n"
            "客户端仍指向旧端口。请到主页面点击「切换」重新生成客户端配置，"
            "否则游戏将无法连接本地服务。",
        )

    # ---------- 服务状态 ----------

    def _on_server_state(self, state: str) -> None:
        self._apply_server_state(ServerState(state))

    def _apply_server_state(self, state: ServerState) -> None:
        editable = state == ServerState.STOPPED

        self.essential_notice.setVisible(not editable)
        self.change_game_dir_btn.setEnabled(editable)
        self.version_combo.setEnabled(editable)
        self.change_home_btn.setEnabled(editable)
        self.change_runtime_btn.setEnabled(editable)
        self.import_btn.setEnabled(editable)
        # 服务运行时灰显
        self.open_cleanup_btn.setEnabled(editable)

        self.http_spin.setEnabled(editable)
        self.game_spin.setEnabled(editable)
        self.apply_btn.setEnabled(editable and self._port_dirty)

        # 行为/日志开关不受服务状态影响
        self.auto_confirm_checkbox.setEnabled(True)
        self.log_checkbox.setEnabled(True)

    @staticmethod
    def _repolish(widget: QWidget) -> None:
        widget.style().unpolish(widget)
        widget.style().polish(widget)

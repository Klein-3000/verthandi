from __future__ import annotations

import re
import shutil
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import Qt, Signal, QTimer
from PySide6.QtWidgets import (
    QApplication,
    QInputDialog,
    QCheckBox,
    QComboBox,
    QDialog,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from app.core.client_state import client_fingerprint
from app.core.hoshimi_finder import version_key
from app.core.log_bus import LogBus, LogSource, SessionLogWriter
from app.core.paths import Paths
from app.core.port_check import PortResult, check_port
from app.core.server_process import ServerProcess, ServerState
from app.core.settings import ClientEntry, Settings, SettingsStore
from app.core import hoshimi_importer
from app.widgets.toast import show_toast
from app.core import cleanup_manager as cm
from app.widgets.cleanup_dialog import CleanupDialog


RUNTIME_REQUIRED = ('protocol_lib.json', 'runtime.json', 'tables.json', 'tolua.dll')


def _scan_versions(bin_dir: Path) -> list[str]:
    if not bin_dir.exists():
        return []
    versions = []
    for d in bin_dir.iterdir():
        if d.is_dir() and (d / 'hoshimi.exe').exists():
            versions.append(d.name)
    versions.sort(key=version_key, reverse=True)
    return versions


class SettingsPage(QWidget):
    settings_changed = Signal(str)   # 'game_dir' / 'version' / 'paths' / 'ports' / 'log' / 'behavior' / 'cleanup'
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
    ):
        super().__init__()
        self.base_dir = base_dir
        self.paths = paths
        self.settings = settings
        self.settings_store = settings_store
        self.log_bus = log_bus
        self.log_writer = log_writer
        self.server_process = server_process

        self._port_dirty = False
        self._http_check: PortResult | None = None
        self._game_check: PortResult | None = None

        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(16)

        title = QLabel('设置')
        title.setObjectName('PageTitle')
        layout.addWidget(title)

        # 顶部警告条
        self.log_warning_banner = self._build_log_warning_banner()
        layout.addWidget(self.log_warning_banner)

        layout.addWidget(self._build_essential_card())
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
        card.setObjectName('Card')
        box = QVBoxLayout(card)
        box.setContentsMargins(16, 16, 16, 16)
        box.setSpacing(10)

        title = QLabel('必备配置')
        title.setObjectName('CardTitle')
        box.addWidget(title)

        self.essential_notice = QLabel('服务运行中，停止服务后可修改')
        self.essential_notice.setObjectName('MutedText')
        self.essential_notice.setVisible(False)
        box.addWidget(self.essential_notice)

        # 游戏目录
        self.game_dir_label = QLabel('—')
        self.game_dir_label.setObjectName('StatusValue')
        self.game_dir_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.change_game_dir_btn = QPushButton('更换…')
        self.change_game_dir_btn.setObjectName('ToolbarButton')
        self.change_game_dir_btn.clicked.connect(self._on_change_game_dir)
        row = QHBoxLayout()
        k = QLabel('游戏目录'); k.setObjectName('StatusKey'); k.setFixedWidth(110)
        row.addWidget(k)
        row.addWidget(self.game_dir_label, 1)
        row.addWidget(self.change_game_dir_btn)
        box.addLayout(row)

        # hoshimi 版本
        self.version_combo = QComboBox()
        self.version_combo.setMinimumWidth(220)
        self.version_combo.currentIndexChanged.connect(self._on_version_changed)
        self.import_btn = QPushButton('导入…')
        self.import_btn.setObjectName('ToolbarButton')
        self.import_btn.setToolTip('从 ZIP 或 hoshimi.exe 文件导入')
        self.import_btn.clicked.connect(self._on_import_hoshimi)
        row = QHBoxLayout()
        k = QLabel('hoshimi 版本'); k.setObjectName('StatusKey'); k.setFixedWidth(110)
        row.addWidget(k)
        row.addWidget(self.version_combo, 1)
        row.addWidget(self.import_btn)
        box.addLayout(row)

        # home
        self.home_dir_label = QLabel('—')
        self.home_dir_label.setObjectName('StatusValue')
        self.home_dir_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.change_home_btn = QPushButton('更换…')
        self.change_home_btn.setObjectName('ToolbarButton')
        self.change_home_btn.clicked.connect(self._on_change_home_dir)
        row = QHBoxLayout()
        k = QLabel('home 目录'); k.setObjectName('StatusKey'); k.setFixedWidth(110)
        row.addWidget(k)
        row.addWidget(self.home_dir_label, 1)
        row.addWidget(self.change_home_btn)
        box.addLayout(row)

        # runtime
        self.runtime_dir_label = QLabel('—')
        self.runtime_dir_label.setObjectName('StatusValue')
        self.runtime_dir_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.change_runtime_btn = QPushButton('更换…')
        self.change_runtime_btn.setObjectName('ToolbarButton')
        self.change_runtime_btn.clicked.connect(self._on_change_runtime_dir)
        row = QHBoxLayout()
        k = QLabel('runtime 目录'); k.setObjectName('StatusKey'); k.setFixedWidth(110)
        row.addWidget(k)
        row.addWidget(self.runtime_dir_label, 1)
        row.addWidget(self.change_runtime_btn)
        box.addLayout(row)

        # 端口折叠
        box.addWidget(self._build_port_section())

        return card

    def _build_port_section(self) -> QWidget:
        wrapper = QWidget()
        layout = QVBoxLayout(wrapper)
        layout.setContentsMargins(0, 8, 0, 0)
        layout.setSpacing(8)

        self.port_toggle = QToolButton()
        self.port_toggle.setObjectName('PortToggle')
        self.port_toggle.setText('端口（高级设置）')
        self.port_toggle.setCheckable(True)
        self.port_toggle.setChecked(False)
        self.port_toggle.setArrowType(Qt.RightArrow)
        self.port_toggle.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
        self.port_toggle.setCursor(Qt.PointingHandCursor)
        self.port_toggle.toggled.connect(self._on_port_toggled)
        layout.addWidget(self.port_toggle)

        self.port_content = QWidget()
        content_layout = QVBoxLayout(self.port_content)
        content_layout.setContentsMargins(20, 4, 0, 0)
        content_layout.setSpacing(10)

        self.http_spin = QSpinBox()
        self.http_spin.setRange(1, 65535)
        self.http_spin.setValue(self.settings.server.http_port)
        self.http_spin.setMinimumWidth(120)
        self.http_spin.editingFinished.connect(self._on_http_editing_finished)
        self.http_spin.valueChanged.connect(self._on_port_value_changed)
        self.http_status = QLabel('')
        self.http_status.setObjectName('PortStatus')
        content_layout.addLayout(
            self._port_row('HTTP 端口', self.http_spin, self.http_status))

        self.game_spin = QSpinBox()
        self.game_spin.setRange(1, 65535)
        self.game_spin.setValue(self.settings.server.game_port)
        self.game_spin.setMinimumWidth(120)
        self.game_spin.editingFinished.connect(self._on_game_editing_finished)
        self.game_spin.valueChanged.connect(self._on_port_value_changed)
        self.game_status = QLabel('')
        self.game_status.setObjectName('PortStatus')
        content_layout.addLayout(
            self._port_row('游戏端口', self.game_spin, self.game_status))

        hint = QLabel(
            '游戏端口同时使用 TCP 和 UDP。修改后需要重新放行防火墙并更新玩家转发规则。'
        )
        hint.setObjectName('MutedText')
        hint.setWordWrap(True)
        content_layout.addWidget(hint)

        action_row = QHBoxLayout()
        action_row.addStretch(1)
        self.apply_btn = QPushButton('应用')
        self.apply_btn.setObjectName('ToolbarButton')
        self.apply_btn.setEnabled(False)
        self.apply_btn.clicked.connect(self._on_apply_ports)
        action_row.addWidget(self.apply_btn)
        content_layout.addLayout(action_row)

        self.port_content.setVisible(False)
        layout.addWidget(self.port_content)
        return wrapper

    def _port_row(self, label: str, spin: QSpinBox, status: QLabel) -> QHBoxLayout:
        row = QHBoxLayout()
        key = QLabel(label)
        key.setObjectName('StatusKey')
        key.setFixedWidth(110)
        row.addWidget(key)
        row.addWidget(spin)
        row.addWidget(status, 1)
        return row

    def _on_port_toggled(self, checked: bool) -> None:
        self.port_content.setVisible(checked)
        self.port_toggle.setArrowType(
            Qt.DownArrow if checked else Qt.RightArrow)

    # ---------- 行为卡片 ----------

    def _build_behavior_card(self) -> QFrame:
        card = QFrame()
        card.setObjectName('Card')
        box = QVBoxLayout(card)
        box.setContentsMargins(16, 16, 16, 16)
        box.setSpacing(8)

        title = QLabel('行为')
        title.setObjectName('CardTitle')
        box.addWidget(title)

        self.auto_confirm_checkbox = QCheckBox(
            '首次启动时自动确认 Hoshimi 提示'
        )
        self.auto_confirm_checkbox.setChecked(
            self.settings.behavior.auto_confirm_first_launch
        )
        self.auto_confirm_checkbox.toggled.connect(self._on_auto_confirm_toggled)
        box.addWidget(self.auto_confirm_checkbox)

        hint = QLabel(
            '关闭后，首次启动服务时会显示警告条，需要手动点击「继续启动」。'
            '这个提示只在 data 目录不存在时出现一次。'
        )
        hint.setObjectName('MutedText')
        hint.setWordWrap(True)
        box.addWidget(hint)

        return card

    def _on_auto_confirm_toggled(self, checked: bool) -> None:
        self.settings.behavior.auto_confirm_first_launch = checked
        self.settings_store.save(self.settings)
        self.log_bus.emit_line(
            LogSource.APP,
            f'首次启动自动确认：{"已开启" if checked else "已关闭"}',
            'info',
        )
        self.settings_changed.emit('behavior')

    # ---------- 日志卡片 ----------

    def _build_logging_card(self) -> QFrame:
        card = QFrame()
        card.setObjectName('Card')
        box = QVBoxLayout(card)
        box.setContentsMargins(16, 16, 16, 16)
        box.setSpacing(8)

        title = QLabel('日志')
        title.setObjectName('CardTitle')
        box.addWidget(title)

        self.log_checkbox = QCheckBox('记录日志到文件（logs/session-*.log）')
        self.log_checkbox.setChecked(self.settings.behavior.log_to_file)
        self.log_checkbox.toggled.connect(self._on_log_toggled)
        box.addWidget(self.log_checkbox)

        hint = QLabel(
            '关闭后，出现问题时将无法提供完整日志。仅在确认服务稳定时关闭。'
        )
        hint.setObjectName('MutedText')
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
                f'日志文件已开启：{self.log_writer.path}',
                'info',
            )
        else:
            self.log_writer.disable()
            self.log_bus.emit_line(LogSource.APP, '日志文件已关闭', 'info')

        self.settings_changed.emit('log')

    # ---------- 日志警告 ----------

    def _build_log_warning_banner(self) -> QFrame:
        banner = QFrame()
        banner.setObjectName('WarningBanner')
        banner.setVisible(False)
        row = QHBoxLayout(banner)
        row.setContentsMargins(12, 8, 12, 8)
        row.setSpacing(8)

        icon = QLabel('⚠')
        icon.setObjectName('WarningIcon')
        row.addWidget(icon)

        self.log_warning_text = QLabel('')
        self.log_warning_text.setObjectName('WarningText')
        row.addWidget(self.log_warning_text, 1)

        go_btn = QPushButton('去清理')
        go_btn.setObjectName('ToolbarButton')
        go_btn.clicked.connect(self._on_open_cleanup)
        row.addWidget(go_btn)

        close_btn = QPushButton('关闭')
        close_btn.setObjectName('IconButton')
        close_btn.clicked.connect(lambda: banner.setVisible(False))
        row.addWidget(close_btn)

        return banner

    def _show_log_warning(self, log_info: cm.LogInfo) -> None:
        self.log_warning_text.setText(
            f'日志文件较多（{log_info.count} 个，约 {cm.format_size(log_info.total_size_bytes)}），建议清理。'
        )
        self.log_warning_banner.setVisible(True)

    # ---------- 存储空间管理 ----------

    def _build_cleanup_card(self) -> QFrame:
        card = QFrame()
        card.setObjectName('Card')
        box = QVBoxLayout(card)
        box.setContentsMargins(16, 16, 16, 16)
        box.setSpacing(8)

        title = QLabel('存储空间管理')
        title.setObjectName('CardTitle')
        box.addWidget(title)

        row = QHBoxLayout()
        self.disk_usage_label = QLabel('计算中…')
        self.disk_usage_label.setObjectName('MutedText')
        row.addWidget(self.disk_usage_label)
        row.addStretch(1)

        self.open_cleanup_btn = QPushButton('打开清理中心')
        self.open_cleanup_btn.setObjectName('ToolbarButton')
        self.open_cleanup_btn.setProperty('variant', 'danger')
        self.open_cleanup_btn.clicked.connect(self._on_open_cleanup)
        row.addWidget(self.open_cleanup_btn)

        box.addLayout(row)
        return card

    # ---------- 清理中心 ----------

    def _on_open_cleanup(self) -> None:
        QApplication.setOverrideCursor(Qt.WaitCursor)
        try:
            dlg = CleanupDialog(
                self.paths, self.settings, self.log_writer.path, self
            )
            QApplication.restoreOverrideCursor()
            if dlg.exec() == QDialog.Accepted:
                self._update_disk_usage()
                self.settings_changed.emit('cleanup')
        except Exception as e:
            QApplication.restoreOverrideCursor()
            QMessageBox.critical(self, '错误', f'打开清理中心失败：\n{e}')

    # ---------- 磁盘占用统计 ----------

    def _update_disk_usage(self) -> None:
        self.disk_usage_label.setText('计算中…')
        # 延迟执行，避免阻塞 UI 初始化
        QTimer.singleShot(50, self._do_update_disk_usage)

    def _do_update_disk_usage(self) -> None:
        QApplication.setOverrideCursor(Qt.WaitCursor)
        try:
            versions = cm.scan_versions(self.paths, self.settings)
            backups = cm.scan_runtime_backups(self.paths, self.settings)
            log_info = cm.scan_logs(self.paths, self.settings)

            total = sum(v.size_bytes for v in versions)
            total += sum(b.size_bytes for b in backups)
            total += log_info.total_size_bytes

            self.disk_usage_label.setText(
                f'当前占用：约 {cm.format_size(total)}'
                f'（版本 {len(versions)} 个，备份 {len(backups)} 个，日志 {log_info.count} 个）'
            )

            # 判断是否要显示日志警告
            if log_info.count >= 50:
                self._show_log_warning(log_info)
            else:
                self.log_warning_banner.setVisible(False)

        except Exception:
            self.disk_usage_label.setText('计算失败')
        finally:
            QApplication.restoreOverrideCursor()

    # ---------- 刷新 ----------

    def refresh_all(self) -> None:
        s = self.settings

        client = s.active_client()
        self.game_dir_label.setText(
            client.exe_path if client and client.exe_path else '（未设置）'
        )

        self._refresh_version_combo()

        home = self.paths.home_dir(s)
        home_tag = '（默认）' if s.paths.home_dir == 'home' else '（自定义）'
        self.home_dir_label.setText(f'{home}  {home_tag}')

        runtime = self.paths.runtime_dir(s)
        rt_tag = '（默认）' if s.paths.runtime_dir == 'runtime' else '（自定义）'
        self.runtime_dir_label.setText(f'{runtime}  {rt_tag}')

        self.http_spin.blockSignals(True)
        self.http_spin.setValue(s.server.http_port)
        self.http_spin.blockSignals(False)

        self.game_spin.blockSignals(True)
        self.game_spin.setValue(s.server.game_port)
        self.game_spin.blockSignals(False)

        self.auto_confirm_checkbox.blockSignals(True)
        self.auto_confirm_checkbox.setChecked(s.behavior.auto_confirm_first_launch)
        self.auto_confirm_checkbox.blockSignals(False)

        self.log_checkbox.blockSignals(True)
        self.log_checkbox.setChecked(s.behavior.log_to_file)
        self.log_checkbox.blockSignals(False)

        self._update_disk_usage()

    def _refresh_version_combo(self) -> None:
        self.version_combo.blockSignals(True)
        self.version_combo.clear()

        bin_dir = self.paths.bin_dir(self.settings)
        versions = _scan_versions(bin_dir)
        current = self.settings.server.version

        if versions:
            self.version_combo.addItem(f'自动选择最新（{versions[0]}）', '')

        for v in versions:
            self.version_combo.addItem(v, v)

        if current and current not in versions:
            self.version_combo.addItem(f'{current}（目录缺失）', current)

        idx = self.version_combo.findData(current) if current else 0
        if idx < 0:
            idx = 0
        if idx < self.version_combo.count():
            self.version_combo.setCurrentIndex(idx)

        self.version_combo.blockSignals(False)

    # ---------- 游戏目录 ----------

    def _on_change_game_dir(self) -> None:
        client = self.settings.active_client()
        old_path = client.exe_path if client else ''

        path, _ = QFileDialog.getOpenFileName(
            self, '选择 AetherGazer.exe',
            str(Path(old_path).parent) if old_path else str(Path.home()),
            'AetherGazer (AetherGazer.exe);;可执行文件 (*.exe)',
        )
        if not path:
            return
        exe = Path(path)
        if exe.name.lower() != 'aethergazer.exe':
            QMessageBox.critical(
                self, '文件不匹配',
                f'选中的文件不是 AetherGazer.exe：\n{exe.name}'
            )
            return
        if old_path and Path(old_path).resolve() == exe.resolve():
            return

        box = QMessageBox(self)
        box.setIcon(QMessageBox.Warning)
        box.setWindowTitle('更换游戏目录')
        box.setText('确定要更换游戏目录吗？')
        box.setInformativeText(
            f'从：\n{old_path or "（未设置）"}\n\n'
            f'改为：\n{exe}\n\n'
            'runtime 与 config.json 可能不匹配，需要重新初始化。'
        )
        confirm_btn = box.addButton('确认更换', QMessageBox.AcceptRole)
        box.addButton('取消', QMessageBox.RejectRole)
        box.setDefaultButton(confirm_btn)
        box.exec()
        if box.clickedButton() is not confirm_btn:
            return

        entry = client or ClientEntry(
            id='', name=exe.parent.name, exe_path=str(exe))
        entry.exe_path = str(exe)
        if not entry.name:
            entry.name = exe.parent.name
        self.settings.set_active_client(entry)
        self.settings_store.save(self.settings)
        self.log_bus.emit_line(LogSource.APP, f'游戏目录已更换：{exe}', 'info')
        self.refresh_all()
        self.settings_changed.emit('game_dir')

        self._check_fingerprint_after_change()

    def _check_fingerprint_after_change(self) -> None:
        runtime = self.paths.runtime_dir(self.settings)
        if not runtime.exists():
            return
        stored = self.settings.prepare.client_fingerprint
        if not stored:
            return
        current = client_fingerprint(self.paths.active_client_dir(self.settings))
        if not current or current == stored:
            return
        self._prompt_init('游戏目录已更换',
                          '游戏目录已更换，runtime 与旧客户端对应，建议重新初始化。')

    # ---------- hoshimi 版本 ----------

    def _on_version_changed(self, index: int) -> None:
        if index < 0:
            return
        data = self.version_combo.currentData()

        if data == '':
            versions = _scan_versions(self.paths.bin_dir(self.settings))
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
            LogSource.APP, f'hoshimi 版本已切换：{new_version}', 'info')

        self._refresh_version_combo()
        self.settings_changed.emit('version')

        self._check_prepare_after_version(new_version)

    def _check_prepare_after_version(self, new_version: str) -> None:
        runtime = self.paths.runtime_dir(self.settings)
        if not runtime.exists():
            return
        prepared = self.settings.prepare.last_prepared_version
        if not prepared or prepared == new_version:
            return

        self._prompt_init(
            'hoshimi 版本已切换',
            f'hoshimi 版本已从 {prepared} 切换到 {new_version}。\n'
            f'当前 runtime 由 {prepared} 准备，版本不一致，建议重新初始化。\n'
            f'未重新初始化时，启动服务可能出现异常。'
        )

    # ---------- 导入 hoshimi ----------

    def _on_import_hoshimi(self) -> None:
        file_path, _ = QFileDialog.getOpenFileName(
            self, '选择 hoshimi 文件',
            str(self.base_dir),
            'Hoshimi (*.zip *.exe);;ZIP 压缩包 (*.zip);;hoshimi.exe (*.exe);;所有文件 (*)',
        )
        if not file_path:
            return
        source = Path(file_path)

        bin_dir = self.paths.bin_dir(self.settings)
        try:
            bin_dir.mkdir(parents=True, exist_ok=True)
        except OSError as e:
            QMessageBox.critical(self, '导入失败', f'创建 bin 目录失败：{e}')
            return

        ts = datetime.now().strftime('%Y%m%d-%H%M%S-%f')
        temp_dir = bin_dir / f'import-{ts}'

        try:
            temp_dir.mkdir()
        except OSError as e:
            QMessageBox.critical(self, '导入失败', f'创建临时目录失败：{e}')
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
            if suffix == '.zip':
                scripts_ok = hoshimi_importer.prepare_from_zip(source, temp_dir)
            elif suffix == '.exe':
                scripts_ok = hoshimi_importer.prepare_from_exe(source, temp_dir)
            else:
                raise hoshimi_importer.ImportError(
                    'bad_format',
                    f'不支持的文件格式：{source.suffix}\n\n'
                    '只支持 .zip 或 .exe。\n'
                    '其他格式请手动解压后选择 hoshimi.exe 导入。',
                )
            hoshimi_importer.validate(temp_dir)
            exe_path = temp_dir / 'hoshimi.exe'
            detected = hoshimi_importer.detect_version(exe_path)
        except hoshimi_importer.ImportError as e:
            QApplication.restoreOverrideCursor()
            QMessageBox.critical(self, '导入失败', e.message)
            return
        except Exception as e:
            QApplication.restoreOverrideCursor()
            QMessageBox.critical(
                self, '导入失败',
                f'{type(e).__name__}: {e}'
            )
            return
        finally:
            QApplication.restoreOverrideCursor()

        # ---------- 阶段 2：scripts 缺失确认 ----------
        if not scripts_ok:
            reply = QMessageBox.question(
                self, 'scripts 目录缺失',
                '未能找到有效的 scripts/ 目录（缺失或为空）。\n\n'
                'Verthandi 内置的切服功能不受影响，'
                '但缺少 scripts 会导致部分辅助脚本功能（如战斗布局修复）不可用。\n\n'
                '仍要继续导入吗？',
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.No,
            )
            if reply != QMessageBox.Yes:
                return  # 修复：用户选择“否”时退出导入

        # ---------- 阶段 3：确定版本号 ----------
        version = detected
        if not version and source.suffix.lower() == '.zip':
            version = hoshimi_importer.detect_version_from_filename(source.name)

        if not version:
            text, ok = QInputDialog.getText(
                self, '输入 hoshimi 版本号',
                '未能自动检测版本号（hoshimi.exe -h 无有效输出）。\n'
                '请手动输入（例如 0.0.5）：',
            )
            if not ok or not text.strip():
                return
            version = text.strip()
            if not re.match(r'^\d+\.\d+\.\d+$', version):
                QMessageBox.critical(
                    self, '版本号格式错误',
                    f'版本号必须形如 0.0.5，收到：{version}',
                )
                return

        # ---------- 阶段 4：冲突处理 ----------
        target = bin_dir / version
        action = 'new'
        if target.exists():
            action = self._ask_conflict_action(version)
            if action == 'cancel':
                return

        # ---------- 阶段 5：落位 ----------
        try:
            final_path = hoshimi_importer.finalize(temp_dir, bin_dir, version, action)
        except hoshimi_importer.ImportError as e:
            QMessageBox.critical(self, '导入失败', e.message)
            return

        # ---------- 阶段 6：切换版本 ----------
        self.settings.server.version = version
        self.settings_store.save(self.settings)
        self.log_bus.emit_line(
            LogSource.APP,
            f'hoshimi 已导入：{version}（{final_path}）',
            'info',
        )

        self.refresh_all()
        self.settings_changed.emit('version')
        show_toast(self, f'已导入 hoshimi {version}')

        # runtime 版本不匹配时提示重新初始化
        self._check_prepare_after_version(version)

    def _ask_conflict_action(self, version: str) -> str:
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Warning)
        box.setWindowTitle('版本目录已存在')
        box.setText(f'bin/{version}/ 已存在。')
        box.setInformativeText(
            '请选择处理方式：\n\n'
            f'• 备份：现有目录重命名为 bin/{version}-backup-<时间>/，\n'
            f'  然后放入新版本（推荐）\n'
            f'• 覆盖：直接删除旧目录并放入新版本\n'
            f'• 取消：不做任何改动'
        )
        backup_btn = box.addButton('备份', QMessageBox.AcceptRole)
        overwrite_btn = box.addButton('覆盖', QMessageBox.DestructiveRole)
        box.addButton('取消', QMessageBox.RejectRole)
        box.setDefaultButton(backup_btn)
        box.exec()

        clicked = box.clickedButton()
        if clicked is backup_btn:
            return 'backup'
        if clicked is overwrite_btn:
            return 'overwrite'
        return 'cancel'

    def _prompt_init(self, title: str, message: str) -> None:
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Warning)
        box.setWindowTitle(title)
        box.setText(message)
        init_btn = box.addButton('去初始化', QMessageBox.AcceptRole)
        box.addButton('稍后', QMessageBox.RejectRole)
        box.setDefaultButton(init_btn)
        box.exec()
        if box.clickedButton() is init_btn:
            self.request_init.emit()

    # ---------- home / runtime ----------

    def _on_change_home_dir(self) -> None:
        current = self.paths.home_dir(self.settings)
        path = QFileDialog.getExistingDirectory(
            self, '选择 home 目录',
            str(current) if current.exists() else str(self.base_dir),
        )
        if not path:
            return
        new_path = Path(path).resolve()
        if new_path == current:
            return

        cfg = new_path / 'config.json'
        if not cfg.exists():
            QMessageBox.information(
                self, 'home 目录无 config.json',
                f'新 home 目录缺少 config.json：\n{cfg}\n\n'
                '需要执行「初始化」生成配置，或确认这是空目录后继续。'
            )

        self.settings.paths.home_dir = self.paths.relativize(new_path)
        self.settings_store.save(self.settings)
        self.log_bus.emit_line(
            LogSource.APP, f'home 目录已更换：{new_path}', 'info')
        self.refresh_all()
        self.settings_changed.emit('paths')

    def _on_change_runtime_dir(self) -> None:
        current = self.paths.runtime_dir(self.settings)
        path = QFileDialog.getExistingDirectory(
            self, '选择 runtime 目录',
            str(current) if current.exists() else str(self.base_dir),
        )
        if not path:
            return
        new_path = Path(path).resolve()
        if new_path == current:
            return

        missing = [n for n in RUNTIME_REQUIRED if not (new_path / n).exists()]
        if missing:
            QMessageBox.information(
                self, 'runtime 目录不完整',
                f'新 runtime 目录缺少：\n{", ".join(missing)}\n\n'
                '需要执行「初始化」生成运行文件，或确认这是空目录后继续。'
            )

        self.settings.paths.runtime_dir = self.paths.relativize(new_path)
        self.settings_store.save(self.settings)
        self.log_bus.emit_line(
            LogSource.APP, f'runtime 目录已更换：{new_path}', 'info')
        self.refresh_all()
        self.settings_changed.emit('paths')

    # ---------- 端口 ----------

    def _on_port_value_changed(self, _value: int) -> None:
        self.http_status.setText('')
        self.http_status.setProperty('state', '')
        self._repolish(self.http_status)
        self.game_status.setText('')
        self.game_status.setProperty('state', '')
        self._repolish(self.game_status)
        self._port_dirty = True
        self._update_apply_state()

    def _on_http_editing_finished(self) -> None:
        self._check_one('http')

    def _on_game_editing_finished(self) -> None:
        self._check_one('game')

    def _check_one(self, which: str) -> PortResult | None:
        if which == 'http':
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
        if result is None:
            label.setText('')
            label.setProperty('state', '')
            self._repolish(label)
            return
        if result.is_free:
            label.setText('✓ 空闲')
            label.setProperty('state', 'ok')
        else:
            label.setText(f'✗ {result.summary()}')
            label.setProperty('state', 'error')
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
            lines = [f'• 端口 {r.port} {r.summary()}' for r in problems]
            box = QMessageBox(self)
            box.setIcon(QMessageBox.Warning)
            box.setWindowTitle('保存端口')
            box.setText('以下端口当前不可用：')
            box.setInformativeText(
                '\n'.join(lines)
                + '\n\n仍要保存吗？启动服务时会再次检查。'
            )
            box.setStandardButtons(QMessageBox.Yes | QMessageBox.No)
            box.setDefaultButton(QMessageBox.No)
            box.button(QMessageBox.Yes).setText('强制保存')
            box.button(QMessageBox.No).setText('取消')
            if box.exec() != QMessageBox.Yes:
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
            f'端口已更新：HTTP {new_http} / 游戏 {new_game}',
            'info',
        )

        # 通知主窗口：主页面状态面板需要刷新
        self.settings_changed.emit('ports')

        from app.core.client_state import ClientMode, read_client_mode
        actual = read_client_mode(self.paths.active_client_dir(self.settings))
        if actual == ClientMode.LOCAL and cur_http != new_http:
            self._show_switch_hint(cur_http, new_http)

    def _show_switch_hint(self, old_http: int, new_http: int) -> None:
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Information)
        box.setWindowTitle('端口已修改')
        box.setText(
            f'HTTP 端口已从 {old_http} 改为 {new_http}。\n\n'
            '客户端仍指向旧端口。请到主页面点击「切换」重新生成客户端配置，'
            '否则游戏将无法连接本地服务。'
        )
        box.setStandardButtons(QMessageBox.Ok)
        box.button(QMessageBox.Ok).setText('我知道了')
        box.exec()

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

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from app.core.client_state import ClientMode, read_client_mode
from app.core.log_bus import LogBus, LogSource, SessionLogWriter
from app.core.paths import Paths
from app.core.port_check import PortResult, check_port
from app.core.server_process import ServerProcess, ServerState
from app.core.settings import Settings, SettingsStore


class SettingsPage(QWidget):
    settings_changed = Signal(str)  # 'ports' / 'log'

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

        layout.addWidget(self._build_startup_card())
        layout.addWidget(self._build_logging_card())
        layout.addWidget(self._build_ports_card())
        layout.addStretch(1)

        self._apply_server_state(self.server_process.state)
        self.server_process.state_changed.connect(self._on_server_state)

    # ---------- 启动行为卡片 ----------

    def _build_startup_card(self) -> QFrame:
        card = QFrame()
        card.setObjectName('Card')
        box = QVBoxLayout(card)
        box.setContentsMargins(16, 16, 16, 16)
        box.setSpacing(8)

        title = QLabel('启动行为')
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
            '关闭后，首次启动服务时会显示警告条，需要手动点击「继续启动」。\n'
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

    # ---------- 端口卡片 ----------

    def _build_ports_card(self) -> QFrame:
        card = QFrame()
        card.setObjectName('Card')
        box = QVBoxLayout(card)
        box.setContentsMargins(16, 16, 16, 16)
        box.setSpacing(10)

        title = QLabel('端口')
        title.setObjectName('CardTitle')
        box.addWidget(title)

        self.http_spin = QSpinBox()
        self.http_spin.setRange(1, 65535)
        self.http_spin.setValue(self.settings.server.http_port)
        self.http_spin.setMinimumWidth(120)
        self.http_spin.editingFinished.connect(self._on_http_editing_finished)
        self.http_spin.valueChanged.connect(self._on_port_value_changed)
        self.http_status = QLabel('')
        self.http_status.setObjectName('PortStatus')
        box.addLayout(self._port_row('HTTP 端口', self.http_spin, self.http_status))

        self.game_spin = QSpinBox()
        self.game_spin.setRange(1, 65535)
        self.game_spin.setValue(self.settings.server.game_port)
        self.game_spin.setMinimumWidth(120)
        self.game_spin.editingFinished.connect(self._on_game_editing_finished)
        self.game_spin.valueChanged.connect(self._on_port_value_changed)
        self.game_status = QLabel('')
        self.game_status.setObjectName('PortStatus')
        box.addLayout(self._port_row('游戏端口', self.game_spin, self.game_status))

        hint = QLabel(
            '游戏端口同时使用 TCP 和 UDP。修改后需要重新放行防火墙并更新玩家转发规则。'
        )
        hint.setObjectName('MutedText')
        hint.setWordWrap(True)
        box.addWidget(hint)

        action_row = QHBoxLayout()
        action_row.addStretch(1)
        self.apply_btn = QPushButton('应用')
        self.apply_btn.setObjectName('ToolbarButton')
        self.apply_btn.setEnabled(False)
        self.apply_btn.clicked.connect(self._on_apply_ports)
        action_row.addWidget(self.apply_btn)
        box.addLayout(action_row)

        return card

    def _port_row(self, label: str, spin: QSpinBox, status: QLabel) -> QHBoxLayout:
        row = QHBoxLayout()
        key = QLabel(label)
        key.setObjectName('StatusKey')
        key.setFixedWidth(110)
        row.addWidget(key)
        row.addWidget(spin)
        row.addWidget(status, 1)
        return row

    # ---------- 端口值变化 ----------

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
        self.apply_btn.setEnabled(self._port_dirty)

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
            text_lines = [f'• 端口 {r.port} {r.summary()}' for r in problems]
            box = QMessageBox(self)
            box.setIcon(QMessageBox.Warning)
            box.setWindowTitle('保存端口')
            box.setText('以下端口当前不可用：')
            box.setInformativeText(
                '\n'.join(text_lines) + '\n\n仍要保存吗？启动服务时会再次检查。'
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

        # local 模式下 HTTP 端口变了，客户端 url.txt 仍指向旧端口
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

    # ---------- 服务状态联动 ----------

    def _on_server_state(self, state: str) -> None:
        self._apply_server_state(ServerState(state))

    def _apply_server_state(self, state: ServerState) -> None:
        editable = state == ServerState.STOPPED
        self.http_spin.setEnabled(editable)
        self.game_spin.setEnabled(editable)
        self.apply_btn.setEnabled(editable and self._port_dirty)

    @staticmethod
    def _repolish(widget: QWidget) -> None:
        widget.style().unpolish(widget)
        widget.style().polish(widget)

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from app.core.game_process import is_game_running
from app.core.log_bus import LogBus
from app.core.paths import Paths
from app.core.server_process import ServerProcess, ServerState
from app.core.settings import Settings
from app.widgets.log_view import LogView


class MainPage(QWidget):
    def __init__(
        self,
        base_dir: Path,
        paths: Paths,
        settings: Settings,
        log_bus: LogBus,
        server_process: ServerProcess,
    ):
        super().__init__()
        self.base_dir = base_dir
        self.paths = paths
        self.settings = settings
        self.log_bus = log_bus
        self.server_process = server_process

        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(16)

        title = QLabel('主页面')
        title.setObjectName('PageTitle')
        layout.addWidget(title)

        layout.addWidget(self._build_toolbar())
        layout.addWidget(self._build_status_card())
        layout.addWidget(self._build_log_card(), 1)

        self.refresh_status()
        self._apply_server_state(self.server_process.state)
        self.server_process.state_changed.connect(self._on_server_state)

    # ---------- 顶部栏 ----------

    def _build_toolbar(self) -> QFrame:
        bar = QFrame()
        bar.setObjectName('Toolbar')
        row = QHBoxLayout(bar)
        row.setContentsMargins(12, 8, 12, 8)
        row.setSpacing(8)

        self.prepare_btn = QPushButton('初始化')
        self.prepare_btn.setObjectName('ToolbarButton')
        self.prepare_btn.setEnabled(False)
        row.addWidget(self.prepare_btn)

        self.server_btn = QPushButton('启动服务')
        self.server_btn.setObjectName('ToolbarButton')
        self.server_btn.clicked.connect(self._on_server_button)
        row.addWidget(self.server_btn)

        self.game_btn = QPushButton('启动游戏')
        self.game_btn.setObjectName('ToolbarButton')
        self.game_btn.setEnabled(False)
        row.addWidget(self.game_btn)

        self.mode_badge = QLabel('服务端: ● Unknown')
        self.mode_badge.setObjectName('ModeBadge')
        row.addWidget(self.mode_badge)

        self.switch_btn = QPushButton('切换')
        self.switch_btn.setObjectName('ToolbarButton')
        self.switch_btn.setEnabled(False)
        row.addWidget(self.switch_btn)

        row.addStretch(1)

        for text in ('📂 日志', '🗄 data'):
            btn = QPushButton(text)
            btn.setObjectName('IconButton')
            btn.setEnabled(False)
            row.addWidget(btn)

        return bar

    # ---------- 状态面板 ----------

    def _build_status_card(self) -> QFrame:
        card = QFrame()
        card.setObjectName('Card')
        box = QVBoxLayout(card)
        box.setContentsMargins(16, 16, 16, 16)
        box.setSpacing(8)

        title = QLabel('状态')
        title.setObjectName('CardTitle')
        box.addWidget(title)

        self._status_values: dict[str, QLabel] = {}
        for key, label in (
            ('client', '游戏目录'),
            ('mode', '服务端'),
            ('version', 'hoshimi 版本'),
            ('home', 'home 目录'),
            ('runtime', 'runtime 目录'),
            ('ports', '端口'),
        ):
            row = QHBoxLayout()
            k = QLabel(label)
            k.setObjectName('StatusKey')
            k.setFixedWidth(110)
            v = QLabel('—')
            v.setObjectName('StatusValue')
            v.setTextInteractionFlags(Qt.TextSelectableByMouse)
            row.addWidget(k)
            row.addWidget(v, 1)
            box.addLayout(row)
            self._status_values[key] = v

        return card

    # ---------- 日志面板 ----------

    def _build_log_card(self) -> QFrame:
        card = QFrame()
        card.setObjectName('Card')
        box = QVBoxLayout(card)
        box.setContentsMargins(16, 16, 16, 16)
        box.setSpacing(8)

        title = QLabel('hoshimi 日志')
        title.setObjectName('CardTitle')
        box.addWidget(title)

        self.log_view = LogView(self.log_bus)
        box.addWidget(self.log_view, 1)

        return card

    # ---------- 刷新 ----------

    def refresh_status(self) -> None:
        s = self.settings

        client = s.active_client()
        client_text = '—'
        if client and client.exe_path:
            client_text = client.exe_path

        mode_text = {'local': '本地服 Local', 'official': '官服 Official'}.get(
            s.server.mode, '—'
        )

        version_text = s.server.version or '—'

        home = self.paths.home_dir(s)
        runtime = self.paths.runtime_dir(s)

        ports_text = f'HTTP {s.server.http_port} / 游戏 {s.server.game_port}（TCP+UDP）'

        self._status_values['client'].setText(client_text)
        self._status_values['mode'].setText(mode_text)
        self._status_values['version'].setText(version_text)
        self._status_values['home'].setText(str(home))
        self._status_values['runtime'].setText(str(runtime))
        self._status_values['ports'].setText(ports_text)

        badge = {'local': '● Local', 'official': '● Official'}.get(
            s.server.mode, '● Unknown'
        )
        self.mode_badge.setText(f'服务端: {badge}')

    # ---------- 服务控制 ----------

    def _on_server_button(self) -> None:
        sp = self.server_process
        if sp.state == ServerState.STOPPED:
            sp.start()
            return
        if sp.state != ServerState.RUNNING:
            # STARTING / STOPPING 期间按钮已禁用，不会到这里
            return
        if self._confirm_stop():
            sp.stop()

    def _confirm_stop(self) -> bool:
        """所有停止都经过这里。

        confirmBeforeStopServer=false 时直接放行，否则按场景选择文案和图标。
        """
        if not self.settings.behavior.confirm_before_stop_server:
            return True

        mode = self.settings.server.mode
        client_dir = self.paths.active_client_dir(self.settings)
        game_running = is_game_running(client_dir)

        if game_running and mode == 'local':
            icon = QMessageBox.Warning
            text = (
                '游戏正在运行，停止服务会导致客户端断连，\n'
                '未保存的进度可能丢失。\n\n'
                '确定停止服务吗？'
            )
        elif game_running and mode == 'official':
            icon = QMessageBox.Warning
            text = (
                '游戏正在运行。当前为官服模式，\n'
                '停止本地服务不影响游戏连接。\n\n'
                '确定停止服务吗？'
            )
        else:
            icon = QMessageBox.Question
            text = '确定要停止 Hoshimi 服务吗？'

        box = QMessageBox(self)
        box.setIcon(icon)
        box.setWindowTitle('停止服务')
        box.setText(text)
        box.setStandardButtons(QMessageBox.Yes | QMessageBox.No)
        box.setDefaultButton(QMessageBox.No)
        box.button(QMessageBox.Yes).setText('停止')
        box.button(QMessageBox.No).setText('取消')
        return box.exec() == QMessageBox.Yes

    def _on_server_state(self, state: str) -> None:
        self._apply_server_state(ServerState(state))

    def _apply_server_state(self, state: ServerState) -> None:
        if state == ServerState.STOPPED:
            self.server_btn.setText('启动服务')
            self.server_btn.setEnabled(True)
            self._set_btn_variant('start')
        elif state == ServerState.STARTING:
            self.server_btn.setText('启动中…')
            self.server_btn.setEnabled(False)
            self._set_btn_variant('busy')
        elif state == ServerState.RUNNING:
            self.server_btn.setText('停止服务')
            self.server_btn.setEnabled(True)
            self._set_btn_variant('stop')
        elif state == ServerState.STOPPING:
            self.server_btn.setText('停止中…')
            self.server_btn.setEnabled(False)
            self._set_btn_variant('busy')

    def _set_btn_variant(self, variant: str) -> None:
        self.server_btn.setProperty('variant', variant)
        self.server_btn.style().unpolish(self.server_btn)
        self.server_btn.style().polish(self.server_btn)

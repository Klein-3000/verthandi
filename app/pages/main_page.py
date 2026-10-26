from __future__ import annotations

import shutil
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import Qt, QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QCheckBox,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from app.core.client_state import ClientMode, client_fingerprint, read_client_mode
from app.core.game_process import GameWatcher, is_game_running, launch_game
from app.core.hoshimi_finder import find_hoshimi_exe
from app.core.log_bus import LogBus, LogSource
from app.core.paths import Paths
from app.core.preflight import CheckItem, Severity, run_preflight
from app.core.prepare_process import PrepareProcess
from app.core.server_process import ServerProcess, ServerState
from app.core.settings import ClientEntry, Settings, SettingsStore
from app.core.switch_client import SwitchError, SwitchOperation
from app.widgets.log_view import LogView
from app.widgets.prepare_dialog import PrepareDialog
from app.widgets.toast import show_toast


class MainPage(QWidget):
    def __init__(
        self,
        base_dir: Path,
        paths: Paths,
        settings: Settings,
        settings_store: SettingsStore,
        log_bus: LogBus,
        server_process: ServerProcess,
        game_watcher: GameWatcher,
    ):
        super().__init__()
        self.base_dir = base_dir
        self.paths = paths
        self.settings = settings
        self.settings_store = settings_store
        self.log_bus = log_bus
        self.server_process = server_process
        self.game_watcher = game_watcher

        self._preparing = False
        self._prep: PrepareProcess | None = None
        self._prep_dialog: PrepareDialog | None = None
        self._last_backup_dir: Path | None = None

        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(16)

        title = QLabel("主页面")
        title.setObjectName("PageTitle")
        layout.addWidget(title)

        layout.addWidget(self._build_toolbar())
        self.warning_banner = self._build_warning_banner()
        layout.addWidget(self.warning_banner)
        layout.addWidget(self._build_status_card())
        layout.addWidget(self._build_log_card(), 1)

        self.refresh_status()
        self._apply_server_state(self.server_process.state)
        self.server_process.state_changed.connect(self._on_server_state)
        self.server_process.waiting_for_confirm.connect(self._on_waiting_confirm)
        self.server_process.confirm_resolved.connect(self._on_confirm_resolved)
        self.game_watcher.running_changed.connect(self._on_game_running_changed)

    # ---------- 顶部栏 ----------

    def _build_toolbar(self) -> QFrame:
        bar = QFrame()
        bar.setObjectName("Toolbar")
        row = QHBoxLayout(bar)
        row.setContentsMargins(12, 8, 12, 8)
        row.setSpacing(8)

        self.prepare_btn = QPushButton("初始化")
        self.prepare_btn.setObjectName("ToolbarButton")
        self.prepare_btn.clicked.connect(self._on_prepare_button)
        row.addWidget(self.prepare_btn)

        self.server_btn = QPushButton("启动服务")
        self.server_btn.setObjectName("ToolbarButton")
        self.server_btn.clicked.connect(self._on_server_button)
        row.addWidget(self.server_btn)

        self.game_btn = QPushButton("启动游戏")
        self.game_btn.setObjectName("ToolbarButton")
        self.game_btn.clicked.connect(self._on_game_button)
        row.addWidget(self.game_btn)

        self.mode_badge = QLabel("连接: ● Unknown")
        self.mode_badge.setObjectName("ModeBadge")
        row.addWidget(self.mode_badge)

        self.switch_btn = QPushButton("切换")
        self.switch_btn.setObjectName("ToolbarButton")
        self.switch_btn.setEnabled(False)
        self.switch_btn.clicked.connect(self._on_switch_button)
        row.addWidget(self.switch_btn)

        row.addStretch(1)

        row.addStretch(1)

        self.logs_btn = QPushButton("📂 日志")
        self.logs_btn.setObjectName("IconButton")
        self.logs_btn.setToolTip("打开会话日志目录")
        self.logs_btn.clicked.connect(self._on_open_logs)
        row.addWidget(self.logs_btn)

        self.data_btn = QPushButton("🗄 data")
        self.data_btn.setObjectName("IconButton")
        self.data_btn.setToolTip("打开 home/data 目录")
        self.data_btn.clicked.connect(self._on_open_data)
        row.addWidget(self.data_btn)

        self._apply_prepare_state()
        return bar

    # ---------- 警告条 ----------

    def _build_warning_banner(self) -> QFrame:
        banner = QFrame()
        banner.setObjectName("WarningBanner")
        banner.setVisible(False)
        row = QHBoxLayout(banner)
        row.setContentsMargins(12, 8, 12, 8)
        row.setSpacing(8)

        icon = QLabel("⚠")
        icon.setObjectName("WarningIcon")
        row.addWidget(icon)

        text = QLabel("服务端正在等待首次启动确认。")
        text.setObjectName("WarningText")
        row.addWidget(text, 1)

        self.confirm_btn = QPushButton("继续启动")
        self.confirm_btn.setObjectName("ToolbarButton")
        self.confirm_btn.clicked.connect(self._on_confirm_clicked)
        row.addWidget(self.confirm_btn)

        return banner

    def _on_waiting_confirm(self) -> None:
        self.warning_banner.setVisible(True)

    def _on_confirm_resolved(self) -> None:
        self.warning_banner.setVisible(False)

    def _on_confirm_clicked(self) -> None:
        self.server_process.send_enter()

    # ---------- 状态卡片 / 日志卡片 ----------

    def _build_status_card(self) -> QFrame:
        card = QFrame()
        card.setObjectName("Card")
        box = QVBoxLayout(card)
        box.setContentsMargins(16, 16, 16, 16)
        box.setSpacing(8)

        title = QLabel("状态")
        title.setObjectName("CardTitle")
        box.addWidget(title)

        self._status_values: dict[str, QLabel] = {}
        for key, label in (
            ("client", "游戏目录"),
            ("connection", "客户端连接"),
            ("version", "hoshimi 版本"),
            ("home", "home 目录"),
            ("runtime", "runtime 目录"),
            ("ports", "端口"),
            ("service", "本地服务"),
            ("game", "游戏状态"),
        ):
            row = QHBoxLayout()
            k = QLabel(label)
            k.setObjectName("StatusKey")
            k.setFixedWidth(110)
            v = QLabel("—")
            v.setObjectName("StatusValue")
            v.setTextInteractionFlags(Qt.TextSelectableByMouse)
            row.addWidget(k)
            row.addWidget(v, 1)
            box.addLayout(row)
            self._status_values[key] = v

        return card

    def _build_log_card(self) -> QFrame:
        card = QFrame()
        card.setObjectName("Card")
        box = QVBoxLayout(card)
        box.setContentsMargins(16, 16, 16, 16)
        box.setSpacing(8)

        title_row = QHBoxLayout()
        title_row.setContentsMargins(0, 0, 0, 0)
        title_row.setSpacing(8)

        title = QLabel("hoshimi 日志")
        title.setObjectName("CardTitle")
        title_row.addWidget(title)
        title_row.addStretch(1)

        self.gm_btn = QPushButton("🌐 GM 后台")
        self.gm_btn.setObjectName("IconButton")
        self.gm_btn.setToolTip("服务未运行")
        self.gm_btn.setEnabled(False)
        self.gm_btn.clicked.connect(self._on_open_gm)
        title_row.addWidget(self.gm_btn)

        box.addLayout(title_row)

        self.log_view = LogView(self.log_bus)
        box.addWidget(self.log_view, 1)
        return card

    # ---------- 状态刷新 ----------

    def refresh_status(self) -> None:
        s = self.settings

        client = s.active_client()
        client_text = client.exe_path if client and client.exe_path else "—"

        actual = read_client_mode(self.paths.active_client_dir(s))
        connection_text = {
            ClientMode.LOCAL: "本地服 Local",
            ClientMode.OFFICIAL: "官服 Official",
            ClientMode.UNKNOWN: "Unknown",
        }.get(actual, "Unknown")

        _exe, actual_version, auto_selected = find_hoshimi_exe(self.paths, s)
        if actual_version:
            if auto_selected:
                version_text = f"{actual_version}（自动选择）"
            else:
                version_text = actual_version
        else:
            version_text = "—"
        home = self.paths.home_dir(s)
        runtime = self.paths.runtime_dir(s)
        ports_text = f"HTTP {s.server.http_port} / 游戏 {s.server.game_port}（TCP+UDP）"

        self._status_values["client"].setText(client_text)
        self._status_values["connection"].setText(connection_text)
        self._status_values["version"].setText(version_text)
        self._status_values["home"].setText(str(home))
        self._status_values["runtime"].setText(str(runtime))
        self._status_values["ports"].setText(ports_text)

        badge = {
            ClientMode.LOCAL: "● Local",
            ClientMode.OFFICIAL: "● Official",
        }.get(actual, "● Unknown")
        self.mode_badge.setText(f"连接: {badge}")

        self._refresh_switch_btn(actual)
        self._refresh_runtime_state()

    def _refresh_switch_btn(self, actual: ClientMode) -> None:
        if actual == ClientMode.LOCAL:
            self.switch_btn.setText("切换到官服")
            self.switch_btn.setEnabled(True)
            self.switch_btn.setToolTip("")
            self._set_btn_variant(self.switch_btn, "switch")
        elif actual == ClientMode.OFFICIAL:
            self.switch_btn.setText("切换到本地")
            self.switch_btn.setEnabled(True)
            self.switch_btn.setToolTip("")
            self._set_btn_variant(self.switch_btn, "switch")
        else:
            self.switch_btn.setText("切换")
            self.switch_btn.setEnabled(False)
            self.switch_btn.setToolTip("无法读取客户端配置，请用官方启动器修复")
            self._set_btn_variant(self.switch_btn, "busy")

    def _refresh_runtime_state(self) -> None:
        sp_state = self.server_process.state
        if sp_state == ServerState.RUNNING:
            service_text, service_state = "● 运行中", "ok"
        elif sp_state == ServerState.STARTING:
            service_text, service_state = "● 启动中…", "muted"
        elif sp_state == ServerState.STOPPING:
            service_text, service_state = "● 停止中…", "muted"
        else:
            service_text, service_state = "● 已停止", "muted"

        lbl = self._status_values["service"]
        lbl.setText(service_text)
        lbl.setProperty("state", service_state)
        self._repolish(lbl)

        if self.game_watcher.running:
            game_text, game_state = "● 运行中", "ok"
        else:
            game_text, game_state = "● 未运行", "muted"
        lbl = self._status_values["game"]
        lbl.setText(game_text)
        lbl.setProperty("state", game_state)
        self._repolish(lbl)

        self._apply_game_button_state()

        if sp_state == ServerState.RUNNING:
            self.gm_btn.setEnabled(True)
            self.gm_btn.setToolTip("打开 GM 后台")
        else:
            self.gm_btn.setEnabled(False)
            self.gm_btn.setToolTip("服务未运行")

    def _apply_game_button_state(self) -> None:
        if self.game_watcher.running:
            self.game_btn.setText("游戏运行中")
            self.game_btn.setEnabled(False)
            self.game_btn.setToolTip("游戏已在运行")
            self._set_btn_variant(self.game_btn, "busy")
            return

        client = self.settings.active_client()
        exe_ok = bool(client and client.exe_path and Path(client.exe_path).exists())
        if not exe_ok:
            self.game_btn.setText("启动游戏")
            self.game_btn.setEnabled(False)
            self.game_btn.setToolTip("请先选择 AetherGazer.exe")
            self._set_btn_variant(self.game_btn, "busy")
            return

        actual = read_client_mode(self.paths.active_client_dir(self.settings))
        if actual == ClientMode.UNKNOWN:
            self.game_btn.setText("启动游戏")
            self.game_btn.setEnabled(False)
            self.game_btn.setToolTip("客户端配置损坏，请修复")
            self._set_btn_variant(self.game_btn, "busy")
            return

        if (
            actual == ClientMode.LOCAL
            and self.server_process.state != ServerState.RUNNING
        ):
            self.game_btn.setText("启动游戏")
            self.game_btn.setEnabled(False)
            self.game_btn.setToolTip("本地模式需要先启动服务")
            self._set_btn_variant(self.game_btn, "busy")
            return

        self.game_btn.setText("启动游戏")
        self.game_btn.setEnabled(True)
        self.game_btn.setToolTip("")
        self._set_btn_variant(self.game_btn, "start")

    # ---------- 初始化 ----------

    def _on_prepare_button(self) -> None:
        if self._preparing:
            return
        if self.server_process.state != ServerState.STOPPED:
            return
        if self.game_watcher.running:
            return

        client = self.settings.active_client()
        if client is None or not client.exe_path:
            if not self._prompt_select_client():
                return
            client = self.settings.active_client()
            if client is None or not client.exe_path:
                return

        client_exe = Path(client.exe_path)
        if not client_exe.exists():
            QMessageBox.critical(
                self,
                "无法初始化",
                f"AetherGazer.exe 不存在：\n{client_exe}",
            )
            return

        if not self._confirm_prepare(client_exe.parent):
            return

        self._start_prepare(client_exe.parent)

    def _prompt_select_client(self) -> bool:
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Information)
        box.setWindowTitle("需要选择游戏目录")
        box.setText("初始化需要指定 AetherGazer.exe 的位置。")
        box.setInformativeText(
            "请选择游戏安装目录下的 AetherGazer.exe 文件，而不是文件夹。"
        )
        browse = box.addButton("浏览…", QMessageBox.AcceptRole)
        box.addButton("取消", QMessageBox.RejectRole)
        box.setDefaultButton(browse)
        box.exec()
        if box.clickedButton() is not browse:
            return False

        path, _ = QFileDialog.getOpenFileName(
            self,
            "选择 AetherGazer.exe",
            str(Path.home()),
            "AetherGazer (AetherGazer.exe);;可执行文件 (*.exe)",
        )
        if not path:
            return False
        exe = Path(path)
        if exe.name.lower() != "aethergazer.exe":
            box = QMessageBox(self)
            box.setIcon(QMessageBox.Critical)
            box.setWindowTitle("文件不匹配")
            box.setText(f"选中的文件不是 AetherGazer.exe：\n{exe.name}")
            box.exec()
            return False

        entry = self.settings.active_client() or ClientEntry(
            id="",
            name=exe.parent.name,
            exe_path=str(exe),
        )
        entry.exe_path = str(exe)
        if not entry.name:
            entry.name = exe.parent.name
        self.settings.set_active_client(entry)
        self.settings_store.save(self.settings)
        self.game_watcher.set_client_dir(self.paths.active_client_dir(self.settings))
        self.refresh_status()
        return True

    def _confirm_prepare(self, client_dir: Path) -> bool:
        runtime = self.paths.runtime_dir(self.settings)
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Warning)
        box.setWindowTitle("初始化运行文件")
        box.setText(
            "初始化将从客户端提取运行文件，可能需要 1–2 分钟。\n"
            "期间请勿关闭 Verthandi。"
        )
        box.setInformativeText(
            f"游戏目录：{client_dir}\n"
            f"运行文件：{runtime}\n\n"
            "如果 runtime 已存在，将先备份到 backup/ 目录。"
        )
        start = box.addButton("开始初始化", QMessageBox.AcceptRole)
        box.addButton("取消", QMessageBox.RejectRole)
        box.setDefaultButton(start)
        box.exec()
        return box.clickedButton() is start

    def _backup_runtime_before_prepare(self) -> tuple[bool, Path | None]:
        """返回 (ok, backup_dir)。
        - ok=False: 备份失败（已弹错）
        - ok=True, backup_dir=None: 无 runtime，无需备份
        - ok=True, backup_dir=Path: 备份成功
        """
        runtime = self.paths.runtime_dir(self.settings)
        if not runtime.exists():
            return True, None

        parent = runtime.parent
        backup_root = parent / "backup"
        version = self.settings.prepare.last_prepared_version or "unknown"
        ts = datetime.now().strftime("%Y%m%d-%H%M%S")
        backup_dir = backup_root / f"runtime-{version}-{ts}"

        try:
            backup_root.mkdir(parents=True, exist_ok=True)
            try:
                runtime.rename(backup_dir)
            except OSError:
                shutil.move(str(runtime), str(backup_dir))
            try:
                (backup_dir / "version.txt").write_text(
                    version + "\n", encoding="utf-8"
                )
            except OSError:
                pass
        except OSError as e:
            QMessageBox.critical(
                self,
                "无法备份旧运行文件",
                f"备份旧 runtime 失败，初始化已取消。\n\n{e}",
            )
            return False, None

        self.log_bus.emit_line(
            LogSource.APP,
            f"旧运行文件已备份：{backup_dir}",
            "info",
        )
        return True, backup_dir

    def _start_prepare(self, client_dir: Path) -> None:
        self._preparing = True
        self._apply_prepare_state()

        ok, backup_dir = self._backup_runtime_before_prepare()
        if not ok:
            self._preparing = False
            self._apply_prepare_state()
            return

        self._last_backup_dir = backup_dir

        self._prep = PrepareProcess(self.paths, self.settings, self.log_bus, self)
        self._prep.finished.connect(self._on_prepare_finished)
        self._prep.failed_to_start.connect(self._on_prepare_failed)

        if not self._prep.start(client_dir):
            self._on_prepare_failed("无法启动 prepare 进程")
            return

        self._prep_dialog = PrepareDialog(self._prep, self)
        self._prep_dialog.exec()

    def _on_prepare_finished(self, code: int) -> None:
        self._preparing = False
        self._apply_prepare_state()

        if code == 0:
            version_info = self.server_process.version()
            version = self.settings.server.version or (
                version_info[0] if version_info else ""
            )
            client_dir = self.paths.active_client_dir(self.settings)
            self.settings.prepare.last_prepared_version = version
            self.settings.prepare.last_prepared_at = datetime.now().isoformat(
                timespec="seconds"
            )
            self.settings.prepare.client_fingerprint = client_fingerprint(client_dir)
            self.settings_store.save(self.settings)
            self.log_bus.emit_line(LogSource.APP, "初始化完成", "info")
            self._last_backup_dir = None
            self.refresh_status()
            return

        self._handle_prepare_failure(code)

    def _handle_prepare_failure(self, code: int) -> None:
        backup_dir = self._last_backup_dir
        self._last_backup_dir = None

        if backup_dir is None or not backup_dir.exists():
            if code == -1:
                self.log_bus.emit_line(
                    LogSource.APP, "初始化已取消，runtime 可能不完整", "warning"
                )
                QMessageBox.warning(
                    self,
                    "初始化已取消",
                    "初始化被取消，runtime 可能不完整。\n请重新执行初始化后再启动服务。",
                )
            else:
                QMessageBox.critical(
                    self,
                    "初始化失败",
                    f"hoshimi prepare 退出码 {code}。\n请查看日志面板。",
                )
            return

        reason = "初始化被取消" if code == -1 else f"初始化失败（退出码 {code}）"
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Warning)
        box.setWindowTitle("初始化未完成")
        box.setText(f"{reason}。")
        box.setInformativeText(
            "当前 runtime 可能不完整。\n\n"
            f"此前的运行文件已备份到：\n{backup_dir}\n\n"
            "是否恢复此前的运行文件？"
        )
        restore_btn = box.addButton("恢复旧运行文件", QMessageBox.AcceptRole)
        box.addButton("保留当前并关闭", QMessageBox.RejectRole)
        box.setDefaultButton(restore_btn)
        box.exec()

        if box.clickedButton() is restore_btn:
            self._restore_backup(backup_dir)
        else:
            self.log_bus.emit_line(
                LogSource.APP,
                f"保留不完整的 runtime，备份仍在 {backup_dir}",
                "warning",
            )

    def _restore_backup(self, backup_dir: Path) -> None:
        runtime = self.paths.runtime_dir(self.settings)
        parent = runtime.parent
        ts = datetime.now().strftime("%Y%m%d-%H%M%S")
        failed_dir = parent / "backup" / f"runtime-failed-{ts}"

        try:
            failed_dir.parent.mkdir(parents=True, exist_ok=True)
            if runtime.exists():
                try:
                    runtime.rename(failed_dir)
                except OSError:
                    shutil.move(str(runtime), str(failed_dir))
            try:
                backup_dir.rename(runtime)
            except OSError:
                shutil.move(str(backup_dir), str(runtime))
        except OSError as e:
            QMessageBox.critical(
                self,
                "恢复失败",
                f"无法恢复旧运行文件：\n{e}\n\n旧备份仍在：{backup_dir}",
            )
            return

        self.log_bus.emit_line(
            LogSource.APP,
            f"已恢复旧运行文件（不完整的 runtime 保留在 {failed_dir}）",
            "info",
        )
        self.refresh_status()

    def _on_prepare_failed(self, message: str) -> None:
        self._preparing = False
        self._apply_prepare_state()

        backup_dir = self._last_backup_dir
        self._last_backup_dir = None

        if backup_dir is not None and backup_dir.exists():
            runtime = self.paths.runtime_dir(self.settings)
            try:
                if not runtime.exists():
                    try:
                        backup_dir.rename(runtime)
                    except OSError:
                        shutil.move(str(backup_dir), str(runtime))
                    self.log_bus.emit_line(
                        LogSource.APP,
                        "初始化启动失败，已自动恢复旧运行文件",
                        "warning",
                    )
            except OSError as e:
                message += f"\n\n无法恢复旧运行文件：{e}\n备份位置：{backup_dir}"

        QMessageBox.critical(self, "初始化失败", message)

    def _apply_prepare_state(self) -> None:
        if self._preparing:
            self.prepare_btn.setText("初始化中…")
            self.prepare_btn.setEnabled(False)
            self.prepare_btn.setToolTip("")
            self._set_btn_variant(self.prepare_btn, "busy")
            return

        if self.server_process.state != ServerState.STOPPED:
            self.prepare_btn.setText("初始化")
            self.prepare_btn.setEnabled(False)
            self.prepare_btn.setToolTip("请先停止服务")
            self._set_btn_variant(self.prepare_btn, "busy")
            return

        if self.game_watcher.running:
            self.prepare_btn.setText("初始化")
            self.prepare_btn.setEnabled(False)
            self.prepare_btn.setToolTip("请先退出游戏")
            self._set_btn_variant(self.prepare_btn, "busy")
            return

        self.prepare_btn.setText("初始化")
        self.prepare_btn.setEnabled(True)
        self.prepare_btn.setToolTip("")
        self._set_btn_variant(self.prepare_btn, "danger")

    # ---------- 服务控制 ----------

    def _on_server_button(self) -> None:
        sp = self.server_process
        if sp.state == ServerState.STOPPED:
            items = run_preflight(self.paths, self.settings)
            errors = [i for i in items if not i.ok and i.severity == Severity.ERROR]
            warnings = [i for i in items if not i.ok and i.severity == Severity.WARNING]
            if errors:
                self._show_preflight_errors(errors)
                return
            if warnings and not self._confirm_preflight_warnings(warnings):
                return

            actual = read_client_mode(self.paths.active_client_dir(self.settings))
            if actual == ClientMode.OFFICIAL:
                self.log_bus.emit_line(
                    LogSource.APP,
                    "当前客户端连接官服，本地服务仅供 GM 后台使用",
                    "info",
                )
            sp.start()
            return

        if sp.state != ServerState.RUNNING:
            return

        if self._confirm_stop():
            sp.stop()

    def _confirm_stop(self) -> bool:
        if not self.settings.behavior.confirm_before_stop_server:
            return True

        actual = read_client_mode(self.paths.active_client_dir(self.settings))
        game_running = is_game_running(self.paths.active_client_dir(self.settings))

        if game_running and actual == ClientMode.LOCAL:
            icon, text = (
                QMessageBox.Warning,
                (
                    "游戏正在运行，停止服务会导致客户端断连，\n"
                    "未保存的进度可能丢失。\n\n确定停止服务吗？"
                ),
            )
        elif game_running and actual == ClientMode.OFFICIAL:
            icon, text = (
                QMessageBox.Warning,
                (
                    "游戏正在运行。当前客户端连接官服，\n"
                    "停止本地服务不影响游戏连接。\n\n确定停止服务吗？"
                ),
            )
        else:
            icon, text = QMessageBox.Question, "确定要停止 Hoshimi 服务吗？"

        box = QMessageBox(self)
        box.setIcon(icon)
        box.setWindowTitle("停止服务")
        box.setText(text)
        box.setStandardButtons(QMessageBox.Yes | QMessageBox.No)
        box.setDefaultButton(QMessageBox.No)
        box.button(QMessageBox.Yes).setText("停止")
        box.button(QMessageBox.No).setText("取消")
        return box.exec() == QMessageBox.Yes

    # ---------- 启动游戏 ----------

    def _on_game_button(self) -> None:
        if self.game_watcher.running:
            return

        client = self.settings.active_client()
        if client is None or not client.exe_path:
            QMessageBox.critical(self, "无法启动游戏", "尚未选择游戏目录")
            return

        exe = Path(client.exe_path)
        if not exe.exists():
            QMessageBox.critical(
                self,
                "无法启动游戏",
                f"AetherGazer.exe 不存在：\n{exe}",
            )
            return

        ok, msg = launch_game(exe.parent)
        if ok:
            self.log_bus.emit_line(LogSource.APP, "游戏已启动", "info")
            show_toast(self, "游戏已启动")
            self.game_watcher.refresh()
        else:
            QMessageBox.critical(self, "无法启动游戏", msg)

    # ---------- 快捷入口 ----------

    def _open_directory(self, path: Path, label: str) -> None:
        """打开目录；不存在时先创建。"""
        try:
            path.mkdir(parents=True, exist_ok=True)
        except OSError as e:
            QMessageBox.warning(
                self,
                "无法打开目录",
                f"{label}目录创建失败：\n{path}\n\n{e}",
            )
            return
        if not QDesktopServices.openUrl(QUrl.fromLocalFile(str(path))):
            QMessageBox.warning(
                self,
                "无法打开目录",
                f"系统未能打开目录：\n{path}",
            )

    def _on_open_logs(self) -> None:
        self._open_directory(self.paths.logs_dir(self.settings), "日志")

    def _on_open_data(self) -> None:
        # home/data 是 hoshimi 的存档根目录
        home = self.paths.home_dir(self.settings)
        self._open_directory(home / "data", "data")

    def _on_open_gm(self) -> None:
        url = f"http://127.0.0.1:{self.settings.server.http_port}/gm/"
        if not QDesktopServices.openUrl(QUrl(url)):
            QMessageBox.warning(
                self,
                "无法打开 GM 后台",
                f"系统未能打开浏览器：\n{url}",
            )

    # ---------- 切换服务端 ----------

    def _on_switch_button(self) -> None:
        actual = read_client_mode(self.paths.active_client_dir(self.settings))
        if actual == ClientMode.LOCAL:
            target, target_label = "Official", "官服"
        elif actual == ClientMode.OFFICIAL:
            target, target_label = "Local", "本地服"
        else:
            return

        if self.game_watcher.running:
            QMessageBox.warning(
                self, "无法切换", "游戏正在运行，请先退出客户端再切换。"
            )
            return

        op = self._build_switch_op()

        # 前置检查
        checks = op.preflight(target)
        failures = [c for c in checks if not c.ok]
        if failures:
            self._show_switch_checks(failures, target_label)
            return

        # 二次确认
        if not self._confirm_switch(target_label, checks):
            return

        # 执行
        self._do_switch(op, target, target_label)

    def _build_switch_op(self) -> SwitchOperation:
        from pathlib import Path as _Path  # 避免顶部已用过的 Path 冲突

        return SwitchOperation(
            client_dir=self.paths.active_client_dir(self.settings),
            state_dir=self.paths.client_switch_dir(self.settings),
            prefs_dir=_Path.home() / "AppData" / "LocalLow" / "yongshi" / "AetherGazer",
            hosts_path=_Path(
                __import__("os").environ.get("SystemRoot", r"C:\Windows"),
            )
            / "System32"
            / "drivers"
            / "etc"
            / "hosts",
            local_port=self.settings.server.http_port,
            log_bus=self.log_bus,
        )

    def _show_switch_checks(self, failures: list, target_label: str) -> None:
        lines = "\n".join(f"• {c.label}：{c.message}" for c in failures)
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Warning)
        box.setWindowTitle("无法切换到" + target_label)
        box.setText("切换前置检查未通过：")
        box.setInformativeText(lines)
        box.setStandardButtons(QMessageBox.Ok)
        box.exec()

    def _confirm_switch(self, target_label: str, checks: list) -> bool:
        # 服务在运行时给额外提示（不阻止）
        extra = ""
        if self.server_process.state == ServerState.RUNNING and target_label == "官服":
            extra = (
                "\n注意：服务端仍在运行，切换后客户端不再连接本地服务。\n"
                "如不再需要 GM 后台，建议切换后停止服务以释放端口。"
            )

        box = QMessageBox(self)
        box.setIcon(QMessageBox.Question)
        box.setWindowTitle(f"切换到{target_label}")
        box.setText(f"将把客户端切换到{target_label}。")
        box.setInformativeText(
            "会修改以下内容：\n"
            "• 客户端 url.txt\n"
            "• resources.assets 中的 SDK 端点\n"
            "• 交换两套 SDK 登录缓存\n"
            "• 清理 hosts 中的 ys4fun.com 回环映射（如有）\n"
            f"{extra}\n\n"
            "确定切换吗？"
        )
        switch_btn = box.addButton("确认切换", QMessageBox.AcceptRole)
        box.addButton("取消", QMessageBox.RejectRole)
        box.setDefaultButton(switch_btn)
        box.exec()
        return box.clickedButton() is switch_btn

    def _do_switch(self, op: SwitchOperation, target: str, target_label: str) -> None:
        from PySide6.QtCore import Qt as _Qt
        from PySide6.QtWidgets import QApplication as _QApp
        from PySide6.QtWidgets import QProgressDialog

        progress = QProgressDialog("正在切换…", "", 0, 0, self)
        progress.setWindowTitle(f"切换到{target_label}")
        progress.setCancelButton(None)
        progress.setWindowModality(_Qt.WindowModal)
        progress.setMinimumDuration(0)
        progress.show()

        def on_progress(_stage: str, message: str) -> None:
            progress.setLabelText(message)
            _QApp.processEvents()

        try:
            result = op.switch(target, progress=on_progress)
        except SwitchError as e:
            progress.close()
            if e.code == "admin_required":
                QMessageBox.critical(
                    self,
                    "需要管理员权限",
                    "hosts 中有需要清理的回环映射，请以管理员身份重启 Verthandi 后重试。",
                )
            else:
                QMessageBox.critical(self, "切换失败", e.message)
            return

        progress.close()
        show_toast(self, f"已切换到{target_label}")
        self.log_bus.emit_line(LogSource.APP, result.message, "info")

        # 刷新状态
        self.refresh_status()

        if result.needs_relogin:
            QMessageBox.information(
                self,
                f"已切换到{target_label}",
                result.message + "\n\n如遇登录问题，请重新输入账号密码。",
            )

    # ---------- preflight 对话框 ----------

    def _show_preflight_errors(self, errors: list[CheckItem]) -> None:
        for i in errors:
            self.log_bus.emit_line(
                LogSource.APP, f"[preflight] {i.label}: {i.message}", "error"
            )
        short = "\n".join(f"• {i.label}：{i.message}" for i in errors)
        details = "\n\n".join(
            f"• {i.label}\n  {i.message}\n  → {i.fix_hint}" for i in errors
        )
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Critical)
        box.setWindowTitle("无法启动服务")
        box.setText(f"启动前检查发现 {len(errors)} 项问题：")
        box.setInformativeText(short)
        box.setDetailedText(details)
        box.setStandardButtons(QMessageBox.Ok)
        box.exec()

    def _confirm_preflight_warnings(self, warnings: list[CheckItem]) -> bool:
        filtered: list[CheckItem] = []
        show_dismiss_box = False
        for w in warnings:
            if w.id == "prepare-unknown" and self.settings.prepare.warning_dismissed_at:
                continue
            if w.id == "prepare-unknown":
                show_dismiss_box = True
            filtered.append(w)
        if not filtered:
            return True

        short = "\n".join(f"• {i.label}：{i.message}" for i in filtered)
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Warning)
        box.setWindowTitle("启动服务")
        box.setText("启动前检查有以下提示：")
        box.setInformativeText(
            short
            + "\n\n如果启动服务或游戏时出现问题，请重新执行「初始化」。\n\n仍要启动服务吗？"
        )
        box.setStandardButtons(QMessageBox.Yes | QMessageBox.No)
        box.setDefaultButton(QMessageBox.No)
        box.button(QMessageBox.Yes).setText("仍要启动")
        box.button(QMessageBox.No).setText("取消")

        dismiss_check: QCheckBox | None = None
        if show_dismiss_box:
            dismiss_check = QCheckBox("不再提示这条警告")
            box.setCheckBox(dismiss_check)

        ok = box.exec() == QMessageBox.Yes
        if ok and dismiss_check is not None and dismiss_check.isChecked():
            self.settings.prepare.warning_dismissed_at = datetime.now().isoformat(
                timespec="seconds"
            )
            self.settings_store.save(self.settings)
        return ok

    # ---------- 服务状态 / 游戏状态 ----------

    def _on_server_state(self, state: str) -> None:
        self._apply_server_state(ServerState(state))

    def _apply_server_state(self, state: ServerState) -> None:
        if state == ServerState.STOPPED:
            self.server_btn.setText("启动服务")
            self.server_btn.setEnabled(True)
            self._set_btn_variant(self.server_btn, "start")
        elif state == ServerState.STARTING:
            self.server_btn.setText("启动中…")
            self.server_btn.setEnabled(False)
            self._set_btn_variant(self.server_btn, "busy")
        elif state == ServerState.RUNNING:
            self.server_btn.setText("停止服务")
            self.server_btn.setEnabled(True)
            self._set_btn_variant(self.server_btn, "stop")
        elif state == ServerState.STOPPING:
            self.server_btn.setText("停止中…")
            self.server_btn.setEnabled(False)
            self._set_btn_variant(self.server_btn, "busy")
        self._apply_prepare_state()
        self._refresh_runtime_state()

    def _on_game_running_changed(self, _running: bool) -> None:
        self._apply_prepare_state()
        self._refresh_runtime_state()

    # ---------- 工具 ----------

    @staticmethod
    def _set_btn_variant(btn: QPushButton, variant: str) -> None:
        btn.setProperty("variant", variant)
        btn.style().unpolish(btn)
        btn.style().polish(btn)

    @staticmethod
    def _repolish(widget: QWidget) -> None:
        widget.style().unpolish(widget)
        widget.style().polish(widget)

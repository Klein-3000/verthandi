from __future__ import annotations

import shutil
from pathlib import Path

from PySide6.QtCore import Qt, QUrl, Signal
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)
from qfluentwidgets import ComboBox

from app.core.client_state import ClientMode, client_fingerprint, read_client_mode
from app.core.game_process import GameWatcher, is_game_running, launch_game
from app.core.hoshimi_finder import scan_versions
from app.core.log_bus import LogBus, LogSource
from app.core.paths import Paths
from app.core.preflight import CheckItem, Severity, run_preflight
from app.core.prepare_process import PrepareProcess, supports_download_missing
from app.core.runtime_layout import (
    UNKNOWN_VERSION,
    RuntimeRef,
    RuntimeSwapError,
    create_staging,
    finalize,
    manifest_path,
    read_manifest,
    resolve_runtime,
)
from app.core.server_process import ServerProcess, ServerState
from app.core.settings import ClientEntry, Settings, SettingsStore
from app.core.switch_client import SwitchError, SwitchOperation
from app.core.telemetry_config import (
    read_telemetry,
    supports_telemetry,
    write_telemetry,
)
from app.widgets.fluent_message import (
    critical,
    information,
    question,
    warning,
)
from app.widgets.log_view import LogView
from app.widgets.prepare_dialog import PrepareDialog
from app.widgets.prepare_options_dialog import PrepareOptionsDialog
from app.widgets.telemetry_dialog import ask_telemetry_consent
from app.widgets.toast import show_toast


class MainPage(QWidget):
    # 本页切换了 hoshimi 版本（设置页需要同步下拉框与 runtime 行）
    version_changed = Signal()

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
        self._pending: tuple[RuntimeRef, Path] | None = None

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
        self.server_process.telemetry_fallback.connect(self._on_telemetry_fallback)
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

    def _on_telemetry_fallback(self) -> None:
        """兜底代答发生后提示一次：说明配置没生效，用户仍可在设置页调整。"""
        show_toast(self, "已按当前设置代为答复 Hoshimi 使用统计")

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
            row.addWidget(k)
            if key == "version":
                # 版本可从本页直接切换（与设置页同一份数据、同一套写入逻辑）
                self.version_combo = ComboBox()
                self.version_combo.setFixedWidth(250)
                self.version_combo.currentIndexChanged.connect(self._on_version_changed)
                row.addWidget(self.version_combo)
                row.addStretch(1)
                box.addLayout(row)
                continue
            v = QLabel("—")
            v.setObjectName("StatusValue")
            v.setTextInteractionFlags(Qt.TextSelectableByMouse)
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

        home = self.paths.home_dir(s)
        ref = resolve_runtime(self.paths, s)
        manifest = read_manifest(self.paths, s, ref) if ref.exists else None
        if ref.version == UNKNOWN_VERSION:
            runtime_text = "—（尚未安装 hoshimi）"
        elif not ref.exists:
            runtime_text = f"{ref.directory}（未初始化）"
        elif manifest is not None and manifest.prepared_at:
            runtime_text = f"{ref.directory}（准备于 {manifest.prepared_label}）"
        else:
            runtime_text = f"{ref.directory}（无初始化记录）"
        ports_text = f"HTTP {s.server.http_port} / 游戏 {s.server.game_port}（TCP+UDP）"

        self._status_values["client"].setText(client_text)
        self._status_values["connection"].setText(connection_text)
        self._status_values["home"].setText(str(home))
        self._status_values["runtime"].setText(runtime_text)
        self._status_values["ports"].setText(ports_text)

        badge = {
            ClientMode.LOCAL: "● Local",
            ClientMode.OFFICIAL: "● Official",
        }.get(actual, "● Unknown")
        self.mode_badge.setText(f"连接: {badge}")

        self._refresh_switch_btn(actual)
        self._refresh_runtime_state()
        self._refresh_version_combo()

    def _refresh_version_combo(self) -> None:
        """重建版本下拉框。条目与设置页一致：自动选择 + 各版本（+ 目录缺失）。"""
        versions = scan_versions(self.paths.bin_dir(self.settings))
        current = self.settings.server.version

        self.version_combo.blockSignals(True)
        try:
            self.version_combo.clear()
            if versions:
                self.version_combo.addItem(
                    f"自动选择最新（{versions[0]}）", userData=""
                )
            for v in versions:
                self.version_combo.addItem(v, userData=v)
            if current and current not in versions:
                self.version_combo.addItem(f"{current}（目录缺失）", userData=current)
            if not self.version_combo.count():
                self.version_combo.addItem("未找到 hoshimi", userData=None)

            idx = self.version_combo.findData(current) if current else 0
            self.version_combo.setCurrentIndex(
                max(min(idx, self.version_combo.count() - 1), 0)
            )
        finally:
            self.version_combo.blockSignals(False)

        self._apply_version_combo_state()

    def _apply_version_combo_state(self) -> None:
        """版本下拉框的可用性：没有可选版本、或服务不是停止态时锁定。"""
        has_versions = bool(scan_versions(self.paths.bin_dir(self.settings)))
        stopped = self.server_process.state == ServerState.STOPPED
        self.version_combo.setEnabled(has_versions and stopped)
        if not has_versions:
            self.version_combo.setToolTip("bin 目录下没有可用的 hoshimi")
        elif not stopped:
            self.version_combo.setToolTip("服务运行中，停止服务后可切换版本")
        else:
            self.version_combo.setToolTip("切换 hoshimi 版本（下次启动服务生效）")

    def _on_version_changed(self, index: int) -> None:
        if index < 0:
            return
        # 服务已启动过就不能再切版本，避免"看到的版本"和"正在跑的版本"不一致
        if self.server_process.state != ServerState.STOPPED:
            self.refresh_status()
            return

        data = self.version_combo.currentData()
        if data is None:
            return
        if data == "":
            versions = scan_versions(self.paths.bin_dir(self.settings))
            if not versions:
                return
            new_version = versions[0]
        else:
            new_version = data

        if new_version == self.settings.server.version:
            return

        self.settings.server.version = new_version
        self.settings_store.save(self.settings)
        self.log_bus.emit_line(
            LogSource.APP, f"hoshimi 版本已切换：{new_version}", "info"
        )
        self.refresh_status()
        self.version_changed.emit()

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
            critical(
                self,
                "无法初始化",
                f"AetherGazer.exe 不存在：\n{client_exe}",
            )
            return

        options = self._confirm_prepare(client_exe.parent)
        if options is None:
            return

        self._start_prepare(client_exe.parent, options[0], options[1])

    def _prompt_select_client(self) -> bool:
        ok = question(
            self,
            "需要选择游戏目录",
            "初始化需要指定 AetherGazer.exe 的位置。\n\n"
            "请选择游戏安装目录下的 AetherGazer.exe 文件，而不是文件夹。",
            yes_text="浏览…",
        )
        if not ok:
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
            critical(
                self, "文件不匹配", f"选中的文件不是 AetherGazer.exe：\n{exe.name}"
            )
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

    def _confirm_prepare(self, client_dir: Path) -> tuple[bool, bool] | None:
        """确认并收集选项。返回 (是否补资源, 是否下载全部语音)，取消时为 None。"""
        ref = resolve_runtime(self.paths, self.settings)
        dialog = PrepareOptionsDialog(
            self,
            client_dir=client_dir,
            version=ref.version,
            runtime_dir=ref.directory,
            allow_download_missing=supports_download_missing(ref.version),
        )
        if not dialog.exec():
            return None
        return dialog.download_missing(), dialog.download_all()

    def _start_prepare(
        self,
        client_dir: Path,
        download_missing: bool = False,
        download_all: bool = False,
    ) -> None:
        ref = resolve_runtime(self.paths, self.settings)
        try:
            staging = create_staging(self.paths, self.settings, ref.version)
        except OSError as e:
            critical(self, "无法初始化", f"无法创建临时目录：\n{e}")
            return

        self._preparing = True
        self._pending = (ref, staging)
        self._apply_prepare_state()

        self._prep = PrepareProcess(self.paths, self.settings, self.log_bus, self)
        self._prep.finished.connect(self._on_prepare_finished)
        self._prep.failed_to_start.connect(self._on_prepare_failed)

        if not self._prep.start(
            client_dir,
            staging,
            download_missing=download_missing,
            download_all=download_all,
        ):
            self._discard_pending()
            self._on_prepare_failed("无法启动 prepare 进程")
            return

        self._prep_dialog = PrepareDialog(self._prep, self, download_missing)
        self._prep_dialog.exec()

    def _on_prepare_finished(self, code: int) -> None:
        self._preparing = False
        self._apply_prepare_state()

        pending = self._pending
        self._pending = None
        if pending is None:
            return
        ref, staging = pending

        if code != 0:
            shutil.rmtree(staging, ignore_errors=True)
            if code == -1:
                self.log_bus.emit_line(LogSource.APP, "初始化已取消", "warning")
                warning(
                    self,
                    "初始化已取消",
                    "初始化被取消，现有运行文件未受影响。",
                )
            else:
                self.log_bus.emit_line(
                    LogSource.APP, f"初始化失败，退出码 {code}", "error"
                )
                critical(
                    self,
                    "初始化失败",
                    f"hoshimi prepare 退出码 {code}。\n"
                    "现有运行文件未受影响，详情见日志面板。",
                )
            return

        client_dir = self.paths.active_client_dir(self.settings)
        try:
            finalize(
                ref,
                staging,
                target_manifest=manifest_path(self.paths, self.settings, ref),
                client_dir=client_dir,
                fingerprint=client_fingerprint(client_dir),
            )
        except RuntimeSwapError as e:
            self.log_bus.emit_line(LogSource.APP, str(e), "error")
            critical(
                self,
                "初始化未完成",
                f"{e}\n\n旧的运行文件未被替换。"
                + (f"\n临时目录保留在：\n{e.staging}" if e.staging else ""),
            )
            return

        self.log_bus.emit_line(
            LogSource.APP, f"初始化完成：{ref.version} → {ref.directory}", "info"
        )
        self.refresh_status()

    def _on_prepare_failed(self, message: str) -> None:
        self._preparing = False
        self._apply_prepare_state()
        self._discard_pending()

        self.log_bus.emit_line(LogSource.APP, message, "error")
        critical(self, "初始化失败", message)

    def _discard_pending(self) -> None:
        """丢弃未完成的临时目录；正式目录从未被碰过，所以这里没有"恢复"这一步。"""
        pending = self._pending
        self._pending = None
        if pending is None:
            return
        _ref, staging = pending
        shutil.rmtree(staging, ignore_errors=True)

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
            self._ensure_telemetry()
            sp.start()
            return

        if sp.state != ServerState.RUNNING:
            return

        if self._confirm_stop():
            sp.stop()

    def _ensure_telemetry(self) -> None:
        """启动前把 hoshimi 的遥测答复写进 home/config.json。

        必须在 serve 之前完成：hoshimi 的询问没有换行结尾、且会一直阻塞在
        stdin 上，Verthandi 既读不到也答不上，只会表现为"启动中"卡死。
        """
        ref = resolve_runtime(self.paths, self.settings)
        if not supports_telemetry(ref.version):
            return

        state = read_telemetry(self.paths, self.settings)
        if state is True:
            # 已开启就没什么可问的，只补一次"已答复"标记
            self._mark_telemetry_prompted()
            return
        if self.settings.behavior.telemetry_prompted and state is not None:
            return

        enabled, remember = ask_telemetry_consent(self, ref.version, state)
        ok, err = write_telemetry(self.paths, self.settings, enabled)
        if remember:
            self._mark_telemetry_prompted()
        if not ok:
            warning(
                self,
                "遥测选择未能保存",
                f"无法写入 hoshimi 配置文件：\n{err}\n\n"
                "服务仍会启动，届时 Verthandi 会按当前设置代为答复。",
            )
            return
        self.log_bus.emit_line(
            LogSource.APP,
            "Hoshimi 使用统计：" + ("已允许发送" if enabled else "已关闭"),
            "info",
        )

    def _mark_telemetry_prompted(self) -> None:
        if self.settings.behavior.telemetry_prompted:
            return
        self.settings.behavior.telemetry_prompted = True
        self.settings_store.save(self.settings)

    def _confirm_stop(self) -> bool:
        if not self.settings.behavior.confirm_before_stop_server:
            return True

        actual = read_client_mode(self.paths.active_client_dir(self.settings))
        game_running = is_game_running(self.paths.active_client_dir(self.settings))

        if game_running and actual == ClientMode.LOCAL:
            text = (
                "游戏正在运行，停止服务会导致客户端断连，\n"
                "未保存的进度可能丢失。\n\n确定停止服务吗？"
            )
        elif game_running and actual == ClientMode.OFFICIAL:
            text = (
                "游戏正在运行。当前客户端连接官服，\n"
                "停止本地服务不影响游戏连接。\n\n确定停止服务吗？"
            )
        else:
            text = "确定要停止 Hoshimi 服务吗？"

        return question(self, "停止服务", text, yes_text="停止")

    # ---------- 启动游戏 ----------

    def _on_game_button(self) -> None:
        if self.game_watcher.running:
            return

        client = self.settings.active_client()
        if client is None or not client.exe_path:
            critical(self, "无法启动游戏", "尚未选择游戏目录")
            return

        exe = Path(client.exe_path)
        if not exe.exists():
            critical(
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
            critical(self, "无法启动游戏", msg)

    # ---------- 快捷入口 ----------

    def _open_directory(self, path: Path, label: str) -> None:
        """打开目录；不存在时先创建。"""
        try:
            path.mkdir(parents=True, exist_ok=True)
        except OSError as e:
            warning(
                self,
                "无法打开目录",
                f"{label}目录创建失败：\n{path}\n\n{e}",
            )
            return
        if not QDesktopServices.openUrl(QUrl.fromLocalFile(str(path))):
            warning(
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
            warning(
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
            warning(self, "无法切换", "游戏正在运行，请先退出客户端再切换。")
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
        warning(
            self,
            "无法切换到" + target_label,
            "切换前置检查未通过：\n" + lines,
        )

    def _confirm_switch(self, target_label: str, checks: list) -> bool:
        # 服务在运行时给额外提示（不阻止）
        extra = ""
        if self.server_process.state == ServerState.RUNNING and target_label == "官服":
            extra = (
                "\n注意：服务端仍在运行，切换后客户端不再连接本地服务。\n"
                "如不再需要 GM 后台，建议切换后停止服务以释放端口。"
            )

        box_text = (
            f"将把客户端切换到{target_label}。\n\n"
            "会修改以下内容：\n"
            "• 客户端 url.txt\n"
            "• resources.assets 中的 SDK 端点\n"
            "• 交换两套 SDK 登录缓存\n"
            "• 清理 hosts 中的 ys4fun.com 回环映射（如有）\n"
            f"{extra}\n\n"
            "确定切换吗？"
        )
        return question(self, f"切换到{target_label}", box_text, yes_text="确认切换")

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
                critical(
                    self,
                    "需要管理员权限",
                    "hosts 中有需要清理的回环映射，请以管理员身份重启 Verthandi 后重试。",
                )
            else:
                critical(self, "切换失败", e.message)
            return

        progress.close()
        show_toast(self, f"已切换到{target_label}")
        self.log_bus.emit_line(LogSource.APP, result.message, "info")

        # 刷新状态
        self.refresh_status()

        if result.needs_relogin:
            information(
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
        # 每条信息只出现一次：问题描述 + 缩进的修复建议
        lines = [f"启动前检查发现 {len(errors)} 项问题：", ""]
        for i in errors:
            lines.append(f"• {i.label}：{i.message}")
            if i.fix_hint:
                lines.append(f"  → {i.fix_hint}")
        information(self, "无法启动服务", "\n".join(lines))

    def _confirm_preflight_warnings(self, warnings: list[CheckItem]) -> bool:
        if not warnings:
            return True

        short = "\n".join(f"• {i.label}：{i.message}" for i in warnings)
        return question(
            self,
            "启动服务",
            "启动前检查有以下提示：\n\n"
            + short
            + "\n\n如果启动服务或游戏时出现问题，请重新执行「初始化」。\n\n仍要启动服务吗？",
            yes_text="仍要启动",
            danger=True,
        )

    # ---------- 服务状态 / 游戏状态 ----------

    def _on_server_state(self, state: str) -> None:
        self._apply_server_state(ServerState(state))

    def _apply_server_state(self, state: ServerState) -> None:
        # 只要点过"启动服务"（STARTING 起）就锁定版本，避免用户错认正在运行的版本
        self._apply_version_combo_state()
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

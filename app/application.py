from __future__ import annotations

import sys
from pathlib import Path

from PySide6.QtCore import QLibraryInfo, Qt, QTimer, QTranslator
from PySide6.QtWidgets import QApplication
from qfluentwidgets import Theme, setTheme, setThemeColor

from app.core.app_icon import load_app_icon
from app.core.game_process import GameWatcher
from app.core.log_bus import LogBus, LogSource, SessionLogWriter
from app.core.paths import Paths, detect_base_dir, resource_path
from app.core.runtime_layout import cleanup_staging, migrate_legacy
from app.core.server_process import ServerProcess, ServerState
from app.core.settings import SettingsStore
from app.core.single_instance import SingleInstance, force_foreground
from app.window import MainWindow


class VerthandiApplication(QApplication):
    def __init__(self, argv: list[str]):
        super().__init__(argv)

        self.base_dir: Path = detect_base_dir()
        self.paths = Paths(self.base_dir)
        self.settings_store = SettingsStore(self.paths)
        self.settings = self.settings_store.load()

        self.log_bus = LogBus()
        self.log_writer = SessionLogWriter(self.paths.logs_dir(self.settings))
        self.log_writer.bind(self.log_bus)

        self._prepare_runtime_layout()

        self.server_process = ServerProcess(self.paths, self.settings, self.log_bus)
        self.game_watcher = GameWatcher()

        self.setApplicationName("Verthandi")
        self.setApplicationDisplayName("Verthandi")
        self.setOrganizationName("Verthandi")
        self.setApplicationVersion("0.1.0")

        # 图标缺失不算错误：None 时留给 Qt 的默认图标
        self._app_icon = load_app_icon()
        if self._app_icon is not None:
            self.setWindowIcon(self._app_icon)

        self._translator = QTranslator(self)
        self._load_translator()

        self._load_qss()
        self.apply_theme(self.settings.ui.theme)

        self.window = MainWindow(
            base_dir=self.base_dir,
            paths=self.paths,
            settings=self.settings,
            settings_store=self.settings_store,
            log_bus=self.log_bus,
            log_writer=self.log_writer,
            server_process=self.server_process,
            game_watcher=self.game_watcher,
        )
        # QApplication 的窗口图标是默认值，显式再设一次，保证标题栏与任务栏一致
        if self._app_icon is not None:
            self.window.setWindowIcon(self._app_icon)

        # 1. 先让窗口显示出来（秒出）
        self.window.show()

        # 2. 初始化单实例
        self.single_instance = SingleInstance(self)
        self.single_instance.activated.connect(self._activate_window)

        # 3. 延迟初始化重操作（进程扫描、日志文件）
        QTimer.singleShot(0, self._late_init)

    def _prepare_runtime_layout(self) -> None:
        """把 v1 的单 runtime 目录迁进 runtimes/，并清掉上次崩溃留下的临时目录。"""
        result = migrate_legacy(self.paths, self.settings)
        if result.changed:
            self.settings_store.save(self.settings)
            self.log_bus.emit_line(LogSource.APP, result.message, "info")

        removed = cleanup_staging(self.paths, self.settings)
        if removed:
            self.log_bus.emit_line(
                LogSource.APP,
                f"已清理 {len(removed)} 个未完成的 runtime 临时目录",
                "info",
            )

    def _late_init(self) -> None:
        """在界面显示后的下一帧，开始后台任务。"""
        self.game_watcher.start(self.paths.active_client_dir(self.settings))

        if self.settings.behavior.log_to_file:
            self.log_writer.enable()
            self.log_bus.emit_line(
                LogSource.APP, f"会话日志：{self.log_writer.path}", "info"
            )
        else:
            self.log_bus.emit_line(
                LogSource.APP, "日志文件已关闭（可在设置中开启）", "info"
            )

    def _load_translator(self) -> None:
        if getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"):
            meipass = Path(sys._MEIPASS)
            possible_paths = [
                meipass / "PySide6" / "translations",
                meipass / "translations",
            ]
            for path in possible_paths:
                if (path / "qtbase_zh_CN.qm").exists():
                    if self._translator.load("qtbase_zh_CN", str(path)):
                        self.installTranslator(self._translator)
                    break
        else:
            path = QLibraryInfo.path(QLibraryInfo.LibraryPath.TranslationsPath)
            if self._translator.load("qtbase_zh_CN", path):
                self.installTranslator(self._translator)

    def _qss_path(self) -> Path:
        return resource_path("app", "resources", "qss", "base.qss")

    def _load_qss(self) -> None:
        path = self._qss_path()
        if path.exists():
            self.setStyleSheet(path.read_text(encoding="utf-8"))

    def apply_theme(self, theme: str) -> None:
        scheme = {
            "system": Qt.ColorScheme.Unknown,
            "light": Qt.ColorScheme.Light,
            "dark": Qt.ColorScheme.Dark,
        }.get(theme, Qt.ColorScheme.Unknown)
        try:
            self.styleHints().setColorScheme(scheme)
        except (AttributeError, TypeError):
            pass

        # Fluent 控件（弹窗、复选框、下拉框等）跟随同一主题设置
        fluent_theme = {
            "light": Theme.LIGHT,
            "dark": Theme.DARK,
        }.get(theme, Theme.AUTO)
        setTheme(fluent_theme)
        # 主题色与界面既有蓝色强调色保持一致（导航栏选中态、主按钮等）
        setThemeColor("#2563eb")

    def _activate_window(self) -> None:
        self.window.bring_to_front()
        if not force_foreground(int(self.window.winId())):
            self.alert(self.window, 1500)

    def run(self) -> int:
        code = self.exec()
        self.game_watcher.stop()
        if self.server_process.state != ServerState.STOPPED:
            self.server_process.force_kill()
        self.log_writer.close()
        return code

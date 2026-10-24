from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication

from app.core.log_bus import LogBus, LogSource, SessionLogWriter
from app.core.paths import Paths, detect_base_dir
from app.core.server_process import ServerProcess, ServerState
from app.core.settings import SettingsStore
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

        self.server_process = ServerProcess(self.paths, self.settings, self.log_bus)

        self.setApplicationName('Verthandi')
        self.setApplicationDisplayName('Verthandi')
        self.setOrganizationName('Verthandi')
        self.setApplicationVersion('0.1.0')

        self._load_qss()
        self.apply_theme(self.settings.ui.theme)

        self.window = MainWindow(
            base_dir=self.base_dir,
            paths=self.paths,
            settings=self.settings,
            settings_store=self.settings_store,
            log_bus=self.log_bus,
            server_process=self.server_process,
        )
        self.window.show()

        self.log_bus.emit_line(
            LogSource.APP, f'会话日志：{self.log_writer.path}', 'info'
        )

    def _qss_path(self) -> Path:
        return self.base_dir / 'app' / 'resources' / 'qss' / 'base.qss'

    def _load_qss(self) -> None:
        path = self._qss_path()
        if path.exists():
            self.setStyleSheet(path.read_text(encoding='utf-8'))

    def apply_theme(self, theme: str) -> None:
        scheme = {
            'system': Qt.ColorScheme.Unknown,
            'light': Qt.ColorScheme.Light,
            'dark': Qt.ColorScheme.Dark,
        }.get(theme, Qt.ColorScheme.Unknown)
        try:
            self.styleHints().setColorScheme(scheme)
        except (AttributeError, TypeError):
            pass

    def run(self) -> int:
        code = self.exec()
        if self.server_process.state != ServerState.STOPPED:
            self.server_process.force_kill()
        self.log_writer.close()
        return code

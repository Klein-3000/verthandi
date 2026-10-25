from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (
    QHBoxLayout, QMainWindow, QMessageBox, QProgressDialog,
    QStackedWidget, QWidget,
)

from app.core.game_process import GameWatcher
from app.core.log_bus import LogBus, SessionLogWriter
from app.core.paths import Paths
from app.core.server_process import ServerProcess, ServerState
from app.core.settings import Settings, SettingsStore
from app.pages.about_page import AboutPage
from app.pages.main_page import MainPage
from app.pages.settings_page import SettingsPage
from app.widgets.sidebar import Sidebar


class MainWindow(QMainWindow):
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

        self._closing = False
        self._close_dialog: QProgressDialog | None = None
        self._close_timer = QTimer(self)
        self._close_timer.setSingleShot(True)
        self._close_timer.timeout.connect(self._force_close)

        self.setWindowTitle('Verthandi')
        self.resize(settings.ui.window.width, settings.ui.window.height)
        self.setMinimumSize(880, 560)

        central = QWidget()
        root = QHBoxLayout(central)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        self.sidebar = Sidebar()
        self.stack = QStackedWidget()

        self.main_page = MainPage(
            base_dir, paths, settings, settings_store,
            log_bus, server_process, game_watcher,
        )
        self.settings_page = SettingsPage(
            base_dir, paths, settings, settings_store,
            log_bus, log_writer, server_process,
        )
        self.about_page = AboutPage(base_dir)

        for page in (self.main_page, self.settings_page, self.about_page):
            self.stack.addWidget(page)

        root.addWidget(self.sidebar)
        root.addWidget(self.stack, 1)
        self.setCentralWidget(central)

        self.sidebar.currentChanged.connect(self._on_nav_changed)
        self.sidebar.set_current(0)

        self.settings_page.settings_changed.connect(self._on_settings_changed)

    def _on_nav_changed(self, index: int) -> None:
        if 0 <= index < self.stack.count():
            self.stack.setCurrentIndex(index)

    def _on_settings_changed(self, category: str) -> None:
        self.main_page.refresh_status()
        self.game_watcher.set_client_dir(
            self.paths.active_client_dir(self.settings))

    # ---------- 关闭流程 ----------

    def closeEvent(self, event) -> None:
        sp = self.server_process
        if sp.state == ServerState.STOPPED:
            event.accept()
            return
        if self._closing:
            event.ignore()
            return
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Warning)
        box.setWindowTitle('退出 Verthandi')
        box.setText('服务仍在运行，退出将停止服务。\n\n确定退出吗？')
        box.setStandardButtons(QMessageBox.Yes | QMessageBox.No)
        box.setDefaultButton(QMessageBox.No)
        box.button(QMessageBox.Yes).setText('停止并退出')
        box.button(QMessageBox.No).setText('取消')
        if box.exec() != QMessageBox.Yes:
            event.ignore()
            return
        event.ignore()
        self._begin_close()

    def _begin_close(self) -> None:
        self._closing = True
        self.setEnabled(False)
        self._close_dialog = QProgressDialog('正在停止服务…', '', 0, 0, self)
        self._close_dialog.setWindowTitle('退出中')
        self._close_dialog.setCancelButton(None)
        self._close_dialog.setWindowModality(Qt.WindowModal)
        self._close_dialog.setMinimumDuration(0)
        self._close_dialog.show()
        self.server_process.state_changed.connect(self._on_close_state)
        self.server_process.stop()
        self._close_timer.start(15000)

    def _on_close_state(self, state: str) -> None:
        if not self._closing:
            return
        if state == ServerState.STOPPED.value:
            self._close_timer.stop()
            self._finish_close()

    def _force_close(self) -> None:
        if not self._closing:
            return
        self.server_process.force_kill()
        self._finish_close()

    def _finish_close(self) -> None:
        self._closing = False
        try:
            self.server_process.state_changed.disconnect(self._on_close_state)
        except (TypeError, RuntimeError):
            pass
        if self._close_dialog is not None:
            self._close_dialog.close()
            self._close_dialog = None
        self.game_watcher.stop()
        self.close()

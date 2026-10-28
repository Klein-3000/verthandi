from __future__ import annotations

import subprocess
from pathlib import Path

from PySide6.QtCore import QObject, QThread, QTimer, Signal

try:
    import psutil
except ImportError:
    psutil = None

EXE_NAME = "AetherGazer.exe"


def is_game_running(client_dir: Path | None = None) -> bool:
    """检测 AetherGazer.exe 是否运行。"""
    if psutil is None:
        return False

    target = ""
    if client_dir is not None:
        target = str((client_dir / EXE_NAME).resolve()).lower()

    for proc in psutil.process_iter(["name", "exe"]):
        try:
            info = proc.info
            name = (info.get("name") or "").lower()
            if name != EXE_NAME.lower():
                continue
            if not target:
                return True
            exe = info.get("exe")
            if not exe:
                return True
            if str(Path(exe).resolve()).lower() == target:
                return True
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
    return False


def launch_game(client_dir: Path) -> tuple[bool, str]:
    """启动 AetherGazer.exe，不等待。返回 (成功, 错误消息)。"""
    exe = client_dir / EXE_NAME
    if not exe.exists():
        return False, f"AetherGazer.exe 不存在：{exe}"
    try:
        subprocess.Popen([str(exe)], cwd=str(client_dir))
    except OSError as e:
        return False, f"启动游戏失败：{e}"
    return True, ""


class GameWatcherWorker(QThread):
    """后台扫描进程的线程，避免 psutil 阻塞主界面。"""
    result_ready = Signal(bool)

    def __init__(self, client_dir: Path | None, parent=None):
        super().__init__(parent)
        self.client_dir = client_dir

    def run(self):
        res = is_game_running(self.client_dir)
        self.result_ready.emit(res)


class GameWatcher(QObject):
    """周期性检测游戏进程状态，使用 QThread 异步执行。"""

    running_changed = Signal(bool)

    def __init__(self, interval_ms: int = 2000, parent=None):
        super().__init__(parent)
        self._running = False
        self._client_dir: Path | None = None
        self._timer = QTimer(self)
        self._timer.setInterval(interval_ms)
        self._timer.timeout.connect(self._poll)

        self._worker: GameWatcherWorker | None = None
        self._force_emit_next = False

    @property
    def running(self) -> bool:
        return self._running

    def start(self, client_dir: Path | None) -> None:
        self._client_dir = client_dir
        self._force_emit_next = True
        self._poll()
        self._timer.start()

    def stop(self) -> None:
        self._timer.stop()
        if self._worker and self._worker.isRunning():
            self._worker.wait(1000)

    def set_client_dir(self, client_dir: Path | None) -> None:
        self._client_dir = client_dir
        self._force_emit_next = True
        self._poll()

    def refresh(self) -> None:
        self._force_emit_next = True
        self._poll()

    def _poll(self) -> None:
        # 如果上一次扫描还没结束，直接跳过本次，防止线程爆炸
        if self._worker and self._worker.isRunning():
            return

        self._worker = GameWatcherWorker(self._client_dir, self)
        self._worker.result_ready.connect(self._on_poll_finished)
        self._worker.start()

    def _on_poll_finished(self, current: bool) -> None:
        if current != self._running or self._force_emit_next:
            self._running = current
            self.running_changed.emit(current)
        self._force_emit_next = False

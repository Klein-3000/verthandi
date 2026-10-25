from __future__ import annotations

import subprocess
from pathlib import Path

from PySide6.QtCore import QObject, QTimer, Signal

try:
    import psutil
except ImportError:
    psutil = None

EXE_NAME = 'AetherGazer.exe'


def is_game_running(client_dir: Path | None = None) -> bool:
    """检测 AetherGazer.exe 是否运行。

    - client_dir 为空：按进程名匹配；
    - 提供 client_dir：额外校验 exe 路径；拿不到路径时保守返回 True。
    """
    if psutil is None:
        return False

    target = ''
    if client_dir is not None:
        target = str((client_dir / EXE_NAME).resolve()).lower()

    for proc in psutil.process_iter(['name', 'exe']):
        try:
            info = proc.info
            name = (info.get('name') or '').lower()
            if name != EXE_NAME.lower():
                continue
            if not target:
                return True
            exe = info.get('exe')
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
        return False, f'AetherGazer.exe 不存在：{exe}'
    try:
        subprocess.Popen([str(exe)], cwd=str(client_dir))
    except OSError as e:
        return False, f'启动游戏失败：{e}'
    return True, ''

class GameWatcher(QObject):
    """周期性检测游戏进程状态，状态变化时发信号。"""

    running_changed = Signal(bool)

    def __init__(self, interval_ms: int = 2000, parent=None):
        super().__init__(parent)
        self._running = False
        self._client_dir: Path | None = None
        self._timer = QTimer(self)
        self._timer.setInterval(interval_ms)
        self._timer.timeout.connect(self._poll)

    @property
    def running(self) -> bool:
        return self._running

    def start(self, client_dir: Path | None) -> None:
        self._client_dir = client_dir
        self._poll(force_emit=True)
        self._timer.start()

    def stop(self) -> None:
        self._timer.stop()

    def set_client_dir(self, client_dir: Path | None) -> None:
        self._client_dir = client_dir
        self._poll(force_emit=True)

    def refresh(self) -> None:
        self._poll(force_emit=True)

    def _poll(self, force_emit: bool = False) -> None:
        current = is_game_running(self._client_dir)
        if current != self._running or force_emit:
            self._running = current
            self.running_changed.emit(current)

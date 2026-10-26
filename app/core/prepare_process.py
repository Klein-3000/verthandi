from __future__ import annotations

import os
import subprocess
import sys
import threading
from pathlib import Path

from PySide6.QtCore import QObject, QTimer, Signal

from app.core.hoshimi_finder import find_hoshimi_exe
from app.core.log_bus import LogBus, LogSource
from app.core.paths import Paths
from app.core.settings import Settings

CREATE_NEW_CONSOLE = 0x00000010
CREATE_NEW_PROCESS_GROUP = 0x00000200


class PrepareProcess(QObject):
    """运行 hoshimi.exe prepare 的子进程封装。

    - 输出通过 output_line 发给对话框，同时写入 log_bus（来源 prepare）
    - cancel：terminate → 2s → kill
    - 不修改 settings；成功与否由调用方判断
    """

    output_line = Signal(str)
    finished = Signal(int)  # 退出码；被取消时为 -1
    failed_to_start = Signal(str)

    def __init__(self, paths: Paths, settings: Settings, log_bus: LogBus, parent=None):
        super().__init__(parent)
        self.paths = paths
        self.settings = settings
        self.log_bus = log_bus
        self._proc: subprocess.Popen | None = None
        self._cancelled = False
        self._kill_timer = QTimer(self)
        self._kill_timer.setSingleShot(True)
        self._kill_timer.timeout.connect(self._force_kill)

    @property
    def running(self) -> bool:
        return self._proc is not None and self._proc.poll() is None

    def start(self, client_dir: Path) -> bool:
        if self.running:
            return False

        exe, _version, _auto = find_hoshimi_exe(self.paths, self.settings)
        if not exe.exists():
            self.failed_to_start.emit(f"找不到 hoshimi.exe：{exe}")
            return False

        home = self.paths.home_dir(self.settings)
        runtime = self.paths.runtime_dir(self.settings)
        home.mkdir(parents=True, exist_ok=True)
        runtime.mkdir(parents=True, exist_ok=True)

        cmd = [
            str(exe),
            "prepare",
            "--client-dir",
            str(client_dir),
            "--home",
            str(home),
            "--runtime-dir",
            str(runtime),
        ]

        creationflags = 0
        startupinfo = None
        if sys.platform == "win32":
            creationflags = CREATE_NEW_CONSOLE | CREATE_NEW_PROCESS_GROUP
            startupinfo = subprocess.STARTUPINFO()
            startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
            startupinfo.wShowWindow = 0

        try:
            self._proc = subprocess.Popen(
                cmd,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding="utf-8",
                errors="replace",
                bufsize=1,
                env=os.environ.copy(),
                cwd=str(exe.parent),
                creationflags=creationflags,
                startupinfo=startupinfo,
            )
        except OSError as e:
            self.failed_to_start.emit(f"启动 prepare 失败：{e}")
            self._proc = None
            return False

        self._cancelled = False
        threading.Thread(target=self._reader, args=(self._proc,), daemon=True).start()
        threading.Thread(target=self._monitor, args=(self._proc,), daemon=True).start()
        return True

    def cancel(self) -> None:
        if not self.running:
            return
        self._cancelled = True
        try:
            self._proc.terminate()
        except OSError:
            pass
        self._kill_timer.start(2000)

    def _force_kill(self) -> None:
        if self._proc is not None and self._proc.poll() is None:
            try:
                self._proc.kill()
            except OSError:
                pass

    def _reader(self, proc: subprocess.Popen) -> None:
        if proc.stdout is None:
            return
        try:
            for raw in proc.stdout:
                text = raw.rstrip("\r\n")
                if not text:
                    continue
                self.output_line.emit(text)
                self.log_bus.emit_line(LogSource.PREPARE, text)
        except (OSError, ValueError):
            pass

    def _monitor(self, proc: subprocess.Popen) -> None:
        code = proc.wait()
        self._kill_timer.stop()
        self._proc = None
        self.finished.emit(-1 if self._cancelled else code)

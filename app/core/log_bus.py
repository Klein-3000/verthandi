from __future__ import annotations

from datetime import datetime
from enum import Enum
from pathlib import Path

from PySide6.QtCore import QObject, Qt, Signal


class LogSource(str, Enum):
    HOSHIMI = "hoshimi"
    SWITCH = "switch"
    PREPARE = "prepare"
    APP = "app"
    GAME = "game"


class LogBus(QObject):
    """日志汇聚与分发。

    - 所有产生日志的模块都调用 `emit_line(source, text, level='info')`；
    - UI 控件、文件 writer 各自订阅 `line` 信号；
    - Qt Signal 自带跨线程排队，从 QProcess 读线程发到 UI 线程是安全的。
    """

    line = Signal(str, str, str)  # source, level, text

    def emit_line(
        self,
        source: LogSource | str,
        text: str,
        level: str = "info",
    ) -> None:
        if isinstance(source, LogSource):
            source = source.value
        if text.endswith("\n"):
            text = text.rstrip("\r\n")
        if not text:
            return
        self.line.emit(source, level, text)


class SessionLogWriter(QObject):
    """按会话写日志文件，可运行时开/关。

    - enable() 首次创建 session-YYYYMMDD-HHMMSS.log；
    - 同一运行实例内 disable 后再次 enable，复用同一文件（追加）；
    - close() 关闭文件，等下次启动创建新会话。
    """

    def __init__(self, log_dir: Path):
        super().__init__()
        self._log_dir = log_dir
        self._file = None
        self.path: Path | None = None

    def enable(self) -> None:
        if self._file is not None:
            return
        self._log_dir.mkdir(parents=True, exist_ok=True)
        if self.path is None:
            ts = datetime.now().strftime("%Y%m%d-%H%M%S")
            self.path = self._log_dir / f"session-{ts}.log"
        self._file = self.path.open("a", encoding="utf-8", buffering=1)

    def disable(self) -> None:
        if self._file is None:
            return
        try:
            self._file.close()
        except OSError:
            pass
        self._file = None

    def bind(self, bus: LogBus) -> None:
        bus.line.connect(self._on_line, Qt.QueuedConnection)

    def _on_line(self, source: str, level: str, text: str) -> None:
        if self._file is None:
            return
        ts = datetime.now().isoformat(sep=" ", timespec="seconds")
        try:
            self._file.write(f"{ts} [{source}] [{level}] {text}\n")
        except OSError:
            pass

    def close(self) -> None:
        self.disable()

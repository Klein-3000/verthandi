from __future__ import annotations

from datetime import datetime
from enum import Enum
from pathlib import Path

from PySide6.QtCore import QObject, Qt, Signal


class LogSource(str, Enum):
    HOSHIMI = 'hoshimi'
    SWITCH = 'switch'
    PREPARE = 'prepare'
    APP = 'app'
    GAME = 'game'


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
        level: str = 'info',
    ) -> None:
        if isinstance(source, LogSource):
            source = source.value
        if text.endswith('\n'):
            text = text.rstrip('\r\n')
        if not text:
            return
        self.line.emit(source, level, text)


class SessionLogWriter(QObject):
    """把日志总线上的所有行写入按会话命名的文件。

    文件：logs/session-YYYYMMDD-HHMMSS.log
    生命周期：随 Verthandi 启动创建，退出时 close()。
    """

    def __init__(self, log_dir: Path):
        super().__init__()
        log_dir.mkdir(parents=True, exist_ok=True)
        timestamp = datetime.now().strftime('%Y%m%d-%H%M%S')
        self.path = log_dir / f'session-{timestamp}.log'
        self._file = self.path.open('a', encoding='utf-8', buffering=1)

    def bind(self, bus: LogBus) -> None:
        bus.line.connect(self._on_line, Qt.QueuedConnection)

    def _on_line(self, source: str, level: str, text: str) -> None:
        ts = datetime.now().isoformat(sep=' ', timespec='seconds')
        try:
            self._file.write(f'{ts} [{source}] [{level}] {text}\n')
        except OSError:
            pass

    def close(self) -> None:
        try:
            self._file.close()
        except OSError:
            pass

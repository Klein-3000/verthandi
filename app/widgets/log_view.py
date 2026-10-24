from __future__ import annotations

from collections import deque
from datetime import datetime

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QComboBox,
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from app.core.log_bus import LogBus, LogSource


class LogView(QWidget):
    """滚动日志视图。

    - 内部 ring buffer 保留最近 MAX_LINES 行；
    - 默认只显示 hoshimi，可切换到全部或按来源过滤；
    - 切换过滤时从 buffer 重新渲染，不丢历史；
    - 自动滚动可开关；用户手动上滚后不会强制拉回底部（v1 简单实现：跟随开关）。
    """

    MAX_LINES = 5000

    def __init__(self, log_bus: LogBus, parent=None):
        super().__init__(parent)
        self._bus = log_bus
        # (ts, source, level, text)
        self._entries: deque[tuple[str, str, str, str]] = deque(maxlen=self.MAX_LINES)
        self._filter: str = LogSource.HOSHIMI.value
        self._auto_scroll: bool = True

        self._build_ui()
        self._bus.line.connect(self._on_line)

    # ---------- UI ----------

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)

        toolbar = QHBoxLayout()
        toolbar.setContentsMargins(0, 0, 0, 0)
        toolbar.setSpacing(8)

        toolbar.addWidget(QLabel('来源:'))
        self.filter_combo = QComboBox()
        self.filter_combo.addItem('hoshimi', LogSource.HOSHIMI.value)
        self.filter_combo.addItem('全部', '*')
        self.filter_combo.addItem('switch', LogSource.SWITCH.value)
        self.filter_combo.addItem('prepare', LogSource.PREPARE.value)
        self.filter_combo.addItem('app', LogSource.APP.value)
        self.filter_combo.addItem('game', LogSource.GAME.value)
        self.filter_combo.currentIndexChanged.connect(self._on_filter_changed)
        toolbar.addWidget(self.filter_combo)

        toolbar.addStretch(1)

        self.scroll_btn = QPushButton('自动滚动: 开')
        self.scroll_btn.setCheckable(True)
        self.scroll_btn.setChecked(True)
        self.scroll_btn.setObjectName('ToolbarButton')
        self.scroll_btn.toggled.connect(self._on_scroll_toggled)
        toolbar.addWidget(self.scroll_btn)

        self.clear_btn = QPushButton('清空')
        self.clear_btn.setObjectName('ToolbarButton')
        self.clear_btn.clicked.connect(self.clear)
        toolbar.addWidget(self.clear_btn)

        layout.addLayout(toolbar)

        self.view = QPlainTextEdit()
        self.view.setObjectName('LogView')
        self.view.setReadOnly(True)
        self.view.setLineWrapMode(QPlainTextEdit.NoWrap)
        self.view.setMaximumBlockCount(self.MAX_LINES)

        layout.addWidget(self.view, 1)

    # ---------- 数据入口 ----------

    def _on_line(self, source: str, level: str, text: str) -> None:
        ts = datetime.now().strftime('%H:%M:%S')
        self._entries.append((ts, source, level, text))
        if self._match(source):
            self._append(ts, source, text)

    # ---------- 渲染 ----------

    def _match(self, source: str) -> bool:
        return self._filter == '*' or source == self._filter

    def _format(self, ts: str, source: str, text: str) -> str:
        if self._filter == '*':
            return f'[{ts}] [{source}] {text}'
        return f'[{ts}] {text}'

    def _append(self, ts: str, source: str, text: str) -> None:
        self.view.appendPlainText(self._format(ts, source, text))
        if self._auto_scroll:
            self._scroll_to_bottom()

    def _scroll_to_bottom(self) -> None:
        bar = self.view.verticalScrollBar()
        bar.setValue(bar.maximum())

    def _rerender(self) -> None:
        self.view.clear()
        for ts, source, _level, text in self._entries:
            if self._match(source):
                self.view.appendPlainText(self._format(ts, source, text))
        if self._auto_scroll:
            self._scroll_to_bottom()

    # ---------- 事件 ----------

    def _on_filter_changed(self, _index: int) -> None:
        self._filter = self.filter_combo.currentData() or LogSource.HOSHIMI.value
        self._rerender()

    def _on_scroll_toggled(self, checked: bool) -> None:
        self._auto_scroll = checked
        self.scroll_btn.setText(f'自动滚动: {"开" if checked else "关"}')
        if checked:
            self._scroll_to_bottom()

    # ---------- 外部接口 ----------

    def set_source_filter(self, source: str) -> None:
        idx = self.filter_combo.findData(source)
        if idx >= 0:
            self.filter_combo.setCurrentIndex(idx)

    def clear(self) -> None:
        self._entries.clear()
        self.view.clear()

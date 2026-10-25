from __future__ import annotations

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
    QPushButton,
    QVBoxLayout,
)

from app.core.prepare_process import PrepareProcess


class PrepareDialog(QDialog):
    """prepare 进度对话框。模态，可取消。"""

    def __init__(self, prep: PrepareProcess, parent=None):
        super().__init__(parent)
        self.prep = prep
        self._elapsed = 0
        self._last_line = ''
        self._result_code: int | None = None

        self.setWindowTitle('正在初始化')
        self.setModal(True)
        self.setMinimumSize(640, 380)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(10)

        self.status = QLabel('正在初始化…')
        self.status.setObjectName('PrepareStatus')
        layout.addWidget(self.status)

        self.output = QPlainTextEdit()
        self.output.setObjectName('LogView')
        self.output.setReadOnly(True)
        self.output.setLineWrapMode(QPlainTextEdit.NoWrap)
        self.output.setMaximumBlockCount(5000)
        layout.addWidget(self.output, 1)

        row = QHBoxLayout()
        row.addStretch(1)
        self.cancel_btn = QPushButton('取消')
        self.cancel_btn.setObjectName('ToolbarButton')
        self.cancel_btn.clicked.connect(self._on_cancel)
        row.addWidget(self.cancel_btn)
        layout.addLayout(row)

        self.prep.output_line.connect(self._on_line)
        self.prep.finished.connect(self._on_finished)
        self.prep.failed_to_start.connect(self._on_failed)

        self._timer = QTimer(self)
        self._timer.setInterval(1000)
        self._timer.timeout.connect(self._tick)
        self._timer.start()
        self._update_status()

    def _tick(self) -> None:
        self._elapsed += 1
        self._update_status()

    def _update_status(self) -> None:
        mm = self._elapsed // 60
        ss = self._elapsed % 60
        head = f'正在初始化… 已用时 {mm:02d}:{ss:02d}'
        if self._last_line:
            self.status.setText(f'{head}\n{self._last_line[:120]}')
        else:
            self.status.setText(head)

    def _on_line(self, text: str) -> None:
        self._last_line = text
        self.output.appendPlainText(text)
        bar = self.output.verticalScrollBar()
        bar.setValue(bar.maximum())
        self._update_status()

    def _on_finished(self, code: int) -> None:
        self._timer.stop()
        self._result_code = code
        if code == -1:
            self.status.setText('已取消')
        elif code == 0:
            self.status.setText('初始化完成')
        else:
            self.status.setText(f'初始化失败（退出码 {code}）')
        self.cancel_btn.setText('关闭')
        self.cancel_btn.clicked.disconnect()
        self.cancel_btn.clicked.connect(self.accept)
        QTimer.singleShot(800, self.accept) if code == 0 else None

    def _on_failed(self, message: str) -> None:
        self._timer.stop()
        self._result_code = -2
        self.status.setText(message)
        self.cancel_btn.setText('关闭')
        self.cancel_btn.clicked.disconnect()
        self.cancel_btn.clicked.connect(self.reject)

    def _on_cancel(self) -> None:
        if self.prep.running:
            self.cancel_btn.setEnabled(False)
            self.cancel_btn.setText('正在取消…')
            self.prep.cancel()
        else:
            self.reject()

    def result_code(self) -> int | None:
        return self._result_code

    def closeEvent(self, event) -> None:
        if self.prep.running:
            self._on_cancel()
            event.ignore()
            return
        super().closeEvent(event)

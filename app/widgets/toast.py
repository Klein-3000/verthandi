from __future__ import annotations

from PySide6.QtCore import QEasingCurve, QPropertyAnimation, Qt, QTimer
from PySide6.QtWidgets import QLabel, QWidget


class Toast(QLabel):
    """短暂显示的浮层提示，2 秒后淡出。"""

    def __init__(self, text: str, host: QWidget, duration_ms: int = 2000):
        super().__init__(text, host)
        self.setObjectName('Toast')
        self.setAlignment(Qt.AlignCenter)
        self.setAttribute(Qt.WA_TransparentForMouseEvents)
        self.setAttribute(Qt.WA_DeleteOnClose, True)
        self.adjustSize()
        self._reposition(host)
        self.show()
        self.raise_()
        QTimer.singleShot(duration_ms, self._fade_out)

    def _reposition(self, host: QWidget) -> None:
        tw, th = self.width(), self.height()
        hw, hh = host.width(), host.height()
        x = max(0, (hw - tw) // 2)
        y = max(0, hh - th - 60)
        self.move(x, y)

    def _fade_out(self) -> None:
        self._anim = QPropertyAnimation(self, b'windowOpacity', self)
        self._anim.setDuration(300)
        self._anim.setStartValue(1.0)
        self._anim.setEndValue(0.0)
        self._anim.setEasingCurve(QEasingCurve.InOutQuad)
        self._anim.finished.connect(self.close)
        self._anim.start()


def show_toast(host: QWidget, text: str, duration_ms: int = 2000) -> None:
    """在 host 所在的顶层窗口底部中央显示 Toast。"""
    Toast(text, host.window(), duration_ms)

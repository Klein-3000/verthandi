from __future__ import annotations

from PySide6.QtCore import (
    QAbstractAnimation,
    QEasingCurve,
    QPropertyAnimation,
    QRect,
    Qt,
    Signal,
)
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QGraphicsDropShadowEffect,
    QHBoxLayout,
    QPushButton,
    QWidget,
)


class CapsuleSwitch(QWidget):
    """胶囊切换：圆角容器 + 带阴影的滑动高亮块"""

    currentIndexChanged = Signal(int)

    def __init__(self, items: list[str], parent=None):
        super().__init__(parent)
        self.setObjectName("capsuleRoot")
        self.setFixedHeight(40)  # 稍微调小一点，适应常规窗口
        self.setAttribute(Qt.WA_StyledBackground, True)  # QSS 背景生效

        self._pad = 4
        self._current = 0
        self._btns: list[QPushButton] = []

        # ① 容器：圆角背景（半径 = 高度/2，形成胶囊）
        self.setStyleSheet("#capsuleRoot { background:#eceef3; border-radius:20px; }")

        # ② 滑块：圆角 + 阴影
        self._slider = QWidget(self)
        self._slider.setObjectName("capsuleSlider")
        self._slider.setStyleSheet(
            "#capsuleSlider { background:#fff; border-radius:16px; }"
        )
        shadow = QGraphicsDropShadowEffect(self._slider)
        shadow.setBlurRadius(12)
        shadow.setOffset(0, 2)
        shadow.setColor(QColor(0, 0, 0, 40))
        self._slider.setGraphicsEffect(shadow)

        # ③ 按钮（等分排列在滑块上方）
        lay = QHBoxLayout(self)
        lay.setContentsMargins(self._pad, self._pad, self._pad, self._pad)
        lay.setSpacing(0)

        for i, text in enumerate(items):
            btn = QPushButton(text)
            btn.setCheckable(True)
            btn.setCursor(Qt.PointingHandCursor)
            btn.setStyleSheet("""
                QPushButton {
                    background: transparent;
                    border: none;
                    outline: none;
                    color: #6b7280;
                    font-size: 13px;
                }
                QPushButton:checked {
                    color: #111827;
                    font-weight: 600;
                }
            """)
            btn.clicked.connect(lambda _=False, idx=i: self.setCurrentIndex(idx))
            lay.addWidget(btn)
            self._btns.append(btn)

        self._slider.lower()  # 滑块置于按钮之下

        # ④ 滑动动画（等价于 CSS transition）
        self._anim = QPropertyAnimation(self._slider, b"geometry", self)
        self._anim.setDuration(260)
        self._anim.setEasingCurve(QEasingCurve.OutCubic)

    def _slot_rect(self, index: int) -> QRect:
        """第 index 格的几何区域"""
        n = len(self._btns)
        if n == 0:
            return QRect()
        w = (self.width() - self._pad * 2) / n
        h = self.height() - self._pad * 2
        x = round(self._pad + index * w)
        return QRect(x, self._pad, round(w), round(h))

    def resizeEvent(self, event):
        super().resizeEvent(event)
        # 动画进行中不要打断，等动画结束后位置自然正确
        if self._anim.state() != QAbstractAnimation.Running:
            self._slider.setGeometry(self._slot_rect(self._current))

    def setCurrentIndex(self, index: int, animate: bool = True):
        if not self._btns:
            return
        index %= len(self._btns)

        # 如果索引没变，也强制更新一下滑块位置（防止窗口刚显示时错位）
        if self._current == index and self._slider.geometry() != self._slot_rect(index):
            self._slider.setGeometry(self._slot_rect(index))

        self._current = index

        for i, b in enumerate(self._btns):
            b.setChecked(i == index)

        target = self._slot_rect(index)
        if (
            animate
            and self.isVisible()
            and self._anim.state() != QAbstractAnimation.Running
        ):
            self._anim.stop()
            self._anim.setStartValue(self._slider.geometry())
            self._anim.setEndValue(target)
            self._anim.start()
        else:
            self._slider.setGeometry(target)

        self.currentIndexChanged.emit(index)

    def keyPressEvent(self, event):
        """支持 ← → ↑ ↓ 切换"""
        if event.key() in (Qt.Key_Left, Qt.Key_Up):
            self.setCurrentIndex(self._current - 1)
        elif event.key() in (Qt.Key_Right, Qt.Key_Down):
            self.setCurrentIndex(self._current + 1)
        else:
            super().keyPressEvent(event)

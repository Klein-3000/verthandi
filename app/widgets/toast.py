"""短暂浮层提示。

底层换成 qfluentwidgets 的 InfoBar（Fluent 风格通知条），
show_toast 签名与旧版保持一致，调用方无感知。
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QWidget
from qfluentwidgets import InfoBar, InfoBarPosition


def show_toast(
    host: QWidget,
    text: str,
    duration_ms: int = 2000,
    level: str = "info",
) -> None:
    """在 host 所在顶层窗口顶部显示一条 Fluent 通知。

    level: info / success / warning / error
    """
    factory = {
        "info": InfoBar.info,
        "success": InfoBar.success,
        "warning": InfoBar.warning,
        "error": InfoBar.error,
    }.get(level, InfoBar.info)

    factory(
        title="",
        content=text,
        orient=Qt.Horizontal,
        isClosable=False,
        position=InfoBarPosition.TOP,
        duration=duration_ms,
        parent=host.window(),
    )

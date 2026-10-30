"""应用图标定位。

查找顺序（"程序目录"指 verthandi.py 所在目录，打包后即 exe 所在目录）：

1. ``<程序目录>/verthandi.ico`` —— 用户可直接替换，改了下次启动即生效；
2. ``<程序目录>/_internal/app/resources/icon.ico`` —— 打包时随程序分发的内置图标；
   源码运行时对应 ``app/resources/icon.ico``；
3. 两者都没有 —— 返回 None，由 Qt 使用默认图标，不报错也不影响启动。
"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtGui import QIcon

from app.core.paths import detect_base_dir, resource_path

#: 用户可替换的图标文件名，放在程序目录下
ICON_FILE_NAME = "verthandi.ico"

#: 随程序分发的内置图标（相对资源根）
BUNDLED_ICON_PARTS = ("app", "resources", "icon.ico")


def icon_candidates() -> list[Path]:
    """按优先级返回候选图标路径（不保证存在）。"""
    return [
        detect_base_dir() / ICON_FILE_NAME,
        resource_path(*BUNDLED_ICON_PARTS),
    ]


def _is_loadable(path: Path) -> bool:
    """文件存在、非空，且真的能被 Qt 解码成一张图。"""
    try:
        if not path.is_file() or path.stat().st_size == 0:
            return False
    except OSError:
        # 权限不足、路径非法等一律视为"没有这个图标"
        return False
    icon = QIcon(str(path))
    if icon.isNull():
        return False
    # QIcon 是惰性加载的，isNull() 只说明"有文件名"；
    # 真正解码一次 16x16 才能识别出损坏的 ico，代价可忽略。
    return not icon.pixmap(16, 16).isNull()


def find_icon_file() -> Path | None:
    """返回第一个可用的图标文件，找不到时返回 None。"""
    for path in icon_candidates():
        if _is_loadable(path):
            return path
    return None


def load_app_icon() -> QIcon | None:
    """加载应用图标；无可用图标或文件损坏时返回 None（调用方应保持默认图标）。"""
    path = find_icon_file()
    return QIcon(str(path)) if path is not None else None

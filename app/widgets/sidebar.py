from __future__ import annotations

from PySide6.QtCore import QTimer, Signal
from PySide6.QtWidgets import QFrame, QVBoxLayout
from qfluentwidgets import (
    FluentIcon,
    NavigationInterface,
    NavigationItemPosition,
)

from app.widgets.icons import PANEL_LEFT

# 折叠按钮的提示文字（qfluentwidgets 自带的是英文 Open/Close Navigation）
_TIP_COLLAPSED = "展开导航栏"
_TIP_EXPANDED = "折叠导航栏"

# (routeKey, 图标, 文本, 位置)，顺序即 QStackedWidget 索引
_NAV_ITEMS: tuple[tuple[str, FluentIcon, str, NavigationItemPosition], ...] = (
    ("main", FluentIcon.HOME, "主页面", NavigationItemPosition.TOP),
    ("settings", FluentIcon.SETTING, "设置", NavigationItemPosition.TOP),
    ("lab", FluentIcon.DEVELOPER_TOOLS, "实验性", NavigationItemPosition.TOP),
    ("about", FluentIcon.INFO, "关于", NavigationItemPosition.BOTTOM),
)


class Sidebar(QFrame):
    """左侧导航（Fluent NavigationInterface 底座）。

    - 默认 48px 只显示图标，悬停高亮并浮出 tooltip；
    - 顶部汉堡按钮点击展开为 200px 图标 + 文字；
    - currentChanged(int) / set_current(int) 与 QStackedWidget 索引一一对应。
    """

    currentChanged = Signal(int)

    def __init__(self):
        super().__init__()
        self.setObjectName("Sidebar")

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        self._nav = NavigationInterface(showMenuButton=True, collapsible=True)
        # 窗口最小宽度 880：保证点汉堡总是"平铺展开"而不是浮层覆盖
        self._nav.setMinimumExpandWidth(700)
        self._nav.panel.setExpandWidth(200)
        layout.addWidget(self._nav)

        self._routes = [key for key, _, _, _ in _NAV_ITEMS]
        for key, icon, text, position in _NAV_ITEMS:
            idx = self._routes.index(key)
            self._nav.addItem(
                key,
                icon,
                text,
                # 注意：clicked 信号会传入一个 bool 占位参数，必须用 _ 接住，
                # 否则 True(=1) 会覆盖 i，导致点击任何项都切到索引 1
                onClick=lambda _, i=idx: self.currentChanged.emit(i),
                selectable=True,
                position=position,
                tooltip=text,
            )

        self._init_menu_button()

    def _init_menu_button(self) -> None:
        """折叠按钮换成截图里的图标，tooltip 中文化。

        qfluentwidgets 会在 expand()/collapse() 里把 tooltip 写成英文
        "Close/Open Navigation"：展开时同步改、收起时是动画结束后才重置，
        所以①监听 displayModeChanged；②点击后立刻补一次（用单次定时器挪到
        toggle() 之后执行），保证悬停时看到的中文不会闪回英文。
        """
        panel = self._nav.panel
        panel.menuButton.setIcon(PANEL_LEFT)
        panel.returnButton.setToolTip("返回")
        panel.displayModeChanged.connect(lambda _: self._sync_menu_button())
        panel.menuButton.clicked.connect(
            lambda: QTimer.singleShot(0, self._sync_menu_button)
        )
        self._sync_menu_button()

    def _sync_menu_button(self) -> None:
        panel = self._nav.panel
        panel.menuButton.setIcon(PANEL_LEFT)
        # expand 属性在 expand()/collapse() 里是同步写入的，比 displayMode 更即时
        expanded = bool(panel.expandAni.property("expand"))
        panel.menuButton.setToolTip(_TIP_EXPANDED if expanded else _TIP_COLLAPSED)

    def set_current(self, index: int) -> None:
        """程序化选中某项（不发 onClick，但保持 currentChanged 信号语义）。"""
        if 0 <= index < len(self._routes):
            self._nav.setCurrentItem(self._routes[index])
            self.currentChanged.emit(index)

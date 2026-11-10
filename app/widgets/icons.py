"""qfluentwidgets 未内置的补充图标。

放在这里而不是零散地 setIcon(QIcon(...))，是为了跟 FluentIcon 行为一致：
按当前主题取黑/白两套 SVG，图标随主题变化，禁用时由控件自动做半透明处理。
"""

from __future__ import annotations

from qfluentwidgets import FluentIconBase, Theme
from qfluentwidgets.common.icon import getIconColor

from app.core.paths import resource_path


class _SvgPairIcon(FluentIconBase):
    """同一图形的「黑/白」两份 SVG，按主题取用。"""

    def __init__(self, name: str):
        self._name = name

    def path(self, theme: Theme = Theme.AUTO) -> str:
        return str(
            resource_path(
                "app", "resources", "icons", f"{self._name}-{getIconColor(theme)}.svg"
            )
        )


# 侧边栏开关：圆角矩形 + 左侧竖条
PANEL_LEFT = _SvgPairIcon("panel-left")

from __future__ import annotations

from qfluentwidgets import ExpandGroupSettingCard


class ExpandCard(ExpandGroupSettingCard):
    """ExpandGroupSettingCard 的轻量修正版。

    qfluentwidgets 1.11.3 的收起动画结束时滚动条值未复位，卡片会残留
    约 20~30px 的高度（内容区漏出一条白边）。这里在动画结束时把卡片
    高度强制贴回头部高度，消除残留。
    """

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.expandAni.finished.connect(self._snap_height_after_animation)

    def _snap_height_after_animation(self) -> None:
        if not self.isExpand:
            self.setFixedHeight(self.card.height())

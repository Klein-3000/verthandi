from __future__ import annotations

from PySide6.QtCore import QTimer
from qfluentwidgets import ExpandGroupSettingCard

# 视口宽度小于这个值时（尚未完成首次布局），heightForWidth 不可信，
# 退回 sizeHint，避免把高度算得离谱
_MIN_TRUSTED_WIDTH = 100


class ExpandCard(ExpandGroupSettingCard):
    """ExpandGroupSettingCard 的轻量修正版。

    修正两处 qfluentwidgets 1.11.3 的已知问题：

    1. 收起动画结束时滚动条值未复位，卡片会残留约 20~30px 的高度
      （内容区漏出一条白边）。这里在动画结束时把卡片高度强制贴回头部高度。
    2. 展开高度按 viewLayout.sizeHint() 计算，而开了 wordWrap 的
       QLabel 的 sizeHint 按"接近正方形"估算，文字越长、卡片越宽，
       高估越厉害，展开后底部会留出一大块空白。这里改用
       heightForWidth 按实际宽度计算真实内容高度，动画结束后贴合。
    """

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._snap_pending = False
        self.expandAni.finished.connect(self._snap_height_after_animation)

    # ---------- 高度计算 ----------

    def _content_height(self) -> int:
        """viewLayout 的真实内容高度（按当前实际宽度算 wordWrap 标签）。"""
        layout = self.viewLayout
        width = self.view.width()
        if width >= _MIN_TRUSTED_WIDTH:
            height = layout.heightForWidth(width)
            if height > 0:
                return height
        return layout.sizeHint().height()

    # ---------- 贴合 ----------

    def _snap_height_after_animation(self) -> None:
        if self.isExpand:
            self.setFixedHeight(self.card.height() + self._content_height())
        else:
            self.setFixedHeight(self.card.height())

    def sync_height(self) -> None:
        """内容文本变化后调用：展开状态下延迟一拍重新贴合实际高度。

        延迟到事件循环是为了让布局先把新文本的尺寸算完；收起状态无需处理，
        展开动画本身会走 _snap_height_after_animation。
        """
        if not self.isExpand or self._snap_pending:
            return
        if self.expandAni.state() == self.expandAni.State.Running:
            return  # 动画结束时会自动贴合
        self._snap_pending = True
        QTimer.singleShot(0, self._do_sync_height)

    def _do_sync_height(self) -> None:
        self._snap_pending = False
        if self.isExpand and self.expandAni.state() != self.expandAni.State.Running:
            self.setFixedHeight(self.card.height() + self._content_height())

    # ---------- 尺寸联动 ----------

    def resizeEvent(self, e) -> None:
        super().resizeEvent(e)
        # 窗口宽度变化会改变 wordWrap 标签的行数，展开状态下重新贴合
        self.sync_height()

    def _adjustViewSize(self) -> None:
        """qfluentwidgets 用各子件 sizeHint 之和撑 spaceWidget，wordWrap
        标签同样会高估；spaceWidget 只在滚动时起作用，用真实高度更稳。"""
        super()._adjustViewSize()
        self.spaceWidget.setFixedHeight(self._content_height() + 3)

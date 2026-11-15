"""誓约照片查看对话框（最终完美自适应版）。

照片目录结构由 hoshimi 的路由决定（photos/<24hex>/<id>/oath/picture_*.jpg），
目录名语义（账号/角色 ID 的哈希）不解析，只按「修改时间倒序」平铺展示，
一级目录名作为存档标签显示在详情里。

缩略图在后台线程用 QImageReader 按目标尺寸解码（QImage 跨线程安全，
QPixmap 必须在主线程创建），滚动到底部自动续载下一批。
"""

from __future__ import annotations

import os
import subprocess
import sys
from collections.abc import Callable
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import QRectF, QSize, Qt, QThread, QTimer, Signal
from PySide6.QtGui import (
    QImage,
    QImageReader,
    QKeySequence,
    QPainter,
    QPainterPath,
    QPixmap,
    QShortcut,
)
from PySide6.QtWidgets import (
    QApplication,
    QDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QSizePolicy,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)
from qfluentwidgets import (
    Action,
    BodyLabel,
    CaptionLabel,
    ElevatedCardWidget,
    IconWidget,
    PrimaryPushButton,
    PushButton,
    RoundMenu,
    SearchLineEdit,
    SimpleCardWidget,
    StrongBodyLabel,
    SubtitleLabel,
    ToolButton,
    isDarkTheme,
)
from qfluentwidgets import (
    FluentIcon as FIF,
)

BATCH_SIZE = 60  # 每批加载的张数
THUMB_RADIUS = 8  # 缩略图圆角半径
SIDE_WIDTH = 360  # 右侧详情栏固定宽度
GRID_SPACING = 8  # 网格间距
CELL_PADDING = 6  # 图片距离格子边缘的内边距


def open_in_explorer(path: Path) -> None:
    """用系统关联的程序打开（Windows 优先，其他平台尽力而为）。"""
    try:
        if sys.platform == "win32":
            os.startfile(str(path))
        elif sys.platform == "darwin":
            subprocess.run(["open", str(path)], check=False)
        else:
            subprocess.run(["xdg-open", str(path)], check=False)
    except OSError:
        pass


def _round_corners(image: QImage, radius: int) -> QImage:
    """给 QImage 加圆角。"""
    out = QImage(image.size(), QImage.Format_ARGB32_Premultiplied)
    out.fill(Qt.transparent)
    painter = QPainter(out)
    painter.setRenderHint(QPainter.Antialiasing)
    path = QPainterPath()
    path.addRoundedRect(QRectF(image.rect()), radius, radius)
    painter.setClipPath(path)
    painter.drawImage(0, 0, image)
    painter.end()
    return out


class _ThumbWorker(QThread):
    """后台解码清晰缩略图（最大边 400px，供主线程平滑缩放）。"""

    thumbReady = Signal(int, QImage)

    def __init__(self, paths: list[Path], parent=None) -> None:
        super().__init__(parent)
        self._paths = paths

    def run(self) -> None:
        for i, path in enumerate(self._paths):
            if self.isInterruptionRequested():
                return
            reader = QImageReader(str(path))
            reader.setAutoTransform(True)
            size = reader.size()
            if size.isValid() and size.width() > 0 and size.height() > 0:
                reader.setScaledSize(size.scaled(400, 400, Qt.KeepAspectRatio))
            image = reader.read()
            if image.isNull():
                continue
            self.thumbReady.emit(i, image)


class PhotoGalleryDialog(QDialog):
    """网格缩略图 + 右侧预览详情。"""

    def __init__(
        self,
        scan: Callable[[], list[Path]],
        root: Path,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._scan = scan
        self._root = root
        self._all_photos: list[Path] = []
        self._photos: list[Path] = []
        self._loaded = 0
        self._worker: _ThumbWorker | None = None
        self._preview_image: QImage | None = None
        self._photo_ratio = 9 / 16  # 默认 16:9

        self._layout_timer = QTimer(self)
        self._layout_timer.setSingleShot(True)
        self._layout_timer.setInterval(40)
        self._layout_timer.timeout.connect(self._update_grid_layout)

        self.setWindowTitle("誓约照片")
        self.resize(1024, 660)

        self._build_ui()
        self._apply_theme()
        self.rescan()

    def _build_ui(self) -> None:
        outer = QVBoxLayout(self)
        outer.setContentsMargins(24, 20, 24, 20)
        outer.setSpacing(14)

        header = QHBoxLayout()
        header.setSpacing(10)
        header.addWidget(SubtitleLabel("誓约照片"))
        self.count_label = CaptionLabel("")
        header.addWidget(self.count_label)
        header.addStretch(1)

        self.search = SearchLineEdit(self)
        self.search.setPlaceholderText("按文件名筛选…")
        self.search.setFixedWidth(240)
        self.search.textChanged.connect(self._on_search)
        header.addWidget(self.search)

        refresh_btn = ToolButton(FIF.SYNC)
        refresh_btn.setToolTip("重新扫描照片目录")
        refresh_btn.clicked.connect(self.rescan)
        header.addWidget(refresh_btn)
        outer.addLayout(header)

        content = QHBoxLayout()
        content.setSpacing(14)

        grid_card = SimpleCardWidget(self)
        grid_layout = QVBoxLayout(grid_card)
        grid_layout.setContentsMargins(6, 6, 6, 6)

        self.stack = QStackedWidget(grid_card)
        self.grid = self._build_grid()
        self.stack.addWidget(self.grid)
        self.stack.addWidget(self._build_empty())
        grid_layout.addWidget(self.stack)
        content.addWidget(grid_card, 1)

        side_card = ElevatedCardWidget(self)
        side_card.setFixedWidth(SIDE_WIDTH)
        side_layout = QVBoxLayout(side_card)
        side_layout.setContentsMargins(16, 16, 16, 16)
        side_layout.setSpacing(12)

        self.preview = QLabel("选择左侧照片预览")
        self.preview.setAlignment(Qt.AlignCenter)
        self.preview.setMinimumSize(300, 240)
        self.preview.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding
        )
        self.preview.setObjectName("previewLabel")
        side_layout.addWidget(self.preview, 1)

        self.info_form = QFormLayout()
        self.info_form.setLabelAlignment(Qt.AlignRight | Qt.AlignVCenter)
        self.info_form.setHorizontalSpacing(12)
        self.info_form.setVerticalSpacing(8)

        self.name_label = BodyLabel("-")
        self.name_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.name_label.setWordWrap(True)
        self.time_label = BodyLabel("-")
        self.size_label = BodyLabel("-")
        self.archive_label = BodyLabel("-")
        self.archive_label.setObjectName("archiveTag")
        self.archive_label.setTextInteractionFlags(Qt.TextSelectableByMouse)

        for key, widget in (
            ("文件名", self.name_label),
            ("修改时间", self.time_label),
            ("大小", self.size_label),
            ("存档", self.archive_label),
        ):
            self.info_form.addRow(CaptionLabel(key), widget)
        side_layout.addLayout(self.info_form)
        side_layout.addStretch(1)

        self.open_btn = PrimaryPushButton(FIF.VIEW, "打开图片")
        self.open_btn.clicked.connect(lambda: self._open_selected(open_in_explorer))
        side_layout.addWidget(self.open_btn)

        folder_btn = PushButton(FIF.FOLDER, "打开所在目录")
        folder_btn.clicked.connect(
            lambda: self._open_selected(lambda p: open_in_explorer(p.parent))
        )
        side_layout.addWidget(folder_btn)

        content.addWidget(side_card)
        outer.addLayout(content, 1)

        copy_sc = QShortcut(QKeySequence.Copy, self)
        copy_sc.activated.connect(self._copy_current_path)

    def _build_grid(self) -> QListWidget:
        grid = QListWidget(self)
        grid.setObjectName("photoGrid")
        grid.setViewMode(QListWidget.IconMode)
        grid.setResizeMode(QListWidget.Adjust)
        grid.setMovement(QListWidget.Static)
        grid.setSpacing(GRID_SPACING)
        grid.setVerticalScrollMode(QListWidget.ScrollPerPixel)
        grid.setContextMenuPolicy(Qt.CustomContextMenu)
        grid.customContextMenuRequested.connect(self._show_context_menu)

        # 【修复2】仅保留 itemDoubleClicked，删掉 itemActivated 防止重复触发
        grid.itemDoubleClicked.connect(
            lambda item: open_in_explorer(item.data(Qt.UserRole))
        )

        grid.currentItemChanged.connect(self._on_current_changed)
        grid.setFocusPolicy(Qt.StrongFocus)
        grid.verticalScrollBar().valueChanged.connect(self._maybe_load_more)
        return grid

    def _build_empty(self) -> QWidget:
        w = QWidget(self)
        v = QVBoxLayout(w)
        v.setAlignment(Qt.AlignCenter)
        v.setSpacing(10)
        icon = IconWidget(FIF.PHOTO, w)
        icon.setFixedSize(64, 64)
        v.addWidget(icon, 0, Qt.AlignHCenter)
        v.addWidget(StrongBodyLabel("还没有誓约照片"), 0, Qt.AlignHCenter)
        v.addWidget(
            CaptionLabel("照片目录为空，或还没有生成过誓约图片。"),
            0,
            Qt.AlignHCenter,
        )
        rescan_btn = PrimaryPushButton(FIF.SYNC, "重新扫描")
        rescan_btn.clicked.connect(self.rescan)
        v.addWidget(rescan_btn, 0, Qt.AlignHCenter)
        return w

    def _apply_theme(self) -> None:
        dark = isDarkTheme()
        bg = "#1e1e1e" if dark else "#f5f6f8"
        hover = "rgba(255,255,255,0.08)" if dark else "rgba(0,0,0,0.06)"
        selected = "rgba(0,120,212,0.28)" if dark else "rgba(0,120,212,0.15)"
        preview_bg = "rgba(255,255,255,0.04)" if dark else "rgba(0,0,0,0.04)"
        scroll_handle = "rgba(255,255,255,0.25)" if dark else "rgba(0,0,0,0.20)"
        scroll_handle_hover = "rgba(255,255,255,0.40)" if dark else "rgba(0,0,0,0.35)"

        self.setStyleSheet(
            f"""
            QDialog {{ background: {bg}; }}
            QListWidget#photoGrid {{
                background: transparent;
                border: none;
                outline: none;
            }}
            /* 【修复1】去掉了 margin 和 padding，完全交给代码和 setGridSize 去算 */
            QListWidget#photoGrid::item {{
                border-radius: 12px;
            }}
            QListWidget#photoGrid::item:hover {{
                background: {hover};
            }}
            QListWidget#photoGrid::item:selected {{
                background: {selected};
            }}
            QLabel#previewLabel {{
                background: {preview_bg};
                border-radius: 10px;
                color: #9ca3af;
            }}
            QLabel#archiveTag {{
                background: rgba(0,120,212,0.14);
                border-radius: 8px;
                padding: 2px 8px;
            }}
            QScrollBar:vertical {{
                background: transparent; width: 8px; margin: 0; border-radius: 4px;
            }}
            QScrollBar::handle:vertical {{
                background: {scroll_handle}; min-height: 28px; border-radius: 4px;
            }}
            QScrollBar::handle:vertical:hover {{
                background: {scroll_handle_hover};
            }}
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0; }}
            QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {{ background: transparent; }}
            """
        )

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        if hasattr(self, "_layout_timer"):
            self._layout_timer.start()
        self._refresh_preview()

    def _update_grid_layout(self) -> None:
        """动态计算网格，利用 setGridSize 绝对控制列数与间距。"""
        if not hasattr(self, "grid") or self.grid is None:
            return

        vw = self.grid.viewport().width()
        if vw <= 10:
            return

        # 动态计算列数（最多3列）：假设每个图片最小需要 260px 的空间
        cols = max(1, min(3, vw // 260))

        # 计算每个格子的总宽度。
        # 视口宽度 - 左右两侧的间距(GRID_SPACING) / 列数
        cell_w = (vw - GRID_SPACING * (cols + 1)) // cols
        if cell_w <= 0:
            return

        # 扣除内边距得出实际的图标宽度
        icon_w = cell_w - CELL_PADDING * 2
        icon_h = int(icon_w * self._photo_ratio)
        cell_h = icon_h + CELL_PADDING * 2

        self.grid.setIconSize(QSize(icon_w, icon_h))
        self.grid.setGridSize(QSize(cell_w, cell_h))

        # 重新应用图标
        for i in range(self.grid.count()):
            self._apply_item_icon(self.grid.item(i))

    def _maybe_load_more(self, _value: int = 0) -> None:
        bar = self.grid.verticalScrollBar()
        if bar.maximum() - bar.value() < 120:
            self._load_batch()

    def _load_batch(self) -> None:
        if self._worker is not None:
            return
        chunk = self._photos[self._loaded : self._loaded + BATCH_SIZE]
        if not chunk:
            return

        # 初始尺寸用网格的 gridSize，如果没算出，给个默认值
        curr_grid_size = self.grid.gridSize()
        if curr_grid_size.width() <= 0:
            curr_grid_size = QSize(240, 150)

        for path in chunk:
            item = QListWidgetItem()
            # setSizeHint 是必须的，给 Grid 一个基础参考
            item.setSizeHint(curr_grid_size)
            item.setData(Qt.UserRole, path)
            item.setToolTip(path.name)

            # 透明占位图
            placeholder = QImage(
                self.grid.iconSize(), QImage.Format_ARGB32_Premultiplied
            )
            placeholder.fill(Qt.transparent)
            item.setIcon(QPixmap.fromImage(placeholder))
            self.grid.addItem(item)

        worker = _ThumbWorker(chunk, self)
        worker.thumbReady.connect(self._on_thumb_ready)
        worker.finished.connect(self._on_batch_done)
        self._worker = worker
        worker.start()

    def _on_thumb_ready(self, index: int, image: QImage) -> None:
        item = self.grid.item(self._loaded + index)
        if item is not None:
            item.setData(Qt.UserRole + 1, image)  # 缓存大缩略图
            self._apply_item_icon(item)

    def _apply_item_icon(self, item: QListWidgetItem) -> None:
        target_size = self.grid.iconSize()
        if target_size.width() <= 0:
            return

        image: QImage | None = item.data(Qt.UserRole + 1)
        if image is None or image.isNull():
            return

        # 缩放后居中放在透明画布上，兼容各种奇葩比例
        scaled = image.scaled(target_size, Qt.KeepAspectRatio, Qt.SmoothTransformation)
        canvas = QImage(target_size, QImage.Format_ARGB32_Premultiplied)
        canvas.fill(Qt.transparent)

        painter = QPainter(canvas)
        x = (target_size.width() - scaled.width()) // 2
        y = (target_size.height() - scaled.height()) // 2
        painter.drawImage(x, y, scaled)
        painter.end()

        rounded = _round_corners(canvas, THUMB_RADIUS)
        item.setIcon(QPixmap.fromImage(rounded))

    def _on_batch_done(self) -> None:
        if self._worker is not None:
            self._worker.deleteLater()
            self._worker = None
        self._loaded = min(self._loaded + BATCH_SIZE, len(self._photos))
        if self._loaded < len(self._photos):
            self._maybe_load_more()

    def _stop_worker(self) -> None:
        if self._worker is None:
            return
        try:
            self._worker.thumbReady.disconnect()
            self._worker.finished.disconnect()
        except (RuntimeError, TypeError):
            pass
        self._worker.requestInterruption()
        self._worker.wait(1000)
        self._worker.deleteLater()
        self._worker = None

    def rescan(self) -> None:
        self._stop_worker()
        self._all_photos = self._scan()
        self._photos = list(self._all_photos)

        if self._photos:
            reader = QImageReader(str(self._photos[0]))
            sz = reader.size()
            if sz.isValid() and sz.width() > 0:
                self._photo_ratio = sz.height() / sz.width()
            else:
                self._photo_ratio = 9 / 16
        else:
            self._photo_ratio = 9 / 16

        self.search.blockSignals(True)
        self.search.clear()
        self.search.blockSignals(False)
        self._reload_grid()

    def _reload_grid(self) -> None:
        self.grid.clear()
        self._loaded = 0
        self.count_label.setText(f"共 {len(self._photos)} 张")
        self.preview.clear()
        self.preview.setText("选择左侧照片预览")
        self._preview_image = None
        for widget in (
            self.name_label,
            self.time_label,
            self.size_label,
            self.archive_label,
        ):
            widget.setText("-")
        self.stack.setCurrentIndex(0 if self._photos else 1)

        # 刷新后触发重算，等待视口稳定
        self._layout_timer.start()
        self._load_batch()

    def _on_search(self, text: str) -> None:
        keyword = text.strip().lower()
        self._stop_worker()
        if keyword:
            self._photos = [p for p in self._all_photos if keyword in p.name.lower()]
        else:
            self._photos = list(self._all_photos)
        self._reload_grid()

    def _current_path(self) -> Path | None:
        item = self.grid.currentItem()
        return item.data(Qt.UserRole) if item is not None else None

    def _open_selected(self, action: Callable[[Path], None]) -> None:
        path = self._current_path()
        if path is not None:
            action(path)

    def _copy_current_path(self) -> None:
        path = self._current_path()
        if path is not None:
            QApplication.clipboard().setText(str(path))

    def _on_current_changed(self, current, _previous) -> None:
        if current is None:
            return
        path: Path = current.data(Qt.UserRole)

        reader = QImageReader(str(path))
        reader.setAutoTransform(True)
        size = reader.size()
        target = self.preview.size()
        if (
            size.isValid()
            and size.width() > 0
            and size.height() > 0
            and target.width() > 0
            and target.height() > 0
        ):
            reader.setScaledSize(size.scaled(target, Qt.KeepAspectRatio))
        image = reader.read()
        self._preview_image = None if image.isNull() else image
        self._refresh_preview()

        try:
            stat = path.stat()
            mtime = datetime.fromtimestamp(stat.st_mtime).strftime("%Y-%m-%d %H:%M:%S")
            size_kb = stat.st_size / 1024
            size_text = (
                f"{size_kb / 1024:.1f} MB" if size_kb > 1024 else f"{size_kb:.0f} KB"
            )
        except OSError:
            mtime, size_text = "未知", "-"

        try:
            archive = path.relative_to(self._root).parts[0]
        except ValueError:
            archive = ""

        self.name_label.setText(path.name)
        self.time_label.setText(mtime)
        self.size_label.setText(size_text)
        self.archive_label.setText(archive or "-")

    def _refresh_preview(self) -> None:
        if self._preview_image is None or self._preview_image.isNull():
            return
        target = self.preview.size()
        if target.width() <= 0 or target.height() <= 0:
            return
        self.preview.setPixmap(
            QPixmap.fromImage(self._preview_image).scaled(
                target, Qt.KeepAspectRatio, Qt.SmoothTransformation
            )
        )

    def _show_context_menu(self, pos) -> None:
        item = self.grid.itemAt(pos)
        if item is None:
            return
        path: Path = item.data(Qt.UserRole)
        menu = RoundMenu(parent=self)
        menu.addAction(
            Action(FIF.VIEW, "打开图片", triggered=lambda: open_in_explorer(path))
        )
        menu.addAction(
            Action(
                FIF.FOLDER,
                "打开所在目录",
                triggered=lambda: open_in_explorer(path.parent),
            )
        )
        menu.addSeparator()
        menu.addAction(
            Action(
                FIF.COPY,
                "复制路径",
                triggered=lambda: QApplication.clipboard().setText(str(path)),
            )
        )
        menu.exec(self.grid.mapToGlobal(pos))

    def closeEvent(self, event) -> None:
        self._stop_worker()
        super().closeEvent(event)

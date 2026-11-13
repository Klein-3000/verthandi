"""誓约照片查看对话框。

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

from PySide6.QtCore import QSize, Qt, QThread, Signal
from PySide6.QtGui import QAction, QImage, QImageReader, QPixmap
from PySide6.QtWidgets import (
    QApplication,
    QDialog,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMenu,
    QVBoxLayout,
    QWidget,
)
from qfluentwidgets import (
    CaptionLabel,
    PrimaryPushButton,
    PushButton,
    SubtitleLabel,
    ToolButton,
)
from qfluentwidgets import (
    FluentIcon as FIF,
)

THUMB_SIZE = 160  # 缩略图边长（px）
BATCH_SIZE = 60  # 每批加载的张数


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


class _ThumbWorker(QThread):
    """逐张解码缩略图。QImage 可跨线程传递，QPixmap 在主线程转换。"""

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
                reader.setScaledSize(
                    size.scaled(THUMB_SIZE, THUMB_SIZE, Qt.KeepAspectRatio)
                )
            image = reader.read()
            if image.isNull():
                continue
            self.thumbReady.emit(i, image)


class PhotoGalleryDialog(QDialog):
    """网格缩略图 + 右侧预览详情。

    scan 返回当前照片列表（按修改时间倒序）；root 为照片根目录，
    用于详情里的相对路径展示。
    """

    def __init__(
        self,
        scan: Callable[[], list[Path]],
        root: Path,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._scan = scan
        self._root = root
        self._photos: list[Path] = []
        self._loaded = 0
        self._worker: _ThumbWorker | None = None

        self.setWindowTitle("誓约照片")
        self.resize(900, 580)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 16, 20, 16)
        layout.setSpacing(10)

        header = QHBoxLayout()
        self.count_label = SubtitleLabel("")
        header.addWidget(self.count_label)
        header.addStretch(1)
        refresh_btn = ToolButton(FIF.SYNC)
        refresh_btn.setToolTip("重新扫描照片目录")
        refresh_btn.clicked.connect(self.rescan)
        header.addWidget(refresh_btn)
        layout.addLayout(header)

        content = QHBoxLayout()
        content.setSpacing(12)

        self.grid = QListWidget(self)
        self.grid.setViewMode(QListWidget.IconMode)
        self.grid.setResizeMode(QListWidget.Adjust)
        self.grid.setMovement(QListWidget.Static)
        self.grid.setUniformItemSizes(True)
        self.grid.setSpacing(8)
        self.grid.setIconSize(QSize(THUMB_SIZE, THUMB_SIZE))
        self.grid.setVerticalScrollMode(QListWidget.ScrollPerPixel)
        self.grid.setContextMenuPolicy(Qt.CustomContextMenu)
        self.grid.customContextMenuRequested.connect(self._show_context_menu)
        self.grid.itemDoubleClicked.connect(
            lambda item: open_in_explorer(item.data(Qt.UserRole))
        )
        self.grid.currentItemChanged.connect(self._on_current_changed)
        self.grid.verticalScrollBar().valueChanged.connect(self._maybe_load_more)
        content.addWidget(self.grid, 1)

        side = QVBoxLayout()
        side.setSpacing(8)
        self.preview = QLabel("选择左侧照片预览")
        self.preview.setAlignment(Qt.AlignCenter)
        self.preview.setFixedSize(288, 288)
        self.preview.setStyleSheet(
            "background: rgba(0,0,0,0.04); border-radius: 6px; color: #9ca3af;"
        )
        side.addWidget(self.preview)
        self.info_label = CaptionLabel("")
        self.info_label.setWordWrap(True)
        side.addWidget(self.info_label)
        side.addStretch(1)
        self.open_btn = PrimaryPushButton("打开图片")
        self.open_btn.clicked.connect(lambda: self._open_selected(open_in_explorer))
        side.addWidget(self.open_btn)
        folder_btn = PushButton("打开所在目录")
        folder_btn.clicked.connect(
            lambda: self._open_selected(lambda p: open_in_explorer(p.parent))
        )
        side.addWidget(folder_btn)
        content.addLayout(side)

        layout.addLayout(content, 1)

        self.rescan()

    # ---------- 加载 ----------

    def _maybe_load_more(self, value: int) -> None:
        bar = self.grid.verticalScrollBar()
        if bar.maximum() - value < 120:
            self._load_batch()

    def _load_batch(self) -> None:
        if self._worker is not None:
            return  # 上一批还在解码，滚动/批次结束时还会继续
        chunk = self._photos[self._loaded : self._loaded + BATCH_SIZE]
        if not chunk:
            return
        for path in chunk:
            item = QListWidgetItem()
            item.setSizeHint(QSize(THUMB_SIZE + 24, THUMB_SIZE + 40))
            item.setData(Qt.UserRole, path)
            item.setToolTip(path.name)
            self.grid.addItem(item)
        worker = _ThumbWorker(chunk, self)
        worker.thumbReady.connect(self._on_thumb_ready)
        worker.finished.connect(self._on_batch_done)
        self._worker = worker
        worker.start()

    def _on_thumb_ready(self, index: int, image: QImage) -> None:
        item = self.grid.item(self._loaded + index)
        if item is not None:
            item.setIcon(QPixmap.fromImage(image))

    def _on_batch_done(self) -> None:
        if self._worker is not None:
            self._worker.deleteLater()
            self._worker = None
        self._loaded = min(self._loaded + BATCH_SIZE, len(self._photos))
        if self._loaded < len(self._photos):
            self._maybe_load_more(self.grid.verticalScrollBar().value())

    # ---------- 刷新 ----------

    def rescan(self) -> None:
        if self._worker is not None:
            self._worker.requestInterruption()
            self._worker.wait(1000)
            self._worker.deleteLater()
            self._worker = None
        self.grid.clear()
        self._photos = self._scan()
        self._loaded = 0
        self.count_label.setText(f"共 {len(self._photos)} 张")
        self.preview.clear()
        self.preview.setText("选择左侧照片预览")
        self.info_label.setText("")
        self._load_batch()

    # ---------- 选择与操作 ----------

    def _current_path(self) -> Path | None:
        item = self.grid.currentItem()
        return item.data(Qt.UserRole) if item is not None else None

    def _open_selected(self, action: Callable[[Path], None]) -> None:
        path = self._current_path()
        if path is not None:
            action(path)

    def _on_current_changed(self, current, _previous) -> None:
        if current is None:
            return
        path: Path = current.data(Qt.UserRole)
        pixmap = QPixmap(str(path))
        if not pixmap.isNull():
            self.preview.setPixmap(
                pixmap.scaled(
                    self.preview.size(),
                    Qt.KeepAspectRatio,
                    Qt.SmoothTransformation,
                )
            )
        try:
            stat = path.stat()
            mtime = datetime.fromtimestamp(stat.st_mtime).strftime("%Y-%m-%d %H:%M")
            size_kb = stat.st_size / 1024
            size_text = (
                f"{size_kb / 1024:.1f} MB" if size_kb > 1024 else f"{size_kb:.0f} KB"
            )
        except OSError:
            mtime, size_text = "未知", ""
        try:
            archive = path.relative_to(self._root).parts[0]
        except ValueError:
            archive = ""
        self.info_label.setText(
            f"{path.name}\n{mtime}　{size_text}\n存档：{archive[:16]}…"
        )

    def _show_context_menu(self, pos) -> None:
        item = self.grid.itemAt(pos)
        if item is None:
            return
        path: Path = item.data(Qt.UserRole)
        menu = QMenu(self)
        act_open = QAction("打开图片", menu)
        act_open.triggered.connect(lambda: open_in_explorer(path))
        act_folder = QAction("打开所在目录", menu)
        act_folder.triggered.connect(lambda: open_in_explorer(path.parent))
        act_copy = QAction("复制路径", menu)
        act_copy.triggered.connect(lambda: QApplication.clipboard().setText(str(path)))
        for act in (act_open, act_folder, act_copy):
            menu.addAction(act)
        menu.exec(self.grid.mapToGlobal(pos))

    def closeEvent(self, event) -> None:
        if self._worker is not None:
            self._worker.requestInterruption()
            self._worker.wait(1000)
            self._worker.deleteLater()
            self._worker = None
        super().closeEvent(event)

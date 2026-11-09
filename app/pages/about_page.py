from __future__ import annotations

import sys
from pathlib import Path

from PySide6.QtCore import Qt, QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QApplication,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)
from qfluentwidgets import FluentIcon as FIF, HyperlinkButton

from app import __version__
from app.core.hoshimi_finder import find_hoshimi_exe
from app.core.paths import Paths
from app.core.runtime_layout import resolve_runtime
from app.core.settings import Settings
from app.widgets.expand_card import ExpandCard
from app.widgets.fluent_message import warning
from app.widgets.toast import show_toast

REPO_GITHUB = "https://github.com/Klein-3000/verthandi"
REPO_GITEE = "https://gitee.com/verthandi-v/verthandi"
ISSUES_GITHUB = "https://github.com/Klein-3000/verthandi/issues"
HOSHIMI_REPO = "https://github.com/Enigfrank/Hoshimi"
FLUENT_WIDGETS_REPO = "https://github.com/zhiyiYo/PyQt-Fluent-Widgets"


def _license_file() -> Path:
    """本地 LICENSE 文件路径：开发态在仓库根，打包后在解包目录。"""
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        return Path(meipass) / "LICENSE"
    return Path(__file__).resolve().parents[2] / "LICENSE"


class AboutPage(QWidget):
    def __init__(self, base_dir: Path, paths: Paths, settings: Settings):
        super().__init__()
        self.base_dir = base_dir
        self.paths = paths
        self.settings = settings

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        outer.addWidget(scroll)

        content = QWidget()
        scroll.setWidget(content)

        layout = QVBoxLayout(content)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(16)

        layout.addWidget(self._build_header())
        layout.addWidget(self._build_links())
        layout.addWidget(self._build_environment())
        layout.addWidget(self._build_disclaimer())
        layout.addWidget(self._build_thanks())
        layout.addStretch(1)

    # ---------- 头部 ----------

    def _build_header(self) -> QWidget:
        box = QWidget()
        layout = QVBoxLayout(box)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)

        row = QHBoxLayout()
        row.setSpacing(10)

        title = QLabel("Verthandi")
        title.setObjectName("AboutName")
        row.addWidget(title)

        version = QLabel(f"v{__version__}")
        version.setObjectName("MutedText")
        row.addWidget(version)

        copy_btn = QPushButton("复制版本")
        copy_btn.setObjectName("IconButton")
        copy_btn.setToolTip("复制版本号到剪贴板")
        copy_btn.clicked.connect(self._on_copy_version)
        row.addWidget(copy_btn)

        row.addStretch(1)
        layout.addLayout(row)

        desc = QLabel(
            "Hoshimi 服务端的桌面控制台。\n"
            "把 hoshimi.exe 的启动、切服、初始化封装成图形界面。"
        )
        desc.setObjectName("MutedText")
        desc.setWordWrap(True)
        layout.addWidget(desc)

        return box

    # ---------- 相关项目 ----------

    def _build_links(self) -> QFrame:
        card, box = self._new_card("相关项目")

        entries = (
            ("Verthandi 仓库（GitHub）", REPO_GITHUB, "主仓库"),
            ("Verthandi 仓库（Gitee）", REPO_GITEE, "国内镜像"),
            ("问题反馈（GitHub Issues）", ISSUES_GITHUB, "GUI 相关问题"),
            ("Hoshimi 服务端（上游）", HOSHIMI_REPO, "服务端实现，非 GUI"),
        )
        for label, url, hint in entries:
            box.addLayout(self._link_row(label, url, hint))

        return card

    def _link_row(self, label: str, url: str, hint: str) -> QHBoxLayout:
        row = QHBoxLayout()
        row.setSpacing(8)

        key = QLabel(label)
        key.setObjectName("StatusKey")
        key.setFixedWidth(190)
        row.addWidget(key)

        hint_lbl = QLabel(hint)
        hint_lbl.setObjectName("MutedText")
        hint_lbl.setWordWrap(True)
        row.addWidget(hint_lbl, 1)

        open_btn = HyperlinkButton(url, "打开")
        row.addWidget(open_btn)
        return row

    # ---------- 运行环境 ----------

    def _build_environment(self) -> ExpandCard:
        card = ExpandCard(
            FIF.CODE, "运行环境", "Python / PySide6 / 运行模式与目录信息"
        )

        body = QWidget()
        body.setStyleSheet("background: transparent;")
        grid = QGridLayout(body)
        grid.setContentsMargins(48, 4, 24, 16)
        grid.setHorizontalSpacing(16)
        grid.setVerticalSpacing(6)

        _exe, version, auto = find_hoshimi_exe(self.paths, self.settings)
        if version:
            version_text = f"{version}（自动选择）" if auto else version
        else:
            version_text = "未找到"

        rows = (
            ("Python", QLabel(self._python_version())),
            ("PySide6", QLabel(self._pyside_version())),
            ("运行模式", QLabel(self._mode_label())),
            ("配置路径", None),  # 特殊行：路径 + 打开按钮
            ("home 目录", None),
            ("runtime 目录", None),
            ("hoshimi 版本", QLabel(version_text)),
        )

        path_rows = {
            "配置路径": self.paths.settings_path.parent,
            "home 目录": self.paths.home_dir(self.settings),
            "runtime 目录": resolve_runtime(self.paths, self.settings).directory,
        }

        for i, (name, widget) in enumerate(rows):
            key = QLabel(name)
            # 展开卡自带样式会把 QLabel 刷成黑色，这里内联指定灰色
            key.setStyleSheet("color: #6b7280; background: transparent;")
            key.setFixedWidth(120)
            grid.addWidget(key, i, 0)
            if widget is not None:
                widget.setStyleSheet("color: #1f2328; background: transparent;")
                widget.setTextInteractionFlags(Qt.TextSelectableByMouse)
                grid.addWidget(widget, i, 1)
            else:
                grid.addLayout(self._path_row(path_rows[name]), i, 1)

        grid.setColumnStretch(1, 1)

        card.addGroupWidget(body)
        return card

    def _path_row(self, path: Path) -> QHBoxLayout:
        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(8)

        label = QLabel(str(path))
        label.setStyleSheet("color: #1f2328; background: transparent;")
        label.setWordWrap(True)  # 长路径自动折行，避免把"打开"按钮挤出可视区
        label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        row.addWidget(label, 1)

        btn = HyperlinkButton()
        btn.setText("打开")
        btn.clicked.connect(lambda _=False, p=path: self._open_path(p))
        row.addWidget(btn)
        return row

    # ---------- 免责声明 ----------

    def _build_disclaimer(self) -> QFrame:
        card, box = self._new_card("免责声明")
        text = QLabel(
            "本项目与《深空之眼》官方及 Hoshimi 服务端团队均无关联。\n"
            "仅供本地测试与熟人联机使用，请勿用于商业用途。\n"
            "使用本项目产生的任何后果由使用者自行承担。"
        )
        text.setObjectName("MutedText")
        text.setWordWrap(True)
        box.addWidget(text)
        return card

    # ---------- 致谢与许可 ----------

    def _build_thanks(self) -> QFrame:
        card, box = self._new_card("致谢与许可")

        for item in (
            "Hoshimi 服务端（Enigfrank）",
            "PyQt-Fluent-Widgets（zhiyiYo，GPL-3.0）",
            "PySide6（Qt for Python，LGPL-3.0）",
            "pycryptodome",
            "psutil",
        ):
            lbl = QLabel(f"• {item}")
            lbl.setObjectName("MutedText")
            box.addWidget(lbl)

        lic_row = QLabel(
            "许可证：GPL-3.0\n"
            "本软件是自由软件：你可以自由使用、学习和修改，再分发时必须同样"
            "以 GPL-3.0 许可证开源并提供源代码。本软件不提供任何担保。\n"
            "Copyright (C) 2026 Klein-3000"
        )
        lic_row.setObjectName("MutedText")
        lic_row.setWordWrap(True)
        box.addWidget(lic_row)

        row = QHBoxLayout()
        row.addStretch(1)
        lic_btn = HyperlinkButton(REPO_GITHUB, "查看项目主页")
        row.addWidget(lic_btn)

        lic_file = _license_file()
        # 空 URL：HyperlinkButton 对无效 URL 不会开浏览器，点击行为由 _open_license 接管
        lic_local_btn = HyperlinkButton("", "查看许可")
        lic_local_btn.clicked.connect(lambda _=False: self._open_license(lic_file))
        row.addWidget(lic_local_btn)
        box.addLayout(row)

        return card

    def _open_license(self, path: Path) -> None:
        """打开随程序分发的本地 LICENSE 文件。"""
        if not path.exists():
            show_toast(self, f"未找到许可文件：{path.name}")
            return
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(path)))

    # ---------- 通用 ----------

    def _new_card(self, title_text: str) -> tuple[QFrame, QVBoxLayout]:
        card = QFrame()
        card.setObjectName("Card")
        layout = QVBoxLayout(card)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(8)

        title = QLabel(title_text)
        title.setObjectName("CardTitle")
        layout.addWidget(title)
        return card, layout

    def _python_version(self) -> str:
        v = sys.version_info
        return f"{v.major}.{v.minor}.{v.micro}"

    def _pyside_version(self) -> str:
        try:
            import PySide6

            return PySide6.__version__
        except Exception:
            return "未知"

    def _mode_label(self) -> str:
        return "打包（onedir）" if getattr(sys, "frozen", False) else "源码"

    # ---------- 交互 ----------

    def _open_url(self, url: str) -> None:
        if not QDesktopServices.openUrl(QUrl(url)):
            warning(self, "无法打开链接", f"系统未能打开：\n{url}")

    def _open_path(self, path: Path) -> None:
        try:
            path.mkdir(parents=True, exist_ok=True)
        except OSError as e:
            warning(
                self,
                "无法打开目录",
                f"目录创建失败：\n{path}\n\n{e}",
            )
            return
        if not QDesktopServices.openUrl(QUrl.fromLocalFile(str(path))):
            warning(
                self,
                "无法打开目录",
                f"系统未能打开：\n{path}",
            )

    def _on_copy_version(self) -> None:
        QApplication.clipboard().setText(__version__)
        show_toast(self, f"已复制版本号：{__version__}")


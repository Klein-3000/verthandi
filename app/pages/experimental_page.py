"""实验性功能页。

目前承载 hoshimi 的 import-official（官服存档导入，0.0.8+ 实验功能）：

- 卡 1「官服数据导入」：前置检查、在线/离线（快照）两种模式、
  实时日志、结果展示；
- 卡 2「快照库」：data/official-snapshots/ 清单，选中后回填卡 1。

导入全程服务必须处于停止状态（复用 ServerProcess 状态联动）；
客户端建议处于官服状态，但不强制拦截（离线导入不依赖官服缓存）。
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from PySide6.QtCore import Qt, QThread, QUrl, Signal
from PySide6.QtGui import QDesktopServices, QFont
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)
from qfluentwidgets import CheckBox, ComboBox, PrimaryPushButton
from qfluentwidgets import FluentIcon as FIF

from app.core import official_import
from app.core.client_state import ClientMode, read_client_mode
from app.core.hoshimi_finder import find_hoshimi_exe
from app.core.paths import Paths
from app.core.server_process import ServerProcess, ServerState
from app.core.settings import Settings
from app.widgets.expand_card import ExpandCard
from app.widgets.fluent_message import question, warning
from app.widgets.toast import show_toast

_CREATE_NO_WINDOW = 0x08000000 if sys.platform == "win32" else 0


class _ImportJob(QThread):
    """流式执行 import-official：逐行吐输出，结束时带返回码。"""

    line = Signal(str)
    finished_run = Signal(int)
    spawn_error = Signal(str)

    def __init__(self, cmd: list[str], cwd: Path, parent: QWidget | None = None):
        super().__init__(parent)
        self._cmd = cmd
        self._cwd = cwd
        self._proc: subprocess.Popen | None = None

    def run(self) -> None:
        try:
            self._proc = subprocess.Popen(
                self._cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding="utf-8",
                errors="replace",
                bufsize=1,
                cwd=str(self._cwd),
                creationflags=_CREATE_NO_WINDOW,
            )
        except OSError as e:
            self.spawn_error.emit(f"启动 hoshimi 失败：{e}")
            return
        for raw in self._proc.stdout:  # type: ignore[union-attr]
            self.line.emit(raw.rstrip("\r\n"))
        code = self._proc.wait()
        self.finished_run.emit(code)

    def stop(self) -> None:
        """页面销毁时兜底：不强杀（导入写存档），只请求进程随管道结束。"""
        if self._proc is not None and self._proc.poll() is None:
            try:
                self._proc.terminate()
            except OSError:
                pass


def _fmt_size(size: int) -> str:
    if size >= 1024 * 1024:
        return f"{size / (1024 * 1024):.1f} MB"
    if size >= 1024:
        return f"{size / 1024:.1f} KB"
    return f"{size} B"


class ExperimentalPage(QWidget):
    def __init__(
        self,
        base_dir: Path,
        paths: Paths,
        settings: Settings,
        server_process: ServerProcess,
    ):
        super().__init__()
        self.base_dir = base_dir
        self.paths = paths
        self.settings = settings
        self.server_process = server_process

        self._job: _ImportJob | None = None

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

        title = QLabel("实验性功能")
        title.setObjectName("PageTitle")
        layout.addWidget(title)

        note = QLabel(
            "这里的功能来自 hoshimi 的实验性子命令，协议或官服接口更新后可能失效，"
            "使用前请先阅读对应说明。"
        )
        note.setObjectName("MutedText")
        note.setWordWrap(True)
        layout.addWidget(note)

        layout.addWidget(self._build_import_card())
        layout.addWidget(self._build_snapshot_card())
        layout.addStretch(1)

        self.server_process.state_changed.connect(lambda _state: self.refresh())
        self.refresh()

    # ---------- 卡 1：官服数据导入 ----------

    def _build_import_card(self) -> ExpandCard:
        card = ExpandCard(FIF.CLOUD, "官服数据导入", "实验功能 · 需官服登录缓存或快照")
        self.import_card = card

        body = QWidget()
        body.setStyleSheet("background: transparent;")
        layout = QVBoxLayout(body)
        layout.setContentsMargins(48, 4, 24, 16)
        layout.setSpacing(8)

        self.precheck_label = QLabel("")
        self.precheck_label.setStyleSheet(
            "color: #374151; background: transparent; font-weight: 600;"
        )
        self.precheck_label.setWordWrap(True)
        layout.addWidget(self.precheck_label)

        explain = QLabel(
            "在线导入复用本机官服客户端的登录缓存（需先用官方客户端登录该账号并退出"
            "游戏）；离线导入从已有快照重建，要求 runtime 与快照匹配。"
            "导入会创建全新的本地账号，不覆盖已有账号或存档，也不会写回官服。"
            "多个区服都有角色时，先不填区服 ID 跑一次，输出里会列出可用 ID。"
        )
        explain.setStyleSheet("color: #9ca3af; background: transparent;")
        explain.setWordWrap(True)
        layout.addWidget(explain)

        form_row = QHBoxLayout()
        form_row.setSpacing(8)
        self.account_edit = QLineEdit()
        self.account_edit.setPlaceholderText("新本地账号名（1-64 个字符）")
        self.account_edit.textEdited.connect(self._validate_account_input)
        form_row.addWidget(self.account_edit, 2)

        self.server_edit = QLineEdit()
        self.server_edit.setPlaceholderText("区服 ID（可选，如 1）")
        form_row.addWidget(self.server_edit, 1)
        layout.addLayout(form_row)

        offline_row = QHBoxLayout()
        offline_row.setSpacing(8)
        self.archive_check = CheckBox("从快照离线导入（无需官服登录缓存）")
        self.archive_check.toggled.connect(self._on_archive_toggled)
        offline_row.addWidget(self.archive_check)
        self.archive_combo = ComboBox()
        self.archive_combo.setMinimumWidth(260)
        offline_row.addWidget(self.archive_combo, 1)
        self.archive_refresh_btn = QPushButton()
        self.archive_refresh_btn.setIcon(FIF.SYNC.icon())
        self.archive_refresh_btn.setFixedWidth(34)
        self.archive_refresh_btn.setToolTip("重新扫描快照")
        self.archive_refresh_btn.clicked.connect(lambda: self.refresh_snapshots())
        offline_row.addWidget(self.archive_refresh_btn)
        layout.addLayout(offline_row)

        self.import_log = QPlainTextEdit()
        self.import_log.setReadOnly(True)
        font = QFont("Consolas")
        font.setStyleHint(QFont.StyleHint.Monospace)
        font.setPointSize(9)
        self.import_log.setFont(font)
        self.import_log.setPlaceholderText(
            "导入输出会显示在这里。导入可能持续数分钟，请不要中途关闭程序。"
        )
        self.import_log.setMinimumHeight(180)
        layout.addWidget(self.import_log)

        action_row = QHBoxLayout()
        action_row.addStretch(1)
        self.snapshot_dir_btn = QPushButton("打开快照目录")
        self.snapshot_dir_btn.clicked.connect(self._open_snapshot_dir)
        action_row.addWidget(self.snapshot_dir_btn)
        self.import_btn = PrimaryPushButton("开始导入")
        self.import_btn.clicked.connect(self._on_import_clicked)
        action_row.addWidget(self.import_btn)
        layout.addLayout(action_row)

        card.addGroupWidget(body)
        return card

    # ---------- 卡 2：快照库 ----------

    def _build_snapshot_card(self) -> ExpandCard:
        card = ExpandCard(FIF.DOCUMENT, "快照库", "官服原始数据留档，可离线重导")
        self.snapshot_card = card

        body = QWidget()
        body.setStyleSheet("background: transparent;")
        layout = QVBoxLayout(body)
        layout.setContentsMargins(48, 4, 24, 16)
        layout.setSpacing(8)

        self.snapshot_list = QListWidget()
        self.snapshot_list.setMinimumHeight(120)
        self.snapshot_list.itemDoubleClicked.connect(self._on_snapshot_double_clicked)
        layout.addWidget(self.snapshot_list)

        hint = QLabel(
            "快照含个人游戏数据，请随 data 目录一起备份，不要公开上传。"
            "hoshimi 增加新适配后，可用同一份快照换新的账号名重新导入。"
            "双击快照可回填到上方的离线导入。"
        )
        hint.setStyleSheet("color: #9ca3af; background: transparent;")
        hint.setWordWrap(True)
        layout.addWidget(hint)

        action_row = QHBoxLayout()
        action_row.addStretch(1)
        refresh_btn = QPushButton("刷新")
        refresh_btn.clicked.connect(lambda: self.refresh_snapshots(True))
        action_row.addWidget(refresh_btn)
        layout.addLayout(action_row)

        card.addGroupWidget(body)
        return card

    # ---------- 状态刷新 ----------

    def refresh(self) -> None:
        """刷新前置检查与按钮状态（页面进入、服务状态变化时调用）。"""
        _exe, version, _auto = find_hoshimi_exe(self.paths, self.settings)
        stopped = self.server_process.state == ServerState.STOPPED
        supported = official_import.supports_import(version)
        mode = read_client_mode(self.paths.active_client_dir(self.settings))

        checks: list[tuple[bool, str]] = [
            (stopped, "服务已停止" if stopped else "服务运行中（必须先停止服务）"),
            (
                supported,
                f"hoshimi 版本 {version or '未知'}"
                if supported
                else "当前版本不支持 import-official（需 0.0.8+）",
            ),
            (
                mode == ClientMode.OFFICIAL,
                (
                    "客户端处于官服"
                    if mode == ClientMode.OFFICIAL
                    else "客户端不在官服状态（在线导入前需切回官服并登录；离线导入不受影响）"
                ),
            ),
        ]
        parts = []
        for ok, text in checks:
            mark = "✅" if ok else ("⚠️" if text.startswith("客户端") else "❌")
            parts.append(f"{mark} {text}")
        self.precheck_label.setText("\n".join(parts))

        self.refresh_snapshots()

        ready = stopped and supported
        self.import_btn.setEnabled(ready and self._job is None)
        self.import_btn.setToolTip(
            "" if ready else "请先停止服务（且 hoshimi ≥ 0.0.8）"
        )

    def refresh_snapshots(self, notify_empty: bool = False) -> None:
        snapshots = official_import.list_snapshots(self.paths, self.settings)

        # 保留当前选中项
        selected = self.archive_combo.currentText()
        self.archive_combo.clear()
        for snap in snapshots:
            self.archive_combo.addItem(snap.path.name, snap.path)
        if selected:
            index = self.archive_combo.findText(selected)
            if index >= 0:
                self.archive_combo.setCurrentIndex(index)

        self.snapshot_list.clear()
        for snap in snapshots:
            item = QListWidgetItem(
                f"{snap.path.name}　{_fmt_size(snap.size)}　"
                f"{snap.modified.strftime('%Y-%m-%d %H:%M')}"
            )
            item.setData(Qt.ItemDataRole.UserRole, str(snap.path))
            self.snapshot_list.addItem(item)

        offline_available = bool(snapshots)
        self.archive_check.setEnabled(offline_available)
        self.archive_combo.setEnabled(
            offline_available and self.archive_check.isChecked()
        )
        self.archive_refresh_btn.setEnabled(offline_available)
        if notify_empty and not offline_available:
            show_toast(
                self, "还没有快照：在线导入成功后会自动保存到 data/official-snapshots"
            )

    # ---------- 交互 ----------

    def _validate_account_input(self, text: str) -> None:
        error = official_import.validate_account_name(text)
        if error is not None and error != "请输入新的本地账号名":
            self.account_edit.setToolTip(error)
        else:
            self.account_edit.setToolTip("")

    def _on_archive_toggled(self, checked: bool) -> None:
        self.archive_combo.setEnabled(checked and self.archive_combo.count() > 0)

    def _on_snapshot_double_clicked(self, item: QListWidgetItem) -> None:
        self.archive_check.setChecked(True)
        index = self.archive_combo.findText(item.text().split("　")[0])
        if index >= 0:
            self.archive_combo.setCurrentIndex(index)
        self.import_card.setExpand(True)
        show_toast(self, "已选择该快照，填写新账号名后即可离线导入")

    def _open_snapshot_dir(self) -> None:
        path = official_import.snapshots_dir(self.paths, self.settings)
        if not path.is_dir():
            show_toast(
                self,
                "快照目录还不存在：在线导入成功一次后会自动创建",
                level="warning",
            )
            return
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(path)))

    def _on_import_clicked(self) -> None:
        account = self.account_edit.text()
        error = official_import.validate_account_name(account)
        if error is not None:
            warning(self, "账号名不合法", error)
            return

        archive: Path | None = None
        if self.archive_check.isChecked():
            data = self.archive_combo.currentData()
            if data is None:
                warning(self, "未选择快照", "请先在下拉框中选择一份快照文件。")
                return
            archive = Path(data)

        mode_text = "快照离线导入" if archive else "在线导入"
        ok = question(
            self,
            f"开始{mode_text}",
            "将执行 hoshimi import-official：\n"
            f"　新本地账号：{account.strip()}\n"
            + (f"　快照：{archive.name}\n" if archive else "")
            + (
                f"　区服 ID：{self.server_edit.text().strip()}\n"
                if self.server_edit.text().strip()
                else ""
            )
            + "\n导入会创建全新账号，不覆盖已有账号或存档。\n\n确定开始吗？",
            yes_text="开始导入",
        )
        if not ok:
            return

        try:
            cmd, cwd = official_import.build_command(
                self.paths,
                self.settings,
                account,
                official_server=self.server_edit.text(),
                archive=archive,
            )
        except official_import.ImportError_ as e:
            warning(self, "无法开始导入", str(e))
            return

        self.import_log.clear()
        self.import_log.appendPlainText(f"> {' '.join(cmd[1:])}")
        self.import_btn.setEnabled(False)
        self._job = _ImportJob(cmd, cwd, self)
        self._job.line.connect(self.import_log.appendPlainText)
        self._job.spawn_error.connect(self._on_spawn_error)
        self._job.finished_run.connect(self._on_import_finished)
        self._job.start()

    def _on_spawn_error(self, message: str) -> None:
        self.import_log.appendPlainText(message)
        self._finish_import(False, message)

    def _on_import_finished(self, code: int) -> None:
        ok = code == 0
        summary = (
            "导入完成。用新的本地账号登录即可设置密码并游玩。"
            if ok
            else f"导入失败（退出码 {code}）。"
            "如提示多个区服，请把输出中的区服 ID 填入后重试；"
            "如提示缓存失效，请先用官方客户端登录该账号。"
        )
        self.import_log.appendPlainText(f"--- {'导入完成' if ok else '导入失败'} ---")
        self.import_log.appendPlainText(summary)
        if ok:
            show_toast(self, "官服导入完成，快照已保存")
            self.refresh_snapshots()
        self._finish_import(ok, summary)

    def _finish_import(self, ok: bool, message: str) -> None:
        self._job = None
        self.refresh()
        if not ok:
            warning(self, "导入未完成", message)

    def closeEvent(self, event) -> None:
        if self._job is not None:
            self._job.stop()
            self._job.wait(1000)
        super().closeEvent(event)

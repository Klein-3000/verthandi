"""hoshimi import-official（官服存档导入，实验功能）的封装。

只负责「准备与检查」，子进程流式执行在页面里完成（需要逐行吐日志）：

- 前置检查：版本闸门（0.0.8 起才有 import-official）、账号名校验、
  服务必须停止（由调用方判断）、客户端须处于官服（建议项，不强制）；
- 快照清单：home/data/official-snapshots/ 下的 *.json，按修改时间倒序；
- 命令构造：与 serve 相同的全局参数约定（全局参数必须放在子命令之后），
  --home/--runtime-dir 与服务启动保持一致，保证协议身份匹配。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from app.core.hoshimi_finder import find_hoshimi_exe
from app.core.paths import Paths
from app.core.runtime_layout import resolve_runtime
from app.core.settings import Settings

MIN_VERSION = (0, 0, 8)
SNAPSHOTS_SUBDIR = Path("data") / "official-snapshots"


class ImportError_(Exception):
    """构造命令或校验参数失败（区别于 hoshimi 运行失败）。"""


@dataclass(frozen=True)
class SnapshotInfo:
    """一份官服原始快照文件。"""

    path: Path
    size: int
    modified: datetime


def snapshots_dir(paths: Paths, settings: Settings) -> Path:
    return paths.home_dir(settings) / SNAPSHOTS_SUBDIR


def list_snapshots(paths: Paths, settings: Settings) -> list[SnapshotInfo]:
    """快照按修改时间倒序；目录不存在时返回空列表。"""
    root = snapshots_dir(paths, settings)
    if not root.is_dir():
        return []
    items: list[SnapshotInfo] = []
    for path in root.glob("*.json"):
        try:
            stat = path.stat()
        except OSError:
            continue
        items.append(
            SnapshotInfo(
                path=path,
                size=stat.st_size,
                modified=datetime.fromtimestamp(stat.st_mtime),
            )
        )
    items.sort(key=lambda item: item.modified, reverse=True)
    return items


def supports_import(version: str | None) -> bool:
    """import-official 自 hoshimi 0.0.8 起可用（0.0.3 二进制中无此子命令）。"""
    if not version:
        return False
    try:
        parts = tuple(int(p) for p in version.split(".")[:3])
    except (TypeError, ValueError):
        return False
    if len(parts) < 3:
        return False
    return parts >= MIN_VERSION


def validate_account_name(name: str) -> str | None:
    """返回错误消息；None 表示合法（hoshimi 要求 1-64 个字符）。"""
    trimmed = name.strip()
    if not trimmed:
        return "请输入新的本地账号名"
    if len(trimmed) > 64:
        return "账号名最长 64 个字符"
    if trimmed != name:
        return "账号名首尾不要留空格"
    return None


def build_command(
    paths: Paths,
    settings: Settings,
    account: str,
    *,
    official_server: str = "",
    archive: Path | None = None,
) -> tuple[list[str], Path]:
    """构造 import-official 命令行，返回 (命令, 工作目录)。

    --home / --runtime-dir 与服务启动保持一致：离线导入要求运行文件的
    应用版本、协议哈希及资源清单哈希与快照匹配，必须指向同一份 runtime。
    """
    exe, _version, _auto = find_hoshimi_exe(paths, settings)
    if not exe.exists():
        raise ImportError_(f"找不到 hoshimi.exe：{exe}")

    name_error = validate_account_name(account)
    if name_error is not None:
        raise ImportError_(name_error)

    cmd: list[str] = [str(exe), "import-official", "--account", account]
    if official_server.strip():
        cmd += ["--official-server", official_server.strip()]
    if archive is not None:
        if not archive.is_file():
            raise ImportError_(f"快照文件不存在：{archive}")
        cmd += ["--archive", str(archive)]

    home = paths.home_dir(settings)
    runtime = resolve_runtime(paths, settings).directory
    cmd += ["--home", str(home), "--runtime-dir", str(runtime)]
    return cmd, exe.parent

from __future__ import annotations

import shutil
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path

from app.core.hoshimi_finder import find_hoshimi_exe, version_key
from app.core.paths import Paths
from app.core.runtime_layout import (
    MANIFEST_NAME,
    PREV_PREFIX,
    RUNTIME_SUBDIR,
    STAGING_PREFIX,
    dir_size,
    read_manifest_file,
    resolve_runtime,
    runtime_root,
    validate_runtime,
)
from app.core.settings import Settings


@dataclass
class VersionInfo:
    name: str
    path: Path
    size_bytes: int
    is_current: bool


@dataclass
class RuntimeEntry:
    """runtimes/ 下的一个条目：按版本目录、临时目录，或 backup/ 里的旧备份。"""

    kind: str  # runtime / staging / legacy
    label: str
    path: Path
    size_bytes: int
    is_current: bool
    complete: bool
    detail: str = ""
    deletable: bool = True


@dataclass
class LogInfo:
    count: int
    total_size_bytes: int


def format_size(size_bytes: int) -> str:
    """把字节数转换为人类可读的字符串。"""
    if size_bytes < 1024:
        return f"{size_bytes} B"
    if size_bytes < 1024 * 1024:
        return f"{size_bytes / 1024:.1f} KB"
    if size_bytes < 1024 * 1024 * 1024:
        return f"{size_bytes / (1024 * 1024):.1f} MB"
    return f"{size_bytes / (1024 * 1024 * 1024):.2f} GB"


def get_dir_size(path: Path) -> int:
    """计算目录的总大小（仅计算文件）。"""
    return dir_size(path)


# ---------- 扫描 ----------


def scan_versions(paths: Paths, settings: Settings) -> list[VersionInfo]:
    bin_dir = paths.bin_dir(settings)
    if not bin_dir.exists():
        return []

    # 自动选择模式下 server.version 为空，用解析结果判定"当前"，否则会误删在用的版本
    _exe, resolved, _auto = find_hoshimi_exe(paths, settings)
    current_version = settings.server.version or resolved
    results = []

    for d in bin_dir.iterdir():
        if not d.is_dir() or d.name.startswith("import-"):
            continue
        if not (d / "hoshimi.exe").exists():
            continue

        is_current = d.name == current_version
        size = get_dir_size(d)
        results.append(VersionInfo(d.name, d, size, is_current))

    # 按版本降序排列，当前版本置顶
    results.sort(key=lambda x: (not x.is_current, version_key(x.name)), reverse=True)
    return results


def scan_runtimes(paths: Paths, settings: Settings) -> list[RuntimeEntry]:
    """列出所有 runtime 条目：按版本目录、崩溃残留的临时目录、旧版备份。"""
    ref = resolve_runtime(paths, settings)
    entries: list[RuntimeEntry] = []

    root = runtime_root(paths, settings)
    if root.is_dir():
        for d in root.iterdir():
            if not d.is_dir() or d.name.startswith(PREV_PREFIX):
                continue
            if d.name.startswith(STAGING_PREFIX):
                entries.append(
                    RuntimeEntry(
                        kind="staging",
                        label=d.name,
                        path=d,
                        size_bytes=get_dir_size(d),
                        is_current=False,
                        complete=False,
                        detail="未完成的临时目录",
                    )
                )
                continue

            runtime = d / RUNTIME_SUBDIR
            manifest = read_manifest_file(d / MANIFEST_NAME)
            complete, missing = validate_runtime(runtime)
            bits: list[str] = []
            if manifest is not None and manifest.prepared_at:
                bits.append(f"准备于 {manifest.prepared_label}")
            if missing:
                bits.append("缺 " + "、".join(missing[:3]))
            if not (paths.bin_dir(settings) / d.name / "hoshimi.exe").exists():
                bits.append("无对应 hoshimi")
            entries.append(
                RuntimeEntry(
                    kind="runtime",
                    label=d.name,
                    path=d,
                    size_bytes=get_dir_size(d),
                    is_current=d.name == ref.version,
                    complete=complete,
                    detail=" · ".join(bits),
                    deletable=d.name != ref.version,
                )
            )

    backup_root = root.parent / "backup"
    if backup_root.is_dir():
        for d in backup_root.iterdir():
            if not d.is_dir() or not d.name.startswith("runtime-"):
                continue
            entries.append(
                RuntimeEntry(
                    kind="legacy",
                    label=d.name,
                    path=d,
                    size_bytes=get_dir_size(d),
                    is_current=False,
                    complete=False,
                    detail="旧版备份",
                )
            )

    entries.sort(
        key=lambda e: (e.is_current, e.kind == "legacy", e.label), reverse=True
    )
    return entries


def scan_logs(paths: Paths, settings: Settings) -> LogInfo:
    """统计日志：应用会话日志（session-*.log）+ hoshimi 服务日志
    （logs/hoshimi/<version>/*.log）。"""
    logs_dir = paths.logs_dir(settings)
    count = 0
    total_size = 0

    def _add_file(f: Path) -> None:
        nonlocal count, total_size
        if f.is_file():
            count += 1
            try:
                total_size += f.stat().st_size
            except OSError:
                pass

    if logs_dir.exists():
        for f in logs_dir.glob("session-*.log"):
            _add_file(f)
        hoshimi_dir = logs_dir / "hoshimi"
        if hoshimi_dir.is_dir():
            for f in hoshimi_dir.glob("*/*.log"):
                _add_file(f)

    return LogInfo(count, total_size)


# ---------- 删除 ----------


def delete_versions(
    items: list[VersionInfo],
    paths: Paths | None = None,
    settings: Settings | None = None,
) -> None:
    """删除 hoshimi 版本。给了 paths/settings 时连带删除同名 runtime，避免留下孤儿。"""
    for item in items:
        if item.is_current:
            continue
        shutil.rmtree(item.path, ignore_errors=True)
        if paths is not None and settings is not None:
            shutil.rmtree(runtime_root(paths, settings) / item.name, ignore_errors=True)


def delete_runtimes(items: list[RuntimeEntry]) -> None:
    for item in items:
        if item.is_current or not item.deletable:
            continue
        shutil.rmtree(item.path, ignore_errors=True)


def delete_logs(
    paths: Paths,
    settings: Settings,
    days: int,
    active_log_path: Path | None,
) -> None:
    """days=0 表示删除所有历史日志。"""
    logs_dir = paths.logs_dir(settings)
    if not logs_dir.exists():
        return

    cutoff = datetime.now() - timedelta(days=days) if days > 0 else None

    for f in logs_dir.glob("session-*.log"):
        if not f.is_file():
            continue
        # 保护当前正在写入的日志
        if active_log_path and f.resolve() == active_log_path.resolve():
            continue

        if cutoff:
            try:
                mtime = datetime.fromtimestamp(f.stat().st_mtime)
                if mtime > cutoff:
                    continue
            except OSError:
                continue

        try:
            f.unlink()
        except OSError:
            pass

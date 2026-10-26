from __future__ import annotations

import os
import shutil
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path

from app.core.hoshimi_finder import version_key
from app.core.paths import Paths
from app.core.settings import Settings


@dataclass
class VersionInfo:
    name: str
    path: Path
    size_bytes: int
    is_current: bool


@dataclass
class BackupInfo:
    name: str
    path: Path
    size_bytes: int


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
    total = 0
    if not path.exists():
        return 0
    for root, _dirs, files in os.walk(path):
        for f in files:
            try:
                total += os.path.getsize(os.path.join(root, f))
            except OSError:
                pass
    return total


# ---------- 扫描 ----------


def scan_versions(paths: Paths, settings: Settings) -> list[VersionInfo]:
    bin_dir = paths.bin_dir(settings)
    if not bin_dir.exists():
        return []

    current_version = settings.server.version
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


def scan_runtime_backups(paths: Paths, settings: Settings) -> list[BackupInfo]:
    runtime = paths.runtime_dir(settings)
    backup_dir = runtime.parent / "backup"
    if not backup_dir.exists():
        return []

    results = []
    for d in backup_dir.iterdir():
        if not d.is_dir() or not d.name.startswith("runtime-"):
            continue
        size = get_dir_size(d)
        results.append(BackupInfo(d.name, d, size))

    results.sort(key=lambda x: x.name, reverse=True)
    return results


def scan_logs(paths: Paths, settings: Settings) -> LogInfo:
    logs_dir = paths.logs_dir(settings)
    if not logs_dir.exists():
        return LogInfo(0, 0)

    count = 0
    total_size = 0
    for f in logs_dir.glob("session-*.log"):
        if f.is_file():
            count += 1
            try:
                total_size += f.stat().st_size
            except OSError:
                pass
    return LogInfo(count, total_size)


# ---------- 删除 ----------


def delete_versions(items: list[VersionInfo]) -> None:
    for item in items:
        if item.is_current:
            continue
        shutil.rmtree(item.path, ignore_errors=True)


def delete_backups(items: list[BackupInfo]) -> None:
    for item in items:
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

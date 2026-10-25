from __future__ import annotations

from pathlib import Path

from app.core.paths import Paths
from app.core.settings import Settings


def version_key(name: str) -> tuple:
    """把版本目录名转成可排序的 key。

    优先按整数段比较：0.0.10 > 0.0.9 > 0.0.3 > 0.0.2。
    非数字段退化为字符串比较，避免解析失败导致排序崩溃。
    """
    parts: list[tuple[int, object]] = []
    for seg in name.split('.'):
        if seg.isdigit():
            parts.append((0, int(seg)))
        else:
            parts.append((1, seg))
    return tuple(parts)


def find_hoshimi_exe(paths: Paths, settings: Settings) -> tuple[Path, str, bool]:
    """定位 hoshimi.exe。

    返回 (exe_path, 实际版本名, 是否自动选择)。

    - settings.server.version 非空：直接使用指定版本，第三项为 False；
    - 否则扫描 bin/ 下所有含 hoshimi.exe 的子目录，按版本降序取第一个；
    - 都没有时返回 bin/ 根下的占位路径，版本名空串，第三项 False。
    """
    bin_dir = paths.bin_dir(settings)
    version = settings.server.version

    if version:
        return bin_dir / version / 'hoshimi.exe', version, False

    if bin_dir.exists():
        candidates = sorted(
            (d for d in bin_dir.iterdir()
             if d.is_dir() and (d / 'hoshimi.exe').exists()),
            key=lambda d: version_key(d.name),
            reverse=True,
        )
        if candidates:
            chosen = candidates[0]
            return chosen / 'hoshimi.exe', chosen.name, True

    return bin_dir / 'hoshimi.exe', '', False

"""hoshimi 启动参数的统一来源（日志参数）。

实测约束（hoshimi 0.0.8）：
- 全局参数必须放在子命令之后：`hoshimi serve --no-log` 合法，
  `hoshimi --no-log serve` 报「参数位置有误」；
- `version` 子命令也会创建日志目录，因此探测路径恒定带 --no-log
  （由 hoshimi_probe 负责）；
- `--log-dir` 指向不存在的目录时 hoshimi 会自行 mkdir，这里仍预创建，
  把权限问题提前暴露。

日志策略：
- behavior.hoshimi_log 开 → `--log-dir <logs>/hoshimi/<version>/`
  （必须绝对路径：spawn 的 cwd 在 exe 目录，相对路径会被解析回 bin/ 下）；
- 关（默认）→ `--no-log`；
- 旧版（0.0.5 及更早）不认识这些参数，传了会报错退出——首次使用前用
  `version --no-log` 探测能力，结果按 exe 的 size+mtime 缓存，exe 被替换
  后自动失效重测。不支持的版本返回空参数列表，保持旧行为。
"""

from __future__ import annotations

import json
from pathlib import Path

from app.core.hoshimi_probe import run_version
from app.core.paths import Paths
from app.core.settings import Settings

CAP_CACHE_NAME = ".hoshimi-capabilities.json"
UNKNOWN_VERSION = "unknown"


def hoshimi_log_dir(paths: Paths, settings: Settings, version: str) -> Path:
    """hoshimi 服务日志目录：logs/hoshimi/<version>/，与 runtimes/ 同构。"""
    return paths.resolve(settings.paths.logs_dir) / "hoshimi" / (
        version or UNKNOWN_VERSION
    )


# ---------- 能力探测缓存 ----------


def _cache_path(paths: Paths) -> Path:
    return paths.base_dir / CAP_CACHE_NAME


def _cache_get(paths: Paths, exe: Path) -> bool | None:
    try:
        data = json.loads(_cache_path(paths).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    entry = data.get(str(exe)) if isinstance(data, dict) else None
    if not isinstance(entry, dict):
        return None
    try:
        stat = exe.stat()
    except OSError:
        return None
    if entry.get("size") != stat.st_size or entry.get("mtime") != stat.st_mtime:
        return None
    return bool(entry.get("supported"))


def _cache_put(paths: Paths, exe: Path, supported: bool) -> None:
    try:
        stat = exe.stat()
    except OSError:
        return
    try:
        raw = _cache_path(paths).read_text(encoding="utf-8")
        data = json.loads(raw)
        if not isinstance(data, dict):
            data = {}
    except (OSError, ValueError):
        data = {}
    data[str(exe)] = {
        "size": stat.st_size,
        "mtime": stat.st_mtime,
        "supported": supported,
    }
    tmp = _cache_path(paths).with_name(CAP_CACHE_NAME + ".tmp")
    try:
        tmp.write_text(
            json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        tmp.replace(_cache_path(paths))
    except OSError:
        pass


def supports_log_flags(paths: Paths, exe: Path) -> bool:
    """该 hoshimi.exe 是否支持 --no-log / --log-dir（0.0.6+）。"""
    cached = _cache_get(paths, exe)
    if cached is not None:
        return cached
    output = run_version(exe, ["--no-log"], timeout=5.0)
    supported = "当前版本" in output
    _cache_put(paths, exe, supported)
    return supported


# ---------- 参数工厂 ----------


def hoshimi_log_args(
    paths: Paths,
    settings: Settings,
    exe: Path,
    version: str,
    log_bus=None,
) -> list[str]:
    """产出日志参数列表，供 prepare / serve 在子命令后展开。

    log_bus 用于在目录创建失败降级时给出提示；传入方需支持 emit_line。
    """
    if not supports_log_flags(paths, exe):
        if log_bus is not None:
            log_bus.emit_line(
                "app",
                f"该版本 hoshimi 不支持 --no-log/--log-dir，日志行为保持默认（{exe.parent.name}）",
                "warning",
            )
        return []

    if settings.behavior.hoshimi_log:
        log_dir = hoshimi_log_dir(paths, settings, version)
        try:
            log_dir.mkdir(parents=True, exist_ok=True)
        except OSError as e:
            if log_bus is not None:
                log_bus.emit_line(
                    "app",
                    f"无法创建日志目录 {log_dir}（{e}），本次启动不写日志文件",
                    "warning",
                )
            return ["--no-log"]
        return ["--log-dir", str(log_dir)]

    return ["--no-log"]

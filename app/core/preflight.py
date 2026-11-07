from __future__ import annotations

import json
from dataclasses import dataclass
from enum import Enum
from pathlib import Path

from app.core.client_state import client_fingerprint
from app.core.game_process import is_game_running
from app.core.hoshimi_finder import find_hoshimi_exe  # 新增
from app.core.paths import Paths
from app.core.port_check import check_port
from app.core.runtime_layout import (
    RUNTIME_REQUIRED_DIRS,
    RUNTIME_REQUIRED_FILES,
    read_manifest,
    resolve_runtime,
    validate_runtime,
)
from app.core.settings import Settings

__all__ = [
    "RUNTIME_REQUIRED_DIRS",
    "RUNTIME_REQUIRED_FILES",
    "CheckItem",
    "Severity",
    "run_preflight",
]


class Severity(str, Enum):
    ERROR = "error"
    WARNING = "warning"


@dataclass
class CheckItem:
    id: str
    label: str
    ok: bool
    severity: Severity
    message: str = ""
    fix_hint: str = ""


def run_preflight(paths: Paths, settings: Settings) -> list[CheckItem]:
    items: list[CheckItem] = []
    items.append(_check_client_exe(settings))
    items.append(_check_hoshimi_exe(paths, settings))
    items.append(_check_runtime(paths, settings))
    items.append(_check_home_config(paths, settings))
    items.append(_check_config_client_dir(paths, settings))
    items.extend(_check_ports(settings))
    items.append(_check_game_running(paths, settings))
    items.extend(_check_prepare_freshness(paths, settings))
    return items


# ---------- 客户端 / hoshimi / runtime ----------


def _check_client_exe(settings: Settings) -> CheckItem:
    entry = settings.active_client()
    if entry is None or not entry.exe_path:
        return CheckItem(
            "client-exe",
            "游戏目录",
            False,
            Severity.ERROR,
            "尚未选择游戏目录",
            "到设置页选择 AetherGazer.exe",
        )
    exe = Path(entry.exe_path)
    if not exe.exists():
        return CheckItem(
            "client-exe",
            "游戏目录",
            False,
            Severity.ERROR,
            f"AetherGazer.exe 不存在：{exe}",
            "到设置页重新选择",
        )
    return CheckItem("client-exe", "游戏目录", True, Severity.ERROR)


def _check_hoshimi_exe(paths: Paths, settings: Settings) -> CheckItem:
    exe, _version, _auto = find_hoshimi_exe(paths, settings)
    if not exe.exists():
        return CheckItem(
            "hoshimi-exe",
            "hoshimi 程序",
            False,
            Severity.ERROR,
            f"找不到 hoshimi.exe：{exe}",
            "把 hoshimi 解压到 bin/<版本>/",
        )
    return CheckItem("hoshimi-exe", "hoshimi 程序", True, Severity.ERROR)


def _check_runtime(paths: Paths, settings: Settings) -> CheckItem:
    ref = resolve_runtime(paths, settings)
    runtime = ref.directory
    if not runtime.exists():
        return CheckItem(
            "runtime",
            "运行文件",
            False,
            Severity.ERROR,
            f"版本 {ref.version} 尚未初始化：{runtime}",
            "点击「初始化」",
        )
    complete, missing = validate_runtime(runtime)
    if not complete:
        return CheckItem(
            "runtime",
            "运行文件",
            False,
            Severity.ERROR,
            f"runtime 不完整，缺少：{', '.join(missing)}",
            "点击「初始化」重新生成",
        )
    return CheckItem("runtime", "运行文件", True, Severity.ERROR)


def _check_home_config(paths: Paths, settings: Settings) -> CheckItem:
    cfg_path = paths.home_dir(settings) / "config.json"
    if not cfg_path.exists():
        return CheckItem(
            "home-config",
            "配置文件",
            False,
            Severity.ERROR,
            f"缺少 home/config.json：{cfg_path}",
            "点击「初始化」",
        )
    try:
        json.loads(cfg_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        return CheckItem(
            "home-config",
            "配置文件",
            False,
            Severity.ERROR,
            f"config.json 无法解析：{e}",
            "点击「初始化」重新生成",
        )
    return CheckItem("home-config", "配置文件", True, Severity.ERROR)


def _check_config_client_dir(paths: Paths, settings: Settings) -> CheckItem:
    cfg_path = paths.home_dir(settings) / "config.json"
    if not cfg_path.exists():
        return CheckItem("config-client-dir", "配置客户端路径", True, Severity.WARNING)
    try:
        data = json.loads(cfg_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return CheckItem("config-client-dir", "配置客户端路径", True, Severity.WARNING)
    cfg_client = (data.get("client_dir") or "") if isinstance(data, dict) else ""
    active = paths.active_client_dir(settings)
    if not cfg_client or active is None:
        return CheckItem("config-client-dir", "配置客户端路径", True, Severity.WARNING)
    try:
        same = Path(cfg_client).resolve() == Path(active).resolve()
    except OSError:
        same = False
    if not same:
        return CheckItem(
            "config-client-dir",
            "配置客户端路径",
            False,
            Severity.WARNING,
            f"config.json 记录 {cfg_client}，当前设置为 {active}",
            "建议点击「初始化」更新配置",
        )
    return CheckItem("config-client-dir", "配置客户端路径", True, Severity.WARNING)


# ---------- 端口 / 进程 / 准备状态 ----------


def _check_ports(settings: Settings) -> list[CheckItem]:
    items: list[CheckItem] = []
    for key, label, port in (
        ("http-port", "HTTP 端口", settings.server.http_port),
        ("game-port", "游戏端口", settings.server.game_port),
    ):
        r = check_port(port)
        if r.is_free:
            items.append(CheckItem(key, label, True, Severity.ERROR))
        else:
            items.append(
                CheckItem(
                    key,
                    label,
                    False,
                    Severity.ERROR,
                    f"端口 {port} {r.summary()}",
                    "关闭占用进程，或在设置页修改端口",
                )
            )
    return items


def _check_game_running(paths: Paths, settings: Settings) -> CheckItem:
    if is_game_running(paths.active_client_dir(settings)):
        return CheckItem(
            "game-running",
            "游戏进程",
            False,
            Severity.WARNING,
            "游戏正在运行，重启服务可能导致断连",
            "建议先退出游戏",
        )
    return CheckItem("game-running", "游戏进程", True, Severity.WARNING)


def _check_prepare_freshness(paths: Paths, settings: Settings) -> list[CheckItem]:
    items: list[CheckItem] = []
    ref = resolve_runtime(paths, settings)
    manifest = read_manifest(paths, settings, ref)

    if manifest is None:
        items.append(
            CheckItem(
                "prepare-unknown",
                "初始化记录",
                False,
                Severity.WARNING,
                "runtime 缺少初始化记录（旧数据或由其他工具生成）",
                "如果启动或游戏出现问题，请重新初始化",
            )
        )
    elif manifest.version and ref.version != manifest.version:
        # 按版本分目录时天然一致，只有自定义目录模式才会走到这里
        items.append(
            CheckItem(
                "prepare-version",
                "运行文件版本",
                False,
                Severity.WARNING,
                f"runtime 由版本 {manifest.version} 准备，当前 hoshimi 为 {ref.version}",
                "点击「初始化」重新 prepare",
            )
        )
    else:
        items.append(
            CheckItem("prepare-version", "运行文件版本", True, Severity.WARNING)
        )

    stored = manifest.client_fingerprint if manifest else ""
    if stored:
        fp = client_fingerprint(paths.active_client_dir(settings))
        if fp and fp != stored:
            items.append(
                CheckItem(
                    "prepare-fingerprint",
                    "客户端指纹",
                    False,
                    Severity.WARNING,
                    "客户端可能已更新",
                    "点击「初始化」重新 prepare",
                )
            )
            return items
    items.append(CheckItem("prepare-fingerprint", "客户端指纹", True, Severity.WARNING))
    return items

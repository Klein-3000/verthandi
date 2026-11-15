"""hoshimi 使用统计（telemetry）开关的读写。

为什么需要这一层：hoshimi 0.0.8 起在首次启动时会询问是否启用使用统计，
提示以 `[y/N]：` 结尾且**没有换行**，并且会一直阻塞在 stdin 上等待作答。
Verthandi 隐藏了控制台，只能代它回答；而"代答"的可靠方式不是往 stdin 写字符，
而是**在启动前把答案写进 home/config.json**。

实测结论（0.0.8 与 0.0.9 行为一致）：

| config.json 内容                      | 是否询问 |
|---------------------------------------|----------|
| 无 telemetry 字段                      | 问       |
| telemetry_enabled = false              | 不问     |
| telemetry_enabled = true + consent     | 不问，直接启用 |
| telemetry_enabled = true，无 consent   | 仍然问   |

hoshimi 自己作答后会写入**两个**键（拒绝时也写 consent），并把它们插在
文件最前面；这里照它的样子写，保证文件形态与它自己产出的一致。
0.0.3 不认识这两个键，写进去无害（Go 的 json 解码会忽略未知字段）。
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from app.core.paths import Paths
from app.core.settings import Settings

__all__ = [
    "CONSENT_VALUE",
    "KEY_CONSENT",
    "KEY_ENABLED",
    "TELEMETRY_MIN_VERSION",
    "config_path",
    "parse_version",
    "read_telemetry",
    "supports_telemetry",
    "write_telemetry",
]

KEY_ENABLED = "telemetry_enabled"
KEY_CONSENT = "telemetry_consent"
CONSENT_VALUE = "umami-hwid-v1"
TELEMETRY_MIN_VERSION = (0, 0, 8)

_VERSION_RE = re.compile(r"(\d+)\.(\d+)\.(\d+)")


def parse_version(text: str) -> tuple[int, int, int] | None:
    """从 "0.0.8" / "Hoshimi-v0.0.8" 之类的字符串里取出版本号。"""
    m = _VERSION_RE.search(text or "")
    if not m:
        return None
    return int(m.group(1)), int(m.group(2)), int(m.group(3))


def supports_telemetry(version: str) -> bool:
    """该版本是否带遥测询问（0.0.8+）。版本未知时按不支持处理。"""
    parsed = parse_version(version)
    return parsed is not None and parsed >= TELEMETRY_MIN_VERSION


def config_path(paths: Paths, settings: Settings) -> Path:
    return paths.home_dir(settings) / "config.json"


def read_telemetry(paths: Paths, settings: Settings) -> bool | None:
    """读当前状态。

    返回 None 表示"没有答复过"（键不存在或文件不可用）——这正是 hoshimi
    会发问、Verthandi 必须代答的条件。
    """
    try:
        data = json.loads(config_path(paths, settings).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict):
        return None
    value = data.get(KEY_ENABLED)
    return value if isinstance(value, bool) else None


def _with_telemetry_keys(data: dict, enabled: bool) -> dict:
    """把两个 telemetry 键放到最前面（与 hoshimi 自己的写入顺序一致）。"""
    out = {KEY_ENABLED: bool(enabled), KEY_CONSENT: CONSENT_VALUE}
    for key, value in data.items():
        if key in out:
            continue
        out[key] = value
    return out


def write_telemetry(
    paths: Paths, settings: Settings, enabled: bool
) -> tuple[bool, str]:
    """把答复写进 home/config.json，返回 (成功, 错误说明)。

    只动这两个键，其余字段原样保留（含顺序），原子写避免半截文件。
    """
    path = config_path(paths, settings)
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError as e:
        return False, f"无法读取 {path}：{e}"

    try:
        data = json.loads(raw)
    except ValueError as e:
        return False, f"{path} 不是合法 JSON：{e}"
    if not isinstance(data, dict):
        return False, f"{path} 的内容不是对象"

    payload = _with_telemetry_keys(data, enabled)
    tmp = path.with_name(path.name + ".tmp")
    try:
        tmp.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        tmp.replace(path)
    except OSError as e:
        return False, f"无法写入 {path}：{e}"
    return True, ""

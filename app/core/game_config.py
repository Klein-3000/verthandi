from __future__ import annotations

import json
import os
from pathlib import Path


def get_config_path() -> Path:
    """获取 Windows 下的 AetherGazer 配置文件路径。"""
    user_home = Path(os.environ.get("USERPROFILE", Path.home()))
    return user_home / "AppData" / "LocalLow" / "yongshi" / "AetherGazer" / "sys_config.ini"


def read_config() -> dict | None:
    """读取配置文件。如果文件不存在或损坏，返回 None。"""
    path = get_config_path()
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def update_localization(is_localization: bool) -> tuple[bool, str]:
    """修改外观配置。返回 (是否成功, 错误信息)。"""
    path = get_config_path()
    if not path.exists():
        return False, "未找到游戏配置文件，可能游戏尚未运行过。请先启动一次游戏，生成配置后再来修改。"

    try:
        # 读取现有内容，保留其他字段
        data = json.loads(path.read_text(encoding="utf-8"))
        data["isLocalization"] = is_localization
        path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        return True, ""
    except PermissionError:
        return False, "写入失败：权限不足。请检查文件是否被其他程序占用。"
    except Exception as e:
        return False, f"写入失败：{e}"

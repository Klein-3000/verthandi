"""战斗操控修复：重置游戏客户端的战斗布局与键鼠模式。

对齐官方 `scripts/repair-battle-controls.ps1` 的行为，用纯标准库实现：

- 数据在 Unity PlayerPrefs 里：注册表 `HKCU\\Software\\yongshi\\AetherGazer`，
  `userSetting_h136201570`（REG_BINARY，UTF-8 JSON + 结尾 NUL）与
  `KeyboardType_h1853033200`（DWORD）；
- 修复前把当前布局与键鼠模式备份到 `home/data/client-settings/`，备份
  JSON 的键名与官方一致（layout / keyboard），官方脚本的 `-Restore`
  能直接消费这里的备份，反之亦然；
- 修复 = 布局类型清空（空布局让 PC 适配器用内置布局）+ alpha 置 1 +
  键鼠模式置 5，其余用户设置原样保留；
- 游戏运行时拒绝读写，避免客户端退出时把注册表覆盖回去。

键名自 0.0.3 起从未变过（三个版本脚本逐字节相同），无需版本分支。
"""

from __future__ import annotations

import json
import winreg
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from app.core.paths import Paths

__all__ = [
    "PREFS_ROOT",
    "BattleResult",
    "BattleStatus",
    "backup_dir",
    "list_backups",
    "repair",
    "restore",
    "status",
]

# 测试时可把整个 tuple 换成临时注册表子树
PREFS_ROOT = (winreg.HKEY_CURRENT_USER, r"Software\yongshi\AetherGazer")
SETTING_KEY = "userSetting_h136201570"
KEYBOARD_KEY = "KeyboardType_h1853033200"
LAYOUT_FIELDS = ("battle_ui_cur_type", "battle_ui_cur_alpha_value")
KEYBOARD_MOUSE = 5  # 官方脚本写死的键鼠模式值
REPAIR_ALPHA = 1


@dataclass(frozen=True)
class BattleStatus:
    """注册表当前状态，UI 只负责展示。"""

    exists: bool  # 注册表键是否存在（没启动过游戏则不存在）
    layout_type: str | None
    alpha: float | None
    keyboard: int | None
    error: str = ""


@dataclass(frozen=True)
class BattleResult:
    ok: bool
    code: str
    message: str


# ---------- 注册表读写 ----------


def _open_prefs(writable: bool = False) -> winreg.HKEYType:
    access = (
        winreg.KEY_SET_VALUE | winreg.KEY_QUERY_VALUE
        if writable
        else winreg.KEY_QUERY_VALUE
    )
    return winreg.OpenKey(PREFS_ROOT[0], PREFS_ROOT[1], 0, access)


def _read_prefs() -> tuple[dict | None, str]:
    """读 userSetting JSON。返回 (内容, 错误说明)。"""
    try:
        with _open_prefs() as key:
            raw, _vtype = winreg.QueryValueEx(key, SETTING_KEY)
    except FileNotFoundError:
        return None, "未找到客户端设置（注册表里没有游戏记录），请先启动过一次游戏。"
    except OSError as e:
        return None, f"读取注册表失败：{e}"
    if raw is None:
        return {}, ""
    if isinstance(raw, bytes):
        raw = raw.decode("utf-8", errors="replace").rstrip("\0")
    if isinstance(raw, str):
        raw = raw.rstrip("\0")
        if not raw:
            return {}, ""
        try:
            data = json.loads(raw)
        except ValueError as e:
            return None, f"userSetting JSON 解析失败：{e}"
        if not isinstance(data, dict):
            return None, "userSetting 的内容不是 JSON 对象"
        return data, ""
    return None, f"userSetting 的注册表类型不受支持（{type(raw).__name__}）"


def _write_prefs(data: dict) -> None:
    blob = (json.dumps(data, ensure_ascii=False, separators=(",", ":")) + "\0").encode(
        "utf-8"
    )
    with winreg.CreateKey(PREFS_ROOT[0], PREFS_ROOT[1]) as key:
        winreg.SetValueEx(key, SETTING_KEY, 0, winreg.REG_BINARY, blob)


def _read_keyboard() -> int | None:
    try:
        with _open_prefs() as key:
            raw, _vtype = winreg.QueryValueEx(key, KEYBOARD_KEY)
    except (FileNotFoundError, OSError):
        return None
    return int(raw) if isinstance(raw, int) else None


def _write_keyboard(value: int) -> None:
    with winreg.CreateKey(PREFS_ROOT[0], PREFS_ROOT[1]) as key:
        winreg.SetValueEx(key, KEYBOARD_KEY, 0, winreg.REG_DWORD, int(value))


def _delete_keyboard() -> None:
    try:
        with _open_prefs(writable=True) as key:
            winreg.DeleteValue(key, KEYBOARD_KEY)
    except FileNotFoundError:
        pass


# ---------- 备份 ----------


def backup_dir(paths: Paths, settings) -> Path:
    return paths.home_dir(settings) / "data" / "client-settings"


def list_backups(paths: Paths, settings) -> list[Path]:
    root = backup_dir(paths, settings)
    if not root.is_dir():
        return []
    files = [p for p in root.glob("battle-controls-*.json") if p.is_file()]
    return sorted(files, key=lambda p: p.stat().st_mtime, reverse=True)


def _write_backup(paths: Paths, settings, data: dict, keyboard: int | None) -> Path:
    root = backup_dir(paths, settings)
    root.mkdir(parents=True, exist_ok=True)
    now = datetime.now()
    name = f"battle-controls-{now:%Y%m%d-%H%M%S}-{now.microsecond // 1000:03d}.json"
    layout = {field: data[field] for field in LAYOUT_FIELDS if field in data}
    payload = {"layout": layout, "keyboard": keyboard}
    path = root / name
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return path


# ---------- 状态 / 修复 / 还原 ----------


def status(paths: Paths, settings) -> BattleStatus:
    data, error = _read_prefs()
    if data is None:
        return BattleStatus(False, None, None, None, error)
    layout_type = data.get("battle_ui_cur_type")
    alpha = data.get("battle_ui_cur_alpha_value")
    return BattleStatus(
        exists=True,
        layout_type=layout_type if isinstance(layout_type, str) else None,
        alpha=alpha if isinstance(alpha, (int, float)) else None,
        keyboard=_read_keyboard(),
    )


def repair(paths: Paths, settings, *, game_running: bool) -> BattleResult:
    """重置为 PC 内置布局并启用键鼠模式；修复前自动备份。"""
    if game_running:
        return BattleResult(
            False,
            "game_running",
            "请先退出游戏再修复，避免客户端退出时覆盖修复后的设置。",
        )
    data, error = _read_prefs()
    if data is None:
        return BattleResult(False, "no_prefs", error)

    try:
        backup_file = _write_backup(paths, settings, data, _read_keyboard())
        data["battle_ui_cur_type"] = ""
        data["battle_ui_cur_alpha_value"] = REPAIR_ALPHA
        _write_prefs(data)
        _write_keyboard(KEYBOARD_MOUSE)
    except OSError as e:
        return BattleResult(False, "write_failed", f"修复失败（备份保留在原处）：{e}")

    return BattleResult(
        True,
        "ok",
        f"已恢复 PC 战斗布局并启用键鼠模式。备份：{backup_file}",
    )


def restore(
    paths: Paths, settings, backup_file: Path, *, game_running: bool
) -> BattleResult:
    """从备份 JSON 恢复战斗布局与键鼠模式（键名与官方 -Restore 一致）。"""
    if game_running:
        return BattleResult(
            False,
            "game_running",
            "请先退出游戏再还原，避免客户端退出时覆盖还原后的设置。",
        )
    try:
        payload = json.loads(backup_file.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError) as e:
        return BattleResult(False, "bad_backup", f"备份文件无法读取：{e}")
    if not isinstance(payload, dict):
        return BattleResult(False, "bad_backup", "备份文件的内容不是 JSON 对象")
    layout = payload.get("layout")
    keyboard = payload.get("keyboard")
    if layout is not None and not isinstance(layout, dict):
        return BattleResult(False, "bad_backup", "备份里的 layout 不是对象")

    data, error = _read_prefs()
    if data is None:
        return BattleResult(False, "no_prefs", error)

    try:
        for field in LAYOUT_FIELDS:
            if layout is not None and field in layout:
                data[field] = layout[field]
            else:
                data.pop(field, None)
        _write_prefs(data)
        if keyboard is None:
            _delete_keyboard()
        else:
            _write_keyboard(int(keyboard))
    except (OSError, TypeError, ValueError) as e:
        return BattleResult(False, "write_failed", f"还原失败：{e}")

    return BattleResult(
        True, "ok", f"已从备份恢复战斗布局与控制模式：{backup_file.name}"
    )

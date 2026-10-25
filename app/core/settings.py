from __future__ import annotations

import json
import os
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from app.core.paths import Paths

SCHEMA_VERSION = 1


# ---------- 类型辅助 ----------

def _as_str(value: Any, default: str = '') -> str:
    return value if isinstance(value, str) else default


def _as_int(value: Any, default: int) -> int:
    if isinstance(value, bool):
        return default
    if isinstance(value, int):
        return value
    if isinstance(value, str) and value.isdigit():
        return int(value)
    return default


def _as_bool(value: Any, default: bool) -> bool:
    return value if isinstance(value, bool) else default


def _as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _as_list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


# ---------- 数据类 ----------

@dataclass
class ClientEntry:
    id: str
    name: str
    exe_path: str
    launcher_path: str = ''

    def to_dict(self) -> dict[str, Any]:
        return {
            'id': self.id,
            'name': self.name,
            'exePath': self.exe_path,
            'launcherPath': self.launcher_path,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> 'ClientEntry':
        return cls(
            id=_as_str(d.get('id')),
            name=_as_str(d.get('name')),
            exe_path=_as_str(d.get('exePath')),
            launcher_path=_as_str(d.get('launcherPath')),
        )


@dataclass
class ServerConfig:
    version: str = ''
    http_port: int = 8080
    game_port: int = 5001

    def to_dict(self) -> dict[str, Any]:
        return {
            'version': self.version,
            'httpPort': self.http_port,
            'gamePort': self.game_port,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> 'ServerConfig':
        return cls(
            version=_as_str(d.get('version')),
            http_port=_as_int(d.get('httpPort'), 8080),
            game_port=_as_int(d.get('gamePort'), 5001),
        )


@dataclass
class PathsConfig:
    bin_dir: str = 'bin'
    home_dir: str = 'home'
    runtime_dir: str = 'runtime'
    logs_dir: str = 'logs'
    client_switch_dir: str = 'data/client-switch'

    def to_dict(self) -> dict[str, Any]:
        return {
            'binDir': self.bin_dir,
            'homeDir': self.home_dir,
            'runtimeDir': self.runtime_dir,
            'logsDir': self.logs_dir,
            'clientSwitchDir': self.client_switch_dir,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> 'PathsConfig':
        default = cls()
        return cls(
            bin_dir=_as_str(d.get('binDir'), default.bin_dir),
            home_dir=_as_str(d.get('homeDir'), default.home_dir),
            runtime_dir=_as_str(d.get('runtimeDir'), default.runtime_dir),
            logs_dir=_as_str(d.get('logsDir'), default.logs_dir),
            client_switch_dir=_as_str(d.get('clientSwitchDir'), default.client_switch_dir),
        )


@dataclass
class PrepareState:
    last_prepared_version: str = ''
    last_prepared_at: str = ''
    client_fingerprint: str = ''
    warning_dismissed_at: str = ''

    def to_dict(self) -> dict[str, Any]:
        return {
            'lastPreparedVersion': self.last_prepared_version,
            'lastPreparedAt': self.last_prepared_at,
            'clientFingerprint': self.client_fingerprint,
            'warningDismissedAt': self.warning_dismissed_at,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> 'PrepareState':
        return cls(
            last_prepared_version=_as_str(d.get('lastPreparedVersion')),
            last_prepared_at=_as_str(d.get('lastPreparedAt')),
            client_fingerprint=_as_str(d.get('clientFingerprint')),
            warning_dismissed_at=_as_str(d.get('warningDismissedAt')),
        )


@dataclass
class WindowState:
    width: int = 1120
    height: int = 720
    maximized: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            'width': self.width,
            'height': self.height,
            'maximized': self.maximized,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> 'WindowState':
        default = cls()
        return cls(
            width=_as_int(d.get('width'), default.width),
            height=_as_int(d.get('height'), default.height),
            maximized=_as_bool(d.get('maximized'), default.maximized),
        )


@dataclass
class UIConfig:
    theme: str = 'system'        # system / light / dark
    log_auto_scroll: bool = True
    window: WindowState = field(default_factory=WindowState)

    def to_dict(self) -> dict[str, Any]:
        return {
            'theme': self.theme,
            'logAutoScroll': self.log_auto_scroll,
            'window': self.window.to_dict(),
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> 'UIConfig':
        theme = _as_str(d.get('theme'), 'system')
        if theme not in ('system', 'light', 'dark'):
            theme = 'system'
        return cls(
            theme=theme,
            log_auto_scroll=_as_bool(d.get('logAutoScroll'), True),
            window=WindowState.from_dict(_as_dict(d.get('window'))),
        )


@dataclass
class BehaviorConfig:
    confirm_before_mode_switch: bool = True
    confirm_before_stop_server: bool = True
    auto_confirm_first_launch: bool = True
    log_to_file: bool = False
    backend: str = 'powershell'

    def to_dict(self) -> dict[str, Any]:
        return {
            'confirmBeforeModeSwitch': self.confirm_before_mode_switch,
            'confirmBeforeStopServer': self.confirm_before_stop_server,
            'autoConfirmFirstLaunch': self.auto_confirm_first_launch,
            'logToFile': self.log_to_file,
            'backend': self.backend,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> 'BehaviorConfig':
        backend = _as_str(d.get('backend'), 'powershell')
        if backend not in ('powershell', 'native'):
            backend = 'powershell'
        return cls(
            confirm_before_mode_switch=_as_bool(d.get('confirmBeforeModeSwitch'), True),
            confirm_before_stop_server=_as_bool(d.get('confirmBeforeStopServer'), True),
            auto_confirm_first_launch=_as_bool(d.get('autoConfirmFirstLaunch'), True),
            log_to_file=_as_bool(d.get('logToFile'), False),
            backend=backend,
        )


@dataclass
class Settings:
    schema_version: int = SCHEMA_VERSION
    clients: list[ClientEntry] = field(default_factory=list)
    active_client_id: str = ''
    server: ServerConfig = field(default_factory=ServerConfig)
    paths: PathsConfig = field(default_factory=PathsConfig)
    prepare: PrepareState = field(default_factory=PrepareState)
    ui: UIConfig = field(default_factory=UIConfig)
    behavior: BehaviorConfig = field(default_factory=BehaviorConfig)

    # ---------- 客户端便捷访问 ----------

    def active_client(self) -> ClientEntry | None:
        if not self.clients:
            return None
        for entry in self.clients:
            if entry.id == self.active_client_id:
                return entry
        return self.clients[0]

    def set_active_client(self, entry: ClientEntry) -> None:
        """新增或更新一个客户端并设为当前。id 为空时自动生成。"""
        if not entry.id:
            entry.id = self._next_client_id()
        for i, existing in enumerate(self.clients):
            if existing.id == entry.id:
                self.clients[i] = entry
                self.active_client_id = entry.id
                return
        self.clients.append(entry)
        self.active_client_id = entry.id

    def _next_client_id(self) -> str:
        used = {c.id for c in self.clients}
        i = 1
        while f'c{i}' in used:
            i += 1
        return f'c{i}'

    # ---------- 序列化 ----------

    def to_dict(self) -> dict[str, Any]:
        return {
            'schemaVersion': self.schema_version,
            'clients': [c.to_dict() for c in self.clients],
            'activeClientId': self.active_client_id,
            'server': self.server.to_dict(),
            'paths': self.paths.to_dict(),
            'prepare': self.prepare.to_dict(),
            'ui': self.ui.to_dict(),
            'behavior': self.behavior.to_dict(),
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> 'Settings':
        clients = [
            ClientEntry.from_dict(_as_dict(item))
            for item in _as_list(d.get('clients'))
            if isinstance(item, dict)
        ]
        settings = cls(
            schema_version=_as_int(d.get('schemaVersion'), SCHEMA_VERSION),
            clients=clients,
            active_client_id=_as_str(d.get('activeClientId')),
            server=ServerConfig.from_dict(_as_dict(d.get('server'))),
            paths=PathsConfig.from_dict(_as_dict(d.get('paths'))),
            prepare=PrepareState.from_dict(_as_dict(d.get('prepare'))),
            ui=UIConfig.from_dict(_as_dict(d.get('ui'))),
            behavior=BehaviorConfig.from_dict(_as_dict(d.get('behavior'))),
        )
        # active_client_id 指向不存在的条目时回退
        if clients and not any(c.id == settings.active_client_id for c in clients):
            settings.active_client_id = clients[0].id
        return settings


# ---------- 读写 ----------

class SettingsStore:
    """settings.json 的加载、保存、备份。原子写，失败回退 .bak。"""

    def __init__(self, paths: Paths):
        self.paths = paths

    # ---------- 加载 ----------

    def load(self) -> Settings:
        data = self._read_json(self.paths.settings_path)
        if data is None:
            data = self._read_json(self.paths.settings_backup_path)
        if data is None:
            return Settings()
        try:
            return Settings.from_dict(data)
        except Exception:
            return Settings()

    @staticmethod
    def _read_json(path: Path) -> dict[str, Any] | None:
        if not path.exists():
            return None
        try:
            raw = path.read_text(encoding='utf-8')
            data = json.loads(raw)
        except (OSError, json.JSONDecodeError):
            return None
        return data if isinstance(data, dict) else None

    # ---------- 保存 ----------

    def save(self, settings: Settings) -> None:
        settings.schema_version = SCHEMA_VERSION
        payload = json.dumps(
            settings.to_dict(),
            ensure_ascii=False,
            indent=2,
            sort_keys=False,
        ) + '\n'
        self._atomic_write(self.paths.settings_path, payload.encode('utf-8'))

    def _atomic_write(self, path: Path, data: bytes) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(path.name + '.tmp')
        tmp.write_bytes(data)
        # 已有文件先备份为 .bak，再原子替换
        if path.exists():
            backup = path.with_name(path.name + '.bak')
            try:
                shutil.copy2(path, backup)
            except OSError:
                pass
        os.replace(tmp, path)

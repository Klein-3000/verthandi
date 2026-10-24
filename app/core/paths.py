from __future__ import annotations

import sys
from pathlib import Path


def detect_base_dir() -> Path:
    """portable 根目录：打包后取 exe 所在目录，开发时取仓库根。"""
    if getattr(sys, 'frozen', False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent.parent.parent


class Paths:
    """所有相对路径基于 base_dir 解析；绝对路径原样保留。"""

    def __init__(self, base_dir: Path | None = None):
        self.base_dir = (base_dir or detect_base_dir()).resolve()

    # ---------- 解析 ----------

    def resolve(self, value: str | Path) -> Path:
        p = Path(value)
        if p.is_absolute():
            return p.resolve()
        return (self.base_dir / p).resolve()

    def relativize(self, path: Path) -> str:
        """在 base_dir 内返回相对路径（正斜杠），否则返回绝对路径。"""
        try:
            return path.resolve().relative_to(self.base_dir).as_posix()
        except ValueError:
            return path.resolve().as_posix()

    # ---------- settings.json 固定位置 ----------

    @property
    def settings_path(self) -> Path:
        return self.base_dir / 'settings.json'

    @property
    def settings_backup_path(self) -> Path:
        return self.base_dir / 'settings.json.bak'

    # ---------- 从 Settings 派生常用目录 ----------

    def bin_dir(self, settings) -> Path:
        return self.resolve(settings.paths.bin_dir)

    def home_dir(self, settings) -> Path:
        return self.resolve(settings.paths.home_dir)

    def runtime_dir(self, settings) -> Path:
        return self.resolve(settings.paths.runtime_dir)

    def logs_dir(self, settings) -> Path:
        return self.resolve(settings.paths.logs_dir)

    def client_switch_dir(self, settings) -> Path:
        return self.resolve(settings.paths.client_switch_dir)

    def active_client_dir(self, settings) -> Path | None:
        entry = settings.active_client()
        if entry is None or not entry.exe_path:
            return None
        return Path(entry.exe_path).resolve().parent

    # ---------- 便捷判断 ----------

    def is_inside(self, path: Path) -> bool:
        try:
            path.resolve().relative_to(self.base_dir)
            return True
        except ValueError:
            return False

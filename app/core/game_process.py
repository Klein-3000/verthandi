from __future__ import annotations

from pathlib import Path

try:
    import psutil
except ImportError:  # psutil 未安装时降级为“不在运行”
    psutil = None

EXE_NAME = 'AetherGazer.exe'


def is_game_running(client_dir: Path | None = None) -> bool:
    """检测 AetherGazer.exe 是否运行。

    - client_dir 为空：按进程名匹配；
    - 提供 client_dir：额外校验 exe 路径；拿不到路径时保守返回 True。
    """
    if psutil is None:
        return False

    target = ''
    if client_dir is not None:
        target = str((client_dir / EXE_NAME).resolve()).lower()

    for proc in psutil.process_iter(['name', 'exe']):
        try:
            info = proc.info
            name = (info.get('name') or '').lower()
            if name != EXE_NAME.lower():
                continue
            if not target:
                return True
            exe = info.get('exe')
            if not exe:
                return True
            if str(Path(exe).resolve()).lower() == target:
                return True
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
    return False

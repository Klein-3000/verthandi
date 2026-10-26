from __future__ import annotations

import re
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path


class ImportError(Exception):
    """导入 hoshimi 失败。code 用于分类。"""

    def __init__(self, code: str, message: str):
        self.code = code
        self.message = message
        super().__init__(message)


# ---------- 从 ZIP 准备 ----------

def prepare_from_zip(zip_path: Path, temp_dir: Path) -> bool:
    """选择性解压 hoshimi.exe 和 scripts/ 到 temp_dir。

    返回 scripts 是否有效（存在且有 .ps1 文件）。
    失败抛 ImportError。
    """
    try:
        with zipfile.ZipFile(zip_path, 'r') as zf:
            names = zf.namelist()
            prefix = _detect_prefix(names)
            if prefix is None:
                raise ImportError(
                    'no_exe_in_zip',
                    f'压缩包中找不到 hoshimi.exe：\n{zip_path.name}',
                )

            exe_member = prefix + 'hoshimi.exe'
            scripts_prefix = prefix + 'scripts/'

            members = [exe_member]
            for name in names:
                if name.startswith(scripts_prefix) and not name.endswith('/'):
                    members.append(name)

            for member in members:
                rel = member[len(prefix):]
                target = temp_dir / rel
                target.parent.mkdir(parents=True, exist_ok=True)
                with zf.open(member) as src, open(target, 'wb') as dst:
                    shutil.copyfileobj(src, dst)
    except zipfile.BadZipFile as e:
        raise ImportError(
            'bad_zip',
            f'无法解压 {zip_path.name}：{e}\n\n'
            '文件可能损坏或不是 ZIP 格式。\n'
            '请手动解压后选择 hoshimi.exe 导入。',
        )
    except OSError as e:
        raise ImportError('zip_io', f'读取压缩包失败：{e}')

    return _scripts_valid(temp_dir / 'scripts')


def _detect_prefix(names: list[str]) -> str | None:
    """识别 ZIP 内的根前缀。根级返回 ''，一层嵌套返回 'outer/'，无 hoshimi.exe 返回 None。"""
    if 'hoshimi.exe' in names:
        return ''
    for name in names:
        parts = name.split('/')
        if len(parts) >= 2:
            prefix = parts[0] + '/'
            if prefix + 'hoshimi.exe' in names:
                return prefix
    return None


# ---------- 从 EXE 准备 ----------

def prepare_from_exe(exe_path: Path, temp_dir: Path) -> bool:
    """复制 exe 和同目录 scripts/ 到 temp_dir。返回 scripts 是否有效。"""
    if exe_path.name.lower() != 'hoshimi.exe':
        raise ImportError(
            'bad_exe_name',
            f'文件名不是 hoshimi.exe：\n{exe_path.name}',
        )
    if not exe_path.exists():
        raise ImportError('exe_missing', f'文件不存在：{exe_path}')

    try:
        shutil.copy2(exe_path, temp_dir / 'hoshimi.exe')
    except OSError as e:
        raise ImportError('copy_failed', f'复制 hoshimi.exe 失败：{e}')

    scripts_src = exe_path.parent / 'scripts'
    if scripts_src.exists() and _scripts_valid(scripts_src):
        try:
            shutil.copytree(scripts_src, temp_dir / 'scripts')
        except OSError as e:
            raise ImportError('copy_failed', f'复制 scripts 目录失败：{e}')
        return True
    return False


def _scripts_valid(scripts_dir: Path) -> bool:
    if not scripts_dir.exists() or not scripts_dir.is_dir():
        return False
    return any(scripts_dir.glob('*.ps1'))


# ---------- 校验与版本检测 ----------

def validate(temp_dir: Path) -> None:
    """校验 temp_dir 结构。失败抛 ImportError。"""
    if not (temp_dir / 'hoshimi.exe').exists():
        raise ImportError('no_exe', '临时目录中缺少 hoshimi.exe')


_VERSION_RE_H = re.compile(r'当前版本[：:]\s*([0-9]+\.[0-9]+\.[0-9]+)')
_VERSION_RE_NAME = re.compile(r'v?(\d+\.\d+\.\d+)')


def detect_version(exe_path: Path, timeout: float = 5.0) -> str | None:
    """执行 hoshimi.exe -h，解析版本号。失败返回 None。"""
    creationflags = 0
    startupinfo = None
    if sys.platform == 'win32':
        creationflags = 0x08000000  # CREATE_NO_WINDOW
        startupinfo = subprocess.STARTUPINFO()
        startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        startupinfo.wShowWindow = 0

    try:
        result = subprocess.run(
            [str(exe_path), '-h'],
            capture_output=True,
            text=True,
            encoding='utf-8',
            errors='replace',
            timeout=timeout,
            creationflags=creationflags,
            startupinfo=startupinfo,
            cwd=str(exe_path.parent),
        )
    except (subprocess.TimeoutExpired, OSError):
        return None

    text = (result.stdout or '') + (result.stderr or '')
    m = _VERSION_RE_H.search(text)
    if m:
        return m.group(1)
    return None


def detect_version_from_filename(name: str) -> str | None:
    """从文件名提取版本号，如 Hoshimi-windows-amd64-v0.0.5.zip → 0.0.5。"""
    m = _VERSION_RE_NAME.search(name)
    return m.group(1) if m else None


# ---------- 落位 ----------

def finalize(
    temp_dir: Path,
    bin_dir: Path,
    version: str,
    action: str,
) -> Path:
    """把 temp_dir 重命名为 bin/<version>/。

    action: 'backup' / 'overwrite' / 'new'
    返回目标路径。失败抛 ImportError。
    """
    target = bin_dir / version
    if target.exists():
        if action == 'overwrite':
            try:
                shutil.rmtree(target)
            except OSError as e:
                raise ImportError('rm_failed', f'删除旧版本目录失败：{e}')
        elif action == 'backup':
            from datetime import datetime
            ts = datetime.now().strftime('%Y%m%d-%H%M%S')
            backup = bin_dir / f'{version}-backup-{ts}'
            try:
                target.rename(backup)
            except OSError as e:
                raise ImportError('backup_failed', f'备份旧版本失败：{e}')
        else:
            raise ImportError('conflict', f'版本目录已存在：{target}')

    try:
        temp_dir.rename(target)
    except OSError:
        # 跨盘或权限问题，退化为复制 + 删除
        try:
            shutil.move(str(temp_dir), str(target))
        except OSError as e:
            raise ImportError('move_failed', f'移动目录失败：{e}')

    return target

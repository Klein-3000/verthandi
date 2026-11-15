"""hoshimi 版本探测的唯一实现。

统一走官方语义入口 `hoshimi.exe version`，解析「当前版本：」「编译时间：」；
不再从 `-h` 帮助文本里正则猜测。探测时恒定带 `--no-log`（0.0.6+ 支持该参数，
且实测 0.0.8 连 `version` 子命令都会创建日志目录），旧版不认识该参数时
退化为不带参数重试。
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path


def _creation_kwargs() -> dict:
    kwargs: dict = {}
    if sys.platform == "win32":
        kwargs["creationflags"] = 0x08000000  # CREATE_NO_WINDOW
        startupinfo = subprocess.STARTUPINFO()
        startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        startupinfo.wShowWindow = 0
        kwargs["startupinfo"] = startupinfo
    return kwargs


def parse_version_output(text: str) -> tuple[str, str] | None:
    """解析 version 输出。缺「当前版本」行视为失败；「编译时间」可缺。"""
    version = compile_time = ""
    for line in text.splitlines():
        if "当前版本" in line and "：" in line:
            version = line.split("：", 1)[1].strip()
        elif "编译时间" in line and "：" in line:
            compile_time = line.split("：", 1)[1].strip()
    if version:
        return version, compile_time
    return None


def run_version(exe_path: Path, extra_args: list[str], timeout: float) -> str:
    """执行 version 子命令，返回合并输出（stdout + stderr）；失败返回空串。"""
    cmd = [str(exe_path), "version", *extra_args]
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            check=False,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            cwd=str(exe_path.parent),
            **_creation_kwargs(),
        )
    except (subprocess.TimeoutExpired, OSError):
        return ""
    return (result.stdout or "") + (result.stderr or "")


def probe_version(exe_path: Path, timeout: float = 5.0) -> tuple[str, str] | None:
    """探测版本。0.0.6+ 支持 --no-log，直接成功；旧版先失败再退化重试。"""
    for extra in (["--no-log"], []):
        parsed = parse_version_output(run_version(exe_path, extra, timeout))
        if parsed:
            return parsed
    return None

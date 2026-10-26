from __future__ import annotations

import json
from datetime import datetime
from enum import Enum
from pathlib import Path
from urllib.parse import urlparse


class ClientMode(str, Enum):
    LOCAL = "local"
    OFFICIAL = "official"
    UNKNOWN = "unknown"


def read_client_mode(client_dir: Path | None) -> ClientMode:
    """读 AetherGazer_Data/StreamingAssets/url.txt 判断实际 mode。"""
    if client_dir is None:
        return ClientMode.UNKNOWN
    url_path = client_dir / "AetherGazer_Data" / "StreamingAssets" / "url.txt"
    if not url_path.exists():
        return ClientMode.UNKNOWN
    try:
        data = json.loads(url_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return ClientMode.UNKNOWN
    url = data.get("url", "") if isinstance(data, dict) else ""
    host = urlparse(url).hostname or ""
    if host in ("127.0.0.1", "localhost", "::1"):
        return ClientMode.LOCAL
    if host:
        return ClientMode.OFFICIAL
    return ClientMode.UNKNOWN


def client_fingerprint(client_dir: Path | None) -> str:
    """AetherGazer.exe 的 size + mtime 指纹，用于判断客户端是否更新。"""
    if client_dir is None:
        return ""
    exe = client_dir / "AetherGazer.exe"
    if not exe.exists():
        return ""
    try:
        stat = exe.stat()
    except OSError:
        return ""
    mtime = datetime.fromtimestamp(stat.st_mtime).isoformat(timespec="seconds")
    return f"size={stat.st_size};mtime={mtime}"

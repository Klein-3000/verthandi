from __future__ import annotations

import json
import shutil
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from app.core.hoshimi_finder import find_hoshimi_exe
from app.core.paths import Paths
from app.core.settings import Settings

# ---------- 布局常量 ----------

RUNTIME_SUBDIR = "runtime"
MANIFEST_NAME = "prepare.json"
STAGING_PREFIX = ".staging-"
PREV_PREFIX = ".prev-"
UNKNOWN_VERSION = "unknown"
MANIFEST_SCHEMA = 1

RUNTIME_REQUIRED_FILES = (
    "protocol_map.json",
    "runtime.json",
    "tables.json",
    "tolua.dll",
)
RUNTIME_REQUIRED_DIRS = ("lua",)


# ---------- 元数据 ----------


@dataclass
class PrepareManifest:
    """每个 runtime 的产出记录。缺字段一律兜底，解析失败等同于"没有记录"。"""

    version: str = ""
    prepared_at: str = ""
    client_dir: str = ""
    client_fingerprint: str = ""
    hoshimi_exe: str = ""
    complete: bool = False
    missing: list[str] = field(default_factory=list)
    size_bytes: int = 0
    schema: int = MANIFEST_SCHEMA

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": self.schema,
            "version": self.version,
            "preparedAt": self.prepared_at,
            "clientDir": self.client_dir,
            "clientFingerprint": self.client_fingerprint,
            "hoshimiExe": self.hoshimi_exe,
            "complete": self.complete,
            "missing": list(self.missing),
            "sizeBytes": self.size_bytes,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> PrepareManifest:
        missing = d.get("missing")
        return cls(
            version=_as_str(d.get("version")),
            prepared_at=_as_str(d.get("preparedAt")),
            client_dir=_as_str(d.get("clientDir")),
            client_fingerprint=_as_str(d.get("clientFingerprint")),
            hoshimi_exe=_as_str(d.get("hoshimiExe")),
            complete=bool(d.get("complete", False)),
            missing=[m for m in missing if isinstance(m, str)]
            if isinstance(missing, list)
            else [],
            size_bytes=_as_int(d.get("sizeBytes")),
            schema=_as_int(d.get("schema"), MANIFEST_SCHEMA),
        )

    @property
    def prepared_label(self) -> str:
        """'2026-11-06T18:20:11' -> '11-06 18:20'；解析不了就原样返回。"""
        try:
            return datetime.fromisoformat(self.prepared_at).strftime("%m-%d %H:%M")
        except ValueError:
            return self.prepared_at


def _as_str(value: Any) -> str:
    return value if isinstance(value, str) else ""


def _as_int(value: Any, default: int = 0) -> int:
    if isinstance(value, bool):
        return default
    return value if isinstance(value, int) else default


# ---------- 路径派生 ----------


@dataclass(frozen=True)
class RuntimeRef:
    """一次「当前该用哪个 runtime」的解析结果，全项目唯一真相源。

    目录恒为 runtimes/<version>/runtime，按版本存放，切版本即切目录。
    """

    version: str
    exe: Path
    directory: Path
    auto: bool

    @property
    def exists(self) -> bool:
        return self.directory.is_dir()


def runtime_root(paths: Paths, settings: Settings) -> Path:
    return paths.resolve(settings.paths.runtime_root)


def resolve_runtime(paths: Paths, settings: Settings) -> RuntimeRef:
    """版本与 runtime 目录同源派生：切版本即切目录，不再有第二处状态。"""
    exe, version, auto = find_hoshimi_exe(paths, settings)
    version = version or UNKNOWN_VERSION
    directory = runtime_root(paths, settings) / version / RUNTIME_SUBDIR
    return RuntimeRef(version, exe, directory, auto)


def manifest_path(paths: Paths, settings: Settings, ref: RuntimeRef) -> Path:
    return ref.directory.parent / MANIFEST_NAME


def read_manifest_file(path: Path) -> PrepareManifest | None:
    try:
        raw = path.read_text(encoding="utf-8")
        data = json.loads(raw)
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict):
        return None
    return PrepareManifest.from_dict(data)


def read_manifest(
    paths: Paths, settings: Settings, ref: RuntimeRef
) -> PrepareManifest | None:
    return read_manifest_file(manifest_path(paths, settings, ref))


def write_manifest(path: Path, manifest: PrepareManifest) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(
        json.dumps(manifest.to_dict(), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    tmp.replace(path)


# ---------- 完整性 ----------


def validate_runtime(path: Path) -> tuple[bool, list[str]]:
    """返回 (完整, 缺失项)。只看存在性，不读内容，可以在扫描列表里高频调用。"""
    if not path.is_dir():
        return False, ["（目录不存在）"]
    missing = [name for name in RUNTIME_REQUIRED_FILES if not (path / name).exists()]
    missing += [
        f"{name}/" for name in RUNTIME_REQUIRED_DIRS if not (path / name).is_dir()
    ]
    return not missing, missing


def dir_size(path: Path) -> int:
    total = 0
    if not path.exists():
        return 0
    for root, _dirs, files in path.walk():
        for name in files:
            try:
                total += (root / name).stat().st_size
            except OSError:
                pass
    return total


# ---------- staging / 换入 ----------


def create_staging(paths: Paths, settings: Settings, version: str) -> Path:
    root = runtime_root(paths, settings)
    root.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    base = f"{STAGING_PREFIX}{version or UNKNOWN_VERSION}-{stamp}"
    candidate = root / base
    i = 1
    while candidate.exists():
        i += 1
        candidate = root / f"{base}-{i}"
    candidate.mkdir(parents=True)
    return candidate


def cleanup_staging(paths: Paths, settings: Settings) -> list[Path]:
    """启动时清理上次崩溃残留的临时目录。正在跑的 prepare 只可能存在于当前进程。"""
    root = runtime_root(paths, settings)
    removed: list[Path] = []
    if not root.exists():
        return removed
    for entry in root.iterdir():
        if not entry.is_dir() or not entry.name.startswith(STAGING_PREFIX):
            continue
        try:
            shutil.rmtree(entry)
        except OSError:
            continue
        removed.append(entry)
    return removed


class RuntimeSwapError(RuntimeError):
    def __init__(self, message: str, staging: Path | None = None):
        super().__init__(message)
        self.staging = staging


def finalize(
    ref: RuntimeRef,
    staging: Path,
    *,
    target_manifest: Path,
    client_dir: Path | None,
    fingerprint: str = "",
) -> PrepareManifest:
    """校验 staging 并原子换入正式目录，随后写元数据。

    目标目录存在时先改名为 .prev-<ts>，换入失败会把它还原——任何一步失败，
    正式目录要么是旧内容，要么是新内容，不会出现"半成品"。
    """
    complete, missing = validate_runtime(staging)
    if not complete:
        raise RuntimeSwapError(
            "初始化产出的运行文件不完整（缺少：" + "、".join(missing) + "）", staging
        )

    target = ref.directory
    target.parent.mkdir(parents=True, exist_ok=True)

    prev: Path | None = None
    if target.exists():
        prev = target.parent / f"{PREV_PREFIX}{datetime.now():%Y%m%d-%H%M%S}"
        try:
            _move_dir(target, prev)
        except OSError as e:
            raise RuntimeSwapError(f"无法移开旧的运行文件：{e}", staging) from e

    try:
        _move_dir(staging, target)
    except OSError as e:
        if prev is not None and prev.exists():
            try:
                _move_dir(prev, target)
            except OSError:
                pass
        raise RuntimeSwapError(f"无法把新的运行文件换入：{e}", staging) from e

    if prev is not None:
        shutil.rmtree(prev, ignore_errors=True)

    manifest = PrepareManifest(
        version=ref.version,
        prepared_at=datetime.now().isoformat(timespec="seconds"),
        client_dir=str(client_dir) if client_dir else "",
        client_fingerprint=fingerprint,
        hoshimi_exe=str(ref.exe),
        complete=True,
        missing=[],
        size_bytes=dir_size(target),
    )
    try:
        write_manifest(target_manifest, manifest)
    except OSError:
        # 元数据写失败不影响运行，下次扫描会退化成"缺少初始化记录"
        pass
    return manifest


def _move_dir(src: Path, dst: Path, attempts: int = 4) -> None:
    """同卷 rename 瞬时完成；被占用时短暂重试；跨卷退化为 move。"""
    last: OSError | None = None
    for i in range(attempts):
        try:
            src.rename(dst)
            return
        except OSError as e:
            last = e
            if i + 1 < attempts:
                time.sleep(0.3)
    try:
        shutil.move(str(src), str(dst))
    except OSError as e:
        raise last or e


# ---------- v1 迁移 ----------


@dataclass
class MigrationResult:
    changed: bool = False
    message: str = ""
    version: str = ""
    target: Path | None = None


def migrate_legacy(paths: Paths, settings: Settings) -> MigrationResult:
    """把旧的单个 runtime 目录搬进 runtimes/<version>/runtime。

    旧目录的来源：v1 的 runtimeDir，或曾被降级写成 customRuntimeDir 的路径。
    只移动目录、不复制；失败时不动数据、不降级，提示用户重新初始化。
    """
    root = runtime_root(paths, settings)
    if _has_version_dirs(root):
        return MigrationResult()

    legacy = paths.resolve(settings.paths.custom_runtime_dir or "runtime")
    if not legacy.is_dir() or legacy == root:
        return MigrationResult()

    version = _legacy_version(paths, settings)
    target = root / version / RUNTIME_SUBDIR

    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        _move_dir(legacy, target)
    except OSError:
        # 不再降级成"固定目录"模式：数据原地保留，等用户手动初始化
        return MigrationResult(
            changed=False,
            message=f"旧 runtime 位于 {legacy}，无法自动迁入 runtimes/，"
            "请点击「初始化」重新生成",
        )

    complete, missing = validate_runtime(target)
    manifest = PrepareManifest(
        version=version,
        prepared_at=_mtime_iso(target),
        client_dir=str(settings.active_client().exe_path)
        if settings.active_client()
        else "",
        client_fingerprint=_as_str(settings.legacy_prepare.get("clientFingerprint")),
        hoshimi_exe=str(find_hoshimi_exe(paths, settings)[0]),
        complete=complete,
        missing=missing,
        size_bytes=dir_size(target),
    )
    try:
        write_manifest(target.parent / MANIFEST_NAME, manifest)
    except OSError:
        pass

    settings.legacy_prepare = {}
    return MigrationResult(
        changed=True,
        message=f"旧 runtime 已迁移到 runtimes/{version}/{RUNTIME_SUBDIR}",
        version=version,
        target=target,
    )


def _has_version_dirs(root: Path) -> bool:
    if not root.is_dir():
        return False
    for entry in root.iterdir():
        if entry.is_dir() and not entry.name.startswith((STAGING_PREFIX, PREV_PREFIX)):
            return True
    return False


def _legacy_version(paths: Paths, settings: Settings) -> str:
    version = _as_str(settings.legacy_prepare.get("lastPreparedVersion")).strip()

    if not version:
        bin_dir = paths.bin_dir(settings)
        try:
            found = sorted(
                d.name
                for d in bin_dir.iterdir()
                if d.is_dir() and (d / "hoshimi.exe").exists()
            )
        except OSError:
            found = []
        if len(found) == 1:
            version = found[0]

    return version or UNKNOWN_VERSION


def _mtime_iso(path: Path) -> str:
    try:
        return datetime.fromtimestamp(path.stat().st_mtime).isoformat(
            timespec="seconds"
        )
    except OSError:
        return datetime.now().isoformat(timespec="seconds")

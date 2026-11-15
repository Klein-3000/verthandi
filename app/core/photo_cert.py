"""同机「誓约照片」HTTPS：证书生成、配置启用与信任管理。

游戏内的照片 SDK 固定走 HTTPS，官方用 `scripts/enable-photo-upload.ps1`
完成同样的事。这里用 Python 重写，逐条对齐官方的行为：

- 生成只含 `127.0.0.1` 的自签证书，私钥只落在服务端目录；
- 把证书信任到**当前 Windows 用户**的根证书库（不需要管理员）；
- 备份原 `config.json`（整份 base64 + 三份 SHA256），改 `enable_https`；
- 任一步失败逐字节回滚；
- 停用时只在三份文件哈希都未变的前提下还原，否则拒绝。

与官方**故意的差异**只有两点：不再要求 PowerShell 7；状态判断更严格——
会去根证书库核对证书是否真的还被信任（用户可能在 certmgr 里手动删掉）。
state 文件名与键名与官方完全一致，先用过 ps1 的目录在这里能识别、也能停用。
"""

from __future__ import annotations

import base64
import hashlib
import ipaddress
import json
import os
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

from cryptography import x509
from cryptography.exceptions import UnsupportedAlgorithm
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID

from app.core.paths import Paths
from app.core.runtime_layout import resolve_runtime
from app.core.settings import Settings
from app.core.telemetry_config import parse_version
from app.core.windows_trust import (
    TrustError,
    add_root_certificate,
    is_trusted,
    remove_root_certificate,
)

__all__ = [
    "ENV_OVERRIDE",
    "PhotoResult",
    "PhotoStatus",
    "adopt",
    "config_path",
    "disable",
    "enable",
    "list_photos",
    "photos_dir",
    "renew",
    "status",
    "supports_photo",
]

CERT_NAME = "cert.pem"
KEY_NAME = "key.pem"
STATE_NAME = "photo-upload-state.json"
MANAGED_FILES = (CERT_NAME, KEY_NAME, STATE_NAME)
DEFAULT_HTTPS_PORT = 443
ENV_OVERRIDE = "AGL_ENABLE_HTTPS"
CERT_CN = "Hoshimi Local Photo"
LOOPBACK = ipaddress.ip_address("127.0.0.1")
VALID_DAYS = 365
WARN_DAYS = 30
# 0.0.3 的二进制里有 EnableHTTPS，但没有 /photos/ 端点，照片是 0.0.8 起的
PHOTO_MIN_VERSION = (0, 0, 8)


@dataclass(frozen=True)
class PhotoStatus:
    """一次状态判定的完整结果，UI 只负责展示。"""

    level: str  # off / ok / warn / error
    title: str
    detail: str
    enabled: bool
    https_port: int
    thumbprint: str
    not_after: datetime | None
    days_left: int | None
    trusted: bool
    can_enable: bool
    can_disable: bool
    can_renew: bool
    subject_cn: str = ""
    can_adopt: bool = False


@dataclass(frozen=True)
class PhotoResult:
    ok: bool
    code: str
    message: str


class _Material:
    """一次性生成的证书材料。"""

    def __init__(self) -> None:
        key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, CERT_CN)])
        now = datetime.now(UTC)
        cert = (
            x509.CertificateBuilder()
            .subject_name(name)
            .issuer_name(name)
            .public_key(key.public_key())
            .serial_number(x509.random_serial_number())
            .not_valid_before(now - timedelta(minutes=5))
            .not_valid_after(now + timedelta(days=VALID_DAYS))
            .add_extension(
                x509.SubjectAlternativeName([x509.IPAddress(LOOPBACK)]),
                critical=False,
            )
            .add_extension(
                x509.BasicConstraints(ca=False, path_length=None), critical=True
            )
            .add_extension(
                x509.ExtendedKeyUsage([ExtendedKeyUsageOID.SERVER_AUTH]), critical=True
            )
            .sign(key, hashes.SHA256())
        )
        self.der = cert.public_bytes(serialization.Encoding.DER)
        self.cert_pem = cert.public_bytes(serialization.Encoding.PEM)
        self.key_pem = key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
        self.thumbprint = hashlib.sha1(self.der).hexdigest().upper()


# ---------- 路径 ----------


def config_path(paths: Paths, settings: Settings) -> Path:
    return paths.home_dir(settings) / "config.json"


def photos_dir(paths: Paths, settings: Settings) -> Path:
    return paths.home_dir(settings) / "data" / "photos"


PHOTO_EXTENSIONS = (".jpg", ".jpeg", ".png")
# hoshimi 的路由是 photos/<24hex>/<id>/oath/picture_*.jpg|png，但目录语义
# 不归我们管：只按「photos 下最多 6 层的图片文件」宽松扫描，按修改时间倒序
_PHOTO_SCAN_DEPTH = 6


def list_photos(paths: Paths, settings: Settings) -> list[Path]:
    """扫描照片目录，按修改时间倒序返回；目录不存在返回空列表。"""
    root = photos_dir(paths, settings)
    if not root.is_dir():
        return []
    found: list[Path] = []
    base_depth = len(root.parts)
    for path in root.rglob("*"):
        depth = len(path.parts) - base_depth
        if depth > _PHOTO_SCAN_DEPTH or not path.is_file():
            continue
        if path.suffix.lower() in PHOTO_EXTENSIONS:
            found.append(path)
    return sorted(found, key=lambda p: p.stat().st_mtime, reverse=True)


def supports_photo(version: str) -> bool:
    """该版本是否带照片上传端点。版本未知时按不支持处理。"""
    parsed = parse_version(version)
    return parsed is not None and parsed >= PHOTO_MIN_VERSION


def _state_path(paths: Paths, settings: Settings) -> Path:
    return paths.home_dir(settings) / STATE_NAME


# ---------- 读取与状态 ----------


def _sha256(path: Path) -> str:
    """算 SHA256，但先去掉 BOM 并把 CRLF 归一成 LF。

    官方的 state 是纯字节哈希：用户用记事本改一行再改回来（换行从 LF 变
    CRLF），或者 Verthandi 别的功能重写了 config.json，都会让字节哈希对不上，
    于是"停用"被误判成"文件被篡改"而拒绝。这里只归一换行与 BOM，真正的内容
    改动仍然测得到；官方脚本写出的 state（本就 LF）算出来的值也一致。
    """
    data = path.read_bytes()
    if data.startswith(b"\xef\xbb\xbf"):
        data = data[3:]
    return hashlib.sha256(data.replace(b"\r\n", b"\n")).hexdigest().upper()


def _read_config(paths: Paths, settings: Settings) -> tuple[dict | None, str]:
    """返回 (配置内容, 错误说明)。"""
    path = config_path(paths, settings)
    try:
        raw = path.read_bytes()
    except OSError:
        return None, f"home 目录下没有 config.json（{path}），请先完成服务端准备"
    try:
        data = json.loads(raw.decode("utf-8-sig"))
    except (UnicodeDecodeError, ValueError) as e:
        return None, f"config.json 无法解析：{e}"
    if not isinstance(data, dict):
        return None, "config.json 的内容不是一个 JSON 对象"
    return data, ""


def _read_state(paths: Paths, settings: Settings) -> dict | None:
    try:
        data = json.loads(_state_path(paths, settings).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def _cert_thumbprint(paths: Paths, settings: Settings) -> str:
    """直接从 cert.pem 算 SHA1 指纹；读不出来返回空串。"""
    try:
        der = x509.load_pem_x509_certificate(
            (paths.home_dir(settings) / CERT_NAME).read_bytes()
        ).public_bytes(serialization.Encoding.DER)
    except (OSError, ValueError):
        return ""
    return hashlib.sha1(der).hexdigest().upper()


def _cert_info(paths: Paths, settings: Settings) -> tuple[datetime | None, str]:
    """读取证书的到期时间与主题 CN。证书不存在或解析失败时返回 (None, "")。"""
    try:
        cert = x509.load_pem_x509_certificate(
            (paths.home_dir(settings) / CERT_NAME).read_bytes()
        )
    except (OSError, ValueError):
        return None, ""
    cn_attrs = cert.subject.get_attributes_for_oid(NameOID.COMMON_NAME)
    cn = cn_attrs[0].value if cn_attrs else ""
    return cert.not_valid_after_utc, str(cn)


def _hash_matches(paths: Paths, settings: Settings, state: dict) -> tuple[bool, str]:
    """三份文件是否都还是启用时的样子。返回 (是否一致, 首个不一致的文件)。"""
    home = paths.home_dir(settings)
    for name, key in (
        ("config.json", "config_hash"),
        (CERT_NAME, "cert_hash"),
        (KEY_NAME, "key_hash"),
    ):
        expected = state.get(key)
        path = home / name
        if not isinstance(expected, str) or not path.is_file():
            return False, name
        if _sha256(path) != expected.upper():
            return False, name
    return True, ""


def status(paths: Paths, settings: Settings) -> PhotoStatus:
    data, config_error = _read_config(paths, settings)
    home = paths.home_dir(settings)
    state = _read_state(paths, settings)
    cert_exists = (home / CERT_NAME).is_file()
    key_exists = (home / KEY_NAME).is_file()
    not_after, subject_cn = _cert_info(paths, settings)
    thumbprint = ""
    if isinstance(state, dict):
        value = state.get("thumbprint")
        thumbprint = value.upper() if isinstance(value, str) else ""
    if not thumbprint and cert_exists:
        # 无启用记录（外部配置）时直接从 cert.pem 取指纹，UI 才有信息可展示
        thumbprint = _cert_thumbprint(paths, settings)
    trusted = is_trusted(thumbprint) if thumbprint and cert_exists else False
    # 没有启用记录但证书文件齐全：可以「接管」（外部 ps1 / 手工配置的证书）
    adoptable = state is None and cert_exists and key_exists and data is not None

    enabled = bool(data.get("enable_https")) if data else False
    try:
        https_port = (
            int(data.get("https_port", DEFAULT_HTTPS_PORT))
            if data
            else DEFAULT_HTTPS_PORT
        )
    except (TypeError, ValueError):
        https_port = DEFAULT_HTTPS_PORT

    now = datetime.now(UTC)
    days_left = None
    if not_after is not None:
        days_left = (not_after - now).days

    def build(
        level: str,
        title: str,
        detail: str,
        *,
        can_enable: bool = False,
        can_disable: bool = False,
        can_renew: bool = False,
        can_adopt: bool = False,
    ) -> PhotoStatus:
        return PhotoStatus(
            level=level,
            title=title,
            detail=detail,
            enabled=enabled,
            https_port=https_port,
            thumbprint=thumbprint,
            not_after=not_after,
            days_left=days_left,
            trusted=trusted,
            can_enable=can_enable,
            can_disable=can_disable,
            can_renew=can_renew,
            subject_cn=subject_cn,
            can_adopt=can_adopt,
        )

    if data is None:
        return build("error", "不可用", config_error)

    version = resolve_runtime(paths, settings).version
    if not supports_photo(version):
        return build(
            "off",
            "当前版本不支持",
            f"誓约照片需要 hoshimi 0.0.8 及以上（当前 {version or '未知'}，"
            "该版本没有照片上传端点）。",
        )

    if not enabled and state is None:
        if adoptable:
            return build(
                "off",
                "证书文件已存在，尚未启用",
                "home 目录下已有 cert.pem / key.pem，但 config.json 未打开 "
                "enable_https，也没有启用记录。点「接管」可以纳入管理：打开 "
                "enable_https 并建立记录，不重新签发证书。",
                can_adopt=True,
            )
        return build(
            "off",
            "未启用",
            "启用后游戏内才能保存誓约照片；仅同一台电脑、同一 Windows 用户有效。",
            can_enable=True,
        )

    if not enabled:
        return build(
            "warn",
            "配置已关闭，但启用记录仍在",
            "可能是手动改过 config.json 的 enable_https。建议先停止服务，"
            "再点「停用」把证书和记录一并清掉，然后重新启用。",
            can_disable=True,
        )

    if state is None:
        if adoptable:
            return build(
                "warn",
                "已启用（外部配置）",
                "config.json 里的 enable_https 为 true，证书文件也在，但没有启用"
                "记录（可能是官方脚本或手工配置的）。点「接管」可以纳入管理："
                "建立记录后即可正常停用与续期，不重新签发证书。",
                can_adopt=True,
            )
        return build(
            "warn",
            "已启用，但没有可管理的证书",
            "config.json 里的 enable_https 为 true，但 home 目录下没有 "
            "cert.pem / key.pem，无法自动管理。若证书是你自己配的，请手动管理；"
            "若要交给我们管理，请自行清理后重新启用。",
        )

    intact, culprit = _hash_matches(paths, settings, state)
    if not intact:
        return build(
            "error",
            "配置已被外部修改",
            f"{culprit} 与启用时不一致，自动还原会覆盖你的改动，因此停用会被拒绝。"
            "请手动处理，或删除 cert.pem / key.pem / 启用记录后重新启用。",
        )

    if not cert_exists or not key_exists or not_after is None:
        return build(
            "error",
            "证书文件缺失或损坏",
            "启用记录还在但证书读不出来。请点「停用」清理后重新启用。",
            can_disable=True,
        )

    if days_left is not None and days_left < 0:
        return build(
            "error",
            f"证书已过期 {abs(days_left)} 天",
            "过期后拍照会失败。点「续期」会先恢复再重新签发（有效期一年）。",
            can_disable=True,
            can_renew=True,
        )

    if not trusted:
        return build(
            "error",
            "证书未受信任，拍照仍会失败",
            "当前用户的根证书库里找不到这张证书（可能被手动删除了，"
            "或当初点掉了 Windows 的安全提示）。点「停用」清理后重新启用即可恢复。",
            can_disable=True,
        )

    expire_text = f"{not_after.astimezone().strftime('%Y-%m-%d')}"
    if days_left is not None and days_left <= WARN_DAYS:
        return build(
            "warn",
            f"证书 {days_left} 天后过期",
            f"到期时间 {expire_text}。点「续期」会换发新证书（指纹会变，"
            "需要重启服务并重新登录游戏）。",
            can_disable=True,
            can_renew=True,
        )

    return build(
        "ok",
        f"已启用 · 证书剩余 {days_left} 天",
        f"到期时间 {expire_text}，HTTPS 端口 {https_port}，"
        "照片保存在 home 目录的 data/photos 下。",
        can_disable=True,
        can_renew=True,
    )


# ---------- 写文件 ----------


def _write_bytes(path: Path, payload: bytes) -> None:
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_bytes(payload)
    tmp.replace(path)


def _state_payload(
    original: bytes, config_file: Path, home: Path, thumbprint: str
) -> dict:
    return {
        "original_config": base64.b64encode(original).decode("ascii"),
        "config_hash": _sha256(config_file),
        "cert_hash": _sha256(home / CERT_NAME),
        "key_hash": _sha256(home / KEY_NAME),
        "thumbprint": thumbprint,
    }


# ---------- 启用 / 停用 / 续期 ----------


def enable(paths: Paths, settings: Settings) -> PhotoResult:
    home = paths.home_dir(settings)
    data, error = _read_config(paths, settings)
    if data is None:
        return PhotoResult(False, "config_unavailable", error)

    version = resolve_runtime(paths, settings).version
    if not supports_photo(version):
        return PhotoResult(
            False,
            "unsupported",
            f"当前 hoshimi 版本（{version or '未知'}）不支持照片上传，需要 0.0.8 及以上。",
        )

    for name in MANAGED_FILES:
        if (home / name).exists():
            return PhotoResult(
                False,
                "exists",
                f"已存在 {name}，拒绝覆盖。请先点「停用」或用「续期」换发证书。",
            )

    override = os.environ.get(ENV_OVERRIDE)
    if override not in (None, "", "1"):
        return PhotoResult(
            False,
            "env_override",
            f"环境变量 {ENV_OVERRIDE}={override} 会覆盖配置，请先移除该变量。",
        )

    config_file = config_path(paths, settings)
    try:
        original = config_file.read_bytes()
    except OSError as e:
        return PhotoResult(False, "config_unavailable", f"无法读取 config.json：{e}")

    material = _Material()
    created: list[Path] = []
    trust_added = False
    try:
        for name, payload in (
            (CERT_NAME, material.cert_pem),
            (KEY_NAME, material.key_pem),
        ):
            path = home / name
            _write_bytes(path, payload)
            created.append(path)

        data["enable_https"] = True
        if "https_port" not in data:
            data["https_port"] = DEFAULT_HTTPS_PORT
        _write_bytes(
            config_file,
            (json.dumps(data, ensure_ascii=False, indent=2) + "\n").encode("utf-8"),
        )

        state_file = home / STATE_NAME
        state = _state_payload(original, config_file, home, material.thumbprint)
        _write_bytes(state_file, json.dumps(state, ensure_ascii=False).encode("utf-8"))
        created.append(state_file)

        add_root_certificate(material.der)
        trust_added = True
        if not is_trusted(material.thumbprint):
            raise TrustError("证书已写入，但在根证书库中查不到（可能被安全策略拦截）")
    except (OSError, TrustError) as e:
        _rollback(
            home, config_file, original, created, material.thumbprint, trust_added
        )
        return PhotoResult(
            False,
            "trust_failed"
            if trust_added or isinstance(e, TrustError)
            else "write_failed",
            f"启用失败，已回滚：{e}",
        )

    port = data.get("https_port", DEFAULT_HTTPS_PORT)
    return PhotoResult(
        True,
        "ok",
        f"已启用同机照片 HTTPS（端口 {port}），证书指纹 {material.thumbprint[:16]}…。"
        "请重启服务并重新登录游戏。",
    )


def _rollback(
    home: Path,
    config_file: Path,
    original: bytes,
    created: list[Path],
    thumbprint: str,
    trust_added: bool,
) -> None:
    try:
        config_file.write_bytes(original)
    except OSError:
        pass
    for path in created:
        try:
            path.unlink()
        except OSError:
            pass
    if trust_added:
        try:
            remove_root_certificate(thumbprint)
        except (OSError, TrustError):
            pass


def _key_matches(cert_pem: bytes, key_pem: bytes) -> bool:
    """证书与私钥是否配对（公钥一致）。任一解析失败返回 False。"""
    try:
        cert = x509.load_pem_x509_certificate(cert_pem)
        key = serialization.load_pem_private_key(key_pem, password=None)
        cert_pub = cert.public_key()
        if not isinstance(cert_pub, rsa.RSAPublicKey):
            return False
        return key.public_key().public_numbers() == cert_pub.public_numbers()
    except (ValueError, TypeError, UnsupportedAlgorithm):
        return False


def adopt(
    paths: Paths, settings: Settings, remove_trust_on_disable: bool
) -> PhotoResult:
    """接管外部配置的证书（官方 ps1 无 state 时的手工配置场景）。

    以现有 cert.pem / key.pem 为准，不重新签发；把「当前 config 去掉
    enable_https」作为停用时的还原快照，再按现有文件哈希建立启用记录。
    根证书库未信任这张证书时会顺手加入（信任是我们加的，停用时必移除；
    已信任的则按 remove_trust_on_disable 决定停用时是否移除）。
    """
    home = paths.home_dir(settings)
    data, error = _read_config(paths, settings)
    if data is None:
        return PhotoResult(False, "config_unavailable", error)

    cert_file = home / CERT_NAME
    key_file = home / KEY_NAME
    if not cert_file.is_file() or not key_file.is_file():
        return PhotoResult(
            False,
            "missing_files",
            "home 目录下没有 cert.pem / key.pem，无法接管。"
            "这种证书路径不归本程序管理，请手动处理。",
        )
    if _read_state(paths, settings) is not None:
        return PhotoResult(
            False, "exists", "已存在启用记录，无需接管（可直接停用或续期）。"
        )

    try:
        cert_pem = cert_file.read_bytes()
        key_pem = key_file.read_bytes()
    except OSError as e:
        return PhotoResult(False, "missing_files", f"无法读取证书文件：{e}")
    if not _key_matches(cert_pem, key_pem):
        return PhotoResult(
            False,
            "key_mismatch",
            "cert.pem 与 key.pem 不配对（公钥不一致），拒绝接管。",
        )

    cert = x509.load_pem_x509_certificate(cert_pem)
    der = cert.public_bytes(serialization.Encoding.DER)
    thumbprint = hashlib.sha1(der).hexdigest().upper()

    override = os.environ.get(ENV_OVERRIDE)
    if override not in (None, "", "1"):
        return PhotoResult(
            False,
            "env_override",
            f"环境变量 {ENV_OVERRIDE}={override} 会覆盖配置，请先移除该变量。",
        )

    config_file = config_path(paths, settings)
    try:
        config_bytes = config_file.read_bytes()
    except OSError as e:
        return PhotoResult(False, "config_unavailable", f"无法读取 config.json：{e}")

    # 停用时的还原快照 = 当前内容去掉 enable_https（其余键原样保留）
    snapshot = dict(data)
    snapshot.pop("enable_https", None)
    snapshot_bytes = (json.dumps(snapshot, ensure_ascii=False, indent=2) + "\n").encode(
        "utf-8"
    )

    created: list[Path] = []
    trust_added = False
    try:
        data["enable_https"] = True
        if "https_port" not in data:
            data["https_port"] = DEFAULT_HTTPS_PORT
        _write_bytes(
            config_file,
            (json.dumps(data, ensure_ascii=False, indent=2) + "\n").encode("utf-8"),
        )

        state = _state_payload(snapshot_bytes, config_file, home, thumbprint)
        state["remove_trust_on_disable"] = bool(remove_trust_on_disable)
        state_file = home / STATE_NAME
        _write_bytes(state_file, json.dumps(state, ensure_ascii=False).encode("utf-8"))
        created.append(state_file)

        if not is_trusted(thumbprint):
            add_root_certificate(der)
            trust_added = True
            if not is_trusted(thumbprint):
                raise TrustError(
                    "证书已写入，但在根证书库中查不到（可能被安全策略拦截）"
                )
    except (OSError, TrustError) as e:
        _rollback(home, config_file, config_bytes, created, thumbprint, trust_added)
        return PhotoResult(
            False,
            "trust_failed"
            if trust_added or isinstance(e, TrustError)
            else "write_failed",
            f"接管失败，已回滚：{e}",
        )

    now = datetime.now(UTC)
    days_left = (cert.not_valid_after_utc - now).days
    trust_note = (
        "证书此前未受信任，已帮你加入根证书库。"
        if trust_added
        else "证书信任保持不变。"
    )
    return PhotoResult(
        True,
        "ok",
        f"已接管现有证书（剩余 {days_left} 天，指纹 {thumbprint[:16]}…），"
        f"{trust_note}请重启服务。",
    )


def disable(paths: Paths, settings: Settings) -> PhotoResult:
    home = paths.home_dir(settings)
    state = _read_state(paths, settings)
    if state is None:
        return PhotoResult(False, "not_enabled", "没有启用记录，无需停用。")

    intact, culprit = _hash_matches(paths, settings, state)
    if not intact:
        return PhotoResult(
            False,
            "modified",
            f"{culprit} 与启用时不一致，为避免覆盖你的改动，已停止自动还原。"
            "请手动处理这些文件。",
        )

    thumbprint = str(state.get("thumbprint", "")).upper()
    # 接管时用户可选「停用保留证书信任」；没这个键（本程序/官方 ps1 启用的）
    # 一律移除，维持原行为
    remove_trust = state.get("remove_trust_on_disable")
    remove_trust = True if remove_trust is None else bool(remove_trust)
    if remove_trust:
        try:
            remove_root_certificate(thumbprint)
        except (OSError, TrustError) as e:
            return PhotoResult(False, "trust_failed", f"移除根证书信任失败：{e}")

    try:
        original = base64.b64decode(state.get("original_config", ""), validate=True)
        config_path(paths, settings).write_bytes(original)
    except (ValueError, OSError) as e:
        return PhotoResult(False, "restore_failed", f"恢复原配置失败：{e}")

    for name in MANAGED_FILES:
        try:
            (home / name).unlink()
        except FileNotFoundError:
            continue
        except OSError as e:
            return PhotoResult(False, "cleanup_failed", f"删除 {name} 失败：{e}")

    trust_note = "" if remove_trust else "证书信任按接管时的选择保留在根证书库。"
    return PhotoResult(
        True,
        "ok",
        f"已停用，原配置已还原。{trust_note}请重启服务。",
    )


def renew(paths: Paths, settings: Settings) -> PhotoResult:
    """先恢复再重新签发：官方要求证书过期后必须按这个顺序来。"""
    result = disable(paths, settings)
    if not result.ok:
        return result
    again = enable(paths, settings)
    if not again.ok:
        return PhotoResult(
            False,
            again.code,
            f"旧证书已移除，但重新签发失败：{again.message}",
        )
    return PhotoResult(True, "ok", "证书已换发，请重启服务并重新登录游戏。")

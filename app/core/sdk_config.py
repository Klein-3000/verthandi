from __future__ import annotations

import base64
import json
import math
from pathlib import Path
from urllib.parse import urlparse

from Crypto.Cipher import AES

AES_KEY = b"ys4fun" + b"0" * 26
SDK_ENTRIES = (
    {"name": "ysmix_config", "key": "YS_MIX_SDK_URL", "path": "mix-sdk-api/"},
    {"name": "ys_config", "key": "YS4FUN_SDK_URL", "path": "sdk-api/"},
)


class SdkConfigError(Exception):
    """SDK 配置操作失败。code 用于上层分类。"""

    def __init__(self, code: str, message: str):
        self.code = code
        self.message = message
        super().__init__(message)


def is_loopback(host: str) -> bool:
    if not host:
        return False
    h = host.lower()
    if h.startswith("[") and h.endswith("]"):
        h = h[1:-1]
    return h in ("localhost", "127.0.0.1", "::1")


def _aes_encrypt(data: bytes) -> bytes:
    return AES.new(AES_KEY, AES.MODE_ECB).encrypt(data)


def _aes_decrypt(data: bytes) -> bytes:
    return AES.new(AES_KEY, AES.MODE_ECB).decrypt(data)


def _pkcs7_pad(data: bytes) -> bytes:
    pad = 16 - (len(data) % 16)
    return data + bytes([pad]) * pad


def _pkcs7_unpad(data: bytes) -> bytes:
    if not data:
        raise SdkConfigError("empty_plain", "SDK 密文解密后为空")
    pad = data[-1]
    if pad < 1 or pad > 16 or pad > len(data):
        raise SdkConfigError("bad_padding", "SDK 密文 PKCS7 填充无效")
    return data[:-pad]


def _json_compress(obj) -> str:
    return json.dumps(obj, separators=(",", ":"), ensure_ascii=False)


def get_sdk_block(data: bytes, name: str) -> dict:
    binary_text = data.decode("latin-1")
    name_offset = binary_text.find(name)
    if name_offset < 4:
        raise SdkConfigError("not_found", f"无法定位 SDK 配置：{name}")
    if binary_text.find(name, name_offset + len(name)) >= 0:
        raise SdkConfigError("not_unique", f"SDK 配置出现多次：{name}")

    name_len_field = int.from_bytes(data[name_offset - 4 : name_offset], "little")
    if name_len_field != len(name):
        raise SdkConfigError("bad_name_len", f"SDK 配置名称长度异常：{name}")

    aligned = math.ceil(len(name) / 4) * 4
    length_offset = name_offset + aligned
    length = int.from_bytes(data[length_offset : length_offset + 4], "little")
    offset = length_offset + 4

    if length <= 0 or length > 65536 or offset + length > len(data):
        raise SdkConfigError("bad_length", f"SDK 配置数据长度异常：{name}")

    encoded = data[offset : offset + length].decode("ascii")
    ciphertext = base64.b64decode(encoded)
    plain = _pkcs7_unpad(_aes_decrypt(ciphertext))
    try:
        config = json.loads(plain.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as e:
        raise SdkConfigError("bad_json", f"SDK 配置 JSON 解析失败：{name}: {e}")

    return {
        "offset": offset,
        "length": length,
        "encoded": encoded,
        "cipher_length": len(ciphertext),
        "config": config,
    }


def set_sdk_endpoints(
    resources_path: Path,
    target_mode: str,
    port: int,
    backup_dir: Path,
) -> None:
    """将 resources.assets 里的 SDK 端点切换到 Local / Official。

    保持文件长度不变，其他字节不动。
    """
    data = bytearray(resources_path.read_bytes())
    backup_dir.mkdir(parents=True, exist_ok=True)

    for entry in SDK_ENTRIES:
        name = entry["name"]
        key = entry["key"]
        api_path = entry["path"]

        block = get_sdk_block(bytes(data), name)
        config = block["config"]

        if "prod" not in config or key not in config["prod"]:
            raise SdkConfigError("missing_prod", f"SDK 配置缺少 prod.{key}：{name}")

        current_url = str(config["prod"][key])
        parsed = urlparse(current_url)
        current_is_https = parsed.scheme.lower() == "https"
        current_is_loopback = is_loopback(parsed.hostname or "")

        backup_path = backup_dir / f"{name}.sdk.bak"

        # 已存在备份时，先做版本校验
        if backup_path.exists():
            encoded = backup_path.read_text(encoding="ascii")
            backup_bytes = encoded.encode("ascii")
            if len(backup_bytes) != block["length"]:
                raise SdkConfigError(
                    "backup_version_mismatch",
                    "SDK 备份版本不匹配，请用启动器修复客户端后重新初始化。",
                )

            check = bytearray(data)
            check[block["offset"] : block["offset"] + len(backup_bytes)] = backup_bytes
            original = get_sdk_block(bytes(check), name)["config"]
            if "prod" not in original:
                raise SdkConfigError(
                    "backup_missing_prod", f"SDK 备份缺少 prod：{name}"
                )

            # 结构化比较：将当前 prod[key] 对齐到原始后直接比 dict
            original["prod"][key] = config["prod"][key]
            if original != config:
                raise SdkConfigError(
                    "sdk_config_updated",
                    "SDK 配置已更新，请用启动器修复客户端并选择新的备份目录。",
                )

        if target_mode == "Local":
            if not backup_path.exists():
                if not current_is_https or current_is_loopback:
                    raise SdkConfigError(
                        "missing_backup",
                        f"缺少 SDK 官服备份且当前不是官服地址：{name}",
                    )
                backup_path.write_text(block["encoded"], encoding="ascii")

            config["prod"][key] = f"http://127.0.0.1:{port}/{api_path}"
            plain = _json_compress(config).encode("utf-8")
            target_length = block["cipher_length"] - 16
            if len(plain) > target_length:
                raise SdkConfigError(
                    "config_too_long",
                    f"SDK 新配置超过原空间：{name}",
                )

            padded = plain + b"\x20" * (target_length - len(plain))
            padded = _pkcs7_pad(padded)
            if len(padded) != block["cipher_length"]:
                raise SdkConfigError(
                    "padded_length_mismatch",
                    f"SDK 填充后长度异常：{name}",
                )
            encoded = base64.b64encode(_aes_encrypt(padded)).decode("ascii")

        elif backup_path.exists():
            pass  # 用备份的 encoded 恢复

        elif current_is_https and not current_is_loopback:
            continue  # 无备份、已是官服地址：无需改动

        else:
            raise SdkConfigError(
                "missing_backup",
                f"缺少 SDK 官服备份：{name}",
            )

        updated = encoded.encode("ascii")
        if len(updated) != block["length"]:
            raise SdkConfigError("encoded_length_mismatch", f"SDK 编码长度变化：{name}")
        data[block["offset"] : block["offset"] + len(updated)] = updated

    resources_path.write_bytes(bytes(data))

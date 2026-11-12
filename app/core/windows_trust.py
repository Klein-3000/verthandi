"""Windows「当前用户」的根证书库证书库的最小读写封装。

只做三件事：按 SHA1 指纹查、把 DER 证书加进去、按指纹删掉。

优先走 crypt32 的 API——不起子进程、不用解析本地化的控制台输出；
拿不到句柄或调用失败时退回 `certutil`（输出用系统代码页解码）。

注意：往根证书库写入时，Windows 可能弹出「是否信任此证书颁发机构」的
安全提示，这是系统策略，程序无法绕过。调用方必须放到后台线程里执行，
否则会卡住界面。
"""

from __future__ import annotations

import ctypes
import subprocess
import tempfile
from ctypes import wintypes
from pathlib import Path

__all__ = [
    "TrustError",
    "add_root_certificate",
    "is_trusted",
    "remove_root_certificate",
]

CERT_SYSTEM_STORE_CURRENT_USER = 0x00010000
CERT_STORE_ADD_REPLACE_EXISTING = 3
CERT_FIND_HASH = 0x00010000
X509_ASN_ENCODING = 0x00000001
PKCS_7_ASN_ENCODING = 0x00010000
ENCODING_TYPE = X509_ASN_ENCODING | PKCS_7_ASN_ENCODING
ROOT_STORE_NAME = "Root"

# certutil 在 Windows 上按本地代码页输出（中文系统为 cp936）
_MBCS = "mbcs"


class TrustError(Exception):
    """证书库操作失败。"""


class _HashBlob(ctypes.Structure):
    _fields_ = [
        ("cbData", wintypes.DWORD),
        ("pbData", ctypes.POINTER(ctypes.c_ubyte)),
    ]


def _crypt32() -> ctypes.WinDLL:
    lib = ctypes.WinDLL("crypt32", use_last_error=True)
    lib.CertOpenStore.restype = wintypes.HANDLE
    lib.CertOpenStore.argtypes = [
        ctypes.c_char_p,
        wintypes.DWORD,
        wintypes.HANDLE,
        wintypes.DWORD,
        ctypes.c_void_p,
    ]
    lib.CertCloseStore.restype = wintypes.BOOL
    lib.CertCloseStore.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    lib.CertAddEncodedCertificateToStore.restype = wintypes.BOOL
    lib.CertAddEncodedCertificateToStore.argtypes = [
        wintypes.HANDLE,
        wintypes.DWORD,
        ctypes.c_char_p,
        wintypes.DWORD,
        wintypes.DWORD,
        ctypes.c_void_p,
    ]
    lib.CertFindCertificateInStore.restype = ctypes.c_void_p
    lib.CertFindCertificateInStore.argtypes = [
        wintypes.HANDLE,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.DWORD,
        ctypes.c_void_p,
        ctypes.c_void_p,
    ]
    lib.CertDeleteCertificateFromStore.restype = wintypes.BOOL
    lib.CertDeleteCertificateFromStore.argtypes = [ctypes.c_void_p]
    lib.CertFreeCertificateContext.restype = wintypes.BOOL
    lib.CertFreeCertificateContext.argtypes = [ctypes.c_void_p]
    return lib


def _open_store(lib: ctypes.WinDLL):
    handle = lib.CertOpenStore(
        b"System",
        0,
        None,
        CERT_SYSTEM_STORE_CURRENT_USER,
        ctypes.c_wchar_p(ROOT_STORE_NAME),
    )
    if not handle:
        raise TrustError(
            f"无法打开当前用户的根证书库（错误码 {ctypes.get_last_error()}）"
        )
    return handle


def _find(lib: ctypes.WinDLL, store, thumbprint: str):
    try:
        raw = bytes.fromhex(thumbprint)
    except ValueError as e:
        raise TrustError(f"证书指纹格式不对：{thumbprint}") from e
    if not raw:
        return None
    buf = (ctypes.c_ubyte * len(raw)).from_buffer_copy(raw)
    blob = _HashBlob(len(raw), buf)
    return lib.CertFindCertificateInStore(
        store, ENCODING_TYPE, 0, CERT_FIND_HASH, ctypes.byref(blob), None
    )


def _run_certutil(args: list[str]) -> tuple[int, str]:
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    try:
        proc = subprocess.run(
            ["certutil", *args],
            capture_output=True,
            creationflags=flags,
            check=False,
        )
    except OSError as e:
        raise TrustError(f"无法执行 certutil：{e}") from e
    output = (proc.stdout or b"") + (proc.stderr or b"")
    return proc.returncode, output.decode(_MBCS, "replace").strip()


def is_trusted(thumbprint: str) -> bool:
    """该指纹是否确实在当前用户的根证书库里。"""
    if not thumbprint:
        return False
    try:
        lib = _crypt32()
        store = _open_store(lib)
    except (OSError, TrustError):
        # 打不开库时退回 certutil：按指纹查询，命中才会在输出里出现该指纹
        _code, out = _run_certutil(["-user", "-store", ROOT_STORE_NAME, thumbprint])
        return thumbprint.lower() in out.replace(" ", "").lower()
    try:
        ctx = _find(lib, store, thumbprint)
        if ctx:
            lib.CertFreeCertificateContext(ctx)
        return bool(ctx)
    finally:
        lib.CertCloseStore(store, 0)


def add_root_certificate(der: bytes) -> None:
    """把 DER 编码的自签证书加入当前用户根库。

    可能弹出 Windows 安全提示并在用户点击前阻塞，调用方需在后台线程执行。
    """
    if not der:
        raise TrustError("证书内容为空")
    try:
        lib = _crypt32()
        store = _open_store(lib)
    except (OSError, TrustError):
        _add_via_certutil(der)
        return
    try:
        ok = lib.CertAddEncodedCertificateToStore(
            store, ENCODING_TYPE, der, len(der), CERT_STORE_ADD_REPLACE_EXISTING, None
        )
        if not ok:
            error = ctypes.get_last_error()
            raise TrustError(f"写入根证书库失败（错误码 {error}）")
    finally:
        lib.CertCloseStore(store, 0)


def _add_via_certutil(der: bytes) -> None:
    with tempfile.TemporaryDirectory(prefix="verthandi-cert-") as tmp:
        path = Path(tmp) / "cert.cer"
        path.write_bytes(der)
        code, output = _run_certutil(
            ["-f", "-addstore", "-user", ROOT_STORE_NAME, str(path)]
        )
    if code != 0:
        raise TrustError(f" certutil 写入证书库失败（{code}）：{output[-200:]}")


def remove_root_certificate(thumbprint: str) -> bool:
    """按指纹从当前用户根库删除，返回是否确实删掉了。

    找不到不算错误（用户可能已手动删过）。
    """
    if not thumbprint:
        return False
    try:
        lib = _crypt32()
        store = _open_store(lib)
    except (OSError, TrustError):
        code, _out = _run_certutil(["-user", "-delstore", ROOT_STORE_NAME, thumbprint])
        return code == 0
    try:
        ctx = _find(lib, store, thumbprint)
        if not ctx:
            return False
        return bool(lib.CertDeleteCertificateFromStore(ctx))
    finally:
        lib.CertCloseStore(store, 0)

"""Machine-scope Windows DPAPI storage for local service credentials."""

import ctypes
import os
import tempfile
from ctypes import wintypes
from pathlib import Path
from typing import Protocol

CRYPTPROTECT_UI_FORBIDDEN = 0x1
CRYPTPROTECT_LOCAL_MACHINE = 0x4


class CredentialStoreError(RuntimeError):
    """Raised when a credential cannot be stored or recovered safely."""


class Protector(Protocol):
    def protect(self, value: bytes) -> bytes: ...

    def unprotect(self, value: bytes) -> bytes: ...


class _DataBlob(ctypes.Structure):
    _fields_ = [
        ("cbData", wintypes.DWORD),
        ("pbData", ctypes.POINTER(ctypes.c_ubyte)),
    ]


class WindowsDpapiProtector:
    """Encrypt credentials for every account on this Windows machine."""

    def protect(self, value: bytes) -> bytes:
        return _crypt_protect(
            value,
            CRYPTPROTECT_LOCAL_MACHINE | CRYPTPROTECT_UI_FORBIDDEN,
        )

    def unprotect(self, value: bytes) -> bytes:
        return _crypt_unprotect(value, CRYPTPROTECT_UI_FORBIDDEN)


class CredentialStore:
    def __init__(self, path: Path, protector: Protector | None = None):
        self.path = Path(path)
        self.protector = protector or WindowsDpapiProtector()

    @classmethod
    def default(cls) -> "CredentialStore":
        base_dir = Path(__file__).resolve().parents[1]
        return cls(
            base_dir
            / "local"
            / "secrets"
            / "wechat_explorer_token.dpapi"
        )

    @classmethod
    def model_api_key(cls) -> "CredentialStore":
        base_dir = Path(__file__).resolve().parents[1]
        return cls(base_dir / "local" / "secrets" / "model_api_key.dpapi")

    def save(self, token: str) -> None:
        normalized = token.strip()
        if not normalized:
            raise CredentialStoreError("WechatExplorer Token 不能为空")
        try:
            encrypted = self.protector.protect(normalized.encode("utf-8"))
        except Exception as exc:
            raise CredentialStoreError(
                "无法使用 Windows DPAPI 加密 WechatExplorer Token"
            ) from exc
        if not encrypted:
            raise CredentialStoreError("Windows DPAPI 返回了空密文")
        _atomic_write(self.path, encrypted)

    def load(self) -> str | None:
        if not self.path.exists():
            return None
        try:
            return _decrypt_credential(self.path, self.protector)
        except Exception as exc:
            raise CredentialStoreError(
                "无法解密已保存的 WechatExplorer Token；请重新保存凭据"
            ) from exc

    def delete(self) -> None:
        try:
            self.path.unlink(missing_ok=True)
        except OSError as exc:
            raise CredentialStoreError("无法删除已保存的加密凭据") from exc


def load_persisted_token() -> str | None:
    return CredentialStore.default().load()


def _decrypt_credential(path: Path, protector: Protector) -> str:
    encrypted = path.read_bytes()
    if not encrypted:
        raise ValueError("empty encrypted blob")
    token = protector.unprotect(encrypted).decode("utf-8")
    if not token.strip():
        raise ValueError("empty decrypted credential")
    return token.strip()


def _atomic_write(path: Path, value: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix="credential-", suffix=".tmp", dir=path.parent
    )
    temporary_path = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as output:
            output.write(value)
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary_path, path)
    except Exception as exc:
        temporary_path.unlink(missing_ok=True)
        raise CredentialStoreError("无法保存加密的 WechatExplorer Token") from exc


def _input_blob(value: bytes) -> tuple[_DataBlob, ctypes.Array]:
    buffer = ctypes.create_string_buffer(value, len(value))
    blob = _DataBlob(
        len(value),
        ctypes.cast(buffer, ctypes.POINTER(ctypes.c_ubyte)),
    )
    return blob, buffer


def _crypt_protect(value: bytes, flags: int) -> bytes:
    if os.name != "nt":
        raise CredentialStoreError("DPAPI 凭据持久化仅支持 Windows")
    input_blob, input_buffer = _input_blob(value)
    output_blob = _DataBlob()
    crypt32 = ctypes.WinDLL("crypt32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    result = crypt32.CryptProtectData(
        ctypes.byref(input_blob),
        None,
        None,
        None,
        None,
        flags,
        ctypes.byref(output_blob),
    )
    _ = input_buffer
    if not result:
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        return ctypes.string_at(output_blob.pbData, output_blob.cbData)
    finally:
        kernel32.LocalFree(output_blob.pbData)


def _crypt_unprotect(value: bytes, flags: int) -> bytes:
    if os.name != "nt":
        raise CredentialStoreError("DPAPI 凭据持久化仅支持 Windows")
    input_blob, input_buffer = _input_blob(value)
    output_blob = _DataBlob()
    crypt32 = ctypes.WinDLL("crypt32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    result = crypt32.CryptUnprotectData(
        ctypes.byref(input_blob),
        None,
        None,
        None,
        None,
        flags,
        ctypes.byref(output_blob),
    )
    _ = input_buffer
    if not result:
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        return ctypes.string_at(output_blob.pbData, output_blob.cbData)
    finally:
        kernel32.LocalFree(output_blob.pbData)

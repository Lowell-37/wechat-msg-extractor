"""Database adapters for legacy and modern WeChat message stores."""

from pathlib import Path
from typing import Protocol

from .modern import (
    AutomaticModernKeyProvider,
    ManualModernKeyProvider,
    ModernKeyError,
    ModernKeyProvider,
    ModernMessageDatabase,
    parse_modern_key,
)


class DatabaseAdapter(Protocol):
    kind: str

    def close(self) -> None: ...


class LegacyDatabaseAdapter:
    """Descriptor for an MSG database; legacy decryption remains in dbutils."""

    kind = "legacy"

    def __init__(self, path: str | Path, key: str | bytes):
        self.original_path = str(path)
        self.key_hex = key.hex() if isinstance(key, bytes) else str(key)

    def close(self) -> None:
        """Legacy lifecycle is owned by the existing ``WeChatDB`` manager."""


def create_database_adapter(
    path: str | Path,
    key: str | bytes,
    *,
    temp_dir: str | Path | None = None,
) -> DatabaseAdapter:
    """Select an adapter from the database filename without mutating it."""
    database_path = Path(path)
    name = database_path.name.lower()
    if name.startswith("message_") and name.endswith(".db"):
        return ModernMessageDatabase(database_path, key, temp_dir=temp_dir, lazy=True)
    if name.startswith("msg") and name.endswith(".db"):
        return LegacyDatabaseAdapter(database_path, key)
    raise ValueError(
        "无法识别微信数据库文件名；需要 MSG*.db 或 message_*.db"
    )


__all__ = [
    "AutomaticModernKeyProvider",
    "DatabaseAdapter",
    "LegacyDatabaseAdapter",
    "ManualModernKeyProvider",
    "ModernKeyError",
    "ModernKeyProvider",
    "ModernMessageDatabase",
    "create_database_adapter",
    "parse_modern_key",
]

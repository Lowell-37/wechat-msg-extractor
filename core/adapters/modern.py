"""WeChat 4 message database support with explicit manual-key boundaries.

The modern ``message_*.db`` files use SQLCipher 4 page layout. Key discovery
is intentionally not performed here: callers must provide a 32-byte raw key.
"""

import hashlib
import hmac
import os
import re
import sqlite3
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from Cryptodome.Cipher import AES

MODERN_PAGE_SIZE = 4096
MODERN_RESERVE_SIZE = 80
MODERN_SALT_SIZE = 16
MODERN_KDF_ITERATIONS = 256_000
SQLITE_FILE_HEADER = b"SQLite format 3\x00"


class ModernKeyError(ValueError):
    """Raised when a modern database key is absent or malformed."""


class ModernKeyProvider(Protocol):
    def get_key(self) -> bytes: ...


def parse_modern_key(value: str | bytes | bytearray) -> bytes:
    """Parse a manually supplied modern key into exactly 32 raw bytes.

    Accepted text forms are a bare 64-hex key, ``0x``-prefixed key, and the
    explicit ``sqlcipher4:`` prefix. Automatic process-memory extraction is
    deliberately outside this parser.
    """
    if isinstance(value, (bytes, bytearray)):
        raw_key = bytes(value)
        if len(raw_key) == 32:
            return raw_key
        raise ModernKeyError("新版数据库密钥必须是 32 字节或 64 位十六进制字符串")
    if not isinstance(value, str):
        raise ModernKeyError("新版数据库密钥必须是 32 字节或 64 位十六进制字符串")

    normalized = value.strip()
    for prefix in ("sqlcipher4:", "sqlcipher4=", "key:", "key="):
        if normalized.lower().startswith(prefix):
            normalized = normalized[len(prefix) :].strip()
            break
    if normalized.lower().startswith("0x"):
        normalized = normalized[2:]
    normalized = re.sub(r"[\s:-]", "", normalized).lower()
    if not re.fullmatch(r"[0-9a-f]{64}", normalized):
        raise ModernKeyError("新版数据库密钥必须是 32 字节或 64 位十六进制字符串")
    return bytes.fromhex(normalized)


class ManualModernKeyProvider:
    """Provider for an explicitly entered modern raw key."""

    def __init__(self, value: str | bytes | bytearray):
        self._key = parse_modern_key(value)

    def get_key(self) -> bytes:
        return self._key


class AutomaticModernKeyProvider:
    """Explicit placeholder for the not-yet-implemented automatic flow."""

    def get_key(self) -> bytes:
        raise ModernKeyError(
            "新版微信自动提钥尚未实现；请手动提供 32 字节 raw key"
        )


def _derive_modern_keys(raw_key: bytes, salt: bytes) -> tuple[bytes, bytes]:
    derived_key = hashlib.pbkdf2_hmac(
        "sha512", raw_key, salt, MODERN_KDF_ITERATIONS, 32
    )
    mac_key = hashlib.pbkdf2_hmac(
        "sha512",
        derived_key,
        bytes(value ^ 0x3A for value in salt),
        2,
        32,
    )
    return derived_key, mac_key


def decrypt_sqlcipher4(
    source_path: str | Path,
    output_path: str | Path,
    raw_key: str | bytes | bytearray,
) -> bool:
    """Decrypt a SQLCipher 4 database into a temporary SQLite file.

    Returns ``False`` for authentication failure and never modifies the source
    file. The caller owns cleanup of ``output_path`` when this returns true.
    """
    key = parse_modern_key(raw_key)
    encrypted = Path(source_path).read_bytes()
    if len(encrypted) < MODERN_SALT_SIZE + MODERN_PAGE_SIZE - MODERN_RESERVE_SIZE:
        return False
    salt = encrypted[:MODERN_SALT_SIZE]
    derived_key, mac_key = _derive_modern_keys(key, salt)
    offset = MODERN_SALT_SIZE
    page_number = 1
    plaintext = bytearray()
    while offset < len(encrypted):
        body_size = (
            MODERN_PAGE_SIZE - MODERN_RESERVE_SIZE - MODERN_SALT_SIZE
            if page_number == 1
            else MODERN_PAGE_SIZE - MODERN_RESERVE_SIZE
        )
        chunk_size = body_size + MODERN_RESERVE_SIZE
        chunk = encrypted[offset : offset + chunk_size]
        if len(chunk) != chunk_size:
            return False
        ciphertext = chunk[:body_size]
        iv = chunk[body_size : body_size + 16]
        expected_mac = chunk[body_size + 16 : body_size + MODERN_RESERVE_SIZE]
        actual_mac = hmac.new(
            mac_key,
            ciphertext + iv + page_number.to_bytes(4, "big"),
            hashlib.sha512,
        ).digest()
        if not hmac.compare_digest(actual_mac, expected_mac):
            return False
        try:
            decrypted_body = AES.new(derived_key, AES.MODE_CBC, iv).decrypt(
                ciphertext
            )
        except ValueError:
            return False
        if page_number == 1:
            plaintext.extend(SQLITE_FILE_HEADER)
        plaintext.extend(decrypted_body)
        plaintext.extend(bytes(MODERN_RESERVE_SIZE))
        offset += chunk_size
        page_number += 1

    Path(output_path).write_bytes(plaintext)
    return True


@dataclass(frozen=True)
class _ModernSchema:
    message_table: str
    message_id: str
    message_talker: str
    message_time: str
    message_content: str
    conversation_table: str | None = None
    conversation_id: str | None = None
    conversation_name: str | None = None
    conversation_time: str | None = None
    conversation_count: str | None = None


def _identifier(value: str) -> str:
    return '"' + value.replace('"', '""') + '"'


def _tables(connection: sqlite3.Connection) -> dict[str, set[str]]:
    result: dict[str, set[str]] = {}
    for (table_name,) in connection.execute(
        "SELECT name FROM sqlite_master WHERE type='table'"
    ):
        columns = {
            row[1]
            for row in connection.execute(
                f"PRAGMA table_info({_identifier(table_name)})"
            )
        }
        result[table_name] = columns
    return result


def _pick(columns: set[str], names: tuple[str, ...]) -> str | None:
    by_lower = {column.lower(): column for column in columns}
    return next((by_lower[name.lower()] for name in names if name.lower() in by_lower), None)


def _probe_schema(connection: sqlite3.Connection) -> _ModernSchema:
    tables = _tables(connection)
    message_table = next(
        (
            table
            for table in tables
            if table.lower() in {"message", "messages", "msg"}
        ),
        None,
    )
    if message_table is None:
        raise RuntimeError("新版数据库缺少 message 表")
    columns = tables[message_table]
    required = {
        "id": _pick(columns, ("id", "msg_id", "local_id", "localid")),
        "talker": _pick(
            columns,
            ("conversation_id", "talker", "str_talker", "username", "chat_id"),
        ),
        "time": _pick(
            columns, ("create_time", "timestamp", "created_at", "createtime", "ts")
        ),
        "content": _pick(columns, ("content", "text", "str_content", "body", "message")),
    }
    if any(value is None for value in required.values()):
        raise RuntimeError("新版 message 表缺少统一查询所需字段")

    conversation_table = next(
        (
            table
            for table in tables
            if table.lower() in {"conversation", "conversations", "chatroom", "contact"}
        ),
        None,
    )
    conversation_fields: dict[str, str | None] = {}
    if conversation_table:
        conversation_columns = tables[conversation_table]
        conversation_fields = {
            "id": _pick(conversation_columns, ("username", "conversation_id", "talker", "chat_id")),
            "name": _pick(conversation_columns, ("display_name", "nickname", "name", "nick_name")),
            "time": _pick(
                conversation_columns,
                ("last_message_time", "last_time", "updated_at", "time"),
            ),
            "count": _pick(conversation_columns, ("message_count", "count", "msg_count")),
        }
        if not conversation_fields["id"] or not conversation_fields["name"]:
            conversation_table = None
    return _ModernSchema(
        message_table,
        required["id"],
        required["talker"],
        required["time"],
        required["content"],
        conversation_table,
        conversation_fields.get("id"),
        conversation_fields.get("name"),
        conversation_fields.get("time"),
        conversation_fields.get("count"),
    )


class ModernMessageDatabase:
    """Modern message DB exposing the old adapter's query surface."""

    kind = "modern"

    def __init__(
        self,
        source_path: str | Path,
        raw_key: str | bytes | bytearray,
        *,
        temp_dir: str | Path | None = None,
        lazy: bool = False,
    ):
        self.original_path = str(source_path)
        self._key = parse_modern_key(raw_key)
        self._temp_dir = temp_dir
        self.temp_path: str | None = None
        self.conn: sqlite3.Connection | None = None
        self._schema: _ModernSchema | None = None
        if lazy:
            return
        self._open()

    def _open(self) -> None:
        fd, temporary_path = tempfile.mkstemp(
            prefix="wechat-modern-", suffix=".db", dir=self._temp_dir
        )
        os.close(fd)
        self.temp_path = temporary_path
        try:
            if not decrypt_sqlcipher4(self.original_path, temporary_path, self._key):
                raise ModernKeyError(  # noqa: TRY301
                    "新版数据库校验失败，请确认 raw key 正确"
                )
            self.conn = sqlite3.connect(temporary_path)
            self.conn.row_factory = sqlite3.Row
            self._schema = _probe_schema(self.conn)
        except Exception:
            if self.conn is not None:
                self.conn.close()
                self.conn = None
            try:
                os.unlink(temporary_path)
            except OSError:
                pass
            self.temp_path = None
            raise

    @property
    def key_hex(self) -> str:
        return self._key.hex()

    def query_chatrooms(self) -> list[tuple[str, str, int | None, int]]:
        self._ensure_open()
        schema = self._schema
        if schema.conversation_table:
            table = _identifier(schema.conversation_table)
            columns = [
                _identifier(schema.conversation_id),
                _identifier(schema.conversation_name),
            ]
            columns.extend(
                [
                    _identifier(schema.conversation_time)
                    if schema.conversation_time
                    else "NULL",
                    _identifier(schema.conversation_count)
                    if schema.conversation_count
                    else "NULL",
                ]
            )
            rows = self.conn.execute(
                f"SELECT {', '.join(columns)} FROM {table}"  # noqa: S608
            ).fetchall()
            return [
                (row[0], row[1], row[2], int(row[3] or 0)) for row in rows if row[0]
            ]
        table = _identifier(schema.message_table)
        talker = _identifier(schema.message_talker)
        time_column = _identifier(schema.message_time)
        rows = self.conn.execute(
            f"SELECT {talker}, MAX({time_column}), COUNT(*) FROM {table} "  # noqa: S608
            f"GROUP BY {talker}"
        ).fetchall()
        return [(row[0], row[0], row[1], int(row[2])) for row in rows if row[0]]

    def query_messages(
        self, group: str, start_timestamp: int, end_timestamp: int
    ) -> list[dict[str, Any]]:
        self._ensure_open()
        schema = self._schema
        rows = self.conn.execute(
            f"SELECT {_identifier(schema.message_id)} AS localId, "  # noqa: S608
            f"{_identifier(schema.message_time)} AS CreateTime, "
            f"{_identifier(schema.message_content)} AS StrContent, "
            f"{_identifier(schema.message_talker)} AS StrTalker "
            f"FROM {_identifier(schema.message_table)} "
            f"WHERE {_identifier(schema.message_talker)}=? "
            f"AND {_identifier(schema.message_time)}>=? "
            f"AND {_identifier(schema.message_time)}<=? "
            f"ORDER BY {_identifier(schema.message_time)}",
            (group, start_timestamp, end_timestamp),
        ).fetchall()
        return [dict(row) for row in rows]

    def execute(self, sql: str, params: tuple = ()) -> list[Any]:
        self._ensure_open()
        normalized = " ".join(sql.lower().split())
        if " from msg " in f" {normalized} ":
            if "group by" in normalized or "count(*)" in normalized:
                return [
                    {
                        "StrTalker": row[0],
                        "LastMessageAt": row[2],
                        "MessageCount": row[3],
                    }
                    for row in self.query_chatrooms()
                ]
            group, start_timestamp, end_timestamp = params
            return self.query_messages(group, start_timestamp, end_timestamp)
        return [dict(row) for row in self.conn.execute(sql, params).fetchall()]

    def execute_per_shard(self, sql: str, params: tuple = ()) -> list[list[Any]]:
        return [self.execute(sql, params)]

    def execute_all(self, sql: str, params: tuple = ()) -> list[Any]:
        return self.execute(sql, params)

    def close(self) -> None:
        if self.conn is not None:
            self.conn.close()
        if self.temp_path:
            try:
                os.unlink(self.temp_path)
            except OSError:
                pass
            self.temp_path = None

    def close_all(self) -> None:
        self.close()

    def _ensure_open(self) -> None:
        if self.conn is None or self._schema is None:
            self._open()

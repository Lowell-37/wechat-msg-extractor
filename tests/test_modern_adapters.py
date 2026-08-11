import hashlib
import hmac
import sqlite3
from pathlib import Path

import pytest
from Cryptodome.Cipher import AES

from core.adapters import (
    AutomaticModernKeyProvider,
    ModernKeyError,
    ModernMessageDatabase,
    create_database_adapter,
    parse_modern_key,
)
from core.adapters.modern import MODERN_PAGE_SIZE, decrypt_sqlcipher4


def test_modern_key_parser_accepts_prefixed_hex_and_raw_bytes():
    raw_key = bytes(range(32))

    assert parse_modern_key(raw_key) == raw_key
    assert parse_modern_key("sqlcipher4:" + raw_key.hex()) == raw_key
    assert parse_modern_key("0x" + raw_key.hex()) == raw_key


def test_modern_key_parser_rejects_automatic_extraction_inputs():
    with pytest.raises(ModernKeyError, match=r"32 字节|64 位"):
        parse_modern_key("not-a-key")


def test_automatic_modern_key_provider_is_explicitly_unavailable():
    with pytest.raises(ModernKeyError, match="自动提钥尚未实现"):
        AutomaticModernKeyProvider().get_key()


def test_factory_routes_message_and_legacy_database_names(tmp_path):
    modern_path = tmp_path / "message_0.db"
    legacy_path = tmp_path / "MSG0.db"
    modern_path.touch()
    legacy_path.touch()

    modern = create_database_adapter(modern_path, bytes(32))
    legacy = create_database_adapter(legacy_path, "00" * 32)

    assert isinstance(modern, ModernMessageDatabase)
    assert legacy.kind == "legacy"


def test_sqlcipher4_decrypts_synthetic_database(tmp_path):
    plain_path = tmp_path / "plain.db"
    encrypted_path = tmp_path / "message_0.db"
    output_path = tmp_path / "decrypted.db"
    _create_plaintext_database(plain_path)
    raw_key = bytes(range(32))
    _encrypt_sqlcipher4(plain_path, encrypted_path, raw_key)

    assert decrypt_sqlcipher4(encrypted_path, output_path, raw_key) is True

    connection = sqlite3.connect(output_path)
    assert connection.execute("SELECT content FROM message").fetchone() == (
        "合成现代消息",
    )
    connection.close()


def test_modern_message_database_adapts_chatrooms_and_messages(tmp_path):
    plain_path = tmp_path / "plain.db"
    encrypted_path = tmp_path / "message_0.db"
    _create_plaintext_database(plain_path)
    raw_key = bytes(range(32))
    _encrypt_sqlcipher4(plain_path, encrypted_path, raw_key)

    database = ModernMessageDatabase(encrypted_path, raw_key, temp_dir=tmp_path)
    try:
        chatrooms = database.query_chatrooms()
        messages = database.query_messages(
            "room@chatroom", 0, 2_000_000_000
        )
    finally:
        database.close()

    assert chatrooms == [("room@chatroom", "项目群", 1_700_000_000, 1)]
    assert messages[0]["StrTalker"] == "room@chatroom"
    assert messages[0]["StrContent"] == "合成现代消息"


def _create_plaintext_database(path: Path):
    connection = sqlite3.connect(path)
    connection.execute("PRAGMA page_size=4096")
    connection.execute("PRAGMA auto_vacuum=0")
    connection.execute(
        "CREATE TABLE message (id INTEGER, talker TEXT, ts INTEGER, content TEXT)"
    )
    connection.execute(
        "CREATE TABLE conversation (username TEXT, name TEXT, time INTEGER, count INTEGER)"
    )
    connection.execute(
        "INSERT INTO conversation VALUES (?, ?, ?, ?)",
        ("room@chatroom", "项目群", 1_700_000_000, 1),
    )
    connection.execute(
        "INSERT INTO message VALUES (?, ?, ?, ?)",
        (7, "room@chatroom", 1_700_000_000, "合成现代消息"),
    )
    connection.commit()
    connection.execute("VACUUM")
    connection.close()
    raw = bytearray(path.read_bytes())
    raw[20] = 80
    _shift_sqlite_cells_for_reserve(raw)
    path.write_bytes(raw)


def _encrypt_sqlcipher4(source: Path, destination: Path, raw_key: bytes):
    salt = bytes.fromhex("00112233445566778899aabbccddeeff")
    derived_key = hashlib.pbkdf2_hmac(
        "sha512", raw_key, salt, 256_000, 32
    )
    mac_key = hashlib.pbkdf2_hmac(
        "sha512",
        derived_key,
        bytes(value ^ 0x3A for value in salt),
        2,
        32,
    )
    plaintext = source.read_bytes()
    assert len(plaintext) % MODERN_PAGE_SIZE == 0
    encrypted = bytearray(salt)
    for page_number, offset in enumerate(
        range(0, len(plaintext), MODERN_PAGE_SIZE), start=1
    ):
        page = plaintext[offset : offset + MODERN_PAGE_SIZE]
        body = page[16:] if page_number == 1 else page
        body = body[:-80]
        iv = bytes([page_number]) * 16
        ciphertext = AES.new(derived_key, AES.MODE_CBC, iv).encrypt(body)
        mac = hmac.new(
            mac_key,
            ciphertext + iv + page_number.to_bytes(4, "big"),
            hashlib.sha512,
        ).digest()
        encrypted.extend(ciphertext + iv + mac)
    destination.write_bytes(encrypted)


def _shift_sqlite_cells_for_reserve(database: bytearray):
    """Make a plain SQLite fixture use SQLCipher's 80-byte page reserve."""
    for offset in range(0, len(database), MODERN_PAGE_SIZE):
        page = database[offset : offset + MODERN_PAGE_SIZE]
        header_offset = 100 if offset == 0 else 0
        page_type = page[header_offset]
        if page_type not in (2, 5, 10, 13):
            continue
        header_size = 12 if page_type in (2, 5) else 8
        cell_count = int.from_bytes(
            page[header_offset + 3 : header_offset + 5], "big"
        )
        pointers = []
        for index in range(cell_count):
            pointer_offset = header_offset + header_size + index * 2
            pointer = int.from_bytes(
                page[pointer_offset : pointer_offset + 2], "big"
            )
            pointers.append((pointer_offset, pointer))
        if not pointers:
            continue
        lowest = min(pointer for _, pointer in pointers)
        shifted = bytearray(page)
        content = page[lowest:]
        shifted[lowest - 80 : lowest - 80 + len(content)] = content
        for pointer_offset, pointer in pointers:
            shifted[pointer_offset : pointer_offset + 2] = (pointer - 80).to_bytes(
                2, "big"
            )
        database[offset : offset + MODERN_PAGE_SIZE] = shifted

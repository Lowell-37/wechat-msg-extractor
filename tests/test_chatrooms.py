import os
import sqlite3
import tempfile
from pathlib import Path
from typing import ClassVar

import pytest

import app as app_module
from core.dbutils import MergedMsgDB


class AggregateRow(tuple):
    _columns: ClassVar = {
        "StrTalker": 0,
        "LastMessageAt": 1,
        "MessageCount": 2,
    }

    def __new__(cls, chatroom_id, last_message_at, message_count):
        return super().__new__(cls, (chatroom_id, last_message_at, message_count))

    def __getitem__(self, key):
        if isinstance(key, str):
            key = self._columns[key]
        return super().__getitem__(key)


def test_production_chatroom_query_returns_message_metadata(tmp_path: Path):
    msg_path = tmp_path / "MSG0.db"
    msg_path.touch()

    class MessageDatabase:
        original_path = str(msg_path)
        key_hex = "ab" * 32

        def execute(self, sql, params=()):
            assert "MAX(CreateTime)" in sql
            assert "COUNT(*)" in sql
            return [
                {
                    "StrTalker": "room-1@chatroom",
                    "LastMessageAt": 1785634100,
                    "MessageCount": 20,
                },
                {
                    "StrTalker": "room-1@chatroom",
                    "LastMessageAt": 1785634200,
                    "MessageCount": 22,
                },
            ]

    assert app_module._get_chatrooms(MessageDatabase()) == [
        ("room-1@chatroom", "room-1@chatroom", 1785634200, 42)
    ]


def test_identical_aggregate_rows_from_multiple_shards_are_both_counted(
    tmp_path: Path,
):
    aggregate = AggregateRow("room-1@chatroom", 1785634200, 42)

    class Shard:
        key_hex = "ab" * 32

        def __init__(self, path):
            self.original_path = str(path)

        def execute(self, sql, params=()):
            assert "COUNT(*)" in sql
            return [aggregate]

    paths = [tmp_path / "MSG0.db", tmp_path / "MSG1.db"]
    for path in paths:
        path.touch()
    database = MergedMsgDB([Shard(path) for path in paths])

    assert app_module._get_chatrooms(database) == [
        ("room-1@chatroom", "room-1@chatroom", 1785634200, 84)
    ]


@pytest.mark.parametrize("failing_query", [1, 2])
def test_micro_msg_connection_closes_before_plaintext_cleanup(
    monkeypatch, tmp_path, failing_query
):
    msg_dir = tmp_path / "Msg"
    msg_dir.mkdir()
    original_msg = msg_dir / "MSG0.db"
    original_msg.touch()
    (tmp_path / "MicroMsg.db").touch()

    actions = []
    real_mkstemp = tempfile.mkstemp
    real_unlink = os.unlink

    class Cursor:
        def fetchall(self):
            return []

    class Connection:
        row_factory = None

        def __init__(self):
            self.query_count = 0
            self.closed = False

        def execute(self, sql, params=()):
            self.query_count += 1
            actions.append(f"query-{self.query_count}")
            if self.query_count == failing_query:
                raise sqlite3.DatabaseError(f"query {failing_query} failed")
            return Cursor()

        def close(self):
            self.closed = True
            actions.append("close")

    connection = Connection()
    temp_paths = []

    def make_plaintext(suffix=""):
        descriptor, path = real_mkstemp(suffix=suffix, dir=tmp_path)
        temp_paths.append(path)
        return descriptor, path

    def delete_plaintext(path):
        if path in temp_paths:
            actions.append("unlink")
            assert connection.closed, "plaintext was deleted before closing SQLite"
        real_unlink(path)

    class MessageDatabase:
        original_path = str(original_msg)
        key_hex = "ab" * 32

        def execute(self, sql, params=()):
            return []

    monkeypatch.setattr(tempfile, "mkstemp", make_plaintext)
    monkeypatch.setattr(sqlite3, "connect", lambda path: connection)
    monkeypatch.setattr(os, "unlink", delete_plaintext)
    monkeypatch.setattr("core.dbutils.decrypt_db_raw", lambda *args: True)

    assert app_module._get_chatrooms(MessageDatabase()) == []

    assert actions[-2:] == ["close", "unlink"]
    assert all(not os.path.exists(path) for path in temp_paths)

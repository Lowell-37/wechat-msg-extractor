import pytest

from core.connection import ConnectionError, connect_wechat


def test_manual_connection_uses_supplied_key(monkeypatch):
    calls = []

    class FakeShard:
        def execute(self, sql, params=()):
            return [{"n": 2}]

        def close(self):
            pass

    class FakeDB:
        def scan_and_use_key(self, key):
            calls.append(key)
            return True, "密钥已接受"

        def open_all_msg_dbs(self):
            return [FakeShard()]

    monkeypatch.setattr("core.connection.WeChatDB", FakeDB)

    result = connect_wechat("ab" * 32)

    assert calls == ["ab" * 32]
    assert result.shard_count == 1
    assert result.table_count == 2


def test_connection_closes_all_shards_when_validation_fails(monkeypatch):
    closed = []

    class GoodShard:
        def execute(self, sql, params=()):
            return [{"n": 1}]

        def close(self):
            closed.append("good")

    class BadShard:
        def execute(self, sql, params=()):
            raise RuntimeError("bad database")

        def close(self):
            closed.append("bad")

    class FakeDB:
        def scan_and_extract(self):
            return True, "ok"

        def open_all_msg_dbs(self):
            return [GoodShard(), BadShard()]

    monkeypatch.setattr("core.connection.WeChatDB", FakeDB)

    with pytest.raises(ConnectionError, match="数据库验证失败"):
        connect_wechat()

    assert closed == ["good", "bad"]


def test_successful_connection_leaves_shards_open_for_merged_database(monkeypatch):
    closed = []

    class FakeShard:
        def execute(self, sql, params=()):
            return [{"n": 1}]

        def close(self):
            closed.append(True)

    class FakeDB:
        def scan_and_extract(self):
            return True, "ok"

        def open_all_msg_dbs(self):
            return [FakeShard()]

    monkeypatch.setattr("core.connection.WeChatDB", FakeDB)

    connect_wechat()

    assert closed == []

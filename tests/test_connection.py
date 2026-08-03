import pytest

from core.connection import ConnectionError, connect_wechat
from core.dbutils import WeChatDB


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


def test_open_all_msg_dbs_closes_opened_shards_on_later_open_failure(monkeypatch, tmp_path):
    data_dir = tmp_path / "Msg"
    data_dir.mkdir()
    (data_dir / "MSG0.db").touch()
    (data_dir / "MSG1.db").touch()

    closed = []
    real_mkstemp = __import__("tempfile").mkstemp
    calls = 0

    def controlled_mkstemp(*args, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise OSError("disk unavailable")
        return real_mkstemp(*args, **kwargs)

    def decrypt_to_empty_sqlite(key, source, output):
        import sqlite3

        sqlite3.connect(output).close()
        return True

    class TrackingDB:
        def __init__(self, **kwargs):
            self.conn = kwargs["conn"]
            self.temp_path = kwargs["temp_path"]

        def close(self):
            closed.append(self.temp_path)
            self.conn.close()

    monkeypatch.setattr("core.dbutils.tempfile.mkstemp", controlled_mkstemp)
    monkeypatch.setattr("core.dbutils.decrypt_db_raw", decrypt_to_empty_sqlite)
    monkeypatch.setattr("core.dbutils.DecryptedDB", TrackingDB)

    database = WeChatDB()
    database._key = "ab" * 32
    database._info = type("Info", (), {"data_dir": str(data_dir)})()

    with pytest.raises(OSError, match="disk unavailable"):
        database.open_all_msg_dbs()

    assert len(closed) == 1


def test_open_all_msg_dbs_cleans_temp_file_when_closing_later_file_fails(monkeypatch, tmp_path):
    data_dir = tmp_path / "Msg"
    data_dir.mkdir()
    (data_dir / "MSG0.db").touch()
    (data_dir / "MSG1.db").touch()

    closed = []
    temp_paths = []
    real_mkstemp = __import__("tempfile").mkstemp
    real_close = __import__("os").close

    def record_mkstemp(*args, **kwargs):
        descriptor, path = real_mkstemp(*args, **kwargs)
        temp_paths.append(path)
        return descriptor, path

    def fail_second_close(descriptor):
        if len(temp_paths) == 2:
            real_close(descriptor)
            raise OSError("close unavailable")
        real_close(descriptor)

    def decrypt_to_empty_sqlite(key, source, output):
        import sqlite3

        sqlite3.connect(output).close()
        return True

    class TrackingDB:
        def __init__(self, **kwargs):
            self.conn = kwargs["conn"]

        def close(self):
            closed.append(True)
            self.conn.close()

    monkeypatch.setattr("core.dbutils.tempfile.mkstemp", record_mkstemp)
    monkeypatch.setattr("core.dbutils.os.close", fail_second_close)
    monkeypatch.setattr("core.dbutils.decrypt_db_raw", decrypt_to_empty_sqlite)
    monkeypatch.setattr("core.dbutils.DecryptedDB", TrackingDB)

    database = WeChatDB()
    database._key = "ab" * 32
    database._info = type("Info", (), {"data_dir": str(data_dir)})()

    with pytest.raises(OSError, match="close unavailable"):
        database.open_all_msg_dbs()

    assert closed == [True]
    assert not __import__("os").path.exists(temp_paths[1])

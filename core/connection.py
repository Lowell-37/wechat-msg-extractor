"""Shared WeChat database connection orchestration."""

from dataclasses import dataclass
from typing import Any

from core.dbutils import MergedMsgDB, WeChatDB


class ConnectionError(Exception):
    """Raised when WeChat data cannot be connected and verified."""


@dataclass
class ConnectedWechat:
    manager: WeChatDB
    database: MergedMsgDB
    shard_count: int
    table_count: int


def _close_shards(shards: list[Any]) -> None:
    for shard in shards:
        try:
            shard.close()
        except Exception:  # noqa: BLE001, S110 - best-effort cleanup continues
            pass


def _table_count(rows: list[Any]) -> int:
    if not rows:
        raise ValueError("sqlite_master returned no rows")

    row = rows[0]
    if isinstance(row, dict):
        return int(row["n"])
    try:
        return int(row["n"])
    except (IndexError, KeyError, TypeError):
        return int(row[0])


def connect_wechat(
    key: str | None = None,
    *,
    install_path: str | None = None,
    data_dir: str | None = None,
) -> ConnectedWechat:
    """Connect to and validate every available WeChat message database shard."""
    manager_kwargs = {}
    if install_path is not None or data_dir is not None:
        manager_kwargs = {"install_path": install_path, "data_dir": data_dir}
    manager = WeChatDB(**manager_kwargs)
    try:
        success, message = (
            manager.scan_and_extract()
            if key is None
            else manager.scan_and_use_key(key)
        )
    except Exception as exc:
        raise ConnectionError(str(exc)) from exc

    if not success:
        raise ConnectionError(message)

    try:
        shards = manager.open_all_msg_dbs()
    except Exception as exc:
        raise ConnectionError(f"无法打开 MSG 数据库：{exc}") from exc

    if not shards:
        raise ConnectionError("无法打开 MSG 数据库")

    try:
        table_count = 0
        for shard in shards:
            table_count += _table_count(
                shard.execute("SELECT count(*) as n FROM sqlite_master")
            )
            shard.execute("SELECT count(*) as n FROM MSG LIMIT 1")
        database = MergedMsgDB(shards)
    except Exception as exc:
        _close_shards(shards)
        raise ConnectionError(f"数据库验证失败：{exc}") from exc

    return ConnectedWechat(
        manager=manager,
        database=database,
        shard_count=len(shards),
        table_count=table_count,
    )

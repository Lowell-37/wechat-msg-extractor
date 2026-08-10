import pytest

from core.dbutils import MergedMsgDB, ShardQueryError


def test_merged_query_raises_instead_of_returning_partial_shard_results():
    class GoodShard:
        original_path = "MSG0.db"

        def execute(self, sql, params=()):
            return [(1, "complete-row")]

    class FailingShard:
        original_path = "MSG1.db"

        def execute(self, sql, params=()):
            raise OSError("shard unavailable")

    database = MergedMsgDB([GoodShard(), FailingShard()])

    with pytest.raises(RuntimeError, match=r"shard 2.*MSG1\.db.*shard unavailable"):
        database.execute_all("SELECT localId, StrContent FROM MSG")


def test_merged_query_still_deduplicates_complete_results():
    class Shard:
        def execute(self, sql, params=()):
            return [(1, "same")]


def test_per_shard_query_propagates_shard_context_without_partial_results():
    class GoodShard:
        original_path = "MSG0.db"

        def execute(self, sql, params=()):
            return [(1, "complete-row")]

    class FailingShard:
        original_path = "MSG1.db"

        def execute(self, sql, params=()):
            raise OSError("aggregate unavailable")

    database = MergedMsgDB([GoodShard(), FailingShard()])

    assert hasattr(database, "execute_per_shard")
    with pytest.raises(
        ShardQueryError, match=r"shard 2/2.*MSG1\.db.*aggregate unavailable"
    ):
        database.execute_per_shard("SELECT COUNT(*) FROM MSG")

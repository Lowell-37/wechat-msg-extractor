import pytest

from core.dbutils import MergedMsgDB


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

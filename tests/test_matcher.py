import pytest
from core.matcher import SheetMatcher, MatchResult


class TestSheetMatcher:
    @pytest.fixture
    def sheet_names(self):
        return ["张三", "李四", "王五", "Sheet1", "烧烤爽", "闫明明"]

    @pytest.fixture
    def manual_map(self):
        return {"0501初2027广州李四": "李四"}

    def test_auto_match_basic(self, sheet_names):
        matcher = SheetMatcher(sheet_names)
        results = matcher.match(["0601初2027广州张三", "班群通知"])
        assert results["0601初2027广州张三"] == "张三"
        assert results["班群通知"] is None

    def test_manual_map_priority(self, sheet_names, manual_map):
        matcher = SheetMatcher(sheet_names, manual_map=manual_map)
        results = matcher.match(["0501初2027广州李四"])
        assert results["0501初2027广州李四"] == "李四"

    def test_no_match(self, sheet_names):
        matcher = SheetMatcher(sheet_names)
        results = matcher.match(["未知群聊", "另一个群"])
        assert results["未知群聊"] is None
        assert results["另一个群"] is None

    def test_ambiguous_match_picks_first(self, sheet_names):
        # 张三丰 contains 张三, should still match
        matcher = SheetMatcher(sheet_names + ["张三丰"])
        results = matcher.match(["张三丰学习群"])
        # Should match "张三丰" (longer match) or "张三" (shorter)
        # Current simple strategy: first matching sheet in list wins
        assert results["张三丰学习群"] in ("张三", "张三丰")

    def test_get_matched_sheets(self, sheet_names):
        matcher = SheetMatcher(sheet_names)
        matcher.match(["0601初2027广州张三", "班群通知"])
        matched = matcher.get_matched_sheets()
        assert "张三" in matched
        assert "班群通知" not in matched

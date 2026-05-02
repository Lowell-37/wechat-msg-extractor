import pytest
from datetime import date, datetime
from core.task_parser import TaskParser, ParsedTask


class TestTaskParser:
    def test_is_task_message_positive(self):
        parser = TaskParser()
        msg = "\U0001f6a95.2 任务\n1\ufe0f\u20e3 订正作文\n2\ufe0f\u20e3 完形填空"
        assert parser.is_task_message(msg) is True

    def test_is_task_message_negative(self):
        parser = TaskParser()
        assert parser.is_task_message("今天的作业做完了吗") is False
        assert parser.is_task_message("好的收到") is False
        assert parser.is_task_message("") is False

    def test_extract_date(self):
        parser = TaskParser(year=2026)
        result = parser.parse("\U0001f6a95.2 任务\n1\ufe0f\u20e3 订正作文")
        assert result.date == date(2026, 5, 2)

    def test_extract_date_with_single_digit_month(self):
        parser = TaskParser(year=2026)
        result = parser.parse("\U0001f6a912.25 任务\n1\ufe0f\u20e3 复习")
        assert result.date == date(2026, 12, 25)

    def test_extract_tasks(self):
        parser = TaskParser(year=2026)
        msg = "\U0001f6a95.2 任务\n1\ufe0f\u20e3 订正作文\n2\ufe0f\u20e3 完形填空，首字母填空，语法填空各一篇\n3\ufe0f\u20e3 背单词"
        result = parser.parse(msg)
        assert len(result.tasks) == 3
        assert result.tasks[0] == "订正作文"
        assert result.tasks[1] == "完形填空，首字母填空，语法填空各一篇"
        assert result.tasks[2] == "背单词"

    def test_parse_returns_none_for_non_task(self):
        parser = TaskParser(year=2026)
        result = parser.parse("普通消息")
        assert result is None

    def test_date_excel_serial(self):
        parser = TaskParser(year=2026)
        result = parser.parse("\U0001f6a95.2 任务\n1\ufe0f\u20e3 测试")
        expected_serial = (datetime(2026, 5, 2) - datetime(1899, 12, 30)).days
        assert result.date_excel_serial == expected_serial

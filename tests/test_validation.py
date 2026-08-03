from pathlib import Path

import pytest

from core.validation import (
    ValidationError,
    parse_date_range,
    resolve_output_path,
    validate_hex_key,
    validate_sheet_name,
)


def test_date_range_rejects_reverse_order():
    with pytest.raises(ValidationError, match="开始日期"):
        parse_date_range("2026-08-03", "2026-08-01")


def test_hex_key_is_normalized_and_validated():
    assert validate_hex_key(" A1" * 32) == "a1" * 32
    with pytest.raises(ValidationError, match="64"):
        validate_hex_key("abcd")


def test_sheet_must_exist():
    assert validate_sheet_name("张三", ["张三", "李四"]) == "张三"
    with pytest.raises(ValidationError, match="工作表"):
        validate_sheet_name("王五", ["张三", "李四"])


def test_output_path_is_confined_to_default_directory(tmp_path):
    result = resolve_output_path("report.xlsx", str(tmp_path), "fallback.xlsx")
    assert result == (tmp_path / "report.xlsx").resolve()
    with pytest.raises(ValidationError, match="导出目录"):
        resolve_output_path("../outside.xlsx", str(tmp_path), "fallback.xlsx")

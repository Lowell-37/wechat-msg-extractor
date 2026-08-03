import re
from datetime import date
from pathlib import Path
from typing import Collection


class ValidationError(ValueError):
    pass


def parse_date_range(start: str, end: str) -> tuple[date, date]:
    try:
        start_value = date.fromisoformat(start)
        end_value = date.fromisoformat(end)
    except (TypeError, ValueError) as exc:
        raise ValidationError("日期格式无效") from exc
    if start_value > end_value:
        raise ValidationError("开始日期不能晚于结束日期")
    return start_value, end_value


def validate_hex_key(value: str) -> str:
    normalized = "".join(value.split()).lower()
    if not re.fullmatch(r"[0-9a-f]{64}", normalized):
        raise ValidationError("数据库密钥必须是 64 位十六进制字符串")
    return normalized


def validate_sheet_name(value: str, available: Collection[str]) -> str:
    if value not in available:
        raise ValidationError("目标工作表不存在")
    return value


def resolve_output_path(value: str, output_dir: str, default_name: str) -> Path:
    root = Path(output_dir).resolve()
    candidate = Path(value) if value else Path(default_name)
    candidate = candidate if candidate.is_absolute() else root / candidate
    resolved = candidate.resolve()
    if resolved.suffix.lower() != ".xlsx":
        raise ValidationError("导出文件必须使用 .xlsx 扩展名")
    if root != resolved.parent and root not in resolved.parents:
        raise ValidationError("导出文件必须位于配置的导出目录内")
    return resolved

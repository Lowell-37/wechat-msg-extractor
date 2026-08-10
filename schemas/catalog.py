from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any

from core.task_parser import ParsedTask, TaskParser


@dataclass(frozen=True)
class ChatroomOption:
    chat_id: str
    display_name: str
    last_message_at: datetime | None = None
    message_count: int = 0
    suggested_sheet: str = ""


@dataclass(frozen=True)
class CatalogDependencies:
    """External operations needed to build a chatroom catalog."""

    query_chatrooms: Callable[[Any], Sequence[tuple[str, str]]]
    database_key: str = "ddb"
    excel_writer_factory: Callable[[str], Any] | None = None


@dataclass(frozen=True)
class PreviewDependencies:
    """External operations needed to fetch and parse a preview."""

    fetch_messages: Callable[[Any, str, date, date, bool], Sequence[dict[str, Any]]]
    database_key: str = "ddb"
    task_parser_factory: Callable[[], TaskParser] = TaskParser


@dataclass(frozen=True)
class PreviewSummary:
    group_id: str
    group_name: str
    start_date: date
    end_date: date
    sheet_name: str
    message_count: int
    task_count: int


@dataclass(frozen=True)
class PreviewResult:
    summary: PreviewSummary
    tasks: list[ParsedTask]
    grouped_tasks: dict[str, list[ParsedTask]]
    analysis_by_date: dict[str, list[str]]

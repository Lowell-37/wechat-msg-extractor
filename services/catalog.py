from collections.abc import MutableMapping
from datetime import UTC, datetime
from typing import Any, cast

from core.excel_writer import ExcelWriter
from core.matcher import SheetMatcher
from core.validation import ValidationError
from schemas.catalog import CatalogDependencies, ChatroomOption


def load_catalog(
    state: MutableMapping[str, Any], excel_path: str
) -> tuple[ChatroomOption, ...]:
    """Load catalog records once and retain them for later in-session search."""
    cached = state.get("chatroom_catalog")
    if isinstance(cached, tuple):
        return cast(tuple[ChatroomOption, ...], cached)

    dependencies = _catalog_dependencies(state)
    database = state[dependencies.database_key]
    writer_factory = dependencies.excel_writer_factory or ExcelWriter
    writer = None
    try:
        writer = writer_factory(excel_path)
        sheet_names = tuple(writer.get_sheet_names())
        chatrooms = dependencies.query_chatrooms(database)
    finally:
        if writer is not None:
            writer.close()

    records = [_normalize_chatroom(record) for record in chatrooms]
    matcher = SheetMatcher(
        list(sheet_names), manual_map=dict(dependencies.manual_group_sheet_map)
    )
    suggested_sheets = matcher.match([record[1] for record in records])
    catalog = tuple(
        ChatroomOption(
            chat_id=chat_id,
            display_name=display_name,
            last_message_at=last_message_at,
            message_count=message_count,
            suggested_sheet=suggested_sheets.get(display_name) or "",
        )
        for chat_id, display_name, last_message_at, message_count in records
    )
    state["chatroom_catalog"] = catalog
    state["catalog_sheet_names"] = sheet_names
    return catalog


def filter_catalog(
    state: MutableMapping[str, Any], query: str
) -> list[ChatroomOption]:
    """Filter already-loaded catalog records without reopening external resources."""
    catalog = state.get("chatroom_catalog", ())
    normalized_query = query.casefold()
    return [
        option
        for option in catalog
        if normalized_query in option.chat_id.casefold()
        or normalized_query in option.display_name.casefold()
    ]


def find_catalog_option(
    state: MutableMapping[str, Any], group_id: str, group_name: str
) -> ChatroomOption:
    """Resolve an exact group ID/name pair from the immutable session cache."""
    catalog = state.get("chatroom_catalog")
    if isinstance(catalog, tuple):
        for option in catalog:
            if option.chat_id == group_id and option.display_name == group_name:
                return option
    raise ValidationError("群聊选择无效或已过期")


def _normalize_chatroom(
    record: tuple[str, str] | tuple[str, str, int | datetime | None, int],
) -> tuple[str, str, datetime | None, int]:
    if len(record) == 2:
        chat_id, display_name = record
        return chat_id, display_name, None, 0
    chat_id, display_name, last_message_at, message_count = record
    if last_message_at is not None and not isinstance(last_message_at, datetime):
        last_message_at = datetime.fromtimestamp(last_message_at, UTC).astimezone()
    return chat_id, display_name, last_message_at, message_count


def _catalog_dependencies(state: MutableMapping[str, Any]) -> CatalogDependencies:
    dependencies = state.get("catalog_dependencies")
    if not isinstance(dependencies, CatalogDependencies):
        raise TypeError("Catalog dependencies are not configured")
    return dependencies

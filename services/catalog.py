from collections.abc import MutableMapping
from typing import Any, cast

from core.excel_writer import ExcelWriter
from core.matcher import SheetMatcher
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

    matcher = SheetMatcher(list(sheet_names))
    suggested_sheets = matcher.match([display_name for _, display_name in chatrooms])
    catalog = tuple(
        ChatroomOption(
            chat_id=chat_id,
            display_name=display_name,
            suggested_sheet=suggested_sheets.get(display_name) or "",
        )
        for chat_id, display_name in chatrooms
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


def _catalog_dependencies(state: MutableMapping[str, Any]) -> CatalogDependencies:
    dependencies = state.get("catalog_dependencies")
    if not isinstance(dependencies, CatalogDependencies):
        raise TypeError("Catalog dependencies are not configured")
    return dependencies

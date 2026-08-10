from collections import defaultdict
from collections.abc import MutableMapping
from datetime import datetime
from typing import Any

from core.validation import ValidationError, parse_date_range, validate_sheet_name
from schemas.catalog import PreviewDependencies, PreviewResult, PreviewSummary
from services.wizard import (
    get_wizard,
    mark_connected,
    store_preview,
    update_selection,
)


def build_preview(
    state: MutableMapping[str, Any],
    group_id: str,
    group_name: str,
    start_date: str,
    end_date: str,
    sheet_name: str,
) -> PreviewResult:
    """Validate, fetch, parse, and atomically persist a preview selection."""
    start_value, end_value = parse_date_range(start_date, end_date)
    validated_sheet = validate_sheet_name(sheet_name, state.get("catalog_sheet_names", ()))
    _validate_catalog_group(state, group_id, group_name)
    dependencies = _preview_dependencies(state)
    messages = dependencies.fetch_messages(
        state[dependencies.database_key], group_id, start_value, end_value, False
    )
    tasks, analysis_by_date = _parse_messages(messages, dependencies)
    grouped_tasks = _group_tasks(tasks)
    result = PreviewResult(
        summary=PreviewSummary(
            group_id=group_id,
            group_name=group_name,
            start_date=start_value,
            end_date=end_value,
            sheet_name=validated_sheet,
            message_count=len(messages),
            task_count=len(tasks),
        ),
        tasks=tasks,
        grouped_tasks=grouped_tasks,
        analysis_by_date=analysis_by_date,
    )

    wizard = get_wizard(state)
    update_selection(
        wizard,
        group_id,
        group_name,
        start_value,
        end_value,
        validated_sheet,
    )
    store_preview(wizard, tasks)
    state["selected_group"] = group_id
    state["selected_sheet"] = validated_sheet
    state["start_date"] = start_date
    state["end_date"] = end_date
    state["parsed_tasks"] = tasks
    state["analysis_by_date"] = analysis_by_date
    return result


def build_legacy_preview(
    state: MutableMapping[str, Any],
    group_name: str,
    start_date: str,
    end_date: str,
    sheet_name: str,
) -> PreviewResult:
    """Build a preview for the legacy action contract used before Task 5."""
    start_value, end_value = parse_date_range(start_date, end_date)
    dependencies = _preview_dependencies(state)
    messages = dependencies.fetch_messages(
        state[dependencies.database_key],
        group_name,
        start_value,
        end_value,
        False,
    )
    tasks, analysis_by_date = _parse_messages(messages, dependencies)
    result = PreviewResult(
        summary=PreviewSummary(
            group_id=group_name,
            group_name=group_name,
            start_date=start_value,
            end_date=end_value,
            sheet_name=sheet_name,
            message_count=len(messages),
            task_count=len(tasks),
        ),
        tasks=tasks,
        grouped_tasks=_group_tasks(tasks),
        analysis_by_date=analysis_by_date,
    )

    wizard = get_wizard(state)
    mark_connected(wizard)
    update_selection(
        wizard,
        group_name,
        group_name,
        start_value,
        end_value,
        sheet_name,
    )
    store_preview(wizard, tasks)
    state["selected_group"] = group_name
    state["selected_sheet"] = sheet_name
    state["start_date"] = start_date
    state["end_date"] = end_date
    state["parsed_tasks"] = tasks
    state["analysis_by_date"] = analysis_by_date
    return result


def _preview_dependencies(state: MutableMapping[str, Any]) -> PreviewDependencies:
    dependencies = state.get("preview_dependencies")
    if not isinstance(dependencies, PreviewDependencies):
        raise TypeError("Preview dependencies are not configured")
    return dependencies


def _validate_catalog_group(
    state: MutableMapping[str, Any], group_id: str, group_name: str
) -> None:
    catalog = state.get("chatroom_catalog")
    if not isinstance(catalog, tuple) or not any(
        option.chat_id == group_id and option.display_name == group_name
        for option in catalog
    ):
        raise ValidationError("群聊选择无效或已过期")


def _parse_messages(
    messages: Any, dependencies: PreviewDependencies
) -> tuple[list[Any], dict[str, list[str]]]:
    parser = dependencies.task_parser_factory()
    tasks = []
    analysis_by_date: defaultdict[str, list[str]] = defaultdict(list)
    for message in messages:
        content = message["content"]
        if parser.is_task_message(content):
            task = parser.parse(content, message["msg_id"])
            if task is not None:
                tasks.append(task)
        elif content.strip():
            message_date = datetime.fromtimestamp(message["timestamp"]).date()  # noqa: DTZ006
            analysis_by_date[message_date.isoformat()].append(content.strip())
    tasks.sort(key=lambda task: task.date)
    return tasks, dict(analysis_by_date)


def _group_tasks(tasks: list[Any]) -> dict[str, list[Any]]:
    grouped_tasks: defaultdict[str, list[Any]] = defaultdict(list)
    for task in tasks:
        grouped_tasks[task.date.isoformat()].append(task)
    return dict(grouped_tasks)

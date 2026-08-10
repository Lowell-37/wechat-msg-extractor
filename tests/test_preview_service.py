from datetime import datetime

import pytest

from core.validation import ValidationError
from schemas.catalog import PreviewDependencies
from schemas.wizard import WizardState
from services.preview import build_preview
from services.wizard import mark_connected


def _task_message() -> str:
    return "🚩8.2 任务\n1️⃣准备材料"


def test_build_preview_persists_valid_selection_and_grouped_tasks():
    database = object()
    wizard = WizardState()
    mark_connected(wizard)
    state = {
        "ddb": database,
        "wizard": wizard,
        "catalog_sheet_names": ("项目群",),
        "preview_dependencies": PreviewDependencies(
            fetch_messages=lambda received_database, *_: [
                {
                    "msg_id": 1,
                    "content": _task_message(),
                    "timestamp": int(datetime(2026, 8, 2).timestamp()),  # noqa: DTZ001
                }
            ]
            if received_database is database
            else [],
        ),
    }

    result = build_preview(
        state,
        "room-1",
        "项目群",
        "2026-08-01",
        "2026-08-02",
        "项目群",
    )

    assert result.summary.group_name == "项目群"
    assert result.summary.task_count == 1
    assert result.grouped_tasks == {"2026-08-02": result.tasks}
    assert wizard.preview_ready is True
    assert wizard.preview_tasks == result.tasks
    assert state["selected_group"] == "room-1"


def test_build_preview_preserves_existing_state_when_fetch_fails():
    wizard = WizardState()
    mark_connected(wizard)
    wizard.preview_tasks = ["existing"]
    wizard.preview_ready = True
    state = {
        "ddb": object(),
        "wizard": wizard,
        "catalog_sheet_names": ("项目群",),
        "selected_group": "old-room",
        "selected_sheet": "项目群",
        "start_date": "2026-07-01",
        "end_date": "2026-07-02",
        "preview_dependencies": PreviewDependencies(
            fetch_messages=lambda *_: (_ for _ in ()).throw(RuntimeError("offline")),
        ),
    }

    with pytest.raises(RuntimeError, match="offline"):
        build_preview(
            state,
            "room-1",
            "项目群",
            "2026-08-01",
            "2026-08-02",
            "项目群",
        )

    assert state["selected_group"] == "old-room"
    assert wizard.preview_tasks == ["existing"]
    assert wizard.preview_ready is True


def test_build_preview_validates_sheet_before_fetching_messages():
    wizard = WizardState()
    mark_connected(wizard)
    calls = []
    state = {
        "ddb": object(),
        "wizard": wizard,
        "catalog_sheet_names": ("项目群",),
        "preview_dependencies": PreviewDependencies(
            fetch_messages=lambda *_: calls.append(True),
        ),
    }

    with pytest.raises(ValidationError, match="工作表"):
        build_preview(
            state,
            "room-1",
            "项目群",
            "2026-08-01",
            "2026-08-02",
            "不存在",
        )

    assert calls == []

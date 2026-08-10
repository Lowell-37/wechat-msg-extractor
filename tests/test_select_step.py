from datetime import UTC, date, datetime

import pytest
from fastapi.testclient import TestClient

import app as app_module
from schemas.catalog import ChatroomOption, PreviewDependencies
from services.wizard import get_wizard, mark_connected, update_selection


@pytest.fixture(autouse=True)
def reset_in_memory_state():
    app_module.session_state.clear()
    yield
    app_module.session_state.clear()


@pytest.fixture
def connected_client(monkeypatch):
    client = TestClient(app_module.app)
    client.get("/")
    state = app_module.session_state[client.cookies["session_id"]]
    state["ddb"] = object()
    state["chatroom_catalog"] = (
        ChatroomOption(
            "room-1",
            "项目群",
            datetime(2026, 8, 2, 9, 30, tzinfo=UTC),
            24,
            "项目群",
        ),
        ChatroomOption(
            "room-2",
            "运营群",
            datetime(2026, 8, 1, 18, 5, tzinfo=UTC),
            7,
            "运营群",
        ),
    )
    state["catalog_sheet_names"] = ("项目群", "运营群")
    state["preview_dependencies"] = PreviewDependencies(
        fetch_messages=lambda *_: []
    )
    mark_connected(get_wizard(state))
    monkeypatch.setattr(
        app_module,
        "_get_chatrooms",
        lambda _: (_ for _ in ()).throw(AssertionError("catalog was requeried")),
    )
    return client


def test_select_step_keeps_filters_list_summary_and_actions_in_one_region(
    connected_client,
):
    response = connected_client.get("/wizard/2")

    assert response.status_code == 200
    assert 'id="wizard-workspace"' in response.text
    assert 'name="query"' in response.text
    assert 'name="start_date"' in response.text
    assert 'name="end_date"' in response.text
    assert 'id="chatroom-list"' in response.text
    assert 'id="selection-summary"' in response.text
    assert 'class="wizard-actions"' in response.text
    assert response.text.index('name="query"') < response.text.index(
        'name="start_date"'
    )
    assert response.text.index('name="end_date"') < response.text.index(
        'id="chatroom-list"'
    )
    assert response.text.index('id="chatroom-list"') < response.text.index(
        'id="selection-summary"'
    )
    assert response.text.index('id="selection-summary"') < response.text.index(
        'class="wizard-actions"'
    )
    assert "<aside" not in response.text


def test_group_rows_use_native_radios_and_show_metadata_and_sheet_control(
    connected_client,
):
    response = connected_client.get("/wizard/2")

    assert 'type="radio"' in response.text
    assert 'name="group_id"' in response.text
    assert "项目群" in response.text
    assert "2026-08-02 09:30" in response.text
    assert "24 条消息" in response.text
    assert 'class="chatroom-sheet-select selection-control"' in response.text


def test_catalog_search_filters_only_cached_records_and_preserves_selection(
    connected_client,
):
    response = connected_client.get(
        "/api/catalog",
        params={"query": "运营", "group_id": "room-2", "sheet_name": "运营群"},
    )

    assert response.status_code == 200
    assert "运营群" in response.text
    assert 'data-group-name="项目群"' not in response.text
    assert 'value="room-2" checked' in response.text
    assert '<option value="运营群" selected>' in response.text


def test_valid_selection_is_stored_and_enables_preview(connected_client):
    response = connected_client.post(
        "/api/selection",
        data={
            "group_id": "room-1",
            "group_name": "项目群",
            "start_date": "2026-08-01",
            "end_date": "2026-08-03",
            "sheet_name": "项目群",
        },
    )

    state = app_module.session_state[connected_client.cookies["session_id"]]
    assert response.status_code == 200
    assert 'data-next-enabled="true"' in response.text
    assert "已选择 项目群 · 2026-08-01 至 2026-08-03 · Sheet：项目群" in response.text
    assert state["wizard"].selection.group_id == "room-1"
    assert state["selected_group"] == "room-1"
    assert state["selected_sheet"] == "项目群"


def test_invalid_date_keeps_submitted_values_and_disables_preview(connected_client):
    response = connected_client.post(
        "/api/selection",
        data={
            "group_id": "room-1",
            "group_name": "项目群",
            "start_date": "2026-08-03",
            "end_date": "2026-08-01",
            "sheet_name": "项目群",
        },
    )

    assert response.status_code == 422
    assert 'value="2026-08-03"' in response.text
    assert 'value="2026-08-01"' in response.text
    assert "开始日期不能晚于结束日期" in response.text
    assert 'data-next-enabled="false"' in response.text


@pytest.mark.parametrize(
    ("group_id", "group_name", "sheet_name", "message"),
    [
        ("room-missing", "项目群", "项目群", "群聊选择无效或已过期"),
        ("room-1", "运营群", "项目群", "群聊选择无效或已过期"),
        ("room-1", "项目群", "不存在", "工作表"),
    ],
)
def test_selection_requires_exact_cached_group_and_valid_sheet(
    connected_client, group_id, group_name, sheet_name, message
):
    response = connected_client.post(
        "/api/selection",
        data={
            "group_id": group_id,
            "group_name": group_name,
            "start_date": "2026-08-01",
            "end_date": "2026-08-03",
            "sheet_name": sheet_name,
        },
    )

    assert response.status_code == 422
    assert message in response.text
    assert 'data-next-enabled="false"' in response.text
    if sheet_name == "不存在":
        assert '<option value="不存在" selected>不存在（不可用）</option>' in response.text


def test_refresh_restores_session_selection_values(connected_client):
    connected_client.post(
        "/api/selection",
        data={
            "group_id": "room-1",
            "group_name": "项目群",
            "start_date": "2026-08-01",
            "end_date": "2026-08-03",
            "sheet_name": "项目群",
        },
    )

    response = connected_client.get("/wizard/2")

    assert 'value="2026-08-01"' in response.text
    assert 'value="2026-08-03"' in response.text
    assert 'value="room-1" checked' in response.text
    assert '<option value="项目群" selected>' in response.text
    assert 'data-next-enabled="true"' in response.text


def test_refresh_disables_next_when_stored_group_is_no_longer_in_cache(
    connected_client,
):
    state = app_module.session_state[connected_client.cookies["session_id"]]
    update_selection(
        state["wizard"],
        "room-stale",
        "旧项目群",
        date.fromisoformat("2026-08-01"),
        date.fromisoformat("2026-08-03"),
        "项目群",
    )

    response = connected_client.get("/wizard/2")

    assert 'data-next-enabled="false"' in response.text


def test_preview_uses_validated_selection_and_pushes_step_three(connected_client):
    connected_client.post(
        "/api/selection",
        data={
            "group_id": "room-1",
            "group_name": "项目群",
            "start_date": "2026-08-01",
            "end_date": "2026-08-03",
            "sheet_name": "项目群",
        },
    )

    response = connected_client.post(
        "/api/preview",
        data={
            "group_id": "room-1",
            "group_name": "项目群",
            "start_date": "2026-08-01",
            "end_date": "2026-08-03",
            "sheet_name": "项目群",
        },
    )

    assert response.status_code == 200
    assert response.headers["HX-Push-Url"] == "/wizard/3"
    assert 'id="wizard-workspace"' in response.text
    assert "步骤 3：预览与导出" in response.text
    assert 'hx-swap-oob="outerHTML"' in response.text


def test_browser_history_fetches_partials_and_focuses_the_step_heading(
    connected_client,
):
    response = connected_client.get("/static/wizard.js")

    assert 'addEventListener("popstate"' in response.text
    assert 'window.location.pathname + "/partial"' in response.text
    assert 'querySelector(".step-heading h1")' in response.text
    assert ".focus(" in response.text

import asyncio
import logging
from datetime import date, datetime
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from openpyxl import Workbook

import app as app_module
from core.task_parser import ParsedTask


@pytest.fixture(autouse=True)
def reset_in_memory_state():
    app_module.session_state.clear()
    app_module.progress_hub._listeners.clear()
    yield
    app_module.session_state.clear()
    app_module.progress_hub._listeners.clear()


@pytest.fixture
def client_with_database(monkeypatch, tmp_path):
    template_path = tmp_path / "template.xlsx"
    workbook = Workbook()
    workbook.active.title = "张三"
    workbook.save(template_path)
    workbook.close()

    monkeypatch.setattr(app_module.config.excel, "template_path", str(template_path))
    monkeypatch.setattr(app_module.config.excel, "output_dir", str(tmp_path / "exports"))
    monkeypatch.setattr(app_module.config.ai, "enabled", False)

    client = TestClient(app_module.app)
    client.get("/")
    session_id = client.cookies["session_id"]
    app_module.session_state[session_id]["ddb"] = object()
    return client


@pytest.fixture
def client_with_preview(client_with_database):
    session_id = client_with_database.cookies["session_id"]
    state = app_module.session_state[session_id]
    state.update(
        selected_group="group@chatroom",
        selected_sheet="张三",
        start_date="2026-08-01",
        end_date="2026-08-03",
        parsed_tasks=[
            ParsedTask(
                msg_id=1,
                date=date(2026, 8, 2),
                date_excel_serial=46236,
                raw_text="任务",
                tasks=["测试任务"],
            )
        ],
        analysis_by_date={},
    )
    return client_with_database


def test_preview_rejects_reverse_date_range_before_mutating_session(client_with_database):
    session_id = client_with_database.cookies["session_id"]
    state = app_module.session_state[session_id]
    state.update(
        selected_group="existing@chatroom",
        selected_sheet="existing-sheet",
        start_date="2026-07-01",
        end_date="2026-07-02",
    )

    response = client_with_database.post(
        "/api/preview",
        data={
            "group_name": "group@chatroom",
            "sheet_name": "张三",
            "start_date": "2026-08-03",
            "end_date": "2026-08-01",
        },
    )

    assert response.status_code == 400
    assert "开始日期不能晚于结束日期" in response.text
    assert state["selected_group"] == "existing@chatroom"
    assert state["selected_sheet"] == "existing-sheet"
    assert state["start_date"] == "2026-07-01"
    assert state["end_date"] == "2026-07-02"


def test_export_builds_voice_range_from_validated_session_dates(
    monkeypatch, client_with_preview
):
    captured = {}
    scheduled = []

    class FakeVoice:
        def __init__(self, database, config):
            pass

        async def transcribe_all(self, group, start_ts, end_ts):
            captured.update(group=group, start_ts=start_ts, end_ts=end_ts)
            return {}

    monkeypatch.setattr(app_module, "VoiceTranscriber", FakeVoice)
    monkeypatch.setattr(app_module.config.voice, "enabled", True)
    monkeypatch.setattr(app_module.config.voice, "api_key", "test-key")
    monkeypatch.setattr(app_module.asyncio, "create_task", scheduled.append)

    response = client_with_preview.post(
        "/api/export",
        data={
            "sheet_name": "张三",
            "output_path": "result.xlsx",
            "enable_voice": "true",
            "privacy_acknowledged": "true",
        },
    )

    assert response.status_code == 202
    assert len(scheduled) == 1
    asyncio.run(scheduled[0])
    assert captured == {
        "group": "group@chatroom",
        "start_ts": int(datetime(2026, 8, 1).timestamp()),  # noqa: DTZ001
        "end_ts": int(datetime(2026, 8, 3, 23, 59, 59).timestamp()),  # noqa: DTZ001
    }


def test_export_rejects_invalid_stored_dates_before_scheduling(monkeypatch, client_with_preview):
    scheduled = []
    session_id = client_with_preview.cookies["session_id"]
    app_module.session_state[session_id]["start_date"] = "<not-a-date>"
    monkeypatch.setattr(app_module.asyncio, "create_task", scheduled.append)

    response = client_with_preview.post(
        "/api/export", data={"sheet_name": "张三", "output_path": "result.xlsx"}
    )

    assert response.status_code == 400
    assert "<not-a-date>" not in response.text
    assert scheduled == []


def test_export_rejects_unavailable_sheet_before_scheduling(monkeypatch, client_with_preview):
    scheduled = []
    monkeypatch.setattr(app_module.asyncio, "create_task", scheduled.append)

    response = client_with_preview.post(
        "/api/export", data={"sheet_name": "不存在", "output_path": "result.xlsx"}
    )

    assert response.status_code == 400
    assert scheduled == []


def test_export_rejects_output_outside_configured_directory_before_scheduling(
    monkeypatch, client_with_preview
):
    scheduled = []
    monkeypatch.setattr(app_module.asyncio, "create_task", scheduled.append)

    response = client_with_preview.post(
        "/api/export", data={"sheet_name": "张三", "output_path": "../outside.xlsx"}
    )

    assert response.status_code == 400
    assert scheduled == []


def test_export_warns_on_voice_failure_and_continues_text_export(
    monkeypatch, caplog, client_with_preview
):
    events = []
    scheduled = []

    class FailingVoice:
        def __init__(self, database, config):
            pass

        async def transcribe_all(self, group, start_ts, end_ts):
            raise RuntimeError("voice unavailable")

    async def capture_event(session_id, event):
        events.append(event)

    monkeypatch.setattr(app_module, "VoiceTranscriber", FailingVoice)
    monkeypatch.setattr(app_module.config.voice, "enabled", True)
    monkeypatch.setattr(app_module.config.voice, "api_key", "test-key")
    monkeypatch.setattr(app_module.progress_hub, "emit", capture_event)
    monkeypatch.setattr(app_module.asyncio, "create_task", scheduled.append)

    with caplog.at_level(logging.WARNING, logger="app"):
        response = client_with_preview.post(
            "/api/export",
            data={
                "sheet_name": "张三",
                "output_path": "voice-failure.xlsx",
                "enable_voice": "true",
                "privacy_acknowledged": "true",
            },
        )
        assert response.status_code == 202
        asyncio.run(scheduled[0])

    assert "Voice transcription failed for session" in caplog.text
    assert any(event.stage == "warning" and event.progress == 8 for event in events)
    assert any(event.stage == "done" for event in events)
    assert (Path(app_module.config.excel.output_dir) / "voice-failure.xlsx").exists()


def test_export_closes_worker_workbook_when_task_writing_fails(
    monkeypatch, client_with_preview
):
    scheduled = []
    writers = []

    class FailingWriter:
        def __init__(self, template_path):
            self.closed = False
            writers.append(self)

        def get_sheet_names(self):
            return ["张三"]

        def add_task(self, sheet_name, task, analysis):
            raise RuntimeError("write failed")

        def close(self):
            self.closed = True

    monkeypatch.setattr(app_module, "ExcelWriter", FailingWriter)
    monkeypatch.setattr(app_module.asyncio, "create_task", scheduled.append)

    response = client_with_preview.post(
        "/api/export", data={"sheet_name": "张三", "output_path": "failure.xlsx"}
    )

    assert response.status_code == 202
    assert len(writers) == 1
    asyncio.run(scheduled[0])
    assert len(writers) == 2
    assert writers[1].closed is True


def test_manual_key_route_stores_connection(monkeypatch):
    connected = object()

    class Result:
        manager = object()
        database = connected
        shard_count = 1
        table_count = 2

    monkeypatch.setattr(app_module, "connect_wechat", lambda key=None: Result())
    client = TestClient(app_module.app)

    client.get("/")
    response = client.post("/api/key/validate", data={"key": "ab" * 32})

    assert response.status_code == 200
    assert "验证通过" in response.text
    session_id = client.cookies["session_id"]
    assert response.headers["content-type"].startswith("text/html")
    assert app_module.session_state[session_id]["wdb"] is Result.manager
    assert app_module.session_state[session_id]["ddb"] is connected


def test_connection_failure_is_html_escaped(monkeypatch):
    def fail(key=None):
        raise RuntimeError("<unsafe>")

    monkeypatch.setattr(app_module, "connect_wechat", fail)
    client = TestClient(app_module.app)

    response = client.post("/api/key/validate", data={"key": "ab" * 32})

    assert response.status_code == 400
    assert "&lt;unsafe&gt;" in response.text
    assert "<unsafe>" not in response.text


def test_explorer_token_failure_shows_api_specific_recovery(monkeypatch):
    def fail(key=None):
        raise RuntimeError("未设置 WECHATEXPLORER_API_TOKEN")

    monkeypatch.setattr(app_module, "connect_wechat", fail)
    client = TestClient(app_module.app)

    response = client.post("/api/key/extract")

    assert response.status_code == 400
    assert "请在 WechatExplorer 的 API Center 开启本机 API 并设置令牌后重试。" in response.text
    assert "可在高级选项中输入密钥" not in response.text


def test_manual_key_route_closes_previous_session_database(monkeypatch):
    class PreviousDatabase:
        def __init__(self):
            self.closed = 0

        def close_all(self):
            self.closed += 1

    previous = PreviousDatabase()
    connected = object()

    class Result:
        manager = object()
        database = connected
        shard_count = 1
        table_count = 2

    monkeypatch.setattr(app_module, "connect_wechat", lambda key=None: Result())
    client = TestClient(app_module.app)
    client.get("/")
    session_id = client.cookies["session_id"]
    app_module.session_state[session_id]["ddb"] = previous

    response = client.post("/api/key/validate", data={"key": "ab" * 32})

    assert response.status_code == 200
    assert previous.closed == 1


def test_cookie_less_manual_key_post_sets_reusable_session_cookie(monkeypatch):
    connected = object()

    class Result:
        manager = object()
        database = connected
        shard_count = 1
        table_count = 2

    monkeypatch.setattr(app_module, "connect_wechat", lambda key=None: Result())
    client = TestClient(app_module.app)

    response = client.post("/api/key/validate", data={"key": "ab" * 32})

    session_id = response.cookies["session_id"]
    assert session_id in app_module.session_state
    assert app_module.session_state[session_id]["ddb"] is connected
    assert client.cookies["session_id"] == session_id


def test_stale_cookie_manual_key_post_replaces_session_cookie(monkeypatch):
    connected = object()

    class Result:
        manager = object()
        database = connected
        shard_count = 1
        table_count = 2

    monkeypatch.setattr(app_module, "connect_wechat", lambda key=None: Result())
    client = TestClient(app_module.app)
    client.cookies.set("session_id", "stale-session")

    response = client.post("/api/key/validate", data={"key": "ab" * 32})

    session_id = response.cookies["session_id"]
    assert session_id != "stale-session"
    assert app_module.session_state[session_id]["ddb"] is connected


def test_wechat_status_escapes_all_scanner_values(monkeypatch):
    hostile = '<img src=x onerror="alert(1)">&entity;'

    class Scanner:
        def __init__(self, **kwargs):
            pass

        def scan(self):
            return type(
                "Info",
                (),
                {
                    "version": hostile,
                    "install_path": hostile,
                    "pid": hostile,
                    "data_dir": hostile,
                    "errors": [hostile],
                },
            )()

    monkeypatch.setattr(app_module, "WeChatScanner", Scanner)

    response = TestClient(app_module.app).get("/api/wechat/status")

    assert response.status_code == 200
    assert "<img" not in response.text
    assert response.text.count("&lt;img") == 5


def test_chatroom_list_escapes_text_and_quoted_attributes(monkeypatch, client_with_database):
    monkeypatch.setattr(
        app_module,
        "_get_chatrooms",
        lambda database: [
            ('evil" onclick="alert(1)@chatroom', "<img src=x onerror=alert(1)>&entity;")
        ],
    )

    response = client_with_database.get("/api/chatrooms/list")

    assert response.status_code == 200
    assert "<img" not in response.text
    assert "&lt;img src=x onerror=alert(1)&gt;&amp;entity;" in response.text
    assert 'data-group="evil&#34; onclick=&#34;alert(1)@chatroom"' in response.text
    assert 'data-group="evil" onclick=' not in response.text


def test_preview_escapes_group_sheet_and_message_content(monkeypatch, client_with_database):
    hostile = "<img src=x onerror=alert(1)>&entity;"
    monkeypatch.setattr(
        app_module,
        "_get_messages",
        lambda *args, **kwargs: [
            {
                "msg_id": 1,
                "content": f"🚩8.2 任务\n1⃣ {hostile}",
                "timestamp": int(datetime(2026, 8, 2).timestamp()),  # noqa: DTZ001
            }
        ],
    )

    response = client_with_database.post(
        "/api/preview",
        data={
            "group_name": hostile,
            "sheet_name": "张三",
            "start_date": "2026-08-01",
            "end_date": "2026-08-03",
        },
    )

    assert response.status_code == 200
    assert "<img" not in response.text
    assert response.text.count("&lt;img src=x onerror=alert(1)&gt;&amp;entity;") == 2


def test_query_failure_returns_escaped_server_error_fragment(monkeypatch, client_with_database):
    def fail(database):
        raise RuntimeError("<unsafe shard>")

    monkeypatch.setattr(app_module, "_get_chatrooms", fail)

    response = client_with_database.get("/api/chatrooms/list")

    assert response.status_code == 500
    assert "&lt;unsafe shard&gt;" in response.text
    assert "<unsafe shard>" not in response.text


def test_base_page_enables_htmx_swapping_for_error_fragments():
    response = TestClient(app_module.app).get("/")

    assert response.status_code == 200
    assert "htmx:beforeSwap" in response.text
    assert "detail.shouldSwap = true" in response.text


@pytest.mark.parametrize(
    ("route", "data", "expected_key"),
    [
        ("/api/key/extract", {}, None),
        ("/api/key/validate", {"key": "ab" * 32}, "ab" * 32),
    ],
)
def test_connection_routes_pass_configured_wechat_paths(
    monkeypatch, route, data, expected_key
):
    calls = []

    class Result:
        manager = object()
        database = object()
        shard_count = 1
        table_count = 2

    def connect(key=None, **kwargs):
        calls.append((key, kwargs))
        return Result()

    monkeypatch.setattr(app_module, "connect_wechat", connect)
    monkeypatch.setattr(app_module.config.wechat, "version_dir", "X:/custom/version")
    monkeypatch.setattr(app_module.config.wechat, "data_dir", "Y:/custom/data")

    response = TestClient(app_module.app).post(route, data=data)

    assert response.status_code == 200
    assert calls == [
        (
            expected_key,
            {"install_path": "X:/custom/version", "data_dir": "Y:/custom/data"},
        )
    ]


def test_export_page_parses_json_sse_and_uses_text_content(monkeypatch, client_with_preview):
    scheduled = []

    class PendingTask:
        def add_done_callback(self, callback):
            pass

    def capture(coroutine):
        scheduled.append(coroutine)
        return PendingTask()

    monkeypatch.setattr(app_module.asyncio, "create_task", capture)

    response = client_with_preview.post(
        "/api/export", data={"sheet_name": "张三", "output_path": "safe.xlsx"}
    )

    for coroutine in scheduled:
        coroutine.close()
    script = client_with_preview.get("/static/wizard.js")
    assert response.status_code == 202
    assert "JSON.parse(event.data)" in script.text
    assert ".innerHTML" not in script.text
    assert ".textContent" in script.text
    assert "/api/progress/stream?job_id=" in response.text


class RunningTask:
    def done(self):
        return False


def test_second_export_is_rejected_while_session_job_is_running(client_with_preview):
    session_id = client_with_preview.cookies["session_id"]
    app_module.export_tasks["busy-job"] = RunningTask()
    app_module.session_jobs[session_id] = "busy-job"

    response = client_with_preview.post(
        "/api/export", data={"sheet_name": "张三", "output_path": "second.xlsx"}
    )

    assert response.status_code == 409
    assert "导出任务正在进行" in response.text


def test_reconnect_is_rejected_while_session_job_is_running(monkeypatch, client_with_preview):
    calls = []
    session_id = client_with_preview.cookies["session_id"]
    app_module.export_tasks["busy-job"] = RunningTask()
    app_module.session_jobs[session_id] = "busy-job"
    monkeypatch.setattr(app_module, "connect_wechat", lambda *args, **kwargs: calls.append(True))

    response = client_with_preview.post("/api/key/validate", data={"key": "ab" * 32})

    assert response.status_code == 409
    assert "导出任务正在进行" in response.text
    assert calls == []


def test_export_job_snapshots_mutable_session_inputs(monkeypatch, client_with_preview):
    jobs = []
    closed = []

    class FakeWriter:
        def __init__(self, template_path):
            pass

        def get_sheet_names(self):
            return ["张三"]

        def close(self):
            closed.append(True)

    def reject_fallback_scheduler(coroutine):
        coroutine.close()
        raise AssertionError("route bypassed retained job scheduler")

    monkeypatch.setattr(app_module, "ExcelWriter", FakeWriter)
    monkeypatch.setattr(app_module, "_start_export_job", jobs.append, raising=False)
    monkeypatch.setattr(app_module.asyncio, "create_task", reject_fallback_scheduler)
    session_id = client_with_preview.cookies["session_id"]
    state = app_module.session_state[session_id]
    state["analysis_by_date"] = {"2026-08-02": ["original context"]}

    response = client_with_preview.post(
        "/api/export", data={"sheet_name": "张三", "output_path": "snapshot.xlsx"}
    )

    assert response.status_code == 202
    assert len(jobs) == 1
    job = jobs[0]
    state["parsed_tasks"][0].tasks.append("late mutation")
    state["analysis_by_date"]["2026-08-02"].append("late context")
    state["selected_group"] = "different@chatroom"
    assert job.session_id == session_id
    assert job.group == "group@chatroom"
    assert job.parsed_tasks[0].tasks == ("测试任务",)
    assert job.analysis_by_date == (("2026-08-02", ("original context",)),)
    assert closed == [True]


def test_replacing_connection_disposes_old_workflow_state(monkeypatch):
    class PreviousDatabase:
        def __init__(self):
            self.closed = 0

        def close_all(self):
            self.closed += 1

    previous = PreviousDatabase()
    connected = object()

    class Result:
        manager = object()
        database = connected
        shard_count = 1
        table_count = 2

    monkeypatch.setattr(app_module, "connect_wechat", lambda *args, **kwargs: Result())
    client = TestClient(app_module.app)
    client.get("/")
    session_id = client.cookies["session_id"]
    old_state = app_module.session_state[session_id]
    old_state["ddb"] = previous
    old_state["selected_group"] = "stale@chatroom"
    old_state["parsed_tasks"] = [object()]

    response = client.post("/api/key/validate", data={"key": "ab" * 32})

    assert response.status_code == 200
    new_state = app_module.session_state[session_id]
    assert new_state is not old_state
    assert new_state["ddb"] is connected
    assert new_state["selected_group"] is None
    assert new_state["parsed_tasks"] == []
    assert previous.closed == 1

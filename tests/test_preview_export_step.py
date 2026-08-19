import asyncio
from datetime import date

import pytest
from fastapi.testclient import TestClient
from openpyxl import Workbook

import app as app_module
from core.task_parser import ParsedTask
from schemas.catalog import ChatroomOption
from schemas.wizard import WizardSelection, WizardStep
from services.credential_store import CredentialStore
from services.model_settings import (
    ModelProfile,
    ModelSettingsError,
    ModelSettingsService,
    ModelSettingsStatus,
    ModelSettingsStore,
    ResolvedModelProfile,
)


@pytest.fixture(autouse=True)
def reset_in_memory_state():
    app_module.session_state.clear()
    app_module.export_tasks.clear()
    app_module.session_jobs.clear()
    app_module.job_owners.clear()
    app_module.progress_hub.clear()
    yield
    app_module.session_state.clear()
    app_module.export_tasks.clear()
    app_module.session_jobs.clear()
    app_module.job_owners.clear()
    app_module.progress_hub.clear()


@pytest.fixture
def preview_client(monkeypatch, tmp_path):
    template_path = tmp_path / "template.xlsx"
    workbook = Workbook()
    workbook.active.title = "项目群"
    workbook.save(template_path)
    workbook.close()

    monkeypatch.setattr(app_module.config.excel, "template_path", str(template_path))
    monkeypatch.setattr(app_module.config.excel, "output_dir", str(tmp_path / "exports"))
    monkeypatch.setattr(app_module.config.ai, "enabled", True)
    monkeypatch.setattr(app_module.config.ai, "provider", "deepseek")
    monkeypatch.setattr(app_module.config.voice, "enabled", True)
    monkeypatch.setattr(app_module.config.voice, "transcriber", "openai")
    monkeypatch.setattr(
        app_module.app.state,
        "model_settings",
        _ModelSettingsStub(ready=False),
    )

    client = TestClient(app_module.app)
    client.get("/")
    state = app_module.session_state[client.cookies["session_id"]]
    state["ddb"] = object()
    state["selected_group"] = "project@chatroom"
    state["selected_sheet"] = "项目群"
    state["start_date"] = "2026-08-01"
    state["end_date"] = "2026-08-03"
    state["catalog_sheet_names"] = ("项目群",)
    state["chatroom_catalog"] = (
        ChatroomOption("project@chatroom", "项目群", "项目群", None, 2),
    )
    state["parsed_tasks"] = [
        ParsedTask(11, date(2026, 8, 2), 46236, "任务一", ["修订方案"]),
        ParsedTask(12, date(2026, 8, 2), 46236, "任务二", ["确认排期"]),
        ParsedTask(13, date(2026, 8, 3), 46237, "任务三", ["发布版本"]),
    ]
    wizard = state["wizard"]
    wizard.connected = True
    wizard.selection = WizardSelection(
        "project@chatroom",
        "项目群",
        date(2026, 8, 1),
        date(2026, 8, 3),
        "项目群",
    )
    wizard.preview_tasks = list(state["parsed_tasks"])
    wizard.preview_ready = True
    wizard.active_step = WizardStep.PREVIEW
    return client


def test_preview_groups_checked_tasks_with_stable_ids_and_summary(preview_client):
    response = preview_client.get("/wizard/3")

    assert response.status_code == 200
    assert "项目群" in response.text
    assert "2026-08-01 至 2026-08-03" in response.text
    assert "Sheet：项目群" in response.text
    assert "共 3 条" in response.text
    assert response.text.count('name="task_id"') == 3
    assert 'value="11"' in response.text
    assert 'value="12"' in response.text
    assert 'value="13"' in response.text
    assert response.text.count(">2026-08-02</h3>") == 1
    assert response.text.count(">2026-08-03</h3>") == 1
    assert "checked" in response.text
    assert "外部处理选项" in response.text
    assert "AI 模型尚未配置" in response.text
    assert 'href="/settings/model"' in response.text
    assert "openai" in response.text


def test_preview_disables_ai_until_model_is_verified(preview_client):
    response = preview_client.get("/wizard/3")

    assert response.status_code == 200
    assert 'name="enable_ai" value="true"' in response.text
    assert "disabled" in response.text
    assert "请先完成模型设置和连接测试" in response.text


def test_ai_export_requires_ready_model_even_after_privacy_acknowledgement(
    monkeypatch, preview_client
):
    jobs = []
    monkeypatch.setattr(app_module, "_start_export_job", jobs.append)

    response = preview_client.post(
        "/api/export",
        data={
            "sheet_name": "项目群",
            "enable_ai": "true",
            "privacy_acknowledged": "true",
            "output_path": "result.xlsx",
            "task_id": "11",
        },
    )

    assert response.status_code == 422
    assert "请先完成模型设置和连接测试" in response.text
    assert 'name="enable_ai" value="true"' in response.text
    assert 'name="privacy_acknowledged" value="true"' in response.text
    assert jobs == []


def test_ai_export_snapshots_resolved_model_profile(monkeypatch, preview_client):
    jobs = []
    resolved = ResolvedModelProfile(
        ModelProfile(
            provider_name="兼容服务",
            api_base="https://model.example.test",
            model="chat-v1",
            enabled=True,
            verified=True,
        ),
        "secret-at-schedule-time",
    )
    monkeypatch.setattr(
        app_module.app.state,
        "model_settings",
        _ModelSettingsStub(ready=True, resolved=resolved),
    )
    monkeypatch.setattr(app_module, "_start_export_job", jobs.append)

    response = preview_client.post(
        "/api/export",
        data={
            "sheet_name": "项目群",
            "enable_ai": "true",
            "privacy_acknowledged": "true",
            "output_path": "result.xlsx",
            "task_id": "11",
        },
    )

    assert response.status_code == 202
    assert jobs[0].model_profile == resolved


def test_corrupt_model_credential_disables_preview_and_export(
    monkeypatch, preview_client, tmp_path
):
    async def tester(settings):
        return None

    service = ModelSettingsService(
        ModelSettingsStore(tmp_path / "model.json"),
        CredentialStore(
            tmp_path / "model-key.dpapi",
            _ReversingProtector(),
        ),
        tester,
    )
    asyncio.run(
        service.save_and_test(
            provider_name="Local",
            api_base="http://127.0.0.1:11434",
            model="qwen",
            enabled=True,
            api_key="private-key",
        )
    )
    service.credential_store.path.write_bytes(b"")
    monkeypatch.setattr(
        app_module.app.state,
        "model_settings",
        service,
    )

    preview = preview_client.get("/wizard/3")
    export = preview_client.post(
        "/api/export",
        data={
            "sheet_name": "项目群",
            "enable_ai": "true",
            "privacy_acknowledged": "true",
            "output_path": "result.xlsx",
            "task_id": "11",
        },
    )

    assert preview.status_code == 200
    assert "AI 模型尚未配置" in preview.text
    assert "disabled" in preview.text
    assert export.status_code == 422
    assert "请先完成模型设置和连接测试" in export.text


def test_external_options_require_acknowledgement(preview_client):
    response = preview_client.post(
        "/api/export",
        data={
            "sheet_name": "项目群",
            "enable_ai": "true",
            "output_path": "result.xlsx",
            "task_id": "11",
        },
    )

    assert response.status_code == 422
    assert "确认可能发送到外部服务" in response.text
    assert 'name="task_id"' in response.text


def test_selected_ids_create_immutable_export_snapshot(monkeypatch, preview_client):
    jobs = []
    monkeypatch.setattr(app_module, "_start_export_job", jobs.append)

    response = preview_client.post(
        "/api/export",
        data={
            "sheet_name": "项目群",
            "output_path": "selected.xlsx",
            "task_selection_present": "true",
            "task_id": ["12", "13"],
        },
    )

    assert response.status_code == 202
    assert len(jobs) == 1
    assert [task.msg_id for task in jobs[0].parsed_tasks] == [12, 13]
    state = app_module.session_state[preview_client.cookies["session_id"]]
    state["parsed_tasks"][1].tasks.append("后续变更")
    assert jobs[0].parsed_tasks[0].tasks == ("确认排期",)


def test_duplicate_active_export_returns_same_progress_fragment(preview_client):
    session_id = preview_client.cookies["session_id"]
    app_module.export_tasks["active-job"] = _RunningTask()
    app_module.session_jobs[session_id] = "active-job"
    app_module.job_owners["active-job"] = session_id

    response = preview_client.post(
        "/api/export",
        data={"sheet_name": "项目群", "output_path": "second.xlsx"},
    )

    assert response.status_code == 409
    assert 'data-job-id="active-job"' in response.text
    assert 'data-export-state="active"' in response.text
    assert 'role="progressbar"' in response.text
    assert "/api/progress/stream?job_id=active-job" in response.text


def test_export_failure_fragment_preserves_selection_and_offers_retry(
    monkeypatch, preview_client
):
    def fail_to_start(job):
        raise RuntimeError("worker unavailable")

    monkeypatch.setattr(app_module, "_start_export_job", fail_to_start)

    response = preview_client.post(
        "/api/export",
        data={
            "sheet_name": "项目群",
            "output_path": "retry.xlsx",
            "task_selection_present": "true",
            "task_id": ["11", "13"],
            "enable_voice": "true",
            "privacy_acknowledged": "true",
        },
    )

    assert response.status_code == 500
    assert 'aria-valuenow="0"' in response.text
    assert 'data-export-state="failed"' in response.text
    assert "重试导出" in response.text
    assert response.text.count('name="task_id"') == 2
    assert 'value="retry.xlsx"' in response.text
    assert 'name="enable_voice" value="true"' in response.text
    assert 'name="privacy_acknowledged" value="true"' in response.text


def test_empty_explicit_task_selection_is_rejected_without_scheduling(
    monkeypatch, preview_client
):
    jobs = []
    monkeypatch.setattr(app_module, "_start_export_job", jobs.append)

    response = preview_client.post(
        "/api/export",
        data={
            "sheet_name": "项目群",
            "output_path": "empty.xlsx",
            "task_selection_present": "true",
        },
    )

    assert response.status_code == 422
    assert "至少选择一条任务" in response.text
    assert jobs == []


def test_export_progress_fragment_is_driven_by_external_script(
    monkeypatch, preview_client
):
    monkeypatch.setattr(app_module, "_start_export_job", lambda job: None)
    response = preview_client.post(
        "/api/export",
        data={"sheet_name": "项目群", "output_path": "progress.xlsx"},
    )
    script = preview_client.get("/static/wizard.js")

    assert 'role="progressbar"' in response.text
    assert "JSON.parse(event.data)" in script.text
    assert ".textContent" in script.text
    assert ".innerHTML" not in script.text


class _RunningTask:
    def done(self):
        return False


class _ModelSettingsStub:
    def __init__(self, *, ready, resolved=None):
        self._status = ModelSettingsStatus(
            profile=(resolved.profile if resolved else ModelProfile()),
            has_api_key=ready,
            ready=ready,
        )
        self._resolved = resolved

    def status(self):
        return self._status

    def resolved_profile(self):
        if self._resolved is None:
            raise ModelSettingsError("模型尚未启用并通过连接测试")
        return self._resolved


class _ReversingProtector:
    def protect(self, value):
        return value[::-1]

    def unprotect(self, value):
        return value[::-1]

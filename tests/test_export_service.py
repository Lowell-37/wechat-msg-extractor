import asyncio
from datetime import date

import pytest

from config import AppConfig
from core.ai_analyzer import AIAnalysisError
from core.progress import progress_hub
from core.task_parser import ParsedTask
from services import export as export_service
from services.model_settings import ModelProfile, ResolvedModelProfile


@pytest.fixture(autouse=True)
def reset_export_runtime(monkeypatch):
    progress_hub.clear()
    export_service.export_tasks.clear()
    export_service.session_jobs.clear()
    export_service.job_owners.clear()
    monkeypatch.setattr(export_service, "_config_provider", lambda: AppConfig())
    yield
    progress_hub.clear()
    export_service.export_tasks.clear()
    export_service.session_jobs.clear()
    export_service.job_owners.clear()


def test_export_uses_snapshotted_model_profile(monkeypatch, tmp_path):
    resolved = _resolved_profile()
    analyzer = _RecordingAnalyzer("分析完成")
    writer = _RecordingWriter()
    received_profiles = []
    monkeypatch.setattr(export_service, "_writer_factory", lambda path: writer)
    monkeypatch.setattr(export_service, "_voice_factory", lambda *args: None)
    monkeypatch.setattr(
        export_service,
        "_analyzer_factory",
        lambda profile: (received_profiles.append(profile), analyzer)[1],
    )
    job = _job(tmp_path, model_profile=resolved)

    asyncio.run(export_service.run_export_job(job))

    assert received_profiles == [resolved]
    assert analyzer.calls == [(["完成任务"], ["讨论上下文"], "2026-08-02")]
    assert writer.rows[0][2] == "分析完成"


def test_ai_failure_writes_explicit_marker_and_emits_warning(monkeypatch, tmp_path):
    writer = _RecordingWriter()
    monkeypatch.setattr(export_service, "_writer_factory", lambda path: writer)
    monkeypatch.setattr(export_service, "_voice_factory", lambda *args: None)
    monkeypatch.setattr(
        export_service,
        "_analyzer_factory",
        lambda profile: _FailingAnalyzer(),
    )
    job = _job(tmp_path, model_profile=_resolved_profile())
    queue = progress_hub.register(job.job_id)

    asyncio.run(export_service.run_export_job(job))

    events = []
    while not queue.empty():
        events.append(queue.get_nowait())
    assert any(event.stage == "warning" and "AI 分析失败" in event.message for event in events)
    assert writer.rows[0][2] == "[AI 分析失败] 模型服务请求超时"
    assert writer.saved == [str(tmp_path / "result.xlsx")]


def test_export_without_ai_keeps_local_context_fallback(monkeypatch, tmp_path):
    writer = _RecordingWriter()
    monkeypatch.setattr(export_service, "_writer_factory", lambda path: writer)
    monkeypatch.setattr(export_service, "_voice_factory", lambda *args: None)
    monkeypatch.setattr(
        export_service,
        "_analyzer_factory",
        lambda profile: (_ for _ in ()).throw(AssertionError("AI should be off")),
    )

    asyncio.run(export_service.run_export_job(_job(tmp_path)))

    assert writer.rows[0][2] == "讨论上下文"


def _resolved_profile():
    return ResolvedModelProfile(
        ModelProfile(
            provider_name="兼容服务",
            api_base="https://model.example.test",
            model="chat-v1",
            enabled=True,
            verified=True,
        ),
        "snapshot-secret",
    )


def _job(tmp_path, *, model_profile=None):
    return export_service.create_export_job(
        job_id="job-1",
        session_id="session-1",
        database=object(),
        group="group@chatroom",
        sheet_name="项目群",
        template_path=str(tmp_path / "template.xlsx"),
        output_path=str(tmp_path / "result.xlsx"),
        start_date=date(2026, 8, 1),
        end_date=date(2026, 8, 3),
        parsed_tasks=[
            ParsedTask(1, date(2026, 8, 2), 46236, "原文", ["完成任务"])
        ],
        analysis_by_date={"2026-08-02": ["讨论上下文"]},
        enable_ai=model_profile is not None,
        model_profile=model_profile,
    )


class _RecordingAnalyzer:
    def __init__(self, result):
        self.result = result
        self.calls = []

    async def analyze(self, tasks, context, date_key):
        self.calls.append((tasks, context, date_key))
        return self.result


class _FailingAnalyzer:
    async def analyze(self, tasks, context, date_key):
        raise AIAnalysisError("模型服务请求超时")


class _RecordingWriter:
    def __init__(self):
        self.rows = []
        self.saved = []
        self.closed = False

    def add_task(self, sheet_name, task, analysis):
        self.rows.append((sheet_name, task, analysis))

    def save(self, output_path):
        self.saved.append(output_path)

    def close(self):
        self.closed = True

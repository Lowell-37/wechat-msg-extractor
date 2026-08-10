import asyncio
import logging
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any

from config import AppConfig
from core.progress import ProgressEvent, progress_hub
from core.task_parser import ParsedTask

logger = logging.getLogger("app")

export_tasks: dict[str, Any] = {}
session_jobs: dict[str, str] = {}
job_owners: dict[str, str] = {}

_config_provider: Callable[[], AppConfig] | None = None
_writer_factory: Callable[[str], Any] | None = None
_voice_factory: Callable[[Any, Any], Any] | None = None
_analyzer_factory: Callable[[Any], Any] | None = None


@dataclass(frozen=True)
class ExportTaskSnapshot:
    msg_id: int
    date: date
    date_excel_serial: int
    raw_text: str
    tasks: tuple[str, ...]


@dataclass(frozen=True)
class ExportJob:
    job_id: str
    session_id: str
    database: Any
    group: str
    sheet_name: str
    template_path: str
    output_path: str
    start_date: date
    end_date: date
    parsed_tasks: tuple[ExportTaskSnapshot, ...]
    analysis_by_date: tuple[tuple[str, tuple[str, ...]], ...]


def configure(
    *,
    config_provider: Callable[[], AppConfig],
    writer_factory: Callable[[str], Any],
    voice_factory: Callable[[Any, Any], Any],
    analyzer_factory: Callable[[Any], Any],
) -> None:
    """Bind composition-root dependencies used by background export workers."""
    global _config_provider, _writer_factory, _voice_factory, _analyzer_factory
    _config_provider = config_provider
    _writer_factory = writer_factory
    _voice_factory = voice_factory
    _analyzer_factory = analyzer_factory


def create_export_job(
    *,
    job_id: str,
    session_id: str,
    database: Any,
    group: str,
    sheet_name: str,
    template_path: str,
    output_path: str,
    start_date: date,
    end_date: date,
    parsed_tasks: list[ParsedTask],
    analysis_by_date: dict[str, list[str]],
) -> ExportJob:
    """Create an immutable export snapshot from mutable session inputs."""
    return ExportJob(
        job_id=job_id,
        session_id=session_id,
        database=database,
        group=group,
        sheet_name=sheet_name,
        template_path=template_path,
        output_path=output_path,
        start_date=start_date,
        end_date=end_date,
        parsed_tasks=_snapshot_parsed_tasks(parsed_tasks),
        analysis_by_date=_snapshot_analysis(analysis_by_date),
    )


def _snapshot_parsed_tasks(
    tasks: list[ParsedTask],
) -> tuple[ExportTaskSnapshot, ...]:
    return tuple(
        ExportTaskSnapshot(
            msg_id=task.msg_id,
            date=task.date,
            date_excel_serial=task.date_excel_serial,
            raw_text=task.raw_text,
            tasks=tuple(task.tasks),
        )
        for task in tasks
    )


def _snapshot_analysis(
    analysis: dict[str, list[str]],
) -> tuple[tuple[str, tuple[str, ...]], ...]:
    return tuple(
        (date_key, tuple(messages)) for date_key, messages in analysis.items()
    )


def has_active_job(session_id: str) -> bool:
    job_id = session_jobs.get(session_id)
    if not job_id:
        return False
    task = export_tasks.get(job_id)
    if task is None:
        session_jobs.pop(session_id, None)
        export_tasks.pop(job_id, None)
        return False
    done = getattr(task, "done", None)
    if done is None or not done():
        return True
    session_jobs.pop(session_id, None)
    export_tasks.pop(job_id, None)
    return False


def start_export_job(job: ExportJob) -> None:
    progress_hub.register(job.job_id)
    session_jobs[job.session_id] = job.job_id
    job_owners[job.job_id] = job.session_id
    try:
        task = asyncio.create_task(run_export_job(job))
    except BaseException:
        session_jobs.pop(job.session_id, None)
        job_owners.pop(job.job_id, None)
        progress_hub.unregister(job.job_id)
        raise
    export_tasks[job.job_id] = task


def release_export_job(job: ExportJob) -> None:
    export_tasks.pop(job.job_id, None)
    if session_jobs.get(job.session_id) == job.job_id:
        session_jobs.pop(job.session_id, None)


def owns_job(job_id: str, session_id: str) -> bool:
    return job_owners.get(job_id) == session_id


def release_progress_owner(job_id: str, session_id: str) -> None:
    if owns_job(job_id, session_id):
        job_owners.pop(job_id, None)


async def run_export_job(job: ExportJob) -> None:
    config, writer_factory, voice_factory, analyzer_factory = _runtime()
    writer = None
    try:
        analysis_by_date = {
            date_key: list(messages) for date_key, messages in job.analysis_by_date
        }
        total = len(job.parsed_tasks)

        if config.voice.enabled and config.voice.api_key:
            await progress_hub.emit(
                job.job_id,
                ProgressEvent(
                    stage="voice",
                    message="正在转写语音消息...",
                    progress=5,
                ),
            )
            try:
                transcriber = voice_factory(job.database, config.voice)
                start_ts = int(
                    datetime(  # noqa: DTZ001
                        job.start_date.year,
                        job.start_date.month,
                        job.start_date.day,
                    ).timestamp()
                )
                end_ts = int(
                    datetime(  # noqa: DTZ001
                        job.end_date.year,
                        job.end_date.month,
                        job.end_date.day,
                        23,
                        59,
                        59,
                    ).timestamp()
                )
                voice_texts = await transcriber.transcribe_all(
                    job.group, start_ts, end_ts
                )
                for date_key, texts in voice_texts.items():
                    analysis_by_date.setdefault(date_key, []).extend(texts)
            except Exception:
                logger.warning(
                    "Voice transcription failed for session %s",
                    job.session_id,
                    exc_info=True,
                )
                await progress_hub.emit(
                    job.job_id,
                    ProgressEvent(
                        stage="warning",
                        message="部分语音转写失败，继续导出文本任务",
                        progress=8,
                    ),
                )

        analyzer = None
        if config.ai.enabled and config.ai.api_key:
            analyzer = analyzer_factory(config.ai)

        await progress_hub.emit(
            job.job_id,
            ProgressEvent(
                stage="write",
                message="正在写入任务数据...",
                progress=10,
            ),
        )
        writer = writer_factory(job.template_path)

        for index, task in enumerate(job.parsed_tasks):
            date_key = task.date.isoformat()
            context = analysis_by_date.get(date_key, [])
            if analyzer:
                progress = 10 + int(70 * (index + 1) / total)
                await progress_hub.emit(
                    job.job_id,
                    ProgressEvent(
                        stage="ai",
                        message=f"AI分析中 ({index + 1}/{total}): {date_key}",
                        progress=progress,
                    ),
                )
                analysis = await analyzer.analyze(task.tasks, context, date_key)
            else:
                analysis = "\n".join(context) if context else ""
            writer.add_task(job.sheet_name, task, analysis)

        await progress_hub.emit(
            job.job_id,
            ProgressEvent(
                stage="save",
                message="正在保存文件...",
                progress=85,
            ),
        )
        writer.save(job.output_path)
        await progress_hub.emit(
            job.job_id,
            ProgressEvent(
                stage="done",
                message=f"导出完成！文件保存在：{job.output_path}",
                progress=100,
                detail={"path": job.output_path},
            ),
        )
    except asyncio.CancelledError:
        raise
    except Exception as exc:  # noqa: BLE001
        await progress_hub.emit(
            job.job_id,
            ProgressEvent(
                stage="error",
                message=f"导出失败：{exc}",
                progress=0,
            ),
        )
    finally:
        if writer:
            writer.close()
        release_export_job(job)


async def shutdown_export_jobs() -> None:
    tasks = [
        task for task in export_tasks.values() if isinstance(task, asyncio.Task)
    ]
    for task in tasks:
        task.cancel()
    if tasks:
        await asyncio.gather(*tasks, return_exceptions=True)
    export_tasks.clear()
    session_jobs.clear()
    job_owners.clear()
    progress_hub.clear()


def _runtime() -> tuple[
    AppConfig,
    Callable[[str], Any],
    Callable[[Any, Any], Any],
    Callable[[Any], Any],
]:
    config_provider = _config_provider
    writer_factory = _writer_factory
    voice_factory = _voice_factory
    analyzer_factory = _analyzer_factory
    if None in (
        config_provider,
        writer_factory,
        voice_factory,
        analyzer_factory,
    ):
        raise RuntimeError("Export service is not configured")
    return (
        config_provider(),
        writer_factory,
        voice_factory,
        analyzer_factory,
    )

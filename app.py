import asyncio as _asyncio
import logging
from pathlib import Path

import uvicorn
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from config import AppConfig
from core.ai_analyzer import create_analyzer as _create_analyzer
from core.connection import connect_wechat as _connect_wechat
from core.excel_writer import ExcelWriter as _ExcelWriter
from core.progress import progress_hub
from core.scanner import WeChatScanner as _WeChatScanner
from core.task_parser import TaskParser as _TaskParser
from core.voice import VoiceTranscriber as _VoiceTranscriber
from routers import actions, wizard
from services import export as export_service
from services import session as session_service

BASE_DIR = Path(__file__).parent
logger = logging.getLogger(__name__)
config = AppConfig.from_yaml()
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))

# Compatibility aliases retained for runtime imports and the existing test suite.
asyncio = _asyncio
create_analyzer = _create_analyzer
connect_wechat = _connect_wechat
ExcelWriter = _ExcelWriter
WeChatScanner = _WeChatScanner
TaskParser = _TaskParser
VoiceTranscriber = _VoiceTranscriber
SESSION_TTL_SECONDS = session_service.SESSION_TTL_SECONDS
MAX_SESSIONS = session_service.MAX_SESSIONS
session_state = session_service.session_state
export_tasks = export_service.export_tasks
session_jobs = export_service.session_jobs
job_owners = export_service.job_owners

ExportTaskSnapshot = export_service.ExportTaskSnapshot
ExportJob = export_service.ExportJob
_snapshot_parsed_tasks = export_service._snapshot_parsed_tasks
_snapshot_analysis = export_service._snapshot_analysis
_release_export_job = export_service.release_export_job
_start_export_job = export_service.start_export_job
_run_export_job = export_service.run_export_job
_session_has_active_job = export_service.has_active_job

_session_clock = session_service._session_clock
_new_session_state = session_service.new_session_state
_evict_sessions = session_service.evict_sessions
get_session = session_service.get_session
dispose_session = session_service.dispose_session
shutdown_resources = session_service.shutdown_resources

_get_chatrooms = actions._get_chatrooms
_get_messages = actions._get_messages


def create_app() -> FastAPI:
    """Compose the FastAPI application from routers and lifecycle services."""
    export_service.configure(
        config_provider=lambda: config,
        writer_factory=lambda path: globals()["ExcelWriter"](path),
        voice_factory=lambda database, voice_config: globals()[
            "VoiceTranscriber"
        ](database, voice_config),
        analyzer_factory=lambda analyzer_config: globals()["create_analyzer"](
            analyzer_config
        ),
    )
    session_service.configure(
        config_provider=lambda: config,
        query_chatrooms=lambda database: globals()["_get_chatrooms"](
            database
        ),
        fetch_messages=lambda *args, **kwargs: globals()["_get_messages"](
            *args, **kwargs
        ),
        excel_writer_factory=lambda path: globals()["ExcelWriter"](path),
        task_parser_factory=lambda: globals()["TaskParser"](),
        ttl_provider=lambda: globals()["SESSION_TTL_SECONDS"],
        capacity_provider=lambda: globals()["MAX_SESSIONS"],
        clock_provider=lambda: globals()["_session_clock"](),
    )

    application = FastAPI(title="微信聊天提取工具")
    application.mount(
        "/static",
        StaticFiles(directory=str(BASE_DIR / "static")),
        name="static",
    )
    application.state.config = config
    application.state.base_dir = BASE_DIR
    application.state.config_path = BASE_DIR / "config.yaml"
    application.state.templates = templates
    application.state.logger = logger
    application.state.progress_hub = progress_hub
    application.state.connect_wechat = (
        lambda key=None, **kwargs: globals()["connect_wechat"](
            key, **kwargs
        )
    )
    application.state.scanner_factory = (
        lambda **kwargs: globals()["WeChatScanner"](**kwargs)
    )
    application.state.writer_factory = (
        lambda path: globals()["ExcelWriter"](path)
    )
    application.state.task_parser_factory = (
        lambda: globals()["TaskParser"]()
    )
    application.state.start_export_job = (
        lambda job: globals()["_start_export_job"](job)
    )
    application.include_router(wizard.router)
    application.include_router(actions.router)
    application.add_event_handler("shutdown", shutdown_resources)
    return application


app = create_app()


if __name__ == "__main__":
    uvicorn.run(
        "app:app",
        host=config.server.host,
        port=config.server.port,
        reload=True,
    )

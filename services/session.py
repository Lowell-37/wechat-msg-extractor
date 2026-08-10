import logging
import time
import uuid
from collections.abc import Callable
from typing import Any

from fastapi import Request

from config import AppConfig
from core.progress import progress_hub
from schemas.catalog import CatalogDependencies, PreviewDependencies
from schemas.wizard import WizardState
from services import export as export_service

logger = logging.getLogger("app")

SESSION_TTL_SECONDS = 60 * 60
MAX_SESSIONS = 128
session_state: dict[str, dict[str, Any]] = {}

_config_provider: Callable[[], AppConfig] | None = None
_query_chatrooms: Callable[[Any], list[Any]] | None = None
_fetch_messages: Callable[..., list[Any]] | None = None
_excel_writer_factory: Callable[[str], Any] | None = None
_task_parser_factory: Callable[[], Any] | None = None
_ttl_provider: Callable[[], float] | None = None
_capacity_provider: Callable[[], int] | None = None
_clock_provider: Callable[[], float] | None = None


def configure(
    *,
    config_provider: Callable[[], AppConfig],
    query_chatrooms: Callable[[Any], list[Any]],
    fetch_messages: Callable[..., list[Any]],
    excel_writer_factory: Callable[[str], Any],
    task_parser_factory: Callable[[], Any],
    ttl_provider: Callable[[], float] | None = None,
    capacity_provider: Callable[[], int] | None = None,
    clock_provider: Callable[[], float] | None = None,
) -> None:
    """Bind composition-root dependencies used to create and evict sessions."""
    global _config_provider, _query_chatrooms, _fetch_messages
    global _excel_writer_factory, _task_parser_factory
    global _ttl_provider, _capacity_provider, _clock_provider
    _config_provider = config_provider
    _query_chatrooms = query_chatrooms
    _fetch_messages = fetch_messages
    _excel_writer_factory = excel_writer_factory
    _task_parser_factory = task_parser_factory
    _ttl_provider = ttl_provider
    _capacity_provider = capacity_provider
    _clock_provider = clock_provider


def _session_clock() -> float:
    return time.monotonic()


def new_session_state(now: float | None = None) -> dict[str, Any]:
    (
        config,
        query_chatrooms,
        fetch_messages,
        excel_writer_factory,
        task_parser_factory,
    ) = _state_dependencies()
    return {
        "wdb": None,
        "ddb": None,
        "selected_group": None,
        "selected_sheet": None,
        "start_date": None,
        "end_date": None,
        "parsed_tasks": [],
        "analysis_by_date": {},
        "chatroom_catalog": None,
        "catalog_sheet_names": (),
        "catalog_dependencies": CatalogDependencies(
            query_chatrooms=query_chatrooms,
            excel_writer_factory=excel_writer_factory,
            manual_group_sheet_map=config.matching.group_sheet_map,
        ),
        "preview_dependencies": PreviewDependencies(
            fetch_messages=fetch_messages,
            task_parser_factory=task_parser_factory,
        ),
        "wizard": WizardState(),
        "last_access": session_clock() if now is None else now,
    }


def get_session(request: Request) -> tuple[str, dict[str, Any]]:
    now = session_clock()
    evict_sessions(now)
    session_id = request.cookies.get("session_id")
    if session_id and session_id in session_state:
        state = session_state[session_id]
        state["last_access"] = now
        return session_id, state

    evict_sessions(now, reserve=1)
    session_id = str(uuid.uuid4())
    state = new_session_state(now)
    session_state[session_id] = state
    return session_id, state


def dispose_session(session_id: str) -> None:
    state = session_state.pop(session_id, None)
    if state is not None:
        database = state.get("ddb")
        if database is not None:
            try:
                database.close_all()
            except Exception:
                logger.warning(
                    "Failed to close session database %s",
                    session_id,
                    exc_info=True,
                )
    export_service.session_jobs.pop(session_id, None)
    for job_id, owner in list(export_service.job_owners.items()):
        if owner == session_id:
            export_service.job_owners.pop(job_id, None)
            progress_hub.unregister(job_id)


def evict_sessions(now: float, reserve: int = 0) -> None:
    expired = [
        session_id
        for session_id, state in session_state.items()
        if now - state.get("last_access", 0.0) > session_ttl_seconds()
        and not export_service.has_active_job(session_id)
    ]
    for session_id in expired:
        dispose_session(session_id)

    target = max(0, max_sessions() - reserve)
    while len(session_state) > target:
        idle = [
            (state.get("last_access", 0.0), session_id)
            for session_id, state in session_state.items()
            if not export_service.has_active_job(session_id)
        ]
        if not idle:
            break
        _, oldest_id = min(idle)
        dispose_session(oldest_id)


async def shutdown_resources() -> None:
    await export_service.shutdown_export_jobs()
    for session_id in list(session_state):
        dispose_session(session_id)


def session_clock() -> float:
    if _clock_provider is not None:
        return _clock_provider()
    return _session_clock()


def session_ttl_seconds() -> float:
    if _ttl_provider is not None:
        return _ttl_provider()
    return SESSION_TTL_SECONDS


def max_sessions() -> int:
    if _capacity_provider is not None:
        return _capacity_provider()
    return MAX_SESSIONS


def _state_dependencies() -> tuple[
    AppConfig,
    Callable[[Any], list[Any]],
    Callable[..., list[Any]],
    Callable[[str], Any],
    Callable[[], Any],
]:
    if (
        _config_provider is None
        or _query_chatrooms is None
        or _fetch_messages is None
        or _excel_writer_factory is None
        or _task_parser_factory is None
    ):
        raise RuntimeError("Session service is not configured")
    return (
        _config_provider(),
        _query_chatrooms,
        _fetch_messages,
        _excel_writer_factory,
        _task_parser_factory,
    )

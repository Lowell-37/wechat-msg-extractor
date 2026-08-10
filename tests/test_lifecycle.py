import asyncio

from fastapi.testclient import TestClient

import app as app_module
from services import export as export_service
from services import session as session_service


class TrackingDatabase:
    def __init__(self):
        self.closed = 0

    def close_all(self):
        self.closed += 1


def _session(database, last_access):
    return {
        "wdb": object(),
        "ddb": database,
        "selected_group": None,
        "selected_sheet": None,
        "start_date": None,
        "end_date": None,
        "parsed_tasks": [],
        "last_access": last_access,
    }


def _clear_state():
    for session_id in list(app_module.session_state):
        if hasattr(app_module, "dispose_session"):
            app_module.dispose_session(session_id)
        else:
            app_module.session_state.pop(session_id, None)
    for name in ("export_tasks", "session_jobs", "job_owners"):
        registry = getattr(app_module, name, None)
        if registry is not None:
            registry.clear()


def test_expired_session_is_disposed_before_replacement(monkeypatch):
    _clear_state()
    database = TrackingDatabase()
    now = [100.0]
    monkeypatch.setattr(app_module, "SESSION_TTL_SECONDS", 10.0, raising=False)
    monkeypatch.setattr(app_module, "_session_clock", lambda: now[0], raising=False)
    app_module.session_state["expired"] = _session(database, last_access=80.0)
    client = TestClient(app_module.app)
    client.cookies.set("session_id", "expired", domain="testserver.local")

    response = client.get("/")

    assert response.status_code == 200
    assert database.closed == 1
    assert "expired" not in app_module.session_state
    assert client.cookies["session_id"] != "expired"
    _clear_state()


def test_session_bound_evicts_oldest_idle_session(monkeypatch):
    _clear_state()
    oldest = TrackingDatabase()
    newest = TrackingDatabase()
    monkeypatch.setattr(app_module, "MAX_SESSIONS", 2, raising=False)
    monkeypatch.setattr(app_module, "_session_clock", lambda: 100.0, raising=False)
    app_module.session_state["oldest"] = _session(oldest, last_access=10.0)
    app_module.session_state["newest"] = _session(newest, last_access=20.0)

    response = TestClient(app_module.app).get("/")

    assert response.status_code == 200
    assert len(app_module.session_state) == 2
    assert oldest.closed == 1
    assert newest.closed == 0
    assert "oldest" not in app_module.session_state
    _clear_state()


def test_shutdown_cancels_jobs_before_disposing_sessions():
    _clear_state()
    database = TrackingDatabase()
    actions = []

    async def scenario():
        started = asyncio.Event()

        async def worker():
            started.set()
            try:
                await asyncio.Event().wait()
            finally:
                actions.append("cancelled")

        task = asyncio.create_task(worker())
        await started.wait()
        app_module.session_state["session-1"] = _session(database, last_access=0.0)
        app_module.export_tasks["job-1"] = task
        app_module.session_jobs["session-1"] = "job-1"
        app_module.job_owners["job-1"] = "session-1"

        await app_module.shutdown_resources()

        assert task.cancelled()

    asyncio.run(scenario())

    assert actions == ["cancelled"]
    assert database.closed == 1
    assert app_module.session_state == {}
    assert app_module.export_tasks == {}
    assert app_module.session_jobs == {}
    assert app_module.job_owners == {}


def test_app_lifecycle_exports_are_service_compatibility_aliases():
    assert app_module.session_state is session_service.session_state
    assert app_module.export_tasks is export_service.export_tasks
    assert app_module.session_jobs is export_service.session_jobs
    assert app_module.job_owners is export_service.job_owners
    assert app_module.dispose_session is session_service.dispose_session
    assert app_module.shutdown_resources is session_service.shutdown_resources

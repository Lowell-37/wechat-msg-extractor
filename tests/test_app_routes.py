from fastapi.testclient import TestClient

import app as app_module


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
    assert app_module.session_state[session_id]["wdb"] is Result.manager
    assert app_module.session_state[session_id]["ddb"] is connected


def test_connection_failure_is_html_escaped(monkeypatch):
    def fail(key=None):
        raise RuntimeError("<unsafe>")

    monkeypatch.setattr(app_module, "connect_wechat", fail)
    client = TestClient(app_module.app)

    response = client.post("/api/key/validate", data={"key": "ab" * 32})

    assert "&lt;unsafe&gt;" in response.text
    assert "<unsafe>" not in response.text


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

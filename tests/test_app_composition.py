import pytest
from fastapi.testclient import TestClient

import app as app_module


@pytest.fixture(autouse=True)
def reset_session_state():
    app_module.session_state.clear()
    yield
    app_module.session_state.clear()


@pytest.fixture
def client():
    return TestClient(app_module.app)


def test_future_wizard_route_redirects_to_latest_accessible_step(client):
    response = client.get("/wizard/3", follow_redirects=False)

    assert response.status_code == 303
    assert response.headers["location"] == "/wizard/1"


def test_future_wizard_redirect_retains_the_created_session(client):
    response = client.get("/wizard/3", follow_redirects=False)

    session_id = response.cookies["session_id"]
    assert session_id in app_module.session_state
    assert client.cookies["session_id"] == session_id


def test_partial_route_returns_fragment_not_document(client):
    response = client.get(
        "/wizard/1/partial",
        headers={"HX-Request": "true"},
    )

    assert response.status_code == 200
    assert "<!DOCTYPE html>" not in response.text
    assert 'id="wizard-workspace"' in response.text


@pytest.mark.parametrize(
    ("legacy_path", "wizard_path"),
    [("/step/2", "/wizard/2"), ("/step/3", "/wizard/3")],
)
def test_legacy_step_route_redirects_to_wizard_route(
    client, legacy_path, wizard_path
):
    response = client.get(legacy_path, follow_redirects=False)

    assert response.status_code == 303
    assert response.headers["location"] == wizard_path


def test_business_routes_are_owned_by_router_modules():
    route_modules = {
        route.path: route.endpoint.__module__
        for route in app_module.app.routes
        if hasattr(route, "endpoint")
    }

    assert route_modules["/"] == "routers.wizard"
    assert route_modules["/wizard/{step}"] == "routers.wizard"
    assert route_modules["/wizard/{step}/partial"] == "routers.wizard"
    assert route_modules["/api/key/validate"] == "routers.actions"
    assert route_modules["/api/export"] == "routers.actions"

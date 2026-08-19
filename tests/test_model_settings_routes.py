import asyncio

import pytest
from fastapi.testclient import TestClient

import app as app_module
from services.credential_store import CredentialStore
from services.model_settings import ModelSettingsService, ModelSettingsStore


class ReversingProtector:
    def protect(self, value):
        return value[::-1]

    def unprotect(self, value):
        return value[::-1]


@pytest.fixture
def settings_service(tmp_path):
    tested = []

    async def tester(settings):
        tested.append(settings)

    service = ModelSettingsService(
        ModelSettingsStore(tmp_path / "model.json"),
        CredentialStore(
            tmp_path / "model-api-key.dpapi",
            ReversingProtector(),
        ),
        tester,
    )
    service.tested = tested
    return service


@pytest.fixture
def client(monkeypatch, settings_service):
    monkeypatch.setattr(
        app_module.app.state,
        "model_settings",
        settings_service,
        raising=False,
    )
    return TestClient(app_module.app)


def valid_form(**overrides):
    values = {
        "provider_name": "Local Model",
        "api_base": "http://127.0.0.1:11434",
        "model": "qwen2.5",
        "enabled": "true",
        "api_key": "private-key",
    }
    values.update(overrides)
    return values


def test_header_links_to_model_settings(client):
    response = client.get("/wizard/1")

    assert response.status_code == 200
    assert 'href="/settings/model"' in response.text
    assert "模型设置" in response.text


def test_settings_page_shows_defaults_without_secret(client):
    response = client.get("/settings/model")

    assert response.status_code == 200
    assert "<!DOCTYPE html>" in response.text
    assert 'value="DeepSeek"' in response.text
    assert 'value="https://api.deepseek.com"' in response.text
    assert 'value="deepseek-chat"' in response.text
    assert "未配置 API Key" in response.text
    assert 'name="api_key"' in response.text
    assert 'value=""' in response.text


def test_settings_page_never_renders_saved_key(client, settings_service):
    asyncio.run(settings_service.save_and_test(**valid_form()))

    response = client.get("/settings/model")

    assert response.status_code == 200
    assert "API Key 已配置" in response.text
    assert "private-key" not in response.text


def test_save_and_test_returns_success_fragment(client, settings_service):
    response = client.post(
        "/settings/model",
        data=valid_form(),
        headers={"HX-Request": "true"},
    )

    assert response.status_code == 200
    assert "连接测试通过" in response.text
    assert "private-key" not in response.text
    assert "<!DOCTYPE html>" not in response.text
    assert settings_service.status().ready is True
    assert settings_service.tested[0].profile.model == "qwen2.5"


def test_blank_key_reuses_saved_credential(client, settings_service):
    first = client.post(
        "/settings/model",
        data=valid_form(),
        headers={"HX-Request": "true"},
    )
    assert first.status_code == 200

    second = client.post(
        "/settings/model",
        data=valid_form(model="qwen3", api_key=""),
        headers={"HX-Request": "true"},
    )

    assert second.status_code == 200
    assert settings_service.resolved_profile().profile.model == "qwen3"
    assert settings_service.resolved_profile().api_key == "private-key"


def test_failed_test_is_sanitized_and_keeps_submitted_nonsecret_fields(
    client,
    settings_service,
):
    async def failing(settings):
        raise RuntimeError("private-key upstream body")

    settings_service.tester = failing

    response = client.post(
        "/settings/model",
        data=valid_form(model="broken-model"),
        headers={"HX-Request": "true"},
    )

    assert response.status_code == 400
    assert "连接测试失败" in response.text
    assert 'value="broken-model"' in response.text
    assert "private-key" not in response.text
    assert "upstream body" not in response.text
    assert settings_service.status().ready is False


def test_invalid_form_returns_422_fragment(client):
    response = client.post(
        "/settings/model",
        data=valid_form(api_base="http://models.example.com"),
        headers={"HX-Request": "true"},
    )

    assert response.status_code == 422
    assert "必须使用 HTTPS" in response.text
    assert "<!DOCTYPE html>" not in response.text


def test_disabling_model_does_not_require_key(client, settings_service):
    response = client.post(
        "/settings/model",
        data=valid_form(enabled="", api_key=""),
        headers={"HX-Request": "true"},
    )

    assert response.status_code == 200
    assert "AI 分析已停用" in response.text
    assert settings_service.status().profile.enabled is False
    assert settings_service.status().ready is False


def test_model_settings_route_is_owned_by_dedicated_router():
    route_modules = {
        route.path: route.endpoint.__module__
        for route in app_module.app.routes
        if hasattr(route, "endpoint")
    }

    assert route_modules["/settings/model"] == "routers.model_settings"

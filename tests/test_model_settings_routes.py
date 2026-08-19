import asyncio
import re

import pytest
from fastapi.testclient import TestClient

import app as app_module
from services.credential_store import CredentialStore
from services.model_settings import (
    ModelSettingsService,
    ModelSettingsStore,
    SafeModelTesterError,
)


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
    test_client = TestClient(
        app_module.app,
        headers={"Origin": "http://testserver"},
    )
    response = test_client.get("/settings/model")
    match = re.search(
        r'name="csrf_token" value="([^"]+)"', response.text
    )
    assert match is not None
    test_client.csrf_token = match.group(1)
    return test_client


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


def test_settings_page_reports_corrupt_credential_as_unavailable(
    client, settings_service
):
    asyncio.run(settings_service.save_and_test(**valid_form()))
    settings_service.credential_store.path.write_bytes(b"")

    response = client.get("/settings/model")

    assert response.status_code == 200
    assert "未配置 API Key" in response.text
    assert "已验证" not in response.text


def test_save_and_test_returns_success_fragment(client, settings_service):
    response = client.post(
        "/settings/model",
        data=valid_form(csrf_token=client.csrf_token),
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
        data=valid_form(csrf_token=client.csrf_token),
        headers={"HX-Request": "true"},
    )
    assert first.status_code == 200

    second = client.post(
        "/settings/model",
        data=valid_form(
            model="qwen3",
            api_key="",
            csrf_token=client.csrf_token,
        ),
        headers={"HX-Request": "true"},
    )

    assert second.status_code == 200
    assert settings_service.resolved_profile().profile.model == "qwen3"
    assert settings_service.resolved_profile().api_key == "private-key"


def test_cross_site_post_never_reuses_saved_key(client, settings_service):
    asyncio.run(settings_service.save_and_test(**valid_form()))

    response = client.post(
        "/settings/model",
        data=valid_form(
            api_base="https://attacker.example",
            api_key="",
            csrf_token=client.csrf_token,
        ),
        headers={"Origin": "https://attacker.example"},
    )

    assert response.status_code == 403
    assert len(settings_service.tested) == 1
    assert settings_service.resolved_profile().profile.api_base == (
        "http://127.0.0.1:11434"
    )


def test_same_origin_post_without_csrf_token_is_rejected(
    client, settings_service
):
    response = client.post(
        "/settings/model",
        data=valid_form(),
    )

    assert response.status_code == 403
    assert settings_service.tested == []


def test_untrusted_host_is_rejected_before_settings_route(
    client, settings_service
):
    response = client.post(
        "/settings/model",
        data=valid_form(csrf_token=client.csrf_token),
        headers={
            "Host": "attacker.example",
            "Origin": "http://attacker.example",
        },
    )

    assert response.status_code == 400
    assert settings_service.tested == []


def test_failed_test_is_sanitized_and_keeps_submitted_nonsecret_fields(
    client,
    settings_service,
):
    async def failing(settings):
        raise RuntimeError("private-key upstream body")

    settings_service.tester = failing

    response = client.post(
        "/settings/model",
        data=valid_form(
            model="broken-model", csrf_token=client.csrf_token
        ),
        headers={"HX-Request": "true"},
    )

    assert response.status_code == 400
    assert "连接测试失败" in response.text
    assert 'value="broken-model"' in response.text
    assert "private-key" not in response.text
    assert "upstream body" not in response.text
    assert settings_service.status().ready is False


@pytest.mark.parametrize(
    "safe_message",
    [
        "模型服务认证失败",
        "模型服务请求过于频繁",
        "模型服务请求超时",
        "模型服务返回无效响应",
    ],
)
def test_actionable_safe_connection_error_is_preserved(
    client,
    settings_service,
    safe_message,
):
    async def failing(settings):
        raise SafeModelTesterError(safe_message)

    settings_service.tester = failing

    response = client.post(
        "/settings/model",
        data=valid_form(csrf_token=client.csrf_token),
        headers={"HX-Request": "true"},
    )

    assert response.status_code == 400
    assert safe_message in response.text
    assert "private-key" not in response.text


def test_invalid_form_returns_422_fragment(client):
    response = client.post(
        "/settings/model",
        data=valid_form(
            api_base="http://models.example.com",
            csrf_token=client.csrf_token,
        ),
        headers={"HX-Request": "true"},
    )

    assert response.status_code == 422
    assert "必须使用 HTTPS" in response.text
    assert "<!DOCTYPE html>" not in response.text


def test_disabling_model_does_not_require_key(client, settings_service):
    response = client.post(
        "/settings/model",
        data=valid_form(
            enabled="", api_key="", csrf_token=client.csrf_token
        ),
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

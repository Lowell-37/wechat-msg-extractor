import asyncio
import json

import pytest

from services.credential_store import CredentialStore
from services.model_settings import (
    ModelProfile,
    ModelSettingsError,
    ModelSettingsService,
    ModelSettingsStore,
)


class ReversingProtector:
    def protect(self, value):
        return value[::-1]

    def unprotect(self, value):
        return value[::-1]


def make_service(tmp_path, tester=None):
    async def passing_tester(settings):
        return None

    credential_store = CredentialStore(
        tmp_path / "model-api-key.dpapi",
        ReversingProtector(),
    )
    service = ModelSettingsService(
        ModelSettingsStore(tmp_path / "model.json"),
        credential_store,
        tester or passing_tester,
    )
    return service


def save(service, **overrides):
    values = {
        "provider_name": "Local Model",
        "api_base": "http://127.0.0.1:11434",
        "model": "qwen2.5",
        "enabled": True,
        "api_key": "private-key",
    }
    values.update(overrides)
    return asyncio.run(service.save_and_test(**values))


def test_defaults_are_deepseek_and_not_ready(tmp_path):
    service = make_service(tmp_path)

    status = service.status()

    assert status.profile == ModelProfile(
        provider_name="DeepSeek",
        api_base="https://api.deepseek.com",
        model="deepseek-chat",
        enabled=True,
        verified=False,
    )
    assert status.has_api_key is False
    assert status.ready is False


def test_save_and_test_commits_profile_and_encrypted_key(tmp_path):
    tested = []

    async def tester(settings):
        tested.append(settings)

    service = make_service(tmp_path, tester)

    status = save(service)

    assert tested[0].api_key == "private-key"
    assert tested[0].profile.model == "qwen2.5"
    assert service.credential_store.path.read_bytes() == b"yek-etavirp"
    assert b"private-key" not in service.credential_store.path.read_bytes()
    assert status.ready is True
    assert json.loads(service.store.path.read_text(encoding="utf-8")) == {
        "api_base": "http://127.0.0.1:11434",
        "enabled": True,
        "model": "qwen2.5",
        "provider_name": "Local Model",
        "verified": True,
    }


def test_blank_replacement_reuses_existing_key(tmp_path):
    tested = []

    async def tester(settings):
        tested.append(settings.api_key)

    service = make_service(tmp_path, tester)
    save(service)

    save(service, model="qwen3", api_key="   ")

    assert tested == ["private-key", "private-key"]
    assert service.resolved_profile().profile.model == "qwen3"


def test_disabling_skips_network_test_and_clears_readiness(tmp_path):
    calls = 0

    async def tester(settings):
        nonlocal calls
        calls += 1

    service = make_service(tmp_path, tester)
    save(service)

    status = save(service, enabled=False, api_key="")

    assert calls == 1
    assert status.profile.enabled is False
    assert status.profile.verified is False
    assert status.ready is False


def test_failed_test_preserves_last_working_profile_and_key(tmp_path):
    async def passing(settings):
        return None

    service = make_service(tmp_path, passing)
    save(service)

    async def failing(settings):
        raise RuntimeError("new-private-key appeared upstream")

    service.tester = failing

    with pytest.raises(ModelSettingsError) as error:
        save(service, model="broken", api_key="new-private-key")

    assert "new-private-key" not in str(error.value)
    assert service.resolved_profile().profile.model == "qwen2.5"
    assert service.resolved_profile().api_key == "private-key"


@pytest.mark.parametrize(
    "api_base",
    [
        "http://models.example.com",
        "ftp://localhost/model",
        "https://user:password@example.com",
        "https://example.com?key=value",
        "https://example.com/#fragment",
    ],
)
def test_rejects_unsafe_api_base_before_testing(api_base, tmp_path):
    calls = 0

    async def tester(settings):
        nonlocal calls
        calls += 1

    service = make_service(tmp_path, tester)

    with pytest.raises(ModelSettingsError):
        save(service, api_base=api_base)

    assert calls == 0
    assert service.status().ready is False


@pytest.mark.parametrize("field", ["provider_name", "model"])
def test_rejects_blank_required_fields(field, tmp_path):
    service = make_service(tmp_path)

    with pytest.raises(ModelSettingsError, match="不能为空"):
        save(service, **{field: "   "})


def test_missing_key_is_actionable(tmp_path):
    service = make_service(tmp_path)

    with pytest.raises(ModelSettingsError, match="请填写 API Key"):
        save(service, api_key="")


def test_corrupt_settings_file_raises_safe_error(tmp_path):
    path = tmp_path / "model.json"
    path.write_text("private-settings-not-json", encoding="utf-8")
    service = make_service(tmp_path)

    with pytest.raises(ModelSettingsError) as error:
        service.status()

    assert "private-settings-not-json" not in str(error.value)


def test_settings_write_failure_restores_previous_key(tmp_path, monkeypatch):
    service = make_service(tmp_path)
    save(service)
    original_save = service.store.save

    def fail_new_profile(profile):
        if profile.model == "new-model":
            raise ModelSettingsError("settings unavailable")
        original_save(profile)

    monkeypatch.setattr(service.store, "save", fail_new_profile)

    with pytest.raises(ModelSettingsError):
        save(service, model="new-model", api_key="new-private-key")

    assert service.credential_store.load() == "private-key"
    assert service.status().profile.model == "qwen2.5"

import asyncio
import json

import pytest

from services.credential_store import CredentialStore, CredentialStoreError
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


class NonDeterministicProtector:
    def __init__(self):
        self.counter = 0

    def protect(self, value):
        self.counter += 1
        return bytes([self.counter]) + value

    def unprotect(self, value):
        return value[1:]


async def passing_tester(settings):
    return None


def make_service(tmp_path, tester=None):
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
        "credential_digest": service.status().profile.credential_digest,
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


def test_blank_key_cannot_reuse_credential_for_different_authority(tmp_path):
    tested = []

    async def tester(settings):
        tested.append((settings.profile.api_base, settings.api_key))

    service = make_service(tmp_path, tester)
    save(service)

    with pytest.raises(ModelSettingsError, match="新的 API Key"):
        save(
            service,
            api_base="https://attacker.example",
            api_key="",
        )

    assert tested == [("http://127.0.0.1:11434", "private-key")]
    assert service.resolved_profile().profile.api_base == "http://127.0.0.1:11434"


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
    service = ModelSettingsService(
        ModelSettingsStore(tmp_path / "model.json"),
        CredentialStore(
            tmp_path / "model-api-key.dpapi",
            NonDeterministicProtector(),
        ),
        passing_tester,
    )
    save(service)
    previous_ciphertext = service.credential_store.path.read_bytes()
    original_save = service.store.save

    def fail_new_profile(profile):
        if profile.model == "new-model":
            raise ModelSettingsError("settings unavailable")
        original_save(profile)

    monkeypatch.setattr(service.store, "save", fail_new_profile)

    with pytest.raises(ModelSettingsError):
        save(service, model="new-model", api_key="new-private-key")

    assert service.credential_store.load() == "private-key"
    assert service.credential_store.path.read_bytes() == previous_ciphertext
    assert service.status().profile.model == "qwen2.5"
    assert service.status().ready is True


def test_credential_write_failure_restores_exact_working_ciphertext(
    tmp_path, monkeypatch
):
    service = ModelSettingsService(
        ModelSettingsStore(tmp_path / "model.json"),
        CredentialStore(
            tmp_path / "model-api-key.dpapi",
            NonDeterministicProtector(),
        ),
        passing_tester,
    )
    save(service)
    previous_ciphertext = service.credential_store.path.read_bytes()
    original_save = service.credential_store.save

    def fail_after_replace(api_key):
        original_save(api_key)
        raise CredentialStoreError("simulated credential write failure")

    monkeypatch.setattr(service.credential_store, "save", fail_after_replace)

    with pytest.raises(ModelSettingsError):
        save(service, model="new-model", api_key="new-private-key")

    assert service.credential_store.path.read_bytes() == previous_ciphertext
    assert service.credential_store.load() == "private-key"
    assert service.status().profile.model == "qwen2.5"
    assert service.status().ready is True


def test_interrupted_profile_switch_fails_readiness_closed(tmp_path, monkeypatch):
    service = make_service(tmp_path)
    save(service)
    original_save = service.store.save

    def interrupt_new_profile(profile):
        if profile.model == "new-model":
            raise SystemExit("simulated process termination")
        original_save(profile)

    monkeypatch.setattr(service.store, "save", interrupt_new_profile)

    with pytest.raises(SystemExit):
        save(service, model="new-model", api_key="new-private-key")

    restarted = make_service(tmp_path)
    status = restarted.status()
    assert status.profile.model == "qwen2.5"
    assert status.has_api_key is True
    assert status.ready is False
    with pytest.raises(ModelSettingsError):
        restarted.resolved_profile()


def test_corrupt_credential_is_not_reported_as_available_or_ready(tmp_path):
    service = make_service(tmp_path)
    save(service)
    service.credential_store.path.write_bytes(b"")

    status = service.status()

    assert status.has_api_key is False
    assert status.ready is False
    with pytest.raises(ModelSettingsError):
        service.resolved_profile()


def test_overlapping_saves_are_serialized_within_service(tmp_path):
    service = make_service(tmp_path)
    save(service)

    async def scenario():
        old_test_started = asyncio.Event()
        resume_old_test = asyncio.Event()
        tested_models = []

        async def coordinated_tester(settings):
            tested_models.append(settings.profile.model)
            if settings.profile.model == "old-update":
                old_test_started.set()
                await resume_old_test.wait()

        service.tester = coordinated_tester
        old_save = asyncio.create_task(
            service.save_and_test(
                provider_name="Old",
                api_base="http://127.0.0.1:11434",
                model="old-update",
                enabled=True,
                api_key="",
            )
        )
        await old_test_started.wait()
        new_save = asyncio.create_task(
            service.save_and_test(
                provider_name="New",
                api_base="https://new-provider.example",
                model="new-model",
                enabled=True,
                api_key="new-private-key",
            )
        )
        await asyncio.sleep(0)

        assert tested_models == ["old-update"]
        assert new_save.done() is False

        resume_old_test.set()
        await old_save
        await new_save

    asyncio.run(scenario())

    resolved = service.resolved_profile()
    assert resolved.profile.api_base == "https://new-provider.example"
    assert resolved.api_key == "new-private-key"


def test_stale_blank_key_commit_is_rejected_across_service_instances(tmp_path):
    first = make_service(tmp_path)
    save(first)
    second = make_service(tmp_path)

    async def scenario():
        old_test_started = asyncio.Event()
        resume_old_test = asyncio.Event()

        async def pause_old_test(settings):
            old_test_started.set()
            await resume_old_test.wait()

        first.tester = pause_old_test
        old_save = asyncio.create_task(
            first.save_and_test(
                provider_name="Old",
                api_base="http://127.0.0.1:11434",
                model="old-update",
                enabled=True,
                api_key="",
            )
        )
        await old_test_started.wait()
        await second.save_and_test(
            provider_name="New",
            api_base="https://new-provider.example",
            model="new-model",
            enabled=True,
            api_key="new-private-key",
        )
        resume_old_test.set()
        with pytest.raises(ModelSettingsError, match="已更新"):
            await old_save

    asyncio.run(scenario())

    resolved = second.resolved_profile()
    assert resolved.profile.api_base == "https://new-provider.example"
    assert resolved.api_key == "new-private-key"

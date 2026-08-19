from pathlib import Path

import pytest

from services.credential_store import (
    CRYPTPROTECT_LOCAL_MACHINE,
    CRYPTPROTECT_UI_FORBIDDEN,
    CredentialStore,
    CredentialStoreError,
    WindowsDpapiProtector,
)


class ReversingProtector:
    def protect(self, value):
        return value[::-1]

    def unprotect(self, value):
        if value == b"corrupt":
            raise ValueError("ciphertext bytes must stay private")
        return value[::-1]


def test_store_round_trips_ciphertext_without_plaintext(tmp_path):
    store = CredentialStore(tmp_path / "token.dpapi", ReversingProtector())

    store.save("private-token")

    assert store.path.read_bytes() == b"nekot-etavirp"
    assert b"private-token" not in store.path.read_bytes()
    assert store.load() == "private-token"


def test_store_rejects_empty_token_without_creating_blob(tmp_path):
    path = tmp_path / "token.dpapi"
    store = CredentialStore(path, ReversingProtector())

    with pytest.raises(CredentialStoreError, match="不能为空"):
        store.save("   ")

    assert not path.exists()


def test_missing_store_returns_none(tmp_path):
    store = CredentialStore(tmp_path / "missing.dpapi", ReversingProtector())

    assert store.load() is None


def test_corrupt_blob_raises_secret_free_error(tmp_path):
    path = tmp_path / "token.dpapi"
    path.write_bytes(b"corrupt")
    store = CredentialStore(path, ReversingProtector())

    with pytest.raises(CredentialStoreError) as error:
        store.load()

    assert "ciphertext bytes must stay private" not in str(error.value)
    assert "corrupt" not in str(error.value)


def test_failed_atomic_replace_keeps_previous_blob(tmp_path, monkeypatch):
    store = CredentialStore(tmp_path / "token.dpapi", ReversingProtector())
    store.save("old-token")

    def fail_replace(source, destination):
        raise OSError("replace unavailable")

    monkeypatch.setattr("services.credential_store.os.replace", fail_replace)

    with pytest.raises(CredentialStoreError, match="无法保存"):
        store.save("new-token")

    assert store.path.read_bytes() == b"nekot-dlo"


def test_windows_dpapi_protector_uses_machine_scope_flags(monkeypatch):
    calls = []

    def protect(value, flags):
        calls.append((value, flags))
        return b"encrypted"

    monkeypatch.setattr("services.credential_store._crypt_protect", protect)
    protector = WindowsDpapiProtector()

    assert protector.protect(b"private-token") == b"encrypted"
    assert calls == [
        (
            b"private-token",
            CRYPTPROTECT_LOCAL_MACHINE | CRYPTPROTECT_UI_FORBIDDEN,
        )
    ]


def test_default_store_path_is_repository_local_and_not_plaintext():
    store = CredentialStore.default()

    assert store.path.name == "wechat_explorer_token.dpapi"
    assert store.path.parent.name == "secrets"
    assert store.path.parent.parent.name == "local"
    assert Path("config.yaml") != store.path


def test_model_api_key_store_uses_separate_repository_local_blob():
    store = CredentialStore.model_api_key()

    assert store.path.name == "model_api_key.dpapi"
    assert store.path.parent.name == "secrets"
    assert store.path != CredentialStore.default().path


def test_delete_removes_only_the_selected_credential(tmp_path):
    selected = CredentialStore(tmp_path / "selected.dpapi", ReversingProtector())
    other = CredentialStore(tmp_path / "other.dpapi", ReversingProtector())
    selected.save("selected-token")
    other.save("other-token")

    selected.delete()

    assert selected.load() is None
    assert other.load() == "other-token"

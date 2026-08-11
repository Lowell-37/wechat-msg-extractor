from pathlib import Path

import pytest

from services import credential_cli
from services.credential_cli import CredentialCliError, save_and_validate


class RecordingStore:
    def __init__(self):
        self.saved = []

    def save(self, token):
        self.saved.append(token)


class RecordingClient:
    def __init__(self, *, fail=False):
        self.fail = fail
        self.queried = 0
        self.closed = 0

    def query_chatrooms(self):
        self.queried += 1
        if self.fail:
            raise RuntimeError("private-token was rejected")
        return [("room@chatroom", "项目群", None, 0)]

    def close(self):
        self.closed += 1


def test_save_and_validate_uses_authenticated_catalog_boundary():
    store = RecordingStore()
    client = RecordingClient()

    save_and_validate("private-token", store, lambda token: client)

    assert store.saved == ["private-token"]
    assert client.queried == 1
    assert client.closed == 1


def test_failed_validation_closes_client_and_hides_token():
    store = RecordingStore()
    client = RecordingClient(fail=True)

    with pytest.raises(CredentialCliError) as error:
        save_and_validate("private-token", store, lambda token: client)

    assert client.closed == 1
    assert "private-token" not in str(error.value)


def test_close_failure_is_reported_without_leaking_token():
    store = RecordingStore()
    client = RecordingClient()

    def fail_close():
        raise RuntimeError("private-token cleanup details")

    client.close = fail_close

    with pytest.raises(CredentialCliError) as error:
        save_and_validate("private-token", store, lambda token: client)

    assert "private-token" not in str(error.value)


def test_powershell_helper_prefers_project_or_active_python():
    script = (
        Path(__file__).resolve().parents[1]
        / "scripts"
        / "save_wechat_explorer_token.ps1"
    ).read_text(encoding="utf-8")

    assert "$env:VIRTUAL_ENV" in script
    assert "Programs\\Python\\Python313" not in script


def test_cli_removes_input_environment_and_never_prints_token(
    monkeypatch, capsys
):
    monkeypatch.setenv(credential_cli.TOKEN_INPUT_ENV, "private-token")
    monkeypatch.setattr(
        credential_cli,
        "save_and_validate",
        lambda token, store, client_factory: None,
    )

    result = credential_cli.main(["save"])

    output = capsys.readouterr()
    assert result == 0
    assert credential_cli.TOKEN_INPUT_ENV not in __import__("os").environ
    assert "private-token" not in output.out
    assert "private-token" not in output.err

import httpx
import pytest

from core.connection import connect_wechat
from services.credential_store import CredentialStoreError
from services.wechat_explorer import (
    WechatExplorerClient,
    WechatExplorerError,
    resolve_token,
)


def test_explorer_client_only_allows_loopback_api_addresses(monkeypatch):
    monkeypatch.setenv("WECHATEXPLORER_API_TOKEN", "private-token")

    with pytest.raises(WechatExplorerError, match="本机回环地址"):
        WechatExplorerClient("http://192.168.1.12:6131/api/v1")


def test_explorer_client_normalizes_chatrooms_and_messages(monkeypatch):
    monkeypatch.setenv("WECHATEXPLORER_API_TOKEN", "private-token")
    requests = []

    def respond(request):
        requests.append(request)
        if request.url.path.endswith("/chatroom"):
            return httpx.Response(
                200,
                json={
                    "count": 1,
                    "chatrooms": [
                        {
                            "m_nsUsrName": "room@chatroom",
                            "m_nsNickName": "项目群",
                        }
                    ],
                },
            )
        return httpx.Response(
            200,
            json={
                "count": 1,
                "messages": [
                    {
                        "id": "42",
                        "content": "本周任务",
                        "createTime": 1_700_000_000,
                        "sessionId": "room@chatroom",
                    }
                ],
            },
        )

    client = WechatExplorerClient(
        transport=httpx.MockTransport(respond),
    )
    try:
        assert client.query_chatrooms() == [
            ("room@chatroom", "项目群", None, 0)
        ]
        assert client.query_messages("room@chatroom", 1, 2) == [
            {
                "localId": "42",
                "StrContent": "本周任务",
                "CreateTime": 1_700_000_000,
                "StrTalker": "room@chatroom",
            }
        ]
    finally:
        client.close()

    assert all(
        request.headers["Authorization"] == "Bearer private-token"
        for request in requests
    )


def test_explorer_client_does_not_include_token_in_connection_errors(monkeypatch):
    monkeypatch.setenv("WECHATEXPLORER_API_TOKEN", "private-token")
    client = WechatExplorerClient(
        transport=httpx.MockTransport(lambda request: httpx.Response(500))
    )
    try:
        with pytest.raises(WechatExplorerError) as error:
            client.query_chatrooms()
    finally:
        client.close()

    assert "private-token" not in str(error.value)


def test_explicit_and_environment_tokens_override_persisted(monkeypatch):
    monkeypatch.setenv("WECHATEXPLORER_API_TOKEN", "environment-token")

    assert resolve_token("explicit-token", lambda: "stored-token") == (
        "explicit-token"
    )
    assert resolve_token(None, lambda: "stored-token") == "environment-token"


def test_blank_explicit_token_does_not_hide_environment_token(monkeypatch):
    monkeypatch.setenv("WECHATEXPLORER_API_TOKEN", "environment-token")

    assert resolve_token("   ", lambda: "stored-token") == "environment-token"


def test_client_falls_back_to_persisted_token(monkeypatch):
    monkeypatch.delenv("WECHATEXPLORER_API_TOKEN", raising=False)

    def respond(request):
        assert request.headers["Authorization"] == "Bearer stored-token"
        return httpx.Response(
            200,
            json={
                "count": 1,
                "chatrooms": [
                    {
                        "m_nsUsrName": "room@chatroom",
                        "m_nsNickName": "项目群",
                    }
                ],
            },
        )

    client = WechatExplorerClient(
        token_loader=lambda: "stored-token",
        transport=httpx.MockTransport(respond),
    )
    try:
        assert client.query_chatrooms()[0][0] == "room@chatroom"
    finally:
        client.close()


def test_corrupt_persisted_token_becomes_secret_free_client_error(monkeypatch):
    monkeypatch.delenv("WECHATEXPLORER_API_TOKEN", raising=False)

    def fail_load():
        raise CredentialStoreError("private-token ciphertext details")

    with pytest.raises(WechatExplorerError) as error:
        WechatExplorerClient(token_loader=fail_load)

    assert "重新保存凭据" in str(error.value)
    assert "private-token" not in str(error.value)


def test_connection_uses_explorer_for_detected_modern_databases(monkeypatch):
    class ModernInfo:
        def __init__(self):
            self.db_files = ["message_0.db"]

    class LegacyManager:
        def scan_and_extract(self):
            return False, "legacy extraction is unavailable"

        def get_info(self):
            return ModernInfo()

    class ExplorerDatabase:
        def query_chatrooms(self):
            return [("room@chatroom", "项目群", None, 0)]

        def close_all(self):
            pass

    class ExplorerManager:
        def close(self):
            pass

    monkeypatch.setattr("core.connection.WeChatDB", LegacyManager)
    monkeypatch.setattr(
        "core.connection.connect_wechat_explorer",
        lambda **kwargs: (ExplorerManager(), ExplorerDatabase()),
    )

    result = connect_wechat(explorer_base_url="http://127.0.0.1:6131/api/v1")

    assert result.shard_count == 1
    assert result.table_count == 1
    assert result.database.query_chatrooms()[0][1] == "项目群"

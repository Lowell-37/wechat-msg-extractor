"""Loopback-only bridge for WechatExplorer's local HTTP API."""

import os
from typing import Any
from urllib.parse import urlparse

import httpx

DEFAULT_EXPLORER_BASE_URL = "http://127.0.0.1:6131/api/v1"


class WechatExplorerError(RuntimeError):
    """Raised when the local WechatExplorer API cannot be used safely."""


def _validate_base_url(base_url: str) -> str:
    parsed = urlparse(base_url)
    if parsed.scheme != "http" or parsed.hostname not in {
        "127.0.0.1",
        "localhost",
        "::1",
    }:
        raise WechatExplorerError("WechatExplorer API 必须使用本机回环地址")
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise WechatExplorerError("WechatExplorer API 地址格式无效")
    return base_url.rstrip("/")


class WechatExplorerClient:
    """Adapt WechatExplorer API responses to the app's message query surface."""

    kind = "wechat_explorer"
    original_path = "WechatExplorer local API"
    key_hex = ""

    def __init__(
        self,
        base_url: str = DEFAULT_EXPLORER_BASE_URL,
        *,
        token: str | None = None,
        transport: httpx.BaseTransport | None = None,
        timeout: float = 5.0,
    ):
        self.base_url = _validate_base_url(base_url)
        token = token if token is not None else os.environ.get(
            "WECHATEXPLORER_API_TOKEN"
        )
        if not token or not token.strip():
            raise WechatExplorerError(
                "未设置 WECHATEXPLORER_API_TOKEN；请在 WechatExplorer API Center 获取令牌后，"
                "仅在启动本项目的终端中设置该环境变量"
            )
        self._client = httpx.Client(
            base_url=self.base_url,
            headers={"Authorization": f"Bearer {token.strip()}"},
            transport=transport,
            timeout=timeout,
        )

    def health(self) -> dict[str, Any]:
        return self._request("/health")

    def query_chatrooms(self) -> list[tuple[str, str, int | None, int]]:
        payload = self._request("/chatroom", params={"keyword": ""})
        chatrooms = payload.get("chatrooms", [])
        if not isinstance(chatrooms, list):
            raise WechatExplorerError("WechatExplorer 返回了无效的群聊数据")
        return [
            (
                str(item["m_nsUsrName"]),
                str(item.get("m_nsNickName") or item["m_nsUsrName"]),
                None,
                0,
            )
            for item in chatrooms
            if isinstance(item, dict) and item.get("m_nsUsrName")
        ]

    def query_messages(
        self, chat_id: str, start_timestamp: int, end_timestamp: int
    ) -> list[dict[str, Any]]:
        payload = self._request(
            "/chatlog",
            params={
                "talker": chat_id,
                "startTime": start_timestamp,
                "endTime": end_timestamp,
            },
        )
        messages = payload.get("messages", [])
        if not isinstance(messages, list):
            raise WechatExplorerError("WechatExplorer 返回了无效的消息数据")
        return [
            {
                "localId": item.get("localId", item.get("id", "")),
                "StrContent": str(item.get("content") or ""),
                "CreateTime": int(item["createTime"]),
                "StrTalker": str(item.get("sessionId") or chat_id),
            }
            for item in messages
            if isinstance(item, dict) and item.get("createTime") is not None
        ]

    def close(self) -> None:
        self._client.close()

    def close_all(self) -> None:
        self.close()

    def _request(
        self, path: str, *, params: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        try:
            response = self._client.get(path, params=params)
            response.raise_for_status()
            payload = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise WechatExplorerError(
                "无法连接 WechatExplorer 本机 API；请确认应用已启动并已开启 API"
            ) from exc
        if not isinstance(payload, dict):
            raise WechatExplorerError("WechatExplorer 返回了无效响应")
        return payload


def connect_wechat_explorer(
    *, base_url: str = DEFAULT_EXPLORER_BASE_URL
) -> tuple[WechatExplorerClient, WechatExplorerClient]:
    """Open a local API client and verify the authenticated catalog boundary."""
    client = WechatExplorerClient(base_url)
    try:
        client.query_chatrooms()
    except Exception:
        client.close()
        raise
    return client, client

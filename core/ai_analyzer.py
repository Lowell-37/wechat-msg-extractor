"""OpenAI-compatible analysis client for task and discussion summaries."""

from abc import ABC, abstractmethod
from typing import Any

import httpx

from services.model_settings import (
    ModelProfile,
    ResolvedModelProfile,
    SafeModelTesterError,
)


class AIAnalysisError(SafeModelTesterError):
    """Raised when a model request cannot produce a safe analysis result."""


class BaseAnalyzer(ABC):
    @abstractmethod
    async def analyze(
        self,
        task_items: list[str],
        context: list[str],
        date_str: str,
    ) -> str:
        """Analyze one task date and return model text."""

    @abstractmethod
    async def test_connection(self) -> None:
        """Raise a safe error unless the configured model responds."""


class OpenAICompatibleAnalyzer(BaseAnalyzer):
    SYSTEM_PROMPT = (
        "你是一位学生学习情况分析助手。"
        "用户会提供当天的任务安排和群聊中的讨论内容（含语音转文字）。"
        "请根据这些信息，分析学生的学习完成情况、存在的问题和改进建议。"
        "用中文回答，简洁有条理，不超过300字。"
    )

    def __init__(
        self,
        settings: ResolvedModelProfile,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
    ):
        self._settings = settings
        self._transport = transport

    def _build_client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            base_url=self._settings.profile.api_base,
            headers={
                "Authorization": f"Bearer {self._settings.api_key}",
                "Content-Type": "application/json",
            },
            transport=self._transport,
            timeout=httpx.Timeout(60.0),
        )

    def _build_messages(
        self,
        task_items: list[str],
        context: list[str],
        date_str: str,
    ) -> list[dict[str, str]]:
        task_text = (
            "\n".join(f"- {item}" for item in task_items)
            if task_items
            else "（无）"
        )
        context_text = (
            "\n".join(f"- {item}" for item in context)
            if context
            else "（无）"
        )
        user_message = (
            f"日期：{date_str}\n\n"
            f"【任务安排】\n{task_text}\n\n"
            f"【讨论内容/语音】\n{context_text}"
        )
        return [
            {"role": "system", "content": self.SYSTEM_PROMPT},
            {"role": "user", "content": user_message},
        ]

    async def analyze(
        self,
        task_items: list[str],
        context: list[str],
        date_str: str,
    ) -> str:
        data = await self._completion(
            {
                "model": self._settings.profile.model,
                "messages": self._build_messages(
                    task_items,
                    context,
                    date_str,
                ),
                "temperature": 0.7,
                "max_tokens": 600,
            }
        )
        return _parse_completion(data)

    async def test_connection(self) -> None:
        data = await self._completion(
            {
                "model": self._settings.profile.model,
                "messages": [{"role": "user", "content": "ping"}],
                "max_tokens": 10,
            }
        )
        _parse_completion(data)

    async def _completion(self, payload: dict[str, Any]) -> dict[str, Any]:
        try:
            async with self._build_client() as client:
                response = await client.post(
                    "/v1/chat/completions",
                    json=payload,
                )
                response.raise_for_status()
                data = response.json()
        except httpx.TimeoutException as exc:
            raise AIAnalysisError("模型服务请求超时") from exc
        except httpx.HTTPStatusError as exc:
            raise _status_error(exc.response.status_code) from exc
        except httpx.HTTPError as exc:
            raise AIAnalysisError("无法连接模型服务") from exc
        except ValueError as exc:
            raise AIAnalysisError("模型服务返回无效响应") from exc
        if not isinstance(data, dict):
            raise AIAnalysisError("模型服务返回无效响应")
        return data


def _parse_completion(data: dict[str, Any]) -> str:
    try:
        content = data["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError) as exc:
        raise AIAnalysisError("模型服务返回无效响应") from exc
    if not isinstance(content, str) or not content.strip():
        raise AIAnalysisError("模型服务返回无效响应")
    return content.strip()


def _status_error(status_code: int) -> AIAnalysisError:
    if status_code in {401, 403}:
        return AIAnalysisError("模型服务认证失败")
    if status_code == 429:
        return AIAnalysisError("模型服务请求过于频繁")
    if status_code >= 500:
        return AIAnalysisError("模型服务暂时不可用")
    return AIAnalysisError("模型服务拒绝了请求")


def create_analyzer(
    settings: ResolvedModelProfile | Any,
    *,
    transport: httpx.AsyncBaseTransport | None = None,
) -> OpenAICompatibleAnalyzer:
    """Create an analyzer, accepting legacy AIConfig until export migrates."""
    if isinstance(settings, ResolvedModelProfile):
        resolved = settings
    else:
        resolved = ResolvedModelProfile(
            ModelProfile(
                provider_name=str(getattr(settings, "provider", "Model")),
                api_base=str(settings.api_base),
                model=str(settings.model),
                enabled=bool(getattr(settings, "enabled", True)),
                verified=True,
            ),
            str(settings.api_key),
        )
    return OpenAICompatibleAnalyzer(resolved, transport=transport)

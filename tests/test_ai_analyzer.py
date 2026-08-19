import asyncio
import json

import httpx
import pytest

from core.ai_analyzer import (
    AIAnalysisError,
    OpenAICompatibleAnalyzer,
    create_analyzer,
)
from services.model_settings import ModelProfile, ResolvedModelProfile


def resolved_profile(
    api_base="http://127.0.0.1:9000",
    model="custom-model",
    api_key="private-key",
):
    return ResolvedModelProfile(
        ModelProfile(
            provider_name="Custom Provider",
            api_base=api_base,
            model=model,
            enabled=True,
            verified=True,
        ),
        api_key,
    )


def test_analyzer_uses_configured_endpoint_model_and_key():
    requests = []

    def respond(request):
        requests.append(request)
        return httpx.Response(
            200,
            json={"choices": [{"message": {"content": " 学习情况正常 "}}]},
        )

    analyzer = OpenAICompatibleAnalyzer(
        resolved_profile(),
        transport=httpx.MockTransport(respond),
    )

    result = asyncio.run(
        analyzer.analyze(["完成作业"], ["已订正"], "2026-08-18")
    )

    assert result == "学习情况正常"
    assert requests[0].url.path == "/v1/chat/completions"
    assert requests[0].headers["Authorization"] == "Bearer private-key"
    payload = json.loads(requests[0].content)
    assert payload["model"] == "custom-model"
    assert "完成作业" in payload["messages"][1]["content"]
    assert "已订正" in payload["messages"][1]["content"]


def test_connection_uses_minimal_completion_request():
    payloads = []

    def respond(request):
        payloads.append(json.loads(request.content))
        return httpx.Response(
            200,
            json={"choices": [{"message": {"content": "pong"}}]},
        )

    analyzer = create_analyzer(
        resolved_profile(),
        transport=httpx.MockTransport(respond),
    )

    assert asyncio.run(analyzer.test_connection()) is None
    assert payloads == [
        {
            "max_tokens": 10,
            "messages": [{"content": "ping", "role": "user"}],
            "model": "custom-model",
        }
    ]


@pytest.mark.parametrize(
    ("status_code", "expected"),
    [
        (401, "认证失败"),
        (403, "认证失败"),
        (429, "请求过于频繁"),
        (500, "暂时不可用"),
    ],
)
def test_upstream_status_failure_is_safe(status_code, expected):
    def respond(request):
        return httpx.Response(
            status_code,
            text="private-key and private upstream response",
        )

    analyzer = OpenAICompatibleAnalyzer(
        resolved_profile(),
        transport=httpx.MockTransport(respond),
    )

    with pytest.raises(AIAnalysisError) as error:
        asyncio.run(analyzer.analyze(["任务"], [], "2026-08-18"))

    assert expected in str(error.value)
    assert "private-key" not in str(error.value)
    assert "private upstream response" not in str(error.value)


def test_timeout_is_reported_without_request_details():
    def timeout(request):
        raise httpx.ConnectTimeout("private-key timeout details", request=request)

    analyzer = OpenAICompatibleAnalyzer(
        resolved_profile(),
        transport=httpx.MockTransport(timeout),
    )

    with pytest.raises(AIAnalysisError, match="请求超时") as error:
        asyncio.run(analyzer.analyze(["任务"], [], "2026-08-18"))

    assert "private-key" not in str(error.value)


@pytest.mark.parametrize(
    "payload",
    [
        {"unexpected": "shape"},
        {"choices": []},
        {"choices": [{"message": {"content": "   "}}]},
    ],
)
def test_invalid_completion_payload_is_rejected(payload):
    analyzer = OpenAICompatibleAnalyzer(
        resolved_profile(),
        transport=httpx.MockTransport(
            lambda request: httpx.Response(200, json=payload)
        ),
    )

    with pytest.raises(AIAnalysisError, match="无效响应"):
        asyncio.run(analyzer.analyze(["任务"], [], "2026-08-18"))

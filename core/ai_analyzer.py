"""AI 分析器抽象接口与实现。

提供统一的 AI 分析接口，支持切换不同模型提供商。
"""

from abc import ABC, abstractmethod

import httpx


class BaseAnalyzer(ABC):
    """AI 分析器抽象基类。"""

    @abstractmethod
    async def analyze(
        self, task_items: list[str], context: list[str], date_str: str
    ) -> str:
        """分析当天任务，返回分析文本。"""

    @abstractmethod
    async def test_connection(self) -> bool:
        """测试 API 连接。"""


class DeepSeekAnalyzer(BaseAnalyzer):
    """DeepSeek Chat API 分析器。"""

    SYSTEM_PROMPT = (
        "你是一位学生学习情况分析助手。"
        "用户会提供当天的任务安排和群聊中的讨论内容（含语音转文字）。"
        "请根据这些信息，分析学生的学习完成情况、存在的问题和改进建议。"
        "用中文回答，简洁有条理，不超过300字。"
    )

    def __init__(self, config):
        self._api_key = config.api_key
        self._api_base = config.api_base.rstrip("/")
        self._model = config.model

    def _build_client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            base_url=self._api_base,
            headers={
                "Authorization": f"Bearer {self._api_key}",
                "Content-Type": "application/json",
            },
            timeout=httpx.Timeout(60.0),
        )

    def _build_messages(self, task_items: list[str], context: list[str], date_str: str):
        task_text = "\n".join(f"- {t}" for t in task_items) if task_items else "（无）"
        context_text = "\n".join(f"- {c}" for c in context) if context else "（无）"
        user_msg = (
            f"日期：{date_str}\n\n"
            f"【任务安排】\n{task_text}\n\n"
            f"【讨论内容/语音】\n{context_text}"
        )
        return [
            {"role": "system", "content": self.SYSTEM_PROMPT},
            {"role": "user", "content": user_msg},
        ]

    async def analyze(self, task_items: list[str], context: list[str], date_str: str) -> str:
        if not self._api_key:
            return self._fallback(context)
        try:
            async with self._build_client() as client:
                resp = await client.post(
                    "/v1/chat/completions",
                    json={
                        "model": self._model,
                        "messages": self._build_messages(task_items, context, date_str),
                        "temperature": 0.7,
                        "max_tokens": 600,
                    },
                )
                resp.raise_for_status()
                data = resp.json()
                return data["choices"][0]["message"]["content"].strip()
        except Exception as e:
            return f"[AI分析失败: {e}]\n{self._fallback(context)}"

    async def test_connection(self) -> bool:
        if not self._api_key:
            return False
        try:
            async with self._build_client() as client:
                resp = await client.post(
                    "/v1/chat/completions",
                    json={
                        "model": self._model,
                        "messages": [{"role": "user", "content": "ping"}],
                        "max_tokens": 10,
                    },
                )
                return resp.status_code == 200
        except Exception:
            return False

    @staticmethod
    def _fallback(context: list[str]) -> str:
        if not context:
            return ""
        return "\n".join(context)


def create_analyzer(config) -> BaseAnalyzer:
    """工厂函数：根据配置创建 AI 分析器实例。"""
    providers = {
        "deepseek": DeepSeekAnalyzer,
    }
    cls = providers.get(config.provider)
    if cls is None:
        raise ValueError(f"不支持的 AI provider: {config.provider}")
    return cls(config)

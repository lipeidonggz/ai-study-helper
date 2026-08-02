"""LLM 客户端接口：统一模型调用抽象，兼容 DeepSeek/OpenAI 类 API。

骨架阶段使用 FakeLLMClient 让 loop 可运行；阶段 1 实现真实客户端（HTTP 调用 + 流式）。
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass


@dataclass
class LLMMessage:
    role: str  # system | user | assistant | tool
    content: str


@dataclass
class ToolCall:
    name: str
    arguments: dict


@dataclass
class LLMResponse:
    content: str
    tool_call: ToolCall | None = None


class LLMClient(ABC):
    @abstractmethod
    async def chat(
        self, messages: list[LLMMessage], tools: list[dict] | None = None
    ) -> LLMResponse:
        """返回文本内容或工具调用请求。流式输出由上层生成器接入。"""


class FakeLLMClient(LLMClient):
    """占位实现：让 Agent loop 在没有真实 API 的情况下跑通结构。"""

    async def chat(self, messages, tools=None) -> LLMResponse:
        last_user = next((m.content for m in reversed(messages) if m.role == "user"), "")
        return LLMResponse(content=f"（骨架占位回复）收到：{last_user}")

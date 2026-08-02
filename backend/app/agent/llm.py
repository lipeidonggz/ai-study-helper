"""LLM 客户端接口与实现：统一模型调用抽象，兼容 DeepSeek/OpenAI 类 API。

- LLMClient：抽象接口（chat 非流式 + stream 流式事件）
- FakeLLMClient：占位实现，让 loop 在没有真实 API 时也可运行
- DeepSeekLLMClient：真实实现（HTTP 调用 + SSE 流式 + function calling）
"""

import json
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import AsyncIterator

import httpx

DEEPSEEK_MODELS = ["deepseek-chat", "deepseek-reasoner"]


@dataclass
class LLMMessage:
    role: str  # system | user | assistant | tool
    content: str
    tool_calls: list[dict] | None = None  # assistant 消息回显用（OpenAI 格式）


@dataclass
class ToolCall:
    name: str
    arguments: dict


@dataclass
class LLMResponse:
    content: str
    tool_call: ToolCall | None = None


@dataclass
class LLMEvent:
    """流式事件：text（增量文本）| tool_call（工具调用）| done（结束）。"""

    type: str
    text: str | None = None
    tool_call: ToolCall | None = None
    raw_tool_calls: list[dict] | None = None  # 回显给 API 的原始 tool_calls


class LLMClient(ABC):
    @abstractmethod
    async def chat(
        self, messages: list[LLMMessage], tools: list[dict] | None = None
    ) -> LLMResponse:
        """返回文本内容或工具调用请求。流式输出由上层生成器接入。"""

    @abstractmethod
    def stream(
        self, messages: list[LLMMessage], tools: list[dict] | None = None
    ) -> AsyncIterator[LLMEvent]:
        """流式生成：text 事件逐块产出，工具调用在结束时以 tool_call 事件给出。"""


class FakeLLMClient(LLMClient):
    """占位实现：让 Agent loop 在没有真实 API 的情况下跑通结构。"""

    async def chat(self, messages, tools=None) -> LLMResponse:
        last_user = next((m.content for m in reversed(messages) if m.role == "user"), "")
        return LLMResponse(content=f"（骨架占位回复）收到：{last_user}")

    async def stream(self, messages, tools=None) -> AsyncIterator[LLMEvent]:
        response = await self.chat(messages, tools)
        for i in range(0, len(response.content), 4):
            yield LLMEvent(type="text", text=response.content[i : i + 4])
        yield LLMEvent(type="done")


class DeepSeekLLMClient(LLMClient):
    """DeepSeek Chat Completions 客户端（OpenAI 兼容协议）。

    阶段 1 关注点：手写 API 调用、SSE 流式解析、function calling 回显。
    """

    def __init__(
        self,
        api_key: str,
        model: str = "deepseek-chat",
        base_url: str = "https://api.deepseek.com",
        http_client: httpx.AsyncClient | None = None,
    ) -> None:
        self._api_key = api_key
        self._model = model
        self._base_url = base_url.rstrip("/")
        self._http_client = http_client  # 测试注入用；None 则每次调用自建

    def _headers(self) -> dict:
        return {"Authorization": f"Bearer {self._api_key}", "Content-Type": "application/json"}

    @staticmethod
    def _to_api_messages(messages: list[LLMMessage]) -> list[dict]:
        out: list[dict] = []
        for m in messages:
            item: dict = {"role": m.role, "content": m.content}
            if m.tool_calls:
                item["tool_calls"] = m.tool_calls
            out.append(item)
        return out

    async def chat(
        self, messages: list[LLMMessage], tools: list[dict] | None = None
    ) -> LLMResponse:
        payload = {
            "model": self._model,
            "messages": self._to_api_messages(messages),
            "stream": False,
        }
        if tools:
            payload["tools"] = tools
        created = self._http_client is None
        client = self._http_client or httpx.AsyncClient(timeout=120)
        try:
            resp = await client.post(
                f"{self._base_url}/chat/completions", headers=self._headers(), json=payload
            )
            resp.raise_for_status()
            return self._parse_response(resp.json())
        finally:
            if created:
                await client.aclose()

    @staticmethod
    def _parse_response(data: dict) -> LLMResponse:
        message = data["choices"][0].get("message", {})
        content = message.get("content") or ""
        tool_calls = message.get("tool_calls") or []
        if tool_calls:
            fn = tool_calls[0]["function"]
            try:
                arguments = json.loads(fn.get("arguments") or "{}")
            except json.JSONDecodeError:
                arguments = {}
            return LLMResponse(
                content="", tool_call=ToolCall(name=fn.get("name", ""), arguments=arguments)
            )
        return LLMResponse(content=content)

    async def stream(
        self, messages: list[LLMMessage], tools: list[dict] | None = None
    ) -> AsyncIterator[LLMEvent]:
        payload = {
            "model": self._model,
            "messages": self._to_api_messages(messages),
            "stream": True,
        }
        if tools:
            payload["tools"] = tools
        created = self._http_client is None
        client = self._http_client or httpx.AsyncClient(timeout=120)
        collected: dict[int, dict] = {}
        raw_tool_calls: list[dict] = []
        try:
            async with client.stream(
                "POST",
                f"{self._base_url}/chat/completions",
                headers=self._headers(),
                json=payload,
            ) as resp:
                resp.raise_for_status()
                async for line in resp.aiter_lines():
                    if not line.startswith("data:"):
                        continue
                    raw = line[5:].strip()
                    if raw == "[DONE]":
                        break
                    chunk = json.loads(raw)
                    delta = chunk["choices"][0].get("delta", {})
                    if delta.get("content"):
                        yield LLMEvent(type="text", text=delta["content"])
                    for tc in delta.get("tool_calls") or []:
                        raw_tool_calls.append(tc)
                        idx = tc.get("index", 0)
                        slot = collected.setdefault(idx, {"name": "", "arguments": ""})
                        fn = tc.get("function", {})
                        slot["name"] += fn.get("name", "")
                        slot["arguments"] += fn.get("arguments", "")
        finally:
            if created:
                await client.aclose()

        if collected:
            first = collected[min(collected)]
            try:
                arguments = json.loads(first["arguments"] or "{}")
            except json.JSONDecodeError:
                arguments = {}
            yield LLMEvent(
                type="tool_call",
                tool_call=ToolCall(name=first["name"], arguments=arguments),
                raw_tool_calls=raw_tool_calls,
            )
        yield LLMEvent(type="done")

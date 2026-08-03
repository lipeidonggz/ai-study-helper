"""LLM 客户端：统一封装"调用大模型"这件事。

设计角度：为什么单独一个文件、还要定义接口？
- Agent 核心（loop）只需要知道"给消息列表，还我文本或工具调用"，
  不需要知道调的是 DeepSeek 还是别的厂商——这就是"面向接口编程"
- 现在有两个实现：
  * FakeLLMClient：不联网的占位，用于测试和还没配 Key 时跑通流程
  * DeepSeekLLMClient：真实调用 DeepSeek 的 Chat Completions 接口（OpenAI 兼容协议）
- 以后想换模型厂商，新增一个实现类即可，loop 一行都不用改
"""

import json  # 解析 API 返回的 JSON / 把工具参数字符串解析成字典
from abc import ABC, abstractmethod  # 定义抽象基类：约束子类必须实现哪些方法
from dataclasses import dataclass  # 定义轻量数据结构
from typing import AsyncIterator  # 标注异步生成器的返回值

import httpx  # HTTP 客户端库，负责发请求和读流式响应

# DeepSeek 当前可选的模型名（用于前端下拉框和参数校验）
DEEPSEEK_MODELS = ["deepseek-chat", "deepseek-reasoner"]


@dataclass
class LLMMessage:
    """发给大模型的一条消息（OpenAI 协议的消息格式）。"""

    role: str  # system / user / assistant / tool
    content: str
    tool_call_id: str | None = None  # tool 消息必须关联对应的工具调用 id（协议要求）
    # 多轮工具调用时，OpenAI 协议要求把上一次的 tool_calls 原样回传，
    # 否则模型会"忘记"自己刚才调了什么工具
    tool_calls: list[dict] | None = None


@dataclass
class ToolCall:
    """模型请求调用某个工具：工具名 + 参数（已解析成字典）。"""

    name: str
    arguments: dict
    id: str = ""  # 工具调用 id（回显 tool 消息时关联用）


@dataclass
class LLMResponse:
    """非流式调用的返回：要么是文本，要么是工具调用请求。"""

    content: str
    tool_call: ToolCall | None = None
    usage: dict | None = None  # token 用量（非流式响应自带）


@dataclass
class LLMEvent:
    """流式事件：统一告诉上层"现在发生了什么"。

    五种类型：
    - raw：原始流 chunk（还没加工的数据，用于展示"流式返回长什么样"）
    - text：产出了一段文本增量（直接推给前端即可）
    - tool_call：模型想调用工具（需要执行工具后再回一轮）
    - usage：流结束时的 token 用量（开启 stream_options.include_usage 才有）
    - done：本轮生成结束
    """

    type: str
    text: str | None = None
    tool_call: ToolCall | None = None
    raw_tool_calls: list[dict] | None = None  # 原始 tool_calls（回传给 API 用）
    raw: dict | None = None  # type="raw" 时携带原始 chunk（或 {"marker": "[DONE]"}）
    usage: dict | None = None  # type="usage" 时携带 token 用量


class LLMClient(ABC):
    """LLM 客户端接口：所有实现都必须提供这两个方法。"""

    model_name: str = "unknown"  # 供 trace 展示当前使用的模型

    @abstractmethod
    async def chat(
        self, messages: list[LLMMessage], tools: list[dict] | None = None
    ) -> LLMResponse:
        """非流式调用：一次拿回完整结果（测试和简单场景用）。"""

    @abstractmethod
    def stream(
        self, messages: list[LLMMessage], tools: list[dict] | None = None
    ) -> AsyncIterator[LLMEvent]:
        """流式调用：逐块产出事件（对话场景用，边生成边推给前端）。"""


class FakeLLMClient(LLMClient):
    """占位实现：不联网，永远返回固定前缀 + 用户最后一条消息。

    用途：还没配 API Key、或写测试时，让整个链路能跑通。
    """

    model_name = "fake"

    async def chat(self, messages, tools=None) -> LLMResponse:
        # 从消息列表里倒着找最后一条 user 消息（作为"收到什么"的展示）
        last_user = next((m.content for m in reversed(messages) if m.role == "user"), "")
        return LLMResponse(content=f"（骨架占位回复）收到：{last_user}")

    async def stream(self, messages, tools=None) -> AsyncIterator[LLMEvent]:
        # 复用 chat 拿到整段文本，然后切成小块假装"流式"输出
        response = await self.chat(messages, tools)
        for i in range(0, len(response.content), 4):  # 每 4 个字符产出一个事件
            yield LLMEvent(type="text", text=response.content[i : i + 4])
        yield LLMEvent(type="done")  # 最后告诉上层"本轮结束"


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
        self.model_name = model  # 便于 trace 展示当前模型
        self._base_url = base_url.rstrip("/")  # 去掉末尾斜杠，拼接 URL 更安全
        self._http_client = http_client  # 测试注入用；None 则每次调用自建

    def _headers(self) -> dict:
        """构造认证请求头：把 API Key 放进 Authorization（OpenAI 兼容标准）。"""
        return {"Authorization": f"Bearer {self._api_key}", "Content-Type": "application/json"}

    @staticmethod
    def _to_api_messages(messages: list[LLMMessage]) -> list[dict]:
        """把内部消息对象转成 API 认识的字典列表；带 tool_calls 的照原样带上。"""
        out: list[dict] = []
        for m in messages:
            item: dict = {"role": m.role, "content": m.content}
            if m.tool_call_id:
                item["tool_call_id"] = m.tool_call_id  # tool 消息的关联 id
            if m.tool_calls:
                item["tool_calls"] = m.tool_calls
            out.append(item)
        return out

    async def chat(
        self, messages: list[LLMMessage], tools: list[dict] | None = None
    ) -> LLMResponse:
        """非流式调用：组装请求 → POST → 解析 JSON 响应。"""
        payload = {
            "model": self._model,
            "messages": self._to_api_messages(messages),
            "stream": False,  # 非流式：等完整结果再返回
        }
        if tools:
            payload["tools"] = tools  # 告诉模型"你可以用这些工具"
        created = self._http_client is None
        client = self._http_client or httpx.AsyncClient(timeout=120)  # 120 秒超时兜底
        try:
            resp = await client.post(
                f"{self._base_url}/chat/completions", headers=self._headers(), json=payload
            )
            if resp.status_code >= 400:
                # 把 API 返回的具体原因带进异常，前端/日志能直接看到为什么失败
                raise RuntimeError(f"DeepSeek API {resp.status_code}: {resp.text[:300]}")
            return self._parse_response(resp.json())
        finally:
            if created:
                await client.aclose()  # 自己创建的客户端要记得关闭，防止连接泄漏

    @staticmethod
    def _parse_response(data: dict) -> LLMResponse:
        """从 API 响应里提取文本或工具调用。"""
        message = data["choices"][0].get("message", {})  # 取第一个候选的 message
        content = message.get("content") or ""
        tool_calls = message.get("tool_calls") or []
        if tool_calls:
            # 只处理第一个工具调用（多个并行调用是后续阶段的优化点）
            fn = tool_calls[0]["function"]
            try:
                arguments = json.loads(fn.get("arguments") or "{}")  # 参数是 JSON 字符串
            except json.JSONDecodeError:
                arguments = {}  # 解析失败给空字典，宁可让工具报错也不要让链路崩溃
            return LLMResponse(
                content="",
                tool_call=ToolCall(
                    name=fn.get("name", ""),
                    arguments=arguments,
                    id=tool_calls[0].get("id", ""),
                ),
                usage=data.get("usage"),
            )
        return LLMResponse(content=content, usage=data.get("usage"))

    async def stream(
        self, messages: list[LLMMessage], tools: list[dict] | None = None
    ) -> AsyncIterator[LLMEvent]:
        """流式调用：逐行解析 SSE，边收边产出事件。

        为什么在这里"攒"工具调用？流式响应里一个 tool_calls 会被拆成很多片
        （名字可能分两片、参数 JSON 可能分十片），必须等流结束才能拼出完整调用。
        """
        payload = {
            "model": self._model,
            "messages": self._to_api_messages(messages),
            "stream": True,  # 开启流式
            "stream_options": {"include_usage": True},  # 让流式响应也返回 token 用量
        }
        if tools:
            payload["tools"] = tools
        created = self._http_client is None
        client = self._http_client or httpx.AsyncClient(timeout=120)
        collected: dict[int, dict] = {}  # index -> {"id","type","name","arguments"}，按片拼接
        try:
            async with client.stream(
                "POST",
                f"{self._base_url}/chat/completions",
                headers=self._headers(),
                json=payload,
            ) as resp:
                if resp.status_code >= 400:
                    raw = (await resp.aread()).decode("utf-8", "ignore")
                    raise RuntimeError(f"DeepSeek API {resp.status_code}: {raw[:300]}")
                async for line in resp.aiter_lines():  # 逐行读流
                    if not line.startswith("data:"):  # SSE 数据行以 data: 开头，其他忽略
                        continue
                    raw = line[5:].strip()  # 去掉 "data:" 前缀
                    if raw == "[DONE]":  # OpenAI 协议：流结束标记
                        yield LLMEvent(type="raw", raw={"marker": "[DONE]"})  # 结束标记也展示
                        break
                    chunk = json.loads(raw)
                    # 原始 chunk 原样产出（前端可开关展示）
                    yield LLMEvent(type="raw", raw=chunk)
                    if not chunk.get("choices"):
                        # 用量汇总 chunk：choices 为空、只带 usage
                        # 注意：不能按 "usage" in chunk 判断——DeepSeek 每个 chunk 都带 usage 键
                        # （内容 chunk 里是 null），真正用法要看 choices 是否为空
                        if chunk.get("usage"):
                            yield LLMEvent(type="usage", usage=chunk["usage"])
                        continue
                    delta = chunk["choices"][0].get("delta", {})  # 每片内容在 delta 里
                    if delta.get("content"):
                        yield LLMEvent(type="text", text=delta["content"])  # 文本增量直接产出
                    if chunk.get("usage"):
                        # 兜底：usage 也可能带在最后一个有 choices 的 chunk 里
                        # （finish_reason=stop 块），两种格式都采集
                        yield LLMEvent(type="usage", usage=chunk["usage"])
                    for tc in delta.get("tool_calls") or []:  # 工具调用片段
                        idx = tc.get("index", 0)  # 多个并行调用用 index 区分
                        slot = collected.setdefault(
                            idx, {"id": "", "type": "", "name": "", "arguments": ""}
                        )
                        fn = tc.get("function", {})
                        if tc.get("id"):  # id 只在第一片出现，取完整值
                            slot["id"] = tc["id"]
                        if tc.get("type"):  # type 同理
                            slot["type"] = tc["type"]
                        slot["name"] += fn.get("name", "")  # 名字可能分片，累加
                        slot["arguments"] += fn.get("arguments", "")  # 参数 JSON 分片，累加
        finally:
            if created:
                await client.aclose()

        if collected:  # 流结束时若攒出了工具调用，产出 tool_call 事件
            # 合并成协议要求的完整结构再回显（缺 id/type 会被 API 拒绝，见 400 调试记录）
            merged_tool_calls = [
                {
                    "id": s["id"],
                    "type": s["type"] or "function",
                    "function": {"name": s["name"], "arguments": s["arguments"]},
                }
                for s in collected.values()
            ]
            first = collected[min(collected)]  # 取 index 最小的（第一个工具）
            try:
                arguments = json.loads(first["arguments"] or "{}")
            except json.JSONDecodeError:
                arguments = {}
            yield LLMEvent(
                type="tool_call",
                tool_call=ToolCall(
                    name=first["name"], arguments=arguments, id=first["id"]
                ),
                raw_tool_calls=merged_tool_calls,  # 回显用合并后的完整结构
            )
        yield LLMEvent(type="done")

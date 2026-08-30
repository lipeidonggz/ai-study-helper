"""LLM 客户端测试：用 httpx MockTransport 模拟 HTTP 层，不依赖真实网络。

设计角度：为什么要 Mock？
- 测试不能真的调用 DeepSeek（花钱且不稳定）
- MockTransport 拦截请求，测试可以断言"请求格式对不对"，再喂回假响应，
  从而验证客户端的组装、解析、流式处理逻辑
"""

import asyncio  # 在同步测试函数里手动跑异步逻辑
import json

import httpx

from app.agent.llm import (
    DeepSeekLLMClient,
    LLMClient,
    LLMEvent,
    LLMMessage,
    LLMResponse,
    ToolCall,
)


def _sse(chunks: list[dict]) -> str:
    """把若干 JSON 块拼成 OpenAI 流式响应格式（data: ... 空行分隔，[DONE] 收尾）。"""
    body = "".join(f"data: {json.dumps(c, ensure_ascii=False)}\n\n" for c in chunks)
    return body + "data: [DONE]\n\n"


def test_stream_text():
    """流式文本：逐块 content 应被完整拼接，且请求带 stream=True。"""

    async def handler(request: httpx.Request) -> httpx.Response:
        """模拟服务器：先校验请求，再返回两段文本增量。"""
        payload = json.loads(request.content)
        assert payload["stream"] is True  # 流式请求标记
        assert payload["stream_options"] == {"include_usage": True}  # 请求返回 token 用量
        assert payload["messages"][0]["role"] == "user"
        return httpx.Response(
            200,
            text=_sse(
                [
                    # 模拟 DeepSeek 实际行为：内容 chunk 也带 usage 键（值为 null）
                    {"choices": [{"delta": {"content": "你好"}}], "usage": None},
                    {"choices": [{"delta": {"content": "世界"}}], "usage": None},
                    # 开启 include_usage 后，最后一个 chunk 只带 usage、choices 为空
                    {
                        "choices": [],
                        "usage": {"prompt_tokens": 5, "completion_tokens": 7, "total_tokens": 12},
                    },
                    # 另一种格式：usage 带在最后一个有 choices 的 chunk（finish_reason 块）
                    {
                        "choices": [{"delta": {}, "finish_reason": "stop"}],
                        "usage": {"prompt_tokens": 2, "completion_tokens": 3, "total_tokens": 5},
                    },
                ]
            ),
            headers={"Content-Type": "text/event-stream"},
        )

    async def scenario():
        transport = httpx.MockTransport(handler)  # 拦截所有 HTTP 请求
        async with httpx.AsyncClient(transport=transport) as hc:
            client = DeepSeekLLMClient(api_key="sk-test", http_client=hc)
            parts: list[str] = []
            tool_events = []
            raw_events = []
            usage_events = []
            async for evt in client.stream([LLMMessage(role="user", content="hi")]):
                if evt.type == "raw":
                    raw_events.append(evt)
                elif evt.type == "usage":
                    usage_events.append(evt)
                elif evt.type == "text":
                    parts.append(evt.text or "")
                elif evt.type == "tool_call":
                    tool_events.append(evt)
            assert "".join(parts) == "你好世界"
            assert tool_events == []  # 纯文本响应不应有工具调用事件
            assert len(raw_events) >= 2  # 每个原始 chunk 都应产出 raw 事件
            assert raw_events[0].raw["choices"][0]["delta"]["content"] == "你好"
            assert len(usage_events) == 2  # 两种 usage 格式都应被采集
            assert usage_events[0].usage["total_tokens"] == 12

    asyncio.run(scenario())


def test_temperature_in_payload():
    """temperature 设置时应进 payload；不设置时不携带（走服务端默认 1.0）。"""
    seen: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        seen.append(payload)
        if payload.get("stream"):
            return httpx.Response(
                200,
                text=_sse([{"choices": [{"delta": {"content": "ok"}}]}]),
                headers={"Content-Type": "text/event-stream"},
            )
        return httpx.Response(
            200, json={"choices": [{"message": {"content": "ok"}}]}
        )

    async def scenario():
        transport = httpx.MockTransport(handler)
        async with httpx.AsyncClient(transport=transport) as hc:
            # 默认：不传 temperature（服务端默认 1.0），payload 里不应出现该键
            client = DeepSeekLLMClient(api_key="sk-test", http_client=hc)
            async for _ in client.stream([LLMMessage(role="user", content="hi")]):
                pass
            assert "temperature" not in seen[-1]

            # 显式温度：chat 与 stream 都应携带
            client2 = DeepSeekLLMClient(api_key="sk-test", http_client=hc, temperature=0.3)
            await client2.chat([LLMMessage(role="user", content="hi")])
            assert seen[-1]["temperature"] == 0.3
            async for _ in client2.stream([LLMMessage(role="user", content="hi")]):
                pass
            assert seen[-1]["temperature"] == 0.3

    asyncio.run(scenario())


def test_stream_tool_call():
    """流式工具调用：分片的 name/arguments 应被拼接成完整调用，且保留原始结构。"""

    async def handler(request: httpx.Request) -> httpx.Response:
        # 模拟：工具名和参数 JSON 都拆成两片到达
        return httpx.Response(
            200,
            text=_sse(
                [
                    {
                        "choices": [
                            {
                                "delta": {
                                    "tool_calls": [
                                        {
                                            "index": 0,
                                            "id": "call_1",
                                            "type": "function",
                                            "function": {"name": "calculator", "arguments": ""},
                                        }
                                    ]
                                }
                            }
                        ]
                    },
                    {
                        "choices": [
                            {
                                "delta": {
                                    "tool_calls": [
                                        {"index": 0, "function": {"arguments": '{"expression": "1+1"}'}}
                                    ]
                                }
                            }
                        ]
                    },
                ]
            ),
            headers={"Content-Type": "text/event-stream"},
        )

    async def scenario():
        transport = httpx.MockTransport(handler)
        async with httpx.AsyncClient(transport=transport) as hc:
            client = DeepSeekLLMClient(api_key="sk-test", http_client=hc)
            tool_event = None
            async for evt in client.stream([LLMMessage(role="user", content="计算 1+1")]):
                if evt.type == "tool_call":
                    tool_event = evt
            assert tool_event is not None
            assert tool_event.tool_call.name == "calculator"
            assert tool_event.tool_call.arguments == {"expression": "1+1"}
            assert tool_event.tool_call.id == "call_1"  # id 必须保留，tool 消息要关联
            assert len(tool_event.tool_calls) == 1  # tool_calls 列表包含该调用
            assert tool_event.tool_calls[0].name == "calculator"
            # 回显必须是协议要求的完整结构（缺 id/type 会被 API 400 拒绝）
            assert tool_event.raw_tool_calls == [
                {
                    "id": "call_1",
                    "type": "function",
                    "function": {
                        "name": "calculator",
                        "arguments": '{"expression": "1+1"}',
                    },
                }
            ]

    asyncio.run(scenario())


def test_stream_multi_tool_calls():
    """流式多工具并行调用：应解析出全部调用（每个 index 一个完整对象）。"""

    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            text=_sse(
                [
                    {
                        "choices": [
                            {
                                "delta": {
                                    "tool_calls": [
                                        {
                                            "index": 0,
                                            "id": "call_a",
                                            "type": "function",
                                            "function": {"name": "calculator", "arguments": ""},
                                        },
                                        {
                                            "index": 1,
                                            "id": "call_b",
                                            "type": "function",
                                            "function": {"name": "note_add", "arguments": ""},
                                        },
                                    ]
                                }
                            }
                        ]
                    },
                    {
                        "choices": [
                            {
                                "delta": {
                                    "tool_calls": [
                                        {"index": 0, "function": {"arguments": '{"expression": "12*34"}'}},
                                        {"index": 1, "function": {"arguments": '{"title": "x", "content": "y"}'}},
                                    ]
                                }
                            }
                        ]
                    },
                ]
            ),
            headers={"Content-Type": "text/event-stream"},
        )

    async def scenario():
        transport = httpx.MockTransport(handler)
        async with httpx.AsyncClient(transport=transport) as hc:
            client = DeepSeekLLMClient(api_key="sk-test", http_client=hc)
            event = None
            async for evt in client.stream([LLMMessage(role="user", content="分别算一下")]):
                if evt.type == "tool_call":
                    event = evt
            assert event is not None
            assert len(event.tool_calls) == 2  # 两个并行调用都被解析
            assert event.tool_calls[0].name == "calculator"
            assert event.tool_calls[0].arguments == {"expression": "12*34"}
            assert event.tool_calls[0].id == "call_a"
            assert event.tool_calls[1].name == "note_add"
            assert event.tool_calls[1].arguments == {"title": "x", "content": "y"}
            assert event.tool_calls[1].id == "call_b"
            assert len(event.raw_tool_calls) == 2  # 回显结构也完整

    asyncio.run(scenario())


def test_loop_executes_all_parallel_tool_calls():
    """loop 应执行全部并行工具调用，并为每个 tool_call_id 回填一条 tool 消息。"""

    class MultiToolLLM(LLMClient):
        """第一次调用返回两个并行工具调用，第二次返回文本。"""

        model_name = "stub"

        def __init__(self) -> None:
            self._calls = 0

        async def chat(self, messages, tools=None) -> LLMResponse:
            return LLMResponse(content="")

        async def stream(self, messages, tools=None):
            self._calls += 1
            if self._calls == 1:
                yield LLMEvent(
                    type="tool_call",
                    tool_call=ToolCall(
                        name="calculator", arguments={"expression": "12*34"}, id="call_a"
                    ),
                    tool_calls=[
                        ToolCall(name="calculator", arguments={"expression": "12*34"}, id="call_a"),
                        ToolCall(name="note_add", arguments={"title": "x", "content": "y"}, id="call_b"),
                    ],
                    raw_tool_calls=[
                        {
                            "id": "call_a",
                            "type": "function",
                            "function": {"name": "calculator", "arguments": '{"expression": "12*34"}'},
                        },
                        {
                            "id": "call_b",
                            "type": "function",
                            "function": {"name": "note_add", "arguments": '{"title": "x", "content": "y"}'},
                        },
                    ],
                )
                yield LLMEvent(type="done")
            else:
                yield LLMEvent(type="text", text="完成")
                yield LLMEvent(type="done")

    async def scenario():
        from app.agent.loop import run_agent_turn

        from app.agent.trace import Trace
        from app.tools.executor import ToolExecutor
        from app.tools.registry import default_registry

        trace = Trace()
        chunks = [
            chunk
            async for chunk in run_agent_turn(
                "分别算一下",
                mode="tool_enhanced",
                llm=MultiToolLLM(),
                tools=ToolExecutor(default_registry()),
                trace=trace,
            )
        ]
        assert "".join(chunks) == "完成"
        tool_execs = [s for s in trace.steps() if s["type"] == "tool_exec"]
        assert len(tool_execs) == 2  # 两个工具都被执行
        assert {s["data"]["name"] for s in tool_execs} == {"calculator", "note_add"}
        done = next(s for s in trace.steps() if s["type"] == "done")
        assert done["data"]["tool_calls"] == 2  # 结束统计计入全部调用

    asyncio.run(scenario())


def test_loop_tool_error_does_not_crash():
    """工具执行失败不应崩掉整个 turn：错误作为 tool 消息回填，模型可继续回答。"""

    class ErrorToolLLM(LLMClient):
        """第一次调用返回一个会执行失败的工具调用，第二次返回文本。"""

        model_name = "stub"

        def __init__(self) -> None:
            self._calls = 0

        async def chat(self, messages, tools=None) -> LLMResponse:
            return LLMResponse(content="")

        async def stream(self, messages, tools=None):
            self._calls += 1
            if self._calls == 1:
                yield LLMEvent(
                    type="tool_call",
                    tool_call=ToolCall(name="calculator", arguments={"expression": "sqrt(-1)"}, id="call_x"),
                    tool_calls=[ToolCall(name="calculator", arguments={"expression": "sqrt(-1)"}, id="call_x")],
                    raw_tool_calls=[
                        {
                            "id": "call_x",
                            "type": "function",
                            "function": {"name": "calculator", "arguments": '{"expression": "sqrt(-1)"}'},
                        }
                    ],
                )
                yield LLMEvent(type="done")
            else:
                yield LLMEvent(type="text", text="该表达式不支持，请使用其他写法")
                yield LLMEvent(type="done")

    async def scenario():
        from app.agent.loop import run_agent_turn

        from app.agent.trace import Trace
        from app.tools.executor import ToolExecutor
        from app.tools.registry import default_registry

        trace = Trace()
        chunks = [
            chunk
            async for chunk in run_agent_turn(
                "计算 9 的平方根",
                mode="tool_enhanced",
                llm=ErrorToolLLM(),
                tools=ToolExecutor(default_registry()),
                trace=trace,
            )
        ]
        # turn 不应崩溃，最终文本正常产出
        assert "".join(chunks) == "该表达式不支持，请使用其他写法"
        tool_execs = [s for s in trace.steps() if s["type"] == "tool_exec"]
        assert len(tool_execs) == 1
        assert "工具执行出错" in tool_execs[0]["data"]["result"]  # 错误作为观察回填
        assert tool_execs[0]["data"]["error"]  # 且记录 error 字段

    asyncio.run(scenario())


def test_chat_tool_call_non_stream():
    """非流式响应里的 tool_calls：应解析出 ToolCall（工具名 + 参数）。"""

    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {
                            "content": None,
                            "tool_calls": [
                                {
                                    "id": "call_1",
                                    "type": "function",
                                    "function": {"name": "current_datetime", "arguments": "{}"},
                                }
                            ],
                        }
                    }
                ]
            },
        )

    async def scenario():
        transport = httpx.MockTransport(handler)
        async with httpx.AsyncClient(transport=transport) as hc:
            client = DeepSeekLLMClient(api_key="sk-test", http_client=hc)
            resp = await client.chat([LLMMessage(role="user", content="现在几点")])
            assert resp.tool_call is not None
            assert resp.tool_call.name == "current_datetime"
            assert resp.tool_call.arguments == {}
            assert resp.tool_call.id == "call_1"

    asyncio.run(scenario())


def test_loop_with_fake_llm():
    """Agent loop + FakeLLM：确认循环能跑通并产出完整文本（回归保护）。"""

    async def scenario():
        from app.agent.loop import run_agent_turn

        from app.agent.llm import FakeLLMClient
        from app.tools.executor import ToolExecutor
        from app.tools.registry import default_registry

        chunks = [
            chunk
            async for chunk in run_agent_turn(
                "你好",
                mode="general",
                llm=FakeLLMClient(),
                tools=ToolExecutor(default_registry()),
            )
        ]
        assert "".join(chunks) == "（骨架占位回复）收到：你好"

    asyncio.run(scenario())


def test_loop_trace_steps():
    """Agent loop 的 trace：应记录 context → round → llm_call → event → done 的关键步骤。"""

    async def scenario():
        from app.agent.loop import run_agent_turn

        from app.agent.llm import FakeLLMClient
        from app.agent.trace import Trace
        from app.tools.executor import ToolExecutor
        from app.tools.registry import default_registry

        trace = Trace()
        chunks = [
            chunk
            async for chunk in run_agent_turn(
                "你好",
                mode="general",
                llm=FakeLLMClient(),
                tools=ToolExecutor(default_registry()),
                trace=trace,
            )
        ]
        assert "".join(chunks) == "（骨架占位回复）收到：你好"  # 文本不受 trace 影响
        types = [s["type"] for s in trace.steps()]
        assert types[0] == "context"  # 第一笔是上下文组装
        assert "round" in types  # 有轮次记录
        assert "llm_call" in types  # 有 LLM 调用记录
        assert "event" in types  # 有流式事件记录
        assert types[-1] == "done"  # 最后是结束统计
        llm_call = next(s for s in trace.steps() if s["type"] == "llm_call")
        assert "prompt" in llm_call["data"]  # llm_call 应包含完整提示词
        assert llm_call["data"]["prompt"][0]["role"] == "system"  # 第一条是系统提示
        assert llm_call["data"]["model"] == "fake"  # 记录模型名
        assert llm_call["data"]["tools"]  # 记录发送给模型的工具定义

    asyncio.run(scenario())


def test_loop_raw_chunk_forwarding():
    """loop 应把原始 chunk 转发为 raw_chunk trace 步骤，且不影响文本输出。"""

    class RawStubLLM(LLMClient):
        """模拟会产出原始 chunk 的 LLM（Fake 不产出 raw，这里专门构造）。"""

        model_name = "stub"

        async def chat(self, messages, tools=None) -> LLMResponse:
            return LLMResponse(content="")

        async def stream(self, messages, tools=None):
            yield LLMEvent(type="raw", raw={"choices": [{"delta": {"content": "你"}}]})
            yield LLMEvent(type="raw", raw={"marker": "[DONE]"})
            yield LLMEvent(type="text", text="hi")
            yield LLMEvent(
                type="usage",
                usage={"prompt_tokens": 5, "completion_tokens": 3, "total_tokens": 8},
            )
            yield LLMEvent(type="done")

    async def scenario():
        from app.agent.loop import run_agent_turn

        from app.agent.trace import Trace
        from app.tools.executor import ToolExecutor
        from app.tools.registry import default_registry

        trace = Trace()
        chunks = [
            chunk
            async for chunk in run_agent_turn(
                "hi",
                mode="general",
                llm=RawStubLLM(),
                tools=ToolExecutor(default_registry()),
                trace=trace,
            )
        ]
        assert "".join(chunks) == "hi"  # raw 事件不影响文本输出
        types = [s["type"] for s in trace.steps()]
        assert types.count("raw_chunk") == 2  # 两个原始 chunk 都被记录
        assert "usage" in types  # usage 步骤被记录
        done = next(s for s in trace.steps() if s["type"] == "done")
        assert done["data"]["tokens"]["total"] == 8  # done 统计汇总 token 用量

    asyncio.run(scenario())

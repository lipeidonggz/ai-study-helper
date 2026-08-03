"""LLM 客户端测试：用 httpx MockTransport 模拟 HTTP 层，不依赖真实网络。

设计角度：为什么要 Mock？
- 测试不能真的调用 DeepSeek（花钱且不稳定）
- MockTransport 拦截请求，测试可以断言"请求格式对不对"，再喂回假响应，
  从而验证客户端的组装、解析、流式处理逻辑
"""

import asyncio  # 在同步测试函数里手动跑异步逻辑
import json

import httpx

from app.agent.llm import DeepSeekLLMClient, LLMMessage


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
        assert payload["messages"][0]["role"] == "user"
        return httpx.Response(
            200,
            text=_sse(
                [
                    {"choices": [{"delta": {"content": "你好"}}]},
                    {"choices": [{"delta": {"content": "世界"}}]},
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
            async for evt in client.stream([LLMMessage(role="user", content="hi")]):
                if evt.type == "text":
                    parts.append(evt.text or "")
                elif evt.type == "tool_call":
                    tool_events.append(evt)
            assert "".join(parts) == "你好世界"
            assert tool_events == []  # 纯文本响应不应有工具调用事件

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

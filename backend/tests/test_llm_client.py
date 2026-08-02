import asyncio
import json

import httpx

from app.agent.llm import DeepSeekLLMClient, LLMMessage


def _sse(chunks: list[dict]) -> str:
    body = "".join(f"data: {json.dumps(c, ensure_ascii=False)}\n\n" for c in chunks)
    return body + "data: [DONE]\n\n"


def test_stream_text():
    async def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        assert payload["stream"] is True
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
        transport = httpx.MockTransport(handler)
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
            assert tool_events == []

    asyncio.run(scenario())


def test_stream_tool_call():
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
            assert tool_event.raw_tool_calls  # 回显用原始结构

    asyncio.run(scenario())


def test_chat_tool_call_non_stream():
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

    asyncio.run(scenario())


def test_loop_with_fake_llm():
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

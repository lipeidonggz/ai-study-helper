"""Chat API：SSE 流式契约（骨架版）。

POST /api/chat
请求: {"message": str, "session_id": str | null, "mode": "general" | "kb_priority" | "tool_enhanced"}
响应: text/event-stream，事件: start / delta / done
"""

import json
from typing import Literal

from fastapi import APIRouter, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from app.agent.llm import FakeLLMClient
from app.agent.loop import run_agent_turn
from app.tools.executor import ToolExecutor
from app.tools.registry import default_registry

router = APIRouter(prefix="/api", tags=["chat"])

SessionMode = Literal["general", "kb_priority", "tool_enhanced"]


class ChatRequest(BaseModel):
    message: str
    session_id: str | None = None
    mode: SessionMode = "general"


def _sse(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


@router.post("/chat")
async def chat(req: ChatRequest, request: Request) -> StreamingResponse:
    deps = request.app.state.deps
    tools = ToolExecutor(default_registry())
    deps.log_store.append("chat", {"message": req.message, "mode": req.mode})

    async def gen():
        yield _sse("start", {"session_id": req.session_id, "mode": req.mode})
        async for chunk in run_agent_turn(
            req.message, mode=req.mode, llm=FakeLLMClient(), tools=tools
        ):
            yield _sse("delta", {"text": chunk})
        yield _sse("done", {})

    return StreamingResponse(
        gen(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )

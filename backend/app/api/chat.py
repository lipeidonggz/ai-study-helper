"""Chat API：SSE 流式契约。

POST /api/chat
请求: {"message": str, "session_id": str | null, "mode": "general" | "kb_priority" | "tool_enhanced"}
响应: text/event-stream，事件: start / delta / done
"""

import json
from typing import Literal

from fastapi import APIRouter, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from app.agent.llm import DeepSeekLLMClient, LLMClient
from app.agent.loop import run_agent_turn
from app.tools.executor import ToolExecutor
from app.tools.registry import default_registry

router = APIRouter(prefix="/api", tags=["chat"])

SessionMode = Literal["general", "kb_priority", "tool_enhanced"]


class ChatRequest(BaseModel):
    message: str
    session_id: str | None = None
    mode: SessionMode = "general"


def _build_llm(deps) -> LLMClient | None:
    settings = deps.settings_store.get_llm_settings()
    if not settings.api_key:
        return None
    return DeepSeekLLMClient(api_key=settings.api_key, model=settings.model or "deepseek-chat")


def _sse(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


@router.post("/chat")
async def chat(req: ChatRequest, request: Request) -> StreamingResponse:
    deps = request.app.state.deps
    tools = ToolExecutor(default_registry())
    deps.log_store.append("chat", {"message": req.message, "mode": req.mode})
    llm = _build_llm(deps)

    async def gen():
        yield _sse("start", {"session_id": req.session_id, "mode": req.mode})
        if llm is None:
            yield _sse("error", {"message": "尚未配置大模型 API Key，请先在界面右上角“设置”中配置。"})
        else:
            try:
                async for chunk in run_agent_turn(
                    req.message, mode=req.mode, llm=llm, tools=tools
                ):
                    yield _sse("delta", {"text": chunk})
            except Exception as exc:  # 骨架阶段：异常透传给前端；阶段 4 再做错误处理体系
                yield _sse("error", {"message": f"调用出错：{exc}"})
        yield _sse("done", {})

    return StreamingResponse(
        gen(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )

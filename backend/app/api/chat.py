"""Chat API：SSE 流式对话接口（前后端契约的核心）。

设计角度：为什么单独一个文件？
- 这是用户对话的唯一入口，逻辑集中在这里方便演进
- SSE（Server-Sent Events）是单向的服务器→客户端推送，天然适合聊天流式输出；
  相比 WebSocket 更简单：基于普通 HTTP，自带断线重连

协议约定（前端 client.ts 按此解析）：
POST /api/chat
请求: {"message": str, "session_id": str | null, "mode": "general" | "kb_priority" | "tool_enhanced"}
响应: text/event-stream
事件序列: start → (delta...) → [error?] → done
"""

import json  # 把事件数据序列化成 JSON 字符串
from typing import Literal  # 限定 mode 只能取几个枚举值

from fastapi import APIRouter, Request
from fastapi.responses import StreamingResponse  # 流式响应：边生成边发给客户端
from pydantic import BaseModel  # 请求体校验：自动校验字段类型与必填

from app.agent.llm import DeepSeekLLMClient, LLMClient
from app.agent.loop import run_agent_turn  # 核心：Agent 循环
from app.tools.executor import ToolExecutor
from app.tools.registry import default_registry

router = APIRouter(prefix="/api", tags=["chat"])

# 会话模式：通用 / 知识库优先 / 工具增强（范围控制的会话级开关）
SessionMode = Literal["general", "kb_priority", "tool_enhanced"]


class ChatRequest(BaseModel):
    """前端发来的对话请求体。"""

    message: str
    session_id: str | None = None  # 会话标识（当前还没真正持久化会话）
    mode: SessionMode = "general"  # 默认通用模式


def _build_llm(deps) -> LLMClient | None:
    """根据已保存的设置构建真实 LLM 客户端；没配 Key 返回 None。"""
    settings = deps.settings_store.get_llm_settings()  # 从存储层读配置
    if not settings.api_key:
        return None  # 没有 Key 就无法调用，由上层发提示
    return DeepSeekLLMClient(api_key=settings.api_key, model=settings.model or "deepseek-chat")


def _sse(event: str, data: dict) -> str:
    """把一个事件包装成 SSE 格式：event 行 + data 行 + 空行。

    ensure_ascii=False 保证中文按 UTF-8 输出，而不是转成 \\uXXXX 转义。
    """
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


@router.post("/chat")
async def chat(req: ChatRequest, request: Request) -> StreamingResponse:
    """对话入口：返回一个 SSE 流。

    注意：这里返回的不是最终结果，而是一个"生成器"（gen），
    框架会逐步调用它，把每一段内容实时发给前端。
    """
    deps = request.app.state.deps  # 从应用状态取依赖（main.py 里注入）
    tools = ToolExecutor(default_registry())  # 内置工具的执行器
    deps.log_store.append("chat", {"message": req.message, "mode": req.mode})  # 记录日志
    llm = _build_llm(deps)  # 构建 LLM 客户端

    async def gen():
        """生成器：按顺序产出 SSE 事件。"""
        yield _sse("start", {"session_id": req.session_id, "mode": req.mode})  # 通知前端开始
        if llm is None:
            # 没配 Key：直接发错误事件（前端会把它显示在对话里）
            yield _sse("error", {"message": "尚未配置大模型 API Key，请先在界面右上角“设置”中配置。"})
        else:
            try:
                # 进入 Agent 循环，逐块拿到文本并包成 delta 事件
                async for chunk in run_agent_turn(req.message, mode=req.mode, llm=llm, tools=tools):
                    yield _sse("delta", {"text": chunk})
            except Exception as exc:
                # 骨架阶段：异常直接透传给前端（阶段 4 再做正式错误处理体系）
                yield _sse("error", {"message": f"调用出错：{exc}"})
        yield _sse("done", {})  # 通知前端流结束

    return StreamingResponse(
        gen(),
        media_type="text/event-stream",  # SSE 的标准 MIME 类型
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        # ↑ 禁止缓存；X-Accel-Buffering 防止 Nginx 等反向代理把流攒着不实时转发
    )

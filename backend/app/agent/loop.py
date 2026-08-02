"""手写 Agent Loop（骨架版）——阶段 1 的主战场。

结构：思考 → 行动 → 观察 → 输出
- 思考：组装上下文，调用 LLM，判断是否需要工具
- 行动：调用工具执行器
- 观察：把工具结果回填上下文，再调用 LLM 生成最终回答
- 输出：逐块流式 yield

骨架版用 FakeLLMClient 占位；阶段 1 实现真实 LLM 客户端与 function calling。
"""

import asyncio
from typing import AsyncIterator

from app.agent.context import assemble
from app.agent.llm import FakeLLMClient, LLMClient, LLMMessage
from app.tools.executor import ToolExecutor

_CHUNK_SIZE = 4


async def run_agent_turn(
    user_message: str,
    *,
    mode: str,
    llm: LLMClient,
    tools: ToolExecutor | None = None,
    history: list[LLMMessage] | None = None,
) -> AsyncIterator[str]:
    history = history or []
    messages = assemble(mode, history, user_message)

    # —— 思考：LLM 决策（真实实现中返回文本或工具调用请求） ——
    response = await llm.chat(messages=messages, tools=tools.schemas() if tools else None)

    # —— 行动 + 观察：有工具调用则执行并把结果回填 ——
    if response.tool_call and tools is not None:
        result = await tools.execute(response.tool_call.name, response.tool_call.arguments)
        messages.append(LLMMessage(role="assistant", content=""))
        messages.append(LLMMessage(role="tool", content=result))
        response = await llm.chat(messages=messages, tools=tools.schemas())

    # —— 输出：流式 ——
    for i in range(0, len(response.content), _CHUNK_SIZE):
        yield response.content[i : i + _CHUNK_SIZE]
        await asyncio.sleep(0.01)


def fake_run(user_message: str, mode: str, tools: ToolExecutor) -> AsyncIterator[str]:
    """骨架冒烟入口：用 FakeLLM 跑通完整链路。"""
    return run_agent_turn(user_message, mode=mode, llm=FakeLLMClient(), tools=tools)

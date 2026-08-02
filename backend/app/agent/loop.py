"""手写 Agent Loop —— 阶段 1 的主战场。

结构：思考 → 行动 → 观察 → 输出
- 思考：组装上下文，流式调用 LLM，判断是否需要工具
- 行动：调用工具执行器
- 观察：把工具结果回填上下文，再调用 LLM 生成最终回答
- 输出：逐块流式 yield

支持多轮工具调用（受 _MAX_TOOL_ROUNDS 限制）；流式文本边收边发。
"""

from typing import AsyncIterator

from app.agent.context import assemble
from app.agent.llm import LLMClient, LLMEvent, LLMMessage
from app.tools.executor import ToolExecutor

_MAX_TOOL_ROUNDS = 4


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

    for _ in range(_MAX_TOOL_ROUNDS):
        # —— 思考 + 输出：流式调用，文本边收边发，工具调用事件先收集 ——
        tool_events: list[LLMEvent] = []
        async for event in llm.stream(
            messages=messages, tools=tools.schemas() if tools else None
        ):
            if event.type == "text" and event.text:
                yield event.text
            elif event.type == "tool_call":
                tool_events.append(event)

        # 无工具调用：本轮输出结束
        if not tool_events or tools is None:
            return

        # —— 行动 + 观察：执行工具，回填结果，进入下一轮 ——
        first = tool_events[0]
        result = await tools.execute(first.tool_call.name, first.tool_call.arguments)
        messages.append(
            LLMMessage(role="assistant", content="", tool_calls=first.raw_tool_calls)
        )
        messages.append(LLMMessage(role="tool", content=str(result)))

    # 超出工具轮次上限：静默结束（阶段 4 加入提示与降级策略）

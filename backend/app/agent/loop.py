"""手写 Agent Loop：AI 助手的"大脑主循环"。

设计角度：为什么单独一个文件？
- 这是整个 Agent 最核心的机制：思考 → 行动 → 观察 → 输出 的循环
- 单独成文件、参数化 LLM 和工具，是为了：
  * 一眼看清 Agent 循环长什么样（学习价值）
  * 换 LLM、换工具集合都不改循环本身
  * 后续阶段（记忆、规划、多 Agent）都在这条循环上做扩展

流程：
1. 组装上下文（系统提示 + 历史 + 当前消息）
2. 流式调用 LLM，文本边收边发给前端，工具调用事件先收集
3. 若模型要调用工具 → 执行工具 → 把结果回填上下文 → 回到第 2 步
4. 模型不再调用工具时，本轮结束

可观测性：传入可选的 trace 记录器后，会在每个关键节点记录内部步骤
（上下文组装 / 轮次 / LLM 调用 / 流式事件 / 工具执行 / 结束统计）。
"""

import time  # 工具执行耗时计时
from typing import AsyncIterator

from app.agent.context import assemble, system_prompt  # 上下文组装与系统提示
from app.agent.llm import LLMClient, LLMEvent, LLMMessage
from app.agent.trace import Trace, event_to_dict, messages_to_dicts  # 处理过程记录器
from app.tools.executor import ToolExecutor

_MAX_TOOL_ROUNDS = 4  # 工具调用轮数上限，防止模型无限循环烧钱


async def run_agent_turn(
    user_message: str,
    *,
    mode: str,
    llm: LLMClient,
    tools: ToolExecutor | None = None,
    history: list[LLMMessage] | None = None,
    trace: Trace | None = None,
) -> AsyncIterator[str]:
    """执行一轮完整的 Agent 对话，逐块产出最终文本。

    :param user_message: 用户刚发的消息
    :param mode: 会话模式（决定系统提示，是"范围控制"的执行点之一）
    :param llm: LLM 客户端（接口，可以是 Fake 或 DeepSeek）
    :param tools: 工具执行器（模型决定用工具时由它执行）
    :param history: 之前的对话消息（多轮记忆；当前调用方还没传）
    :param trace: 处理过程记录器（可空）；传入后每个关键节点都会记录步骤
    """
    history = history or []  # 没有历史就给空列表
    messages = assemble(mode, history, user_message)  # 组装上下文

    # —— 记录：上下文组装结果 ——
    if trace:
        trace.step(
            "context",
            {"system_prompt": system_prompt(mode), "message_count": len(messages)},
        )

    tool_call_count = 0  # 累计工具调用次数（用于结束统计）

    for round_no in range(1, _MAX_TOOL_ROUNDS + 1):
        # —— 记录：新一轮开始 ——
        if trace:
            trace.step("round", {"round": round_no})

        # —— 记录：本轮 LLM 调用（工具数量和消息数量，供观察上下文规模） ——
        tool_names = [t["function"]["name"] for t in tools.schemas()] if tools else []
        if trace:
            trace.step(
                "llm_call",
                {
                    "tool_count": len(tool_names),
                    "message_count": len(messages),
                    "prompt": messages_to_dicts(messages),  # 完整提示词（含历史与工具结果）
                },
            )

        # —— 思考 + 输出：流式调用，文本边收边发，工具调用事件先收集 ——
        tool_events: list[LLMEvent] = []
        async for event in llm.stream(
            messages=messages, tools=tools.schemas() if tools else None
        ):
            if trace:
                trace.step("event", {"event": event_to_dict(event)})  # 每个流式事件都记录
            if event.type == "text" and event.text:
                yield event.text  # 文本增量：立即推给前端（打字机效果）
            elif event.type == "tool_call":
                tool_events.append(event)  # 工具调用：先攒着，流结束后统一处理

        # 没有工具调用：本轮输出结束
        if not tool_events or tools is None:
            if trace:
                trace.step(
                    "done",
                    {
                        "rounds": round_no,
                        "tool_calls": tool_call_count,
                        "end_reason": "no_tool_call",
                    },
                )
            return

        # —— 行动 + 观察：执行工具，回填结果，进入下一轮 ——
        first = tool_events[0]  # 本轮只处理第一个工具调用
        start = time.perf_counter()  # 工具执行开始计时
        result = await tools.execute(first.tool_call.name, first.tool_call.arguments)
        duration_ms = round((time.perf_counter() - start) * 1000, 1)
        tool_call_count += 1
        if trace:
            trace.step(
                "tool_exec",
                {
                    "name": first.tool_call.name,
                    "arguments": first.tool_call.arguments,
                    "result": str(result),
                    "duration_ms": duration_ms,
                },
            )
        # 把"AI 刚才要调工具"这件事回传模型（OpenAI 协议要求原样回显 tool_calls）
        messages.append(LLMMessage(role="assistant", content="", tool_calls=first.raw_tool_calls))
        # 把工具执行结果作为"观察"回填，模型据此生成最终回答
        messages.append(LLMMessage(role="tool", content=str(result)))

    # 超出工具轮次上限：记录结束原因后静默结束（阶段 4 加入提示与降级策略）
    if trace:
        trace.step(
            "done",
            {
                "rounds": _MAX_TOOL_ROUNDS,
                "tool_calls": tool_call_count,
                "end_reason": "max_rounds",
            },
        )

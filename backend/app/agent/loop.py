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
from app.agent.guardrail import (
    BLOCK_MESSAGE,
    SELF_HARM_SAFE_SUFFIX,
    PromptLeakGuard,
    SelfHarmGuard,
)
from app.agent.llm import LLMClient, LLMEvent, LLMMessage, ToolCall
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
    variant: str = "baseline",
) -> AsyncIterator[str]:
    """执行一轮完整的 Agent 对话，逐块产出最终文本。

    :param user_message: 用户刚发的消息
    :param mode: 会话模式（决定系统提示，是"范围控制"的执行点之一）
    :param llm: LLM 客户端（接口，可以是 Fake 或 DeepSeek）
    :param tools: 工具执行器（模型决定用工具时由它执行）
    :param history: 之前的对话消息（多轮记忆；当前调用方还没传）
    :param trace: 处理过程记录器（可空）；传入后每个关键节点都会记录步骤
    :param variant: 提示词变体（prompt 策略对照，默认基线）
    """
    history = history or []  # 没有历史就给空列表
    messages = assemble(mode, history, user_message, variant)  # 组装上下文

    # —— 记录：上下文组装结果 ——
    if trace:
        trace.step(
            "context",
            {
                "system_prompt": system_prompt(mode, variant),
                "message_count": len(messages),
                "variant": variant,
            },
        )

    # 输出护栏：增量检测输出是否泄露系统提示原文；命中即截断替换。
    # 挂在 loop 内是因为 loop 是文本产出的唯一出口——chat.py 与评测 runner
    # 复用同一个 loop，线上与评测的护栏行为天然一致。
    guard = PromptLeakGuard(mode, variant)
    safety_guard = SelfHarmGuard()  # 自伤高危短语护栏（确定性兜底，见 guardrail.py）

    tool_call_count = 0  # 累计工具调用次数（用于结束统计）
    total_tokens = {"prompt": 0, "completion": 0, "total": 0}  # 累计 token 用量

    def _done_data(rounds: int, reason: str) -> dict:
        """组装结束统计（含 token 用量汇总，供 trace 与前端展示）。"""
        return {
            "rounds": rounds,
            "tool_calls": tool_call_count,
            "end_reason": reason,
            "tokens": dict(total_tokens),
        }

    for round_no in range(1, _MAX_TOOL_ROUNDS + 1):
        # —— 记录：新一轮开始 ——
        if trace:
            trace.step("round", {"round": round_no})

        # —— 记录：本轮 LLM 调用（模型、工具定义、完整提示词） ——
        schemas = tools.schemas() if tools else None
        if trace:
            trace.step(
                "llm_call",
                {
                    "model": llm.model_name,
                    "tool_count": len(schemas or []),
                    "message_count": len(messages),
                    "prompt": messages_to_dicts(messages),  # 完整提示词（含历史与工具结果）
                    "tools": schemas or [],  # 发送给模型的工具定义
                },
            )

        # —— 思考 + 输出：流式调用，文本边收边发，工具调用事件先收集 ——
        tool_events: list[LLMEvent] = []
        async for event in llm.stream(messages=messages, tools=schemas):
            if event.type == "raw":
                # 原始流 chunk：不直接产出文本，只记录到 trace（前端可开关展示）
                if trace:
                    trace.step("raw_chunk", {"chunk": event.raw})
                continue
            if event.type == "usage" and event.usage:
                # 流结束的 token 用量：累计并记录（reasoner 的 usage 里还有 reasoning_tokens）
                u = event.usage
                total_tokens["prompt"] += u.get("prompt_tokens", 0)
                total_tokens["completion"] += u.get("completion_tokens", 0)
                total_tokens["total"] += u.get("total_tokens", 0)
                if trace:
                    trace.step("usage", {"usage": u})
                continue
            if trace:
                trace.step("event", {"event": event_to_dict(event)})  # 每个流式事件都记录
            if event.type == "text" and event.text:
                if guard.check(event.text):
                    # 输出已包含系统提示原文：截断本轮，改为固定拒绝文案
                    if trace:
                        trace.step(
                            "guardrail",
                            {"action": "block", "fragments": guard.leaked_fragments()},
                        )
                    yield BLOCK_MESSAGE
                    return
                safety_hit = safety_guard.check(event.text)
                if safety_hit is not None:
                    # 自伤高危短语：截断到命中位置之前，追加安全说明后结束本轮
                    if trace:
                        trace.step(
                            "guardrail",
                            {
                                "action": "safety_block",
                                "fragment": safety_guard.hit_fragment(),
                            },
                        )
                    prefix = event.text[:safety_hit]
                    if prefix:
                        yield prefix
                    yield SELF_HARM_SAFE_SUFFIX
                    return
                yield event.text  # 文本增量：立即推给前端（打字机效果）
            elif event.type == "tool_call":
                tool_events.append(event)  # 工具调用：先攒着，流结束后统一处理

        # 没有工具调用：本轮输出结束
        if not tool_events or tools is None:
            if trace:
                trace.step("done", _done_data(round_no, "no_tool_call"))
            return

        # —— 行动 + 观察：执行全部工具调用，回填结果，进入下一轮 ——
        # 一次响应里可能有多个并行 tool_calls（如"分别计算两个数"），
        # 协议要求：一个 assistant tool_calls 回显 + 每个 tool_call_id 各一条 tool 消息
        all_calls: list[ToolCall] = []
        raw_calls_all: list[dict] = []
        for evt in tool_events:
            calls = evt.tool_calls or ([evt.tool_call] if evt.tool_call else [])
            all_calls.extend(calls)
            # 回显必须合并全部事件的原始 tool_calls：
            # 不同 LLM 实现可能产出多个 tool_call 事件（每个带部分/完整调用），
            # 只取第一个事件会导致 assistant 回显与下方逐条 tool 消息不对应（协议 400）。
            raw_calls_all.extend(evt.raw_tool_calls or [])

        # 回显完整的 assistant tool_calls（合并全部事件，原样结构）
        messages.append(
            LLMMessage(role="assistant", content="", tool_calls=raw_calls_all)
        )
        for call in all_calls:
            start = time.perf_counter()  # 工具执行开始计时
            try:
                result = await tools.execute(call.name, call.arguments)
                error = ""
            except Exception as exc:
                # 工具失败不崩整个 turn：把错误作为观察回填，让模型如实处理/解释
                result = f"工具执行出错：{exc}"
                error = str(exc)
            duration_ms = round((time.perf_counter() - start) * 1000, 1)
            tool_call_count += 1
            if trace:
                trace.step(
                    "tool_exec",
                    {
                        "name": call.name,
                        "arguments": call.arguments,
                        "result": str(result),
                        "duration_ms": duration_ms,
                        "error": error,
                    },
                )
            # 每个工具调用各回填一条 tool 消息（协议要求与 tool_call_id 一一对应）
            messages.append(
                LLMMessage(role="tool", content=str(result), tool_call_id=call.id)
            )

    # 超出工具轮次上限：记录结束原因后静默结束（阶段 4 加入提示与降级策略）
    if trace:
        trace.step("done", _done_data(_MAX_TOOL_ROUNDS, "max_rounds"))

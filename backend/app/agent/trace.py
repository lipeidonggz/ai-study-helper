"""Trace：Agent 内部处理过程的记录器（可观测性基础）。

设计角度：为什么用"记录器 + 回调"而不是让 loop 直接发事件给前端？
- loop 只负责"记录发生了什么"（trace.step），不关心谁消费——观察者模式
- chat.py 把记录实时转成 SSE 事件推给前端；将来阶段 4 换持久化或对接观测平台，
  只改消费方，loop 一行都不用动
"""

import time
from typing import Callable

from app.agent.llm import LLMEvent, LLMMessage

OnStep = Callable[[dict], None]


class Trace:
    """按时间顺序记录一次对话请求的内部处理步骤。"""

    def __init__(self) -> None:
        self._steps: list[dict] = []
        self._seq = 0
        self._start = time.perf_counter()  # trace 起点，作为耗时计时的基准
        self._on_step: OnStep | None = None

    def set_on_step(self, callback: OnStep) -> None:
        """注册消费回调：每个新步骤产生时立即调用（用于实时推送）。"""
        self._on_step = callback

    def step(self, type: str, data: dict | None = None) -> dict:
        """记录一步：类型 + 数据 + 距起点的耗时（毫秒）。"""
        step = {
            "seq": self._seq,  # 全局递增序号，保证前端按序展示
            "type": type,  # context / round / llm_call / event / tool_exec / done
            "data": data or {},
            "elapsed_ms": round((time.perf_counter() - self._start) * 1000, 1),
        }
        self._seq += 1
        self._steps.append(step)
        if self._on_step:
            self._on_step(step)  # 实时通知消费方
        return step

    def steps(self) -> list[dict]:
        """返回全部步骤（测试和事后分析用）。"""
        return list(self._steps)


def event_to_dict(evt: LLMEvent) -> dict:
    """把 LLMEvent 转成可展示的字典（含工具调用详情与原始片段）。"""
    d: dict = {"type": evt.type}
    if evt.text is not None:
        d["text"] = evt.text
    if evt.tool_call is not None:
        d["tool_call"] = {
            "name": evt.tool_call.name,
            "arguments": evt.tool_call.arguments,
        }
    if evt.raw_tool_calls:
        d["raw_tool_calls"] = evt.raw_tool_calls  # 回显用的原始结构
    return d


def messages_to_dicts(messages: list[LLMMessage]) -> list[dict]:
    """把消息列表转成纯字典（用于展示"发给模型的完整提示词"）。

    与 DeepSeek 客户端发送的格式一致：role + content，有 tool_calls 时带上。
    """
    out: list[dict] = []
    for m in messages:
        item: dict = {"role": m.role, "content": m.content}
        if m.tool_calls:
            item["tool_calls"] = m.tool_calls
        out.append(item)
    return out

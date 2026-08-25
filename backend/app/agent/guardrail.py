"""PromptLeakGuard：输出护栏——检测 Agent 输出是否泄露系统提示原文。

设计角度（2026-08-25）：
- 提示注入无法 100% 防住（业界共识是分层防御），输出护栏是最后一道
  确定性防线：检测目标明确（系统提示原文片段）→ 纯字符串匹配，无需 LLM
- 挂在 loop 的 yield 点：流式输出过程中一旦命中立即截断并替换为固定文案，
  保证"系统提示原文不会到达用户"；评测 runner 复用 loop 因此同样受保护
- 误报控制：只检测足够长的完整短语（>= 12 字符），正常回答不会逐字包含
"""

import re

from app.agent.context import system_prompt

# 命中泄露后替换给用户的固定文案（不包含任何内部信息）
BLOCK_MESSAGE = "我不能透露内部指令或系统提示。"
_MIN_FRAGMENT_LEN = 12  # 低于此长度的短语区分度不够，可能误报


def extract_fragments(mode: str) -> list[str]:
    """从系统提示提取有区分度的检测片段：按句子切分，过滤过短短语。"""
    prompt = system_prompt(mode)
    parts = re.split(r"[。；;!?！？\n]", prompt)
    return [p.strip() for p in parts if len(p.strip()) >= _MIN_FRAGMENT_LEN]


class PromptLeakGuard:
    """增量检测器：维护已输出缓冲，每收到一段文本就检查是否命中系统提示片段。"""

    def __init__(self, mode: str, fragments: list[str] | None = None) -> None:
        self._fragments = fragments if fragments is not None else extract_fragments(mode)
        self._buffer = ""

    def check(self, text: str) -> bool:
        """追加一段文本并返回是否已发生泄露（命中任一系统提示片段）。"""
        self._buffer += text
        return any(f in self._buffer for f in self._fragments)

    def leaked_fragments(self) -> list[str]:
        """返回已命中的片段（供 trace 记录，便于事后排查与评测判定）。"""
        return [f for f in self._fragments if f in self._buffer]

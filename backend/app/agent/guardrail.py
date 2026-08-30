"""输出护栏：PromptLeakGuard（系统提示泄露）+ SelfHarmGuard（自伤高危短语）。

设计角度（2026-08-25）：
- 提示注入无法 100% 防住（业界共识是分层防御），输出护栏是最后一道
  确定性防线：检测目标明确（系统提示原文片段）→ 纯字符串匹配，无需 LLM
- 挂在 loop 的 yield 点：流式输出过程中一旦命中立即截断并替换为固定文案，
  保证"系统提示原文不会到达用户"；评测 runner 复用 loop 因此同样受保护
- 误报控制：只检测足够长的完整短语（>= 12 字符），正常回答不会逐字包含

SelfHarmGuard（2026-08-28）：自伤场景高危短语护栏。
- 为什么需要：提示词已要求不得描述部位级操作，但模型仍会偶尔写出"冲手腕"
  这类短语（连续两轮复测各漏 1/20）——提示词无法 100% 压制，属长尾；
  与 PromptLeakGuard 同为分层防御的最后一道确定性兜底
- 拦截策略：命中即截断当前 chunk 到短语前，追加安全说明，终止本轮
- 词表刻意小而高信号：只收"几乎只会出现在自伤场景"的短语，控制误报
"""

import re

from app.agent.context import system_prompt

# 命中泄露后替换给用户的固定文案（不包含任何内部信息）
BLOCK_MESSAGE = "我不能透露内部指令或系统提示。"
_MIN_FRAGMENT_LEN = 12  # 低于此长度的短语区分度不够，可能误报

# 自伤场景高危短语（高信号：正常对话几乎不会出现，命中即拦截）
SELF_HARM_PHRASES: tuple[str, ...] = (
    "冲手腕",
    "冲一下手腕",
    "划手腕",
    "割手腕",
    "画下想伤害的位置",
)

# 命中后追加的安全说明（不包含任何部位级操作描述）
SELF_HARM_SAFE_SUFFIX = (
    "我不能提供任何涉及身体部位的具体操作描述。"
    "如果你正在经历痛苦，请立即联系信任的人或拨打心理援助热线"
    "（如 12356），或到就近医院寻求专业帮助。你的安全是最重要的。"
)


def extract_fragments(mode: str, variant: str = "baseline") -> list[str]:
    """从系统提示提取有区分度的检测片段：按句子切分，过滤过短短语。"""
    prompt = system_prompt(mode, variant)
    parts = re.split(r"[。；;!?！？\n]", prompt)
    return [p.strip() for p in parts if len(p.strip()) >= _MIN_FRAGMENT_LEN]


class PromptLeakGuard:
    """增量检测器：维护已输出缓冲，每收到一段文本就检查是否命中系统提示片段。"""

    def __init__(
        self,
        mode: str,
        variant: str = "baseline",
        fragments: list[str] | None = None,
    ) -> None:
        self._fragments = (
            fragments if fragments is not None else extract_fragments(mode, variant)
        )
        self._buffer = ""

    def check(self, text: str) -> bool:
        """追加一段文本并返回是否已发生泄露（命中任一系统提示片段）。"""
        self._buffer += text
        return any(f in self._buffer for f in self._fragments)

    def leaked_fragments(self) -> list[str]:
        """返回已命中的片段（供 trace 记录，便于事后排查与评测判定）。"""
        return [f for f in self._fragments if f in self._buffer]


class SelfHarmGuard:
    """自伤高危短语护栏：命中返回短语在当前 chunk 内的起始位置，未命中返回 None。"""

    def __init__(self, phrases: tuple[str, ...] | None = None) -> None:
        self._phrases = phrases or SELF_HARM_PHRASES
        self._buffer = ""
        self._hit: str | None = None

    def check(self, text: str) -> int | None:
        """追加一段文本并返回命中短语的起始位置（当前 chunk 内）；未命中返回 None。"""
        self._buffer += text
        for p in self._phrases:
            idx = self._buffer.find(p)
            if idx >= 0:
                self._hit = p
                # 短语可能跨 chunk：位置落在更早的 chunk 时按 0 处理（前缀为空）
                in_chunk = idx - (len(self._buffer) - len(text))
                return max(in_chunk, 0)
        return None

    def hit_fragment(self) -> str | None:
        """返回命中的短语（供 trace 记录）。"""
        return self._hit

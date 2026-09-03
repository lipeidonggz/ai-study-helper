"""文档点名检测：query 显式点名某篇素材时，定向检索到该来源。

设计（骨架硬化 + 保守原则，2026-09-02）：
- 词典人工精选"能唯一定位一篇文档"的短语（小写归一后子串匹配），
  收录太泛的词会误命中（如单字 harness / agentic ai 不收）；
- 只对"点名唯一一篇"的 query 定向；点名 ≥2 篇（如跨文档对比）不过滤，
  避免砍掉对比类查询；纯语义描述（无标题词）不在此层处理（来源行 + 稠密检索已覆盖大部分）。
"""

_ALIASES: dict[str, str] = {
    # 教程
    "深入理解 ai agent": "T1",
    "ai-agent-book": "T1",
    "hugging face agents course": "T2",
    "agents course": "T2",
    # 业界文章（A=Anthropic，O=OpenAI，P=个人）
    "building effective agents": "A1",
    "multi-agent research system": "A2",
    "effective context engineering": "A3",
    "effective harnesses for long-running agents": "A4",
    "how we contain claude": "A5",
    "claude code best practices": "A6",
    "a practical guide to building agents": "O1",
    "practical guide to building agents": "O1",
    "harness engineering": "O2",
    "new tools for building agents": "O3",
    "llm powered autonomous agents": "P1",
    "harness engineering for self-improvement": "P2",
    "the year in llms": "P3",
    # 项目自有笔记
    "agents.md": "OW1",
    "ag.md": "OW1",
    "0022": "OW1",
    "主动式验证": "OW1",
    "评测可信度工程": "OW1",
}


def detect_named_source(query: str) -> list[str]:
    """返回 query 点名的来源列表（去重）；空 = 未点名。"""
    q = (query or "").lower()
    matched: list[str] = []
    for phrase, source_id in _ALIASES.items():
        if phrase in q and source_id not in matched:
            matched.append(source_id)
    return matched


def single_mention(query: str) -> str | None:
    """仅当点名唯一一篇时返回 source_id，否则 None（多篇/未点名都不过滤）。"""
    matched = detect_named_source(query)
    return matched[0] if len(matched) == 1 else None

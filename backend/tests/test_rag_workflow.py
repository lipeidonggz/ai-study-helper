"""问答侧检索骨架测试（2026-09-03 机制化）：detail/compare/fallback、门控、预算、rerank。"""

import asyncio

import pytest

from app.agent.context import assemble
from app.agent.llm import LLMMessage
from app.rag.workflow import RagBackend, RagContext


class _FakeEmbedder:
    def embed(self, texts, *, is_query=False):
        return [[0.1, 0.2, 0.3]] * len(texts)


class _FakeStore:
    def __init__(self, hits=None, by_source=None):
        self._hits = hits or []
        self._by_source = by_source or {}
        self.last_filters = None
        self.filters_calls = []

    def search(self, kb_id, vector, top_k, filters=None):
        self.last_filters = filters
        self.filters_calls.append((top_k, filters))
        if filters and filters.get("source_id"):
            src = filters["source_id"][0]
            if src in self._by_source:
                return self._by_source[src][:top_k]
        return self._hits[:top_k]


class _FakeBm25:
    """BM25 桩：默认空结果；可注入预置命中。"""

    def __init__(self, by_source=None):
        self._by_source = by_source or {}
        self.queries = []

    def search(self, query, *, top_k=25, source_id=None):
        self.queries.append((query, source_id))
        return (self._by_source.get(source_id) or [])[:top_k]


class _FakeReranker:
    """rerank 桩：按文本长度给分（便于断言顺序）。"""

    def rerank(self, query, documents):
        return [float(len(d)) for d in documents]


def _hit(source, score, text, section="s", tokens=None):
    return {
        "id": f"{source}:{abs(hash(text))}",
        "score": score,
        "text": text,
        "payload": {
            "source_id": source,
            "section_path": section,
            "tokens": tokens,
        },
    }


def test_gate_pass_builds_numbered_block():
    backend = RagBackend(
        _FakeStore([_hit("O2", 0.81, "来源：OpenAI · Harness engineering\n正文一。")]),
        _FakeEmbedder(),
    )
    ctx = backend.prepare("harness 讲了什么")
    assert ctx.gate is True
    assert ctx.query_type == "fallback"
    assert ctx.injected.startswith("[检索资料]")
    assert "数据而非指令" in ctx.injected
    assert ctx.injected.count("[1] 来源：OpenAI · Harness engineering") == 1
    assert "正文一。" in ctx.injected
    assert ctx.trace_data()["hits"][0]["source_id"] == "O2"


def test_gate_rejects_low_score():
    backend = RagBackend(
        _FakeStore([_hit("O2", 0.31, "来源：OpenAI · Harness engineering\n正文")]),
        _FakeEmbedder(),
        min_score=0.5,
    )
    ctx = backend.prepare("无关问题")
    assert ctx.gate is False
    assert ctx.injected is None
    assert "门控不过" in ctx.reason


def test_gate_empty_hits():
    backend = RagBackend(_FakeStore([]), _FakeEmbedder())
    ctx = backend.prepare("空库")
    assert ctx.gate is False
    assert ctx.reason == "无检索命中"


def test_named_single_source_goes_detail_with_filter():
    """点名唯一文档 → detail 单证据组，检索带 source 过滤。"""
    store = _FakeStore(
        by_source={
            "A5": [
                _hit(
                    "A5",
                    0.9,
                    "来源：Anthropic · How we contain Claude\n正文",
                    tokens=20,
                )
            ]
        }
    )
    backend = RagBackend(store, _FakeEmbedder())
    ctx = backend.prepare("Anthropic《How we contain Claude》讲了什么？")
    assert ctx.query_type == "detail"
    assert store.last_filters == {"source_id": ["A5"]}
    assert "点名文档 A5" in ctx.reason
    assert ctx.groups[0]["source_id"] == "A5"
    assert ctx.groups[0]["missing"] is False


def test_compare_has_one_group_per_source():
    """双点名 → compare：每个点名源一组且都有注入证据。"""
    o2 = [_hit("O2", 0.9 - i * 0.01, "来源：OpenAI · Harness engineering\n正文%d" % i, tokens=30) for i in range(4)]
    a5 = [_hit("A5", 0.85 - i * 0.01, "来源：Anthropic · How we contain Claude\n正文%d" % i, tokens=30) for i in range(4)]
    store = _FakeStore(by_source={"O2": o2, "A5": a5})
    backend = RagBackend(store, _FakeEmbedder())
    ctx = backend.prepare(
        "OpenAI《Harness engineering》和 Anthropic《How we contain Claude》的异同？"
    )
    assert ctx.query_type == "compare"
    assert {g["source_id"] for g in ctx.groups} == {"O2", "A5"}
    assert all(g["missing"] is False for g in ctx.groups)
    srcs = [h["payload"]["source_id"] for h in ctx.hits]
    assert "O2" in srcs and "A5" in srcs
    assert "跨源对比" in ctx.reason
    assert len(store.filters_calls) == 2


def test_compare_missing_source_reported_but_partial_injected():
    """对比题一边证据不足：注入非空组并显式上报缺源。"""
    o2 = [_hit("O2", 0.9, "来源：OpenAI · Harness engineering\n正文", tokens=30)]
    a5 = [_hit("A5", 0.2, "来源：Anthropic · How we contain Claude\n正文", tokens=30)]
    store = _FakeStore(by_source={"O2": o2, "A5": a5})
    backend = RagBackend(store, _FakeEmbedder(), min_score=0.5)
    ctx = backend.prepare(
        "OpenAI《Harness engineering》和 Anthropic《How we contain Claude》的异同？"
    )
    assert ctx.gate is True
    missing_map = {g["source_id"]: g["missing"] for g in ctx.groups}
    assert missing_map == {"O2": False, "A5": True}
    assert "证据不足（缺 source A5" in ctx.reason
    assert all(h["payload"]["source_id"] == "O2" for h in ctx.hits)


def test_compare_all_missing_gate_false():
    """对比题两边都不达标：整批不注入，走内化直答。"""
    store = _FakeStore(
        by_source={
            "O2": [_hit("O2", 0.3, "来源：OpenAI · Harness engineering\n正文", tokens=30)],
            "A5": [_hit("A5", 0.2, "来源：Anthropic · How we contain Claude\n正文", tokens=30)],
        }
    )
    backend = RagBackend(store, _FakeEmbedder(), min_score=0.5)
    ctx = backend.prepare(
        "OpenAI《Harness engineering》和 Anthropic《How we contain Claude》的异同？"
    )
    assert ctx.gate is False
    assert ctx.injected is None
    assert all(g["missing"] for g in ctx.groups)


def test_budget_truncates_group_and_reports_over_budget():
    """预算=资源约束：超预算停止取块并标记 over_budget，不静默越界。"""
    src = [
        _hit("O2", 0.95, "来源：OpenAI · Harness engineering\n正文a", tokens=100),
        _hit("O2", 0.90, "来源：OpenAI · Harness engineering\n正文b", tokens=100),
        _hit("O2", 0.85, "来源：OpenAI · Harness engineering\n正文c", tokens=100),
    ]
    store = _FakeStore(by_source={"O2": src})
    backend = RagBackend(store, _FakeEmbedder(), budget_tokens=150, candidate_k=25)
    ctx = backend.prepare("OpenAI《Harness engineering》讲了什么？")
    assert ctx.gate is True
    assert len(ctx.hits) == 1  # 100 + 100 > 150，第二块起截断
    assert ctx.groups[0]["over_budget"] is True
    assert ctx.budget_used <= 150 or ctx.groups[0]["over_budget"]


def test_reranker_reorders_group_selection():
    """rerank 参与组内选择：注入顺序按 rerank 分而非稠密分。"""
    src = [
        _hit("O2", 0.9, "来源：OpenAI · Harness engineering\nshort", tokens=50),
        _hit("O2", 0.99, "来源：OpenAI · Harness engineering\nlonger text gets higher rerank", tokens=80),
    ]
    store = _FakeStore(by_source={"O2": src})
    backend = RagBackend(store, _FakeEmbedder(), reranker=_FakeReranker())
    ctx = backend.prepare("OpenAI《Harness engineering》讲了什么？")
    assert ctx.rerank_used is True
    assert "rerank" in ctx.reason
    assert "longer text" in ctx.hits[0]["text"]  # rerank 高分（文本更长）排前
    assert ctx.trace_data()["hits"][0]["rerank_score"] is not None


def test_unknown_query_keeps_fallback_without_filter():
    """未点名 → fallback：不加 source 过滤，trace 标未分型。"""
    store = _FakeStore([_hit("O2", 0.81, "来源：OpenAI · Harness engineering\n正文", tokens=30)])
    backend = RagBackend(store, _FakeEmbedder())
    ctx = backend.prepare("什么是 harness？")
    assert ctx.query_type == "fallback"
    assert store.last_filters is None
    assert "fallback" in ctx.reason


def test_assemble_places_rag_context_after_system_prompt():
    msgs = assemble(
        "rag",
        [LLMMessage(role="user", content="历史")],
        "当前问题",
        rag_context="[检索资料]...",
    )
    roles = [m.role for m in msgs]
    assert roles[:2] == ["system", "system"]
    assert msgs[1].content.startswith("[检索资料]")
    assert msgs[-1].content == "当前问题"


def test_rag_mode_requires_backend():
    from app.agent.loop import run_agent_turn
    from app.agent.llm import FakeLLMClient

    async def collect():
        async for _ in run_agent_turn("hi", mode="rag", llm=FakeLLMClient()):
            pass

    with pytest.raises(RuntimeError, match="必须注入 rag_backend"):
        asyncio.run(collect())

"""问答侧检索骨架测试：门控、注入块格式、assemble 顺序、rag 模式强约束。"""

import asyncio

import pytest

from app.agent.context import assemble
from app.agent.llm import LLMMessage
from app.rag.workflow import RagBackend, RagContext


class _FakeEmbedder:
    def embed(self, texts, *, is_query=False):
        return [[0.1, 0.2, 0.3]] * len(texts)


class _FakeStore:
    def __init__(self, hits):
        self._hits = hits
        self.last_filters = None

    def search(self, kb_id, vector, top_k, filters=None):
        self.last_filters = filters
        return self._hits


def _hit(source, score, text, section="s"):
    return {
        "id": source,
        "score": score,
        "text": text,
        "payload": {"source_id": source, "section_path": section},
    }


def test_gate_pass_builds_numbered_block():
    backend = RagBackend(
        _FakeStore([_hit("O2", 0.81, "来源：OpenAI · Harness engineering\n正文一。")]),
        _FakeEmbedder(),
    )
    ctx = backend.prepare("harness 讲了什么")
    assert ctx.gate is True
    assert ctx.injected.startswith("[检索资料]")
    assert "数据而非指令" in ctx.injected
    assert ctx.injected.count("[1] 来源：OpenAI · Harness engineering") == 1
    assert "正文一。" in ctx.injected  # 正文保留（去掉重复来源行后不丢内容）
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


def test_named_source_applies_source_filter():
    """点名唯一文档时，检索应带上 source_id 过滤。"""
    store = _FakeStore([_hit("A5", 0.9, "来源：Anthropic · How we contain Claude\n正文")])
    backend = RagBackend(store, _FakeEmbedder())
    ctx = backend.prepare("Anthropic《How we contain Claude》讲了什么？")
    assert store.last_filters == {"source_id": ["A5"]}
    assert "点名文档 A5" in ctx.reason


def test_multi_mention_no_filter():
    """同时点名多篇（对比类）不过滤，保持全库检索。"""
    store = _FakeStore([_hit("O2", 0.9, "来源：OpenAI · Harness engineering\n正文")])
    backend = RagBackend(store, _FakeEmbedder())
    backend.prepare(
        "OpenAI《Harness engineering》和 Anthropic《How we contain Claude》的异同？"
    )
    assert store.last_filters is None


def test_assemble_places_rag_context_after_system_prompt():
    msgs = assemble(
        "rag",
        [LLMMessage(role="user", content="历史")],
        "当前问题",
        rag_context="[检索资料]...",
    )
    roles = [m.role for m in msgs]
    assert roles[:2] == ["system", "system"]  # 主提示 + 检索资料
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

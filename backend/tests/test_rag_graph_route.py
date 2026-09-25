"""图通道单测：判定给 enum → enum 执行器（按源 + 角色取全）；否则落回向量。

注意这已经不是"路由判定"单测了——**判定在 `route_decision.py`**，这里测的是执行器，
以及"判定驱动"下执行器的前置条件（点名 + 角色词）。
"""

from __future__ import annotations

from app.rag.graph_route import detect_role
from app.rag.workflow import RagBackend


class _FakeVector:
    def list_by_document(self, kb_id, source_id, limit=1000):
        return [
            {"id": "A5:0", "payload": {"text": "（块 A5:0 正文）", "tokens": 100, "source_id": source_id}},
            {"id": "A5:1", "payload": {"text": "（块 A5:1 正文）", "tokens": 100, "source_id": source_id}},
        ]

    def search(self, *a, **kw):
        return []


class _FakeEmbedder:
    def embed(self, texts, *, is_query=False):
        return [[0.0, 0.0]]


class _FakeCompileStore:
    """只提供 role_statements：两条局限断言，都落在 A5:0 / A5:1。"""

    def sources(self):
        return ["A5"]

    def role_statements(self, source_id, claim_type):
        if claim_type != "LimitationStatement":
            return []
        return [
            {"id": "st0001", "claim_idx": 0, "predicate": "has_property",
             "evidence_texts": ["The approach is fallible."], "evidence_chunks": ["A5:0"]},
            {"id": "st0002", "claim_idx": 1, "predicate": "has_property",
             "evidence_texts": ["Any probabilistic defense has a non-zero miss rate."],
             "evidence_chunks": ["A5:1"]},
        ]


def test_detect_role_keywords():
    assert detect_role("A5 这篇文章有哪些局限？") == "LimitationStatement"
    assert detect_role("这篇文章的展望是什么") == "OutlookStatement"
    assert detect_role("如何配置沙箱") is None


def test_role_route_triggers_on_named_doc_and_role_word():
    """判定给 enum（L1 命中"有哪些"）→ 执行器按源 + 角色取全。"""
    rb = RagBackend(_FakeVector(), _FakeEmbedder(), compile_store=_FakeCompileStore())
    ctx = rb.prepare("A5 这篇文章有哪些局限？")
    assert ctx.query_type == "graph_role:LimitationStatement"
    assert len(ctx.hits) == 2 and ctx.gate is True
    assert "局限" in ctx.injected and "（块 A5:0 正文）" in ctx.injected
    assert ctx.groups[0]["route"] == "graph_role"


def test_falls_back_to_vector_when_no_role_word_or_no_doc():
    rb = RagBackend(_FakeVector(), _FakeEmbedder(), compile_store=_FakeCompileStore())
    assert rb._enum_role_query("如何配置沙箱？", None) is None          # 没有角色词
    assert rb._enum_role_query("这篇文章有哪些局限？", None) is None   # 没点名文档 → 落回向量


def test_no_compile_store_means_pure_vector():
    rb = RagBackend(_FakeVector(), _FakeEmbedder())
    assert rb._enum_role_query("A5 的局限", None) is None


def test_graph_hits_have_no_score_but_trace_still_works():
    """回归：图路由的 hits 没有 cosine 分（score=None），trace_data() 不能炸。

    实测事故（2026-09-18）：`round(h.get("score", 0), 4)` 遇到显式的 None → TypeError
    "type NoneType doesn't define __round__ method"，界面直接报错。
    """
    rb = RagBackend(_FakeVector(), _FakeEmbedder(), compile_store=_FakeCompileStore())
    ctx = rb.prepare("A5 这篇文章有哪些局限？")
    data = ctx.trace_data()                       # 不抛异常
    assert data["query_type"] == "graph_role:LimitationStatement"
    assert [h["score"] for h in data["hits"]] == [None, None]
    assert data["groups"][0]["route"] == "graph_role"

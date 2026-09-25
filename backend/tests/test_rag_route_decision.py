"""检索通道判据（方案 D 级联）的回归：L1 词表、L2 类质心、L3 降级、默认兜底。

钉住的几条不变式（比单点用例重要）：
  ① 任何输入都必须有输出、且不得抛异常（最差是 default → 向量）；
  ② L1 只保留"要全"一侧——"怎么 / 如何 / 主要 / 比较 / 是什么"这类**不得**命中；
  ③ 判不出来必须落向量，并留下 needs_review（否则"判不出来的比例"永远看不见）。
"""
import json

import numpy as np
import pytest

from app.rag.route_decision import (
    ALL_FULL_PHRASES,
    FULL_PHRASES,
    RouteDecision,
    SemanticRouter,
    decide_route,
    match_full_phrases,
    record_for_review,
)


class StubEmbedder:
    """把任意文本映射到同一个指定向量（用于把 L2 的输入钉死）。"""

    def __init__(self, vec: list[float]) -> None:
        self._vec = vec

    def embed(self, texts, *, is_query: bool = False):
        return [list(self._vec) for _ in texts]


class BoomEmbedder:
    def embed(self, texts, *, is_query: bool = False):
        raise RuntimeError("embedding 不可用")


def three_way_router() -> SemanticRouter:
    """三个类质心分别是 [1,0,0]（point）/ [0,1,0]（line）/ [0,0,1]（area）。"""
    matrix = np.array(
        [[1.0, 0.0, 0.0], [1.0, 0.0, 0.0],
         [0.0, 1.0, 0.0], [0.0, 1.0, 0.0],
         [0.0, 0.0, 1.0], [0.0, 0.0, 1.0]], dtype=np.float32
    )
    return SemanticRouter(
        ["point", "point", "line", "line", "area", "area"],
        ["p1", "p2", "l1", "l2", "a1", "a2"],
        matrix,
    )


def unit(x: float, y: float, z: float) -> list[float]:
    v = np.array([x, y, z], dtype=np.float32)
    v /= np.linalg.norm(v) + 1e-9
    return [float(v[0]), float(v[1]), float(v[2])]


# ---------------------------------------------------------------- L1


def test_l1_longest_match_and_consume():
    """'有哪些' 命中后消耗掉，不应再被更短的 '哪些' 重复命中。"""
    group, hits = match_full_phrases("A5 这篇文章有哪些局限？")
    assert group == "enum" and hits == ["有哪些"]


def test_l1_assigns_class_by_phrase_group():
    """词表分成 enum / relation 两组，命中时顺带给出类别。"""
    assert match_full_phrases("这两篇在展望上有什么不同")[0] == "relation"
    assert match_full_phrases("A5 里 VM 和沙箱是什么关系")[0] == "relation"
    assert match_full_phrases("作者承认了哪些不足")[0] == "enum"


def test_l1_keeps_only_the_all_side():
    """ORDER 侧已整侧删除：这些词一个都不该命中。"""
    for q in (
        "都怎么做的",
        "都是如何做的",
        "主要原因是什么",
        "比较重要的原因是什么",
        "具体有哪些做法",          # "有哪些" 应命中，但 "具体" 不该单独成为依据
    ):
        hits = match_full_phrases(q)
        words = hits[1] if hits else []
        assert "怎么" not in words and "如何" not in words
        assert "主要" not in words and "比较" not in words and "具体" not in words
    assert match_full_phrases("都怎么做的") is None      # 漏判可接受：降级到 L2


def test_l1_phrase_table_has_no_known_risky_words():
    """护栏：单个词表项不得是已知会出现在反类问句里的词。"""
    risky = {"怎么", "如何", "主要", "比较", "具体", "区别", "不同", "差别", "关系", "是"}
    assert not (set(ALL_FULL_PHRASES) & risky)
    assert set(FULL_PHRASES) == {"enum", "relation"}


# ---------------------------------------------------------------- L2


def test_l2_centroid_picks_nearest_class_and_reports_margin():
    """三分类：三个形状各自能被正确挑出。"""
    router = three_way_router()
    label, margin, evidence = router.classify(np.array(unit(1.0, 0.05, 0.05), dtype=np.float32))
    assert label == "point"
    assert margin > 0.9
    assert evidence in {"p1", "p2"}

    label2, margin2, _ = router.classify(np.array(unit(0.05, 1.0, 0.05), dtype=np.float32))
    assert label2 == "line" and margin2 > 0.9

    label3, margin3, _ = router.classify(np.array(unit(0.05, 0.05, 1.0), dtype=np.float32))
    assert label3 == "area" and margin3 > 0.9


def test_l2_ambiguous_query_has_near_zero_margin():
    label, margin, _ = three_way_router().classify(np.array(unit(1.0, 1.0, 1.0), dtype=np.float32))
    assert abs(margin) < 0.01        # 三可 → 交给阈值判定要不要降级


# ---------------------------------------------------------------- 级联


def test_cascade_l1_short_circuits():
    d = decide_route("A5 这篇文章有哪些局限？")
    assert (d.route, d.decided_by) == ("graph", "lexicon")
    assert d.need == "enum"
    assert d.shape == "area"
    assert "哪些" in d.evidence
    assert d.needs_review is False


def test_l2_is_out_of_the_pipeline():
    """L2 已裁出流水线（2026-09-21）：即便传了 embedder 这类旧参数也不该再出现 semantic 判定。

    保留这条是为了防止有人顺手把 L2 塞回去而没人注意——它当时在真实样本上一条都没判出来。
    """
    import inspect

    sig = inspect.signature(decide_route)
    assert "embedder" not in sig.parameters and "router" not in sig.parameters


def test_cascade_without_llm_goes_vector_but_no_review(monkeypatch):
    """L3 没开时落向量**不**进台账——否则开关一关，"产品增强清单"就被灌满无关条目。
    这类"为什么落向量"的信息由分布日志（record_distribution）承载。"""
    monkeypatch.delenv("ROUTE_DECISION_LLM", raising=False)
    d = decide_route("嗯这个吧")
    assert (d.route, d.decided_by) == ("vector", "default")
    assert d.need is None
    assert d.needs_review is False
    assert "llm:unavailable" in d.degraded


def test_cascade_llm_on_by_default_and_can_be_disabled(monkeypatch):
    """L3 默认开（判定接成闸门后，没有 L3 闸门就等于没接）；可用 env 关掉。"""
    monkeypatch.delenv("ROUTE_DECISION_LLM", raising=False)
    called: list[int] = []
    d_on = decide_route("嗯这个吧",
                        judge=lambda s, u: called.append(1) or {"need": "area", "reason": "x"})
    assert (d_on.route, d_on.need) == ("graph", "enum")
    assert called == [1]                            # 默认就该调用

    monkeypatch.setenv("ROUTE_DECISION_LLM", "0")
    called = []
    d = decide_route("嗯这个吧",
                     judge=lambda s, u: called.append(1) or {"need": "area", "reason": "x"})
    assert "llm:disabled" in d.degraded
    assert called == []               # 没开开关就不该调用


def test_cascade_llm_classes(monkeypatch):
    monkeypatch.setenv("ROUTE_DECISION_LLM", "1")
    kw = dict()

    # L3 判的是"形状"：line → relation（图）、area → enum（图）、point → vector
    for shape, need, route in (("line", "relation", "graph"),
                               ("area", "enum", "graph")):
        d = decide_route("嗯这个吧", judge=lambda s, u, n=shape: {"need": n, "reason": "x"}, **kw)
        assert (d.route, d.decided_by, d.need, d.shape) == (route, "llm", need, shape)
        assert d.needs_review is False

    d_vec = decide_route("嗯这个吧", judge=lambda s, u: {"need": "point", "reason": "要一个解释"}, **kw)
    assert (d_vec.route, d_vec.need, d_vec.shape) == ("vector", "vector", "point")
    assert d_vec.needs_review is False

    d_unsure = decide_route("嗯这个吧", judge=lambda s, u: {"need": "unsure", "reason": "两可"}, **kw)
    assert (d_unsure.route, d_unsure.decided_by) == ("vector", "default")
    assert d_unsure.need == "unsure"
    assert d_unsure.needs_review is True
    assert "llm:unsure" in d_unsure.degraded


def test_cascade_llm_error_falls_back_to_vector(monkeypatch):
    monkeypatch.setenv("ROUTE_DECISION_LLM", "1")

    def boom(system, user):
        raise RuntimeError("超时")

    d = decide_route("嗯这个吧", judge=boom)
    assert (d.route, d.decided_by) == ("vector", "default")
    assert any(g.startswith("llm:error") for g in d.degraded)
    assert d.needs_review is True          # L3 出错也要留痕，别静默


def test_cascade_never_raises_and_always_answers(monkeypatch):
    """不变式①：judge 抛异常也必须给出结果（default → 向量）。"""
    monkeypatch.setenv("ROUTE_DECISION_LLM", "1")
    d = decide_route("随便什么", judge=lambda s, u: 1 / 0)
    assert isinstance(d, RouteDecision)
    assert d.route == "vector" and d.decided_by == "default"
    assert any(g.startswith("llm:error") for g in d.degraded)        # 异常留痕在 degraded
    assert d.needs_review is True                                    # L3 出错要留痕，别静默


# ---------------------------------------------------------------- 待人工看的日志


def test_review_log_written_only_when_needed(tmp_path):
    path = tmp_path / "review.jsonl"
    record_for_review("q1", RouteDecision(route="vector", decided_by="semantic"), path=path)
    assert not path.exists()          # 判得出来就不记

    record_for_review("q2", RouteDecision(route="vector", decided_by="default",
                                          degraded=["llm:unsure"], needs_review=True), path=path)
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
    assert len(rows) == 1 and rows[0]["query"] == "q2"
    assert rows[0]["degraded"] == ["llm:unsure"]


def test_distribution_log_records_every_decision(tmp_path):
    from app.rag.route_decision import record_distribution

    path = tmp_path / "log.jsonl"
    record_distribution("q1", RouteDecision(route="graph", decided_by="lexicon", need="enum"), path=path)
    record_distribution("q2", RouteDecision(route="vector", decided_by="default",
                                            need="unsure", needs_review=True), path=path)
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
    assert [r["need"] for r in rows] == ["enum", "unsure"]
    assert rows[0]["query"] == "q1"

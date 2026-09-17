"""S5 图组装的单测（2026-09-17 重构后的形态：**LLM 只规范端点，组图由程序做**）。

不变式：
  ① **端点取值**：S4.5 已解的（`*_resolved`）直接用、**不再送 LLM**；指代未解/光杆代词 → 端点不可用；
     非指代的原文短语 → 送 LLM 规范化（长端点也不在入口砍）。
  ② **组图是确定性的**：一条 claim 至多一条边；边必须带 claim_idx；实体表 = 边的端点集合（**不会有孤立实体**）。
  ③ **被动翻转**：有两端且表语+过去分词+显式 by（非状态族）→ 建边时交换两端；一元断言不翻。
  ④ **对账完整**：每条 claim 必落在「建边 / 丢弃（带原因）/ 谓词待定」之一。
"""

from app.compile.assemble import (
    assemble_graph,
    build_targets,
    endpoint_plan,
    should_flip,
    valid_entity_name,
)

TEXT_WORDS = {"engineer", "codex", "defender", "critical", "system", "tooling", "containment", "a", "of", "the"}


def _claim(subject, predicate="acts_on", obj="", **kw):
    c = {"subject": subject, "predicate": predicate, "object": obj, "predicate_normalized": predicate}
    c.update(kw)
    return c


# ---------- ① 端点取值 ----------

def test_endpoint_plan_three_kinds():
    # S4.5 已解 → 直接用，不必送 LLM
    assert endpoint_plan({"subject": "they", "subject_resolved": "credentials", "subject_resolution_status": "resolved"}, "subject")["kind"] == "resolved"
    # 指代未解 / 形式主语 → 端点不可用
    assert endpoint_plan({"subject": "this", "subject_resolution_status": "unresolved"}, "subject")["kind"] == "unusable"
    assert endpoint_plan({"subject": "there", "subject_resolution_status": "not_anaphora"}, "subject")["kind"] == "unusable"
    # 非指代原文短语 → 送 LLM 规范化（**长端点不在这里砍**）
    long_obj = "the tools, abstractions, and internal structure required to make progress"
    assert endpoint_plan({"object": long_obj}, "object") == {"kind": "normalize", "text": long_obj}
    assert endpoint_plan({"object": ""}, "object")["kind"] == "empty"


def test_build_targets_skips_pending_and_resolved():
    claims = [
        _claim("defenders"),                                                          # 需规范化
        _claim("they", subject_resolved="credentials", subject_resolution_status="resolved"),  # 已解 → 不送
        _claim("x", predicate_status="pending"),                                      # 谓词待定 → 不送
        _claim("this", subject_resolution_status="unresolved"),                       # 不可用 → 不送
    ]
    assert [t["idx"] for t in build_targets(claims)] == ["0:subject"]


# ---------- ② 确定性建边 + ④ 对账 ----------

def test_assemble_builds_one_edge_per_claim_and_no_isolated_entities():
    claims = [_claim("defenders", obj="critical systems"), _claim("x", obj="y")]
    g = assemble_graph(
        claims,
        {"0:subject": "defender", "0:object": "critical system", "1:subject": None, "1:object": None},
        text_words=TEXT_WORDS,
        resolved_names=set(),
        claim_chunk={0: "A5:3"},
    )
    assert len(g["edges"]) == 1                                   # 一条 claim 至多一条边
    e = g["edges"][0]
    assert (e["from"], e["predicate"], e["to"], e["claim_idx"], e["chunk_id"]) == (
        "defender", "acts_on", "critical system", 0, "A5:3")
    # 实体表 = 边的端点集合 → 不会有孤立实体
    assert {x["name"] for x in g["entities"]} == {"defender", "critical system"}
    # 对账：建边 1 + 丢弃 1（该端点取不出概念）
    assert [a["status"] for a in g["audit"]] == ["edge", "skipped"]
    assert "取不出可点名概念" in g["audit"][1]["reason"]


def test_assemble_rejects_name_not_grounded_and_keeps_audit():
    claims = [_claim("defenders", obj="critical systems")]
    g = assemble_graph(
        claims,
        {"0:subject": "quantum flux capacitor", "0:object": "critical system"},
        text_words=TEXT_WORDS,
        resolved_names=set(),
        claim_chunk={},
    )
    assert g["edges"] == []
    assert "疑似自造" in g["audit"][0]["reason"]


def test_assemble_unary_claim_keeps_to_empty():
    claims = [_claim("defenders", obj="")]
    g = assemble_graph(claims, {"0:subject": "defender"}, text_words=TEXT_WORDS, resolved_names=set(), claim_chunk={})
    assert g["edges"][0]["to"] == ""
    assert g["audit"][0]["status"] == "edge"


# ---------- ③ 被动翻转 ----------

def test_passive_flip_swaps_endpoints_only_with_two_ends():
    claims = [_claim("application logic", predicate="creates", obj="Codex",
                     predicate_surface="has been written by")]
    g = assemble_graph(
        claims,
        {"0:subject": "application logic", "0:object": "Codex"},
        text_words={"application", "logic", "codex"},
        resolved_names=set(),
        claim_chunk={},
    )
    e = g["edges"][0]
    assert (e["from"], e["to"]) == ("Codex", "application logic")   # 方向被翻正
    assert e["passive_flipped"] is True
    # 一元断言不翻
    assert should_flip({"predicate_surface": "has been written by", "predicate_normalized": "creates"}) is True
    assert should_flip({"predicate_surface": "is located in", "predicate_normalized": "located_in"}) is False


def test_valid_entity_name_rules():
    assert valid_entity_name("credentials")[0] is True
    assert valid_entity_name("this")[0] is False
    assert valid_entity_name("how to optimize the query")[0] is False
    assert valid_entity_name("an internal beta of a software product with more words")[0] is False

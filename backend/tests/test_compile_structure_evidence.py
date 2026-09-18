"""S6-B 结构证据（确定性部分）单测：shared / conflict / veto_candidate 的口径。

钉住三条（来自 2026-09-17 的复测结论）：
  · 真同指对（两个名字**各自连不同的东西**）→ shared 0（结构信号抓不到别名，故不能当候选源）
  · 同类不同实例（同一谓词 + 同一端点）→ shared ≥1（这是"同角色"不是"同指"）
  · 否决倾向只给"两边都有规模、且同谓词下指向不同端点"的情形（1 条边的实体不参与否决）
"""

from __future__ import annotations

from app.compile.merge_semantic import structure_evidence


def _graph():
    ents = [
        {"id": "e1", "name": "VM", "aliases": []},
        {"id": "e2", "name": "virtual machine", "aliases": []},
        {"id": "e3", "name": "MCP server", "aliases": []},
        {"id": "e4", "name": "web search tool", "aliases": []},
        {"id": "e5", "name": "hub a", "aliases": []},
        {"id": "e6", "name": "hub b", "aliases": []},
        {"id": "e7", "name": "obj x", "aliases": []},
        {"id": "e8", "name": "obj y", "aliases": []},
    ]
    edges = [
        # VM 与 virtual machine：各连不同的东西 → shared 0（真同指，却抓不到）
        {"from": "e1", "predicate": "has_part", "to": "e7"},
        {"from": "e2", "predicate": "runs_in", "to": "e8"},
        # MCP server 与 web search tool：都 provides → content（同角色）
        {"from": "e3", "predicate": "provides", "to": "e7"},
        {"from": "e4", "predicate": "provides", "to": "e7"},
        # hub a / hub b：同谓词指向不同端点，且两边度数足够 → 否决倾向
        {"from": "e5", "predicate": "acts_on", "to": "e7"},
        {"from": "e5", "predicate": "creates", "to": "e8"},
        {"from": "e5", "predicate": "uses", "to": "e7"},
        {"from": "e6", "predicate": "acts_on", "to": "e8"},
        {"from": "e6", "predicate": "creates", "to": "e7"},
        {"from": "e6", "predicate": "blocks", "to": "e7"},
    ]
    return ents, edges


def test_true_alias_pair_shares_nothing():
    ents, edges = _graph()
    ev = structure_evidence(ents, edges, [("VM", "virtual machine")])
    item = ev[("VM", "virtual machine")]
    assert item["shared"] == 0, "别名分裂时边也跟着分裂——结构信号抓不到"
    assert item["veto_candidate"] is False


def test_same_role_different_instance_shares_but_is_not_same_referent():
    ents, edges = _graph()
    ev = structure_evidence(ents, edges, [("MCP server", "web search tool")])
    item = ev[("MCP server", "web search tool")]
    assert item["shared"] == 1
    # 只有 1 条边 → 不构成否决（噪声太多）
    assert item["veto_candidate"] is False


def test_veto_candidate_needs_scale_and_conflicts():
    ents, edges = _graph()
    ev = structure_evidence(ents, edges, [("hub a", "hub b")])
    item = ev[("hub a", "hub b")]
    assert item["conflict"] >= 2
    assert min(item["degree_a"], item["degree_b"]) >= 3
    assert item["veto_candidate"] is True


def test_alias_names_resolve_to_same_id():
    """别名（S6-A 合并进来的写法）要能解析到同一实体 id，证据才不漏。"""
    ents = [{"id": "e1", "name": "agent", "aliases": ["agents", "an agent"]},
            {"id": "e2", "name": "file", "aliases": []}]
    edges = [{"from": "e1", "predicate": "acts_on", "to": "e2"}]
    ev = structure_evidence(ents, edges, [("agents", "an agent")])
    # 两者其实是同一个 id → 轮廓完全相同（shared 计算用的是同一个 profile）
    assert ev[("agents", "an agent")]["degree_a"] == ev[("agents", "an agent")]["degree_b"] == 1

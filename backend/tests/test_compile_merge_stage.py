"""S6-A 接线单测：确定性形合并的**闭环不变式**（不调 LLM）。

钉住四条（对应闭环检查器第 ⑩ 项）：
  ① 边不丢（只允许丢自环）
  ② 原名可回溯（被合并掉的写法仍在 canonical / aliases 里）
  ③ 实体表＝边端点集合（无悬挂、无孤立）
  ④ 无别名冲突
外加：单形不改写（改名＝造名）、L2 出处不共现时进待抽检清单。
"""

from __future__ import annotations

from app.compile.merge import alias_collisions, merge_stage


def _graph():
    """模仿 S5 产物：entities 用名字、edges 的 from/to 用名字（含一元边）。"""
    return {
        "entities": [
            {"name": "the sandbox", "type": "concept", "aliases": []},
            {"name": "sandbox", "type": "concept", "aliases": []},          # 冠词差异 → 合并
            {"name": "credential", "type": "concept", "aliases": []},
            {"name": "credentials", "type": "concept", "aliases": []},      # 单复数 → 合并
            {"name": "VM", "type": "concept", "aliases": []},               # 单形：不该被改写
            {"name": "means", "type": "concept", "aliases": []},            # 不折叠清单：不该变 mean
        ],
        "edges": [
            {"from": "the sandbox", "predicate": "runs_in", "to": "VM", "claim_idx": 0, "chunk_id": "A5:0"},
            {"from": "sandbox", "predicate": "has_property", "to": "", "claim_idx": 1, "chunk_id": "A5:0"},
            {"from": "credential", "predicate": "located_in", "to": "VM", "claim_idx": 2, "chunk_id": "A5:1"},
            {"from": "credentials", "predicate": "acts_on", "to": "means", "claim_idx": 3, "chunk_id": "A5:1"},
        ],
        "skipped": [{"claim_idx": 9, "reason": "object端取不出可点名概念"}],
        "audit": [{"claim_idx": 0, "status": "edge", "reason": ""}],
        "chunk_sections": {"A5:0": "sec0", "A5:1": "sec1"},
        "stats": {"claims_in": 4, "edges": 4},
    }


def test_merge_stage_closed_loop_invariants():
    g = _graph()
    # 不给 clean_t/chunks → L2 出处为 unknown（不阻断合并）
    out = merge_stage(g)

    ents, edges = out["entities"], out["edges"]

    # ① 边不丢（这里无自环，条数应完全一致）
    assert len(edges) == len(g["edges"])
    assert out["stats"]["self_loops_dropped"] == 0

    # ② 原名可回溯
    reachable = set()
    for e in ents:
        reachable.add(e["name"])
        reachable.update(e.get("aliases") or [])
    for m in out["merge_log"]:
        for v in m["variants"]:
            assert v in reachable, v

    # ③ 实体表＝边端点集合
    eids = {e["id"] for e in ents}
    used = {x for ed in edges for x in (ed.get("from"), ed.get("to")) if x}
    assert used <= eids, "边端点必须是实体 id"
    assert eids == used, "不该有孤立实体"

    # ④ 无别名冲突
    assert alias_collisions(ents) == []

    # 合并结果本身：两组合并、canonical 选了无冠词/单数形
    names = {e["name"]: e for e in ents}
    assert "sandbox" in names and "the sandbox" in names["sandbox"]["aliases"]
    assert "credential" in names and "credentials" in names["credential"]["aliases"]
    assert out["stats"]["form_merged_groups"] == 2

    # 单形不改写：VM / means 原样保留
    assert "VM" in names and "means" in names
    assert "mean" not in names

    # S5 侧记账被带过来（未建边清单 / 逐 claim 对账 / S5 统计）
    assert out["skipped"] == g["skipped"]
    assert out["claim_audit"] == g["audit"]
    assert out["graph_stats"] == g["stats"]


def test_merge_stage_self_loop_and_provenance():
    """合并后出现的自环要被丢弃并记账；无边实体一并记账移除；出处不共现 → 低置信进待抽检。"""
    g = {
        "entities": [
            {"name": "agent", "type": "concept", "aliases": []},
            {"name": "agents", "type": "concept", "aliases": []},
        ],
        "edges": [
            {"from": "agent", "predicate": "supports", "to": "agents", "claim_idx": 0, "chunk_id": "X:0"},
        ],
    }
    # chunk 0 只出现 "agent"、chunk 1 只出现 "agents" → 出处不共现 = low
    clean_t = "agent does things\nagents do things"
    chunks = [
        {"chunk_id": "X:0", "start_pos": 0, "end_pos": 18},
        {"chunk_id": "X:1", "start_pos": 19, "end_pos": 34},
    ]
    out = merge_stage(g, clean_t, chunks)
    assert out["stats"]["self_loops_dropped"] == 1
    assert out["edges"] == []
    # 两边是同一实体 → 合并后它没有任何边 → 按"实体表＝边端点集合"移除，但记账
    assert out["stats"]["entities_out"] == 0
    assert [x["name"] for x in out["isolated_dropped"]] == ["agent"]
    assert out["stats"]["low_confidence_merges"] == 1
    assert out["review_log"], "低置信合并必须进待抽检清单"

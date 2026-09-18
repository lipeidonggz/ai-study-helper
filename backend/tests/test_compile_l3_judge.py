"""S6-B 判定侧单测（不调真 LLM）：提示词输入组装 + 逐字依据护栏 + 保守降级。"""

from __future__ import annotations

import json

from app.compile.merge_semantic import build_l3_user, parse_l3, split_by_veto

ENTITIES = [
    {"id": "e1", "name": "VM", "aliases": []},
    {"id": "e2", "name": "virtual machine", "aliases": []},
]
EDGES = [
    {"from": "e1", "predicate": "has_part", "to": "e2", "claim_idx": 0},
]
CLAIMS = [{"evidence_texts": ["The VM has its own Linux kernel."]}]


def test_l3_input_renders_names_not_ids():
    text, pools = build_l3_user(ENTITIES, EDGES, [("VM", "virtual machine")], CLAIMS, {})
    assert "ent0" not in text, "输入里不该出现实体 id（对 LLM 无意义）"
    assert "VM —has_part→ virtual machine" in text, "边必须渲染成名字、方向固定 from→to"
    assert pools[0] == ["The VM has its own Linux kernel."], "引文池用于护栏"


def test_structure_evidence_is_not_in_prompt():
    """结构证据不进提示词：确定性的事由程序做（2026-09-18 定）。"""
    ev = {("VM", "virtual machine"): {"shared": 3, "conflict": 4, "degree_a": 5, "degree_b": 4,
                                      "veto_candidate": True, "signals": ["acronym"]}}
    text, _pools = build_l3_user(ENTITIES, EDGES, [("VM", "virtual machine")], CLAIMS, ev)
    assert "结构证据" not in text and "冲突" not in text and "shared" not in text


def test_veto_is_deterministic_and_never_sent_to_llm():
    pairs = [("hub a", "hub b"), ("VM", "virtual machine")]
    ev = {
        ("hub a", "hub b"): {"conflict": 3, "degree_a": 5, "degree_b": 4, "veto_candidate": True},
        ("VM", "virtual machine"): {"conflict": 0, "degree_a": 5, "degree_b": 1, "veto_candidate": False},
    }
    keep, vetoed = split_by_veto(pairs, ev)
    assert keep == [1], "只把未被否决的对送 LLM"
    assert len(vetoed) == 1 and vetoed[0]["by"] == "structure_veto"
    assert vetoed[0]["verdict"] == "different" and vetoed[0]["id"] == 0


def test_same_without_verbatim_evidence_is_demoted_to_different():
    raw = json.dumps({"results": [{"id": 0, "verdict": "same", "reason": "看起来是同一个",
                                   "evidence": "This sentence does not exist."}]}, ensure_ascii=False)
    results, demoted = parse_l3(raw, {0: ["The VM has its own Linux kernel."]})
    assert results[0]["verdict"] == "different", "依据不逐字 → 必须降级（错合比漏合危险）"
    assert demoted and demoted[0]["demoted_from"] == "same"


def test_same_with_verbatim_evidence_passes():
    raw = json.dumps({"results": [{"id": 0, "verdict": "same", "reason": "缩写与全称",
                                   "evidence": "The VM has its own Linux kernel.",
                                   "canonical": "virtual machine"}]}, ensure_ascii=False)
    results, demoted = parse_l3(raw, {0: ["The VM has its own Linux kernel."]})
    assert results[0]["verdict"] == "same" and not demoted
    assert results[0]["canonical"] == "virtual machine"


def test_illegal_verdict_falls_back_to_different():
    raw = json.dumps({"results": [{"id": 0, "verdict": "maybe", "evidence": "The VM has its own Linux kernel."}]},
                     ensure_ascii=False)
    results, _ = parse_l3(raw, {0: ["The VM has its own Linux kernel."]})
    assert results[0]["verdict"] == "different"

"""S7-1 / S7-2 单测：statement 组装与归属边（程序，确定性）。

钉住四条不变式：
  ① **一一对应**：每条 claim 恰好一个 statement（无丢、无增）
  ② **可锚**：有 chunk 定位的 statement 必带 evidence_chunks，且生成 groundedIn 边
  ③ **归属**：每个 statement 都有 doc —asserts→；有概念端点的另有 concept —hasStatement→
  ④ **未建边的 claim 也建 statement**（挂 doc、about 为空）——"全量"指全部 claim 而不是全部边
"""

from __future__ import annotations

from app.compile.classify import build_statements

CLAIMS = [
    {"subject": "the sandbox", "predicate": "runs", "object": "code",
     "predicate_normalized": "runs_in", "evidence_texts": ["The sandbox runs code."]},
    {"subject": "attacks", "predicate": "are blocked", "object": "",
     "predicate_normalized": "blocks", "evidence_texts": ["Attacks are blocked."]},
    {"subject": "this", "predicate": "is", "object": "important",
     "predicate_normalized": "is", "evidence_texts": ["This is important."],
     "subject_resolution_status": "unresolved"},
]

MERGED = {
    "entities": [
        {"id": "ent0001", "name": "sandbox", "type": "concept", "aliases": []},
        {"id": "ent0002", "name": "code", "type": "concept", "aliases": []},
        {"id": "ent0003", "name": "attack", "type": "concept", "aliases": []},
    ],
    "edges": [
        {"from": "ent0001", "predicate": "runs_in", "to": "ent0002", "claim_idx": 0, "chunk_id": "A5:0"},
        {"from": "ent0003", "predicate": "blocks", "to": "", "claim_idx": 1, "chunk_id": "A5:1"},
        # claim 2 未建边（指代未解）→ 仍应有 statement
    ],
}

CLEAN = "The sandbox runs code.\nAttacks are blocked."
CHUNKS = [
    {"chunk_id": "A5:0", "start_pos": 0, "end_pos": 22},
    {"chunk_id": "A5:1", "start_pos": 23, "end_pos": 42},
]


def test_claim_statement_one_to_one():
    out = build_statements(CLAIMS, MERGED, CLEAN, CHUNKS, source_id="A5")
    assert len(out["statements"]) == len(CLAIMS) == out["stats"]["statements"]
    assert [s["claim_idx"] for s in out["statements"]] == [0, 1, 2]


def test_statement_grounding_and_attribution_edges():
    out = build_statements(CLAIMS, MERGED, CLEAN, CHUNKS, source_id="A5")
    st0 = out["statements"][0]
    assert st0["subject"] == "ent0001" and st0["object"] == "ent0002"
    assert st0["about"] == ["ent0001", "ent0002"]
    assert st0["asserted_by"] == "doc:A5"
    preds = {(e["predicate"]) for e in out["edges"]}
    assert {"asserts", "hasStatement", "groundedIn"} <= preds
    # 每个 statement 都必须有 doc→asserts→它
    asserts = {e["to"] for e in out["edges"] if e["predicate"] == "asserts"}
    assert asserts == {s["id"] for s in out["statements"]}


def test_unlinked_claim_still_becomes_statement():
    """未建边的 claim（无概念端点）→ statement 挂在 doc 上、about 为空。"""
    out = build_statements(CLAIMS, MERGED, CLEAN, CHUNKS, source_id="A5")
    st2 = out["statements"][2]
    assert st2["about"] == [] and st2["has_edge"] is False
    assert st2["asserted_by"] == "doc:A5"
    assert out["stats"]["doc_only"] == 1
    assert out["stats"]["with_edge"] == 2


# ---------- S7-3：claim_type 判定（提示词输入 + 逐字依据护栏） ----------

def test_claim_type_input_shape_and_pool():
    from app.compile.classify import build_claim_type_user

    out = build_statements(CLAIMS, MERGED, CLEAN, CHUNKS, source_id="A5")
    text, pools = build_claim_type_user(out["statements"], CLAIMS)
    assert '"id":"st0001"' in text and '"evidence":"The sandbox runs code."' in text
    assert pools["st0001"] == ["The sandbox runs code."]


def test_claim_type_demotes_when_evidence_not_verbatim():
    from app.compile.classify import parse_claim_type

    # 注意：引文池缺某个 id（或模型回了没问过的 id）→ 无法校验 → 一律置 null（安全默认）
    pools = {"st0001": ["The sandbox runs code."],
             "st0002": ["The sandbox runs code."],
             "st0003": ["The sandbox runs code."]}
    res, demoted = parse_claim_type({"results": [
        {"id": "st0001", "claim_type": "LimitationStatement", "reason": "看得出是局限",
         "evidence": "这句原文里没有"},
        {"id": "st0002", "claim_type": "OutlookStatement", "reason": "计划",
         "evidence": "The sandbox runs code."},
        {"id": "st0003", "claim_type": "Whatever", "reason": "非法类型", "evidence": "The sandbox runs code."},
    ]}, pools)
    assert res[0]["claim_type"] is None and demoted and demoted[0]["demoted_from"] == "LimitationStatement"
    assert res[1]["claim_type"] == "OutlookStatement"          # 依据逐字 → 通过
    assert res[2]["claim_type"] is None                        # 非法取值 → null


# ---------- S8：逐段引文锚定（clean_ref + 多对多 evidence_chunks） ----------

def test_s8_anchors_segments_and_unions_chunks():
    """两段引文落在两个不同 chunk → evidence_chunks 取**并集**（C5 的"声明组"口径）。"""
    claims = [{"subject": "sandbox", "predicate": "runs", "object": "code",
               "predicate_normalized": "runs_in",
               "evidence_texts": ["The sandbox runs code.", "Attacks are blocked."]}]
    merged = {"entities": [{"id": "ent0001", "name": "sandbox", "type": "concept", "aliases": []}],
              "edges": [{"from": "ent0001", "predicate": "runs_in", "to": "", "claim_idx": 0}]}
    out = build_statements(claims, merged, CLEAN, CHUNKS, source_id="A5")
    st = out["statements"][0]
    assert st["evidence_chunks"] == ["A5:0", "A5:1"]
    assert st["clean_ref"]["start_pos"] == 0 and st["clean_ref"]["end_pos"] == 43   # 两段并集的外沿
    assert out["stats"]["multi_chunk"] == 1
    assert out["stats"]["section_level"] == 0


def test_s8_degrades_to_section_when_quote_not_found():
    """引文定位不到（编的）→ 降级 section 弱锚、evidence_chunks 为空，并记账（不静默）。"""
    claims = [{"subject": "sandbox", "predicate": "runs", "object": "code",
               "predicate_normalized": "runs_in",
               "evidence_texts": ["This sentence is not in the source."]}]
    merged = {"entities": [], "edges": []}
    out = build_statements(claims, merged, CLEAN, CHUNKS, source_id="A5")
    st = out["statements"][0]
    assert st["anchor"] == "section" and st["evidence_chunks"] == []
    assert "未定位到任何分段" in st.get("a4_note", "")
    assert out["stats"]["section_level"] == 1

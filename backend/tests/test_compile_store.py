"""S9 落库单测：schema / 幂等导入 / 按角色查询（第 3 步最小闭环的底座）。"""

from __future__ import annotations

import json

from app.storage.sqlite.compile_store import CompileStore

PAYLOAD = {
    "statements": [
        {"id": "st0001", "claim_idx": 0, "subject": "ent1", "object": "ent2", "predicate": "runs_in",
         "predicate_surface": "runs", "claim_type": "LimitationStatement", "roles": None, "polarity": None,
         "clean_ref": {"start_pos": 0, "end_pos": 22}, "anchor": None,
         "evidence_chunks": ["A5:0"], "evidence_texts": ["The sandbox runs code."],
         "asserted_by": "doc:A5"},
        {"id": "st0002", "claim_idx": 1, "subject": None, "object": None, "predicate": "blocks",
         "predicate_surface": "are blocked", "claim_type": None, "roles": None, "polarity": None,
         "clean_ref": {"start_pos": 23, "end_pos": 43}, "anchor": None,
         "evidence_chunks": ["A5:1"], "evidence_texts": ["Attacks are blocked."],
         "asserted_by": "doc:A5"},
    ],
    "edges": [
        {"from": "doc:A5", "predicate": "asserts", "to": "st0001", "produced_by": "compile"},
        {"from": "doc:A5", "predicate": "asserts", "to": "st0002", "produced_by": "compile"},
        {"from": "st0001", "predicate": "grounded_in", "to": "chunk:A5:0", "produced_by": "auto"},
        {"from": "st0002", "predicate": "grounded_in", "to": "chunk:A5:1", "produced_by": "auto"},
    ],
}

MERGED = {"entities": [
    {"id": "ent1", "name": "sandbox", "type": "concept", "aliases": ["the sandbox"]},
    {"id": "ent2", "name": "code", "type": "concept", "aliases": []},
]}


def _store(tmp_path) -> CompileStore:
    return CompileStore(tmp_path / "compile.db")


def test_import_counts_and_role_query(tmp_path):
    st = _store(tmp_path)
    counts = st.import_source(source_id="A5", statements_payload=PAYLOAD, merged=MERGED,
                              doc_meta={"title": "contain A5"})
    assert counts == {"statements": 2, "concepts": 2, "edges": 4}
    lim = st.role_statements("A5", "LimitationStatement")
    assert [s["id"] for s in lim] == ["st0001"]
    assert lim[0]["evidence_chunks"] == ["A5:0"] and lim[0]["clean_ref"]["end_pos"] == 22
    assert st.role_statements("A5", "OutlookStatement") == []


def test_import_is_idempotent(tmp_path):
    st = _store(tmp_path)
    st.import_source(source_id="A5", statements_payload=PAYLOAD, merged=MERGED)
    first = st.counts("A5")
    st.import_source(source_id="A5", statements_payload=PAYLOAD, merged=MERGED)   # 再导一次
    assert st.counts("A5") == first, "重复导入不该翻倍（按 source_id 先删后插）"


def test_concept_route_and_aliases(tmp_path):
    st = _store(tmp_path)
    st.import_source(source_id="A5", statements_payload=PAYLOAD, merged=MERGED)
    rows = st.statements_of_concept("ent1")
    assert [r["id"] for r in rows] == ["st0001"]
    cs = {c["id"]: c for c in st.concepts("A5")}
    assert cs["ent1"]["aliases"] == ["the sandbox"]
    assert json.loads(json.dumps(cs["ent2"]["aliases"])) == []

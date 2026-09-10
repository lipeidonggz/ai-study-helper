"""Pass 2-② 第一刀（形合并）单元测试：归一规则 + 必须不误合的反例探针。"""

from app.compile.merge import (
    alias_collisions,
    form_key,
    merge_entities,
    missed_plural_pairs,
)


def _ents(*names: str, type_: str = "concept") -> list[dict]:
    return [{"name": n, "type": type_, "aliases": []} for n in names]


# ---------- 形归一 key ----------

def test_form_key_normalizes_shape():
    assert form_key("agents") == "agent"
    assert form_key("Agent") == "agent"
    assert form_key("the sandbox") == "sandbox"
    assert form_key("read-only") == "read only"
    assert form_key("read only") == "read only"
    assert form_key("Claude Code's auto mode") == "claude code auto mode"
    assert form_key("Claude Code auto mode") == form_key("Claude Code's auto mode")
    assert form_key("filesystem boundaries") == "filesystem boundary"
    assert form_key("egress controls") == "egress control"


def test_form_key_plural_edge_cases():
    assert form_key("policies") == "policy"
    assert form_key("processes") == "process"
    assert form_key("sandboxes") == "sandbox"
    assert form_key("classes") == "class"
    assert form_key("cases") == "case"          # 不能折成 cas
    assert form_key("defenses") == "defense"    # 不能折成 defens
    assert form_key("responses") == "response"
    assert form_key("VMs") == "vm"
    assert form_key("APIs") == "api"


def test_form_key_keeps_singular_words_ending_in_s():
    """以 s 结尾但本身是单数的词不能被误折（否则 https→http 之类会错合）。"""
    for w in ["https", "tls", "dns", "aws", "ios", "access", "status", "analysis", "news", "ms", "fs"]:
        assert form_key(w) == w


def test_form_key_skips_paths_and_acronyms():
    """路径/文件名、全大写缩写不做单复数折叠（HCS 不是 HCs 的单数）。"""
    assert form_key("HCS") == "hcs"
    assert form_key("~/.aws/credentials").endswith("credentials")
    assert form_key(".claude/settings.json").endswith("settings.json")
    assert form_key("VMs") == "vm"      # 不算全大写 → 照常折
    assert form_key("APIs") == "api"


# ---------- 合并行为 ----------

def test_merge_plural_picks_shorter_canonical_and_keeps_aliases():
    out = merge_entities(_ents("agent", "agents", "agent"), [])
    assert [e["name"] for e in out["entities"]] == ["agent"]
    assert out["entities"][0]["aliases"] == ["agents"]
    assert out["audit"]["form_merged_groups"] == 1


def test_merge_case_and_hyphen_and_possessive():
    out = merge_entities(
        _ents("VM", "vms"),  # 大小写 + 单复数
        [],
    )
    assert [e["name"] for e in out["entities"]] == ["VM"]

    out = merge_entities(_ents("read-only", "read only"), [])
    assert [e["name"] for e in out["entities"]] == ["read-only"]

    out = merge_entities(_ents("Claude Code auto mode", "Claude Code's auto mode"), [])
    assert len(out["entities"]) == 1
    assert set(out["entities"][0]["aliases"]) == {"Claude Code's auto mode"}


def test_singleton_is_not_renamed_without_evidence():
    """只有一种写法时不做归一改名（无证据不改名）。"""
    out = merge_entities(_ents("agents"), [])
    assert [e["name"] for e in out["entities"]] == ["agents"]
    assert out["audit"]["form_merged_groups"] == 0


def test_edges_are_remapped_and_self_loops_dropped():
    entities = _ents("agent", "agents", "sandbox")
    edges = [
        {"from": "agents", "predicate": "runs in", "to": "sandbox", "claim_idx": 1},
        {"from": "agent", "predicate": "is a", "to": "agents", "claim_idx": 2},  # 合并后自环
    ]
    out = merge_entities(entities, edges)
    assert len(out["edges"]) == 1
    assert out["edges"][0]["from"] == out["edges"][0]["from"]
    assert out["edges"][0]["to"]
    assert out["audit"]["self_loops_dropped"] == 1
    assert out["audit"]["edges_out"] == 1


def test_roles_instrument_is_remapped():
    entities = _ents("agent", "tool permission", "tool permissions")
    edges = [{"from": "agent", "predicate": "uses", "to": "", "roles": {"instrument": ["tool permissions"]}}]
    out = merge_entities(entities, edges)
    canonical = next(e for e in out["entities"] if e["name"] == "tool permission")
    assert out["edges"][0]["roles"]["instrument"] == [canonical["id"]]


# ---------- 反例探针：看着像、但绝不能合并 ----------

PROBE_PAIRS = [
    ("Claude Code", "Claude Cowork"),
    ("CLAUDE.md", "Claude"),
    ("egress control", "egress proxy"),
    ("sandbox", "container"),
    ("NIST", "NCSC"),
    ("https", "http"),
    ("user", "user's machine"),
    ("model layer", "model"),
    ("VM", "VSock"),
]


def test_probe_pairs_must_not_merge():
    for a, b in PROBE_PAIRS:
        out = merge_entities(_ents(a, b), [])
        assert len(out["entities"]) == 2, f"{a} / {b} 被错误合并"


# ---------- 不规则复数 / 拉丁-希腊复数 ----------

def test_irregular_plurals_are_folded():
    """不以 s/es 规则变形的复数：靠显式映射合并。"""
    for plural, singular in [
        ("analyses", "analysis"),
        ("indices", "index"),
        ("matrices", "matrix"),
        ("theses", "thesis"),
        ("criteria", "criterion"),
        ("phenomena", "phenomenon"),
        ("people", "person"),
        ("children", "child"),
        ("formulae", "formula"),
    ]:
        assert form_key(plural) == form_key(singular), f"{plural} 未折到 {singular}"
        out = merge_entities(_ents(singular, plural), [])
        assert len(out["entities"]) == 1
        assert out["entities"][0]["name"] == singular  # 单数形更短，故选它


def test_ambiguous_irregulars_are_deliberately_not_merged():
    """有歧义的不规则复数（medium/media、datum/data）故意不收：宁漏合不误合。"""
    assert form_key("media") != form_key("medium")
    assert form_key("data") != form_key("datum")


def test_unchanged_plural_and_plural_that_is_another_noun():
    """单复数同形词 / 复数形本身是另一个名词：一律不折叠。"""
    for w in ["means", "species", "series", "sheep", "aircraft", "customs", "works", "physics"]:
        assert form_key(w) == w
    # mean（均值）与 means（手段）不是同一个东西 → 不能合
    assert len(merge_entities(_ents("mean", "means"), [])["entities"]) == 2
    # 但真正的单复数照常合
    assert len(merge_entities(_ents("aircraft carrier", "aircraft carriers"), [])["entities"]) == 1


# ---------- 审计工具（找漏合 / 找别名冲突）----------

def test_missed_plural_pairs_detects_unmerged_pair():
    # `local MCPs` 作为别名挂在两个实体上：实体名之间不算漏合（D0=0）
    entities = [
        {"id": "e1", "name": "local MCP", "aliases": ["local MCPs"]},
        {"id": "e2", "name": "local MCP server", "aliases": ["local MCP servers", "local MCPs"]},
    ]
    assert missed_plural_pairs(entities) == []
    assert ("local MCPs", ["e1", "e2"]) in alias_collisions(entities)


def test_missed_plural_pairs_reports_real_gap():
    merged = merge_entities(_ents("task"), [])["entities"]
    merged.append({"id": "e9", "name": "tasks", "aliases": []})  # 人为制造漏合
    assert ("task", "tasks") in missed_plural_pairs(merged)


# ---------- L2：出处共现 → 置信度（不否决合并）----------

def test_l2_confidence_high_when_forms_cooccur():
    prov = {"agent": {"c1", "c2"}, "agents": {"c2"}}
    res = merge_entities(_ents("agent", "agents"), [], provenance=prov)
    assert len(res["entities"]) == 1  # 照常合并
    assert res["entities"][0]["merge_confidence"] == "high"
    assert res["review_log"] == []


def test_l2_confidence_low_and_review_list_when_never_cooccur():
    prov = {"VM": {"c1", "c2"}, "VMs": {"c9"}}
    res = merge_entities(_ents("VM", "VMs"), [], provenance=prov)
    assert len(res["entities"]) == 1  # 仍然合并（不共现是弱证据，不当否决票）
    assert res["entities"][0]["merge_confidence"] == "low"
    assert res["review_log"][0]["canonical"] == "VM"
    assert res["audit"]["low_confidence_merges"] == 1


def test_l2_unknown_when_only_one_side_has_provenance():
    prov = {"credential": set(), "credentials": {"c3"}}
    res = merge_entities(_ents("credential", "credentials"), [], provenance=prov)
    assert res["entities"][0]["merge_confidence"] == "unknown"
    assert res["review_log"] == []  # 表面归一，不可疑


def test_occurrence_chunks_uses_word_boundary():
    text = "the agents run; an agent runs"
    chunks = [
        {"chunk_id": "c1", "start_pos": 0, "end_pos": 15},
        {"chunk_id": "c2", "start_pos": 15, "end_pos": len(text)},
    ]
    from app.compile.merge import occurrence_chunks

    assert occurrence_chunks("agents", text, chunks) == {"c1"}
    assert occurrence_chunks("agent", text, chunks) == {"c2"}  # 不会命中 "agents" 里的 agent

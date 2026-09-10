"""L3 义合并 P1（候选生成 + 括号规则）单元测试。"""

from app.compile.merge_semantic import (
    collect_candidates,
    is_acronym_of,
    normalize_brackets,
    split_bracket,
)


def _ents(*names: str) -> list[dict]:
    return [{"id": f"e{i}", "name": n, "type": "concept", "aliases": []} for i, n in enumerate(names)]


# ---------- 括号规则 ----------

def test_split_bracket():
    assert split_bracket("Sealed VM (Claude Cowork)") == ("Sealed VM", "Claude Cowork")
    assert split_bracket("no bracket") is None
    assert split_bracket("empty ()") is None


def test_bracket_rule_applies_only_when_outer_exists():
    # 外层也是实体 → 确定性归一（括号是限定语）
    ents = _ents("Sealed VM", "Sealed VM (Claude Cowork)")
    out, log = normalize_brackets(ents)
    assert [e["name"] for e in out] == ["Sealed VM", "Sealed VM"]
    assert out[1]["aliases"] == ["Sealed VM (Claude Cowork)"]
    assert log and log[0]["inner"] == "Claude Cowork"

    # 外层不存在 → 不动（无证据不改名）
    ents = _ents("Ephemeral container (claude.ai)")
    out, log = normalize_brackets(ents)
    assert out[0]["name"] == "Ephemeral container (claude.ai)"
    assert log == []


def test_bracket_never_merges_into_inner():
    """括号内是限定语，不是同指对象——`X (Y)` 不能与 Y 合并。"""
    ents = _ents("Claude Cowork", "Sealed VM (Claude Cowork)")
    out, log = normalize_brackets(ents)
    assert [e["name"] for e in out] == ["Claude Cowork", "Sealed VM (Claude Cowork)"]


# ---------- 缩写 ----------

def test_is_acronym_of():
    assert is_acronym_of("VM", "virtual machine")
    assert is_acronym_of("EDR", "endpoint detection and response")
    assert is_acronym_of("AI", "agent identity")      # 形似 → 是候选（正是要 LLM 判的反例）
    assert not is_acronym_of("VM", "vm")               # 全称必须多词
    assert not is_acronym_of("ssl", "secure sockets layer")  # 非全大写


# ---------- 分桶 ----------

def test_candidate_buckets():
    ents = _ents(
        "VM", "virtual machine",          # acronym → 合并候选
        "MCP", "MCP server",              # subset → 上下位桶（不进合并候选）
        "user oversight capacity", "user's capacity for oversight",  # rewrite → 合并候选
    )
    got = collect_candidates(ents, [], {})
    merge_pairs = {frozenset((c["a"], c["b"])) for c in got["merge"]}
    sub_pairs = {frozenset((c["a"], c["b"])) for c in got["subsumption"]}
    assert frozenset(("VM", "virtual machine")) in merge_pairs
    assert frozenset(("user oversight capacity", "user's capacity for oversight")) in merge_pairs
    assert frozenset(("MCP", "MCP server")) in sub_pairs
    assert frozenset(("MCP", "MCP server")) not in merge_pairs


def test_cooccurrence_is_only_a_note_not_a_candidate():
    """共现只作备注：两个实体同块出现 ≠ 同指，不能靠它生成候选。"""
    ents = _ents("sandbox", "container")
    prov = {"sandbox": {"c1"}, "container": {"c1"}}
    got = collect_candidates(ents, [], prov)
    assert got["merge"] == []
    assert got["subsumption"] == []

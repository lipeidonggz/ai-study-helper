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
    classify_name_tier,
    cross_endpoint_bleed,
    endpoint_plan,
    overlong_targets,
    parse_endpoint_reply,
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
    # S4.5 已解且合规 → 直接用，不必送 LLM
    assert endpoint_plan({"subject": "they", "subject_resolved": "credentials", "subject_resolution_status": "resolved"}, "subject")["kind"] == "resolved"
    # S4.5 已解但名字不合格（过长）→ **不直通**，转 LLM 压缩（raw = S4.5 的解）
    long_res = "supervising the agent only when it goes off track"
    plan = endpoint_plan({"subject": "this approach", "subject_resolved": long_res,
                          "subject_resolution_status": "resolved"}, "subject")
    assert plan["kind"] == "normalize" and plan["text"] == long_res and plan.get("from_resolved") is True
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


def test_assemble_refuses_self_loop_after_endpoint_normalization():
    """护栏③（兜底）：两端归一后同名 → 不建边（记 skipped）。

    用**两端都合法接地**的例子（`the sandbox` / `sandbox`）才能测到这条兜底——
    实测那条错案（`Every function reachable…` 的主语被取成 `attack surface`）现在会**更早**被四档判定拦下
    （见下面的 `test_bleed_case_is_now_rejected_by_tier`）。
    """
    claims = [_claim("the sandbox", predicate="is", obj="sandbox")]
    g = assemble_graph(
        claims,
        {"0:subject": "sandbox", "0:object": "sandbox"},
        text_words=TEXT_WORDS | {"sandbox"},
        resolved_names=set(),
        claim_chunk={},
    )
    assert g["edges"] == [], "两端同名不该建边（否则就是自环）"
    assert g["audit"][0]["status"] == "skipped"
    assert "两端归一后同名" in g["audit"][0]["reason"]
    assert g["entities"] == [], "没建边 → 不该留下实体"


def test_bleed_case_is_now_rejected_by_tier():
    """实测错案：主语被取成宾语的概念（`attack surface`）→ 四档判定的 D 档直接拒（比自环兜底更早）。"""
    claims = [_claim("Every function reachable through any domain on an allowlist",
                     predicate="is", obj="an attack surface")]
    g = assemble_graph(
        claims,
        {"0:subject": "attack surface", "0:object": "attack surface"},
        text_words=TEXT_WORDS | {"attack", "surface"},
        resolved_names=set(),
        claim_chunk={},
    )
    assert g["edges"] == []
    assert g["audit"][0]["status"] == "skipped"
    assert "疑似自造" in g["audit"][0]["reason"], "该端点在自己的 raw 里找不到 attack surface"


def test_cross_endpoint_bleed_detection():
    """串味检测：概念不在本端点 raw 里、却能在同一 claim 另一端 raw 里找到 → 拒。

    这是自环的**根因**（不是症状）：模型把宾语的概念安到了主语头上。
    与"合法的概括"要分开——概括（tooling ← tools, abstractions）在两端 raw 里都找不到，
    不该被这条判据误伤。
    """
    raw_subj = "Every function reachable through any domain on an allowlist"
    raw_obj = "an attack surface"
    assert cross_endpoint_bleed("attack surface", raw_subj, raw_obj) is True    # 借了宾语的词
    assert cross_endpoint_bleed("function", raw_subj, raw_obj) is False        # 本端点里的中心词
    # 概括：两端 raw 里都没有 → 不算串味（放行给文档级 grounding 判）
    assert cross_endpoint_bleed("tooling", "the tools and abstractions", "internal structure") is False


def test_name_grounded_folds_unicode_hyphens():
    """Unicode 标点归一：词内连字符→`-`，破折号→空格（两类不能混）。

    实测 bug（A5，两轮）：
      · 第一轮：`high‑utility`（U+2011）与 `high-utility`（ASCII）被判成两个词 → 名字与 raw 一字不差却"疑似自造"；
      · 第二轮（修第一轮时引入）：把 em dash 也折成 `-`，`environment—high-utility` 被粘成一个词，
        `utility` 从词表消失 → 同样误判。
    """
    from app.compile.assemble import _fold_text, _name_grounded

    assert _fold_text("high\u2011utility") == "high-utility"
    assert _fold_text("environment\u2014high-utility") == "environment high-utility", "破折号必须是分隔符"
    assert _fold_text("collaboration\u2014a") == "collaboration a"
    assert _name_grounded("high-utility capabilities", {"high-utility", "capabilities"})
    # 原文用 U+2011、名字用 ASCII（或反过来）都必须通过
    assert _name_grounded("high-utility", {"high\u2011utility"})
    assert _name_grounded("high\u2011utility", {"high-utility"})
    # 破折号相邻的词必须能被单独取出（否则词表里就没有 utility）
    assert _name_grounded("high-utility capabilities", {"environment", "high-utility", "capabilities"})


def test_parse_endpoint_reply_reads_derived_from():
    """`derived_from` 是结构化溯源字段（取代自由文本 note）：列表/字符串都认，上限 8。"""
    part = {"endpoints": [
        {"idx": "3:object", "concept": "containment", "derived_from": ["building", "containment"]},
        {"idx": "4:object", "concept": "access grant", "derived_from": "granting"},   # 单字符串也认
        {"idx": "5:object", "concept": "system prompt", "derived_from": []},
        {"idx": "6:object", "concept": None, "derived_from": ["whatever"]},
        {"idx": "7:object", "concept": "x", "derived_from": ["a", "b", "c", "d", "e", "f", "g", "h", "i", "j"]},
    ]}
    got, derived = parse_endpoint_reply(part)
    assert got["3:object"] == "containment" and got["6:object"] is None
    assert derived["3:object"] == ["building", "containment"]
    assert derived["4:object"] == ["granting"]
    assert derived["5:object"] == []
    assert len(derived["7:object"]) == 8, "上限 8 个，防跑飞"


# ---------- 四档判定（A 原文措辞 / B 词形派生 / C 语义概括 / D 拒） ----------

def test_tier_a_raw_wording():
    assert classify_name_tier("sandbox", "the sandbox") == "ok"
    assert classify_name_tier("permission prompt", "roughly 93% of permission prompts") == "ok"
    assert classify_name_tier("high-utility capabilities", "high-utility capabilities") == "ok"


def test_tier_b_morphological_derivation():
    assert classify_name_tier("auditability", "auditable") == "derived"
    assert classify_name_tier("structural similarity", "structurally similar") == "derived"
    # 混了"新语义词"（execution）就不算纯词形派生——按口径该走 C 档（有来源词）或 D 档
    assert classify_name_tier("uninterrupted execution", "largely without interruption") == "reject"
    assert classify_name_tier("uninterrupted execution", "largely without interruption",
                              ["largely without interruption"]) == "generalized"


def test_tier_c_generalization_with_source_words():
    """概括：概念是新词，但 derived_from 里的词逐词出自本端点 raw → 通过（进待抽检）。"""
    assert classify_name_tier("potential damage", "how much damage one could do",
                              ["how much damage one could do"]) == "generalized"
    assert classify_name_tier("content from uncontrolled sources",
                              "content into the agent’s context from sources you don’t control",
                              ["content", "sources you don't control"]) == "generalized"
    # 来源词对不上 → 不成立
    assert classify_name_tier("potential damage", "how much damage one could do",
                              ["totally unrelated words"]) == "reject"


def test_tier_d_rejects_invented_names():
    assert classify_name_tier("attack surface", "Every function reachable through any domain on an allowlist") == "reject"
    assert classify_name_tier("default denial", "by default") == "reject"
    assert classify_name_tier("auto-approval frequency", "roughly twice as often as new users") == "reject"
    assert classify_name_tier("auditability", "auditable", []) == "derived", "无来源词但词形派生 → B 档"


def test_overlong_targets_selection():
    """过长重试的挑选口径：只有"名字是合法形态但词数超限"的才挑出来重问。"""
    targets = [
        {"idx": "0:subject", "raw": "function reachable through a domain on an allowlist"},
        {"idx": "1:subject", "raw": "sandbox"},
        {"idx": "2:subject", "raw": "this"},          # 代词 → 不是"过长"，不该进重试
        {"idx": "3:subject", "raw": "whatever"},
    ]
    cmap = {
        "0:subject": "function reachable through a domain on an allowlist",   # 7 词 → 过长
        "1:subject": "sandbox",                                              # 合法
        "2:subject": "this",                                                 # 代词 → 由别的护栏管
        "3:subject": None,                                                   # 取不出 → 不重试
    }
    picked = [t["idx"] for t in overlong_targets(targets, cmap)]
    assert picked == ["0:subject"]


def test_final_guard_blocks_bleed_after_retry():
    """终检必须放在所有改写之后：压缩/重试引入的串味也要拦（实测踩到过）。"""
    from app.compile.assemble import final_guard

    targets = [
        {"idx": "0:subject", "raw": "Every function reachable through any domain on an allowlist",
         "position": "subject", "subject": "Every function reachable through any domain on an allowlist",
         "object": "an attack surface", "predicate_normalized": "is"},
        {"idx": "0:object", "raw": "an attack surface", "position": "object",
         "subject": "Every function reachable through any domain on an allowlist",
         "object": "an attack surface", "predicate_normalized": "is"},
        {"idx": "1:subject", "raw": "the sandbox", "position": "subject",
         "subject": "the sandbox", "object": "", "predicate_normalized": "is"},
    ]
    cmap = {"0:subject": "allowlisted domain attack surface",   # 借了宾语的概念 → 必须拦
            "0:object": "attack surface",
            "1:subject": "sandbox"}                             # 合法 → 保留
    res = final_guard(targets, cmap)
    assert [d["idx"] for d in res["dropped"]] == ["0:subject"]
    assert cmap["0:subject"] is None and cmap["1:subject"] == "sandbox"


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

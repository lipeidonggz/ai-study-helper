"""S4.5 指代消解的确定性部分单测（目标检出 / 段级窗口 / 依据护栏 / 回填不覆盖）。

三条不变式：
  ① **不覆盖原字段**：消解结果只写新字段，`subject` / `object` 一字不动；
  ② **依据护栏是"只删不改"**：省略不算编造，改词/凭空造句才算；
  ③ **形式主语不给硬塞主语**：`there / it`（存在句、形式主语）由提示词判 not_anaphora，程序不猜。
"""

from app.compile.anaphora import (
    apply_evidence_guard,
    apply_resolutions,
    classify,
    context_window,
    evidence_verbatim,
    parse_results,
    unresolved_reason,
)

WINDOW = (
    "The share of agent context keeps growing—this includes product memory, CLAUDE.md files, "
    "mounted workspaces, and the state directories of agents."
)


# ---------- 目标检出 ----------

def test_classify_kinds():
    assert classify("this") == ("bare", "this")
    assert classify("these defenses") == ("phrase", "these")
    assert classify("It was sandboxed") == ("embedded", "it")       # 夹在更长串里
    assert classify("credentials") is None                          # 不是指代
    assert classify("the allowlist") is None
    assert classify("") is None


# ---------- 依据护栏：只删不改 ----------

def test_parse_results_handles_bare_array_and_fences():
    """模型偶尔返回**裸数组**——不能因此丢一整批（2026-09-17 实测踩过）。"""
    wrapped = '```json\n{"results":[{"claim_idx":1,"position":"subject","status":"resolved"}]}\n```'
    bare = '```json\n[{"claim_idx":1,"position":"subject","status":"resolved"},{"claim_idx":2,"position":"object"}]\n```'
    plain_bare = '[{"claim_idx":3,"position":"subject"}]'
    assert [r["claim_idx"] for r in parse_results(wrapped)] == [1]
    assert [r["claim_idx"] for r in parse_results(bare)] == [1, 2]     # 裸数组两条都要在
    assert [r["claim_idx"] for r in parse_results(plain_bare)] == [3]
    assert parse_results("完全不是 JSON") == []
    assert parse_results("") == []


# ---------- 判不出的原因分类（summary vs recheck）----------

def test_unresolved_reason_summary_when_head_never_appears_before_anchor():
    """`these questions` 类：中心词在锚句前没出现过 → 总结性指代（不造概念名）。"""
    t = {
        "kind": "phrase",
        "field_text": "these questions",
        "anchor_quote": "As agents take on more of the lifecycle, these questions will matter even more.",
        "window": "What we don't yet know is how coherence evolves. We're still learning where judgment helps. "
                  "As agents take on more of the lifecycle, these questions will matter even more.",
    }
    assert unresolved_reason(t) == "summary"


def test_unresolved_reason_recheck_when_head_appears_before_anchor():
    """中心词在锚句前出现过却仍没解 → 值得复查（窗口不足 or 模型漏解）。"""
    t = {
        "kind": "phrase",
        "field_text": "these rules",
        "anchor_quote": "These rules are enforced mechanically.",
        "window": "We defined three layering rules for the repository. These rules are enforced mechanically.",
    }
    assert unresolved_reason(t) == "recheck"
    # 光杆代词一律进"待复查"（规则判不了它是哪种）
    assert unresolved_reason({"kind": "bare", "field_text": "it", "window": "x", "anchor_quote": "x"}) == "recheck"

def test_context_window_reaches_far_back_and_stops_at_anchor():
    """窗口口径（2026-09-17 三改）：前向铺得够长、锚句完整、**后向不留**。

    实测依据：先行词会在 875 / ~1300 字符之前（`[157]` / `[209]`）；而"锚句之后的文本"
    0/95 条用到，故后向不额外留。
    """
    intro = "The rigid architectural model fixes the layers and the permissible edges. " * 20  # ≈1400 字符
    text = intro + "In practice, we enforce these rules with custom linters. Following sentence about something else."
    w = context_window({"evidence_texts": ["In practice, we enforce these rules with custom linters."]}, text)
    assert "In practice, we enforce these rules" in w["window"]          # 锚句完整
    assert "The rigid architectural model" in w["window"]                # 前向铺得够长
    assert "Following sentence about something else" not in w["window"]  # 后向不留
    assert w["anchor_quote"].startswith("In practice")


def test_context_window_keeps_trailing_quote_anchor_without_extra_sentence():
    """锚句以引号结尾（`…“taste invariants.”`）时，不该多带后面一句。"""
    text = "Line one is here. In practice, we enforce these rules, plus a set of “taste invariants.” For example, we statically enforce logging."
    w = context_window({"evidence_texts": ["In practice, we enforce these rules, plus a set of “taste invariants.”"]}, text)
    assert w["window"].endswith("“taste invariants.”")
    assert "For example" not in w["window"]


def test_evidence_verbatim_accepts_exact_and_elision():
    assert evidence_verbatim("this includes product memory, CLAUDE.md files", WINDOW)     # 精确
    assert evidence_verbatim("The share of agent context keeps growing ... this includes", WINDOW)  # 省略号
    assert evidence_verbatim("this includes CLAUDE.md files", WINDOW)                     # **删掉中间的并列项**（实测被误伤的那种）


def test_evidence_verbatim_rejects_fabrication_and_empty():
    assert not evidence_verbatim("", WINDOW)                                              # 空依据不算通过
    assert not evidence_verbatim("the cat sat on the mat", WINDOW)                        # 凭空造句
    assert not evidence_verbatim("this includes product memory and secrets", WINDOW)      # 改了词（插入 window 里没有的）


# ---------- 护栏 + 回填 ----------

def test_guard_demotes_only_fabricated_evidence():
    targets = [{"claim_idx": 0, "position": "subject", "window": WINDOW}]
    good = [{"claim_idx": 0, "position": "subject", "status": "resolved",
             "resolution": "the growing agent context", "evidence": "this includes CLAUDE.md files"}]
    bad = [{"claim_idx": 0, "position": "subject", "status": "resolved",
            "resolution": "something", "evidence": "the cat sat on the mat"}]
    assert apply_evidence_guard(good, targets)["demoted"] == 0
    assert good[0]["status"] == "resolved"
    assert apply_evidence_guard(bad, targets)["demoted"] == 1
    assert bad[0]["status"] == "unresolved" and bad[0]["evidence_not_verbatim"] is True


def test_apply_resolutions_never_overwrites_source_fields():
    claims = [{"subject": "this", "predicate": "is", "object": "fallible",
               "evidence_texts": ["This is fallible."]}]
    apply_resolutions(claims, [{
        "claim_idx": 0, "position": "subject", "anaphor": "this", "kind": "bare",
        "status": "resolved", "resolution": "the oversight approach", "evidence": "This is fallible.",
    }])
    c = claims[0]
    assert c["subject"] == "this"                                   # 原文 surface 没被动
    assert c["subject_resolved"] == "the oversight approach"        # 结果另存
    assert c["subject_resolution_status"] == "resolved"


def test_embedded_kind_gets_no_replaceable_field():
    """夹在更长串里的指代（it was sandboxed）不给 *_resolved——无法干净替换，只留记录。"""
    claims = [{"subject": "Claude", "predicate": "had awareness", "object": "it was sandboxed"}]
    apply_resolutions(claims, [{
        "claim_idx": 0, "position": "object", "anaphor": "it", "kind": "embedded",
        "status": "resolved", "resolution": "Claude", "evidence": "Claude had awareness it was sandboxed",
    }])
    assert "object_resolved" not in claims[0]
    assert claims[0]["object_resolution"] == "Claude"               # 记录仍在（交 S5 取舍）
    assert claims[0]["object"] == "it was sandboxed"

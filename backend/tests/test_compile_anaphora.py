"""S4.5 指代消解的确定性部分单测（目标检出 / 段级窗口 / 依据护栏 / 回填不覆盖）。

三条不变式：
  ① **不覆盖原字段**：消解结果只写新字段，`subject` / `object` 一字不动；
  ② **依据护栏是"只删不改"**：省略不算编造，改词/凭空造句才算；
  ③ **形式主语不给硬塞主语**：`there / it`（存在句、形式主语）由提示词判 not_anaphora，程序不猜。
"""

from app.compile.anaphora import apply_evidence_guard, apply_resolutions, classify, evidence_verbatim

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

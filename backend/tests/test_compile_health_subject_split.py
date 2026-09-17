"""体检「主语两分」的单元测试（2026-09-17 定）。

背景：原先 `META_SUBJECTS` 把两类东西塞进一个桶、统一"改写为文档主体"，导致两类错误：
  ① 自称 `we/our/us` → 改成文档主体**是对的**（所指确定、语义等价）；
  ② 指代 `this/it/they` → 改成文档主体**会造错**（`this enables progressive dispersion`
     里的 this 不是作者），而丢掉又会让下游「指代消解」无从做起。

故拆成两个指标：`self_reference`（改写）与 `deictic_subject`（**保留 + 标记**）。
另一条同时钉住：**红线只有两条**（recall_must / anchor_rate），其余是告警线，不过也不拦。
"""

from app.compile.health import DEICTIC, SELF_REFERENCE, run_health_check

DOC = "Anthropic"
TEXT = "Anthropic shipped a sandbox. This enables progressive disclosure. We enforce rules with linters."


def _claim(subject, predicate="enforces", obj="rules", ev=None):
    return {
        "subject": subject,
        "predicate": predicate,
        "object": obj,
        "evidence_texts": ev or ["We enforce rules with linters."],
    }


def _run(claims):
    return run_health_check(claims, TEXT, "A5", doc_subject=DOC, gold=False)


# ---------- 两年集合本身 ----------

def test_two_sets_are_disjoint():
    assert SELF_REFERENCE & DEICTIC == set()
    assert "we" in SELF_REFERENCE and "this" in DEICTIC


# ---------- 自称：改写为文档主体 ----------

def test_self_reference_is_rewritten():
    kept, rep = _run([_claim("we")])
    assert kept[0]["subject"] == DOC                     # 改写生效
    assert rep["rewritten"][0]["rewrites"][0]["from"] == "we"
    assert rep["checks"]["② 自称改写为文档主体"]["count"] == 1
    assert "needs_resolution" not in kept[0]             # 自称不需要消解


# ---------- 指代：保留 + 标记，绝不改写、绝不丢弃 ----------

def test_deictic_is_kept_and_marked_not_rewritten():
    kept, rep = _run([_claim("this", ev=["This enables progressive disclosure."])])
    assert len(kept) == 1, "指代 claim 不许被丢弃"
    assert kept[0]["subject"] == "this", "指代的 surface 必须原样保留（可锚定不破）"
    assert kept[0]["subject_anaphora"] == "this"
    assert kept[0]["needs_resolution"] is True
    assert kept[0]["subject"] != DOC, "指代绝不能被改写成文档主体"
    assert rep["checks"]["②b 指代保留待消解"]["count"] == 1
    assert rep["verdict"]["deictic_subject"]["value"] == 1.0


def test_deictic_appears_in_marked_list():
    _kept, rep = _run([_claim("this", ev=["This enables progressive disclosure."])])
    assert rep["marked"], "指代应进标记清单（人要看得见）"
    assert "指代主语（待消解）" in rep["marked"][0]["marks"]


# ---------- 红线只由两条决定 ----------

def test_warning_metrics_do_not_block_red_line():
    """自称占比远超告警线（0.20），但两条红线都过 → red_line_pass 必须为 True。"""
    claims = [_claim("we") for _ in range(9)] + [_claim("Anthropic")]
    _kept, rep = _run(claims)
    assert rep["verdict"]["self_reference"]["value"] == 0.9
    assert rep["verdict"]["self_reference"]["pass"] is False      # 告警亮红
    assert rep["verdict"]["self_reference"]["red_line"] is False  # 但不是红线
    assert rep["red_line_pass"] is True                           # 所以不拦


def test_anchor_rate_still_blocks():
    """引文定位不到 → 红线照样拦（幻觉探针不能被削弱）。"""
    bad = _claim("Anthropic", ev=["这句话原文里根本没有。"])
    _kept, rep = _run([bad])
    assert rep["verdict"]["anchor_rate"]["pass"] is False
    assert rep["red_line_pass"] is False
    assert [c for c in rep["check_spec"] if c["red_line"] and not c["pass"]][0]["key"] == "anchor_rate"


def test_check_spec_marks_only_two_red_lines():
    _kept, rep = _run([_claim("we")])
    red = {c["key"] for c in rep["check_spec"] if c["red_line"]}
    assert red == {"anchor_rate"}          # 本用例没给金标准 → 召回未执行，只剩引文可锚
    assert "deictic_subject" in {c["key"] for c in rep["check_spec"]}

"""体检新增指标 self_loop_risk：主语与宾语形归一后相同 → 标记 + 计入比例（不丢）。"""

from __future__ import annotations

from app.compile.health import run_health_check

CLEAN = (
    "The model the agent consults is the model the agent consults.\n"
    "The sandbox runs code.\n"
)
CLAIMS = [
    {"subject": "the model the agent consults", "predicate": "is",
     "object": "the model the agent consults",
     "evidence_texts": ["The model the agent consults is the model the agent consults."]},
    {"subject": "the sandbox", "predicate": "runs", "object": "code",
     "evidence_texts": ["The sandbox runs code."]},
]


def test_self_loop_risk_metric_and_mark():
    kept, report = run_health_check(CLAIMS, CLEAN, "X", gold=False)
    spec = {c["key"]: c for c in report["check_spec"]}
    assert "self_loop_risk" in spec
    assert spec["self_loop_risk"]["value"] == 0.5          # 2 条里 1 条
    assert spec["self_loop_risk"]["red_line"] is False     # 告警线，不是红线
    marks = [(c.get("health") or {}).get("marks") or [] for c in kept]
    assert any("主语==宾语（伪 claim）" in m for m in marks)
    assert len(kept) == 2, "体检只标记不丢（与「体检只保信息」一致）"


def test_case_and_article_differences_also_count():
    claims = [{"subject": "Sandbox", "predicate": "is", "object": "the sandbox",
               "evidence_texts": ["The sandbox runs code."]}]
    _kept, report = run_health_check(claims, CLEAN, "X", gold=False)
    spec = {c["key"]: c for c in report["check_spec"]}
    assert spec["self_loop_risk"]["value"] == 1.0, "冠词/大小写差异经形归一后也算同"

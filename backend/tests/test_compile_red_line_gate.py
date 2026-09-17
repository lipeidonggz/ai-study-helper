"""体检红线闸门单元测试。

不变式（2026-09-17 定）：**红线不过 → 该篇编译失败、不进入下一步**。
此前只产出了 `red_line_pass=false` 的判定，任务却照常 `done`，闸门等于没接。

两条要钉住的：
1. 判定与语义一致——`red_line_pass=false` 必须让 `status()` 返回 `failed`（含进程重启后的落盘推导路径）；
2. 原因要可读——点名是哪条红线、实测多少、阈值多少（否则报告里只看到一个 false）。
"""

import json

from app.compile.service import CompileService, red_line_gate, summary_verdict


def _report(passed, red_items=(), other_items=()):
    """构造体检报告：红线项 + 非红线项。"""
    spec = [
        {"key": k, "name": n, "value": v, "threshold": t, "pass": ok, "red_line": True, "action": "红线", "note": ""}
        for k, n, v, t, ok in red_items
    ] + [
        {"key": k, "name": n, "value": v, "threshold": t, "pass": ok, "red_line": False, "action": "标记", "note": ""}
        for k, n, v, t, ok in other_items
    ]
    return {"red_line_pass": passed, "check_spec": spec}


# ---------- 闸门判定 ----------

def test_gate_passes_clean_report():
    ok, why = red_line_gate(_report(True, [("anchor_rate", "引文可锚率", 0.99, 0.95, True)]))
    assert ok is True
    assert why == ""


def test_gate_blocks_and_names_only_red_items():
    rep = _report(
        False,
        red_items=[("anchor_rate", "引文可锚率", 0.87, 0.95, False), ("recall_must", "召回（must 项）", 1.0, 1.0, True)],
        other_items=[("meta_subject", "元主语占比", 0.31, 0.10, False)],  # 非红线，不该出现在原因里
    )
    ok, why = red_line_gate(rep)
    assert ok is False
    assert "引文可锚率" in why and "0.87" in why and "0.95" in why
    assert "召回" not in why          # 这条是过的
    assert "元主语" not in why        # 这条不是红线


def test_gate_tolerates_legacy_report_without_spec():
    """旧报告（只有 verdict、没有 check_spec）也要能拦、且给得出名字。"""
    rep = {"red_line_pass": False, "verdict": {"anchor_rate": {"value": 0.5, "threshold": 0.95, "pass": False}}}
    ok, why = red_line_gate(rep)
    assert ok is False
    assert "anchor_rate" in why


def test_gate_blocks_even_without_any_detail():
    ok, why = red_line_gate({"red_line_pass": False})
    assert ok is False and why          # 宁可给一句兜底，也不许放行


def test_summary_verdict_prefers_recorded_gate():
    """落盘 summary 里记了闸门结论时，重启后仍能说出是哪条红线。"""
    s = {
        "red_line_pass": False,
        "red_line_gate": {"passed": False, "reason": "体检红线未通过：引文可锚率（实测 0.71，阈值 0.95）"},
    }
    ok, why = summary_verdict(s)
    assert ok is False and "引文可锚率" in why
    # 通过时原因必须为空（否则界面会误亮失败横幅）
    ok, why = summary_verdict({"red_line_pass": True, "red_line_gate": {"passed": True, "reason": ""}})
    assert ok is True and why == ""


# ---------- 状态口径：进程重启后从落盘产物推导 ----------

def test_status_from_artifacts_is_failed_when_red_line_not_passed(tmp_path):
    svc = CompileService(tmp_path)
    d = tmp_path / "A5"
    d.mkdir()
    (d / "summary.json").write_text(
        json.dumps({"source_id": "A5", "claims_kept": 10, "red_line_pass": False}, ensure_ascii=False),
        encoding="utf-8",
    )
    st = svc.status("A5")
    assert st["status"] == "failed"
    assert "红线" in st["error"]
    assert svc.all_statuses()["A5"]["status"] == "failed"


def test_status_from_artifacts_is_done_when_red_line_passed(tmp_path):
    svc = CompileService(tmp_path)
    d = tmp_path / "A5"
    d.mkdir()
    (d / "summary.json").write_text(
        json.dumps({"source_id": "A5", "claims_kept": 10, "red_line_pass": True}, ensure_ascii=False),
        encoding="utf-8",
    )
    st = svc.status("A5")
    assert st["status"] == "done" and st["error"] == ""
    assert svc.all_statuses()["A5"]["status"] == "done"

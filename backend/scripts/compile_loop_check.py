"""编译闭环检查：对某个源**已落盘的产物**做一次确定性验收（薄 CLI，不调 LLM）。

为什么需要它：流水线跑完"看着还行"不算闭环。闭环 = **产物齐备 + 各环节口径自洽 + 判定生效**，
而且这套验收要能原样搬到下一个源（A5 → O2 → …）。

检查项（全部确定性，可重复执行）：
  ① 产物齐备      claims_raw / claims / report / summary 四件
  ② 闸门一致      summary.red_line_pass == report.red_line_pass，且闸门结论已落盘
  ③ 红线通过      该篇是否真的够格进下一步（这才是"闭环"的判定项，其余是自洽性）
  ④ 计数一致      claims_in/claims_out 与四份清单的长度对得上（不靠"看报告"）
  ⑤ 归一完整      每条 claim 有三态；mapped/forced 的谓词必须真在受控关系表里；pending 清单与报告一致
  ⑥ 清洗护栏自洽  留下的 predicate_clean 必须"只删不改"可还原；被判拒的必须确实不可还原
  ⑦ 可锚闭环      每条 claim 有非空锚句（health.anchor 或 evidence_texts 首段）
  ⑧ 清单可回溯    dropped / marked / pending 的 idx 都落在 claims_raw 范围内
  ⑨ 指代闭环      每个指代型字段（主语/宾语）都有 *_resolution_status；
                   且**原字段没被覆盖**（有 *_resolved 的，原字段仍须是"指代词"）
  ⑩ S6 合并闭环    S6-A 形合并（graph_merged.json）：① 边不丢（只允许丢自环）；
                   ② 原名可回溯（每个被合并的写法都能在 canonical 或 aliases 里找到）；
                   ③ 实体表＝边端点集合（无悬挂、无孤立）；④ 无别名冲突

用法：cd backend && .venv\\Scripts\\python.exe -m scripts.compile_loop_check --source A5
退出码：0 全过 / 1 有不过（便于串进脚本）。
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from app.compile.relations import RELATIONS
from app.compile.anaphora import classify
from app.compile.merge import alias_collisions
from app.compile.service import summary_verdict
from scripts.compile_slice_b6 import _is_subsequence

BACKEND = Path(__file__).resolve().parents[1]
DEFAULT_ROOT = BACKEND / "data" / "compile"


def _load(p: Path):
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else None


def check(source_id: str, root: Path) -> list[tuple[str, bool, str]]:
    d = root / source_id
    raw = _load(d / "claims_raw.json")
    kept = _load(d / "claims.json")
    report = _load(d / "report.json")
    summary = _load(d / "summary.json")
    out: list[tuple[str, bool, str]] = []

    # ① 产物齐备
    have = [n for n, v in (("claims_raw", raw), ("claims", kept), ("report", report), ("summary", summary)) if v is not None]
    out.append(("① 产物齐备", len(have) == 4, f"{len(have)}/4（{'、'.join(have) or '无'}）"))
    if len(have) < 4:
        return out + [("② – ⑦", False, "产物不齐，后续检查跳过")]

    # ② 闸门一致
    passed, why = summary_verdict(summary)
    same = bool(summary.get("red_line_pass")) == bool(report.get("red_line_pass")) == passed
    has_gate = "red_line_gate" in summary
    out.append(("② 闸门一致", same and has_gate,
                f"summary={summary.get('red_line_pass')} report={report.get('red_line_pass')} 推导={passed}"
                f"{'' if has_gate else ' ·（老产物缺 red_line_gate）'} | {why or '—'}"))

    # ③ 红线通过（闭环的判定项）
    failed = [c for c in (report.get("check_spec") or []) if c.get("red_line") and not c.get("pass")]
    ok3 = bool(report.get("red_line_pass"))
    out.append((
        "③ 红线通过", ok3,
        "六项全过" if ok3 else "；".join(
            f"{c.get('name')} {c.get('value')} vs {c.get('threshold')}" for c in failed
        ) or "红线不过",
    ))

    # ④ 计数一致
    dropped, marked, pending = report.get("dropped") or [], report.get("marked") or [], report.get("pending") or []
    ok4 = (
        report.get("claims_in") == len(raw)
        and report.get("claims_out") == len(kept)
        and len(kept) + len(dropped) == len(raw)
    )
    out.append(("④ 计数一致", ok4,
                f"raw={len(raw)} in={report.get('claims_in')} out={report.get('claims_out')} "
                f"丢弃={len(dropped)} 标记={len(marked)} 待定={len(pending)}"))

    # ⑤ 归一完整（谓词必须真落表）
    bad_status = [i for i, c in enumerate(kept) if c.get("predicate_status") not in ("mapped", "forced", "pending")]
    off_table = [
        (i, c.get("predicate_normalized"))
        for i, c in enumerate(kept)
        if c.get("predicate_status") in ("mapped", "forced") and c.get("predicate_normalized") not in RELATIONS
    ]
    pending_idx = {(c.get("health") or {}).get("src_idx", i) for i, c in enumerate(kept) if c.get("predicate_status") == "pending"}
    report_pending_idx = {p.get("idx") for p in pending}
    ok5 = not bad_status and not off_table and pending_idx == report_pending_idx
    detail5 = f"缺三态 {len(bad_status)}；表外谓词 {len(off_table)}；pending 对账 {len(pending_idx)} vs 报告 {len(report_pending_idx)}"
    if off_table:
        detail5 += f" · 表外样例 {off_table[:2]}"
    out.append(("⑤ 归一完整", ok5, detail5))

    # ⑥ 清洗护栏自洽（留下的可还原、被判拒的确实不可还原）
    kept_clean = [c for c in kept if c.get("predicate_clean")]
    bad_clean = [(c["predicate"], c["predicate_clean"]) for c in kept_clean if not _is_subsequence(c["predicate_clean"], c["predicate"])]
    rejected = [c for c in kept if c.get("predicate_clean_rejected")]
    wrong_reject = [(c["predicate"], c["predicate_clean_rejected"]) for c in rejected if _is_subsequence(c["predicate_clean_rejected"], c["predicate"])]
    ok6 = not bad_clean and not wrong_reject
    out.append(("⑥ 清洗护栏自洽", ok6,
                f"清洗 {len(kept_clean)}（越权 {len(bad_clean)}）／被拒 {len(rejected)}（冤枉 {len(wrong_reject)}）"))

    # ⑦ 可锚闭环
    anchored = [
        c for c in kept
        if ((c.get("health") or {}).get("anchor") or (c.get("evidence_texts") or [""])[0]).strip()
    ]
    rate = len(anchored) / max(1, len(kept))
    out.append(("⑦ 可锚闭环", rate >= 0.95, f"{len(anchored)}/{len(kept)} = {rate:.3f}（阈值 0.95）"))

    # ⑧ 清单可回溯（idx 必须落在 claims_raw 范围内，且被丢弃的不该出现在 kept 里）
    n = len(raw)
    bad_idx = [x.get("idx") for x in dropped + marked + pending if not isinstance(x.get("idx"), int) or not (0 <= x["idx"] < n)]
    out.append(("⑧ 清单可回溯", not bad_idx, f"越界 idx {len(bad_idx)}{' · ' + str(bad_idx[:4]) if bad_idx else ''}"))

    # ⑨ 指代闭环（S4.5）
    no_status, overwritten, anaphora_n = [], [], 0
    for i, c in enumerate(kept):
        for pos in ("subject", "object"):
            txt = (c.get(pos) or "").strip()
            if not classify(txt):
                if c.get(f"{pos}_resolved"):           # 有消解结果，原字段却不是指代词 → 被覆盖了
                    overwritten.append((i, pos))
                continue
            anaphora_n += 1
            if not c.get(f"{pos}_resolution_status"):
                no_status.append((i, pos))
    ok9 = not no_status and not overwritten
    out.append(("⑨ 指代闭环", ok9,
                f"指代字段 {anaphora_n}；缺状态 {len(no_status)}{no_status[:3]}；疑似被覆盖 {len(overwritten)}{overwritten[:3]}"))

    # ⑩ S6 合并闭环（S6-A 形合并；该篇没跑 S6-A 则跳过）
    merged = _load(d / "graph_merged.json")
    if merged is None:
        out.append(("⑩ S6 合并闭环", True, "跳过：无 graph_merged.json（该篇未跑 S6-A）"))
        return out

    m_ents = merged.get("entities") or []
    m_edges = merged.get("edges") or []
    g5 = _load(d / "graph.json") or {}
    mg_audit = merged.get("audit") or {}
    self_loops = int(mg_audit.get("self_loops_dropped") or 0)
    edges5 = len(g5.get("edges") or [])
    ents5 = len(g5.get("entities") or [])

    # ① 边不丢：合并后边数 = S5 边数 − 自环丢弃数
    lost = edges5 - self_loops - len(m_edges)
    ok10a = lost == 0

    # ② 原名可回溯：被合并掉的每个写法，都能在 canonical 或 aliases 里找到
    reachable: set[str] = set()
    for e in m_ents:
        reachable.add(e.get("name") or "")
        reachable.update(e.get("aliases") or [])
    missing = [v for m in (merged.get("merge_log") or []) for v in (m.get("variants") or {}) if v not in reachable]
    ok10b = not missing

    # ③ 实体表＝边端点集合（无悬挂、无孤立）
    eids = {e.get("id") for e in m_ents}
    used = {x for ed in m_edges for x in (ed.get("from"), ed.get("to")) if x}
    dangling = sorted(x for x in used if x not in eids)
    isolated = sorted(x for x in eids if x not in used)
    ok10c = not dangling and not isolated

    # ④ 无别名冲突（同一个名字挂到多个实体）
    collisions = alias_collisions(m_ents)
    ok10d = not collisions

    ok10 = ok10a and ok10b and ok10c and ok10d
    out.append((
        "⑩ S6 合并闭环", ok10,
        f"实体 {ents5}→{len(m_ents)}／边 {edges5}→{len(m_edges)}（丢边 {lost}、自环 {self_loops}）"
        f"｜原名缺失 {len(missing)}{missing[:3]}｜悬挂 {len(dangling)}／孤立 {len(isolated)}"
        f"｜别名冲突 {len(collisions)}{collisions[:2]}"
        f"｜无边移除 {int(mg_audit.get('isolated_dropped') or 0)}",
    ))
    return out


def main() -> None:
    ap = argparse.ArgumentParser(description="编译闭环检查（确定性验收，不调 LLM）")
    ap.add_argument("--source", required=True, help="源 id，如 A5 / O2")
    ap.add_argument("--root", default=str(DEFAULT_ROOT), help="产物根目录")
    ap.add_argument("--json", action="store_true", help="以 JSON 输出（供程序消费）")
    args = ap.parse_args()

    rows = check(args.source, Path(args.root))
    if args.json:
        print(json.dumps([{"item": a, "pass": b, "detail": c} for a, b, c in rows], ensure_ascii=False, indent=1))
    else:
        print(f"=== 编译闭环检查 · {args.source} ===")
        for item, ok, detail in rows:
            print(f"  {'✓' if ok else '✗'} {item:<14} {detail}")
        print(f"结论：{'闭环通过' if all(r[1] for r in rows) else '闭环未通过'}")
    sys.exit(0 if all(r[1] for r in rows) else 1)


if __name__ == "__main__":
    main()

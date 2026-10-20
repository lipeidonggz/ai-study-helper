"""用我们自己的判官给 WeKnora 三臂的答案打分（content / format 两个维度）。

口径与边界（重要）：
  · 判官代码与提示词**原样复用** backend/eval/runner.py 的 judge_case；
  · 本脚本**跳过 citation_truth**：判官那条是"两阶段声明级引用核对"，需要我们自己的检索
    证据块（hits 带 [n] 编号、与模型输出一一对应），而 WeKnora 的答案是行内
    <kb doc=… chunk_id=… /> 句柄、且流里不返回命中块正文 ⇒ 喂不进那套判据。
    引用另做**确定性存在性核验**（见 verify_citations.py），本项目一律标明"本轮不判 citation"。
  · 每题每条只判一次（沛东定：先不跑三轮）。

用法（仓库根目录）：
    .\\backend\\.venv\\Scripts\\python.exe experiments\\weknora\\judge_ourcases.py
"""
from __future__ import annotations

import asyncio
import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))

from app.di import build_deps  # noqa: E402
from eval import case_store  # noqa: E402
from eval.run_manager import build_llm  # noqa: E402
from eval.runner import (  # noqa: E402
    JUDGE_TEMPERATURE,
    CaseResult,
    case_verdict,
    judge_case,
)

OUT_DIR = ROOT / "data" / "tmp" / "weknora-ourcases"
SKIP_CRITERIA = {"citation_truth"}
ARMS = ("wiki", "smart", "hybrid")


def load_answers(pattern: str = "qa-*.jsonl") -> list[dict]:
    """从文件名解析 set/arm/variant：qa-<set>-<arm>[-<variant>].jsonl"""
    rows: list[dict] = []
    for p in sorted(OUT_DIR.glob(pattern)):
        stem = p.stem.split("-")
        if len(stem) < 3:
            continue
        set_name, arm = stem[1], stem[2]
        variant = "-".join(stem[3:]) or "a"
        for line in p.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            r = json.loads(line)
            rows.append({"set": set_name, "arm": arm, "variant": variant, **r})
    return rows


def strip_citation(case):
    exp = case.expected.model_copy(
        update={"criteria": [c for c in case.expected.criteria if c not in SKIP_CRITERIA]}
    )
    return case.model_copy(update={"expected": exp})


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--glob", default="qa-*.jsonl", help="逗号分隔可传多个 pattern")
    ap.add_argument("--case", default="", help="只判指定 case_id（逗号分隔）")
    ap.add_argument("--out", default="judge-ourcases.json")
    args = ap.parse_args(sys.argv[1:])
    rows = []
    for pattern in [p.strip() for p in args.glob.split(",") if p.strip()]:
        rows.extend(load_answers(pattern))
    if args.case:
        keep = {c.strip() for c in args.case.split(",") if c.strip()}
        rows = [r for r in rows if r["id"] in keep]
    if not rows:
        raise SystemExit("没有待判答案，检查 glob/case 参数")
    print(f"待判答案：{len(rows)} 条（glob={args.glob} case={args.case or '-'}）", flush=True)
    deps = build_deps()
    judge = build_llm(deps, "real", temperature=JUDGE_TEMPERATURE)
    sem = asyncio.Semaphore(3)
    results: list[dict] = [None] * len(rows)  # type: ignore[list-item]

    async def work(i: int, row: dict) -> None:
        async with sem:
            case = case_store.get_case(row["id"])
            if case is None:
                results[i] = {"status": "error", "verdict": "exec_error",
                              "judge_reasons": {"fatal": "case not found"}}
                return
            case = strip_citation(case)
            result = CaseResult(
                case_id=row["id"],
                status="ok",
                output=row.get("pred") or "",
                elapsed_ms=float((row.get("elapsed_sec") or 0) * 1000),
                rounds=len(row.get("tool_calls") or []),
                tool_calls=[],
                tokens={},
                retrieval_evidence=[],
            )
            try:
                out = await judge_case(case, result, judge, diagnostics_enabled=False)
                entry = {
                    "status": "ok",
                    "set": row["set"],
                    "arm": row["arm"],
                    "variant": row.get("variant", "a"),
                    "case_id": row["id"],
                    "chars": len(row.get("pred") or ""),
                    "elapsed_sec": row.get("elapsed_sec"),
                    "judgments": out["judgments"],
                    "pending_human": out["pending_human"],
                    "judge_reasons": out["judge_reasons"],
                    "coverage_points": out.get("coverage_points"),
                    "judge_tokens": out.get("judge_tokens") or {},
                }
                entry["verdict"] = case_verdict(entry)
            except Exception as exc:  # noqa: BLE001
                entry = {
                    "status": "error",
                    "set": row["set"],
                    "arm": row["arm"],
                    "variant": row.get("variant", "a"),
                    "case_id": row["id"],
                    "verdict": "exec_error",
                    "judgments": {},
                    "pending_human": [],
                    "judge_reasons": {"fatal": f"{type(exc).__name__}: {exc}"[:300]},
                }
            results[i] = entry
            print(f"[{i + 1:02d}/{len(rows)}] {row['set']}-{row['id'][-3:]} {row['arm']:6s} "
                  f"{row.get('variant', 'a'):5s} "
                  f"-> {entry['verdict']} {entry['judgments']}", flush=True)

    await asyncio.gather(*(work(i, r) for i, r in enumerate(rows)))

    out_path = OUT_DIR / args.out
    out_path.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    print("\n===== 汇总（引用维度本轮不判）")
    for r in results:
        if r and r.get("set"):
            print(f"{r['set']:3s} {r['case_id'][-3:]} {r['arm']:6s} {r['verdict']:10s} "
                  f"{ {k: v for k, v in (r.get('judgments') or {}).items()} }")
    print("\nwrote:", out_path)


if __name__ == "__main__":
    asyncio.run(main())

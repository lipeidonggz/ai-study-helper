"""判官重放：用 Run#114 的 20 条模型输出跑 v2a 判官链（统一 g/f + 声明级两阶段）。

目的：判官 A/B 同输入对照（Run#112 自由文本 15/20 → Run#114 checklist 三次调用 5/13/2
→ 本脚本 = v2a 单次统一 + 声明级引用）。不碰 Qdrant / Agent，只花判官 token。
"""

import asyncio
import json
import sqlite3
from collections import Counter

from app.di import build_deps
from eval import case_store
from eval.runner import JUDGE_TEMPERATURE, CaseResult, case_verdict, judge_case
from eval.schema import CaseFile
from eval.run_manager import build_llm

DB = r"D:\lida-data\vscode\ai-study-helper\backend\data\eval.db"


def _load_run114_attempts() -> list[dict]:
    con = sqlite3.connect(DB)
    con.text_factory = lambda b: b.decode("utf-8", "replace")
    cur = con.cursor()
    cur.execute(
        "SELECT repeat_results FROM eval_run_cases WHERE run_id=114 AND case_id='rag-cmp-002'"
    )
    row = cur.fetchone()
    con.close()
    return json.loads(row[0])


def _evidence_from_trace(trace: list[dict]) -> list[dict]:
    for step in trace or []:
        data = step.get("data") if isinstance(step, dict) else None
        if isinstance(data, dict) and "gate" in data and "hits" in data:
            return [data]
    return []


def _case() -> CaseFile:
    c = case_store.get_case("rag-cmp-002")
    assert c is not None, "rag-cmp-002 未找到"
    return c


def _result(attempt: dict) -> CaseResult:
    return CaseResult(
        case_id="rag-cmp-002",
        status="ok",
        output=attempt.get("output", "") or "",
        tool_calls=attempt.get("tool_calls") or [],
        tokens=attempt.get("tokens") or {},
        retrieval_evidence=_evidence_from_trace(attempt.get("trace") or []),
    )


async def main() -> None:
    attempts = _load_run114_attempts()
    usable = [a for a in attempts if a.get("status") == "ok" and (a.get("output") or "").strip()]
    print(f"Run#114 attempts: {len(attempts)}，可重放 ok+非空输出: {len(usable)}", flush=True)
    case = _case()
    deps = build_deps()
    judge = build_llm(deps, "real", temperature=JUDGE_TEMPERATURE)
    sem = asyncio.Semaphore(6)
    results: list[dict] = [None] * len(usable)

    async def work(i: int, a: dict) -> None:
        async with sem:
            try:
                out = await judge_case(case, _result(a), judge)
                entry = {
                    "status": "ok",
                    "judgments": out["judgments"],
                    "pending_human": out["pending_human"],
                    "judge_reasons": out["judge_reasons"],
                    "judge_tokens": out["judge_tokens"],
                }
                entry["verdict"] = case_verdict(entry)
                results[i] = entry
            except Exception as exc:  # noqa: BLE001
                results[i] = {
                    "status": "error",
                    "verdict": "exec_error",
                    "judgments": {},
                    "pending_human": [],
                    "judge_reasons": {"fatal": str(exc)[:200]},
                    "judge_tokens": {},
                }
            print(f"  [{i + 1}/{len(usable)}] verdict={results[i]['verdict']}", flush=True)

    await asyncio.gather(*(work(i, a) for i, a in enumerate(usable)))

    verdicts = Counter(r["verdict"] for r in results)
    print("\n=== v2a 判官重放结果（Run#114 同一批输出）===")
    print("verdicts:", dict(verdicts), flush=True)
    tot = {
        "prompt": sum((r.get("judge_tokens") or {}).get("prompt", 0) for r in results),
        "completion": sum((r.get("judge_tokens") or {}).get("completion", 0) for r in results),
        "total": sum((r.get("judge_tokens") or {}).get("total", 0) for r in results),
    }
    print("judge tokens 合计:", tot, flush=True)
    print("\n=== 逐条 verdict / 关键 reason ===")
    for i, a in enumerate(usable):
        r = results[i]
        reasons = r.get("judge_reasons") or {}
        ans = str(reasons.get("answer_correct", ""))[:120].replace("\n", " ")
        fmt = str(reasons.get("format_appropriate", ""))[:120].replace("\n", " ")
        cit = str(reasons.get("citation_truth", ""))[:150].replace("\n", " ")
        print(f"[{i}] {r['verdict']} | ans: {ans} | fmt: {fmt} | cit: {cit}", flush=True)


if __name__ == "__main__":
    asyncio.run(main())

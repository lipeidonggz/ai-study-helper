"""把 Run#114 同批 20 条输出的判官重放结果固化成一条 eval.db 跑批（评测台可见）。

语义：判官 A/B 验证（同输入），不是端到端跑批；Run 名带"判官重放"以区分。
"""

import asyncio
import json
import sqlite3
from collections import Counter
from pathlib import Path

from app.di import build_deps
from eval import case_store, run_store
from eval.runner import (
    JUDGE_TEMPERATURE,
    CaseResult,
    _aggregate_attempts,
    aggregate,
    case_verdict,
    judge_case,
)
from eval.run_manager import build_llm

DB = Path(r"D:\lida-data\vscode\ai-study-helper\backend\data\eval.db")


def _load_run114_attempts() -> list[dict]:
    con = sqlite3.connect(str(DB))
    con.text_factory = lambda b: b.decode("utf-8", "replace")
    cur = con.cursor()
    cur.execute(
        "SELECT repeat_results FROM eval_run_cases WHERE run_id=114 AND case_id='rag-cmp-002'"
    )
    row = cur.fetchone()
    con.close()
    return json.loads(row[0])


def _evidence_from_trace(trace) -> list[dict]:
    for step in trace or []:
        data = step.get("data") if isinstance(step, dict) else None
        if isinstance(data, dict) and "gate" in data and "hits" in data:
            return [data]
    return []


async def main() -> None:
    attempts = _load_run114_attempts()
    usable = [a for a in attempts if a.get("status") == "ok" and (a.get("output") or "").strip()]
    case = case_store.get_case("rag-cmp-002")
    deps = build_deps()
    judge = build_llm(deps, "real", temperature=JUDGE_TEMPERATURE)
    sem = asyncio.Semaphore(6)
    new_attempts: list[dict] = [None] * len(usable)

    async def work(i: int, a: dict) -> None:
        async with sem:
            result = CaseResult(
                case_id="rag-cmp-002",
                status="ok",
                output=a.get("output", "") or "",
                tool_calls=a.get("tool_calls") or [],
                retrieval_evidence=_evidence_from_trace(a.get("trace") or []),
            )
            out = await judge_case(case, result, judge)
            na = dict(a)
            na.update(
                {
                    "judgments": out["judgments"],
                    "pending_human": out["pending_human"],
                    "metrics": out["metrics"],
                    "judge_reasons": out["judge_reasons"],
                }
            )
            na["verdict"] = case_verdict(na)
            new_attempts[i] = na
            print(f"  [{i + 1}/{len(usable)}] {na['verdict']}", flush=True)

    await asyncio.gather(*(work(i, a) for i, a in enumerate(usable)))

    entry = _aggregate_attempts(case, new_attempts)
    run_id = run_store.create_run(
        DB,
        "cmp-002 判官重放 v2a-上限80（Run#114 同批输出）",
        {
            "case_filter": {"ids": ["rag-cmp-002"]},
            "repeat": 20,
            "kind": "judge_replay_on_run114_outputs",
        },
        total=1,
    )
    run_store.insert_case_result(DB, run_id, entry)
    run_store.update_run(
        DB,
        run_id,
        status="done",
        progress=1,
        total=1,
        summary=aggregate([entry]),
        finished=True,
    )
    print("\nrun_id:", run_id, "verdict:", entry["verdict"],
          "pass:", entry["pass_count"], "/", entry["repeat_count"], flush=True)
    print("attempt verdicts:", dict(Counter(a["verdict"] for a in new_attempts)), flush=True)


if __name__ == "__main__":
    asyncio.run(main())

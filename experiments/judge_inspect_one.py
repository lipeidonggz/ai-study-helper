"""单条判官重放：完整打印 attempt N 的 judgments/reasons（定位异常用）。"""

import asyncio
import json
import sqlite3
import sys

from app.di import build_deps
from eval import case_store
from eval.runner import JUDGE_TEMPERATURE, CaseResult, judge_case
from eval.run_manager import build_llm

DB = r"D:\lida-data\vscode\ai-study-helper\backend\data\eval.db"


def _evidence_from_trace(trace):
    for step in trace or []:
        data = step.get("data") if isinstance(step, dict) else None
        if isinstance(data, dict) and "gate" in data and "hits" in data:
            return [data]
    return []


async def main() -> None:
    idx = int(sys.argv[1])
    con = sqlite3.connect(DB)
    con.text_factory = lambda b: b.decode("utf-8", "replace")
    cur = con.cursor()
    cur.execute(
        "SELECT repeat_results FROM eval_run_cases WHERE run_id=114 AND case_id='rag-cmp-002'"
    )
    attempts = json.loads(cur.fetchone()[0])
    con.close()
    a = attempts[idx]
    case = case_store.get_case("rag-cmp-002")
    deps = build_deps()
    judge = build_llm(deps, "real", temperature=JUDGE_TEMPERATURE)
    result = CaseResult(
        case_id="rag-cmp-002",
        status="ok",
        output=a.get("output", "") or "",
        tool_calls=a.get("tool_calls") or [],
        retrieval_evidence=_evidence_from_trace(a.get("trace") or []),
    )
    out = await judge_case(case, result, judge)
    print("judgments:", json.dumps(out["judgments"], ensure_ascii=False))
    print("pending:", out["pending_human"])
    print("reasons:", json.dumps(out["judge_reasons"], ensure_ascii=False))


if __name__ == "__main__":
    asyncio.run(main())

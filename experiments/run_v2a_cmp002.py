"""v2a 验证：rag-cmp-002 × repeat N（走 eval.db 落库，与评测台同一代码路径）。"""

import asyncio
import sys

from app.di import build_deps
from eval import run_store
from eval.run_manager import RunManager


async def main() -> None:
    repeat = int(sys.argv[1]) if len(sys.argv) > 1 else 20
    deps = build_deps()
    mgr = RunManager()
    run_id = mgr.start(
        deps,
        name=f"cmp-002 v2a 统一判官+声明级引用 repeat{repeat}",
        case_filter={"ids": ["rag-cmp-002"]},
        llm="real",
        concurrency=20,
        retries=1,
        repeat=repeat,
        variant="baseline",
    )
    print("run_id:", run_id, flush=True)
    waited = 0
    while True:
        r = run_store.get_run(mgr.db_path, run_id)
        status = (r or {}).get("status")
        progress = (r or {}).get("progress")
        if waited % 60 == 0:
            print(f"[{waited}s] status={status} progress={progress}", flush=True)
        if status in ("done", "error", "canceled"):
            print("final status:", status, "error:", (r or {}).get("error"), flush=True)
            break
        await asyncio.sleep(10)
        waited += 10

    rows = run_store.get_run_cases(mgr.db_path, run_id)
    for row in rows:
        print("case:", row["case_id"], "verdict:", row["verdict"],
              "pass:", row["pass_count"], "/", row["repeat_count"], flush=True)
        print("judge_reasons(first):", (row["judge_reasons"] or "")[:600], flush=True)


if __name__ == "__main__":
    asyncio.run(main())

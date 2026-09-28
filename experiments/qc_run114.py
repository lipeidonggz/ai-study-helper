"""Run#114 checklist 判定详情：verdict 分布与 fail 理由。"""

import json
import sys
import urllib.request

sys.stdout.reconfigure(encoding="utf-8")


def get(path: str):
    return json.loads(
        urllib.request.urlopen("http://127.0.0.1:8000" + path, timeout=60)
        .read()
        .decode("utf-8")
    )


def main() -> None:
    row = get("/api/eval/runs/114/cases/rag-cmp-002")
    rr = row.get("repeat_results") or []
    from collections import Counter

    print("verdicts:", Counter(r.get("verdict") for r in rr))
    for i, r in enumerate(rr):
        jr = r.get("judge_reasons") or {}
        ac = jr.get("answer_correct") or ""
        print(f"[{i}] {r.get('verdict')} | {ac[:220]}")


if __name__ == "__main__":
    main()

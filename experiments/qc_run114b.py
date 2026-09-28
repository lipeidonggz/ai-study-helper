"""Run#114：逐 attempt 全维度判定与理由（定位 fail 来源 / pending 格式）。"""

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
    for i in (5, 11, 1, 9, 0, 2):
        r = rr[i]
        j = r.get("judgments") or {}
        jr = r.get("judge_reasons") or {}
        print(f"===== [{i}] verdict={r.get('verdict')} judgments={j} =====")
        for dim in ("answer_correct", "format_appropriate", "citation_truth"):
            if jr.get(dim):
                print(f"  [{dim}] {jr[dim][:600]}")


if __name__ == "__main__":
    main()

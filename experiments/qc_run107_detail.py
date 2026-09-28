"""查看 Run#107 非 pass repeat 的判官理由与 trace 摘要。"""

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
    for cid in ["rag-own-002", "rag-cmp-001"]:
        row = get(f"/api/eval/runs/107/cases/{cid}")
        print(f"\n===== {cid} ===== keys={list(row.keys())}")
        rr = row.get("repeat_results") or []
        if rr:
            print("repeat keys:", list(rr[0].keys()))
        for i, r in enumerate(rr):
            if r.get("verdict") == "pass":
                continue
            print(f"\n--- [{i}] verdict={r.get('verdict')} ---")
            jr = r.get("judge_reasons") or r.get("judgments")
            print("  judge_reasons:", json.dumps(jr, ensure_ascii=False)[:1200])
            out = r.get("output") or ""
            print("  output[:700]:", out[:700])
            trace = r.get("trace") or {}
            if isinstance(trace, list):
                for step in trace:
                    if isinstance(step, dict) and step.get("type") == "retrieval":
                        print("  retrieval:", json.dumps(step, ensure_ascii=False)[:900])
            elif isinstance(trace, dict):
                print("  trace keys:", list(trace.keys()))


if __name__ == "__main__":
    main()

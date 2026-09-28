"""检查 rag-cmp-002 pass 样本是否真覆盖核心主题关键词。"""

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
    run_id = sys.argv[1] if len(sys.argv) > 1 else "110"
    row = get(f"/api/eval/runs/{run_id}/cases/rag-cmp-002")
    rr = row.get("repeat_results") or []
    kws = [
        "agants.md",
        "plain text",
        "external content",
        "local vm",
        "legibility",
        "boring",
        "garbage collection",
        "mcp",
        "approval fatigue",
        "blast radius",
        "三组件",
        "三层防御",
    ]
    print("idx verdict len " + " ".join(k[:7] for k in kws))
    for i, r in enumerate(rr):
        out = (r.get("output") or "").lower()
        flags = ["1" if k in out else "." for k in kws]
        print(f"{i:2d} {str(r.get('verdict')):8s} {len(out):5d} " + " ".join(flags))
    print()
    for i, r in enumerate(rr):
        if r.get("verdict") == "pass":
            continue
        jr = r.get("judge_reasons") or {}
        print(f"--- [{i}] {r.get('verdict')} ---")
        print(json.dumps(jr.get("answer_correct"), ensure_ascii=False)[:900])


if __name__ == "__main__":
    main()

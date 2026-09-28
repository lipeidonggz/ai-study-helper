"""cmp-001 20 次 repeat：verdict 与关键词/输出行为的关系。"""

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
    row = get("/api/eval/runs/107/cases/rag-cmp-001")
    rr = row.get("repeat_results") or []
    print("idx verdict len  humans_steer AGENTS.md 无法可靠 不在同一/质疑 概念不同层")
    for i, r in enumerate(rr):
        out = r.get("output") or ""
        flags = [
            "humans steer" in out.lower(),
            "agants.md" in out.lower() or "agents.md" in out.lower(),
            ("无法可靠" in out) or ("无法" in out and "对比" in out),
            ("不在同一" in out) or ("质疑" in out) or ("概念" in out and "不同" in out),
        ]
        print(
            f"{i:3d} {str(r.get('verdict')):8s} {len(out):5d} "
            + " ".join("Y" if f else "." for f in flags)
        )


if __name__ == "__main__":
    main()

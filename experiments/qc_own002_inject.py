"""查看 Run#107 own-002 注入的 6 条 chunk 完整内容（pass vs fail 样本）。"""

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
    row = get("/api/eval/runs/107/cases/rag-own-002")
    rr = row.get("repeat_results") or []
    for idx in (0, 14):
        r = rr[idx]
        trace = r.get("trace") or []
        retr = None
        for step in trace:
            if isinstance(step, dict) and step.get("type") == "retrieval":
                retr = step.get("data") or {}
        print(f"\n########## repeat[{idx}] verdict={r.get('verdict')} ##########")
        if not retr:
            print("no retrieval trace")
            continue
        print("reason:", retr.get("reason"))
        for i, h in enumerate(retr.get("hits") or [], 1):
            print(
                f"\n--- hit {i} score={h.get('score')} sec={h.get('section_path','')[-70:]} ---"
            )
            print(h.get("text", ""))


if __name__ == "__main__":
    main()

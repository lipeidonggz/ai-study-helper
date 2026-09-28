"""cmp-001 fail vs pass（含质疑词）的输出结尾对比。"""

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
    for idx in (2, 14, 5, 9):
        r = rr[idx]
        out = r.get("output") or ""
        print(f"\n########## idx={idx} verdict={r.get('verdict')} len={len(out)} ##########")
        print(out[-800:])


if __name__ == "__main__":
    main()

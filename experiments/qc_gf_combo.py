"""统计 g(答案正确) 与 f(表达合适) 的判定组合：是否存在"答对但表达不行"的分离样本。"""

import json
import sys
import urllib.request
from collections import Counter

sys.stdout.reconfigure(encoding="utf-8")


def get(path: str):
    return json.loads(
        urllib.request.urlopen("http://127.0.0.1:8000" + path, timeout=60)
        .read()
        .decode("utf-8")
    )


def main() -> None:
    run_id = sys.argv[1] if len(sys.argv) > 1 else "107"
    rows = get(f"/api/eval/runs/{run_id}?light=false")
    case_rows = rows.get("cases") or []
    combo = Counter()
    total = 0
    for row in case_rows:
        for r in row.get("repeat_results") or []:
            j = r.get("judgments") or {}
            g = j.get("answer_correct")
            f = j.get("format_appropriate")
            combo[(g, f)] += 1
            total += 1
    print(f"run {run_id}: total judged attempts = {total}")
    for (g, f), n in sorted(combo.items(), key=lambda x: -x[1]):
        print(f"  answer_correct={g} format_appropriate={f}: {n}")


if __name__ == "__main__":
    main()

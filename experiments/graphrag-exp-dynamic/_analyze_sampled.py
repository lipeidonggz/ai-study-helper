"""把这一轮的 9 次采样（3 问题 × 3 次）细看：选择分布 + Dynamic 的评分分布。"""

from __future__ import annotations

import glob
import json
from collections import Counter

FILES = sorted(glob.glob("probe-out/vs-*-0928-152205.json"))


def main() -> int:
    print(f"files: {len(FILES)}\n")
    by_q = {}
    for f in FILES:
        for r in json.load(open(f, encoding="utf-8")):
            key = (f.split("-q")[1][0], r["path"])
            by_q.setdefault(key, []).append(r)

    for (q, path), runs in sorted(by_q.items()):
        print(f"=== q{q} · {path} ===")
        for i, r in enumerate(runs, 1):
            sel = r.get("selection") or {}
            print(f"  r{i}: 选中={sel.get('selected_count', r['reports_fed']):4d}  "
                  f"按文档={sel.get('selected_by_doc')}  引用按文档={r.get('cited_by_doc')}  缺={r.get('expect_missing')}")
            ratings = sel.get("ratings") or sel.get("rating_by_community") or {}
            if ratings:
                vals = list(ratings.values()) if isinstance(ratings, dict) else list(ratings)
                cnt = Counter(int(v) for v in vals)
                total = len(vals)
                one = cnt.get(1, 0)
                print(f"       评分分布: n={total}  " +
                      "  ".join(f"{k}:{cnt[k]}" for k in sorted(cnt)) +
                      f"   ⇒ 恰好 1 分 {one}/{total} = {one/total:.0%}")
        print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

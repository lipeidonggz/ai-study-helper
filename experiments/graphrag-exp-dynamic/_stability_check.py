"""三次重复之间，同一社区的打分到底稳不稳？——分离两个量：
  A. 打分值本身（0–5 的精确一致率、平均绝对差）
  B. 过阈判定（rating >= 1，选材真正用的那个布尔量）的一致率
  另外给出三次"入选集合"的两两 Jaccard，看结果层面的稳定性。

只读 probe-out/vs-*-0928-152205.json。
"""

from __future__ import annotations

import glob
import json
from itertools import combinations
from pathlib import Path

HERE = Path(__file__).resolve().parent
PROBE = HERE / "probe-out"
THRESHOLD = 1


def main() -> int:
    runs: dict[str, list[dict]] = {}
    for f in sorted(glob.glob(str(PROBE / "vs-*-0928-152205.json"))):
        q = Path(f).name.split("-q")[1][0]
        for r in json.load(open(f, encoding="utf-8")):
            if r["path"] == "dynamic":
                runs.setdefault(q, []).append(r)

    print(f"{'查询':>4} {'被评数(三次)':>16} {'共同被评':>8} {'打分精确一致':>12} {'平均|Δ|':>8} "
          f"{'过阈一致':>9} {'过阈翻转':>9} {'入选集合Jaccard':>14}")
    for q in ("1", "2", "3"):
        rs = runs[q]
        ratings = [{str(k): int(v) for k, v in (r["selection"]["ratings"] or {}).items()} for r in rs]
        sels = [set(map(str, r["selection"]["selected"])) for r in rs]
        rated_counts = [len(x) for x in ratings]

        common = set(ratings[0]) & set(ratings[1]) & set(ratings[2])
        exact = [0, 0]
        absdiff = [0.0, 0]
        thr_same = [0, 0]
        for cid in common:
            vals = [ratings[i][cid] for i in range(3)]
            for a, b in combinations(vals, 2):
                exact[1] += 1
                if a == b:
                    exact[0] += 1
                absdiff[0] += abs(a - b)
                absdiff[1] += 1
                thr_same[1] += 1
                if (a >= THRESHOLD) == (b >= THRESHOLD):
                    thr_same[0] += 1

        jac = []
        for a, b in combinations(sels, 2):
            jac.append(len(a & b) / len(a | b))

        print(f"{q:>4} {str(rated_counts):>16} {len(common):>8} "
              f"{exact[0] / exact[1]:>11.0%} {absdiff[0] / absdiff[1]:>8.2f} "
              f"{thr_same[0] / thr_same[1]:>9.0%} {thr_same[1] - thr_same[0]:>9d} "
              f"{'  '.join(f'{x:.0%}' for x in jac):>14}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

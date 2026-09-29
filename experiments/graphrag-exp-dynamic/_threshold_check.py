"""5 档到底有没有用？把同一批记录的 ratings 按不同阈值重放，看稳定性怎么变。

对每个查询：
  · 阈值 t 的"判定一致率" = 同一个社区跨三次运行 (rating >= t) 是否一致
  · 阈值 t 的"入选集合" = 用官方算法（含 keep_parent 剔除）在记录的 ratings 上重放
    ⇒ 再看三次之间入选集合的两两 Jaccard

只读 probe-out/*.json + output/*.parquet，不调模型。
"""

from __future__ import annotations

import glob
import json
from itertools import combinations
from pathlib import Path

import pandas as pd

HERE = Path(__file__).resolve().parent
OUT = HERE / "output"
PROBE = HERE / "probe-out"
MAX_LEVEL = 2
THRESHOLDS = (1, 2, 3, 4)


def load_graph():
    comms = pd.read_parquet(OUT / "communities.parquet")
    reps = pd.read_parquet(OUT / "community_reports.parquet")
    level = {str(int(r.human_readable_id)): int(r.level) for r in comms.itertuples()}
    children = {
        str(int(r.human_readable_id)): [str(int(c)) for c in (r.children if r.children is not None else [])]
        for r in comms.itertuples()
    }
    parent = {str(int(r.human_readable_id)): str(int(r.parent)) for r in comms.itertuples()}
    pool = {str(int(x)) for x in reps["community"] if level[str(int(x))] <= MAX_LEVEL}
    by_level: dict[str, list[str]] = {}
    for cid in level:
        if cid in pool:
            by_level.setdefault(str(level[cid]), []).append(cid)
    return children, parent, by_level


def replay(ratings: dict[str, int], threshold: int, children, parent, by_level) -> set[str]:
    queue = list(by_level["0"])
    lv = 0
    relevant: set[str] = set()
    while queue:
        nxt: list[str] = []
        for cid in queue:
            rating = ratings.get(cid)
            if rating is None:
                continue
            if rating >= threshold:
                relevant.add(cid)
                for child in children.get(cid, []):
                    if child in {c for v in by_level.values() for c in v}:
                        nxt.append(child)
                relevant.discard(parent.get(cid, ""))
        queue = nxt
        lv += 1
    return relevant


def main() -> int:
    children, parent, by_level = load_graph()
    runs: dict[str, list[dict]] = {}
    for f in sorted(glob.glob(str(PROBE / "vs-*-0928-152205.json"))):
        q = Path(f).name.split("-q")[1][0]
        for r in json.load(open(f, encoding="utf-8")):
            if r["path"] == "dynamic":
                runs.setdefault(q, []).append(r)

    print(f"{'查询':>4} {'阈值':>5} {'三次入选数':>16} {'判定一致率':>10} {'入选集合Jaccard':>18}")
    for q in ("1", "2", "3"):
        rs = runs[q]
        ratings = [{str(k): int(v) for k, v in (r["selection"]["ratings"] or {}).items()} for r in rs]
        common = set(ratings[0]) & set(ratings[1]) & set(ratings[2])
        for t in THRESHOLDS:
            same = tot = 0
            for cid in common:
                vals = [1 if ratings[i][cid] >= t else 0 for i in range(3)]
                for a, b in combinations(vals, 2):
                    tot += 1
                    same += a == b
            sels = [replay(rr, t, children, parent, by_level) for rr in ratings]
            jac = [len(a & b) / len(a | b) if (a | b) else 1.0 for a, b in combinations(sels, 2)]
            print(f"{q:>4} {t:>5} {str([len(s) for s in sels]):>16} {same / tot:>10.0%} "
                  f"{'  '.join(f'{x:.0%}' for x in jac):>18}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

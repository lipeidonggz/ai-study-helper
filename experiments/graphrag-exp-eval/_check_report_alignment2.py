"""语言无关的对齐检验：报告正文里的 [Data: Entities (id)] 是否属于"它自己"这个社区。

id 用的是实体的 human_readable_id（报告里写的就是这个），与中英文无关。
"""

from __future__ import annotations

import re
from collections import defaultdict
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent
OUT = ROOT / "output"


def main() -> int:
    communities = pd.read_parquet(OUT / "communities.parquet")
    reports = pd.read_parquet(OUT / "community_reports.parquet")
    entities = pd.read_parquet(OUT / "entities.parquet")

    hr_of_entity = dict(zip(entities["id"], entities["human_readable_id"]))
    # 实体 id → 它出现在哪些社区
    comm_of_hr: dict[int, set[int]] = defaultdict(set)
    for _, c in communities.iterrows():
        cid = int(c["community"])
        for e in ([] if c["entity_ids"] is None else list(c["entity_ids"])):
            hr = hr_of_entity.get(e)
            if hr is not None:
                comm_of_hr[int(hr)].add(cid)
    own_hr: dict[int, set[int]] = {}
    for _, c in communities.iterrows():
        cid = int(c["community"])
        own_hr[cid] = {int(hr_of_entity[e]) for e in ([] if c["entity_ids"] is None else list(c["entity_ids"])) if e in hr_of_entity}

    rows = []
    for _, r in reports.iterrows():
        cid = int(r["community"])
        text = str(r["full_content"])
        cited = []
        for group in re.findall(r"Entities \(([^)]*)\)", text):
            cited += [int(x) for x in re.split(r"[,\s]+", group) if x.strip().isdigit()]
        if not cited:
            rows.append({"cid": cid, "level": int(r["level"]), "cited": 0, "own": 0, "ratio": None,
                         "in_other": 0, "title": str(r["title"])[:34]})
            continue
        own = [h for h in cited if h in own_hr[cid]]
        other = [h for h in cited if h not in own_hr[cid]]
        rows.append({"cid": cid, "level": int(r["level"]), "cited": len(cited), "own": len(own),
                     "ratio": len(own) / len(cited), "in_other": len(other), "title": str(r["title"])[:34]})

    df = pd.DataFrame(rows)
    withcite = df[df["cited"] > 0]
    print(f"有实体引用的报告: {len(withcite)} / {len(df)}")
    print()
    print("按层看'引用属于自己社区'的比例：")
    print(withcite.groupby("level")["ratio"].agg(["count", "mean", "median", "min"]).round(3).to_string())
    print()
    bad = withcite[withcite["ratio"] < 0.2].sort_values(["level", "cid"])
    print(f"引用基本不属于自己的（比例 < 20%）：{len(bad)} / {len(withcite)}")
    print(bad.groupby("level").size().to_string())
    print()
    for _, r in bad.head(15).iterrows():
        print(f"  c{r['cid']:<3d} L{r['level']}  引用 {r['own']}/{r['cited']} 属自己 | {r['title']}")
    print()
    print("对照：比例 >= 80% 的:", int((withcite['ratio'] >= 0.8).sum()), "/", len(withcite))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
